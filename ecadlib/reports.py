"""The reports people open, in the shared voice (docs/ui-voice.md): outcome first, then what the reader must do,
grouped by action, in plain words. One document structure renders as Markdown and as a self-contained HTML page with
the web UI's look (light and dark), so both always say the same.

    part_view(item)                      -> one plan item as people see it: status word, reasons + fixes, sources
    review_doc(items, bom)               -> the library review (librarian review.md / review.html)
    to_md(doc) / to_html(doc)            -> text
    write_review_files(items, out, bom, details_md) -> writes <out>/review.md and <out>/review.html

A doc is {"title", "lead": [lines], "next": [lines], "sections": [{"title", "intro", "parts": [part], "table":
(header, rows)}], "details": [markdown lines]}; a part is {"name", "status", "word", "lines": [{"text", "fix",
"note", "code"}], "meta": [(label, value)]}."""
import html, re
from pathlib import Path

from . import messages as M

CHIP = {"ready": "ok", "held": "bad", "awaiting_approval": "info", "duplicate": "off", "existing": "off"}


def tried_line(t):
    """A librarian "what was tried" entry with its source in words: "standard: footprint ..." -> "standard parts: ..."."""
    t = M.clean(t)
    m = re.match(r"(kicad|standard|easyeda|local|datasheet)( reader)?\b", t)
    return M.SOURCES[m.group(1)] + t[m.end():] if m else t


def _par(it, *names):
    p = {x.get("name"): x.get("value") for x in it.get("parameters") or []}
    return next((str(p[n]) for n in names if str(p.get(n) or "").strip()), "")


def part_view(it):
    """A plan item -> {name, status, word, reasons, checks, meta, tried, datasheet}. reasons: why it is held (or waits);
    checks: what a person should look at even when it is ready. Each is an explain() dict (code kept for JSON)."""
    st = it.get("status") or ""
    rc = it.get("release_check") or {}
    lib = it.get("librarian") or {}
    reasons = M.explain_all(it.get("hold_reasons") or []) if st == "held" else []
    if st == "awaiting_approval":
        miss = "; ".join((it.get("approval") or {}).get("missing") or [])
        reasons.append(M.explain("awaiting approval" + (f": {miss}" if miss else "")))
    if st == "duplicate" or it.get("duplicate_of"):
        reasons.append(M.explain(f"duplicate of {it.get('duplicate_of') or '?'}"))
    if st == "deleted_in_workspace":
        reasons.append(M.explain("deleted in the Altium 365 workspace"))
    have = {(r["text"], r["fix"]) for r in reasons}
    checks = [c for c in M.explain_all(lib.get("review") or []) if (c["text"], c["fix"]) not in have]
    if st == "held":                          # a disagreeing cross-check is why nothing was written: say it first
        first = [c for c in checks if c["code"] == "crosscheck.disagree"]
        reasons, checks = first + reasons, [c for c in checks if c not in first]
    cc = (it.get("cross_check") or {}).get("status")
    if cc == "unchecked":
        checks.append(M.explain("cross-check unchecked: " + str((it.get("cross_check") or {}).get("summary") or "")))
    word = M.status_word("allowed" if rc.get("result") == "allowed" else st)
    src = it.get("source") or {}
    meta = [("Symbol from", M.source_word(src.get("symbol"))), ("Footprint from", M.source_word(src.get("footprint")))]
    if cc:
        meta.append(("Cross-check", {"agreed": "agreed with a second source", "disagreed": "disagreed (held)",
                                     "unchecked": "could not be made", "skipped": "not needed"}.get(cc, cc)))
    if rc.get("parity"):
        meta.append(("KiCad and Altium", "match" if rc["parity"] == "match" else "differ"))
    if rc.get("height"):
        meta.append(("Height", f"{rc['height']:.2f} mm"))
    if it.get("allow"):
        a = it["allow"]
        meta.append(("Released anyway", " ".join(x for x in (str(a.get("by") or ""), str(a.get("date") or "")) if x)))
    ds = _par(it, "ComponentLink2URL", "Datasheet")
    return {"name": it.get("name", "?"), "status": st, "word": word, "reasons": reasons, "checks": checks, "meta": meta,
            "tried": [tried_line(t) for t in lib.get("tried") or []], "datasheet": ds if ds.startswith("http") else ""}


