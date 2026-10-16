"""Run the real pipeline on an accuracy test set and score every part (`ecadlib eval run` / `compare`).

    ecadlib eval run [--set kicad-v1] [--limit N] [--family qfn,sot] [--model M] [--record DIR | --replay DIR] -o out/eval/x
    ecadlib eval compare out/eval/a out/eval/b

run: the set's cases become one BOM (MPN, manufacturer, the package a BOM would name, datasheet link) and go through
librarian.run with the sources you choose (default: the rules' [sources] order without `kicad`, because KiCad's library
is the ground truth here and would answer its own questions). Whatever source resolves a part, its .kicad_mod and
.SchLib are scored against the reference (ecadlib/eval/score.py). Reported: pass rate overall, per family and per
source; tokens and USD per part (the API usage block of the message that answered: live, cached or replayed; else
datasheet's own estimate, which is rough without a key); wall time. Claude modes:
  live        ANTHROPIC_API_KEY set: one synchronous call per part that reaches the datasheet source (cached in <out>)
  --record D  live, and every answer is also saved to D/<MPN>.json (message + what was sent)
  --replay D  no API at all: answers come from D; a part without a recording is reported unresolved
  estimate    no key and no --replay: nothing is sent; those parts are unresolved, with estimated tokens / USD
Outputs: <out>/report.json, <out>/report.md, <out>/lib (the librarian's output: plan.json, review.md, models).
compare: pass rate and cost per family side by side, and the cases that flipped (pass -> fail, fail -> pass)."""
import csv, datetime, json, re, time
from pathlib import Path

from .. import altium_lib as AL, datasheet as D, footprint_bench as FB, librarian as L, rules as RU
from . import score as S

SETS = Path(__file__).with_name("sets")
BOM_HEAD = ["Designator", "Qty", "Value", "Footprint", "Manufacturer", "Mfr. Part #", "Description", "Datasheet"]


def set_path(name):
    p = Path(name)
    if p.suffix == ".json" and p.exists():
        return p
    f = SETS / f"{name}.json"
    if not f.exists():
        raise FileNotFoundError(f"no test set {name!r}; available: {', '.join(available()) or 'none'}")
    return f


def available():
    return sorted(f.stem for f in SETS.glob("*.json"))


def load_set(name):
    return json.loads(set_path(name).read_text(encoding="utf-8"))


def select(cases, families=None, ids=None, limit=None):
    """Filter, then interleave families round-robin so --limit N still covers every family."""
    cs = [c for c in cases if (not families or c["family"] in families) and (not ids or c["id"] in ids or c["mpn"] in ids)]
    by = {}
    for c in cs:
        by.setdefault(c["family"], []).append(c)
    out, i = [], 0
    while any(i < len(v) for v in by.values()):
        out += [v[i] for v in by.values() if i < len(v)]
        i += 1
    return out[:limit] if limit else out


def write_bom(cases, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(BOM_HEAD)
        for i, c in enumerate(cases, 1):
            w.writerow([f"U{i}", 1, c["mpn"], c["package"], c["manufacturer"], c["mpn"], c["description"], c["datasheet"]])
    return path


def usd(model, usage):
    """USD of one message from its usage block (synchronous rates): ds_route.usage_usd, which also knows the cache-read
    prices and Claude Haiku 5.5's dearer tier above 100K prompt tokens."""
    if not usage:
        return None
    from .. import ds_route
    return ds_route.usage_usd(model, usage)


def _safe(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))[:100] or "part"


