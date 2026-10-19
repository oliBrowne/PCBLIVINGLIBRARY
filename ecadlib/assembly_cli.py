"""ecadlib assembly: assembly-ready parts (docs/assembly.md).

    ecadlib assembly part FOOTPRINT.kicad_mod --lcsc C25744 [--png overlay.png]   one footprint against the assembler's
    ecadlib assembly check BOARD.kicad_pcb|PNP.csv [--bom BOM.csv] [--footprints DIR]   the preflight report
    ecadlib assembly export BOARD.kicad_pcb|PNP.csv -o out/                     the assembler's BOM + CPL and the report
    ecadlib assembly profiles [NAME]                                            the profiles and where each rule comes from

The profile comes from --profile, else [assembly] profile in ecadlib.toml (default jlcpcb); --strict (or [assembly]
strict) holds risky pads and unknown rotations as well as incompatible pads. JLCPCB's own footprints come from EasyEDA
behind [providers] unofficial: with it off (or --offline and nothing cached) rotations come from the family table, and
parts it cannot settle are listed, never guessed. Exit status: 0 nothing held, 1 something held, 2 bad input."""
import argparse, json, sys
from pathlib import Path

from . import assembly as A
from . import assembly_io as IO


def _rules(path, start):
    from . import rules as R
    return R.load(path, start=start)


def _http(a):
    from .httpcache import CachedHTTP
    return CachedHTTP(a.cache, min_interval=1.5, offline=a.offline)


def _setup(a, start):
    rules = _rules(a.rules, start)
    asm = rules.get("assembly") or {}
    prof = A.load_profile(a.profile or asm.get("profile") or "jlcpcb")
    strict = bool(a.strict or asm.get("strict"))
    if getattr(a, "recorded", None):
        fetch = IO.recorded(a.recorded)
    else:
        fetch = IO.fetcher(_http(a), rules, prof)
    return rules, prof, strict, fetch


def _common(p, board=True):
    p.add_argument("--profile", help="assembly profile: jlcpcb, or a path to your own .json (default: [assembly] profile)")
    p.add_argument("--strict", action="store_true", help="hold risky pads and unknown rotations too")
    p.add_argument("--rules", help="library rules file (default: ecadlib.toml found from the input's folder)")
    p.add_argument("--offline", action="store_true", help="use the HTTP cache only")
    p.add_argument("--cache", default="out/http_cache")
    p.add_argument("--recorded", metavar="DIR", help=argparse.SUPPRESS)       # <LCSC>.json answers instead of the network
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    if board:
        p.add_argument("board", help="a KiCad board (.kicad_pcb) or an Altium pick-and-place file (.csv)")
        p.add_argument("--bom", help="a BOM CSV with a Designator column: its LCSC / Assembly columns fill in the parts")
        p.add_argument("--footprints", metavar="DIR", help="your library's .kicad_mod files, for an Altium file (no pads in it)")
        p.add_argument("--origin", choices=("auto", "aux", "page"), default="auto",
                       help="KiCad: CPL coordinates from the drill / place origin (aux), the page origin, or whichever the "
                            "Gerbers use (auto, default)")


def _load_board(a):
    pls, info = IO.read_board(a.board, a.origin)
    if a.bom:
        n = IO.merge_bom(pls, a.bom)
        info["format"] += f"; {n} part(s) filled in from {Path(a.bom).name}"
    lib = IO.footprints_from(a.footprints) if a.footprints else None
    return pls, info, lib


def _print_listed(rep):
    for title, rows in IO.listed(rep):
        print(f"\n{title}:")
        for r in rows:
            print(f"  {r}")


