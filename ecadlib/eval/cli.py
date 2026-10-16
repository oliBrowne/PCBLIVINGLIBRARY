"""ecadlib eval: measure how often the pipeline gets a part right, and what it costs.

    ecadlib eval run [--set kicad-v1] [--limit N] [--family F,..] [--case ID,..] [--model M] [--sources S,..]
                     [--record DIR | --replay DIR] [--offline] [-o out/eval/run]
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
    r.add_argument("--offline", action="store_true", help="HTTP cache only")
    r.add_argument("--cache", default="out/http_cache")
    r.add_argument("--rules", help="library rules file")
    r.add_argument("--tol", action="append", default=[], metavar="FIELD=MM",
                   help=f"override a tolerance ({', '.join(f'{k}={v}' for k, v in S.TOL.items())})")
    r.add_argument("-o", "--out", default="out/eval/run")
    r.add_argument("--json", action="store_true", help="print the summary as JSON")
    c = sub.add_parser("compare", help="diff two reports")
    c.add_argument("a"); c.add_argument("b")
    c.add_argument("--json", action="store_true")
    sub.add_parser("gate", help="CI accuracy gate: the whole set offline against the committed baseline (--update rewrites it)")
    sub.add_parser("golden", help="golden snapshots of the KiCad / Altium files (--update rewrites them)")
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
    if a.cmd == "compare":
        res = R.compare(a.a, a.b)
        print(json.dumps({k: v for k, v in res.items() if k != "text"}, indent=1, default=str) if a.json else res["text"])
        return 0
    tol = {}
    for t in a.tol:
        k, _, v = t.partition("=")
        if k not in S.TOL:
            ap.error(f"--tol {t}: unknown field {k!r}")
        tol[k] = float(v)
    rep = R.run(a.set, a.out, a.limit, a.model, a.record, a.replay, _list(a.sources), _list(a.family), _list(a.case),
                a.offline, a.cache, rules=a.rules, providers=a.providers, tol=tol, provider=a.provider,
                base_url=a.base_url, assist=a.assist, api_key_env=a.api_key_env)
    if a.json:
        print(json.dumps(rep["summary"], indent=1))
    else:
        o = rep["summary"]["overall"]
        print("\n".join(R.report_md(rep).splitlines()[2:6]))
        print(f"report: {a.out}/report.md ({o['pass']}/{o['cases']} pass)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