class EvalReader(D.Reader):
    """datasheet.Reader that remembers what each part cost, and can record answers or replay them."""

    def __init__(self, *a, record=None, replay=None, **kw):
        super().__init__(*a, **kw)
        self.record, self.replay = (Path(record) if record else None), (Path(replay) if replay else None)
        self.results = D.ResultCache(self.out / "cache")          # per eval run: no answer leaks in from elsewhere
        self.used = {}
        if self.replay:
            self.key, self.estimate_only = "replay (never sent)", False
        elif not self.key:
            self.estimate_only = True

    def _estimate(self, job):
        k, self.key = self.key, None                              # datasheet's own estimate, without count_tokens
        try:
            r = self.estimate([job], batch=False)["rows"][0]
        finally:
            self.key = k
        return {"input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"], "usd": r["usd"], "how": r["how"]}

    def job(self, part, urls, package=None, pages=None, name=None):
        if self.replay:
            f = self.replay / f"{_safe(part)}.json"
            if not f.exists():
                raise D.DatasheetError(f"no recorded response for {part} in {self.replay}")
            rec = json.loads(f.read_text(encoding="utf-8"))
            j = rec["job"]
            job = {"key": f"replay-{_safe(part)}", "name": name or part, "part": part, "package": package, "url": j["url"],
                   "where": j["where"], "offset": j["offset"], "headers": {}, "body": {"max_tokens": D.MAX_TOKENS},
                   "prov": j["prov"], "recording": rec}
        else:
            job = super().job(part, urls, package, pages, name)
        job["estimate"] = (job.get("recording") or {}).get("estimate") or self._estimate(job)
        self.used.setdefault(part, {"estimate": job["estimate"]})
        return job

    def cached(self, job):
        rec = job.get("recording")
        if rec:
            return self._result(job, {"message": rec["message"], "model": rec.get("model"), "via": "replay",
                                      "page_offset": job["offset"]})
        res = super().cached(job)
        if res is not None:
            self.used[job["part"]]["via"] = "cached"
        return res

    def read_now(self, job, _guarded=False):
        if self.replay:
            raise D.DatasheetError(f"no recorded response for {job['part']}")
        t0 = time.time()
        try:
            return super().read_now(job, _guarded)
        finally:
            self.used[job["part"]]["api_s"] = round(time.time() - t0, 2)

    def _result(self, job, rec):
        msg = rec["message"]
        u = self.used.setdefault(job["part"], {"estimate": job.get("estimate")})
        u.setdefault("via", rec.get("via") or "live")
        u.update(usage=msg.get("usage"), model=msg.get("model") or rec.get("model") or self.model)
        # every answer the part's route paid for (cheaper / fewer-page tries first, this one last): all count in the cost
        atts = (job["recording"].get("attempts") if job.get("recording") else
                [{"model": a.get("model"), "usage": a.get("usage")} for a in (job.get("route") or {}).get("attempts") or []])
        u["attempts"] = atts or []
        if self.record and not job.get("recording"):
            self.record.mkdir(parents=True, exist_ok=True)
            j = {k: job[k] for k in ("url", "where", "offset", "prov")}
            (self.record / f"{_safe(job['part'])}.json").write_text(json.dumps(
                {"part": job["part"], "model": rec.get("model") or self.model, "recorded": datetime.datetime.now().isoformat(timespec="seconds"),
                 "job": j, "estimate": job.get("estimate"), "message": msg, "attempts": u["attempts"]}, indent=1, default=str), encoding="utf-8")
        return super()._result(job, rec)


def _symbol_pins(path):
    try:
        _, syms = AL.read_schlib(path)
    except (OSError, ValueError):
        return None
    pins = {}
    for s in syms:
        for r in s["records"]:
            if r.get("RECORD") == "2" and "num" in r:
                pins.setdefault(r["num"], r["name"])
    return list(pins.items())


def _cost(case_used, model):
    """-> tokens_in, tokens_out, usd, how."""
    if not case_used:
        return 0, 0, 0.0, ""
    u, est = case_used.get("usage"), case_used.get("estimate") or {}
    if u:
        tin = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
        tout, cost = u.get("output_tokens", 0), usd(case_used.get("model") or model, u)
        how = {"replay": "recorded usage (replay)", "cached": "recorded usage (cached)"}.get(case_used.get("via"), "API usage")
        earlier = (case_used.get("attempts") or [])[:-1]          # answers before the kept one (route escalations)
        for a in earlier:
            au = a.get("usage") or {}
            tin += au.get("input_tokens", 0) + au.get("cache_creation_input_tokens", 0) + au.get("cache_read_input_tokens", 0)
            tout += au.get("output_tokens", 0)
            c = usd(a.get("model") or model, au) if au else None
            cost = None if cost is None or c is None else round(cost + c, 6)
        if earlier:
            how += f" + {len(earlier)} earlier answer{'s' if len(earlier) > 1 else ''}"
        return tin, tout, cost, how
    return est.get("input_tokens", 0), est.get("output_tokens", 0), est.get("usd"), f"estimate ({est.get('how', '')})"


def summarize(rows):
    def agg(rs):
        n = len(rs)
        p = sum(r["pass"] for r in rs)
        cost = [r["usd"] for r in rs if r["usd"]]
        return {"cases": n, "pass": p, "rate": round(p / n, 4) if n else None,
                "tokens_in": sum(r["tokens_in"] for r in rs), "tokens_out": sum(r["tokens_out"] for r in rs),
                "usd": round(sum(cost), 4), "usd_per_part": round(sum(cost) / n, 5) if n else None,
                "sources": dict(sorted({s: sum(r["source"] == s for r in rs) for s in {r["source"] for r in rs}}.items()))}
    fams = {}
    for r in rows:
        fams.setdefault(r["family"], []).append(r)
    srcs = {}
    for r in rows:
        srcs.setdefault(r["source"], []).append(r)
    fields = {}
    for r in rows:
        for k, f in r["fields"].items():
            fields.setdefault(k, [0, 0])
            fields[k][0] += f["ok"]
            fields[k][1] += 1
    shared = [r for r in rows if r.get("footprint_shared_with")]
    return {"overall": agg(rows), "shared_footprints": {"cases": len(shared), "failing": sum(not r["pass"] for r in shared)}, "families": {k: agg(v) for k, v in sorted(fams.items())},
            "by_source": {k: agg(v) for k, v in sorted(srcs.items())},
            "fields": {k: {"ok": a, "of": b} for k, (a, b) in sorted(fields.items())}}


