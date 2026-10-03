"""The Ace pipeline: pick skills, compose the prompt, call the provider, check the output, fix once if needed."""
import json
import os
import time

from . import config as cfgmod
from . import gate, providers, skills as sk


def get_provider(cfg, name=None):
    name = name or os.environ.get("ACE_PROVIDER") or cfg.get("default_provider")
    pc = cfg["providers"].get(name)
    if pc is None:
        raise providers.ProviderError("no provider named '%s'. Known: %s" % (name, ", ".join(sorted(cfg["providers"]))))
    return providers.make(name, pc)


def _skills(r, cfg):
    return sk.load_skills([os.path.join(r, cfg.get("skills_dir", ".ace/skills"))])


def run_plain(provider, prompt):
    """Baseline: the user's prompt, unchanged, one call, no checks applied to the result's text."""
    rep = provider.complete(prompt)
    kind, artifact, issues = gate.check(rep.text)
    return {"mode": "plain", "prompt_sent": prompt, "reply": rep.text, "kind": kind, "artifact": artifact,
            "issues": issues, "calls": 1, "tokens_in": rep.tokens_in, "tokens_out": rep.tokens_out,
            "usage_known": rep.usage_known, "seconds": rep.seconds, "mock": rep.mock,
            "cost": provider.price(rep), "fixes": 0, "skills": []}


def run_ace(provider, prompt, skill_list, max_skills=2, max_fixes=1, use_skills=True):
    """Ace: automatic skills, then check the output, then one repair pass for hard failures."""
    picks = sk.select(prompt, skill_list, max_skills) if use_skills else []
    sent = sk.compose(prompt, picks)
    kind_hint = picks[0].skill.kind if picks and picks[0].skill.kind != "text" else None
    t0 = time.time()
    rep = provider.complete(sent)
    tin, tout, known, mock, cost, calls, fixes = rep.tokens_in, rep.tokens_out, rep.usage_known, rep.mock, provider.price(rep), 1, 0
    kind, artifact, issues = gate.check(rep.text, kind_hint)
    text = rep.text
    while gate.hard(issues) and fixes < max_fixes:
        fix = ("Your previous answer failed these checks:\n- " + "\n- ".join(gate.hard(issues)) +
               "\n\nReturn the full corrected answer in the same format.\n\nOriginal task:\n" + prompt)
        rep2 = provider.complete(fix)
        fixes += 1
        calls += 1
        tin += rep2.tokens_in
        tout += rep2.tokens_out
        known = known and rep2.usage_known
        c2 = provider.price(rep2)
        cost = (cost or 0) + c2 if c2 is not None else None
        text = rep2.text
        kind, artifact, issues = gate.check(text, kind_hint)
    return {"mode": "ace", "prompt_sent": sent, "reply": text, "kind": kind, "artifact": artifact, "issues": issues,
            "calls": calls, "tokens_in": tin, "tokens_out": tout, "usage_known": known,
            "seconds": time.time() - t0, "mock": mock, "cost": cost, "fixes": fixes,
            "skills": [{"name": p.skill.name, "matched": p.matched} for p in picks]}


def save_run(r, obj):
    d = os.path.join(cfgmod.ace_dir(r), "runs")
    os.makedirs(d, exist_ok=True)
    rid = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
    obj = dict(obj, id=rid, at=time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(os.path.join(d, rid + ".json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    return rid


def compare(provider, prompt, skill_list, **kw):
    """Same prompt, same provider, same checks, run twice. Report what was measured and nothing more."""
    a = run_plain(provider, prompt)
    b = run_ace(provider, prompt, skill_list, **kw)
    return {"prompt": prompt, "provider": provider.name, "plain": a, "ace": b, "mock": a["mock"] or b["mock"]}
