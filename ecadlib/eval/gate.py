"""Accuracy gate for CI: the 331-case eval set, offline, against a committed baseline (`ecadlib eval gate`).

    ecadlib eval gate                    run and check against ecadlib/eval/baselines/kicad-v1.json (exit 1 on a drop)
    ecadlib eval gate --update           run and rewrite the baseline (commit it: a deliberate change is one reviewed diff)
    ecadlib eval gate --make-replay      rebuild the committed replay pack (ecadlib/eval/replay/kicad-v1.json.gz)

What runs: `eval run` with sources `standard,datasheet`, no network at all (every HTTP request is refused and counted)
and the datasheet step answered from the replay pack. The pack is SYNTHETIC, not a model's reading: for every case it
holds the answer a perfect reader would give, in the Messages API shape the reader validates (strict output schema):
  * packages in the standard table (ecadlib/package_specs/standard.json.gz: gullwing, no-lead, QFP, chip) as printed
    dimensions (min / nom / max in mm, labelled like a JEDEC drawing): the IPC-7351B generator builds the land;
  * every other package (BGA, connectors, THT, power, hand-drawn standard parts) as the land-pattern table (pad_table)
    copied from the case's reference pads, with the courtyard box as the body outline;
  * pins: the case's pin numbers and names.
So the gate measures the pipeline (reader checks, spec conversion, footprint generator, pad tables, symbol writer,
librarian, scorer), not a model. A real model's accuracy is `eval run --record` / `--replay` with a key.

Fails when (a) a family's pass rate drops more than --tol (default 0.01) below the baseline, or (b) a case that passes
in the baseline now fails. New passes are reported; `--update` locks them in."""
import argparse, copy, datetime, gzip, json, sys, tempfile, time
from pathlib import Path

from .. import rules as RU
from . import runner as R

HERE = Path(__file__).parent
BASELINES, REPLAYS = HERE / "baselines", HERE / "replay"
SOURCES = ["standard", "datasheet"]
MODEL = "claude-opus-5-5"            # the model named in the synthetic messages (the default; frontier tier, no extra rails)
LABELS = {"pitch": "e", "body_length": "D", "body_width": "E1", "lead_span": "E", "terminal_length": "L",
          "terminal_width": "b", "height_max": "A", "inner_span": "E3", "pitch_y": "e1", "ball_diameter": "b",
          "land_diameter": "b1", "drill": "d", "lead_span_y": "D1", "courtyard_excess": "CE"}
TYPED = ("gullwing", "qfn", "qfp", "chip")


def baseline_path(set_name="kicad-v1"):
    return BASELINES / f"{Path(set_name).stem}.json"


def replay_path(set_name="kicad-v1"):
    return REPLAYS / f"{Path(set_name).stem}.json.gz"


# ---------------------------------------------------------------- synthetic answers
def _complete(v, sch):
    """Fill every property strict structured outputs would return (null when the schema allows it), recursively."""
    alts = sch.get("anyOf")
    if alts:
        if v is None:
            return None
        for a in alts:
            if a.get("type") == "null":
                continue
            if (a.get("type") == "object") == isinstance(v, dict) and (a.get("type") == "array") == isinstance(v, list):
                return _complete(v, a)
        return v
    if sch.get("type") == "object" and isinstance(v, dict):
        return {k: _complete(v.get(k), s) for k, s in sch.get("properties", {}).items()}
    if sch.get("type") == "array" and isinstance(v, list) and "items" in sch:
        return [_complete(x, sch["items"]) for x in v]
    return v


