"""ecadlib eval: measure how often the pipeline gets a part right, and what it costs.

    ecadlib eval run [--set kicad-v1] [--limit N] [--family F,..] [--case ID,..] [--model M] [--sources S,..]
                     [--record DIR | --replay DIR] [--repeat N] [--offline] [-o out/eval/run]
    ecadlib eval consistency A B      two runs of the same cases: parts that gave the same files (and cross-check)
    ecadlib eval compare A B          two run folders (or report.json files): pass rate and cost per family, flipped cases
    ecadlib eval sets                 the test sets shipped with ecadlib (cases per family)
    ecadlib eval show [--set NAME] [--family F]   the cases of a set
    ecadlib eval build-set --symbols DIR --footprints DIR [-o FILE]   rebuild a set from KiCad library checkouts
    ecadlib eval gate [--update]      CI accuracy gate: the whole set offline vs a committed baseline (gate.py)
    ecadlib eval golden [--update]    golden snapshots of the KiCad / Altium files we write (golden.py)

See ecadlib/eval/runner.py (run, modes), score.py (fields, tolerances) and build_set.py (where the cases come from)."""
import argparse, json, sys

from . import runner as R, score as S


def _list(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "build-set":
        from . import build_set
        return build_set.main(argv[1:])
    if argv and argv[0] in ("gate", "golden"):
        from . import gate, golden
        return {"gate": gate, "golden": golden}[argv[0]].main(argv[1:])
    ap = argparse.ArgumentParser(prog="ecadlib eval", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the pipeline on a test set and score every part")
    r.add_argument("--set", default="kicad-v1", help="set name (ecadlib/eval/sets) or a set .json file")
    r.add_argument("--limit", type=int, help="first N cases, families interleaved")
    r.add_argument("--family", help="only these families (comma list)")
    r.add_argument("--case", help="only these case ids or MPNs (comma list)")
    r.add_argument("--model", help="datasheet model (default: [ai] / [datasheet] model)")
    r.add_argument("--provider", help="anthropic | openai-compatible | openai | openrouter | gemini | ollama | lmstudio | vllm | "
                                      "llamacpp (default: [ai] provider); compare models on the same set")
    r.add_argument("--base-url", help="openai-compatible endpoint, e.g. http://localhost:11434/v1")
    r.add_argument("--api-key-env", help="NAME of the env var with the key for --provider")
    r.add_argument("--assist", choices=["auto", "off", "full"], help="guide rails (default: [ai] assist = auto, by tier)")
    r.add_argument("--sources", help="librarian sources in order (default: the rules' order without kicad)")
    r.add_argument("--providers", help="triage providers (default: the rules' order)")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--record", metavar="DIR", help="save every Claude answer here (live)")
    g.add_argument("--replay", metavar="DIR", help="answer from saved responses; nothing is sent")
    r.add_argument("--repeat", type=int, default=1, metavar="N",
                   help="run the cases N times (the repeats from the recorded answers when --record/--replay) and report "
                        "how many parts gave the same files")
    r.add_argument("--batch", action="store_true", help="send the datasheet reads as one Message Batch (half price; "
                                                         "answers can take minutes to hours)")
    r.add_argument("--cached-only", action="store_true", help="re-score this out folder with nothing sent: its cached "
                   "answers, and the answers of its collected batches downloaded again (free), read by this code")
    r.add_argument("--offline", action="store_true", help="HTTP cache only")
    r.add_argument("--cache", default="out/http_cache")
    r.add_argument("--rules", help="library rules file")
    r.add_argument("--tol", action="append", default=[], metavar="FIELD=MM",
                   help=f"override a tolerance ({', '.join(f'{k}={v}' for k, v in S.TOL.items())})")
    r.add_argument("--pins", help="pinned datasheets (URL + SHA-256) to check against (default: <set>.pins.json)")
    r.add_argument("-o", "--out", default="out/eval/run")
    r.add_argument("--json", action="store_true", help="print the summary as JSON")
    pn = sub.add_parser("pin", help="pin the datasheets a run read (URL + SHA-256) so later runs flag a changed PDF")
    pn.add_argument("report", help="a run's out folder or report.json")
    pn.add_argument("-o", "--out", help="pins file (default: <set>.pins.json next to the shipped set)")
    pb = sub.add_parser("publish", help="a results page (Markdown) from a run's report: headline, failures by type, every miss")
    pb.add_argument("report", help="a run's out folder or report.json")
    pb.add_argument("-o", "--out", default="docs/benchmark-results.md")
    pb.add_argument("--title")
    c = sub.add_parser("compare", help="diff two reports")
    c.add_argument("a"); c.add_argument("b")
    c.add_argument("--json", action="store_true")
    sub.add_parser("gate", help="CI accuracy gate: the whole set offline against the committed baseline (--update rewrites it)")
    sub.add_parser("golden", help="golden snapshots of the KiCad / Altium files (--update rewrites them)")
    k = sub.add_parser("consistency", help="did two runs of the same cases give the same files?")
    k.add_argument("a"); k.add_argument("b")
    k.add_argument("--json", action="store_true")
    sub.add_parser("sets", help="list the shipped test sets")
    s = sub.add_parser("show", help="list the cases of a set")
    s.add_argument("--set", default="kicad-v1"); s.add_argument("--family")
    a = ap.parse_args(argv)
    if a.cmd == "sets":
        for n in R.available():
            d = R.load_set(n)
            fams = {}
            for cs in d["cases"]:
                fams[cs["family"]] = fams.get(cs["family"], 0) + 1
            print(f"{n}: {len(d['cases'])} cases, built {d['meta'].get('built')} from kicad-symbols {d['meta'].get('kicad_symbols')}, "
                  f"kicad-footprints {d['meta'].get('kicad_footprints')}\n  " + ", ".join(f"{k} {v}" for k, v in fams.items()))
        return 0
    if a.cmd == "show":
        for cs in R.load_set(a.set)["cases"]:
            if not a.family or cs["family"] in _list(a.family):
                print(f"{cs['id']:<40} {cs['manufacturer'][:18]:<18} {cs['package']:<16} {cs['footprint']}  [{cs['from']['kind']}]")
        return 0
    if a.cmd == "pin":
        out = a.out or R.set_path(R._load_report(a.report)["meta"]["set"]).with_name(
            R._load_report(a.report)["meta"]["set"] + ".pins.json")
        n = R.write_pins(a.report, out)
        print(f"{n} datasheets pinned -> {out}")
        return 0
    if a.cmd == "publish":
        print(R.publish(a.report, a.out, a.title))
        print(f"-> {a.out}")
        return 0
    if a.cmd == "compare":
        res = R.compare(a.a, a.b)
        print(json.dumps({k: v for k, v in res.items() if k != "text"}, indent=1, default=str) if a.json else res["text"])
        return 0
    if a.cmd == "consistency":
        res = R.consistency(a.a, a.b)
        if a.json:
            print(json.dumps(res, indent=1))
        else:
            print(f"{res['same']}/{res['cases']} parts gave the same files" + ("" if not res["differ"] else ":"))
            for d in res["differ"]:
                print(f"  {d['id']}: differ in {', '.join(d['files']) or '-'}"
                      + (f"; cross-check {d['cross_check'][0] or '-'} vs {d['cross_check'][1] or '-'}"
                         if d["cross_check"][0] != d["cross_check"][1] else ""))
        return 0 if not res["differ"] else 2
    tol = {}
    for t in a.tol:
        k, _, v = t.partition("=")
        if k not in S.TOL:
            ap.error(f"--tol {t}: unknown field {k!r}")
        try:
            tol[k] = float(v)
        except ValueError:
            ap.error(f"--tol {t}: {v!r} is not a number of mm")
        if not 0 <= tol[k] < float("inf"):            # nan would fail every comparison, a negative one every part
            ap.error(f"--tol {t}: must be a number of 0 or more")
    rep = R.run(a.set, a.out, a.limit, a.model, a.record, a.replay, _list(a.sources), _list(a.family), _list(a.case),
                a.offline, a.cache, rules=a.rules, providers=a.providers, tol=tol, provider=a.provider,
                base_url=a.base_url, assist=a.assist, api_key_env=a.api_key_env, repeat=a.repeat, batch=a.batch,
                cached_only=a.cached_only, pins=a.pins)
    if a.json:
        print(json.dumps(rep["summary"], indent=1))
    else:
        o = rep["summary"]["overall"]
        print("\n".join(R.report_md(rep).splitlines()[2:6]))
        print(f"report: {a.out}/report.md ({o['pass']}/{o['cases']} pass)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
