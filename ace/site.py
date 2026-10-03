"""`ace-agent site`: build a complete one-page website from one prompt, in small model calls.

Small and local models fail at one giant "write the whole page" request. So Ace asks for
the content in short, separate steps (name and tagline, about text, offerings, reviews,
contact), each in a plain line format that is easy to parse, retries a step whose answer
is unusable, assembles the page, and runs the HTML check. The words come from the model.
The layout and the CSS come from Ace's built-in page template, not from the model.
"""
import html
import os
import re

from . import gate

STYLE = """:root{--bg:#faf6f1;--ink:#2b211b;--accent:#a8552a;--card:#ffffff;--muted:#6f625a}
*{box-sizing:border-box}body{margin:0;font-family:Georgia,'Times New Roman',serif;background:var(--bg);color:var(--ink);line-height:1.6}
nav{display:flex;gap:1.2rem;justify-content:flex-end;padding:1rem 2rem;position:sticky;top:0;background:var(--bg);border-bottom:1px solid #e6dcd2}
nav b{margin-right:auto;color:var(--accent);font-size:1.2rem}nav a{color:var(--ink);text-decoration:none}
.hero{padding:6rem 2rem;text-align:center;background:linear-gradient(135deg,#3a2a20,#a8552a);color:#fff}
.hero h1{font-size:clamp(2.2rem,6vw,4rem);margin:0 0 .5rem}.hero p{font-size:1.25rem;max-width:36rem;margin:0 auto 1.5rem}
.btn{display:inline-block;background:#fff;color:var(--accent);padding:.8rem 1.6rem;border-radius:2rem;text-decoration:none;font-weight:bold}
section{max-width:60rem;margin:0 auto;padding:3.5rem 1.5rem}h2{color:var(--accent);font-size:2rem;margin-top:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:1.2rem}
.card{background:var(--card);border-radius:.8rem;padding:1.2rem;box-shadow:0 2px 8px rgba(0,0,0,.08)}
.card h3{margin:.1rem 0}.price{color:var(--accent);font-weight:bold}.card p{color:var(--muted);margin:.4rem 0 0}
blockquote{margin:0;font-style:italic}.who{color:var(--muted);font-style:normal;margin-top:.5rem}
footer{text-align:center;padding:2rem;color:var(--muted);border-top:1px solid #e6dcd2}
@media(max-width:600px){nav{padding:.8rem 1rem;gap:.7rem}.hero{padding:3.5rem 1rem}}"""


def _ask(prov, prompt, ok, tries=3, say=None):
    last = ""
    for i in range(tries):
        text = prov.complete(prompt + ("" if i == 0 else "\nFollow the format exactly."), timeout=600).text.strip()
        val = ok(text)
        if val:
            return val
        last = text
        if say:
            say("  retry (unusable answer)")
    raise RuntimeError("the model did not give a usable answer; last reply: %r" % last[:200])


def _clean(s):
    return re.sub(r"\s+", " ", s.strip().strip("*#\"'`- ").strip())


def _lines(text):
    return [l for l in (x.strip() for x in text.splitlines()) if l and not l.startswith("```")]


