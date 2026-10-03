"""Full-screen terminal interface for Ace (standard library only).

Layout: header, scrolling transcript, status line, input box. Approvals for edits and
shell commands appear as a prompt in the status area (y / n). The agent runs in a
background thread so the screen stays responsive; Ctrl-C stops a running task at the
next step, Ctrl-C when idle quits.
"""
import os
import queue
import shutil
import sys
import textwrap
import threading
import time

from . import __version__
from .chat import Chat

CSI = "\033["


class Sink:
    """File-like object that turns writes into transcript lines."""

    def __init__(self, app, style=""):
        self.app, self.style, self.buf = app, style, ""

    def write(self, s):
        if self.app.cancel.is_set():
            self.app.cancel.clear()
            raise KeyboardInterrupt
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.app.add(line, self.style)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def wrap(text, width):
    out = []
    for raw in text.split("\n"):
        out.extend(textwrap.wrap(raw, width, replace_whitespace=False, drop_whitespace=False) or [""])
    return out


class App:
    def __init__(self, root, provider=None, keys=None, size=None):
        os.environ["NO_COLOR"] = "1"  # the transcript does its own styling
        self.lines = []  # (style, text)
        self.buf = ""
        self.scroll = 0
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.running = False
        self.pending = None  # (kind, detail, queue)
        self.history, self.hpos = [], 0
        self.quit = False
        self.keys = keys or KeyReader()
        self.size = size or (lambda: shutil.get_terminal_size((80, 24)))
        self.chat = Chat(root, provider, out=Sink(self), err=Sink(self, "dim"))
        self.chat.approve = self.approve
        self.add("Ace %s  folder: %s" % (__version__, root), "bold")
        if self.chat.cfg["providers"].get(self.chat.pname, {}).get("type") == "echo":
            self.add("This is the offline MOCK provider: it cannot use tools. Use /model to pick a real one.", "warn")
        self.add("Edits and commands ask first. Enter sends, /help lists commands, PgUp/PgDn scroll, Ctrl-C stops or quits.", "dim")

    def add(self, text, style=""):
        with self.lock:
            self.lines.append((style, text))

    def approve(self, kind, detail):
        label = {"write": "write a file", "edit": "edit a file", "shell": "run a command"}.get(kind, kind)
        q = queue.Queue()
        self.add("Ace wants to %s:" % label, "warn")
        for l in detail.split("\n"):
            self.add("  " + l, "add" if l.startswith("+") else "del" if l.startswith("-") else "")
        self.pending = (kind, detail, q)
        while True:
            try:
                ok = q.get(timeout=0.1)
                break
            except queue.Empty:
                if self.cancel.is_set():
                    self.cancel.clear()
                    ok = False
                    break
        self.pending = None
        self.add("  -> allowed" if ok else "  -> refused", "dim")
        return ok

    # ---- rendering -------------------------------------------------
    def render(self):
        cols, rows = self.size()
        cols, rows = max(cols, 20), max(rows, 6)
        body_h = rows - 3
        header = " Ace %s | %s | %s%s " % (
            __version__, self.chat.pname,
            "edits+shell auto" if self.chat.auto_edit and self.chat.allow_shell else "auto-edit" if self.chat.auto_edit else "asks first",
            " | working..." if self.running else "")
        with self.lock:
            lines = list(self.lines)
        flat = []
        for style, text in lines:
            for w in wrap(text, cols):
                flat.append((style, w))
        total = len(flat)
        self.scroll = max(0, min(self.scroll, max(0, total - body_h)))
        end = total - self.scroll
        view = flat[max(0, end - body_h):end]
        view = [("", "")] * (body_h - len(view)) + view
        if self.pending:
            status = " Allow this? y = yes, n = no "
        elif self.running:
            status = " Ctrl-C stops the task "
        else:
            status = " /help  /model  /sessions  /resume ID  /new  /yes-edits  /yes-shell  /exit "
        prompt = "> " + self.buf
        return [("hdr", header[:cols].ljust(cols))] + [(s, t[:cols]) for s, t in view] + [
            ("status", status[:cols].ljust(cols)), ("", prompt[-cols:])]

    def draw(self, out=None):
        out = out or sys.stdout
        codes = {"hdr": "7", "bold": "1", "dim": "2", "warn": "33", "add": "32", "del": "31", "status": "7;36"}
        buf = [CSI + "H"]
        for style, text in self.render():
            c = codes.get(style)
            buf.append((CSI + c + "m" + text + CSI + "0m" if c else text) + CSI + "K\r\n")
        out.write("".join(buf)[:-2])
        out.flush()

    # ---- input -----------------------------------------------------
    def start(self, text):
        self.running = True

        def work():
            try:
                self.chat.task(text)
            finally:
                self.running = False
        threading.Thread(target=work, daemon=True).start()

    def key(self, k):
        if k is None:
            return
        if self.pending:
            if k in ("y", "Y"):
                self.pending[2].put(True)
            elif k in ("n", "N", "esc"):
                self.pending[2].put(False)
            elif k == "ctrl-c":
                self.cancel.set()
            return
        if k == "ctrl-c":
            if self.running:
                self.cancel.set()
                self.add("(stopping at the next step)", "dim")
            else:
                self.quit = True
        elif k == "pgup":
            self.scroll += max(1, self.size()[1] - 5)
        elif k == "pgdn":
            self.scroll = max(0, self.scroll - max(1, self.size()[1] - 5))
        elif k == "up" and self.history and not self.running:
            self.hpos = max(0, self.hpos - 1)
            self.buf = self.history[self.hpos]
        elif k == "down" and self.history:
            self.hpos = min(len(self.history), self.hpos + 1)
            self.buf = self.history[self.hpos] if self.hpos < len(self.history) else ""
        elif k == "backspace":
            self.buf = self.buf[:-1]
        elif k == "enter":
            line, self.buf = self.buf.strip(), ""
            if not line or self.running:
                if line:
                    self.add("(still working, wait or press Ctrl-C)", "dim")
                return
            self.history.append(line)
            self.hpos = len(self.history)
            self.scroll = 0
            self.add("> " + line, "bold")
            if line.startswith("/"):
                if not self.chat.command(line):
                    self.quit = True
            else:
                self.start(line)
        elif len(k) == 1 and k.isprintable():
            self.buf += k

    def run(self):
        with Screen():
            while not self.quit:
                k = self.keys.read(0.05)
                self.key(k)
                self.draw()
        return 0


