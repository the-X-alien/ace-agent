"""Project configuration lives in .ace/config.json. API keys are never written there, only the name of the environment variable."""
import json
import os

DEFAULT = {
    "default_provider": "mock",
    "skills_dir": ".ace/skills",
    "providers": {
        "mock": {"type": "echo"},
        "openai": {"type": "openai-compatible", "base_url": "https://api.openai.com/v1", "model": "", "key_env": "OPENAI_API_KEY"},
        "openrouter": {"type": "openai-compatible", "base_url": "https://openrouter.ai/api/v1", "model": "", "key_env": "OPENROUTER_API_KEY"},
        "featherless": {"type": "openai-compatible", "base_url": "https://api.featherless.ai/v1", "model": "", "key_env": "FEATHERLESS_API_KEY"},
        "lmstudio": {"type": "openai-compatible", "base_url": "http://localhost:1234/v1", "model": ""},
        "ollama": {"type": "openai-compatible", "base_url": "http://localhost:11434/v1", "model": ""},
        "anthropic": {"type": "anthropic", "model": "", "key_env": "ANTHROPIC_API_KEY"},
        "claude-cli": {"type": "cli", "command": ["claude", "-p"], "prompt_via": "arg"},
        "codex-cli": {"type": "cli", "command": ["codex", "exec"], "prompt_via": "arg"},
    },
}


def root(start=None):
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(d, ".ace")):
            return d
        p = os.path.dirname(d)
        if p == d:
            return os.path.abspath(start or os.getcwd())
        d = p


def ace_dir(r=None):
    return os.path.join(r or root(), ".ace")


def load(r=None):
    p = os.path.join(ace_dir(r), "config.json")
    if not os.path.exists(p):
        return json.loads(json.dumps(DEFAULT))
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DEFAULT.items():
        cfg.setdefault(k, v)
    return cfg


def init(r=None):
    r = r or os.getcwd()
    d = ace_dir(r)
    os.makedirs(os.path.join(d, "skills"), exist_ok=True)
    os.makedirs(os.path.join(d, "runs"), exist_ok=True)
    p = os.path.join(d, "config.json")
    created = not os.path.exists(p)
    if created:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(DEFAULT, f, indent=2)
            f.write("\n")
    return d, created
