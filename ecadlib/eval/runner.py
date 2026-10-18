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
import csv, datetime, hashlib, json, re, time
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


# Designator class by the reference footprint's library, as a real BOM would have it: triage sorts parts by it, and
# a chip capacitor on a U line is not a capacitor to any source. Everything else stays U.
DESIGNATOR = {"Resistor_SMD": "R", "Capacitor_SMD": "C", "Inductor_SMD": "L", "LED_SMD": "D", "Diode_SMD": "D"}


def write_bom(cases, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(BOM_HEAD)
        for i, c in enumerate(cases, 1):
            w.writerow([f"{DESIGNATOR.get(c['footprint'].split(':')[0], 'U')}{i}", 1, c["mpn"], c["package"], c["manufacturer"], c["mpn"], c["description"], c["datasheet"]])
    return path


def usd(model, usage, batch=False):
    """USD of one message from its usage block (synchronous rates, half for a Message Batch): ds_route.usage_usd, which
    also knows the cache-read prices and Claude Haiku 5.5's dearer tier above 100K prompt tokens."""
    if not usage:
        return None
    from .. import ds_route
    return ds_route.usage_usd(model, usage, batch=batch)


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

    def job(self, part, urls, package=None, pages=None, name=None, fp_needed=None):
        if self.replay:
            f = self.replay / f"{_safe(part)}.json"
            if not f.exists():
                raise D.DatasheetError(f"no recorded response for {part} in {self.replay}")
            rec = json.loads(f.read_text(encoding="utf-8"))
            j = rec["job"]
            job = {"key": f"replay-{_safe(part)}", "name": name or part, "part": part, "package": package, "url": j["url"],
                   "where": j["where"], "offset": j["offset"], "headers": {}, "body": {"max_tokens": D.MAX_TOKENS},
                   "prov": j["prov"], "recording": rec, "fp_needed": fp_needed}
        else:
            job = super().job(part, urls, package, pages, name, fp_needed)
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

    def check_message(self, job, model, headers, body, key, pages_sent=None):
        """The cross-check's second read: replayed from the recording (never sent), or live and added to it."""
        from .. import crosscheck as CC
        rec = job.get("recording")
        if self.replay:
            got = (rec or {}).get("check")
            if not got:
                raise CC.Unavailable("no recorded second read")
            msg, via = got["message"], "replay"
        else:
            msg, via = super().check_message(job, model, headers, body, key, pages_sent)
        from .. import ds_route as DR
        self.used.setdefault(job["part"], {})["check"] = {"model": DR.billed_model(model, msg), "usage": msg.get("usage")}
        if self.replay:
            return msg, via
        if self.record and not rec:
            f = self.record / f"{_safe(job['part'])}.json"
            if f.exists():
                r = json.loads(f.read_text(encoding="utf-8"))
                r["check"] = {"model": model, "message": msg}
                f.write_text(json.dumps(r, indent=1, default=str), encoding="utf-8")
        return msg, via

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
        u["batch"] = str(rec.get("via") or "").startswith("batch") or bool((job.get("recording") or {}).get("batch"))
        from .. import ds_route as DR                 # priced as the served model when its price is known
        u.update(usage=msg.get("usage"), model=DR.billed_model(rec.get("model") or job.get("model") or self.model, msg))
        # every answer the part's route paid for (cheaper / fewer-page tries first, this one last): all count in the cost
        atts = (job["recording"].get("attempts") if job.get("recording") else
                [{"model": a.get("served") or a.get("model"), "usage": a.get("usage"), "via": a.get("via")}
                 for a in (job.get("route") or {}).get("attempts") or []])
        u["attempts"] = atts or []
        if self.record and not job.get("recording"):
            self.record.mkdir(parents=True, exist_ok=True)
            j = {k: job[k] for k in ("url", "where", "offset", "prov")}
            (self.record / f"{_safe(job['part'])}.json").write_text(json.dumps(
                {"part": job["part"], "model": rec.get("model") or self.model, "recorded": datetime.datetime.now().isoformat(timespec="seconds"),
                 "job": j, "estimate": job.get("estimate"), "message": msg, "attempts": u["attempts"], "batch": u["batch"]}, indent=1, default=str), encoding="utf-8")
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
        bat = bool(case_used.get("batch"))
        tout, cost = u.get("output_tokens", 0), usd(case_used.get("model") or model, u, bat)
        how = {"replay": "recorded usage (replay)", "cached": "recorded usage (cached)"}.get(case_used.get("via"), "API usage")
        how += " (batch: half price)" if bat else ""
        earlier = (case_used.get("attempts") or [])[:-1]          # answers before the kept one (route escalations)
        for a in earlier:
            au = a.get("usage") or {}
            tin += au.get("input_tokens", 0) + au.get("cache_creation_input_tokens", 0) + au.get("cache_read_input_tokens", 0)
            tout += au.get("output_tokens", 0)
            ab = str(a["via"] or "").startswith("batch") if "via" in a else bat   # older recordings: the kept answer's way
            c = usd(a.get("model") or model, au, ab) if au else None
            cost = None if cost is None or c is None else round(cost + c, 6)
        if earlier:
            how += f" + {len(earlier)} earlier answer{'s' if len(earlier) > 1 else ''}"
        ck = case_used.get("check") or {}
        if ck.get("usage"):                                       # the cross-check's second read (ecadlib.crosscheck)
            cu = ck["usage"]
            tin += cu.get("input_tokens", 0) + cu.get("cache_creation_input_tokens", 0) + cu.get("cache_read_input_tokens", 0)
            tout += cu.get("output_tokens", 0)
            c = usd(ck.get("model") or model, cu)
            cost = None if cost is None or c is None else round(cost + c, 6)
            how += " + cross-check read"
        return tin, tout, cost, how
    return est.get("input_tokens", 0), est.get("output_tokens", 0), est.get("usd"), f"estimate ({est.get('how', '')})"


# The part never reached the model: no datasheet link, or none gave a PDF (here or to the API's fetcher). The benchmark
# reports these apart: a dead maker link is not a misread drawing.
NO_DATASHEET = re.compile(r"no datasheet (link gave a PDF|URL known)|could not (be )?(fetch|download)|unable to (fetch|download)", re.I)


def no_datasheet(row):
    return row["source"] == "none" and any(NO_DATASHEET.search(str(t)) for t in row.get("tried") or [])


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
    cc = {}
    for r in rows:
        if r.get("cross_check"):
            cc[r["cross_check"]] = cc.get(r["cross_check"], 0) + 1
    unread = [r for r in rows if no_datasheet(r)]
    return {"cross_check": dict(sorted(cc.items())),"overall": agg(rows),
            "no_datasheet": {"cases": len(unread), "ids": [r["id"] for r in unread]},
            "with_datasheet": agg([r for r in rows if not no_datasheet(r)]), "shared_footprints": {"cases": len(shared), "failing": sum(not r["pass"] for r in shared)}, "families": {k: agg(v) for k, v in sorted(fams.items())},
            "by_source": {k: agg(v) for k, v in sorted(srcs.items())},
            "fields": {k: {"ok": a, "of": b} for k, (a, b) in sorted(fields.items())}}


def run(set_name="kicad-v1", out="out/eval/run", limit=None, model=None, record=None, replay=None, sources=None,
        families=None, ids=None, offline=False, cache="out/http_cache", http=None, api_key=None, rules=None, providers=None,
        tol=None, log=print, provider=None, base_url=None, assist=None, api_key_env=None, repeat=1, batch=False):
    """repeat: run the same cases again (into <out>/repeat-2 ...) and report how many parts gave the same files
    (summary["consistency"]): from the recorded answers when there are any (--record / --replay: the code must be
    deterministic), else live again (the model's own consistency; this pays again).
    provider / base_url / assist / api_key_env: the [ai] settings for this run (ecadlib.llm), so models from any
    provider can be compared on the same set: e.g. provider="ollama", model="qwen2.5:7b" against the Claude default.
    batch: the datasheet reads go out as one Message Batch (half price; the run waits for it)."""
    data = load_set(set_name)
    cases = select(data["cases"], families, ids, limit)
    if not cases:
        raise ValueError("no cases selected")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lib = out / "lib"
    rules = RU.resolve(rules, out)
    over = {k: v for k, v in (("provider", provider), ("base_url", base_url), ("assist", assist),
                              ("api_key_env", api_key_env)) if v}
    if over or (model and provider):
        rules = dict(rules, ai={**rules.get("ai", {}), **over, **({"model": model} if model else {})},
                     datasheet={**rules.get("datasheet", {}), **({k: "" for k in ("provider", "base_url", "assist")} if over else {}),
                                **({"api_key_env": ""} if provider and provider != "anthropic" else {})})
        RU.validate({k: v for k, v in rules.items() if k in ("ai",)}, "eval --provider/--model")
    sources = list(sources) if sources else [s for s in rules["sources"]["order"] if s != "kicad"]
    if "kicad" in sources:
        log("  note: `kicad` is the ground truth of this set; parts it resolves are scored against themselves")
    bom = write_bom(cases, out / "bom.csv")
    if http is None:
        from ..httpcache import CachedHTTP
        http = CachedHTTP(cache, min_interval=0.5, offline=offline, timeout=60)
    reader = EvalReader(lib, cache, api_key, rules, http, model=model, record=record, replay=replay, log=lambda *a: None,
                        batch=bool(batch and not replay))
    mode = "replay" if replay else "estimate (no API key: nothing sent)" if reader.estimate_only else \
        ("live, recording" if record else "live") + (", batch" if reader.batch else "")
    inf = reader.info(reader.model)
    log(f"  eval {data['meta']['name']}: {len(cases)} cases, sources {','.join(sources)}, model {reader.model} "
        f"({inf['ep']['name']}, {inf['plan']['tier']} tier, assist {inf['plan']['assist']}), {mode}")
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
        files = {}                                    # what this run wrote for the part (consistency between runs)
        for kind, rel in (("kicad_mod", fp), ("pcblib", (it.get("footprint") or {}).get("file")), ("schlib", sf)):
            if rel and (lib / rel).exists():
                files[kind] = file_fingerprint(lib / rel)
        rows.append({"id": c["id"], "family": c["family"], "mpn": c["mpn"], "footprint": c["footprint"], "pass": sc["pass"],
                     "files": files, "cross_check": (it.get("cross_check") or {}).get("status", ""),
                     "source": src, "symbol_source": ((it.get("source") or {}).get("symbol") or "none").split(":")[0],
                     "errors": sc["errors"], "fields": sc["fields"], "tokens_in": tin, "tokens_out": tout, "usd": cost,
                     "cost_how": how, "footprint_shared_with": shared, "api_s": (reader.used.get(c["mpn"]) or {}).get("api_s"),
                     "tried": ((it.get("librarian") or {}).get("tried") or [])})
    rep = {"meta": {"set": data["meta"]["name"], "set_version": data["meta"].get("version"), "set_built": data["meta"].get("built"),
                    "model": reader.model, "provider": inf["ep"]["name"], "tier": inf["plan"]["tier"],
                    "rails": inf["plan"]["rails"], "sources": sources, "mode": mode, "record": str(record or ""), "replay": str(replay or ""),
                    "tolerances": dict(S.TOL, **(tol or {})), "cases": len(rows),
                    "started": datetime.datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
                    "wall_s": round(wall, 1), "wall_s_per_part": round(wall / len(rows), 3)},
           "summary": summarize(rows), "cases": rows}
    for i in range(2, max(1, int(repeat or 1)) + 1):
        again = run(set_name, out / f"repeat-{i}", limit, model, None, replay or record, sources, families, ids, offline, cache,
                    http, api_key, rules, providers, tol, log, provider, base_url, assist, api_key_env, batch=batch)
        c = consistency(rep, again)
        prev = rep["summary"].get("consistency")
        if prev is None or c["same"] < prev["same"]:
            rep["summary"]["consistency"] = dict(c, runs=i)
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    (out / "report.md").write_text(report_md(rep), encoding="utf-8")
    return rep


def file_fingerprint(path):
    """sha256 (16 hex) of what a library file holds: a .kicad_mod's bytes; for .PcbLib / .SchLib the records read back
    (altium_lib), so the write time in the Altium file header does not count as a different answer."""
    p = Path(path)
    try:
        if p.suffix.lower() == ".pcblib":
            data = json.dumps(AL.read_pcblib(p), sort_keys=True, default=str).encode()
        elif p.suffix.lower() == ".schlib":
            data = json.dumps(AL.read_schlib(p), sort_keys=True, default=str).encode()
        else:
            data = p.read_bytes()
    except (OSError, ValueError):
        data = p.read_bytes() if p.exists() else b""
    return hashlib.sha256(data).hexdigest()[:16]


def consistency(a, b):
    """Two runs of the same cases (reports, dirs or report.json files) -> how many parts gave the same files:
    {cases, same, rate, from, differ: [{id, files}]}. A part with no files in both runs counts as the same answer."""
    A, B = (x if isinstance(x, dict) else _load_report(x) for x in (a, b))
    cb = {r["id"]: r for r in B["cases"]}
    differ, n = [], 0
    for r in A["cases"]:
        o = cb.get(r["id"])
        if o is None:
            continue
        n += 1
        fa, fb = r.get("files") or {}, o.get("files") or {}
        bad = sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))
        if bad or r.get("cross_check", "") != o.get("cross_check", ""):
            differ.append({"id": r["id"], "files": bad, "cross_check": [r.get("cross_check", ""), o.get("cross_check", "")]})
    same = n - len(differ)
    return {"cases": n, "same": same, "rate": round(same / n, 4) if n else None, "differ": differ,
            "from": "recorded answers" if B["meta"].get("mode") == "replay" else B["meta"].get("mode")}


