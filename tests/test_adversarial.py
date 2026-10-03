"""Adversarial tests: broken providers, hostile HTTP input, concurrency, approval bypass."""
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

from ace import board as boardmod
from ace import providers


def fake_server(handler_fn):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            handler_fn(self)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def reply(h, code, body, ctype="application/json"):
    data = body if isinstance(body, bytes) else body.encode()
    h.send_response(code)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


class BadProviders(unittest.TestCase):
    def prov(self, base):
        return providers.make("t", {"type": "openai-compatible", "base_url": base, "model": "m"})

    def expect_provider_error(self, fn):
        srv, base = fake_server(fn)
        try:
            with self.assertRaises(providers.ProviderError):
                self.prov(base).complete("hi", timeout=2)
        finally:
            srv.shutdown()

    def test_http_500(self):
        self.expect_provider_error(lambda h: reply(h, 500, "boom"))

    def test_malformed_json(self):
        self.expect_provider_error(lambda h: reply(h, 200, "{not json"))

    def test_json_not_object(self):
        self.expect_provider_error(lambda h: reply(h, 200, "[1,2,3]"))

    def test_empty_choices(self):
        self.expect_provider_error(lambda h: reply(h, 200, '{"choices": []}'))

    def test_non_utf8(self):
        self.expect_provider_error(lambda h: reply(h, 200, b"\xff\xfe\x00"))

    def test_timeout(self):
        def slow(h):
            time.sleep(3)
            reply(h, 200, "{}")
        self.expect_provider_error(slow)

    def test_connection_dropped(self):
        def drop(h):
            h.connection.close()
        self.expect_provider_error(drop)

    def test_usage_garbage(self):
        srv, base = fake_server(lambda h: reply(h, 200, json.dumps({"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": "x", "completion_tokens": None}})))
        try:
            try:
                r = self.prov(base).complete("hi", timeout=2)
                self.assertEqual(r.text, "ok")
            except providers.ProviderError:
                pass  # a clear provider error is acceptable; a crash is not
        finally:
            srv.shutdown()

    def test_null_content(self):
        srv, base = fake_server(lambda h: reply(h, 200, json.dumps({"choices": [{"message": {"content": None}}]})))
        try:
            self.assertEqual(self.prov(base).complete("hi", timeout=2).text, "")
        finally:
            srv.shutdown()

    def test_key_not_in_error(self):
        os.environ["ACE_TEST_KEY"] = "sk-secret-123456"
        srv, base = fake_server(lambda h: reply(h, 401, "bad key"))
        try:
            p = providers.make("t", {"type": "openai-compatible", "base_url": base, "model": "m", "key_env": "ACE_TEST_KEY"})
            with self.assertRaises(providers.ProviderError) as cx:
                p.complete("hi", timeout=2)
            self.assertNotIn("sk-secret-123456", str(cx.exception))
        finally:
            srv.shutdown()
            del os.environ["ACE_TEST_KEY"]

    def test_cli_timeout_and_missing(self):
        p = providers.make("c", {"type": "cli", "command": [sys.executable, "-c", "import time;time.sleep(5)"]})
        with self.assertRaises(providers.ProviderError):
            p.complete("x", timeout=1)
        p = providers.make("c", {"type": "cli", "command": ["definitely-not-a-command-xyz"]})
        with self.assertRaises(providers.ProviderError):
            p.complete("x")
        p = providers.make("c", {"type": "cli", "command": [sys.executable, "-c", "import sys;sys.exit(3)"]})
        with self.assertRaises(providers.ProviderError):
            p.complete("x")


