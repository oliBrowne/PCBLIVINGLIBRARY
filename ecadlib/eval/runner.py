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
    return sorted(f.stem for f in SETS.glob("*.json") if not f.stem.endswith((".errata", ".pins")))


def load_errata(set_name):
    """<set>.errata.json next to the set: {mpn: {field: {ours: [[num, name]...], why}}}, the cases where KiCad's
    reference disagrees with the datasheet (checked by hand). {} when there is none."""
    try:
        f = set_path(set_name)
        f = f.with_name(f.stem + ".errata.json")
        return json.loads(f.read_text(encoding="utf-8")).get("cases") or {}
    except (OSError, ValueError):
        return {}


def load_pins(set_name, pins=None):
    """<set>.pins.json (or `pins`): {mpn: {url, sha256, pages}}, the datasheet each case was benchmarked on. A later run
    whose PDF has another SHA-256 is flagged (the maker changed the datasheet). {} when there is none."""
    try:
        f = Path(pins) if pins else set_path(set_name).with_name(set_path(set_name).stem + ".pins.json")
        return json.loads(f.read_text(encoding="utf-8")).get("cases") or {}
    except (OSError, ValueError):
        return {}


def pin_state(ds, pinned):
    """This run's datasheet against the pinned one -> same | changed | new | url (sent as a link: not downloaded here,
    so it cannot be pinned) | "" (no datasheet read)."""
    if not ds:
        return ""
    if not ds.get("sha256"):
        return "url" if ds.get("url") else ""
    if not pinned:
        return "new"
    return "same" if pinned.get("sha256") == ds["sha256"] else "changed"


def write_pins(report, out):
    """The datasheets a report read (URL, SHA-256, pages) as a pins file: commit it next to the set."""
    rep = _load_report(report)
    cases = {r["mpn"]: {k: r["datasheet"][k] for k in ("url", "sha256", "bytes", "pages", "local") if r["datasheet"].get(k)}
             for r in rep["cases"] if (r.get("datasheet") or {}).get("sha256")}
    doc = {"about": "The datasheet PDF each case of the set was benchmarked on: its URL and SHA-256. `ecadlib eval run` "
                    "flags a part whose PDF has changed since (datasheet_pin = changed). Written by `ecadlib eval pin`.",
           "set": rep["meta"]["set"], "from_run": rep["meta"].get("started"), "cases": dict(sorted(cases.items()))}
    Path(out).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return len(cases)


PIN_WORDS = {"same": "the same PDF", "changed": "CHANGED since pinned", "new": "not pinned yet",
             "url": "sent as a link (not downloaded, so not pinned)"}


def publish(report, out, title=None):
    """A results page from a report: the headline in one line (model, parts, pass, pad match, $ per part), then the
    report. Commit it, or put it on the site."""
    rep = _load_report(report)
    m, s = rep["meta"], rep["summary"]
    o, pm = s["overall"], (s.get("pad_match") or {}).get("built") or {}
    ds = o.get("right", o["pass"]) - o["pass"]
    hl = s.get("headline") or {}
    head = (f"{m['model']}, {o['cases']} real parts: {o.get('right', o['pass'])} right ({_pct(o.get('right_rate', o['rate']))})"
            + (f", {o['pass']} of them matching KiCad exactly and {ds} the datasheet where KiCad differs" if ds else "")
            + (f"; of KiCad's matches {hl['from_datasheet']} read from a datasheet, {hl['from_standard']} from the "
               "standard-package table" if hl.get("right") else "")
            + (f", pad match {_pct(pm['rate'])} over {pm['parts']} footprints built" if pm.get("of") else "")
            + (f", {_usd(o['usd_per_part'])} per part" if o.get("usd") else "")
            + (f". {o['cases'] - o['pass'] - ds} failures, by type: " + ", ".join(f"{k} {len(v)}" for k, v in s["failures"].items()
                                                                               if k not in DATASHEET_RIGHT)
               if s.get("failures") else "") + ".")
    body = report_md(rep).splitlines()
    md = [f"# {title or 'Accuracy benchmark results'}", "", f"**{head}**", "",
          f"Run {m.get('started', '')}, set {m['set']} v{m['set_version']}, mode {m['mode']}. Method and what counts as a "
          "miss: [benchmark.md](benchmark.md). Generated by `ecadlib eval publish` from the run's report.json.", ""]
    md += body[2:]
    Path(out).write_text("\n".join(md) + "\n", encoding="utf-8")
    return head


