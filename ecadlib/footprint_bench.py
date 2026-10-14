"""Benchmark generated footprints against KiCad's official library: every pad (position, size, drill) and the courtyard.

    ecadlib footprint-bench                        # fetch the references (cached), compare, print pass rates
    ecadlib footprint-bench --offline              # from the HTTP cache / the numbers-only fixture only
    ecadlib footprint-bench --save-fixture tests/fixtures/kicad_bench.json

BENCH lists ecadlib package specs (package_specs/ and package_specs/advanced/) and the official footprint each one is
compared with (gitlab.com/kicad/libraries/kicad-footprints, fetched once through httpcache). Pads are matched by
number; pads that share a number (MP, SH, NPTH holes "") are matched to the nearest one of that number. A pad passes
when its centre and its size (after rotation) are within POS_TOL / SIZE_TOL and a through-hole pad's drill matches;
the courtyard passes when every edge of its bounding box is within CRT_TOL. Reported: pads passing, footprints whose
pads all pass, courtyards passing, and footprint_check findings on our footprint.

The fixture keeps only numbers (pad centres, sizes, drills, courtyard box) from the KiCad files (CC-BY-SA 4.0; facts,
not the drawings), so the tests run offline.
"""
import argparse, json, math, sys
from pathlib import Path

from . import footprint_check as C, footprint_gen as F, sexpr

KICAD_RAW = "https://gitlab.com/kicad/libraries/kicad-footprints/-/raw/master/"
SPECS = Path(__file__).with_name("package_specs")
POS_TOL, SIZE_TOL, CRT_TOL = 0.01, 0.01, 0.011
# (spec name, KiCad library, what the comparison shows)
BENCH = [
    ("BGA-256_17.0x17.0mm_Layout16x16_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD", "Package_BGA", "IPC land from the ball size"),
    ("BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD", "Package_BGA", "IPC land from the ball size"),
    ("BGA-324_15.0x15.0mm_Layout18x18_P0.8mm_Ball0.5mm_Pad0.4mm_NSMD", "Package_BGA", "18 rows: A..V without I O Q S"),
    ("BGA-144_7.0x7.0mm_Layout13x13_P0.5mm_Ball0.3mm_Pad0.25mm_NSMD", "Package_BGA", "depopulated centre E5:J9"),
    ("UFBGA-132_7x7mm_Layout12x12_P0.5mm", "Package_BGA", "ball map with a missing ring; datasheet land"),
    ("PinHeader_1x05_P2.54mm_Vertical", "Connector_PinHeader_2.54mm", "THT header table"),
    ("PinHeader_2x05_P2.54mm_Vertical", "Connector_PinHeader_2.54mm", "dual-row zig-zag numbering"),
    ("PinHeader_1x05_P2.00mm_Vertical", "Connector_PinHeader_2.00mm", "THT header table"),
    ("PinHeader_2x05_P1.27mm_Vertical", "Connector_PinHeader_1.27mm", "body offset from the pins"),
    ("PinHeader_2x05_P1.27mm_Vertical_SMD", "Connector_PinHeader_1.27mm", "SMD header land"),
    ("PinHeader_2x05_P2.54mm_Vertical_SMD", "Connector_PinHeader_2.54mm", "SMD header land"),
    ("PinHeader_1x05_P2.54mm_Vertical_SMD_Pin1Left", "Connector_PinHeader_2.54mm", "single row, alternating sides"),
    ("JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", "Connector_JST", "THT pads sized from the drill"),
    ("JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical", "Connector_JST", "THT pads sized from the drill"),
    ("JST_PH_S4B-PH-SM4-TB_1x04-1MP_P2.00mm_Horizontal", "Connector_JST", "SMD + mounting pads, y-up drawing"),
    ("JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", "Connector_JST", "SMD + mounting pads"),
    ("Molex_KK-254_AE-6410-04A_1x04_P2.54mm_Vertical", "Connector_Molex", "table in mil"),
    ("Molex_PicoBlade_53398-0471_1x04-1MP_P1.25mm_Vertical", "Connector_Molex", "bottom-view drawing from pin 1"),
    ("USB_C_Receptacle_HRO_TYPE-C-31-M-12", "Connector_USB", "stacked pads, slots, NPTH, datum origin"),
    ("SOT-23-6", "Package_TO_SOT_SMD", "gull-wing, uneven rows"),
]


