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
# raw file block: no JSON escaping needed, friendlier for small models
FILE_RE = re.compile(r"```ace-write[ \t]+(\S+)[ \t]*\n(.*?)\n?```", re.S)
TRIPLE_RE = re.compile(r'"""(.*?)"""', re.S)

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
- To write a whole file without JSON escaping, reply with a block like this instead:
```ace-write index.html
<!DOCTYPE html>
...the complete file as plain text...
```
- run_shell {"command": "python -m unittest"}           run a command in the project folder (60s limit)

Rules: paths are relative to the project folder. Read a file before editing it. Make the smallest change that does the task, then check it with run_shell when a check exists. When the task is done, reply in plain text with no tool block."""


_PLACEHOLDERS = ("goes here", "your code here", "todo: implement", "lorem ipsum dolor sit amet, consectetur adipiscing elit. (")


def placeholder_reason(path, content, fullpath):
    """Return a reason when this write looks like a placeholder that would destroy a real file."""
    low = content.strip().lower()
    hit = any(m in low for m in _PLACEHOLDERS) and len(low) < 400
    tiny_html = path.lower().endswith((".html", ".htm")) and "<" not in low
    if not (hit or tiny_html):
        return ""
    if os.path.exists(fullpath) and os.path.getsize(fullpath) > len(content) + 200:
        return "the new content is a placeholder (%d characters) and would replace a longer file. Write the real, complete content." % len(content)
    if hit or tiny_html:
        return "the content is a placeholder, not real content. Write the real, complete content."
    return ""


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
    def __init__(self, root, approve=None, auto_edit=False, allow_shell=False, shell_timeout=SHELL_TIMEOUT, read_only=False):
        self.read_only = read_only
        self.ws = Workspace(root)
        self.approve = approve or (lambda kind, detail: False)
        self.auto_edit, self.allow_shell, self.shell_timeout = auto_edit, allow_shell, shell_timeout

    def call(self, name, args):
        fn = getattr(self, "t_" + str(name), None)
        if fn is None or not isinstance(args, dict):
            raise ToolError("unknown tool or bad arguments: %s" % name)
        if self.read_only and name in ("write_file", "edit_file", "run_shell"):
            raise ToolError("plan mode is read-only: %s is not allowed. Read and search, then describe the plan in plain text." % name)
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
        why = placeholder_reason(path, content, f)
        if why:
            raise ToolError("write refused, the existing file was kept: " + why)
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
            msg += self._missing_assets(path, content)
        return msg

    def _missing_assets(self, path, content):
        """Local files an HTML page links to that do not exist yet (stylesheets, scripts, images)."""
        base = os.path.dirname(self.ws.path(path))
        miss = []
        for m in re.finditer(r"""(?:href|src)\s*=\s*["']([^"'#?]+)["']""", content, re.I):
            u = m.group(1).strip()
            if re.match(r"^(https?:|//|data:|mailto:|tel:|javascript:)", u, re.I):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, u))) and u not in miss:
                miss.append(u)
        if not miss:
            return ""
        return "\nMISSING FILES linked by %s: %s. Create each with write_file, or remove the link and put the CSS in a <style> tag." % (path, ", ".join(miss))

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
    fw = FILE_RE.search(text)
    m = TOOL_RE.search(text)
    if fw and (not m or fw.start() < m.start()):
        return "write_file", {"path": fw.group(1), "content": fw.group(2) + "\n"}
    if not m:
        return None
    try:
        raw = m.group(1)
        try:
            d = json.loads(raw)
        except ValueError:
            # small models sometimes use Python-style triple-quoted strings
            d = json.loads(TRIPLE_RE.sub(lambda t: json.dumps(t.group(1).strip("\n")), raw))
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


def build_prompt(events, keep_full=6, cap=1500, max_chars=None):
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
    if max_chars:
        # fit a context budget (set "context_chars" on a provider): keep the system text, the first task and the
        # newest turns; drop the oldest middle turns and say so
        head, body = parts[:2], parts[2:-1]
        kept, used = [], sum(len(x) for x in head) + len("ASSISTANT:") + 80
        for x in reversed(body):
            if used + len(x) + 2 > max_chars and kept:
                break
            kept.append(x if used + len(x) + 2 <= max_chars else x[: max(200, max_chars - used - 2)])
            used += len(kept[-1]) + 2
        kept.reverse()
        if len(kept) < len(body):
            kept.insert(0, "[earlier turns dropped to fit the context budget]")
        parts = head + kept + ["ASSISTANT:"]
    return "\n\n".join(parts)


def with_skills(task, root, say=None):
    """Pick working-rule skills that match the task and put them in front of it (the picks are shown, with the words that caused them)."""
    from . import skills as sk
    try:
        picks = sk.select(task, sk.load_skills([os.path.join(root, ".ace", "skills")]))
    except (OSError, ValueError):
        picks = []
    if not picks:
        return task
    if say:
        say("skills: " + ", ".join("%s (%s)" % (p.skill.name, ", ".join(p.matched[:3])) for p in picks))
    text = sk.compose(task, picks)
    if len(text) > 1800:   # small local models have small windows: keep the rules short
        text = text[:1800].rsplit("\n", 1)[0] + "\n\nTask: " + task
    return text + "\n\n(You are using tools: save files with write_file or an ace-write block instead of returning a code block, and keep any CSS inside the page.)"


def run(provider, task, root, tools, max_steps=12, session=None, say=None, timeout=300, skills=False):
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

    add("user", with_skills(task, root, say) if skills else task)
    steps, calls, answer, stopped = 0, 0, "", "max_steps"
    recent = []
    while steps < max_steps:
        steps += 1
        reply = provider.complete(build_prompt(events, max_chars=(getattr(provider, "cfg", None) or {}).get("context_chars")), timeout=timeout)
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
        sig = json.dumps([name, args], sort_keys=True, default=str)
        recent.append(sig)
        if len(recent) >= 3 and recent[-1] == recent[-2] == recent[-3]:
            add("tool", "error: you ran the same call three times in a row. Stopping: the loop is not making progress.")
            stopped = "loop"
            break
        if len(recent) >= 2 and recent[-1] == recent[-2]:
            add("tool", "error: you just did exactly this. Do something different, or reply in plain text with a short final answer if the task is done.")
            continue
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