class HostileBoard(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.srv, self.board, self.ht, self.at = boardmod.serve(os.path.join(self.d, "b.json"), port=0, secure=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()

    def call(self, path, body, token=None, ctype="application/json", raw=None):
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method="POST",
                                     headers={"Content-Type": ctype, "X-Ace-Token": token or ""})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {}

    def test_non_object_bodies(self):
        for raw in (b"[1]", b'"s"', b"null", b"123", b"{bad", b"\xff"):
            code, _ = self.call("/api/tasks", None, self.ht, raw=raw)
            self.assertIn(code, (400,), raw)

    def test_actor_not_object(self):
        for a in ("bob", 5, [], None):
            code, _ = self.call("/api/tasks", {"actor": a, "title": "t"}, self.ht)
            self.assertIn(code, (400,), a)

    def test_negative_and_bad_length(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        s.sendall(("POST /api/tasks HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Ace-Token: %s\r\nContent-Type: application/json\r\nContent-Length: -5\r\n\r\n" % self.ht).encode())
        s.settimeout(3)
        try:
            data = s.recv(200)
            self.assertTrue(data.startswith(b"HTTP/1.1 4"), data)
        finally:
            s.close()

    def test_agent_token_cannot_approve_or_forge(self):
        c, t = self.call("/api/tasks", {"actor": {"name": "ann", "kind": "human"}, "title": "t", "needs_approval": True}, self.ht)
        self.assertEqual(c, 200)
        tid = t["id"]
        for kind in ("human", "agent", "root"):
            c, _ = self.call("/api/tasks/%d/approve" % tid, {"actor": {"name": "ann", "kind": kind}}, self.at)
            self.assertIn(c, (403, 400))
        c, _ = self.call("/api/tasks/%d/update" % tid, {"actor": {"name": "bot", "kind": "human"}, "status": "done"}, self.at)
        self.assertEqual(c, 409)
        self.assertNotEqual(self.board.snapshot()["tasks"][0]["status"], "done")
        self.assertIsNone(self.board.snapshot()["tasks"][0]["approved_by"])

    def test_no_token_and_wrong_token(self):
        for tk in (None, "", "x" * 5, self.ht[:-1], self.ht + "a"):
            c, _ = self.call("/api/tasks", {"actor": {"name": "a"}, "title": "t"}, tk)
            self.assertEqual(c, 401)

    def test_bad_host_header_localhost_mode(self):
        d = tempfile.mkdtemp()
        srv, b, _, _ = boardmod.serve(os.path.join(d, "x.json"), port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=3)
            s.sendall(b"GET /api/state HTTP/1.1\r\nHost: evil.example\r\n\r\n")
            self.assertTrue(s.recv(100).startswith(b"HTTP/1.1 403"))
            s.close()
        finally:
            srv.shutdown()

    def test_concurrent_tasks_no_lost_updates(self):
        errs = []

        def worker(i):
            for j in range(20):
                c, _ = self.call("/api/tasks", {"actor": {"name": "w%d" % i}, "title": "t%d-%d" % (i, j)}, self.ht)
                if c != 200:
                    errs.append(c)
        ths = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(errs, [])
        tasks = self.board.snapshot()["tasks"]
        self.assertEqual(len(tasks), 160)
        self.assertEqual(len({t["id"] for t in tasks}), 160)
        with open(self.board.path) as f:
            self.assertEqual(len(json.load(f)["tasks"]), 160)

    def test_huge_and_odd_values(self):
        c, t = self.call("/api/tasks", {"actor": {"name": "a" * 500}, "title": "x" * 10000, "prompt": "p" * 100000}, self.ht)
        self.assertEqual(c, 200)
        self.assertLessEqual(len(t["title"]), 160)
        c, _ = self.call("/api/tasks/%d/update" % t["id"], {"actor": {"name": "a"}, "progress": "abc"}, self.ht)
        self.assertEqual(c, 400)
        c, _ = self.call("/api/tasks/%d/update" % t["id"], {"actor": {"name": "a"}, "progress": 1e400}, self.ht)
        self.assertIn(c, (200, 400))
        c, _ = self.call("/api/tasks/999/update", {"actor": {"name": "a"}, "status": "done"}, self.ht)
        self.assertEqual(c, 404)
        c, _ = self.call("/api/tasks/abc/update", {"actor": {"name": "a"}}, self.ht)
        self.assertIn(c, (400, 404))

    def test_corrupt_board_file(self):
        p = os.path.join(self.d, "corrupt.json")
        with open(p, "w") as f:
            f.write("{truncated")
        try:
            boardmod.Board(p)
        except Exception as e:
            self.assertIsInstance(e, (ValueError, boardmod.BoardError), type(e))


if __name__ == "__main__":
    unittest.main()