# Why a part failed, in the words of the benchmark page. A misread and a made-up number look the same to the scorer
# (both are a dimension that is not the drawing's); a person tells them apart from the part's review page.
FAILURE_TYPES = {
    "no_pdf": "no datasheet PDF (no link, a dead link, or the maker's site refused the download)",
    "not_read": "not read in this run (--cached-only, or waiting for the cost OK)",
    "no_key": "not run: no API key, so the AI datasheet read was never sent (not a wrong answer)",
    "blank_dimension": "the read left a needed dimension blank: held, nothing built",
    "held_cross_check": "held: the cross-check's second read disagreed",
    "no_footprint": "no footprint for another reason (see tried)",
    "wrong_unit": "a unit mistake (mil / inch read as mm, or the reverse)",
    "wrong_variant": "the wrong package or variant (pin count or pitch differs)",
    "mirrored": "pad 1 in the wrong place: mirrored or rotated",
    "misread_table": "a pad table misread (pads in the wrong place or size)",
    "thermal_pad": "only the exposed / thermal pad differs",
    "misread_dimension": "a dimension misread or made up (pads or courtyard off)",
    "maker_land": "follows the datasheet's own land pattern, which KiCad's differs from",
    "datasheet_body": "only the courtyard differs: ours is around the datasheet's body, KiCad's around a nominal one",
    "symbol": "the footprint is right; the symbol's pins or names differ",
}


def failure_type(row, mine, spec):
    """One FAILURE_TYPES key for a failed case (None when it passed)."""
    if row["pass"]:
        return None
    tried = " | ".join(str(t) for t in row.get("tried") or [])
    if not_run(row):
        return "no_key"
    if row["source"] == "none":
        if no_datasheet(row):
            return "no_pdf"
        if row.get("cross_check") == "disagreed" or "cross-check disagrees" in tried:
            return "held_cross_check"
        if re.search(r"unresolved|left .* blank|no footprint: the datasheet read left", tried):
            return "blank_dimension"
        if re.search(r"not sent \(--cached-only|waiting for your OK|needs datasheet reader", tried):
            return "not_read"
        return "no_footprint"
    if row.get("kicad_differs_maker_land"):
        return "maker_land"
    if row.get("kicad_differs_body"):
        return "datasheet_body"
    f = row["fields"]
    failed = {k for k, v in f.items() if not v["ok"] and k not in S.INFO_ONLY}
    if failed <= {"symbol_pins", "symbol_names"}:            # SM6T100A (10-19): the PDF never reached the model
        return "no_pdf" if any(NO_DATASHEET.search(str(t)) for t in row.get("tried") or []) else "symbol"
    pa, pb = (f.get("pitch") or {}).get("ours"), (f.get("pitch") or {}).get("ref")
    if "pitch" in failed and pa and pb and any(abs(pa / pb - r) < 0.03 * r for r in (25.4, 1 / 25.4, 39.37, 1 / 39.37)):
        return "wrong_unit"
    if failed & {"pad_count", "pitch"}:
        return "wrong_variant"
    if "pin1" in failed:
        return "mirrored"
    bad = set((f.get("pads") or {}).get("bad_pads") or [])
    if failed <= {"pads", "courtyard"} and bad and bad <= set(S.ep_numbers(mine)):
        return "thermal_pad"
    if (spec or {}).get("pad_table") and failed & {"pads", "pin1"}:
        return "misread_table"
    return "misread_dimension"


def apply_errata(sc, mine_pins, errata, ref_pins=None, tol=None):
    """A failing field that a checked erratum covers, with ours what the datasheet says, is scored right. Only an
    erratum that says what the datasheet has can be checked against our part: "ours": [[num, name], ...] on a symbol
    field, or a number ("pitch": {"ours": 0.65}) that ours must match before the part's footprint fields are excused.
    One with neither would pass whatever we generated, so it is not applied."""
    fixed = []
    num = {k: e["ours"] for k, e in (errata or {}).items() if isinstance(e.get("ours"), (int, float))}
    if any(abs(((sc["fields"].get(k) or {}).get("ours") or 0) - v) > 0.01 for k, v in num.items()):
        return fixed                                  # ours isn't the datasheet's package either (PCA9557BS: pitch 0.65)
    for field, e in (errata or {}).items():
        f = sc["fields"].get(field)
        if not f or f["ok"]:
            continue
        pins = field.startswith("symbol_") and isinstance(e, dict) and isinstance(e.get("ours"), list) and e["ours"]
        if not pins and not num:                      # nothing to compare ours with: the field keeps its verdict
            continue                                  # (a footprint erratum is checked by its numbers, above)
        if pins:
            have = {str(n): S.norm_name(v) for n, v in mine_pins or []}
            if any(have.get(str(n)) != S.norm_name(v) for n, v in e["ours"]):
                continue                              # ours is not what the datasheet says either: a real miss
            if ref_pins:                              # the pins the erratum does not cover must still agree with KiCad
                fixed_ref = dict((str(n), v) for n, v in ref_pins)
                fixed_ref.update((str(n), v) for n, v in e["ours"])
                if not S.score_symbol(mine_pins, list(fixed_ref.items()), tol).get(field, {"ok": True})["ok"]:
                    continue
        sc["fields"][field] = dict(f, ok=True, error="", erratum=e["why"])
        fixed.append(field)
    if fixed:
        sc["pass"] = S.verdict(sc["fields"])
        sc["errors"] = [x for x in sc["errors"] if x.split(":")[0] not in fixed]
    return fixed


