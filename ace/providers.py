"""Provider adapters. Bring your own provider: keys come from environment variables and are never stored.

Types:
  openai-compatible  any /chat/completions endpoint (OpenAI, OpenRouter, a local Ollama or LM Studio, OmniRoute, ...)
  anthropic          the Anthropic Messages API
  cli                any command-line agent that takes a prompt (for example a coding agent CLI)
  echo               offline test provider. It returns canned text and is labelled MOCK everywhere.
"""
import http.client
import json
import socket
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class ProviderError(Exception):
    pass


@dataclass
class Reply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0
    mock: bool = False
    usage_known: bool = False


def _post(url, headers, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        if not isinstance(d, dict):
            raise ProviderError("unexpected response from %s: not a JSON object" % url)
        return d
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise ProviderError("HTTP %s from %s: %s" % (e.code, url, body))
    except urllib.error.URLError as e:
        raise ProviderError("cannot reach %s: %s" % (url, e.reason))
    except (TimeoutError, socket.timeout):
        raise ProviderError("request to %s timed out after %ss" % (url, timeout))
    except (ConnectionError, http.client.HTTPException, OSError) as e:
        raise ProviderError("connection to %s failed: %s" % (url, e))
    except ValueError:
        raise ProviderError("response from %s was not valid JSON" % url)


class Provider:
    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg

    def complete(self, prompt, timeout=300):
        raise NotImplementedError

    def price(self, reply):
        pin, pout = self.cfg.get("price_in_per_mtok"), self.cfg.get("price_out_per_mtok")
        if pin is None or pout is None or not reply.usage_known:
            return None
        return (reply.tokens_in * pin + reply.tokens_out * pout) / 1e6


class OpenAICompatible(Provider):
    def complete(self, prompt, timeout=300):
        base = self.cfg.get("base_url", "").rstrip("/")
        model = self.cfg.get("model")
        if not base or not model:
            raise ProviderError("provider '%s' needs base_url and model in .ace/config.json" % self.name)
        headers = {"Content-Type": "application/json"}
        key_env = self.cfg.get("key_env")
        if key_env:
            key = os.environ.get(key_env)
            if not key:
                raise ProviderError("environment variable %s is not set" % key_env)
            headers["Authorization"] = "Bearer " + key
        t0 = time.time()
        d = _post(base + "/chat/completions", headers, dict({"model": model, "messages": [{"role": "user", "content": prompt}]}, **({"max_tokens": int(self.cfg["max_tokens"])} if self.cfg.get("max_tokens") else {})), timeout)
        try:
            text = d["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError("unexpected response shape from %s" % base)
        u = d.get("usage") or {}
        return Reply(text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), time.time() - t0, usage_known=bool(u))


class Anthropic(Provider):
    def complete(self, prompt, timeout=300):
        model = self.cfg.get("model")
        if not model:
            raise ProviderError("provider '%s' needs a model in .ace/config.json" % self.name)
        key = os.environ.get(self.cfg.get("key_env", "ANTHROPIC_API_KEY"))
        if not key:
            raise ProviderError("environment variable %s is not set" % self.cfg.get("key_env", "ANTHROPIC_API_KEY"))
        base = self.cfg.get("base_url", "https://api.anthropic.com").rstrip("/")
        headers = {"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}
        t0 = time.time()
        d = _post(base + "/v1/messages", headers, {"model": model, "max_tokens": int(self.cfg.get("max_tokens", 4096)),
                                                   "messages": [{"role": "user", "content": prompt}]}, timeout)
        try:
            text = "".join(b.get("text", "") for b in d["content"])
        except (KeyError, TypeError):
            raise ProviderError("unexpected response shape from Anthropic API")
        u = d.get("usage") or {}
        return Reply(text, u.get("input_tokens", 0), u.get("output_tokens", 0), time.time() - t0, usage_known=bool(u))


class Cli(Provider):
    """Run a local agent CLI. Token usage is not reported by most CLIs, so it is left unknown, not guessed."""

    def complete(self, prompt, timeout=600):
        cmd = list(self.cfg.get("command") or [])
        if not cmd:
            raise ProviderError("provider '%s' needs a command list" % self.name)
        exe = shutil.which(cmd[0])
        if not exe:
            raise ProviderError("command '%s' not found on PATH" % cmd[0])
        cmd[0] = exe
        via = self.cfg.get("prompt_via", "arg")
        t0 = time.time()
        try:
            if via == "stdin":
                r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
            else:
                r = subprocess.run(cmd + [prompt], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ProviderError("command timed out after %ss" % timeout)
        if r.returncode != 0:
            raise ProviderError("command exited %s: %s" % (r.returncode, (r.stderr or r.stdout)[:300]))
        return Reply(r.stdout, seconds=time.time() - t0)


class Echo(Provider):
    """Offline test provider. Returns a fixed page or text so the pipeline can be tested without a model. Never a real result."""

    def complete(self, prompt, timeout=0):
        t0 = time.time()
        if "Follow these working rules" in prompt and "web-ui-design" in prompt:
            text = ("```html\n<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Mock page</title>"
                    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head>"
                    "<body><main><h1>Mock page from the echo provider</h1><p>This text is canned. It exists to test the "
                    "pipeline, not to show model quality.</p></main></body></html>\n```")
        elif "html" in prompt.lower() or "page" in prompt.lower():
            text = "```html\n<html><body><p>mock</p></body></html>\n```"
        else:
            text = "MOCK reply. This is canned text from the echo provider, used only for tests."
        return Reply(text, len(prompt) // 4, len(text) // 4, time.time() - t0, mock=True, usage_known=False)


TYPES = {"openai-compatible": OpenAICompatible, "anthropic": Anthropic, "cli": Cli, "echo": Echo}


def make(name, cfg):
    t = cfg.get("type")
    if t not in TYPES:
        raise ProviderError("provider '%s' has unknown type %r (use one of %s)" % (name, t, ", ".join(sorted(TYPES))))
    return TYPES[t](name, cfg)