def _lines(view, with_checks=True):
    out = [dict(r) for r in view["reasons"]]
    if with_checks:
        out += [dict(c, also=bool(view["reasons"])) for c in view["checks"]]
    return out


def review_doc(items, bom=""):
    views = [part_view(it) for it in items]
    by = lambda *s: [v for v in views if v["status"] in s]
    held, waiting, dup = by("held"), by("awaiting_approval"), by("duplicate")
    pulled = by("existing", "deleted_in_workspace")
    ready = by("ready")
    check = [v for v in ready if v["checks"]]
    name = Path(str(bom)).name if bom else ""
    lead = [f"**{M.plural(len(views), 'part')}: {M.counts(v['status'] for v in views)}.**"]
    todo = len(held) + len(waiting) + len(check)
    if todo:
        lead.append(" ".join(x for x in (
            f"{M.plural(len(held), 'part')} {'is' if len(held) == 1 else 'are'} held: fix {'it' if len(held) == 1 else 'them'} first." if held else "",
            f"{len(waiting)} waiting for sign-off." if waiting else "",
            f"{M.plural(len(check), 'ready part')} to double-check." if check else "") if x))
    else:
        lead.append("Nothing needs a person.")
    nxt = []
    if held:
        nxt.append("Open each held part in `ecadlib ui` (Fill the library): **Pick…** a symbol or footprint, or **Release anyway** after you have looked.")
    if waiting:
        nxt.append("Ask a reviewer to approve the parts waiting for sign-off (`ecadlib ui`, Sign-off).")
    if not held and not waiting:
        nxt.append("Send the library to Altium 365 or KiCad (`ecadlib ui`, step 3), or run `ecadlib status` for the whole workspace.")
    part = lambda v, wc=True: {"name": v["name"], "status": v["status"], "word": v["word"], "lines": _lines(v, wc),
                               "meta": v["meta"], "tried": v["tried"], "datasheet": v["datasheet"]}
    sections = []
    if held:
        sections.append({"id": "held", "title": f"Held: fix these first ({len(held)})",
                         "intro": "Each held part says what is wrong and what to do. It stays out of the library until it is fixed or released anyway.",
                         "parts": [part(v) for v in held]})
    if waiting:
        sections.append({"id": "waiting", "title": f"Waiting for sign-off ({len(waiting)})", "parts": [part(v) for v in waiting]})
    if check:
        sections.append({"id": "check", "title": f"Ready, but check before you release ({len(check)})",
                         "intro": "These passed every check; a person should still look at the points below.",
                         "parts": [part(v) for v in check]})
    if dup:
        sections.append({"id": "duplicate", "title": f"Duplicates ({len(dup)})", "parts": [part(v) for v in dup]})
    if ready:
        rows = [[v["name"], v["word"]] + [dict(v["meta"]).get(k, "") for k in ("Symbol from", "Footprint from", "Cross-check", "KiCad and Altium")]
                for v in ready]
        sections.append({"id": "ready", "title": f"Ready ({len(ready)})",
                         "table": (["Part", "Status", "Symbol from", "Footprint from", "Cross-check", "KiCad and Altium"], rows)})
    if pulled:
        sections.append({"id": "a365", "title": f"Already in Altium 365 ({len(pulled)})",
                         "table": (["Part", "Status"], [[v["name"], v["word"]] for v in pulled])})
    return {"title": f"Library review{': ' + name if name else ''}", "lead": lead, "next": nxt, "sections": sections,
            "details": []}