def spec_path(name):
    for d in (SPECS, SPECS / "advanced"):
        if (d / f"{name}.json").exists():
            return d / f"{name}.json"
    raise FileNotFoundError(f"no package spec {name}")


def numbers(text):
    """The numbers the comparison needs from a .kicad_mod: pads (num, x, y, w, h, drill or 0) and the courtyard box."""
    t = sexpr.parse(text)
    pads = []
    for p, r in zip(sexpr.find(t, "pad"), C._pad_rects(t)):
        d = sexpr.first(p, "drill")
        drill = max(float(v) for v in d[1:] if v != "oval") if d else 0.0
        pads.append([r["num"], round(r["x"], 4), round(r["y"], 4), round(r["w"], 4), round(r["h"], 4), drill])
    pads = [p for p in pads if p[0] or p[5]]                 # paste-only windows are not pads
    return {"pads": pads, "courtyard": list(sexpr.courtyard_bbox(t) or ())}


def compare(mine, ref):
    """mine/ref: numbers() dicts -> report dict."""
    left = list(ref["pads"])
    rows, missing = [], []
    for p in mine["pads"]:
        cands = [q for q in left if q[0] == p[0]]
        if not cands:
            missing.append(p[0] or "(hole)")
            continue
        q = min(cands, key=lambda q: math.hypot(q[1] - p[1], q[2] - p[2]))
        left.remove(q)
        dpos = max(abs(p[1] - q[1]), abs(p[2] - q[2]))
        dsize = max(abs(p[3] - q[3]), abs(p[4] - q[4]))
        ddrill = abs(p[5] - q[5])
        rows.append({"pad": p[0], "dpos": round(dpos, 4), "dsize": round(dsize, 4), "ddrill": round(ddrill, 4),
                     "ok": dpos <= POS_TOL + 1e-9 and dsize <= SIZE_TOL + 1e-9 and ddrill <= SIZE_TOL + 1e-9})
    a, b = mine["courtyard"], ref["courtyard"]
    dcrt = max(abs(x - y) for x, y in zip(a, b)) if a and b else None
    ok_pads = sum(r["ok"] for r in rows)
    total = max(len(mine["pads"]), len(ref["pads"]))
    return {"pads": total, "pads_ok": ok_pads, "extra_in_ours": missing, "missing_in_ours": [q[0] or "(hole)" for q in left],
            "worst_pos": max((r["dpos"] for r in rows), default=None), "worst_size": max((r["dsize"] for r in rows), default=None),
            "courtyard_ours": a, "courtyard_kicad": b, "courtyard_diff": None if dcrt is None else round(dcrt, 4),
            "courtyard_ok": dcrt is not None and dcrt <= CRT_TOL, "all_pads_ok": ok_pads == total, "rows": rows}


def reference(name, lib, http=None, fixture=None):
    """KiCad's numbers for one footprint: from the fixture, else fetched (and cached) from GitLab."""
    if fixture and name in fixture:
        return fixture[name]
    if http is None:
        return None
    r = http.get(f"{KICAD_RAW}{lib}.pretty/{name}.kicad_mod")
    return numbers(r.text) if r.ok else None


