import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ace import update as upd  # noqa: E402


def make_zip(dest, version, broken=False):
    src = tempfile.mkdtemp()
    top = os.path.join(src, "ace-agent-" + version)
    shutil.copytree(ROOT, top, ignore=shutil.ignore_patterns(".git", "build", "*.egg-info", "__pycache__", ".ace"))
    for rel, old in (("pyproject.toml", 'version = "'), ("ace/__init__.py", '__version__ = "')):
        p = os.path.join(top, rel)
        txt = open(p, encoding="utf-8").read()
        import re
        txt = re.sub(re.escape(old) + r'[^"]*"', old + version + '"', txt, count=1)
        open(p, "w", encoding="utf-8").write(txt)
    if broken:
        with open(os.path.join(top, "ace", "__main__.py"), "w") as f:
            f.write("raise SystemExit('boom')\n")
    with zipfile.ZipFile(dest, "w") as z:
        for d, _, fs in os.walk(top):
            for f in fs:
                full = os.path.join(d, f)
                z.write(full, os.path.relpath(full, src))
    shutil.rmtree(src)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = os.path.join(self.server.root, self.path.lstrip("/"))
        if not os.path.exists(path):
            self.send_response(404)
            self.end_headers()
            return
        data = open(path, "rb").read()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertTrue(upd.parse_version("v0.10.0") > upd.parse_version("0.9.9"))
        self.assertEqual(upd.parse_version("v1.2.3-beta"), (1, 2, 3))
        self.assertEqual(upd.parse_version("junk"), (0,))


class UpdateFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.web = os.path.join(cls.tmp, "web")
        os.makedirs(cls.web)
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        cls.srv.root = cls.web
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.port
        make_zip(os.path.join(cls.web, "good.zip"), "9.9.9")
        make_zip(os.path.join(cls.web, "bad.zip"), "9.9.8", broken=True)
        make_zip(os.path.join(cls.web, "orig.zip"), "0.0.1")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def venv(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        subprocess.check_call([sys.executable, "-m", "venv", d])
        py = os.path.join(d, "Scripts" if os.name == "nt" else "bin", "python")
        subprocess.check_call([py, "-m", "pip", "install", "-q", "--disable-pip-version-check", os.path.join(self.web, "orig.zip")])
        return py, d

    def run_ace(self, py, args, release):
        with open(os.path.join(self.web, "latest.json"), "w") as f:
            json.dump(release, f)
        env = dict(os.environ, ACE_UPDATE_API=self.base + "/latest.json", ACE_NO_UPDATE_CHECK="1",
                   ACE_HOME=os.path.join(self.tmp, "home"), ACE_ROLLBACK_URL=self.base + "/orig.zip")
        env.pop("CI", None)
        return subprocess.run([py, "-m", "ace"] + args, capture_output=True, text=True, env=env, cwd=self.tmp)

    def version(self, py):
        return subprocess.run([py, "-m", "ace", "--version"], capture_output=True, text=True, cwd=self.tmp).stdout.strip()

    def test_update_check_then_install(self):
        py, _ = self.venv()
        self.assertEqual(self.version(py), "ace 0.0.1")
        r = self.run_ace(py, ["update", "--check"], {"tag_name": "v9.9.9", "archive": self.base + "/good.zip"})
        self.assertIn("v9.9.9 is available", r.stdout)
        self.assertEqual(self.version(py), "ace 0.0.1")
        r = self.run_ace(py, ["update"], {"tag_name": "v9.9.9", "archive": self.base + "/good.zip"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.version(py), "ace 9.9.9")
        r = self.run_ace(py, ["update"], {"tag_name": "v9.9.9", "archive": self.base + "/good.zip"})
        self.assertIn("latest version", r.stdout)

    def test_broken_update_rolls_back(self):
        py, _ = self.venv()
        r = self.run_ace(py, ["update"], {"tag_name": "v9.9.8", "archive": self.base + "/bad.zip"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Rolling back", r.stdout)
        self.assertEqual(self.version(py), "ace 0.0.1")
        r = subprocess.run([py, "-m", "ace", "doctor"], capture_output=True, text=True, env=dict(os.environ, ACE_NO_UPDATE_CHECK="1"), cwd=self.tmp)
        self.assertEqual(r.returncode, 0)

    def test_offline_check_is_quiet_failure(self):
        py, _ = self.venv()
        env = dict(os.environ, ACE_UPDATE_API="http://127.0.0.1:9/none", ACE_NO_UPDATE_CHECK="1")
        r = subprocess.run([py, "-m", "ace", "update"], capture_output=True, text=True, env=env, cwd=self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Could not check", r.stdout)

    def test_startup_never_raises_when_offline(self):
        os.environ["ACE_UPDATE_API"] = "http://127.0.0.1:9/none"
        try:
            upd.API = "http://127.0.0.1:9/none"
            old = os.environ.pop("CI", None)
            os.environ.pop("ACE_NO_UPDATE_CHECK", None)
            upd.startup_check("doctor", say=lambda *_: None)
        finally:
            os.environ.pop("ACE_UPDATE_API", None)


if __name__ == "__main__":
    unittest.main()
