"""The library plan of one BOM as people see it: one card per part with its status in plain words (ready, held,
waiting for sign-off), each held reason as a sentence plus a next step, the cross-check and KiCad/Altium results with
both values when they disagree, the datasheet link (and page when known), what reading the datasheet cost and which
model read it, and a small footprint picture. Read-only; used by the web UI (Workspace.library_parts).

Held-reason wording comes from ecadlib.messages (the shared voice, docs/ui-voice.md)."""
import json, re
from pathlib import Path

from . import messages as M

FIELD = {"pin_count": "pin count", "pitch": "pin pitch", "body_length": "body length", "body_width": "body width",
         "lead_span": "lead span", "ep_length": "exposed pad length", "ep_width": "exposed pad width",
         "has_ep": "exposed pad", "package_type": "package type", "pins": "pin numbers", "pin_names": "pin names"}
_LABEL_TO_FIELD = {"pin count": "pin_count", "pitch": "pitch", "body length": "body_length", "body width": "body_width",
                   "lead span": "lead_span", "exposed pad length": "ep_length", "exposed pad width": "ep_width",
                   "exposed pad": "has_ep", "package type": "package_type", "pin numbers": "pins", "pin names": "pin_names"}
_CC = re.compile(r"^(?:(?P<against>.*): )?(?P<label>[a-z ]+?) read (?P<first>.+?), (?P<src>table|second read) (?P<second>.+)$")
_PAR = re.compile(r"^(?P<what>.+?): KiCad (?P<kicad>.+?) / Altium (?P<altium>.+?)(?: \((?P<note>.*)\))?$")
SVG_MAX = 60_000                                  # a footprint picture bigger than this is left out of the list


# ------------------------------------------------------------------ held reasons in plain words
def reason(raw):
    """One hold reason -> {code, text, fix, detail} in the shared voice (ecadlib.messages); detail is the machine
    wording, kept for reports and JSON."""
    e = M.explain(raw)
    return {"code": e["code"], "text": e["text"] + (f" ({e['note']})" if e.get("note") else ""), "fix": e["fix"], "detail": raw}


# ------------------------------------------------------------------ cross-check, parity, datasheet, cost
def _num(s):
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(s))
    return m and m.group(1)


def cross_check(rec, sidecar=None):
    """Plan item cross_check {status, summary} (+ the read's sidecar, when written) -> what the panel shows:
    {status, label, rows: [{field, label, first, second, against, page}], summary}."""
    if not rec:
        return None
    st = rec.get("status") or "unchecked"
    label = {"agreed": "agrees with a second source", "disagreed": "two sources disagree",
             "unchecked": "not cross-checked", "skipped": "not needed (footprint from another source)"}.get(st, st)
    rows = []
    full = (sidecar or {}).get("cross_check") or {}
    dims = (sidecar or {}).get("dimensions") or {}
    page = lambda f: (dims.get(f) or {}).get("page") if isinstance(dims.get(f), dict) else None
    if full.get("checks"):
        for c in full["checks"]:
            for d in c.get("disagreed") or []:
                rows.append({"field": d["field"], "label": FIELD.get(d["field"], d["field"]), "first": d["first"],
                             "second": d["second"], "against": c.get("against") or c.get("source") or "",
                             "second_is": "table" if c.get("source") == "standard table" else "second read",
                             "page": page(d["field"])})
    else:
        against = ""
        for seg in (rec.get("summary") or "").split("; "):
            m = _CC.match(seg)
            if not m:
                continue
            against = m.group("against") or against
            f = _LABEL_TO_FIELD.get(m.group("label").strip(), m.group("label").strip())
            rows.append({"field": f, "label": FIELD.get(f, m.group("label")), "first": m.group("first"),
                         "second": m.group("second"), "against": against, "second_is": m.group("src"), "page": page(f)})
    out = {"status": st, "label": label, "rows": rows, "summary": rec.get("summary") or ""}
    if st == "disagreed" and rows:
        r = rows[0]
        text, fix = M.held("crosscheck.disagree", field=r["label"], first=r["first"], second=r["second"],
                           other=r["second_is"], page=r["page"])
        out["reason"] = {"code": "crosscheck.disagree", "text": text, "fix": fix, "detail": "cross-check disagrees: " + out["summary"]}
    return out


def parity(item):
    """{result: match | mismatch | None, rows: [{what, kicad, altium}]} from the release check's parity rule."""
    rc = item.get("release_check") or {}
    res = rc.get("parity")
    rows = []
    for m in list(item.get("hold_reasons") or []) + [w for w in rc.get("warnings") or []]:
        rule, _, msg = m.partition(": ")
        if rule != "parity":
            continue
        p = _PAR.match(re.sub(r" \((cannot be overridden|not covered by --allow)\)$", "", msg))
        rows.append({"what": p.group("what"), "kicad": p.group("kicad"), "altium": p.group("altium")} if p
                    else {"what": msg, "kicad": "", "altium": ""})
    if rows and not res:
        res = "mismatch"
    return {"result": res, "label": {"match": "KiCad and Altium match", "mismatch": "KiCad and Altium differ"}.get(res, "not compared"),
            "rows": rows}


def datasheet_url(item):
    params = {p.get("name"): p.get("value") for p in item.get("parameters") or [] if isinstance(p, dict)}
    for k, v in params.items():
        m = re.fullmatch(r"ComponentLink(\d+)Description", k or "")
        if m and str(v).strip().lower() == "datasheet":
            u = params.get(f"ComponentLink{m.group(1)}URL")
            if u:
                return u
    return params.get("Datasheet") or params.get("datasheet") or None