def run(bench=BENCH, http=None, fixture=None):
    results = []
    for name, lib, what in bench:
        spec = json.loads(spec_path(name).read_text())
        F.validate(spec)
        txt, rep = F.build(spec)
        ref = reference(name, lib, http, fixture)
        res = {"name": name, "library": lib, "shows": what, "type": spec["package_type"],
               "check_errors": [c["message"] for c in rep["checks"] if c["level"] == "error"]}
        if ref is None:
            res["error"] = "reference not available (offline and not in the fixture)"
        else:
            res.update(compare(numbers(txt), ref))
        results.append(res)
    done = [r for r in results if "error" not in r]
    summary = {"footprints": len(done), "unavailable": len(results) - len(done),
               "pads": sum(r["pads"] for r in done), "pads_ok": sum(r["pads_ok"] for r in done),
               "footprints_all_pads_ok": sum(r["all_pads_ok"] for r in done), "courtyards_ok": sum(r["courtyard_ok"] for r in done),
               "check_clean": sum(not r["check_errors"] for r in results)}
    return results, summary


def report(results, summary):
    out = [f"{'footprint':<58} {'pads ok':>9} {'worst pos/size':>15} {'courtyard':>10}  notes"]
    for r in results:
        if "error" in r:
            out.append(f"{r['name']:<58} {'-':>9} {'':>15} {'':>10}  {r['error']}")
            continue
        crt = "ok" if r["courtyard_ok"] else f"{r['courtyard_diff']:+.2f}" if r["courtyard_diff"] is not None else "-"
        note = r["shows"]
        if r["missing_in_ours"] or r["extra_in_ours"]:
            note += f"; KiCad-only pads {r['missing_in_ours']}, ours-only {r['extra_in_ours']}"
        if not r["courtyard_ok"] and r["courtyard_kicad"]:
            note += f"; courtyard ours {r['courtyard_ours']} KiCad {r['courtyard_kicad']}"
        out.append(f"{r['name']:<58} {r['pads_ok']:>4}/{r['pads']:<4} {r['worst_pos']:>7.3f}/{r['worst_size']:<7.3f} {crt:>10}  {note}")
    s = summary
    pct = lambda a, b: f"{100 * a / b:.0f} %" if b else "-"
    out += ["", f"pads within {POS_TOL} mm / {SIZE_TOL} mm: {s['pads_ok']}/{s['pads']} ({pct(s['pads_ok'], s['pads'])}); "
            f"footprints with every pad matching: {s['footprints_all_pads_ok']}/{s['footprints']} "
            f"({pct(s['footprints_all_pads_ok'], s['footprints'])}); courtyards within {CRT_TOL:.2f} mm: "
            f"{s['courtyards_ok']}/{s['footprints']} ({pct(s['courtyards_ok'], s['footprints'])}); "
            f"footprint_check clean: {s['check_clean']}/{s['footprints'] + s['unavailable']}"]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib footprint-bench", description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true", help="use only the HTTP cache and the fixture")
    ap.add_argument("--fixture", help="numbers-only reference file to read (default: none; fetch from GitLab)")
    ap.add_argument("--save-fixture", help="write the KiCad numbers used to this JSON file")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--json", help="write the full results as JSON")
    a = ap.parse_args(argv)
    from .httpcache import CachedHTTP
    http = CachedHTTP(a.cache, min_interval=0.3, offline=a.offline, ttl_s=30 * 86400)
    fixture = json.loads(Path(a.fixture).read_text()) if a.fixture else None
    results, summary = run(http=http, fixture=fixture)
    print(report(results, summary))
    if a.save_fixture:
        keep = {}
        for name, lib, _ in BENCH:
            ref = reference(name, lib, http, fixture)
            if ref:
                keep[name] = ref
        src = KICAD_RAW + " (numbers only: pad centre, size, drill; courtyard box). KiCad libraries are CC-BY-SA 4.0."
        lines = [f"{json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}" for k, v in {"_source": src, **keep}.items()]
        Path(a.save_fixture).write_text("{\n" + ",\n".join(lines) + "\n}\n")
        print(f"fixture: {len(keep)} footprints -> {a.save_fixture}")
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    return 0 if summary["footprints"] and not summary["unavailable"] and summary["pads_ok"] == summary["pads"] else 1


if __name__ == "__main__":
    sys.exit(main())
