"""Output checks. Each check returns issues with a severity: 'hard' or 'advisory'.

These are structural checks on text. They do not render pixels or judge taste.
"""
import ast
import json
import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


def extract_code(text, kind):
    """Pull the main artifact out of a model reply."""
    fence = {"html": ("html",), "python": ("python", "py"), "json": ("json",)}.get(kind, ())
    for lang in fence:
        m = re.search(r"```%s[^\n]*\n(.*?)```" % lang, text, re.S | re.I)
        if m:
            return m.group(1).strip()
    if kind == "html":
        m = re.search(r"(<!doctype html.*</html>|<html.*</html>)", text, re.S | re.I)
        if m:
            return m.group(1).strip()
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    if m and kind in ("python", "json", "html"):
        return m.group(1).strip()
    return text.strip()


def detect_kind(text):
    low = text.lower()
    if "```html" in low or "<!doctype html" in low or "<html" in low:
        return "html"
    if "```python" in low or re.search(r"^\s*(def |import |from \w+ import )", text, re.M):
        return "python"
    s = text.strip()
    if s[:1] in "{[":
        try:
            json.loads(s)
            return "json"
        except ValueError:
            pass
    return "text"


class _H(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.tags, self.errors = [], [], []
        self.imgs_no_alt, self.empty_links, self.text = 0, 0, []
        self.html_lang, self.viewport, self.title_txt, self._in_title = False, False, "", False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append(tag)
        if tag == "html" and a.get("lang"):
            self.html_lang = True
        if tag == "meta" and (a.get("name") or "").lower() == "viewport":
            self.viewport = True
        if tag == "img" and "alt" not in a:
            self.imgs_no_alt += 1
        if tag == "a" and (a.get("href") in (None, "", "#")):
            self.empty_links += 1
        if tag == "title":
            self._in_title = True
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.errors.append("unclosed <%s>" % self.stack.pop())
            self.stack.pop()
        else:
            self.errors.append("stray </%s>" % tag)

    def handle_data(self, data):
        if self._in_title:
            self.title_txt += data
        if not self.stack or self.stack[-1] not in ("script", "style"):
            self.text.append(data)


def check_html(src):
    issues = []
    p = _H()
    try:
        p.feed(src)
        p.close()
    except Exception as e:  # parser errors are issues, not crashes
        return [("hard", "html does not parse: %s" % e)]
    for e in p.errors[:3]:
        issues.append(("advisory", "markup: " + e))
    if "html" not in p.tags:
        issues.append(("hard", "no <html> element"))
    if "body" not in p.tags:
        issues.append(("hard", "no <body> element"))
    body_text = " ".join(p.text).strip()
    if len(body_text) < 40:
        issues.append(("hard", "page has almost no visible text"))
    if not p.title_txt.strip():
        issues.append(("hard", "missing <title>"))
    if not p.viewport:
        issues.append(("hard", "missing viewport meta tag (breaks mobile layout)"))
    if "h1" not in p.tags:
        issues.append(("advisory", "no <h1>"))
    if not p.html_lang:
        issues.append(("advisory", "missing lang attribute on <html>"))
    if p.imgs_no_alt:
        issues.append(("advisory", "%d image(s) without alt text" % p.imgs_no_alt))
    if p.empty_links:
        issues.append(("advisory", "%d link(s) with empty or '#' href" % p.empty_links))
    if re.search(r"lorem ipsum|\bTODO\b|your (name|company) here|<!--[^>]*\bhere\b[^>]*-->|/\*[^*]*\byour\b[^*]*\bhere\b[^*]*\*/", src, re.I):
        issues.append(("hard", "placeholder text left in the page"))
    if re.search(r"(src|href)=[\"']https?://", src, re.I):
        issues.append(("advisory", "loads an external resource (not self-contained)"))
    return issues


def check_python(src):
    try:
        ast.parse(src)
    except SyntaxError as e:
        return [("hard", "python syntax error line %s: %s" % (e.lineno, e.msg))]
    issues = []
    if re.search(r"\bTODO\b|NotImplementedError", src):
        issues.append(("hard", "unfinished code (TODO or NotImplementedError)"))
    return issues


def check_json(src):
    try:
        json.loads(src)
    except ValueError as e:
        return [("hard", "invalid JSON: %s" % e)]
    return []


def check_text(src):
    issues = []
    if len(src.strip()) < 20:
        issues.append(("hard", "reply is nearly empty"))
    if "\u2014" in src:
        issues.append(("advisory", "contains em dashes"))
    return issues


def check(reply, kind=None):
    """Return (kind, artifact, issues). Issues is a list of (severity, message)."""
    kind = kind or detect_kind(reply)
    artifact = extract_code(reply, kind)
    fn = {"html": check_html, "python": check_python, "json": check_json}.get(kind, check_text)
    return kind, artifact, fn(artifact)


def hard(issues):
    return [m for s, m in issues if s == "hard"]