class KeyReader:
    def __init__(self):
        self.win = os.name == "nt"
        self.pending = []

    def read(self, timeout):
        if self.pending:
            return self.pending.pop(0)
        return self._win(timeout) if self.win else self._unix(timeout)

    def _unix(self, timeout):
        import select
        if not select.select([sys.stdin], [], [], timeout)[0]:
            return None
        data = os.read(sys.stdin.fileno(), 32).decode("utf-8", "ignore")
        return self._decode(data)

    def _decode(self, data):
        table = {"\x1b[A": "up", "\x1b[B": "down", "\x1b[5~": "pgup", "\x1b[6~": "pgdn", "\x1bOA": "up", "\x1bOB": "down"}
        if data in table:
            return table[data]
        keys = []
        i = 0
        while i < len(data):
            ch = data[i]
            if ch == "\x1b":
                seq = data[i:i + 4]
                for t, v in table.items():
                    if seq.startswith(t):
                        keys.append(v)
                        i += len(t)
                        break
                else:
                    keys.append("esc")
                    i += 1
                continue
            keys.append({"\r": "enter", "\n": "enter", "\x7f": "backspace", "\x08": "backspace", "\x03": "ctrl-c", "\x04": "ctrl-c"}.get(ch, ch))
            i += 1
        self.pending.extend(keys[1:])
        return keys[0] if keys else None

    def _win(self, timeout):
        import msvcrt
        end = time.time() + timeout
        while time.time() < end:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    return {"H": "up", "P": "down", "I": "pgup", "Q": "pgdn"}.get(msvcrt.getwch())
                return {"\r": "enter", "\x08": "backspace", "\x03": "ctrl-c"}.get(ch, ch)
            time.sleep(0.01)
        return None


class Screen:
    """Alternate screen + raw-ish input; restores the terminal on exit."""

    def __enter__(self):
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            m = ctypes.c_uint32()
            k.GetConsoleMode(h, ctypes.byref(m))
            k.SetConsoleMode(h, m.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        else:
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
            new = termios.tcgetattr(self.fd)
            new[3] &= ~termios.ISIG  # deliver Ctrl-C as a key, not a signal
            termios.tcsetattr(self.fd, termios.TCSADRAIN, new)
        sys.stdout.write(CSI + "?1049h" + CSI + "2J")
        sys.stdout.flush()
        return self

    def __exit__(self, *a):
        sys.stdout.write(CSI + "0m" + CSI + "?1049l")
        sys.stdout.flush()
        if os.name != "nt":
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)


def main(root=None, provider=None):
    from . import config as cfgmod
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("The full-screen interface needs a real terminal. Use `ace-agent chat` for plain text.", file=sys.stderr)
        return 2
    return App(root or cfgmod.root(), provider).run()
