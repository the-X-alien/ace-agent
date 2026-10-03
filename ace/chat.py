"""Interactive terminal chat for Ace: talk to a model that can work on the files in the current folder."""
import os
import sys

from . import __version__
from . import agent as agentmod
from . import config as cfgmod
from . import providers, runner

HELP = """Type a task and press Enter. Commands:
  /model            list providers, /model NAME [MODEL] switches (and sets the model for this session)
  /plan  /build     read-only planning mode, or back to building
  /yes-edits        stop asking before file edits and writes   (/ask turns questions back on)
  /yes-shell        stop asking before shell commands
  /sessions         list saved sessions, /resume ID continues one, /new starts fresh
  /site IDEA        build index.html from one idea in small steps (needs a real provider)
  /ping             check that the current provider answers
  /help  /exit      (Ctrl-C stops a running task)"""


def _c(code, s):
    return "\033[%sm%s\033[0m" % (code, s) if sys.stdout.isatty() and not os.environ.get("NO_COLOR") else s


class Chat:
    def __init__(self, root, provider_name=None, inp=input, out=None, err=None):
        self.root = root
        self.cfg = cfgmod.load(root)
        self.pname = provider_name or self.cfg.get("default_provider", "mock")
        self.inp, self.out, self.err = inp, out or sys.stdout, err or sys.stderr
        self.auto_edit = self.allow_shell = False
        self.plan = False
        self.sid = None

    def say(self, s=""):
        print(s, file=self.out)

    def note(self, s):
        print(s, file=self.err)

    def approve(self, kind, detail):
        label = {"write": "write a file", "edit": "edit a file", "shell": "run a command"}.get(kind, kind)
        self.note(_c("33", "\nAce wants to %s:" % label) + "\n" + detail)
        try:
            return self.inp("Allow? [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            return False

    def banner(self):
        self.say(_c("1", "Ace %s" % __version__) + "  folder: %s  provider: %s" % (self.root, self.pname))
        if self.cfg["providers"].get(self.pname, {}).get("type") == "echo":
            self.note(_c("33", "This is the offline MOCK provider: it cannot use tools. Pick a real one with /model."))
        self.say("Edits and commands ask first. /help for commands, /exit to leave.")

    def command(self, line):
        parts = line.split(None, 1)
        cmd, arg = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        if cmd in ("/exit", "/quit"):
            return False
        if cmd == "/help":
            self.say(HELP)
        elif cmd == "/model":
            if arg:
                name, _, model = arg.partition(" ")
                if name not in self.cfg["providers"]:
                    self.say("No provider named %s." % name)
                else:
                    self.pname = name
                    if model.strip():
                        self.cfg["providers"][name] = dict(self.cfg["providers"][name], model=model.strip())
                    self.say("Provider: %s%s" % (name, ", model " + model.strip() if model.strip() else ""))
            else:
                for n, c in self.cfg["providers"].items():
                    self.say("%s %s  (%s%s)" % ("*" if n == self.pname else " ", n, c.get("type"), ", model " + c["model"] if c.get("model") else ""))
        elif cmd == "/plan":
            self.plan = True
            self.say("Plan mode: the model can read and search but not write, edit or run commands. /build to go back.")
        elif cmd == "/build":
            self.plan = False
            self.say("Build mode.")
        elif cmd == "/yes-edits":
            self.auto_edit = True
            self.say("Edits and writes will not ask. /ask to undo.")
        elif cmd == "/yes-shell":
            self.allow_shell = True
            self.say("Commands will not ask. /ask to undo.")
        elif cmd == "/ask":
            self.auto_edit = self.allow_shell = False
            self.say("Asking before edits and commands.")
        elif cmd == "/ping":
            try:
                rep = runner.get_provider(self.cfg, self.pname).complete("Reply with the single word: pong", timeout=60)
                self.say("%s answered in %.1fs%s" % (self.pname, rep.seconds, " (MOCK, proves nothing)" if getattr(rep, "mock", False) else ": " + rep.text.strip()[:60]))
            except providers.ProviderError as e:
                self.say("FAILED: %s" % e)
        elif cmd == "/site":
            from . import site
            if not arg:
                self.say("Usage: /site a one-line description of the site")
            elif self.cfg["providers"].get(self.pname, {}).get("type") == "echo":
                self.say("The mock provider cannot write a site. Pick a real one with /model.")
            else:
                try:
                    site.run(runner.get_provider(self.cfg, self.pname), arg, os.path.join(self.root, "index.html"), say=self.say)
                except (providers.ProviderError, RuntimeError) as e:
                    self.say("Could not build the site: %s" % e)
        elif cmd == "/sessions":
            for sid, n, first in agentmod.list_sessions(self.root):
                self.say("%s  %d events  %s" % (sid, n, first))
        elif cmd == "/resume":
            try:
                agentmod.load_session(self.root, arg)
                self.sid = arg
                self.say("Resumed %s." % arg)
            except agentmod.ToolError as e:
                self.say(str(e))
        elif cmd == "/new":
            self.sid = None
            self.say("Fresh session.")
        else:
            self.say("Unknown command. /help")
        return True

    def task(self, text):
        try:
            prov = runner.get_provider(self.cfg, self.pname)
        except Exception as e:
            self.say("error: %s" % e)
            return
        tools = agentmod.Tools(self.root, approve=self.approve, auto_edit=self.auto_edit, allow_shell=self.allow_shell, read_only=self.plan)
        try:
            res = agentmod.run(prov, text, self.root, tools, session=self.sid, say=lambda m: self.note(_c("2", m)))
        except KeyboardInterrupt:
            self.say("\nStopped.")
            return
        except providers.ProviderError as e:
            self.say("error: %s" % e)
            return
        self.sid = res["session"]
        self.say(res["answer"] or "(stopped after %d steps without a final answer; type a follow-up to continue)" % res["steps"])

    def loop(self):
        self.banner()
        while True:
            try:
                line = self.inp(_c("36", "ace> ")).strip()
            except (EOFError, KeyboardInterrupt):
                self.say()
                return 0
            if not line:
                continue
            if line.startswith("/"):
                if not self.command(line):
                    return 0
            else:
                self.task(line)


def main(root=None, provider=None):
    return Chat(root or cfgmod.root(), provider).loop()
