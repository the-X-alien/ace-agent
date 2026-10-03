"""Full-screen terminal interface for Ace (standard library only).

Layout: header, scrolling transcript, status line, input box. Approvals for edits and
shell commands appear as a prompt in the status area (y / n). The agent runs in a
background thread so the screen stays responsive; Ctrl-C stops a running task at the
next step, Ctrl-C when idle quits.
"""
import json
import os
import queue
import re
import shutil
import sys
import textwrap
import threading
import time

from . import __version__
from .chat import Chat

CSI = "\033["

# (command, description, takes_argument)
COMMANDS = [
    ("/help", "Help", False),
    ("/model", "Switch provider / model", True),
    ("/plan", "Read-only planning mode (Tab toggles)", False),
    ("/build", "Back to building", False),
    ("/site", "Build index.html from one idea", True),
    ("/ping", "Check the provider answers", False),
    ("/sessions", "List saved sessions", False),
    ("/resume", "Resume a saved session", True),
    ("/new", "Start a fresh session", False),
    ("/yes-edits", "Stop asking before file edits", False),
    ("/yes-shell", "Stop asking before shell commands", False),
    ("/ask", "Ask before edits and commands again", False),
    ("/exit", "Exit the app", False),
]


STEP = re.compile(r"^step (\d+): (\w+) (\{.*\})$")
LOGO = [
    "   _    ___ ___ ",
    "  /_\\  / __| __|",
    " / _ \\| (__| _| ",
    "/_/ \\_\\___|___|",
]


def pretty_step(line):
    m = STEP.match(line)
    if not m:
        return None
    try:
        a = json.loads(m.group(3))
    except ValueError:
        return None
    what = a.get("path") or a.get("command") or a.get("pattern") or ""
    return "\u25cf %s  %s" % (m.group(2), what)


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
            nice = pretty_step(line)
            if nice:
                m = STEP.match(line)
                try:
                    tgt = json.loads(m.group(3)).get("path")
                except ValueError:
                    tgt = None
                self.app.tool_note(m.group(2), tgt)
                self.app.add(nice, "tool")
            elif self.style == "" and line.strip() and not line.startswith(" "):
                self.app.add("\u258c " + line, "ans")
            else:
                self.app.add(line, self.style)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def md_line(text, fence):
    """Tiny markdown pass for one assistant line. Returns (style, text, fence_open)."""
    body = text[2:] if text.startswith("\u258c ") else text
    if body.lstrip().startswith("```"):
        return "dim", ("\u2500\u2500 " + body.strip()[3:].strip() + " \u2500\u2500") if not fence and body.strip()[3:].strip() else "\u2500\u2500\u2500", not fence
    if fence:
        if body.startswith("+") and not body.startswith("+++"):
            return "add", body, fence
        if body.startswith("-") and not body.startswith("---"):
            return "del", body, fence
        return "code", " " + body, fence
    m = re.match(r"^(#{1,6})\s+(.*)$", body)
    if m:
        return "bold", re.sub(r"\*\*|`", "", m.group(2)), fence
    body = re.sub(r"\*\*(.+?)\*\*", r"\1", body)
    body = re.sub(r"`([^`]+)`", r"\1", body)
    body = re.sub(r"^(\s*)[-*] ", "\\1\u2022 ", body)
    return "ans", body, fence


def wrap(text, width):
    out = []
    for raw in text.split("\n"):
        out.extend(textwrap.wrap(raw, width, replace_whitespace=False, drop_whitespace=False) or [""])
    return out


