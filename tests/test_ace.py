import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ace import board, cli, gate, providers, runner, skills as sk  # noqa: E402

GOOD = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Bakery</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main><h1>Oven & Co</h1>'
        '<p>Fresh bread baked every morning in Dublin, sold by the loaf or by the dozen rolls.</p></main></body></html>')


class Scripted(providers.Provider):
    """Returns queued replies. Used to test the pipeline logic, not model quality."""

    def __init__(self, replies):
        super().__init__("scripted", {})
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt, timeout=0):
        self.prompts.append(prompt)
        return providers.Reply(self.replies.pop(0), 10, 20, 0.01, usage_known=True)


class Skills(unittest.TestCase):
    def setUp(self):
        self.sl = sk.load_skills()

    def test_bundled_load(self):
        self.assertGreaterEqual(len(self.sl), 6)

    def test_select_web(self):
        p = sk.select("build a responsive landing page in html", self.sl)
        self.assertEqual(p[0].skill.name, "web-ui-design")
        self.assertIn("landing page", p[0].matched)

    def test_no_match_leaves_prompt_unchanged(self):
        self.assertEqual(sk.select("hello there", self.sl), [])
        self.assertEqual(sk.compose("hello there", []), "hello there")

    def test_compose_keeps_task_last_and_intact(self):
        picks = sk.select("write python code to parse csv", self.sl)
        out = sk.compose("write python code to parse csv", picks)
        self.assertTrue(out.endswith("## Task\nwrite python code to parse csv"))

    def test_user_skill_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "w.md"), "w") as f:
                f.write("---\nname: writing\nsummary: mine\ntriggers: zebra\nkind: text\n---\nbody")
            sl = {s.name: s for s in sk.load_skills([d])}
            self.assertEqual(sl["writing"].summary, "mine")

    def test_bad_skill_rejected(self):
        with self.assertRaises(ValueError):
            sk.parse_skill("no front matter")


class Gate(unittest.TestCase):
    def test_good_html_passes(self):
        k, art, issues = gate.check("```html\n%s\n```" % GOOD)
        self.assertEqual(k, "html")
        self.assertEqual(gate.hard(issues), [])

    def test_bad_html_flags(self):
        _, _, issues = gate.check("```html\n<html><body><p>hi</p></body></html>\n```")
        h = " ".join(gate.hard(issues))
        self.assertIn("title", h)
        self.assertIn("viewport", h)

    def test_placeholder_text(self):
        _, _, issues = gate.check("```html\n%s\n```" % GOOD.replace("Fresh bread", "Lorem ipsum bread"))
        self.assertTrue(any("placeholder" in m for m in gate.hard(issues)))

    def test_python_syntax(self):
        _, _, issues = gate.check("```python\ndef f(:\n  pass\n```")
        self.assertTrue(gate.hard(issues))
        self.assertEqual(gate.hard(gate.check("```python\ndef f():\n    return 1\n```")[2]), [])

    def test_json(self):
        self.assertTrue(gate.hard(gate.check('{"a": }', "json")[2]))
        self.assertEqual(gate.hard(gate.check('{"a": 1}', "json")[2]), [])


class Pipeline(unittest.TestCase):
    def test_repair_pass_runs_once_and_fixes(self):
        bad = "```html\n<html><body><p>hi</p></body></html>\n```"
        p = Scripted([bad, "```html\n%s\n```" % GOOD])
        r = runner.run_ace(p, "make a landing page in html", sk.load_skills())
        self.assertEqual((r["calls"], r["fixes"]), (2, 1))
        self.assertEqual(gate.hard(r["issues"]), [])
        self.assertIn("failed these checks", p.prompts[1])
        self.assertEqual((r["tokens_in"], r["tokens_out"]), (20, 40))

    def test_repair_bounded(self):
        bad = "```html\n<html><body><p>hi</p></body></html>\n```"
        p = Scripted([bad, bad, bad])
        r = runner.run_ace(p, "make a landing page in html", sk.load_skills(), max_fixes=1)
        self.assertEqual(r["calls"], 2)
        self.assertTrue(gate.hard(r["issues"]))

    def test_plain_sends_prompt_unchanged(self):
        p = Scripted(["ok reply that is long enough to pass"])
        r = runner.run_plain(p, "make a landing page")
        self.assertEqual(p.prompts, ["make a landing page"])
        self.assertEqual(r["skills"], [])

    def test_compare_same_provider_both_arms(self):
        p = Scripted(["plain reply that is long enough", "ace reply that is long enough"])
        c = runner.compare(p, "explain loops in writing", sk.load_skills())
        self.assertEqual(len(p.prompts), 2)
        self.assertEqual(c["provider"], "scripted")

    def test_echo_marked_mock(self):
        r = runner.run_plain(providers.make("m", {"type": "echo"}), "hello")
        self.assertTrue(r["mock"])