def build(prov, prompt, say=print):
    topic = "The website is for: %s\n" % prompt
    say("step 1/5: name and tagline")

    def ok_head(t):
        ls = [re.sub(r"^\w+\s*:\s*", "", l) for l in _lines(t)]
        ls = [_clean(l) for l in ls if _clean(l)]
        return (ls[0][:50], ls[1][:140]) if len(ls) >= 2 else None
    name, tag = _ask(prov, topic + "Reply with exactly two lines. Line 1: a short business name. Line 2: a one-sentence tagline. No other text.", ok_head, say=say)

    say("step 2/5: about")

    def ok_para(t):
        t = _clean(re.sub(r"^\w+\s*:\s*", "", " ".join(_lines(t))))
        return t[:700] if len(t) > 60 else None
    about = _ask(prov, topic + "Business name: %s\nWrite one friendly paragraph of 3 or 4 sentences about this business. Plain text only." % name, ok_para, say=say)

    say("step 3/5: offerings")

    def ok_items(t):
        out = []
        for l in _lines(t):
            l = re.sub(r"^[\d.\-*\s]+", "", l)
            parts = [_clean(p) for p in l.split("|")]
            if len(parts) >= 3 and parts[0] and parts[2]:
                out.append(tuple(parts[:3]))
        return out[:6] if len(out) >= 3 else None
    items = _ask(prov, topic + "Business name: %s\nList 6 products or services this business offers, one per line, in exactly this format:\nname | price | one short description\nExample line:\nHouse latte | $4.50 | Espresso with steamed milk\nNo other text." % name, ok_items, say=say)

    say("step 4/5: reviews")

    def ok_rev(t):
        out = []
        for l in _lines(t):
            l = re.sub(r"^[\d.\-*\s]+", "", l)
            parts = [_clean(p) for p in l.split("|")]
            if len(parts) >= 2 and len(parts[0]) > 15 and parts[1]:
                out.append(tuple(parts[:2]))
            elif len(parts) == 1 and len(parts[0]) > 25:
                out.append((parts[0], "Sample customer"))
        return out[:3] if len(out) >= 2 else None
    revs = _ask(prov, topic + "Business name: %s\nWrite 3 short customer reviews, one per line, exactly:\nreview text | first name\nExample line:\nBest flat white in town, and the staff remember my order. | Maya\nNo other text." % name, ok_rev, say=say)

    say("step 5/5: section title and contact")

    def ok_ct(t):
        ls = [_clean(re.sub(r"^[A-Za-z][A-Za-z ]*:\s*", "", l)) for l in _lines(t)]
        ls = [l for l in ls if l]
        return ls[:4] if len(ls) >= 4 else None
    ct = _ask(prov, topic + "Business name: %s\nReply with exactly four lines. Line 1: a one or two word title for the offerings section (like Menu or Services). Line 2: a street address. Line 3: opening hours. Line 4: a phone number. No other text." % name, ok_ct, say=say)
    sect, addr, hours, phone = ct

    e = html.escape
    cards = "".join('<div class="card"><h3>%s</h3><div class="price">%s</div><p>%s</p></div>' % (e(n), e(p), e(d)) for n, p, d in items)
    rev = "".join('<div class="card"><blockquote>&ldquo;%s&rdquo;<div class="who">&mdash; %s</div></blockquote></div>' % (e(t), e(w)) for t, w in revs)
    page = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(name)s</title><style>%(style)s</style></head><body>
<nav><b>%(name)s</b><a href="#about">About</a><a href="#offer">%(sect)s</a><a href="#reviews">Reviews</a><a href="#contact">Contact</a></nav>
<header class="hero"><h1>%(name)s</h1><p>%(tag)s</p><a class="btn" href="#offer">See the %(sect)s</a></header>
<section id="about"><h2>About us</h2><p>%(about)s</p></section>
<section id="offer"><h2>%(sect)s</h2><div class="grid">%(cards)s</div></section>
<section id="reviews"><h2>What people say</h2><p class="who">Sample reviews written by an AI as placeholders, not real customers.</p><div class="grid">%(rev)s</div></section>
<section id="contact"><h2>Visit us</h2><p>%(addr)s<br>%(hours)s<br>%(phone)s</p></section>
<footer>&copy; %(name)s. Page text written by a language model, assembled by Ace.</footer>
</body></html>
""" % dict(name=e(name), style=STYLE, sect=e(sect), tag=e(tag), about=e(about), cards=cards, rev=rev, addr=e(addr), hours=e(hours), phone=e(phone))
    hard = [m for lvl, m in gate.check_html(page) if lvl == "hard"]
    return page, hard


def run(prov, prompt, out, say=print):
    page, hard = build(prov, prompt, say)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    say("wrote %s (%d characters); hard check failures: %s" % (out, len(page), "; ".join(hard) or "none"))
    return page, hard