class App:
    def __init__(self, root, provider=None, keys=None, size=None):
        os.environ["NO_COLOR"] = "1"  # the transcript does its own styling
        self.lines = []  # (style, text)
        self.files, self.nsteps = [], 0
        self.buf = ""
        self.scroll = 0
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.running = False
        self.pending = None  # (kind, detail, queue)
        self.history, self.hpos = [], 0
        self.quit = False
        self.sel = 0
        self.palette = None  # {"q": str, "sel": int} while ctrl+p is open
        self.keys = keys or KeyReader()
        self.size = size or (lambda: shutil.get_terminal_size((80, 24)))
        self.chat = Chat(root, provider, out=Sink(self), err=Sink(self, "dim"))
        self.chat.approve = self.approve
        if self.chat.cfg["providers"].get(self.chat.pname, {}).get("type") == "echo":
            self.add("This is the offline MOCK provider: it cannot use tools. Use /model to pick a real one.", "warn")

    def add(self, text, style=""):
        with self.lock:
            self.lines.append((style, text))

    def approve(self, kind, detail):
        label = {"write": "write a file", "edit": "edit a file", "shell": "run a command"}.get(kind, kind)
        q = queue.Queue()
        self.add("\u250c\u2500 Ace wants to %s " % label + "\u2500" * 20, "warn")
        for l in detail.split("\n"):
            self.add("\u2502 " + l, "add" if l.startswith("+") else "del" if l.startswith("-") else "warn")
        self.add("\u2514" + "\u2500" * 30, "warn")
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
        self.add("  allowed" if ok else "  refused", "ok" if ok else "del")
        return ok

    # ---- rendering -------------------------------------------------
    SIDEBAR = 34

    def tool_note(self, name, target):
        self.nsteps += 1
        if name in ("write_file", "edit_file") and target and target not in self.files:
            self.files.append(target)

    def _flat(self, width):
        with self.lock:
            lines = list(self.lines)
        out = []
        fence = False
        for style, text in lines:
            if style == "brand":
                continue
            if style == "ans":
                style, text, fence = md_line(text, fence)
            for w in wrap(text, max(10, width - 3)):
                out.append((style, w))
        return out

    def _inputbox(self, width):
        """Prompt panel in the same layout as OpenCode's: blue left bar, padded rows, mode and model line."""
        pc = self.chat.cfg["providers"].get(self.chat.pname, {})
        mode = "Plan" if self.chat.plan else ("Build" if not (self.chat.auto_edit or self.chat.allow_shell) else "Build (auto)")
        pad = lambda: [("pbar", "\u2503"), ("panel", " " * (width - 1))]
        if self.buf:
            body = (self.buf[-(width - 6):] + "\u2588").ljust(width - 4)
            mid = [("pbar", "\u2503"), ("panel", "   " + body)]
        else:
            ph = "Ask anything... \"add a contact form to index.html\""
            mid = [("pbar", "\u2503"), ("panel", "   "), ("cursor", "\u2588"), ("pdim", ph[: width - 6].ljust(width - 5))]
        model = pc.get("model") or ""
        used = 3 + len(mode) + 3 + len(model) + (1 + len(self.chat.pname) if model else 0)
        if not model:
            used = 3 + len(mode) + 3 + len(self.chat.pname)
        info = [("pbar", "\u2503"), ("panel", "   "), ("pmode", mode), ("pdim", " \u00b7 "),
                ("panel", model or self.chat.pname), ("pdim", (" " + self.chat.pname) if model else ""),
                ("panel", " " * max(0, width - used - 1))]
        return [pad(), mid, pad(), info, pad()]

    def _splash(self, width, height):
        rows = []
        logo = ["\u2588\u2580\u2580\u2588 \u2588\u2580\u2580\u2580 \u2588\u2580\u2580\u2580",
                "\u2588\u2580\u2580\u2588 \u2588    \u2588\u2580\u2580 ",
                "\u2580  \u2580 \u2580\u2580\u2580\u2580 \u2580\u2580\u2580\u2580"]
        bw = min(width - 4, 75)
        box = self._inputbox(bw)
        content = len(logo) + 2 + len(box) + 4
        top = max(0, (height - content) // 2)
        rows += [[("", "")]] * top
        for l in logo:
            rows.append([("logo", l.center(width))])
        rows.append([("", "")])
        rows.append([("", "")])
        lm = (width - bw) // 2
        for r in box:
            rows.append([("", " " * lm)] + r)
        rows.append([("", " " * lm), ("dim", "tab plan/build   / commands   ctrl+p palette   ctrl+c quit".rjust(bw))])
        if self.chat.cfg["providers"].get(self.chat.pname, {}).get("type") == "echo":
            rows.append([("warn", "Offline MOCK provider: it cannot use tools. Use /model to pick a real one.".center(width))])
        return (rows + [[("", "")]] * height)[:height]

    def _sidebar(self, height):
        w = self.SIDEBAR
        pc = self.chat.cfg["providers"].get(self.chat.pname, {})
        r = [[("", "")]]

        def head(t):
            r.append([("bold", " " + t)])

        def row(k, v):
            r.append([("dim", " %-9s" % k), ("", str(v)[: w - 11])])
        head("Session")
        row("provider", self.chat.pname)
        row("model", pc.get("model") or "-")
        row("steps", self.nsteps)
        row("status", "working..." if self.running else "idle")
        r.append([("", "")])
        head("Modified Files")
        if not self.files:
            r.append([("dim", " none yet")])
        for f in self.files[-(max(1, height - len(r) - 3)):]:
            r.append([("add", " + "), ("", f[: w - 4])])
        return (r + [[("", "")]] * height)[:height]

    def render(self):
        cols, rows_n = self.size()
        cols, rows_n = max(cols, 24), max(rows_n, 9)
        flat0 = self._flat(cols)
        started = any(st == "user" for st, _ in flat0)
        side = cols >= 110 and started
        mw = cols - self.SIDEBAR - 1 if side else cols
        flat = self._flat(mw)
        # footer (1) + optional approval bar (1); prompt box (3) lives in the body on the home screen
        box_h = 0 if not started else 5
        body_h = rows_n - 1 - 1 - box_h
        if not started:
            self.scroll = 0
            body = self._splash(mw, body_h)
        else:
            total = len(flat)
            self.scroll = max(0, min(self.scroll, max(0, total - body_h)))
            end = total - self.scroll
            view = flat[max(0, end - body_h):end]
            body = []
            for st, t in view:
                if st == "user":
                    body.append([("ubar", " \u2503 "), ("user", t.ljust(mw - 3))])
                elif st == "ans":
                    body.append([("", "   " + (t[2:] if t.startswith("\u258c ") else t))])
                elif st == "code":
                    body.append([("", "   "), ("panel", t.ljust(max(0, mw - 6)))])
                elif st in ("add", "del", "bold"):
                    body.append([("", "   "), (st, t)])
                elif st == "tool":
                    body.append([("tool", "   " + t)])
                else:
                    body.append([(st, "   " + t)])
            body = ([[("", "")]] * (body_h - len(body))) + body
            if side:
                sb = self._sidebar(body_h)
                body = [self._pad(a, mw) + [("sep", "\u2502")] + b for a, b in zip(body, sb)]
        if self.pending:
            mid = [("warnbar", " \u26a0  Allow this?   [y] yes   [n] no ".ljust(cols))]
        elif self.running:
            mid = [("dim", "   working... Ctrl-C stops after the current step")]
        else:
            mid = [("", "")]
        out = body + [mid]
        if started:
            out += [self._pad(r, cols) for r in self._inputbox(cols)]
        right = "ace %s " % __version__
        room = max(0, cols - len(right) - 2)
        path = os.path.abspath(self.chat.root)
        if len(path) > room:
            path = ("..." + path[-max(0, room - 3):]) if room > 3 else path[:room]
        left = " " + path
        out.append([("dim", left + " " * max(1, cols - len(left) - len(right)) + right)])
        out = self._overlay(out, cols)
        return [self._clip(r, cols) for r in out]

    @staticmethod
    def _clip(row, width):
        res, used = [], 0
        for st, t in row:
            if used >= width:
                break
            t = t[: width - used]
            used += len(t)
            res.append((st, t))
        return res

    @staticmethod
    def _pad(row, width):
        n = sum(len(t) for _, t in row)
        return row + [("", " " * (width - n))] if n < width else row

    @staticmethod
    def plain(row):
        return "".join(t for _, t in row)

    def draw(self, out=None):
        out = out or sys.stdout
        cols = self.size()[0]
        cols = max(cols, 24)
        sty = {"bold": "1", "dim": "38;2;128;128;128", "code": "48;2;30;30;30;38;2;238;238;238", "warn": "38;2;250;178;131", "add": "38;2;127;216;143", "del": "38;2;224;108;117",
               "tool": "38;2;128;128;128", "ok": "38;2;127;216;143", "logo": "1;38;2;238;238;238", "sep": "38;2;60;60;60",
               "user": "48;2;30;30;30;38;2;238;238;238", "ubar": "48;2;30;30;30;38;2;92;156;245",
               "pbar": "48;2;30;30;30;38;2;92;156;245", "panel": "48;2;30;30;30;38;2;238;238;238", "pdim": "48;2;30;30;30;38;2;110;110;110",
               "pmode": "48;2;30;30;30;1;38;2;92;156;245", "cursor": "48;2;30;30;30;38;2;250;178;131",
               "warnbar": "1;48;2;250;178;131;38;2;10;10;10", "accent": "38;2;250;178;131", "pop": "48;2;30;30;30;38;2;238;238;238", "sel": "48;2;250;178;131;38;2;10;10;10"}
        buf = [CSI + "H"]
        rows = self.render()
        for i, row in enumerate(rows):
            used = 0
            for st, t in row:
                if used >= cols:
                    break
                t = t[: cols - used]
                used += len(t)
                if isinstance(st, tuple):
                    c = "1;38;2;%d;%d;%d" % st[1]
                else:
                    c = sty.get(st, "")
                buf.append((CSI + c + "m" + t + CSI + "0m") if c else t)
            buf.append(CSI + "K" + ("\r\n" if i < len(rows) - 1 else ""))
        out.write("".join(buf))
        out.flush()

    def popup_items(self):
        if self.palette is not None:
            q = self.palette["q"].lower()
            return [c for c in COMMANDS if q in c[0].lower() or q in c[1].lower()]
        if self.buf.startswith("/") and " " not in self.buf and not self.pending:
            return [c for c in COMMANDS if c[0].startswith(self.buf)]
        return []

    def _overlay(self, out, cols):
        items = self.popup_items()
        if self.palette is not None:
            w = min(cols - 4, 60)
            lm = (cols - w) // 2
            rows = [[("", " " * lm), ("pop", " Commands".ljust(w - 5) + "esc  ")],
                    [("", " " * lm), ("pop", (" " + (self.palette["q"] or "") + "\u2588" if self.palette["q"] else " Search").ljust(w))],
                    [("", " " * lm), ("pop", " " * w)]]
            self.palette["sel"] = max(0, min(self.palette["sel"], len(items) - 1))
            for i, (c, d, _) in enumerate(items[:14]):
                rows.append([("", " " * lm), ("sel" if i == self.palette["sel"] else "pop", (" %s  %s" % (c.ljust(11), d)).ljust(w))])
            if not items:
                rows.append([("", " " * lm), ("pop", " no matching command".ljust(w))])
            rows.append([("", " " * lm), ("pop", " " * w)])
            top = 2
            for i, r in enumerate(rows):
                if top + i < len(out) - 1:
                    out[top + i] = r
            return out
        if not items:
            return out
        self.sel = max(0, min(self.sel, len(items) - 1))
        box_top = next((i for i, r in enumerate(out) if any(st == "pbar" for st, _ in r[:2])), None)
        if box_top is None:
            return out
        r0 = out[box_top]
        lm = len(r0[0][1]) if r0 and r0[0][0] == "" and not r0[0][1].strip() else 0
        w = sum(len(t) for _, t in r0) - lm
        shown = items[:14]
        for i, (c, d, _) in enumerate(shown):
            row = box_top - len(shown) + i
            if row < 1:
                continue
            out[row] = [("", " " * lm), ("sel" if i == self.sel else "pop", (" %s  %s" % (c.ljust(11), d)).ljust(w))]
        return out

    # ---- input -----------------------------------------------------
    def start(self, text):
        self.running = True

        def work():
            try:
                self.chat.task(text)
            finally:
                self.running = False
        threading.Thread(target=work, daemon=True).start()

    def run_command(self, c):
        cmd, _, needs_arg = c
        if needs_arg:
            self.buf = cmd + " "
            return
        self.buf = ""
        self.add(cmd, "user")
        if not self.chat.command(cmd):
            self.quit = True

    def key(self, k):
        if k is None:
            return
        if self.palette is not None:
            items = self.popup_items()
            if k == "esc" or k == "ctrl-p" or k == "ctrl-c":
                self.palette = None
            elif k == "enter":
                idx = self.palette["sel"]
                self.palette = None
                if items:
                    self.run_command(items[max(0, min(idx, len(items) - 1))])
            elif k == "up":
                self.palette["sel"] = max(0, self.palette["sel"] - 1)
            elif k == "down":
                self.palette["sel"] += 1
            elif k == "backspace":
                self.palette["q"] = self.palette["q"][:-1]
            elif len(k) == 1 and k.isprintable():
                self.palette["q"] += k
                self.palette["sel"] = 0
            return
        if k == "ctrl-p" and not self.pending:
            self.palette = {"q": "", "sel": 0}
            return
        if k == "tab" and not self.pending and not self.running and not self.popup_items():
            self.chat.plan = not self.chat.plan
            return
        items = self.popup_items()
        if items and not self.pending:
            if k == "up":
                self.sel = (self.sel - 1) % len(items)
                return
            if k == "down":
                self.sel = (self.sel + 1) % len(items)
                return
            if k == "tab":
                self.buf = items[self.sel % len(items)][0] + " "
                self.sel = 0
                return
            if k == "enter" and self.buf not in [c[0] for c in items]:
                self.run_command(items[self.sel % len(items)])
                self.sel = 0
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
            self.add(line, "user")
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
            keys.append({"\r": "enter", "\n": "enter", "\x7f": "backspace", "\x08": "backspace", "\x03": "ctrl-c", "\x04": "ctrl-c", "\x10": "ctrl-p", "\t": "tab"}.get(ch, ch))
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
                return {"\r": "enter", "\x08": "backspace", "\x03": "ctrl-c", "\x10": "ctrl-p", "\t": "tab"}.get(ch, ch)
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
