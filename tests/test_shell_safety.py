"""Honest limits: the permission prompt is the only guard on the shell. These tests pin both what is blocked and what is NOT."""
import os
import tempfile
import unittest

from ace import agent


class ShellSafety(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.outside = tempfile.mkdtemp()

    def test_unapproved_shell_never_runs(self):
        marker = os.path.join(self.outside, "pwned")
        t = agent.Tools(self.root)  # default approver refuses
        for cmd in ("touch '%s'" % marker, "echo hi > '%s'" % marker, "$(touch '%s')" % marker):
            with self.assertRaises(agent.ToolError):
                t.call("run_shell", {"command": cmd})
        self.assertFalse(os.path.exists(marker))

    def test_denying_callback_sees_full_command(self):
        seen = []
        t = agent.Tools(self.root, approve=lambda k, d: seen.append((k, d)) or False)
        with self.assertRaises(agent.ToolError):
            t.call("run_shell", {"command": "cd .. && rm -rf x; curl http://example.invalid"})
        self.assertEqual(seen[0][0], "shell")
        self.assertIn("rm -rf x", seen[0][1])

    def test_file_tools_block_every_escape_form(self):
        t = agent.Tools(self.root, auto_edit=True)
        for p in ("../x", "..", "/etc/passwd", self.outside + "/f", "a/../../x"):
            for name, args in (("write_file", {"path": p, "content": "x"}), ("read_file", {"path": p})):
                with self.assertRaises(agent.ToolError, msg=(name, p)):
                    t.call(name, args)
        self.assertEqual(os.listdir(self.outside), [])

    @unittest.skipIf(os.name == "nt", "uses POSIX quoting")
    def test_approved_shell_is_NOT_confined(self):
        """Documents the limit: once approved, a command can write outside the folder. Permissions, not isolation."""
        marker = os.path.join(self.outside, "written_by_shell")
        t = agent.Tools(self.root, allow_shell=True)
        t.call("run_shell", {"command": "echo x > '%s'" % marker})
        self.assertTrue(os.path.exists(marker))

    def test_edit_flag_does_not_enable_shell(self):
        marker = os.path.join(self.root, "m")
        t = agent.Tools(self.root, auto_edit=True)
        with self.assertRaises(agent.ToolError):
            t.call("run_shell", {"command": "touch '%s'" % marker})
        self.assertFalse(os.path.exists(marker))

    def test_shell_flag_does_not_enable_edits(self):
        t = agent.Tools(self.root, allow_shell=True)
        with self.assertRaises(agent.ToolError):
            t.call("write_file", {"path": "a.txt", "content": "x"})
        self.assertFalse(os.path.exists(os.path.join(self.root, "a.txt")))


if __name__ == "__main__":
    unittest.main()