# ---------------------------------------------------------------- Markdown
def _cell(s):
    return str(s if s not in (None, "") else "-").replace("|", "/").replace("\n", " ")


def _table_md(header, rows):
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]


def to_md(doc):
    md = [f"# {doc['title']}", ""] + [x for line in doc["lead"] for x in (line, "")]
    if doc.get("next"):
        md += ["**Next:**", ""] + [f"{i}. {n}" for i, n in enumerate(doc["next"], 1)] + [""]
    for s in doc["sections"]:
        md += [f"## {s['title']}", ""]
        if s.get("intro"):
            md += [s["intro"], ""]
        for p in s.get("parts") or []:
            md += [f"### {p['name']} ({p['word']})", ""]
            for ln in p["lines"]:
                md.append(f"- {'Also check: ' if ln.get('also') else ''}{ln['text']}"
                          + (f" {ln['note']}" if ln.get("note") else "") + f"  \n  **Next:** {ln['fix']}")
            if p["lines"]:
                md.append("")
            meta = [f"{k}: {v}" for k, v in p["meta"]] + ([f"Datasheet: <{p['datasheet']}>"] if p.get("datasheet") else [])
            md += [" · ".join(meta), ""]
            if p["status"] == "held" and p.get("tried") and any(ln.get("code", "").startswith("files.") for ln in p["lines"]):
                md += ["What was tried: " + "; ".join(p["tried"]), ""]
        if s.get("table"):
            md += _table_md(*s["table"]) + [""]
    if doc.get("details"):
        md += ["## Details", ""] + list(doc["details"])
    return "\n".join(md).rstrip() + "\n"


# ---------------------------------------------------------------- HTML (the web UI's tokens and chips)
CSS = """:root{--bg:#f6f7f9;--panel:#fff;--text:#1b1f24;--muted:#5d6673;--line:#dde1e6;--accent:#2357d9;
--ok:#1d7a46;--ok-bg:#e3f4ea;--warn:#9a5b00;--warn-bg:#fdf1dc;--bad:#b3261e;--bad-bg:#fbe6e4;--info:#3d4aa8;
--info-bg:#e9ebfb;--off:#5d6673;--off-bg:#eceef1}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--panel:#1c2026;--text:#e6e9ed;--muted:#9aa3ad;--line:#2e343c;
--accent:#7ea2ff;--ok:#6fd39a;--ok-bg:#173325;--warn:#f0b660;--warn-bg:#3a2c12;--bad:#ff8a80;--bad-bg:#3d1d1b;
--info:#a9b3ff;--info-bg:#262a4a;--off:#9aa3ad;--off-bg:#262b32}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--text)}
header{padding:14px 20px;border-bottom:1px solid var(--line);background:var(--panel)}header h1{font-size:18px;margin:0}
main{max-width:1100px;margin:0 auto;padding:18px 16px 60px}a{color:var(--accent)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 6px}.lead{font-size:15px}.muted{color:var(--muted)}
.part{border-top:1px solid var(--line);padding:12px 0}.part:first-of-type{border-top:0}
.part h3{font-size:14px;margin:0 0 6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.chip{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;font-weight:600}
.chip.ok{background:var(--ok-bg);color:var(--ok)}.chip.bad{background:var(--bad-bg);color:var(--bad)}
.chip.info{background:var(--info-bg);color:var(--info)}.chip.off{background:var(--off-bg);color:var(--off)}
.chip.warn{background:var(--warn-bg);color:var(--warn)}
ul.why{margin:4px 0;padding-left:18px}ul.why li{margin:4px 0}.fix{display:block;color:var(--muted)}
.fix b{color:var(--text)}.meta{color:var(--muted);font-size:12.5px;overflow-wrap:anywhere}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}
details summary{cursor:pointer;color:var(--muted)}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
@media (max-width:600px){main{padding:12px 10px}th,td{padding:5px 4px}}"""