def run(set_name="kicad-v1", out="out/eval/run", limit=None, model=None, record=None, replay=None, sources=None,
        families=None, ids=None, offline=False, cache="out/http_cache", http=None, api_key=None, rules=None, providers=None,
        tol=None, log=print):
    data = load_set(set_name)
    cases = select(data["cases"], families, ids, limit)
    if not cases:
        raise ValueError("no cases selected")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lib = out / "lib"
    rules = RU.resolve(rules, out)
    sources = list(sources) if sources else [s for s in rules["sources"]["order"] if s != "kicad"]
    if "kicad" in sources:
        log("  note: `kicad` is the ground truth of this set; parts it resolves are scored against themselves")
    bom = write_bom(cases, out / "bom.csv")
    if http is None:
        from ..httpcache import CachedHTTP
        http = CachedHTTP(cache, min_interval=0.5, offline=offline, timeout=60)
    reader = EvalReader(lib, cache, api_key, rules, http, model=model, record=record, replay=replay, log=lambda *a: None)
    mode = "replay" if replay else "estimate (no API key: nothing sent)" if reader.estimate_only else \
        "live, recording" if record else "live"
    log(f"  eval {data['meta']['name']}: {len(cases)} cases, sources {','.join(sources)}, model {reader.model}, {mode}")
    t0 = time.time()
    items = L.run(bom, [], lib, sources=sources, refresh=[], offline=offline, cache=cache, http=http, api_key=api_key,
                  log=lambda *a: None, gen_step=False, rules=rules, reader=reader, providers=providers)
    wall = time.time() - t0
    by_mpn = {}
    for it in items:
        mpn = next((p["value"] for p in it.get("parameters", []) if p["name"] == "Manufacturer Part Number"), it["name"])
        by_mpn[mpn] = it
    rows = []
    for c in cases:
        it = by_mpn.get(c["mpn"]) or {}
        fp = (it.get("footprint") or {}).get("kicad_mod")
        mine = FB.numbers((lib / fp).read_text(encoding="utf-8")) if fp and (lib / fp).exists() else None
        sf = (it.get("symbol") or {}).get("file")
        pins = _symbol_pins(lib / sf) if sf and (lib / sf).exists() else None
        sc = S.score(mine, data["references"][c["footprint"]], pins, c.get("pins"), tol)
        tin, tout, cost, how = _cost(reader.used.get(c["mpn"]), reader.model)
        src = ((it.get("source") or {}).get("footprint") or "none").split(":")[0]
        shared = next((n[len("shared with "):] for n in (it.get("librarian") or {}).get("notes", []) if n.startswith("shared with ")), "")
        rows.append({"id": c["id"], "family": c["family"], "mpn": c["mpn"], "footprint": c["footprint"], "pass": sc["pass"],
                     "source": src, "symbol_source": ((it.get("source") or {}).get("symbol") or "none").split(":")[0],
                     "errors": sc["errors"], "fields": sc["fields"], "tokens_in": tin, "tokens_out": tout, "usd": cost,
                     "cost_how": how, "footprint_shared_with": shared, "api_s": (reader.used.get(c["mpn"]) or {}).get("api_s"),
                     "tried": ((it.get("librarian") or {}).get("tried") or [])})
    rep = {"meta": {"set": data["meta"]["name"], "set_version": data["meta"].get("version"), "set_built": data["meta"].get("built"),
                    "model": reader.model, "sources": sources, "mode": mode, "record": str(record or ""), "replay": str(replay or ""),
                    "tolerances": dict(S.TOL, **(tol or {})), "cases": len(rows),
                    "started": datetime.datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
                    "wall_s": round(wall, 1), "wall_s_per_part": round(wall / len(rows), 3)},
           "summary": summarize(rows), "cases": rows}
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    (out / "report.md").write_text(report_md(rep), encoding="utf-8")
    return rep


def _pct(x):
    return "-" if x is None else f"{100 * x:.1f} %"


def _usd(x):
    return "-" if x is None else f"${x:.4f}"


