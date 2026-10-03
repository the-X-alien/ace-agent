"""Provider adapters against local stub servers and a fake CLI. These check request shape and error handling
(headers, paths, parsing). They do NOT show that any real hosted service accepts the requests."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from ace import providers


class H(BaseHTTPRequestHandler):
    seen = []
    mode = "ok"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        H.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        if H.mode == "401":
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"bad key"}'); return
        if H.mode == "html":
            out = b"<html>not json</html>"
        elif self.path.endswith("/chat/completions"):
            out = json.dumps({"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}).encode()
        elif self.path.endswith("/v1/messages"):
            out = json.dumps({"content": [{"type": "text", "text": "hel"}, {"type": "text", "text": "lo"}], "usage": {"input_tokens": 4, "output_tokens": 1}}).encode()
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class ProviderContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), H)
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        H.seen.clear()
        H.mode = "ok"

    def test_openai_compatible_headers_path_body(self):
        os.environ["ACE_TEST_KEY"] = "k123"
        p = providers.make("x", {"type": "openai-compatible", "base_url": self.url + "/v1", "model": "m", "key_env": "ACE_TEST_KEY", "max_tokens": 50})
        r = p.complete("hi")
        self.assertEqual((r.text, r.tokens_in, r.tokens_out), ("hello", 3, 2))
        path, headers, body = H.seen[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers.get("authorization"), "Bearer k123")
        self.assertEqual((body["model"], body["max_tokens"], body["messages"]), ("m", 50, [{"role": "user", "content": "hi"}]))

    def test_openai_without_key_env_sends_no_auth(self):
        p = providers.make("x", {"type": "openai-compatible", "base_url": self.url + "/v1", "model": "m"})
        p.complete("hi")
        self.assertNotIn("authorization", H.seen[0][1])

    def test_missing_model_or_key_explained(self):
        with self.assertRaises(providers.ProviderError) as e:
            providers.make("x", {"type": "openai-compatible", "base_url": self.url}).complete("hi")
        self.assertIn("model", str(e.exception))
        os.environ.pop("ACE_NOPE", None)
        with self.assertRaises(providers.ProviderError) as e:
            providers.make("x", {"type": "openai-compatible", "base_url": self.url, "model": "m", "key_env": "ACE_NOPE"}).complete("hi")
        self.assertIn("ACE_NOPE", str(e.exception))

    def test_anthropic_shape(self):
        os.environ["ACE_ANT"] = "sk-test"
        p = providers.make("a", {"type": "anthropic", "base_url": self.url, "model": "claude-x", "key_env": "ACE_ANT"})
        r = p.complete("hi")
        self.assertEqual((r.text, r.tokens_in, r.tokens_out), ("hello", 4, 1))
        path, headers, body = H.seen[0]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(headers.get("x-api-key"), "sk-test")
        self.assertEqual(headers.get("anthropic-version"), "2023-06-01")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
        self.assertIn("max_tokens", body)

    def test_http_error_and_bad_json_are_provider_errors(self):
        p = providers.make("x", {"type": "openai-compatible", "base_url": self.url + "/v1", "model": "m"})
        H.mode = "401"
        with self.assertRaises(providers.ProviderError) as e:
            p.complete("hi")
        self.assertIn("401", str(e.exception))
        H.mode = "html"
        with self.assertRaises(providers.ProviderError):
            p.complete("hi")

    def test_unreachable_server(self):
        p = providers.make("x", {"type": "openai-compatible", "base_url": "http://127.0.0.1:9/v1", "model": "m"})
        with self.assertRaises(providers.ProviderError):
            p.complete("hi", timeout=3)

    def test_cli_arg_and_stdin_and_failure(self):
        with tempfile.TemporaryDirectory() as d:
            arg = os.path.join(d, "arg.py")
            open(arg, "w").write("import sys\nprint('ARG:' + sys.argv[-1])\n")
            std = os.path.join(d, "std.py")
            open(std, "w").write("import sys\nprint('STDIN:' + sys.stdin.read())\n")
            bad = os.path.join(d, "bad.py")
            open(bad, "w").write("import sys\nsys.stderr.write('boom')\nsys.exit(3)\n")
            py = sys.executable
            self.assertIn("ARG:hello", providers.make("c", {"type": "cli", "command": [py, arg], "prompt_via": "arg"}).complete("hello").text)
            self.assertIn("STDIN:hello", providers.make("c", {"type": "cli", "command": [py, std], "prompt_via": "stdin"}).complete("hello").text)
            with self.assertRaises(providers.ProviderError) as e:
                providers.make("c", {"type": "cli", "command": [py, bad]}).complete("x")
            self.assertIn("boom", str(e.exception))
            with self.assertRaises(providers.ProviderError):
                providers.make("c", {"type": "cli", "command": ["definitely-not-a-command-xyz"]}).complete("x")

    def test_unknown_type(self):
        with self.assertRaises(providers.ProviderError):
            providers.make("x", {"type": "nope"})


if __name__ == "__main__":
    unittest.main()


class Ping(unittest.TestCase):
    def test_ping_mock_and_failure(self):
        import contextlib
        import io
        from ace import cli
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".ace"))
            json.dump({"default_provider": "mock", "providers": {"mock": {"type": "echo"}, "bad": {"type": "openai-compatible", "base_url": "http://127.0.0.1:9/v1", "model": "m"}}}, open(os.path.join(d, ".ace", "config.json"), "w"))
            old = os.getcwd()
            os.chdir(d)
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cli.main(["ping"]), 0)
                self.assertIn("MOCK", out.getvalue())
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cli.main(["ping", "--provider", "bad", "--timeout", "3"]), 1)
                self.assertIn("FAILED", out.getvalue())
            finally:
                os.chdir(old)