def reference_for(case, errata):
    """The case's KiCad reference footprint, or the one its erratum names when KiCad's is another variant of the package
    (PCA9557BS: the set's 3 x 3 mm HVQFN-16 for a 4 x 4 mm SOT629-1 part). Every field is then scored against it."""
    return ((errata.get(case["mpn"]) or {}).get("reference") or {}).get("footprint") or case["footprint"]


def reference_numbers(case, errata, refs):
    """The numbers the case is scored against: its reference footprint's, with the pads renumbered where the erratum
    says the datasheet numbers them otherwise ("reference": {"renumber": {"2": "3", "3": "2"}}: AH1806-W, whose SC59
    has pin 2 alone on one side where KiCad's SOT-23 numbering puts pin 3)."""
    ref = refs[reference_for(case, errata)]
    m = ((errata.get(case["mpn"]) or {}).get("reference") or {}).get("renumber")
    if not m:
        return ref
    return dict(ref, pads=[[m.get(str(p[0]), p[0])] + list(p[1:]) for p in ref["pads"]])


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
DESIGNATOR = {"Resistor_SMD": "R", "Capacitor_SMD": "C", "Inductor_SMD": "L", "LED_SMD": "D", "Diode_SMD": "D",
              "Diode_THT": "D"}                     # a BOM names its diodes D, through-hole ones too


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



def saved_prov(prov, offset):
    """The PDF record of a saved answer scored in place of a step never sent: its own pages, not the pages this run's
    picker would send. TLC5951DAP's 10-21 answer read pages 4,5,15,39,49,50; stamped with today's pick (4,5,39,49-51) its
    sidecar named a land page (51) the scored read never saw. The zoom pages and why each page was picked describe
    today's request too, so they go."""
    p = {k: v for k, v in (prov or {}).items() if k not in ("zoom_pages", "picked_why", "pages_sent", "note")}
    if isinstance(offset, list) and offset:
        p.update(pages_sent=list(offset), note=f"{len(offset)} pages sent by the saved read ({D._ranges(offset)}); "
                                               "this run's page picking was not used")
    else:
        p["note"] = ("the saved read records no page list (the whole PDF, or a batch answer that kept none)" if not offset
                     else f"the saved read was sent the pages from {offset + 1}")
    return p