def _dim(key, v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        mn = mx = None
        nom = v
        if key == "height_max":
            nom, mx = None, v
    elif "tol" in v:
        nom, mn, mx = v["nom"], round(v["nom"] - v["tol"], 4), round(v["nom"] + v["tol"], 4)
    else:
        mn, nom, mx = v.get("min"), v.get("nom"), v.get("max")
    return {"label": LABELS.get(key, key), "min": mn, "nom": nom, "max": mx, "unit": "mm", "page": 1, "figure": "package outline"}


def _typed(name, e):
    from .. import datasheet as D
    pkg = {"name": name, "description": f"{name} (synthetic answer from the standard table)", "package_type": e["package_type"],
           "unresolved": [], "drawing": "synthetic"}
    for k, v in e.items():
        if k in ("lib", "source", "name", "package_type"):
            continue
        if k in D.DIMS or k in D.NUM_DIMS:
            pkg[k] = _dim(k, v)
        elif k == "exposed_pad":
            pkg[k] = {d: _dim(f"exposed_pad.{d}", v[d]) for d in ("length", "width")}
        else:
            pkg[k] = copy.deepcopy(v)
    return pkg


def _pin_count(pt):
    """Distinct numbered pins of the table as footprint_adv lays it out (an exposed pad split in parts counts once;
    mounting / shield pads and holes do not), the number a drawing states."""
    from .. import footprint_adv as FA
    try:
        pads = FA.table_layout({"name": "x", "package_type": "custom", "pad_table": copy.deepcopy(pt)})[0]
    except Exception:                                # a table the layout rejects: no count (the reader's run reports why)
        return None
    return len({p["num"] for p in pads if p["role"] in FA.PIN_ROLES}) or None


def _table(name, ref):
    """The reference pads as the land-pattern table a reader copies from a drawing (KiCad frame: mm, +Y down)."""
    pads, xs, ys = [], [], []
    for num, x, y, w, h, drill in ref["pads"]:
        p = {"number": num, "x": x, "y": y, "w": w, "h": h}
        if drill:
            p["type"], p["drill"] = ("tht" if num else "npth"), drill
        pads.append(p)
        xs += [x]; ys += [y]
    tht = any(p.get("type") == "tht" for p in pads)
    c = ref.get("courtyard") or []
    body = {"x1": c[0] + 0.25, "y1": c[1] + 0.25, "x2": c[2] - 0.25, "y2": c[3] - 0.25} if len(c) == 4 else None
    pt = {"unit": "mm", "y_axis": "down", "view": "top", "origin": "datum",
          "center_at": {"x": round((min(xs) + max(xs)) / 2, 4), "y": round((min(ys) + max(ys)) / 2, 4)},
          "footprint_origin": "pin1" if tht else "center", "pads": pads}
    if body:
        pt["body"] = {k: round(v, 4) for k, v in body.items()}
    return {"name": name, "description": f"{name} (synthetic answer from the reference land)", "package_type": "custom",
            "pin_count": _pin_count(pt), "pad_table": pt, "courtyard_excess": _dim("courtyard_excess", 0.25),
            "mount": "tht" if tht else None, "unresolved": [], "drawing": "synthetic"}


def synth_answer(case, ref, table=None):
    """One case -> the extraction (datasheet output schema) a perfect reader would return."""
    from .. import datasheet as D, stdparts
    table = stdparts.packages() if table is None else table
    name = case["footprint"].split(":", 1)[1]
    e = table.get(name)
    pkg = _typed(name, e) if e and e["package_type"] in TYPED else _table(name, ref)
    fam = case["family"]
    n = len(case["pins"])
    pins = [{"num": str(num), "name": nm, "type": "passive" if fam in ("chip", "diode") else "io",
             "side": "left" if i < (n + 1) // 2 else "right", "page": 1} for i, (num, nm) in enumerate(case["pins"])]
    ext = {"package": pkg, "pin_table": {"page": 1, "figure": "pin table", "rows": n}, "pins": pins,
           "symbol_description": f"{case['description']} (synthetic)", "notes": [], "embedded_instructions": []}
    return _complete(ext, D.output_schema())


def make_replay(set_name="kicad-v1", out=None):
    """Every case of the set -> its synthetic answer, in one compact gzip JSON (deterministic: same input, same bytes)."""
    data = R.load_set(set_name)
    answers = {c["mpn"]: synth_answer(c, data["references"][c["footprint"]]) for c in data["cases"]}
    pack = {"meta": {"set": data["meta"]["name"], "set_version": data["meta"].get("version"), "model": MODEL,
                     "about": "SYNTHETIC answers (ecadlib/eval/gate.py synth_answer), not a model's reading",
                     "answers": len(answers)}, "answers": dict(sorted(answers.items()))}
    out = Path(out or replay_path(set_name))
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(pack, sort_keys=True, separators=(",", ":")).encode()
    with open(out, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as g:
        g.write(raw)
    return out


def load_replay(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def expand(pack, d):
    """Replay pack -> one recording per part in d (the `eval run --replay` layout)."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    for part, ext in pack["answers"].items():
        url = f"https://example.invalid/{R._safe(part)}.pdf"
        msg = {"id": "msg_synthetic", "type": "message", "role": "assistant", "model": pack["meta"]["model"],
               "content": [{"type": "text", "text": json.dumps(ext)}], "stop_reason": "end_turn", "stop_sequence": None,
               "usage": {"input_tokens": 0, "output_tokens": 0}}
        (d / f"{R._safe(part)}.json").write_text(json.dumps(
            {"part": part, "model": pack["meta"]["model"], "estimate": {"input_tokens": 0, "output_tokens": 0, "usd": 0.0, "how": "synthetic"},
             "job": {"url": url, "where": "BOM", "offset": 0, "prov": {"url": url, "sent_as": "synthetic"}}, "message": msg}),
            encoding="utf-8")
    return d


class NoNetwork:
    """HTTP stand-in for the gate: every request is refused (and counted); the run must not depend on the network."""
    offline = True

    def __init__(self):
        self.calls, self.log = [], []

    def _no(self, method, url):
        from ..httpcache import Response
        self.calls.append((method, url))
        return Response(0, "", error="eval gate: no network")

    def get(self, url, *a, **kw):
        return self._no("GET", url)

    def post(self, url, *a, **kw):
        return self._no("POST", url)

    def head(self, url, *a, **kw):
        return self._no("HEAD", url)


# ---------------------------------------------------------------- run, baseline, check
def run(set_name="kicad-v1", out=None, replay=None, log=lambda *a: None, ids=None):
    """-> eval report (report.json) of the gate configuration. ids: only these cases (tests; check(subset=True))."""
    import os
    from unittest import mock
    replay = Path(replay or replay_path(set_name))
    tmp = tempfile.TemporaryDirectory(prefix="ecadlib-gate-")
    try:
        out = Path(out) if out else Path(tmp.name) / "run"
        rec = expand(load_replay(replay), Path(tmp.name) / "replay")
        env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("ECADLIB_")}
        http = NoNetwork()
        with mock.patch.dict(os.environ, env, clear=True):
            rep = R.run(set_name, out, replay=rec, sources=SOURCES, ids=ids, offline=True, cache=Path(tmp.name) / "cache", http=http,
                        rules=RU.load({}), log=log)   # DEFAULTS: no ecadlib.toml from the cwd
        rep["meta"]["network_requests"] = len(http.calls)
        return rep
    finally:
        tmp.cleanup()


def baseline(rep):
    s = rep["summary"]
    return {"meta": {"set": rep["meta"]["set"], "set_version": rep["meta"]["set_version"], "sources": rep["meta"]["sources"],
                     "replay": f"ecadlib/eval/replay/{rep['meta']['set']}.json.gz (synthetic)",
                     "updated": datetime.date.today().isoformat(),
                     "how": "python -m ecadlib eval gate --update; review the diff; commit"},
            "overall": {k: s["overall"][k] for k in ("cases", "pass", "rate")},
            "families": {k: {x: f[x] for x in ("cases", "pass", "rate")} for k, f in s["families"].items()},
            "passing": sorted(c["id"] for c in rep["cases"] if c["pass"])}


def check(rep, base, tol=0.01, subset=False):
    """-> {"ok", "problems": [...], "fixed": [...], "text"}. subset: the run covers only some cases (no family rates;
    baseline cases that are not in the run are skipped)."""
    problems, lines = [], []
    fams = rep["summary"]["families"]
    for k, b in sorted(base["families"].items() if not subset else ()):
        f = fams.get(k)
        if not f:
            problems.append(f"family {k}: missing from this run (baseline {b['pass']}/{b['cases']})")
            continue
        drop = (b["rate"] or 0) - (f["rate"] or 0)
        flag = "DROP" if drop > tol + 1e-9 else "ok"
        lines.append(f"  {k:<10} {f['pass']:>4}/{f['cases']:<4} {100 * (f['rate'] or 0):6.1f} %   baseline {b['pass']:>4}/{b['cases']:<4} "
                     f"{100 * (b['rate'] or 0):6.1f} %   {flag}")
        if flag == "DROP":
            problems.append(f"family {k}: pass rate {100 * f['rate']:.1f} % < baseline {100 * b['rate']:.1f} % - {100 * tol:.1f} %")
    by = {c["id"]: c for c in rep["cases"]}
    for i in base["passing"]:
        c = by.get(i)
        if c is None and subset:
            continue
        if c is None:
            problems.append(f"{i}: passed in the baseline, missing from this run")
        elif not c["pass"]:
            why = "; ".join(c["errors"][:3]) if c["source"] != "none" else "unresolved: " + "; ".join(c["tried"][-2:])
            problems.append(f"{i}: passed in the baseline, now fails ({c['source']}): {why}")
    was = set(base["passing"])
    fixed = sorted(c["id"] for c in rep["cases"] if c["pass"] and c["id"] not in was)
    o, bo = rep["summary"]["overall"], base["overall"]
    head = f"eval gate {rep['meta']['set']}: {o['pass']}/{o['cases']} pass (baseline {bo['pass']}/{bo['cases']}), " \
           f"{rep['meta']['wall_s']} s, network requests {rep['meta'].get('network_requests', '?')}"
    text = [head] + lines
    if fixed:
        text.append(f"newly passing ({len(fixed)}; `ecadlib eval gate --update` locks them in): " + ", ".join(fixed))
    text += ([f"FAIL ({len(problems)}):"] + [f"  {p}" for p in problems]) if problems else ["OK: no family dropped, no passing case broke"]
    return {"ok": not problems, "problems": problems, "fixed": fixed, "text": "\n".join(text)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib eval gate", description=__doc__.split("\n")[0])
    ap.add_argument("--set", default="kicad-v1")
    ap.add_argument("--baseline", help="baseline file (default ecadlib/eval/baselines/<set>.json)")
    ap.add_argument("--replay", help="replay pack (default ecadlib/eval/replay/<set>.json.gz)")
    ap.add_argument("--tol", type=float, default=0.01, help="allowed drop of a family's pass rate (fraction, default 0.01)")
    ap.add_argument("--update", action="store_true", help="rewrite the baseline from this run")
    ap.add_argument("--make-replay", action="store_true", help="rebuild the replay pack from the set and the standard table")
    ap.add_argument("-o", "--out", help="keep the run's report and library here")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.make_replay:
        p = make_replay(a.set, a.replay)
        print(f"replay pack: {p} ({load_replay(p)['meta']['answers']} answers)")
        return 0
    t0 = time.time()
    rep = run(a.set, a.out, a.replay)
    bp = Path(a.baseline or baseline_path(a.set))
    if a.update or not bp.exists():
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(json.dumps(baseline(rep), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        o = rep["summary"]["overall"]
        print(f"baseline written: {bp} ({o['pass']}/{o['cases']} pass, {time.time() - t0:.1f} s)")
        return 0
    res = check(rep, json.loads(bp.read_text(encoding="utf-8")), a.tol)
    print(json.dumps({k: v for k, v in res.items() if k != "text"}, indent=1) if a.json else res["text"])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
