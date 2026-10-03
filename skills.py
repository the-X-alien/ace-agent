"""Skill loading and automatic selection.

A skill is a markdown file with a small front matter block. Selection is a
transparent keyword score: every pick comes with the words that caused it, so
a wrong pick is easy to see and fix. This is a heuristic, not a trained
router, and it has not been benchmarked.
"""
import os
import re
from dataclasses import dataclass, field

_PKG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills_data")


@dataclass
class Skill:
    name: str
    summary: str
    triggers: list
    kind: str
    body: str
    source: str = ""


@dataclass
class Pick:
    skill: Skill
    score: int
    matched: list = field(default_factory=list)


def parse_skill(text, source=""):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise ValueError("skill %s has no front matter" % source)
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    if "name" not in meta:
        raise ValueError("skill %s has no name" % source)
    trig = [t.strip().lower() for t in meta.get("triggers", "").split(",") if t.strip()]
    return Skill(meta["name"], meta.get("summary", ""), trig, meta.get("kind", "text"), m.group(2).strip(), source)


def load_skills(extra_dirs=()):
    """Bundled skills first, then user skills (a user skill with the same name wins)."""
    skills = {}
    for d in (_PKG_DIR,) + tuple(extra_dirs):
        if not d or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".md"):
                p = os.path.join(d, fn)
                with open(p, encoding="utf-8") as f:
                    s = parse_skill(f.read(), p)
                skills[s.name] = s
    return list(skills.values())


def _words(text):
    return set(re.findall(r"[a-z0-9+#.-]+", text.lower()))


def select(prompt, skills, max_skills=2, min_score=1):
    """Return the best-matching skills for a prompt, highest score first."""
    low = prompt.lower()
    words = _words(prompt)
    picks = []
    for s in skills:
        matched = []
        for t in s.triggers:
            if " " in t:
                if t in low:
                    matched.append(t)
            elif t in words:
                matched.append(t)
        if len(matched) >= min_score:
            picks.append(Pick(s, len(matched), matched))
    picks.sort(key=lambda p: (-p.score, p.skill.name))
    return picks[:max_skills]


def compose(prompt, picks):
    """Build the prompt sent to the model: skill rules first, the user's task last and unchanged."""
    if not picks:
        return prompt
    parts = ["Follow these working rules.\n"]
    for p in picks:
        parts.append("## %s\n%s\n" % (p.skill.name, p.skill.body))
    parts.append("## Task\n%s" % prompt)
    return "\n".join(parts)