def cmd_part(a):
    rules, prof, strict, fetch = _setup(a, Path(a.footprint).parent)
    ours = A.from_kicad_mod(Path(a.footprint).read_text(encoding="utf-8"), a.footprint)
    lcsc = (a.lcsc or "").upper()
    theirs, note = (None, "no LCSC number given (--lcsc)")
    if a.theirs:
        theirs, note = A.from_easyeda(a.theirs, a.theirs), ""
        lcsc = lcsc or (theirs.meta or {}).get("lcsc", "")
    elif lcsc:
        theirs, note = fetch(lcsc)
    res = A.assess(ours, theirs, prof, lcsc, strict=strict)
    if note:
        res["notes"].append(note)
    params = A.parameters(res)
    if a.png:
        try:
            A.render_overlay(ours, theirs, res, a.png)
        except (A.AssemblyError, ImportError) as e:
            res["notes"].append(f"overlay not drawn: {e}")
    if a.json:
        out = {k: v for k, v in res.items() if k != "fit"}
        out["parameters"], out["correction"] = params, A.correction(res)
        print(json.dumps(out, indent=1, default=str))
    else:
        print(f"{ours.name} vs {res['theirs'] or 'no assembler footprint'}{f' ({lcsc})' if lcsc else ''}: "
              f"pads {res['pads']}, rotation {res['rotation']}")
        for k in A.PARAMS:
            print(f"  {k}: {params[k]}")
        for r in res["reasons"]:
            print(f"  - {r}")
        for r in res["review"]:
            print(f"  review: {r}")
        for n in res["notes"]:
            print(f"  note: {n}")
        if a.png:
            print(f"  overlay: {a.png}")
    return 1 if res["hold"] else 0


def _board_run(a, write):
    rules, prof, strict, fetch = _setup(a, Path(a.board).parent)
    pls, info, lib = _load_board(a)
    out = Path(a.out) if write else None
    rep = IO.run_check(pls, prof, fetch, strict, lib, overlays=(out / "overlays") if out else a.overlays)
    files = IO.export(rep, info, prof, out) if write else []
    if a.json:
        print(json.dumps({**IO.report_json(rep, info), "files": [str(f) for f in files]}, indent=1, default=str))
    else:
        s = rep["summary"]
        print(f"{Path(a.board).name}: {s['placed']} parts to place ({prof['profile']}), {s['skipped']} not placed; "
              f"pads " + ", ".join(f"{v} {k}" for k, v in s["pads"].items() if v) + "; rotation " +
              ", ".join(f"{v} {k}" for k, v in s["rotation"].items() if v))
        print(f"coordinates from the {info['origin_why']}")
        _print_listed(rep)
        for f in files:
            print(f"wrote {f}")
    return 1 if rep["summary"]["hold"] else 0


def cmd_profiles(a):
    names = [a.name] if a.name else A.profiles()
    out = []
    for n in names:
        p = A.load_profile(n)
        fams = {k: {"zero": v.get("zero"), "polarized": v.get("polarized", False), "source": v.get("source")}
                for k, v in p["families"].items()}
        out.append({"profile": p["profile"], "name": p.get("name", ""), "file": p["_path"], "families": fams,
                    "overrides": len(p.get("overrides", [])), "sources": p["sources"]})
    if a.json:
        print(json.dumps(out, indent=1))
        return 0
    for p in out:
        print(f"{p['profile']}: {p['name']} ({p['file']})")
        for k, f in p["families"].items():
            z = f["zero"]
            pose = "no 0 deg rule yet (rotation unknown unless the assembler's footprint is checked)" if not z else \
                f"pin 1 / cathode {z.get('pin1')}" + (f", numbers run {z['run']}" if z.get("run") else "")
            print(f"  {k:13} {pose}")
        print(f"  {p['overrides']} per-part override(s); sources: " + ", ".join(p["sources"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib assembly", description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n", 1)[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("part", help="one footprint against the assembler's footprint for an LCSC number")
    p.add_argument("footprint", help="a .kicad_mod file")
    p.add_argument("--lcsc", help="the LCSC part number (JLCPCB), e.g. C25744")
    p.add_argument("--theirs", help="a recorded EasyEDA answer (.json) instead of fetching")
    p.add_argument("--png", help="write the overlay picture here")
    _common(p, board=False)
    c = sub.add_parser("check", help="preflight: every part's pads, rotation, LCSC number and stock")
    _common(c)
    c.add_argument("--overlays", metavar="DIR", help="write an overlay picture per checked part here")
    e = sub.add_parser("export", help="the assembler's BOM and CPL files, with the report")
    _common(e)
    e.add_argument("-o", "--out", required=True, help="output folder")
    r = sub.add_parser("profiles", help="the assembly profiles and their rules")
    r.add_argument("name", nargs="?")
    r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "part":
            return cmd_part(a)
        if a.cmd == "profiles":
            return cmd_profiles(a)
        return _board_run(a, write=a.cmd == "export")
    except (A.AssemblyError, FileNotFoundError, ValueError) as e:
        print(f"ecadlib assembly: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