def report_md(rep):
    m, s = rep["meta"], rep["summary"]
    o = s["overall"]
    md = [f"# Accuracy: {m['set']} (v{m['set_version']})", "",
          f"Model {m['model']}, sources {', '.join(m['sources'])}, mode {m['mode']}; {m['cases']} cases in {m['wall_s']} s "
          f"({m['wall_s_per_part']} s per part).", "",
          f"**Pass: {o['pass']}/{o['cases']} ({_pct(o['rate'])})**; tokens {o['tokens_in']} in / {o['tokens_out']} out; "
          f"{_usd(o['usd'])} in all, {_usd(o['usd_per_part'])} per part.", "",
          "| Family | Cases | Pass | Rate | Answered by | USD / part |", "|---|---|---|---|---|---|"]
    for k, f in s["families"].items():
        md.append(f"| {k} | {f['cases']} | {f['pass']} | {_pct(f['rate'])} | "
                  + ", ".join(f"{a} {b}" for a, b in f["sources"].items()) + f" | {_usd(f['usd_per_part'])} |")
    sh = s.get("shared_footprints") or {}
    if sh.get("cases"):
        md += ["", f"{sh['cases']} parts got the footprint file of another part with the same BOM footprint name "
               f"(librarian: one footprint per name); {sh['failing']} of them fail because the two land patterns differ."]
    md += ["", "| Source | Cases | Pass | Rate |", "|---|---|---|---|"]
    md += [f"| {k} | {f['cases']} | {f['pass']} | {_pct(f['rate'])} |" for k, f in s["by_source"].items()]
    md += ["", "| Field | OK |", "|---|---|"] + [f"| {k} | {v['ok']}/{v['of']} |" for k, v in s["fields"].items()]
    md += ["", "## Failing cases", ""]
    for r in rep["cases"]:
        if not r["pass"]:
            why = "; ".join(r["errors"][:3]) if r["source"] != "none" else "unresolved: " + "; ".join(r["tried"][-2:])
            sh = f" [footprint shared with {r['footprint_shared_with']}]" if r.get("footprint_shared_with") else ""
            md.append(f"- **{r['id']}** ({r['source']}){sh}: {why}")
    return "\n".join(md) + "\n"


def _load_report(p):
    p = Path(p)
    return json.loads((p / "report.json" if p.is_dir() else p).read_text(encoding="utf-8"))


def compare(a, b):
    """Two reports (dirs or report.json) -> {families: {fam: (A, B)}, flipped: {...}, text}."""
    A, B = _load_report(a), _load_report(b)
    fa, fb = A["summary"]["families"], B["summary"]["families"]
    fams = {}
    lines = [f"A: {a} ({A['meta']['model']}, {A['meta']['mode']}, {A['meta']['cases']} cases)",
             f"B: {b} ({B['meta']['model']}, {B['meta']['mode']}, {B['meta']['cases']} cases)", "",
             f"{'family':<12} {'pass A':>9} {'pass B':>9} {'diff':>8} {'$/part A':>10} {'$/part B':>10}"]
    for k in sorted(set(fa) | set(fb)) + ["overall"]:
        x = A["summary"]["overall"] if k == "overall" else fa.get(k)
        y = B["summary"]["overall"] if k == "overall" else fb.get(k)
        fams[k] = (x, y)
        ra, rb = (x or {}).get("rate"), (y or {}).get("rate")
        d = f"{100 * (rb - ra):+.1f}" if ra is not None and rb is not None else "-"
        lines.append(f"{k:<12} {_pct(ra):>9} {_pct(rb):>9} {d:>8} {_usd((x or {}).get('usd_per_part')):>10} "
                     f"{_usd((y or {}).get('usd_per_part')):>10}")
    ca, cb = {r["id"]: r for r in A["cases"]}, {r["id"]: r for r in B["cases"]}
    both = [i for i in ca if i in cb]
    flipped = {"fixed": [i for i in both if not ca[i]["pass"] and cb[i]["pass"]],
               "broken": [i for i in both if ca[i]["pass"] and not cb[i]["pass"]],
               "only_in_a": [i for i in ca if i not in cb], "only_in_b": [i for i in cb if i not in ca]}
    lines += ["", f"fail -> pass ({len(flipped['fixed'])}): " + ", ".join(flipped["fixed"]),
              f"pass -> fail ({len(flipped['broken'])}): " + "; ".join(f"{i} [{'; '.join(cb[i]['errors'][:2])}]" for i in flipped["broken"])]
    if flipped["only_in_a"] or flipped["only_in_b"]:
        lines.append(f"cases only in A: {len(flipped['only_in_a'])}, only in B: {len(flipped['only_in_b'])}")
    return {"families": fams, "flipped": flipped, "text": "\n".join(lines)}
