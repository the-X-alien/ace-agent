"""Ace agent loop: a model that can read, search, edit files and run commands inside one project folder.

Works with any Ace provider, because tool calls travel as plain text blocks:

    ```ace-tool
    {"tool": "read_file", "args": {"path": "src/app.py"}}
    ```

The loop runs the tool, adds the result to the transcript, and calls the model again until it answers without a tool block
or the step limit is reached. Everything the model can touch stays inside the workspace root. Edits and shell commands
need approval unless you opt in with a flag.
"""
import json
import os
import re
import subprocess
import time
import uuid

MAX_READ = 20000
MAX_OUT = 8000
SHELL_TIMEOUT = 60
TOOL_RE = re.compile(r"```ace-tool\s*\n(.*?)\n?```", re.S)

SYSTEM = """You are Ace, a coding agent working inside one project folder. Do the user's task by using tools, then give a short final answer.

To use a tool, reply with exactly one block and nothing after it:

```ace-tool
{"tool": "<name>", "args": {...}}
```

Tools:
- list_dir {"path": "."}                       list files and folders
- read_file {"path": "a.py", "start": 1, "end": 200}   read lines (start and end optional)
- search {"pattern": "text or regex", "path": "."}      find lines in files
- write_file {"path": "a.py", "content": "..."}         create or overwrite a file
- edit_file {"path": "a.py", "old": "exact text", "new": "replacement"}   replace one exact, unique piece of text
- run_shell {"command": "python -m unittest"}           run a command in the project folder (60s limit)

Rules: paths are relative to the project folder. Read a file before editing it. Make the smallest change that does the task, then check it with run_shell when a check exists. When the task is done, reply in plain text with no tool block."""


class ToolError(Exception):
    pass


class Workspace:
    def __init__(self, root):
        self.root = os.path.realpath(root)

    def path(self, p):
        if not isinstance(p, str) or not p:
            raise ToolError("path is required")
        full = os.path.realpath(os.path.join(self.root, p))
        if full != self.root and not full.startswith(self.root + os.sep):
            raise ToolError("path is outside the project folder: %s" % p)
        return full

    def rel(self, full):
        return os.path.relpath(full, self.root)


def _clip(s, n=MAX_OUT):
    return s if len(s) <= n else s[:n] + "\n...[%d more characters cut]" % (len(s) - n)


class Tools:
    def __init__(self, root, approve=None, auto_edit=False, allow_shell=False, shell_timeout=SHELL_TIMEOUT):
        self.ws = Workspace(root)
        self.approve = approve or (lambda kind, detail: False)
        self.auto_edit, self.allow_shell, self.shell_timeout = auto_edit, allow_shell, shell_timeout

    def call(self, name, args):
        fn = getattr(self, "t_" + str(name), None)
        if fn is None or not isinstance(args, dict):
            raise ToolError("unknown tool or bad arguments: %s" % name)
        return fn(**args)

    def t_list_dir(self, path="."):
        d = self.ws.path(path)
        if not os.path.isdir(d):
            raise ToolError("not a folder: %s" % path)
        out = []
        for n in sorted(os.listdir(d))[:300]:
            if n in (".git", "node_modules", "__pycache__", ".ace"):
                continue
            out.append(n + ("/" if os.path.isdir(os.path.join(d, n)) else ""))
        return "\n".join(out) or "(empty)"

    def t_read_file(self, path, start=1, end=None):
        f = self.ws.path(path)
        if not os.path.isfile(f):
            raise ToolError("not a file: %s" % path)
        with open(f, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
        s = max(int(start or 1), 1)
        e = int(end) if end else len(lines)
        body = "\n".join("%d: %s" % (i + 1, l) for i, l in enumerate(lines[s - 1:e], s - 1))
        return _clip(body, MAX_READ)

    def t_search(self, pattern, path="."):
        base = self.ws.path(path)
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        hits = []
        files = [base] if os.path.isfile(base) else [os.path.join(d, f) for d, ds, fs in os.walk(base)
                                                      if not any(x in d.split(os.sep) for x in (".git", "node_modules", "__pycache__", ".ace")) for f in fs]
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            hits.append("%s:%d: %s" % (self.ws.rel(f), i, line.rstrip()[:200]))
                            if len(hits) >= 80:
                                return "\n".join(hits) + "\n...[more matches cut]"
            except (OSError, UnicodeDecodeError):
                continue
        return "\n".join(hits) or "no matches"

    def _ok(self, kind, detail):
        if kind in ("write", "edit") and self.auto_edit:
            return True
        if kind == "shell" and self.allow_shell:
            return True
        return bool(self.approve(kind, detail))

    def t_write_file(self, path, content):
        f = self.ws.path(path)
        if not isinstance(content, str):
            raise ToolError("content must be text")
        if not self._ok("write", "%s (%d characters)" % (path, len(content))):
            raise ToolError("the user did not approve this write")
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        msg = "wrote %s (%d characters)" % (path, len(content))
        if path.lower().endswith((".html", ".htm")):
            from . import gate
            bad = [m for lvl, m in gate.check_html(content) if lvl == "hard"]
            if bad:
                msg += "\nPROBLEMS to fix with another write_file of the same path: " + "; ".join(bad)
        return msg

    def t_edit_file(self, path, old, new):
        f = self.ws.path(path)
        if not os.path.isfile(f):
            raise ToolError("not a file: %s" % path)
        with open(f, encoding="utf-8", newline="") as fh:
            text = fh.read()
        n = text.count(old) if old else 0
        if n != 1:
            raise ToolError("old text must appear exactly once, found %d times" % n)
        if not self._ok("edit", "%s\n%s\n%s" % (path, "\n".join("- " + l for l in old.split("\n")[:12]), "\n".join("+ " + l for l in new.split("\n")[:12]))):
            raise ToolError("the user did not approve this edit")
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace(old, new, 1))
        return "edited %s" % path

    def t_run_shell(self, command):
        if not isinstance(command, str) or not command.strip():
            raise ToolError("command is required")
        if not self._ok("shell", command):
            raise ToolError("the user did not approve this command")
        try:
            r = subprocess.run(command, shell=True, cwd=self.ws.root, capture_output=True, text=True,
                               timeout=self.shell_timeout, errors="replace")
        except subprocess.TimeoutExpired:
            raise ToolError("command timed out after %ds" % self.shell_timeout)
        out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
        return "exit code %d\n%s" % (r.returncode, _clip(out))