def _pct(x):
    return "-" if x is None else f"{100 * x:.1f} %"


def _usd(x):
    return "-" if x is None else f"${x:.4f}"


def report_md(rep):
    """Outcome first (lines 2-5 are what `ecadlib eval run` prints), then the wrong parts, then the breakdowns."""
    m, s = rep["meta"], rep["summary"]
    o = s["overall"]
    bad = [r for r in rep["cases"] if not r["pass"]]
    md = [f"# Accuracy: {m['set']} (v{m['set_version']})", "",
          f"**Pass: {o['pass']}/{o['cases']} ({_pct(o['rate'])})**. "
          + (f"{len(bad)} {'part was' if len(bad) == 1 else 'parts were'} wrong: see \"Wrong parts\" below." if bad else "Every part was right."), "",
          f"Model {m['model']}, sources {', '.join(m['sources'])}, mode {m['mode']}. {m['cases']} parts in {m['wall_s']} s "
          f"({m['wall_s_per_part']} s per part); {o['tokens_in']} tokens in / {o['tokens_out']} out; "
          f"{_usd(o['usd'])} in all, {_usd(o['usd_per_part'])} per part.", ""]
    nd = s.get("no_datasheet") or {}
    if nd.get("cases"):
        w = s["with_datasheet"]
        md += [f"{nd['cases']} {'part' if nd['cases'] == 1 else 'parts'} never reached the model (no datasheet PDF could be "
               f"fetched); without them: **{w['pass']}/{w['cases']} ({_pct(w['rate'])})**.", ""]
    if bad:
        md += [f"## Wrong parts ({len(bad)})", ""]
        for r in bad:
            why = "; ".join(r["errors"][:3]) if r["source"] != "none" else "no source had it: " + "; ".join(r["tried"][-2:])
            sh = f" [footprint shared with {r['footprint_shared_with']}]" if r.get("footprint_shared_with") else ""
            md.append(f"- **{r['id']}** (from {r['source']}){sh}: {why}")
        md.append("")
    md += ["## By package family", "", "| Family | Cases | Pass | Rate | Answered by | USD / part |", "|---|---|---|---|---|---|"]
    for k, f in s["families"].items():
        md.append(f"| {k} | {f['cases']} | {f['pass']} | {_pct(f['rate'])} | "
                  + ", ".join(f"{a} {b}" for a, b in f["sources"].items()) + f" | {_usd(f['usd_per_part'])} |")
    co = s.get("consistency")
    if co:
        md += ["", f"**Consistency:** {co['same']}/{co['cases']} parts gave the same files when run again ({co['from']}, "
               f"{co['runs']} runs)" + (": differ " + ", ".join(d["id"] for d in co["differ"][:20]) if co["differ"] else ".")]
    if s.get("cross_check"):
        md += ["", "Cross-checks of datasheet reads: " + ", ".join(f"{k} {v}" for k, v in s["cross_check"].items()) + "."]
    sh = s.get("shared_footprints") or {}
    if sh.get("cases"):
        md += ["", f"{sh['cases']} parts got the footprint file of another part with the same BOM footprint name "
               f"(shared only when the pads are the same); {sh['failing']} of them fail."]
    md += ["", "## By source", "", "| Source | Cases | Pass | Rate |", "|---|---|---|---|"]
    md += [f"| {k} | {f['cases']} | {f['pass']} | {_pct(f['rate'])} |" for k, f in s["by_source"].items()]
    md += ["", "## By field", "", "| Field | OK |", "|---|---|"] + [f"| {k} | {v['ok']}/{v['of']} |" for k, v in s["fields"].items()]
    return "\n".join(md) + "\n"


def _load_report(p):
    p = Path(p)
    return json.loads((p / "report.json" if p.is_dir() else p).read_text(encoding="utf-8"))


def compare(a, b):
    """Two reports (dirs or report.json) -> {families: {fam: (A, B)}, flipped: {...}, text}."""
    A, B = _load_report(a), _load_report(b)
    fa, fb = A["summary"]["families"], B["summary"]["families"]
    fams = {}
    lines = [f"A: {a} ({A['meta']['model']}{' via ' + A['meta']['provider'] if A['meta'].get('provider') else ''}, {A['meta']['mode']}, {A['meta']['cases']} cases)",
             f"B: {b} ({B['meta']['model']}{' via ' + B['meta']['provider'] if B['meta'].get('provider') else ''}, {B['meta']['mode']}, {B['meta']['cases']} cases)", "",
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
