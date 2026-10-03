"""Side-by-side comparison report as one self-contained HTML file."""
import html

E = html.escape


def _stats(x):
    tok = ("%d in / %d out" % (x["tokens_in"], x["tokens_out"])) if x["usage_known"] else "not reported by provider"
    cost = ("$%.4f" % x["cost"]) if x["cost"] is not None else "n/a (no price set or no usage reported)"
    hard = [m for s, m in x["issues"] if s == "hard"]
    adv = [m for s, m in x["issues"] if s == "advisory"]
    return ("<dl><dt>Time</dt><dd>%.2fs</dd><dt>Calls</dt><dd>%d (%d repair)</dd><dt>Tokens</dt><dd>%s</dd><dt>Cost</dt><dd>%s</dd>"
            "<dt>Hard check failures</dt><dd>%d%s</dd><dt>Advisory notes</dt><dd>%d</dd></dl>") % (
        x["seconds"], x["calls"], x["fixes"], tok, cost, len(hard),
        (": " + E("; ".join(hard))) if hard else "", len(adv))


def _view(x):
    if x["kind"] == "html":
        return '<iframe sandbox title="rendered output" srcdoc="%s"></iframe>' % E(x["artifact"], quote=True)
    return "<pre>%s</pre>" % E(x["artifact"][:6000])


def render(c):
    mock = ('<p class="mock">MOCK RUN: the echo provider returned canned text. These numbers say nothing about quality.</p>'
            if c["mock"] else "")
    skills = ", ".join("%s (%s)" % (s["name"], ", ".join(s["matched"])) for s in c["ace"]["skills"]) or "none selected"
    t = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Ace comparison</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{margin:0;font:15px/1.5 system-ui,sans-serif;background:#0b0f1a;color:#e8ecf4}
header{padding:20px 28px;border-bottom:1px solid #1f2740}h1{margin:0;font-size:20px}h1 b{color:#ffb020}
.sub{color:#8f9bb8;margin-top:4px}.mock{background:#3a2a00;color:#ffd27a;padding:10px 28px;margin:0}
main{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:20px 28px}@media(max-width:800px){main{grid-template-columns:1fr}}
section{background:#121a2e;border:1px solid #1f2740;border-radius:12px;padding:16px}h2{margin:0 0 8px;font-size:15px;color:#ffb020}
dl{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:13px;color:#b8c2dc}dt{color:#8f9bb8}dd{margin:0}
iframe{width:100%;height:420px;border:1px solid #1f2740;border-radius:8px;background:#fff;margin-top:12px}
pre{white-space:pre-wrap;background:#0b0f1a;padding:12px;border-radius:8px;max-height:420px;overflow:auto;font-size:12px}
footer{padding:0 28px 28px;color:#8f9bb8;font-size:13px}</style></head><body>
<header><h1><b>Ace</b> comparison</h1><div class="sub">Same prompt, same provider (@@PROV@@), same checks. Prompt: @@PROMPT@@</div></header>@@MOCK@@
<main><section><h2>Plain</h2>@@PSTAT@@@@PVIEW@@</section><section><h2>With Ace</h2><div class="sub">Skills: @@SKILLS@@</div>@@ASTAT@@@@AVIEW@@</section></main>
<footer>One run each. A single run is an anecdote, not a benchmark. Checks are structural (markup, syntax); they do not judge taste or correctness of facts.</footer>
</body></html>"""
    vals = dict(PROV=E(c["provider"]), PROMPT=E(c["prompt"][:200]), MOCK=mock, PSTAT=_stats(c["plain"]), PVIEW=_view(c["plain"]),
                SKILLS=E(skills), ASTAT=_stats(c["ace"]), AVIEW=_view(c["ace"]))
    for k, v in vals.items():
        t = t.replace("@@%s@@" % k, v)
    return t