def parse_tool(text):
    """Return (tool, args) from the first ace-tool block, ('error', message) for a broken block, or None."""
    m = TOOL_RE.search(text)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
        if not isinstance(d, dict) or "tool" not in d:
            raise ValueError("missing tool")
        args = d.get("args")
        if not isinstance(args, dict):
            # small models often put the arguments next to "tool" instead of under "args"
            args = {k: v for k, v in d.items() if k != "tool"}
        return d["tool"], args
    except ValueError as e:
        return "error", "could not read the tool block: %s" % e


def sessions_dir(root):
    return os.path.join(root, ".ace", "sessions")


def load_session(root, sid):
    p = os.path.join(sessions_dir(root), sid + ".jsonl")
    if not os.path.isfile(p):
        raise ToolError("no such session: %s" % sid)
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def list_sessions(root):
    d = sessions_dir(root)
    if not os.path.isdir(d):
        return []
    out = []
    for n in sorted(os.listdir(d)):
        if n.endswith(".jsonl"):
            ev = load_session(root, n[:-6])
            first = next((e["text"] for e in ev if e.get("role") == "user"), "")
            out.append((n[:-6], len(ev), first[:60]))
    return out


def build_prompt(events, keep_full=6, cap=1500):
    """Flatten the transcript. Older tool results are shortened so the prompt does not grow without bound."""
    tool_idx = [i for i, e in enumerate(events) if e["role"] == "tool"]
    old = set(tool_idx[:-keep_full]) if len(tool_idx) > keep_full else set()
    parts = [SYSTEM]
    for i, e in enumerate(events):
        t = e["text"]
        if i in old and len(t) > cap:
            t = t[:cap] + "\n...[older result shortened]"
        tag = {"user": "USER", "assistant": "ASSISTANT", "tool": "TOOL RESULT"}[e["role"]]
        parts.append("%s:\n%s" % (tag, t))
    parts.append("ASSISTANT:")
    return "\n\n".join(parts)


def run(provider, task, root, tools, max_steps=12, session=None, say=None, timeout=300):
    """Run the loop. Returns a dict: answer, steps, session, stopped ('done'|'max_steps'|'error'), calls."""
    say = say or (lambda s: None)
    sid = session or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    events = load_session(root, sid) if session else []
    os.makedirs(sessions_dir(root), exist_ok=True)
    path = os.path.join(sessions_dir(root), sid + ".jsonl")

    def add(role, text):
        ev = {"role": role, "text": text, "ts": time.time()}
        events.append(ev)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")

    add("user", task)
    steps, calls, answer, stopped = 0, 0, "", "max_steps"
    while steps < max_steps:
        steps += 1
        reply = provider.complete(build_prompt(events), timeout=timeout)
        calls += 1
        text = reply.text if hasattr(reply, "text") else str(reply)
        add("assistant", text)
        call = parse_tool(text)
        if call is None:
            answer, stopped = text.strip(), "done"
            break
        name, args = call
        if name == "error":
            add("tool", "error: " + args)
            continue
        say("step %d: %s %s" % (steps, name, json.dumps(args)[:120]))
        try:
            res = tools.call(name, args)
        except ToolError as e:
            res = "error: %s" % e
        except TypeError as e:
            res = "error: bad arguments: %s" % e
        except OSError as e:
            res = "error: %s" % e
        add("tool", _clip(str(res)))
    return {"answer": answer, "steps": steps, "session": sid, "stopped": stopped, "calls": calls}
