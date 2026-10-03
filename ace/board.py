"""Shared board: humans and agents on the same tasks, with live progress and a human approval gate.

Trust model for v1: one shared token per server. Anyone holding the token can act as any named actor. That is
enough for a team on one network, not for the open internet. By default the server only listens on localhost.
"""
import json
import os
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATUSES = ("todo", "doing", "review", "blocked", "done")
KINDS = ("human", "agent")
_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "index.html")


class BoardError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


class Board:
    def __init__(self, path):
        self.path, self.lock, self.subs = path, threading.RLock(), []
        self.state = {"seq": 0, "tasks": [], "context": []}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.state = json.load(f)

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)
        snap = json.dumps(self.state)
        for q in list(self.subs):
            q.put(snap)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def _task(self, tid):
        for t in self.state["tasks"]:
            if t["id"] == tid:
                return t
        raise BoardError("no task %s" % tid, 404)

    @staticmethod
    def _actor(a):
        if not isinstance(a, dict) or not str(a.get("name", "")).strip():
            raise BoardError("actor name required")
        k = a.get("kind", "human")
        if k not in KINDS:
            raise BoardError("actor kind must be human or agent")
        return {"name": str(a["name"]).strip()[:40], "kind": k}

    def _note(self, t, actor, text):
        t["notes"].append({"at": time.strftime("%H:%M:%S"), "by": actor["name"], "kind": actor["kind"], "text": str(text)[:2000]})

    def add_task(self, actor, title, prompt="", assignee="", assignee_kind="human", needs_approval=False):
        actor = self._actor(actor)
        if not str(title).strip():
            raise BoardError("title required")
        if assignee_kind not in KINDS:
            raise BoardError("assignee kind must be human or agent")
        with self.lock:
            self.state["seq"] += 1
            t = {"id": self.state["seq"], "title": str(title).strip()[:160], "prompt": str(prompt)[:8000],
                 "assignee": str(assignee)[:40], "assignee_kind": assignee_kind, "status": "todo", "progress": 0,
                 "needs_approval": bool(needs_approval), "approved_by": None, "output": "", "notes": [],
                 "created_by": actor["name"]}
            self._note(t, actor, "created task")
            self.state["tasks"].append(t)
            self._save()
            return t

    def update(self, tid, actor, status=None, progress=None, note=None, assignee=None, assignee_kind=None, output=None):
        actor = self._actor(actor)
        with self.lock:
            t = self._task(tid)
            if assignee is not None:
                if assignee_kind not in (None,) + KINDS:
                    raise BoardError("bad assignee kind")
                t["assignee"], t["assignee_kind"] = str(assignee)[:40], assignee_kind or t["assignee_kind"]
                self._note(t, actor, "assigned to %s (%s)" % (t["assignee"], t["assignee_kind"]))
            if progress is not None:
                t["progress"] = max(0, min(100, int(progress)))
            if output is not None:
                t["output"] = str(output)[:50000]
            if status is not None:
                if status not in STATUSES:
                    raise BoardError("status must be one of %s" % ", ".join(STATUSES))
                if status == "done" and t["needs_approval"] and not t["approved_by"]:
                    raise BoardError("this task needs human approval before it can be done", 409)
                t["status"] = status
                if status == "done":
                    t["progress"] = 100
                self._note(t, actor, "status -> %s" % status)
            if note:
                self._note(t, actor, note)
            self._save()
            return t

    def approve(self, tid, actor, approve=True):
        actor = self._actor(actor)
        if actor["kind"] != "human":
            raise BoardError("only a human can approve", 403)
        with self.lock:
            t = self._task(tid)
            if not t["needs_approval"]:
                raise BoardError("task does not need approval", 409)
            t["approved_by"] = actor["name"] if approve else None
            self._note(t, actor, "approved" if approve else "approval withdrawn")
            self._save()
            return t

    def add_context(self, actor, text):
        actor = self._actor(actor)
        with self.lock:
            self.state["context"].append({"at": time.strftime("%H:%M:%S"), "by": actor["name"], "kind": actor["kind"], "text": str(text)[:4000]})
            self._save()

    def subscribe(self):
        q = queue.Queue()
        self.subs.append(q)
        return q

    def unsubscribe(self, q):
        if q in self.subs:
            self.subs.remove(q)


