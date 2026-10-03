import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ace import agent as ag  # noqa: E402
from ace.providers import Reply  # noqa: E402


def block(tool, **args):
    import json
    return "```ace-tool\n%s\n```" % json.dumps({"tool": tool, "args": args})


class Script:
    """A fake provider that replies from a fixed list and records the prompts it saw."""
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt, timeout=0):
        self.prompts.append(prompt)
        return Reply(self.replies.pop(0))


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "a.py"), "w") as f:
            f.write("def add(a, b):\n    return a - b\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def tools(self, **kw):
        return ag.Tools(self.d, **kw)

    def test_fix_bug_end_to_end(self):
        p = Script([block("read_file", path="a.py"),
                    block("edit_file", path="a.py", old="a - b", new="a + b"),
                    block("run_shell", command=sys.executable + ' -c "import a; print(a.add(2,3))"'),
                    "Fixed: add now returns a + b."])
        res = ag.run(p, "fix add", self.d, self.tools(auto_edit=True, allow_shell=True))
        self.assertEqual(res["stopped"], "done")
        self.assertEqual(res["steps"], 4)
        self.assertIn("a + b", open(os.path.join(self.d, "a.py")).read())
        self.assertIn("exit code 0\n5", p.prompts[-1])
        self.assertEqual(len(ag.load_session(self.d, res["session"])), 8)

    def test_denied_without_approval(self):
        p = Script([block("edit_file", path="a.py", old="a - b", new="a + b"), block("run_shell", command="echo hi"), "ok"])
        res = ag.run(p, "x", self.d, self.tools())
        self.assertIn("a - b", open(os.path.join(self.d, "a.py")).read())
        self.assertIn("did not approve this edit", p.prompts[1])
        self.assertIn("did not approve this command", p.prompts[2])
        self.assertEqual(res["stopped"], "done")

    def test_approval_callback(self):
        seen = []
        t = self.tools(approve=lambda k, d: seen.append((k, d)) or True)
        t.call("write_file", {"path": "new/b.txt", "content": "hi"})
        self.assertEqual(seen[0][0], "write")
        self.assertEqual(open(os.path.join(self.d, "new", "b.txt")).read(), "hi")

    def test_paths_cannot_escape(self):
        t = self.tools(auto_edit=True, allow_shell=True)
        for bad in ("../x.txt", "/etc/passwd", "a/../../x"):
            with self.assertRaises(ag.ToolError):
                t.call("read_file", {"path": bad})
        with self.assertRaises(ag.ToolError):
            t.call("write_file", {"path": "../evil.txt", "content": "x"})
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.d), "evil.txt")))

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_escape_blocked(self):
        outside = tempfile.mkdtemp()
        try:
            open(os.path.join(outside, "secret.txt"), "w").write("s")
            os.symlink(outside, os.path.join(self.d, "link"))
            with self.assertRaises(ag.ToolError):
                self.tools().call("read_file", {"path": "link/secret.txt"})
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_edit_needs_unique_match(self):
        with open(os.path.join(self.d, "c.txt"), "w") as f:
            f.write("x x")
        with self.assertRaises(ag.ToolError):
            self.tools(auto_edit=True).call("edit_file", {"path": "c.txt", "old": "x", "new": "y"})
        with self.assertRaises(ag.ToolError):
            self.tools(auto_edit=True).call("edit_file", {"path": "c.txt", "old": "zzz", "new": "y"})

    def test_shell_timeout_and_output_cap(self):
        t = self.tools(allow_shell=True, shell_timeout=1)
        with self.assertRaises(ag.ToolError):
            t.call("run_shell", {"command": sys.executable + " -c \"import time; time.sleep(5)\""})
        out = t.call("run_shell", {"command": sys.executable + " -c \"print('x'*50000)\""})
        self.assertLess(len(out), 9000)
        self.assertIn("cut", out)

    def test_bad_blocks_do_not_crash(self):
        p = Script(["```ace-tool\n{not json\n```", block("nope"), block("read_file"), block("read_file", path="missing.txt"), "done"])
        res = ag.run(p, "x", self.d, self.tools())
        self.assertEqual(res["stopped"], "done")
        self.assertIn("could not read the tool block", p.prompts[1])
        self.assertIn("unknown tool", p.prompts[2])
        self.assertIn("error", p.prompts[3])

    def test_max_steps(self):
        p = Script([block("list_dir")] * 5)
        res = ag.run(p, "loop", self.d, self.tools(), max_steps=3)
        self.assertEqual((res["stopped"], res["steps"]), ("loop", 3))

    def test_resume_session(self):
        p = Script(["first answer"])
        r1 = ag.run(p, "one", self.d, self.tools())
        p2 = Script(["second answer"])
        r2 = ag.run(p2, "two", self.d, self.tools(), session=r1["session"])
        self.assertIn("first answer", p2.prompts[0])
        self.assertEqual(r2["session"], r1["session"])
        self.assertEqual(len(ag.list_sessions(self.d)), 1)

    def test_old_tool_results_shortened(self):
        ev = [{"role": "user", "text": "t"}] + [{"role": "tool", "text": "z" * 5000} for _ in range(10)]
        prompt = ag.build_prompt(ev, keep_full=2, cap=100)
        self.assertEqual(prompt.count("older result shortened"), 8)

    def test_search_and_list(self):
        t = self.tools()
        self.assertIn("a.py:2:", t.call("search", {"pattern": "return"}))
        self.assertIn("a.py", t.call("list_dir", {}))


if __name__ == "__main__":
    unittest.main()


class ParseTolerance(unittest.TestCase):
    def test_raw_file_block(self):
        from ace import agent
        self.assertEqual(agent.parse_tool("```ace-write a.html\n<h1>x</h1>\n```"), ("write_file", {"path": "a.html", "content": "<h1>x</h1>\n"}))

    def test_triple_quoted_and_flat_args(self):
        from ace import agent
        t = agent.parse_tool('```ace-tool\n{"tool":"write_file","path":"a","content":"""\n<p>"q"</p>\n"""}\n```')
        self.assertEqual(t, ("write_file", {"path": "a", "content": '<p>"q"</p>'}))


class LoopAndAssets(unittest.TestCase):
    def test_missing_asset_reported_and_loop_stops(self):
        import tempfile
        from ace import agent

        class Same:
            def complete(self, prompt, timeout=0):
                class R:
                    text = '```ace-write index.html\n<html><head><title>t</title><link rel="stylesheet" href="styles.css"></head><body><h1>Hello there</h1></body></html>\n```'
                return R()
        with tempfile.TemporaryDirectory() as d:
            tools = agent.Tools(d, approve=lambda k, x: True, auto_edit=True)
            res = agent.run(Same(), "make a page", d, tools, max_steps=8)
            self.assertEqual(res["stopped"], "loop")
            self.assertLess(res["steps"], 8)
            out = tools.call("write_file", {"path": "x.html", "content": '<link href="a.css"><img src="https://e.com/i.png"><script src="app.js"></script>'})
            self.assertIn("MISSING FILES", out)
            self.assertIn("a.css", out)
            self.assertIn("app.js", out)
            self.assertNotIn("e.com", out)