class EvalReader(D.Reader):
    """datasheet.Reader that remembers what each part cost, and can record answers or replay them."""

    def __init__(self, *a, record=None, replay=None, cached_only=False, **kw):
        super().__init__(*a, **kw)
        self.record, self.replay = (Path(record) if record else None), (Path(replay) if replay else None)
        self.cached_only = cached_only
        self.results = D.ResultCache(self.out / "cache")          # per eval run: no answer leaks in from elsewhere
        self.used = {}
        if self.replay:
            self.key, self.estimate_only = "replay (never sent)", False
            self.offline = False                                      # recorded answers: nothing goes to a model
        elif not self.key:
            self.estimate_only = True

    def _estimate(self, job):
        k, self.key = self.key, None                              # datasheet's own estimate, without count_tokens
        try:
            r = self.estimate([job], batch=False)["rows"][0]
        finally:
            self.key = k
        return {"input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"], "usd": r["usd"], "how": r["how"]}

    def job(self, part, urls, package=None, pages=None, name=None, fp_needed=None, mpn=None, maker=None):
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
            job = super().job(part, urls, package, pages, name, fp_needed, mpn, maker)
        job["estimate"] = (job.get("recording") or {}).get("estimate") or self._estimate(job)
        self.used.setdefault(part, {"estimate": job["estimate"]})
        pv = job.get("prov") or {}                     # the PDF read: pinned by its SHA-256 (<set>.pins.json)
        self.used[part]["datasheet"] = {k: pv.get(k) for k in ("url", "sha256", "bytes", "pages", "sent_as", "via", "local") if pv.get(k)}
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
        elif self.cached_only and "message" not in (self.results.get_check(key) or {}):
            # never "unchecked": unchecked passes, so a part the live run held could pass for free (review 2026-10-20 #5)
            raise D.DatasheetError("not sent (--cached-only: saved answers only): the cross-check's second read was not "
                                   "saved, so the part is not scored; a live run reads it")
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

    def recollect(self, jobs=()):
        return 0 if self.replay else super().recollect(jobs)

    def _flush_round(self, jobs):
        """--cached-only: a step the route now asks for that was never sent (an answer the old parser rejected took
        the route another way) is scored from the part's best saved answer for the same datasheet instead."""
        if not self.cached_only:
            return super()._flush_round(jobs)
        self.recollect(jobs)
        out, rest = {}, []
        for j in jobs:
            if "message" in (self.results.get(j["key"]) or {}):
                rest.append(j)
                continue
            try:
                out[j["key"]] = self._from_saved(j)
            except D.DatasheetError as e:
                out[j["key"]] = e
        if rest:
            out.update(super()._flush_round(rest))
        return out

    def _from_saved(self, j):
        rec = self._saved_answer(j)
        if rec is None:
            raise D.DatasheetError("not sent (--cached-only: saved answers only); a live run reads it")
        self.used.setdefault(j["part"], {"estimate": j.get("estimate")})["via"] = "cached (another step of its route)"
        off = rec.get("page_offset", j["offset"])
        return self._result(dict(j, final=True, offset=off, prov=saved_prov(j.get("prov"), off)), rec)

    def _saved_answer(self, job):
        """The part's saved answer for the same datasheet (any step of its route) that does best: ok first, then one
        that reads, then the latest. None when there is none."""
        best = None
        for f in sorted(self.results.dir.glob(f"{job['key'][:16]}-*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("part") != job["part"] or "message" not in rec or rec.get("purpose"):
                continue
            try:
                kind = self._judge(dict(job, offset=rec.get("page_offset", job["offset"])), rec)[0] if "rung" in job else "ok"
            except D.DatasheetError:
                continue
            rank = (kind == "ok", kind != "failed", str(rec.get("at") or ""))
            if best is None or rank > best[0]:
                best = (rank, rec)
        return best and best[1]

    def _guard_cost(self, jobs):
        if self.cached_only and jobs:
            raise D.DatasheetError("not sent (--cached-only: saved answers only); a live run reads it")
        return super()._guard_cost(jobs)

    def read_now(self, job, _guarded=False):
        if self.replay:
            raise D.DatasheetError(f"no recorded response for {job['part']}")
        if self.cached_only and "message" not in (self.results.get(job["key"]) or {}):
            return self._from_saved(job)              # a step never sent, or saved under an older schema's key
        t0 = time.time()
        try:
            return super().read_now(job, _guarded)
        finally:
            self.used[job["part"]]["api_s"] = round(time.time() - t0, 2)

    def _result(self, job, rec):
        msg = rec["message"]
        u = self.used.setdefault(job["part"], {"estimate": job.get("estimate")})
        u.setdefault("via", rec.get("via") or "live")
        if (job.get("prov") or {}).get("url"):        # the link read: the next one when the API could not fetch the first
            u.setdefault("datasheet", {})["url"] = job["prov"]["url"]
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
        k = str(rec.get("key") or "")
        if k.count("-") >= 3 and not k.startswith(("replay-", "check-")):  # <pdf>-<model>-<prompt version>-<request>
            u["prompt_version"] = k.rsplit("-", 2)[1]
        res = super()._result(job, rec)
        u["spec"] = res["spec"]                       # the package as read: its land is scored as the maker's (S.maker_land)
        return res


def _cc_version():
    from .. import crosscheck as CC
    return CC.version()


def _symbol_pins(path):
    try:
        _, syms = AL.read_schlib(path)
    except (OSError, ValueError):
        return None
    pins = {}                                         # num -> names: one pin repeated in each part of a multi-part
    for s in syms:                                    # symbol is one pin; one number with two names is scored as such
        for r in s["records"]:
            if r.get("RECORD") == "2" and "num" in r:
                got = pins.setdefault(r["num"], [])
                if r["name"] not in got:
                    got.append(r["name"])
    return [(n, nm) for n, names in pins.items() for nm in names]


def _cost(case_used, model, live=False):
    """-> tokens_in, tokens_out, usd, how. live: the run could send, so a part with no answer cost nothing (a request
    the API refused or errored is not billed, and one never sent costs nothing); its estimate is only mentioned."""
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
    if live:
        return 0, 0, 0.0, "no answer: nothing billed" + (f" (estimate was ${est['usd']:.2f})" if est.get("usd") else "")
    return est.get("input_tokens", 0), est.get("output_tokens", 0), est.get("usd"), f"estimate ({est.get('how', '')})"


# The part never reached the model: no datasheet link, or none gave a PDF (here or to the API's fetcher). The benchmark
# reports these apart: a dead maker link is not a misread drawing.
NO_DATASHEET = re.compile(r"no datasheet (link gave a PDF|URL known)|could not (be )?(fetch|download)|unable to (fetch|download)"
                          r"|timed out while trying to download|unable to connect to the remote server", re.I)


def no_datasheet(row):
    return row["source"] == "none" and any(NO_DATASHEET.search(str(t)) for t in row.get("tried") or [])


DATASHEET_RIGHT = ("maker_land", "datasheet_body")      # failure types that are right by the datasheet


def datasheet_right(row):
    """Wrong by KiCad but right by the datasheet (Oliver 2026-10-21: the maker's datasheet is the truth, KiCad the
    fallback): the maker's recommended land, or a courtyard around the datasheet's real body."""
    return not row["pass"] and bool(row.get("kicad_differs_maker_land") or row.get("kicad_differs_body"))


def not_run(row):
    """Estimate mode (no API key): the part needed the AI datasheet read, which was never sent. Not a wrong answer."""
    return bool(row.get("not_run"))


def summarize(rows):
    def agg(rs):
        n = len(rs)
        p = sum(r["pass"] for r in rs)
        ok = p + sum(datasheet_right(r) for r in rs)
        cost = [r["usd"] for r in rs if r["usd"]]
        return {"cases": n, "pass": p, "rate": round(p / n, 4) if n else None,
                "right": ok, "right_rate": round(ok / n, 4) if n else None,
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
    local = [r for r in rows if (r.get("datasheet") or {}).get("local")]   # read from --datasheets, not a maker's link
    skipped = [r for r in rows if not_run(r)]
    given = [r for r in rows if r.get("maker_land")]
    maker = {"given": len(given), "followed": sum(r["maker_land"]["followed"] for r in given),
             "kicad_differs": [r["id"] for r in rows if r.get("kicad_differs_maker_land")],
             "body_differs": [r["id"] for r in rows if r.get("kicad_differs_body") and not r.get("kicad_differs_maker_land")]}
    built = [r for r in rows if r.get("pads_of") and r["source"] != "none"]
    pad = lambda rs: {"right": sum(r.get("pads_right", 0) for r in rs), "of": sum(r.get("pads_of", 0) for r in rs),
                      "parts": len(rs)}
    pm = {"built": pad(built), "all": pad([r for r in rows if r.get("pads_of")])}
    for v in pm.values():
        v["rate"] = round(v["right"] / v["of"], 4) if v["of"] else None
    fails = {}
    for r in rows:
        if r.get("failure"):
            fails.setdefault(r["failure"], []).append(r["id"])
    pins = {}
    for r in rows:
        if r.get("datasheet_pin"):
            pins[r["datasheet_pin"]] = pins.get(r["datasheet_pin"], 0) + 1
    pv = {}
    for r in rows:
        if r.get("prompt_version"):
            pv[r["prompt_version"]] = pv.get(r["prompt_version"], 0) + 1
    right = [r for r in rows if r["pass"]]            # the standard table is KiCad's own pad data: it matches by construction
    head = {"right": len(right), "from_datasheet": sum(r["source"] == "datasheet" for r in right),
            "from_standard": sum(r["source"] == "standard" for r in right),
            "datasheet_built": sum(r["source"] == "datasheet" for r in rows),
            "unchecked_passes": sum(r.get("cross_check") == "unchecked" for r in right)}
    head["other"] = head["right"] - head["from_datasheet"] - head["from_standard"]
    return {"cross_check": dict(sorted(cc.items())), "overall": agg(rows), "maker_land": maker, "pad_match": pm, "headline": head,
            "failures": dict(sorted(fails.items(), key=lambda kv: -len(kv[1]))), "datasheet_pins": dict(sorted(pins.items())),
            "answer_prompt_versions": pv,
            "errata": {r["id"]: r["errata"] for r in rows if r.get("errata")},
            "no_datasheet": {"cases": len(unread), "ids": [r["id"] for r in unread]},
            "not_run": {"cases": len(skipped), "ids": [r["id"] for r in skipped]},
            "hand_downloaded": {**agg(local), "ids": [r["id"] for r in local]},
            "ran": agg([r for r in rows if not not_run(r)]),
            "with_datasheet": agg([r for r in rows if not no_datasheet(r)]), "shared_footprints": {"cases": len(shared), "failing": sum(not r["pass"] for r in shared)}, "families": {k: agg(v) for k, v in sorted(fams.items())},
            "by_source": {k: agg(v) for k, v in sorted(srcs.items())},
            "fields": {k: {"ok": a, "of": b} for k, (a, b) in sorted(fields.items())}}


def run(set_name="kicad-v1", out="out/eval/run", limit=None, model=None, record=None, replay=None, sources=None,
        families=None, ids=None, offline=False, cache="out/http_cache", http=None, api_key=None, rules=None, providers=None,
        tol=None, log=print, provider=None, base_url=None, assist=None, api_key_env=None, repeat=1, batch=False,
        cached_only=False, pins=None, max_cost_usd=None, datasheets=None):
    """repeat: run the same cases again (into <out>/repeat-2 ...) and report how many parts gave the same files
    (summary["consistency"]): from the recorded answers when there are any (--record / --replay: the code must be
    deterministic), else live again (the model's own consistency; this pays again).
    provider / base_url / assist / api_key_env: the [ai] settings for this run (ecadlib.llm), so models from any
    provider can be compared on the same set: e.g. provider="ollama", model="qwen2.5:7b" against the Claude default.
    batch: the datasheet reads go out as one Message Batch (half price; the run waits for it).
    cached_only: re-score a live run's out folder with nothing sent: its cached answers, and the answers of its
    collected batches downloaded again (free), are read by this code; a part that needs another read is reported.
    datasheets: a folder of PDFs downloaded by hand, read before any link ([datasheet] folder)."""
    data = load_set(set_name)
    cases = select(data["cases"], families, ids, limit)
    if not cases:
        raise ValueError("no cases selected")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lib = out / "lib"
    rules = RU.resolve(rules, out)
    # a benchmark never waits on a person's browser: a refused link stays refused unless a way that needs no one works
    rules = dict(rules, datasheet={**rules.get("datasheet", {}), "handoff": "off"})
    if max_cost_usd is not None:                      # the run's spending cap ([datasheet] max_cost_usd, default $10)
        rules = dict(rules, datasheet={**rules.get("datasheet", {}), "max_cost_usd": float(max_cost_usd)},
                     _set=[*(rules.get("_set") or []), "datasheet.max_cost_usd"])     # a cap you set, 10 too
    over = {k: v for k, v in (("provider", provider), ("base_url", base_url), ("assist", assist),
                              ("api_key_env", api_key_env)) if v}
    if over or (model and provider):
        rules = dict(rules, ai={**rules.get("ai", {}), **over, **({"model": model} if model else {})},
                     datasheet={**rules.get("datasheet", {}), **({k: "" for k in ("provider", "base_url", "assist")} if over else {}),
                                **({"api_key_env": ""} if provider and provider != "anthropic" else {})})
        RU.validate({k: v for k, v in rules.items() if k in ("ai",)}, "eval --provider/--model")
    if datasheets:
        rules = dict(rules, datasheet={**rules.get("datasheet", {}), "folder": str(datasheets)})
    sources = list(sources) if sources else [s for s in rules["sources"]["order"] if s != "kicad"]
    if "kicad" in sources:
        log("  note: `kicad` is the ground truth of this set; parts it resolves are scored against themselves")
    bom = write_bom(cases, out / "bom.csv")
    if http is None:
        from ..httpcache import CachedHTTP
        http = CachedHTTP(cache, min_interval=0.5, offline=offline, timeout=60)
    reader = EvalReader(lib, cache, api_key, rules, http, model=model, record=record, replay=replay, log=lambda *a: None,
                        batch=bool(batch and not replay), cached_only=bool(cached_only and not replay))
    mode = "replay" if replay else "estimate (no API key: nothing sent)" if reader.estimate_only else \
        "cached answers only (nothing sent)" if reader.cached_only else \
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
    rows, erratas, pinned = [], load_errata(set_name), load_pins(set_name, pins)
    for c in cases:
        it = by_mpn.get(c["mpn"]) or {}
        fp = (it.get("footprint") or {}).get("kicad_mod")
        mine = FB.numbers((lib / fp).read_text(encoding="utf-8")) if fp and (lib / fp).exists() else None
        sf = (it.get("symbol") or {}).get("file")
        sym_pins = _symbol_pins(lib / sf) if sf and (lib / sf).exists() else None
        ref_fp = reference_for(c, erratas)
        ref = reference_numbers(c, erratas, data["references"])
        sc = S.score(mine, ref, sym_pins, c.get("pins"), tol)
        errata = apply_errata(sc, sym_pins, erratas.get(c["mpn"]), c.get("pins"), tol)
        tin, tout, cost, how = _cost(reader.used.get(c["mpn"]), reader.model, not reader.estimate_only)
        src = ((it.get("source") or {}).get("footprint") or "none").split(":")[0]
        used = reader.used.get(c["mpn"]) or {}
        ml = S.maker_land(mine, used.get("spec")) if src == "datasheet" else None
        ref_pads = len(S.drop_vias(ref)["pads"])
        shared = next((n[len("shared with "):] for n in (it.get("librarian") or {}).get("notes", []) if n.startswith("shared with ")), "")
        files = {}                                    # what this run wrote for the part (consistency between runs)
        for kind, rel in (("kicad_mod", fp), ("pcblib", (it.get("footprint") or {}).get("file")), ("schlib", sf)):
            if rel and (lib / rel).exists():
                files[kind] = file_fingerprint(lib / rel)
        rows.append({"id": c["id"], "family": c["family"], "mpn": c["mpn"], "footprint": ref_fp, "pass": sc["pass"],
                     "files": files, "cross_check": (it.get("cross_check") or {}).get("status", ""),
                     "source": src, "symbol_source": ((it.get("source") or {}).get("symbol") or "none").split(":")[0],
                     "errors": sc["errors"], "fields": sc["fields"], "maker_land": ml, "errata": errata,
                     "kicad_differs_maker_land": not sc["pass"] and S.only_maker_land(
                         sc["fields"], ml, S.body_evidence(used.get("spec"), ref_fp, S.drop_vias(ref))),
                     "kicad_differs_body": bool(not sc["pass"] and src == "datasheet"
                                                and S.datasheet_body(sc["fields"], S.drop_vias(mine) if mine else None, used.get("spec"),
                                                                     ref_fp, S.drop_vias(ref))),
                     "tokens_in": tin, "tokens_out": tout, "usd": cost,
                     "cost_how": how, "footprint_shared_with": shared, "api_s": (reader.used.get(c["mpn"]) or {}).get("api_s"),
                     "tried": ((it.get("librarian") or {}).get("tried") or []),
                     "pads_right": (sc["fields"].get("pads") or {}).get("right", 0), "pads_of": ref_pads,
                     "datasheet": used.get("datasheet") or {}, "prompt_version": used.get("prompt_version", ""),
                     "datasheet_pin": pin_state(used.get("datasheet"), pinned.get(c["mpn"])),
                     "not_run": bool(reader.estimate_only and not sc["pass"] and src == "none" and c["mpn"] in reader.used
                                     and not (reader.used[c["mpn"]] or {}).get("usage"))})
        rows[-1]["failure"] = failure_type(rows[-1], mine, used.get("spec") if src == "datasheet" else None)
    rep = {"meta": {"set": data["meta"]["name"], "set_version": data["meta"].get("version"), "set_built": data["meta"].get("built"),
                    "model": reader.model, "provider": inf["ep"]["name"], "tier": inf["plan"]["tier"],
                    "rails": inf["plan"]["rails"], "sources": sources, "mode": mode,
                    "prompt_version": D.schema_version(), "crosscheck_version": _cc_version(), "record": str(record or ""), "replay": str(replay or ""),
                    "tolerances": dict(S.TOL, **(tol or {})), "cases": len(rows),
                    "started": datetime.datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
                    "wall_s": round(wall, 1), "wall_s_per_part": round(wall / len(rows), 3)},
           "summary": summarize(rows), "cases": rows}
    for i in range(2, max(1, int(repeat or 1)) + 1):
        again = run(set_name, out / f"repeat-{i}", limit, model, None, replay or record, sources, families, ids, offline, cache,
                    http, api_key, rules, providers, tol, log, provider, base_url, assist, api_key_env, batch=batch,
                    cached_only=cached_only, pins=pins, max_cost_usd=max_cost_usd)
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
    bad = [r for r in rep["cases"] if not r["pass"] and not not_run(r) and not datasheet_right(r)]
    dsr = [r for r in rep["cases"] if datasheet_right(r)]
    pm = (s.get("pad_match") or {}).get("built") or {}
    pad = (f"Pad match {_pct(pm['rate'])} ({pm['right']} of KiCad's {pm['of']} pads, over the {pm['parts']} footprints "
           "built). " if pm.get("of") else "")
    nr = (s.get("not_run") or {}).get("cases", 0)
    if nr:                                            # no API key: those parts were never tried, so they aren't "wrong"
        ran = s["ran"]
        head = (f"**Not run: {nr} of {o['cases']} parts need the AI datasheet read and no API key is set, so nothing "
                f"was sent.** Of the {ran['cases']} that ran without AI: **{ran['pass']}/{ran['cases']} pass**. " + pad
                + (f"{len(bad)} wrong: see \"Wrong parts\" below." if bad else ""))
        cost = f"the AI reads would cost about {_usd(o['usd'])} ({_usd(o['usd_per_part'])} per part; estimate, nothing billed)."
    else:
        right = o.get("right", o["pass"])
        head = (f"**Right: {right}/{o['cases']} ({_pct(o.get('right_rate', o['rate']))})**"
                + (f": {o['pass']} match KiCad exactly and {len(dsr)} match the datasheet where KiCad differs (see "
                   "\"Right by the datasheet\")" if dsr else "") + ". " + pad
                + (f"{len(bad)} {'part was' if len(bad) == 1 else 'parts were'} wrong: see \"Wrong parts\" below." if bad else "Every part was right."))
        cost = f"{_usd(o['usd'])} in all, {_usd(o['usd_per_part'])} per part."
    hl = s.get("headline") or {}
    if hl.get("right"):
        head += (f" Of the {hl['right']} that match KiCad: {hl['from_datasheet']} read from a datasheet, {hl['from_standard']} from the "
                 "standard-package table (KiCad's own pad data, so those match KiCad by construction)"
                 + (f", {hl['other']} from other sources" if hl.get("other") else "")
                 + f". {hl['from_datasheet']} of the {hl['datasheet_built']} footprints built from a datasheet match KiCad.")
        if hl.get("unchecked_passes"):
            head += (f" {hl['unchecked_passes']} of the passes had no second read (unchecked): the cross-check could not "
                     "be made, so nothing confirmed those reads.")
    md = [f"# Accuracy: {m['set']} (v{m['set_version']})", "", head.rstrip(), "",
          f"Model {m['model']}, sources {', '.join(m['sources'])}, mode {m['mode']}. {m['cases']} parts in {m['wall_s']} s "
          f"({m['wall_s_per_part']} s per part); {o['tokens_in']} tokens in / {o['tokens_out']} out; " + cost, ""]
    if m.get("prompt_version"):
        old = {k: v for k, v in (s.get("answer_prompt_versions") or {}).items() if k != m["prompt_version"]}
        md += [f"Prompt version {m['prompt_version']} (reader schema and instructions), cross-check {m.get('crosscheck_version', '-')}"
               + (f"; {sum(old.values())} answers were saved under an earlier version ({', '.join(old)}) and scored by "
                  "this code" if old else "") + ".", ""]
    dp = s.get("datasheet_pins") or {}
    if dp:
        md += ["Datasheets against the pinned PDFs (the set's pins file): "
               + ", ".join(f"{v} {PIN_WORDS.get(k, k)}" for k, v in dp.items()) + "."
               + (" A changed PDF is scored as read; compare it before trusting a change in the result." if dp.get("changed") else ""), ""]
    if s.get("failures"):
        md += ["## Why parts failed", "", "| Type | Parts | What it means |", "|---|---|---|"]
        md += [f"| {k} | {len(v)} | {FAILURE_TYPES.get(k, k)} |" for k, v in s["failures"].items()]
        md.append("")
    nd = s.get("no_datasheet") or {}
    if nd.get("cases"):
        w = s["with_datasheet"]
        md += [f"{nd['cases']} {'part' if nd['cases'] == 1 else 'parts'} never reached the model (no datasheet PDF could be "
               f"fetched); without them: **{w['pass']}/{w['cases']} ({_pct(w['rate'])})**.", ""]
    hd = s.get("hand_downloaded") or {}
    if hd.get("cases"):                               # counted as the headline is: right, not only matching KiCad
        hr, orr = hd.get("right", hd["pass"]), o.get("right", o["pass"])
        md += [f"{hd['cases']} {'part was' if hd['cases'] == 1 else 'parts were'} read from PDFs downloaded by hand "
               f"(--datasheets), not from a maker's link: {hr}/{hd['cases']} of them right. Without them: "
               f"**{orr - hr}/{o['cases'] - hd['cases']}"
               + (f" ({_pct(round((orr - hr) / (o['cases'] - hd['cases']), 4))})" if o['cases'] > hd['cases'] else "")
               + "**.", ""]
    if s.get("errata"):
        md += [f"{len(s['errata'])} {'part is' if len(s['errata']) == 1 else 'parts are'} scored right where KiCad's reference "
               "disagrees with the datasheet (checked by hand, see the set's errata file): "
               + ", ".join(f"{k} ({', '.join(v)})" for k, v in s["errata"].items()) + ".", ""]
    if dsr:
        md += [f"## Right by the datasheet ({len(dsr)})", "",
               "Counted as right: the maker's datasheet is the truth, KiCad's library the fallback. These differ from "
               "KiCad only where KiCad follows its own convention.", ""]
        for r in dsr:
            why = ("the datasheet's recommended land; KiCad works its land out from the package dimensions"
                   if r.get("kicad_differs_maker_land") else
                   "courtyard around the datasheet's body; KiCad's is around a nominal body for the pin count")
            md.append(f"- **{r['id']}**: {why} ({'; '.join(r['errors'][:2])})")
        md.append("")
    if bad:
        md += [f"## Wrong parts ({len(bad)})", ""]
        for r in bad:
            why = "; ".join(r["errors"][:3]) if r["source"] != "none" else "no source had it: " + "; ".join(r["tried"][-2:])
            sh = f" [footprint shared with {r['footprint_shared_with']}]" if r.get("footprint_shared_with") else ""
            ft = f" [{r['failure']}]" if r.get("failure") else ""
            md.append(f"- **{r['id']}**{ft} (from {r['source']}){sh}: {why}")
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
