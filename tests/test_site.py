import os
import tempfile
import unittest

from ace import site


class R:
    def __init__(self, text):
        self.text = text


class Scripted:
    """Scripted stand-in model: checks the staged site builder's parsing, not model quality."""
    def __init__(self):
        self.n = 0
        self.answers = [
            "Name: Bean There\nBrewing joy since 2020",
            "Bean There is a cozy neighbourhood coffee shop with friendly staff and fresh beans every morning.",
            "1. Latte | $4.50 | Espresso with milk\n2. Mocha | $5.00 | Chocolate coffee\n3. Tea | $3.00 | Hot tea",
            "Great coffee and a warm welcome every time I visit | Maya\nThe pastries are fresh and the staff is lovely | Sam",
            "Menu\n12 Main St, Portland\nOpening hours: 9:00 AM - 5:00 PM\n(503) 555-0100",
        ]

    def complete(self, prompt, timeout=0):
        a = self.answers[min(self.n, len(self.answers) - 1)]
        self.n += 1
        return R(a)


class SiteTest(unittest.TestCase):
    def test_build_parses_and_passes_check(self):
        page, hard = site.build(Scripted(), "coffee shop", say=lambda m: None)
        self.assertEqual(hard, [])
        for s in ("Bean There", "Brewing joy since 2020", "$4.50", "Maya", "9:00 AM - 5:00 PM", "(503) 555-0100", "<h2>Menu</h2>"):
            self.assertIn(s, page)

    def test_long_section_title_and_missing_phone(self):
        m = Scripted()
        m.answers[4] = "Menu/Services: Grooming for dogs\n1 Main St\n9am-5pm\n9am-4pm"
        page, hard = site.build(m, "x", say=lambda s: None)
        self.assertIn("<h2>Menu</h2>", page)
        self.assertNotIn("Grooming for dogs", page)

    def test_unusable_answers_raise(self):
        class Bad:
            def complete(self, p, timeout=0):
                return R("")
        with self.assertRaises(RuntimeError):
            site.build(Bad(), "x", say=lambda m: None)

    def test_run_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "index.html")
            site.run(Scripted(), "coffee", out, say=lambda m: None)
            self.assertTrue(os.path.getsize(out) > 1000)


if __name__ == "__main__":
    unittest.main()