class FakeOpenAI(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(n))
        FakeOpenAI.seen.append((self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") == "Bearer bad":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"no"}')
            return
        out = json.dumps({"choices": [{"message": {"content": "hello from fake"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class Adapters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d/v1" % cls.srv.server_port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_openai_compatible_over_http(self):
        os.environ["ACE_TEST_KEY"] = "k123"
        p = providers.make("f", {"type": "openai-compatible", "base_url": self.base, "model": "m1", "key_env": "ACE_TEST_KEY",
                                 "price_in_per_mtok": 1.0, "price_out_per_mtok": 2.0})
        r = p.complete("hi")
        self.assertEqual((r.text, r.tokens_in, r.tokens_out), ("hello from fake", 7, 3))
        path, auth, body = FakeOpenAI.seen[-1]
        self.assertEqual((path, auth, body["model"]), ("/v1/chat/completions", "Bearer k123", "m1"))
        self.assertAlmostEqual(p.price(r), (7 * 1.0 + 3 * 2.0) / 1e6)

    def test_missing_key_and_http_error(self):
        os.environ.pop("ACE_NOPE", None)
        with self.assertRaises(providers.ProviderError):
            providers.make("f", {"type": "openai-compatible", "base_url": self.base, "model": "m", "key_env": "ACE_NOPE"}).complete("x")
        os.environ["ACE_BAD"] = "bad"
        with self.assertRaises(providers.ProviderError) as cm:
            providers.make("f", {"type": "openai-compatible", "base_url": self.base, "model": "m", "key_env": "ACE_BAD"}).complete("x")
        self.assertIn("401", str(cm.exception))

    def test_cli_provider(self):
        p = providers.make("c", {"type": "cli", "command": [sys.executable, "-c", "import sys; print('got:' + sys.argv[1])"], "prompt_via": "arg"})
        self.assertIn("got:hello", p.complete("hello").text)
        p2 = providers.make("c", {"type": "cli", "command": [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], "prompt_via": "stdin"})
        self.assertIn("HELLO", p2.complete("hello").text)
        self.assertFalse(p2.complete("x").usage_known)

    def test_cli_missing_command(self):
        with self.assertRaises(providers.ProviderError):
            providers.make("c", {"type": "cli", "command": ["definitely-not-a-command-xyz"]}).complete("x")

    def test_unknown_type(self):
        with self.assertRaises(providers.ProviderError):
            providers.make("x", {"type": "nope"})

    def test_no_price_means_no_cost(self):
        p = providers.make("f", {"type": "openai-compatible", "base_url": self.base, "model": "m"})
        self.assertIsNone(p.price(p.complete("hi")))


def call(base, path, body=None, token=None, host=None, ctype="application/json"):
    h = {"Content-Type": ctype}
    if token:
        h["X-Ace-Token"] = token
    if host:
        h["Host"] = host
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class BoardTests(unittest.TestCase):
    def start(self, token=None, host="127.0.0.1"):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "board.json")
        srv, b, tok, self.atok = board.serve(self.path, host, 0, token)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(self.tmp.cleanup)
        return "http://127.0.0.1:%d" % srv.server_address[1], b, tok

    H = {"name": "Dhiaan", "kind": "human"}
    A = {"name": "bot", "kind": "agent"}

    def test_task_lifecycle_and_approval_gate(self):
        base, _, _ = self.start()
        c, t = call(base, "/api/tasks", {"actor": self.H, "title": "Ship v1", "needs_approval": True, "assignee": "bot", "assignee_kind": "agent"})
        self.assertEqual(c, 200)
        tid = t["id"]
        c, d = call(base, "/api/tasks/%d/update" % tid, {"actor": self.A, "status": "done"})
        self.assertEqual(c, 409)
        c, d = call(base, "/api/tasks/%d/approve" % tid, {"actor": self.A})
        self.assertEqual(c, 403)
        c, d = call(base, "/api/tasks/%d/approve" % tid, {"actor": self.H})
        self.assertEqual((c, d["approved_by"]), (200, "Dhiaan"))
        c, d = call(base, "/api/tasks/%d/update" % tid, {"actor": self.A, "status": "done"})
        self.assertEqual((c, d["status"], d["progress"]), (200, "done", 100))

    def test_progress_notes_context_and_persistence(self):
        base, b, _ = self.start()
        _, t = call(base, "/api/tasks", {"actor": self.H, "title": "x"})
        call(base, "/api/tasks/%d/update" % t["id"], {"actor": self.A, "progress": 40, "note": "halfway"})
        call(base, "/api/context", {"actor": self.H, "text": "use the blue palette"})
        s = call(base, "/api/state")[1]
        self.assertEqual(s["tasks"][0]["progress"], 40)
        self.assertEqual(s["context"][0]["text"], "use the blue palette")
        again = board.Board(self.path).snapshot()
        self.assertEqual(again["tasks"][0]["notes"][-1]["text"], "halfway")

    def test_validation(self):
        base, _, _ = self.start()
        self.assertEqual(call(base, "/api/tasks", {"actor": {"name": ""}, "title": "x"})[0], 400)
        self.assertEqual(call(base, "/api/tasks", {"actor": self.H, "title": " "})[0], 400)
        self.assertEqual(call(base, "/api/tasks/99/update", {"actor": self.H, "status": "done"})[0], 404)
        _, t = call(base, "/api/tasks", {"actor": self.H, "title": "x"})
        self.assertEqual(call(base, "/api/tasks/%d/update" % t["id"], {"actor": self.H, "status": "bogus"})[0], 400)

    def test_rejects_non_json_post_and_foreign_host(self):
        base, _, _ = self.start()
        self.assertEqual(call(base, "/api/tasks", {"actor": self.H, "title": "x"}, ctype="text/plain")[0], 415)
        self.assertEqual(call(base, "/api/state", host="evil.example")[0], 403)

    def test_token_required_off_localhost(self):
        base, _, tok = self.start(host="0.0.0.0")
        self.assertTrue(tok)
        self.assertEqual(call(base, "/api/state")[0], 401)
        self.assertEqual(call(base, "/api/state", token="wrong")[0], 401)
        self.assertEqual(call(base, "/api/state", token=tok)[0], 200)

    def test_secure_mode_agent_cannot_impersonate_human(self):
        base, _, tok = self.start(host="0.0.0.0")
        _, t = call(base, "/api/tasks", {"actor": self.H, "title": "gate", "needs_approval": True}, token=tok)
        # the agent token claims to be a human in the payload; the server overrides the kind
        c, d = call(base, "/api/tasks/%d/approve" % t["id"], {"actor": {"name": "Dhiaan", "kind": "human"}}, token=self.atok)
        self.assertEqual(c, 403)
        c, d = call(base, "/api/tasks/%d/update" % t["id"], {"actor": {"name": "x", "kind": "human"}, "status": "done"}, token=self.atok)
        self.assertEqual(c, 409)
        c, d = call(base, "/api/tasks/%d/approve" % t["id"], {"actor": {"name": "D", "kind": "agent"}}, token=tok)
        self.assertEqual((c, d["approved_by"]), (200, "D"))

    def test_open_mode_is_not_a_security_boundary(self):
        base, _, _ = self.start()
        _, t = call(base, "/api/tasks", {"actor": self.H, "title": "gate", "needs_approval": True})
        c, _d = call(base, "/api/tasks/%d/approve" % t["id"], {"actor": {"name": "bot", "kind": "human"}})
        self.assertEqual(c, 200)  # documented limitation: in open mode the payload decides

    def test_ui_served_with_security_headers(self):
        base, _, _ = self.start()
        r = urllib.request.urlopen(base + "/")
        self.assertIn("Ace", r.read().decode())
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    def test_events_stream_sends_snapshot(self):
        base, _, _ = self.start()
        r = urllib.request.urlopen(base + "/api/events", timeout=5)
        self.assertTrue(r.readline().startswith(b"data: "))
        r.close()


class Cli(unittest.TestCase):
    def test_init_run_compare(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                self.assertEqual(cli.main(["init"]), 0)
                self.assertTrue(os.path.exists(".ace/config.json"))
                self.assertEqual(cli.main(["run", "build a landing page in html", "--artifact", "--out", "o.html"]), 0)
                self.assertTrue(os.path.exists("o.html"))
                self.assertEqual(cli.main(["compare", "build a landing page in html", "--report", "r.html"]), 0)
                self.assertIn("MOCK RUN", open("r.html").read())
                self.assertEqual(cli.main(["doctor"]), 0)
                self.assertEqual(cli.main(["run", "x", "--provider", "nosuch"]), 2)
            finally:
                os.chdir(old)

    def test_work_end_to_end_with_board(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        srv, _, tok, _a = board.serve(os.path.join(tmp.name, "b.json"), "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        _, t = call(base, "/api/tasks", {"actor": {"name": "D", "kind": "human"}, "title": "page", "prompt": "build a landing page in html", "needs_approval": True})
        old = os.getcwd()
        os.chdir(tmp.name)
        try:
            self.assertEqual(cli.main(["work", str(t["id"]), "--server", base]), 0)
        finally:
            os.chdir(old)
        s = call(base, "/api/state")[1]["tasks"][0]
        self.assertEqual(s["status"], "review")
        self.assertTrue(s["output"])
        self.assertTrue(any("MOCK" in n["text"] for n in s["notes"]))


if __name__ == "__main__":
    unittest.main()