def make_handler(board, tokens, allowed_hosts):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(data)

        def _ok(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if allowed_hosts and host not in allowed_hosts:
                self._send(403, json.dumps({"error": "host not allowed"}))
                return False
            self.role = None
            if tokens:
                q = parse_qs(urlparse(self.path).query)
                got = self.headers.get("X-Ace-Token") or (q.get("token") or [""])[0]
                for tk, role in tokens.items():
                    if secrets.compare_digest(got, tk):
                        self.role = role
                if not self.role:
                    self._send(401, json.dumps({"error": "token required"}))
                    return False
            return True

        def do_GET(self):
            if not self._ok():
                return
            p = urlparse(self.path).path
            if p == "/":
                with open(_UI, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if p == "/api/state":
                return self._send(200, json.dumps(board.snapshot()))
            if p == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                q = board.subscribe()
                try:
                    self.wfile.write(("data: %s\n\n" % json.dumps(board.snapshot())).encode())
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=15)
                            self.wfile.write(("data: %s\n\n" % msg).encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    board.unsubscribe(q)
                    self.close_connection = True
                return
            self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            if not self._ok():
                return
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._send(415, json.dumps({"error": "send application/json"}))
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0:
                self.close_connection = True
                return self._send(400, json.dumps({"error": "bad Content-Length"}))
            if n > 200000:
                return self._send(413, json.dumps({"error": "too large"}))
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(d, dict):
                    raise BoardError("body must be a JSON object")
                path = urlparse(self.path).path.strip("/").split("/")
                actor = d.get("actor")
                if self.role and isinstance(actor, dict):
                    actor = dict(actor, kind=self.role)  # secure mode: the token, not the payload, decides human vs agent
                if path == ["api", "tasks"]:
                    t = board.add_task(actor, d.get("title", ""), d.get("prompt", ""), d.get("assignee", ""),
                                       d.get("assignee_kind", "human"), d.get("needs_approval", False))
                    return self._send(200, json.dumps(t))
                if path == ["api", "context"]:
                    board.add_context(actor, d.get("text", ""))
                    return self._send(200, "{}")
                if len(path) == 4 and path[:2] == ["api", "tasks"]:
                    tid = int(path[2])
                    if path[3] == "update":
                        t = board.update(tid, actor, d.get("status"), d.get("progress"), d.get("note"), d.get("assignee"),
                                         d.get("assignee_kind"), d.get("output"))
                        return self._send(200, json.dumps(t))
                    if path[3] == "approve":
                        return self._send(200, json.dumps(board.approve(tid, actor, d.get("approve", True))))
                return self._send(404, json.dumps({"error": "not found"}))
            except BoardError as e:
                return self._send(e.code, json.dumps({"error": str(e)}))
            except (ValueError, TypeError, OverflowError) as e:
                return self._send(400, json.dumps({"error": "bad request: %s" % e}))

    return H


def serve(path, host="127.0.0.1", port=8765, token=None, agent_token=None, secure=False):
    """Open mode (default on localhost): actor kind comes from the request, so the approval gate is a workflow
    step, not a security boundary. Secure mode (forced off localhost): a human token and a separate agent token;
    the server sets the actor kind from the token, so an agent holding only the agent token cannot approve."""
    local = host in ("127.0.0.1", "localhost", "::1")
    tokens = {}
    if secure or not local or token or agent_token:
        token = token or secrets.token_urlsafe(16)
        agent_token = agent_token or secrets.token_urlsafe(16)
        if token == agent_token:
            raise ValueError("human and agent tokens must differ")
        tokens = {token: "human", agent_token: "agent"}
    board = Board(path)
    allowed = {"127.0.0.1", "localhost", "::1", host} if local else None  # off-localhost the tokens are the gate
    srv = ThreadingHTTPServer((host, port), make_handler(board, tokens, allowed))
    srv.daemon_threads = True
    return srv, board, (token if tokens else None), (agent_token if tokens else None)