def _inline(s):
    t = html.escape(str(s))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"&lt;(https?://[^&\s]+)&gt;", lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', t)
    return t


def md_to_html(lines):
    """The small Markdown the reports use (headings, bullets, numbered lists, tables, paragraphs) -> HTML."""
    out, i, lines = [], 0, list(lines)
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.startswith("#"):
            lvl = min(len(ln) - len(ln.lstrip("#")) + 1, 4)
            out.append(f"<h{lvl}>{_inline(ln.lstrip('#').strip())}</h{lvl}>"); i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", lines[i].strip()):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], rows[1:]
            out.append('<div class="scroll"><table><thead><tr>' + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table></div>")
        elif re.match(r"\s*([-*]|\d+\.) ", ln):
            tag = "ol" if re.match(r"\s*\d+\. ", ln) else "ul"
            items = []
            while i < len(lines) and re.match(r"\s*([-*]|\d+\.) ", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.) ", "", lines[i])); i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(r"(#|\||\s*([-*]|\d+\.) )", lines[i]):
                para.append(lines[i]); i += 1
            out.append(f"<p>{'<br>'.join(_inline(p) for p in para)}</p>")
    return "\n".join(out)


def to_html(doc):
    e = html.escape
    body = ['<div class="card">'] + [f'<p class="lead">{_inline(x)}</p>' for x in doc["lead"]]
    if doc.get("next"):
        body.append("<p><b>Next:</b></p><ol>" + "".join(f"<li>{_inline(n)}</li>" for n in doc["next"]) + "</ol>")
    body.append("</div>")
    for s in doc["sections"]:
        body.append(f'<section class="card" id="{e(s.get("id", ""))}"><h2>{e(s["title"])}</h2>')
        if s.get("intro"):
            body.append(f'<p class="muted">{e(s["intro"])}</p>')
        for p in s.get("parts") or []:
            body.append(f'<div class="part"><h3>{e(p["name"])} <span class="chip {CHIP.get(p["status"], "warn")}">{e(p["word"])}</span></h3>')
            if p["lines"]:
                body.append('<ul class="why">' + "".join(
                    f"<li>{'Also check: ' if ln.get('also') else ''}{e(ln['text'])}{(' ' + e(ln['note'])) if ln.get('note') else ''}"
                    f'<span class="fix"><b>Next:</b> {e(ln["fix"])}</span></li>' for ln in p["lines"]) + "</ul>")
            meta = [f"{e(k)}: {e(v)}" for k, v in p["meta"]]
            if p.get("datasheet"):
                meta.append(f'<a href="{e(p["datasheet"])}">Datasheet</a>')
            body.append(f'<div class="meta">{" · ".join(meta)}</div>')
            if p["status"] == "held" and p.get("tried") and any(ln.get("code", "").startswith("files.") for ln in p["lines"]):
                body.append(f'<div class="meta">What was tried: {e("; ".join(p["tried"]))}</div>')
            body.append("</div>")
        if s.get("table"):
            body.append(md_to_html(_table_md(*s["table"])))
        body.append("</section>")
    if doc.get("details"):
        body.append(f'<section class="card" id="details"><details><summary>Details</summary>{md_to_html(doc["details"])}</details></section>')
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{e(doc['title'])}</title><style>{CSS}</style></head>\n<body><header><h1>{e(doc['title'])}</h1></header>"
            f"<main>\n" + "\n".join(body) + "\n</main></body></html>\n")


def write_review_files(items, out, bom="", details=()):
    """<out>/review.md and <out>/review.html from the plan items; details: Markdown lines for the Details section."""
    doc = review_doc(items, bom)
    doc["details"] = [M.clean(x) for x in details]
    out = Path(out)
    (out / "review.md").write_text(to_md(doc), encoding="utf-8")
    (out / "review.html").write_text(to_html(doc), encoding="utf-8")
    return doc
