"""Self-update for Ace: check the public release, install it, verify it starts, roll back if it does not.

Source of truth is the public GitHub release list of the-X-alien/ace-agent, read over HTTPS with no login.
Archive zips from GitHub have no published checksum, so the trust anchor is the repository's own tag over HTTPS.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

from . import __version__

REPO = "the-X-alien/ace-agent"
API = os.environ.get("ACE_UPDATE_API") or "https://api.github.com/repos/%s/releases/latest" % REPO
CHECK_EVERY = 24 * 3600


def state_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "ace-agent")
    return os.environ.get("ACE_HOME") or os.path.join(os.path.expanduser("~"), ".ace-agent")


def _state_path():
    return os.path.join(state_dir(), "state.json")


def load_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(d):
    try:
        os.makedirs(state_dir(), exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, _state_path())
    except OSError:
        pass


def parse_version(v):
    v = str(v).strip().lstrip("vV")
    out = []
    for part in v.split("."):
        n = ""
        for ch in part:
            if ch.isdigit():
                n += ch
            else:
                break
        out.append(int(n) if n else 0)
    return tuple(out)


def archive_url(tag):
    return "https://github.com/%s/archive/refs/tags/%s.zip" % (REPO, tag)


def latest(timeout=5):
    """Return (tag, archive_url) of the newest release, or raise OSError/ValueError."""
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "ace-agent/" + __version__})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    if not isinstance(d, dict) or not d.get("tag_name"):
        raise ValueError("unexpected release reply")
    tag = str(d["tag_name"])
    return tag, str(d.get("archive") or archive_url(tag))


def _pip(args):
    return subprocess.run([sys.executable, "-m", "pip"] + args + ["--disable-pip-version-check", "--quiet"], capture_output=True, text=True)


def _starts():
    r = subprocess.run([sys.executable, "-m", "ace", "--version"], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())


def _launchers():
    d = os.path.dirname(sys.executable)
    names = ("ace.exe", "ace-agent.exe") if os.name == "nt" else ()
    return [os.path.join(d, n) for n in names if os.path.exists(os.path.join(d, n))]


def cleanup_old():
    for p in _launchers() if os.name == "nt" else []:
        try:
            if os.path.exists(p + ".old"):
                os.remove(p + ".old")
        except OSError:
            pass


def apply(tag, url, say=print):
    """Install `url` over the current environment. Roll back to the current version if it will not start."""
    old_tag = "v" + __version__
    moved = []
    # Windows will not let pip overwrite a running .exe, but it does let us rename it.
    for p in _launchers():
        try:
            os.replace(p, p + ".old")
            moved.append(p)
        except OSError:
            pass

    def restore_launchers():
        for p in moved:
            if not os.path.exists(p) and os.path.exists(p + ".old"):
                try:
                    os.replace(p + ".old", p)
                except OSError:
                    pass

    r = _pip(["install", "--upgrade", "--force-reinstall", url])
    ok, out = (False, r.stderr.strip()[-300:]) if r.returncode != 0 else _starts()
    if ok:
        for p in moved:
            try:
                os.remove(p + ".old")
            except OSError:
                pass
        return True, out
    say("Update to %s failed (%s). Rolling back to %s." % (tag, out or "did not start", old_tag))
    rb = _pip(["install", "--upgrade", "--force-reinstall", os.environ.get("ACE_ROLLBACK_URL") or archive_url(old_tag)])
    restore_launchers()
    ok2, out2 = _starts()
    if ok2:
        say("Rolled back: Ace %s is working again." % __version__)
    else:
        say("Rollback did not finish (%s). Reinstall with the one-line command in the README." % (out2 or rb.stderr.strip()[-200:]))
    return False, out


def cmd_update(check_only=False, say=print):
    cleanup_old()
    try:
        tag, url = latest()
    except (OSError, ValueError) as e:
        say("Could not check for updates: %s" % e)
        return 1
    st = load_state()
    st.update({"last_check": time.time(), "latest": tag})
    save_state(st)
    if parse_version(tag) <= parse_version(__version__):
        say("Ace %s is the latest version." % __version__)
        return 0
    if check_only:
        say("Ace %s is available (you have %s). Run: ace-agent update" % (tag, __version__))
        return 0
    say("Updating Ace %s -> %s" % (__version__, tag))
    ok, out = apply(tag, url, say)
    if ok:
        say("Updated. %s" % out)
        return 0
    return 1


def set_auto(mode):
    st = load_state()
    st["auto"] = mode
    save_state(st)


def startup_check(cmd, say=print):
    """Called before a command. Never raises, never blocks more than a few seconds, at most once a day."""
    try:
        if os.environ.get("ACE_NO_UPDATE_CHECK") or os.environ.get("CI") or cmd in ("update", "board", "work", None):
            return
        st = load_state()
        mode = st.get("auto", "notify")
        if mode == "off":
            return
        if time.time() - float(st.get("last_check", 0) or 0) < CHECK_EVERY:
            tag = st.get("latest")
        else:
            tag, url = latest(timeout=2)
            st.update({"last_check": time.time(), "latest": tag})
            save_state(st)
        if not tag or parse_version(tag) <= parse_version(__version__):
            return
        if mode == "apply":
            say("Ace %s is available. Updating now (turn off with: ace-agent update --auto off)." % tag)
            apply(tag, archive_url(tag), say)
        else:
            say("Ace %s is available (you have %s). Run: ace-agent update" % (tag, __version__))
    except Exception:
        return
