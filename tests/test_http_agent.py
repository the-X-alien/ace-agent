"""End-to-end over real HTTP: the OpenAI-compatible provider talks to a local stub server (not a real model)."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from ace import agent as ag
from ace import providers


def block(tool, **args):
    return "```ace-tool\n%s\n```" % json.dumps({"tool": tool, "args": args})


class Stub(BaseHTTPRequestHandler):
    replies = []
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Stub.seen.append((self.path, self.headers.get("Authorization"), body))
        text = Stub.replies.pop(0)
        out = json.dumps({"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class HttpAgent(unittest.TestCase):
    def test_agent_over_http(self):
        srv = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        Stub.seen = []
        Stub.replies = [block("write_file", path="index.html", content="<h1>Hi</h1>"), "Done: wrote index.html."]
        d = tempfile.mkdtemp()
        os.environ["STUB_KEY"] = "test-key"
        prov = providers.make("stub", {"type": "openai-compatible", "base_url": "http://127.0.0.1:%d/v1" % srv.server_port,
                                        "model": "m", "key_env": "STUB_KEY"})
        res = ag.run(prov, "make a page", d, ag.Tools(d, auto_edit=True))
        srv.shutdown(); srv.server_close()
        self.assertEqual(res["stopped"], "done")
        self.assertEqual(open(os.path.join(d, "index.html")).read(), "<h1>Hi</h1>")
        self.assertEqual(Stub.seen[0][0], "/v1/chat/completions")
        self.assertEqual(Stub.seen[0][1], "Bearer test-key")
        self.assertEqual(Stub.seen[0][2]["model"], "m")


if __name__ == "__main__":
    unittest.main()
