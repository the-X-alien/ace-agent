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

    def test_long_folder_path_never_overflows(self):
        long_root = os.path.join(self.d, "a" * 90, "b" * 30)
        for w in (24, 40, 80, 140):
            a = tui.App(long_root, size=lambda w=w: (w, 12))
            a.add("hi", "user")
            self.assertTrue(all(sum(len(x) for _, x in row) <= w for row in a.render()), w)

    def test_slash_popup_filters_and_runs(self):
        a = self.app((100, 30))
        a.key("/")
        txt = self.text(a)
        self.assertIn("/model", txt)
        self.assertIn("Exit the app", txt)
        a.key("m")
        a.key("o")
        self.assertEqual([c[0] for c in a.popup_items()], ["/model"])
        a.key("tab")
        self.assertEqual(a.buf, "/model ")
        b = self.app((100, 30))
        for k in list("/ex") + ["enter"]:
            b.key(k)
        self.assertTrue(b.quit)

    def test_palette_open_search_run_escape(self):
        a = self.app((100, 30))
        a.key("ctrl-p")
        self.assertIn("Commands", self.text(a))
        for k in "sess":
            a.key(k)
        self.assertEqual(a.popup_items()[0][0], "/sessions")
        a.key("enter")
        self.assertIsNone(a.palette)
        c = self.app((100, 30))
        c.key("ctrl-p")
        c.key("esc")
        self.assertIsNone(c.palette)
        self.assertFalse(c.quit)

    def test_overlays_never_overflow(self):
        for w, h in ((24, 9), (40, 12), (100, 30)):
            a = tui.App(self.d, size=lambda w=w, h=h: (w, h))
            a.key("/")
            self.assertTrue(all(sum(len(x) for _, x in row) <= w for row in a.render()))
            a.buf = ""
            a.key("ctrl-p")
            self.assertTrue(all(sum(len(x) for _, x in row) <= w for row in a.render()))
            self.assertEqual(len(a.render()), max(h, 9))

    def test_markdown_and_diff_render(self):
        a = self.app((100, 30))
        a.add("hi", "user")
        for ln in ["# Title", "use **bold** and `code`", "- item", "```diff", "+new line", "-old line", " same", "```", "after"]:
            a.add("\u258c " + ln, "ans")
        flat = a._flat(100)
        styles = [(st, tx) for st, tx in flat]
        self.assertIn(("bold", "Title"), styles)
        self.assertIn(("ans", "use bold and code"), styles)
        self.assertIn(("ans", "\u2022 item"), styles)
        self.assertIn(("add", "+new line"), styles)
        self.assertIn(("del", "-old line"), styles)
        self.assertIn(("ans", "after"), styles)
        txt = self.text(a)
        self.assertNotIn("```", txt)
        self.assertNotIn("**", txt)

    def test_typing_and_command(self):
        a = self.app()
        for k in typed("/help"):
            a.key(k)
        self.assertIn("/help  /exit", self.text(a))

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
