import os
import tempfile
import time
import unittest

from ace import tui


class FakeKeys:
    def __init__(self, seq):
        self.seq = list(seq)

    def read(self, t):
        return self.seq.pop(0) if self.seq else None


def typed(s):
    return list(s) + ["enter"]


class TuiTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)

    def app(self, size=(60, 14)):
        return tui.App(self.d, size=lambda: size)

    def text(self, a):
        return "\n".join("".join(x for _, x in row) for row in a.render())

    def test_render_fits_screen(self):
        a = self.app((40, 10))
        r = a.render()
        self.assertEqual(len(r), 10)
        self.assertTrue(all(sum(len(x) for _, x in row) <= 40 for row in r))
        self.assertIn("ace", self.text(a))

    def test_typing_and_command(self):
        a = self.app()
        for k in typed("/help"):
            a.key(k)
        self.assertIn("/yes-edits", self.text(a))

    def test_backspace_and_history(self):
        a = self.app()
        for k in list("abc") + ["backspace"]:
            a.key(k)
        self.assertEqual(a.buf, "ab")
        a.key("enter")
        for _ in range(100):
            if not a.running:
                break
            time.sleep(0.02)
        a.key("up")
        self.assertEqual(a.buf, "ab")

    def test_scroll_keys(self):
        a = self.app((40, 8))
        a.add("go", "user")
        for i in range(50):
            a.add("line %d" % i)
        a.render()
        a.key("pgup")
        a.render()
        self.assertGreater(a.scroll, 0)
        a.key("pgdn")
        a.render()
        self.assertEqual(a.scroll, 0)

    def test_exit_command_and_ctrl_c(self):
        a = self.app()
        for k in typed("/exit"):
            a.key(k)
        self.assertTrue(a.quit)
        b = self.app()
        b.key("ctrl-c")
        self.assertTrue(b.quit)

    def test_approval_modal_flow(self):
        a = self.app()
        a.add("go", "user")
        res = []
        import threading
        t = threading.Thread(target=lambda: res.append(a.approve("edit", "f.txt\n-old\n+new")))
        t.start()
        for _ in range(50):
            if a.pending:
                break
            time.sleep(0.02)
        self.assertIn("Allow this?", self.text(a))
        self.assertIn("+new", self.text(a))
        a.key("n")
        t.join(3)
        self.assertEqual(res, [False])

    def test_draw_emits_ansi_without_crash(self):
        import io
        a = self.app()
        o = io.StringIO()
        a.draw(o)
        self.assertIn("\033[", o.getvalue())

    def test_key_decoder(self):
        k = tui.KeyReader()
        self.assertEqual(k._decode("\x1b[A"), "up")
        self.assertEqual(k._decode("\x1b[6~"), "pgdn")
        self.assertEqual(k._decode("\x03"), "ctrl-c")
        self.assertEqual(k._decode("ab"), "a")
        self.assertEqual(k.pending, ["b"])


if __name__ == "__main__":
    unittest.main()
