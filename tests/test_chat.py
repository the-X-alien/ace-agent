import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ace import chat  # noqa: E402


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.fake = os.path.join(self.d, "fake_model.py")
        with open(self.fake, "w") as f:
            f.write('import sys, json\np = sys.stdin.read()\nn = p.count("TOOL RESULT:")\n'
                    'def blk(t, **a): print("```ace-tool\\n" + json.dumps({"tool": t, "args": a}) + "\\n```")\n'
                    'if "make hello" in p and n == 0: blk("write_file", path="hello.txt", content="hi")\n'
                    'else: print("done")\n')
        os.makedirs(os.path.join(self.d, ".ace"))
        import json
        cfg = {"default_provider": "fake", "providers": {"mock": {"type": "echo"}, "fake": {"type": "cli", "command": [sys.executable, self.fake], "prompt_via": "stdin"}}}
        json.dump(cfg, open(os.path.join(self.d, ".ace", "config.json"), "w"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def session(self, lines):
        it = iter(lines)

        def inp(prompt=""):
            try:
                return next(it)
            except StopIteration:
                raise EOFError
        out, err = io.StringIO(), io.StringIO()
        c = chat.Chat(self.d, inp=inp, out=out, err=err)
        c.loop()
        return out.getvalue(), err.getvalue()

    def test_write_needs_approval_then_works(self):
        out, err = self.session(["make hello", "n", "/exit"])
        self.assertFalse(os.path.exists(os.path.join(self.d, "hello.txt")))
        self.assertIn("Ace wants to write a file", err)
        out, err = self.session(["make hello", "y", "/exit"])
        self.assertEqual(open(os.path.join(self.d, "hello.txt")).read(), "hi")
        self.assertIn("done", out)

    def test_yes_edits_and_commands(self):
        out, err = self.session(["/yes-edits", "make hello", "/model", "/model nope", "/sessions", "/help", "/exit"])
        self.assertTrue(os.path.exists(os.path.join(self.d, "hello.txt")))
        self.assertIn("No provider named nope", out)
        self.assertIn("* fake", out)

    def test_follow_up_keeps_session(self):
        out, err = self.session(["hello", "again", "/sessions", "/exit"])
        lines = [l for l in out.split("\n") if "events" in l]
        self.assertEqual(len(lines), 1)

    def test_eof_exits(self):
        out, err = self.session([])
        self.assertIn("Ace", out)

    def test_mock_warning(self):
        it = iter(["/exit"])
        err = io.StringIO()
        c = chat.Chat(self.d, provider_name="mock", inp=lambda p="": next(it), out=io.StringIO(), err=err)
        c.loop()
        self.assertIn("MOCK", err.getvalue())

    def test_model_command_sets_model(self):
        out = io.StringIO()
        c = chat.Chat(self.d, out=out, err=io.StringIO())
        c.command("/model featherless some/model-x")
        self.assertEqual(c.pname, "featherless")
        self.assertEqual(c.cfg["providers"]["featherless"]["model"], "some/model-x")
        c.command("/model nope")
        self.assertIn("No provider", out.getvalue())


if __name__ == "__main__":
    unittest.main()