def _sidecar(out, name):
    d = Path(out) / "datasheet"
    if not d.is_dir():
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    for f in (d / f"{name}.extraction.json", d / f"{safe}.extraction.json"):
        try:
            if f.is_file() and f.resolve().parent == d.resolve():
                v = json.loads(f.read_text(encoding="utf-8"))
                return v if isinstance(v, dict) else None
        except (OSError, ValueError):
            return None
    return None


def costs(out):
    """What the datasheet reads of this plan cost so far: totals and per part (from datasheet/cost.jsonl)."""
    from . import ds_route as DR
    recs = DR.read_ledger(Path(out) / "datasheet" / "cost.jsonl")
    s = DR.summarize(recs)
    return {"answers": s["answers"], "usd": round(s["usd"] + s["usd_estimated"], 4), "usd_estimated": s["usd_estimated"],
            "models": sorted(m for m in s["by_model"] if m and m != "?"), "by_part": s["by_part"],
            "tokens": s["input_tokens"] + s["output_tokens"]}


def _picture(out, item):
    """Small top-view SVG of the footprint (from its .kicad_mod), or None."""
    km = (item.get("footprint") or {}).get("kicad_mod")
    if not km:
        return None
    f = (Path(out) / km)
    try:
        if not f.is_file() or Path(out).resolve() not in f.resolve().parents:
            return None
        from .app import footprint_svg
        svg = footprint_svg(None, 12, f.read_text(encoding="utf-8"))
    except Exception:
        return None
    return svg if len(svg) <= SVG_MAX else None


def _pads(svg):
    return len(re.findall(r'class="pad[ "]', svg or ""))


def _decisions(approvals):
    """Recorded decisions without an approvals.json policy: who approved last (a later reject takes it back)."""
    last = {}
    for a in approvals or []:
        if isinstance(a, dict) and a.get("who"):
            last[a.get("key") or a["who"]] = a
    ok = [a["who"] for a in last.values() if a.get("decision") == "approve"]
    no = [a["who"] for a in last.values() if a.get("decision") == "reject"]
    if not ok and not no:
        return None
    return {"required": False, "met": None, "approved_by": ok, "rejected_by": no, "missing": [], "stale": []}


# ------------------------------------------------------------------ the cards
def cards(out, items, pictures=True):
    """out: the librarian folder of a BOM (plan.json, models/, datasheet/); items: the plan. -> {parts, counts, cost}."""
    from . import librarian as L
    cost = costs(out)
    review = {r["part"]: r for r in L.review_rows(items)}
    parts = []
    for it in items:
        name, st = it["name"], it.get("status") or ""
        sc = _sidecar(out, name) if it.get("cross_check") else None
        cc = cross_check(it.get("cross_check"), sc)
        reasons = []
        for h in it.get("hold_reasons") or []:
            if cc and cc.get("reason") and h.startswith("cross-check"):     # said once, with both values
                continue
            reasons.append(reason(h))
        if cc and cc["status"] == "disagreed" and cc.get("reason"):
            reasons.insert(0, cc["reason"])
        elif st == "held" and not reasons:
            reasons = [reason(x) for x in (review.get(name, {}).get("why") or "").split("; ") if x][:3]
        appr = it.get("approval") or None
        if st == "awaiting_approval" and appr:
            for m in appr.get("missing") or []:
                reasons.append(reason(f"awaiting approval: {m}"))
        pc = cost["by_part"].get(name) or {}
        svg = _picture(out, it) if pictures else None
        url = datasheet_url(it)
        page = next((r["page"] for r in (cc or {}).get("rows", []) if r.get("page")), None) or \
            ((sc or {}).get("pin_table") or {}).get("page")
        parts.append({
            "part": name, "folder": it.get("folder") or "", "description": it.get("description") or "",
            "status": st, "status_label": M.status_word(st),
            "allowed": bool(it.get("allow")), "duplicate_of": it.get("duplicate_of"),
            "reasons": reasons,
            "symbol": {"ref": (it.get("symbol") or {}).get("ref"), "found": bool((it.get("symbol") or {}).get("file")),
                       "from": (it.get("source") or {}).get("symbol") or ""},
            "footprint": {"ref": (it.get("footprint") or {}).get("ref"), "found": bool((it.get("footprint") or {}).get("file")),
                          "from": (it.get("source") or {}).get("footprint") or "", "pads": _pads(svg) or None},
            "picture": svg,
            "cross_check": cc, "parity": parity(it),
            "datasheet": {"url": url, "page": page} if url else None,
            "cost": {"usd": round(pc.get("usd", 0.0), 4), "models": [m for m in pc.get("models") or [] if m],
                     "answers": pc.get("answers", 0)} if pc else None,
            "approval": {k: appr.get(k) for k in ("required", "met", "approved_by", "missing", "stale")} if appr
            else _decisions(it.get("approvals")),
            "tried": (it.get("librarian") or {}).get("tried") or [],
        })
    counts = {"parts": len(parts), "ready": sum(p["status"] == "ready" for p in parts),
              "held": sum(p["status"] == "held" for p in parts),
              "waiting": sum(p["status"] == "awaiting_approval" for p in parts),
              "cross_check_disagreed": sum(bool(p["cross_check"]) and p["cross_check"]["status"] == "disagreed" for p in parts),
              "parity_mismatch": sum(p["parity"]["result"] == "mismatch" for p in parts)}
    return {"parts": parts, "counts": counts,
            "cost": {k: cost[k] for k in ("answers", "usd", "usd_estimated", "models", "tokens")}}
