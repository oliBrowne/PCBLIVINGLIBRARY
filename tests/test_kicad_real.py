"""Real KiCad as the judge of what ecadlib writes. Run:  python3 -m tests.test_kicad_real

The tests that need KiCad skip (print a line and return) when kicad-cli or a Python with `pcbnew` is missing, so the
normal CI job stays green; .github/workflows/tests.yml has an optional job that installs KiCad and runs them.
pcbnew lives in KiCad's own Python (usually /usr/bin/python3), so pcbnew work runs in tests/kicad_pcbnew_job.py
in a subprocess and comes back as JSON; everything is compared here.

What KiCad checks (checked with KiCad 7.0.11, 9.0.9 and 10.0.8; CI also runs KiCad 8):
  * every footprint footprint_gen / footprint_adv makes (all package_specs, density A and C of the IPC parts, random
    BGA / header / connector specs) loads in pcbnew with our pads, layers, courtyard (one closed outline, same box)
    and graphics; kicad-cli upgrades and plots them;
  * a board with all of them passes KiCad's DRC except for what KiCad's own library footprints also show (ALLOWED);
  * kicad_export's fp-lib-table resolves in a KiCad project (${KIPRJMOD}) and the footprints match the library;
  * kicad_export's symbol library loads (KiCad 8+ directly; KiCad 7 refuses the KiCad 8 format, so a copy with the
    two KiCad 8 tokens removed is loaded and must come back with the same pins, graphics and fields);
  * kicad_import reads pads exactly as pcbnew does; a PcbLib converted back to KiCad loads with the same copper;
  * step_gen models load through KiCad's OpenCascade STEP reader (kicad-cli pcb export step).
The last section holds offline regression tests for the defects these runs found (they always run)."""
import json, os, random, re, shutil, subprocess, sys, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, easyeda_convert as EC, footprint_bench as B, footprint_check as C, \
    footprint_gen as F, kicad_export as X, kicad_import as K, sexpr, step_gen as SG

ROOT = Path(__file__).parent.parent
SPECS = sorted((ROOT / "ecadlib" / "package_specs").glob("*.json")) + sorted((ROOT / "ecadlib" / "package_specs" / "advanced").glob("*.json"))
JOB = Path(__file__).with_name("kicad_pcbnew_job.py")
_CACHE = {}

# DRC findings that KiCad's own library footprints show too (checked against KiCad 7.0.11's footprints of the same
# names): 0.2 mm thermal-via drills, the 1.00 mm header's 0.15 mm pad gap, USB-C's stacked GND/VBUS pads, pads
# that share a number (mounting pads, SOT-223 tab) not joined by a track, REF** over the SOT-23 pin-1 triangle.
ALLOWED = [(r"_ThermalVias$", {"drill_out_of_range"}), (r"^PinHeader_1x10_P1\.00mm", {"clearance"}),
           (r"^USB_C_", {"clearance", "solder_mask_bridge", "unconnected_items",
                         "shorting_items"}), (r"^SOT-23", {"silk_overlap"})]


# ---------------------------------------------------------------- KiCad discovery
def kicad_cli():
    return shutil.which("kicad-cli")


def kicad_major():
    if "major" not in _CACHE:
        exe, v = kicad_cli(), None
        if exe:
            r = subprocess.run([exe, "version"], capture_output=True, text=True)
            m = re.search(r"(\d+)\.", r.stdout)
            v = int(m.group(1)) if m else None
        _CACHE["major"] = v
    return _CACHE["major"]


def pcbnew_python():
    """A Python interpreter that can import pcbnew (KiCad's), or None."""
    if "py" not in _CACHE:
        _CACHE["py"] = None
        for exe in dict.fromkeys([sys.executable, "/usr/bin/python3", shutil.which("python3") or ""]):
            if exe and Path(exe).exists() and subprocess.run([exe, "-c", "import pcbnew"], capture_output=True).returncode == 0:
                _CACHE["py"] = exe
                break
    return _CACHE["py"]


def _skip(what):
    """A test that needs `what`: skipped. Under pytest a real skip (reported as skipped, not passed); under
    tests.run_all (no pytest) a printed line, and the caller returns."""
    import sys
    if "pytest" in sys.modules:
        sys.modules["pytest"].skip(f"{what} not installed")
    print(f"      ({what} not installed: skipped)")


def pcbnew_job(job, **args):
    r = subprocess.run([pcbnew_python(), str(JOB), job, json.dumps(args)], capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, (job, r.returncode, r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def cli(*args):
    """kicad-cli. For `... export svg ... -o DIR` the folder is made first: KiCad 10 no longer creates it."""
    if tuple(map(str, args[1:3])) == ("export", "svg") and "-o" in args:
        Path(args[args.index("-o") + 1]).mkdir(parents=True, exist_ok=True)
    return subprocess.run([kicad_cli(), *map(str, args)], capture_output=True, text=True, timeout=900)


def kicad7_symbols(text):
    """KiCad 8 symbol library text -> the KiCad 7 format (version 20220914): drop the only KiCad 8 tokens we write."""
    text = text.replace(f"(version {X.SYM_VERSION})", "(version 20220914)")
    return re.sub(r"\n?[ \t]*\((?:generator_version|exclude_from_sim) [^()]*\)", "", text)


def kicad7_footprint(text):
    """kicad_export's KiCad 8 footprint (version 20240108, Reference / Value as properties) -> KiCad 7 syntax."""
    text = text.replace(f"(version {X.FP_VERSION})", "(version 20221018)")
    return re.sub(r'\(property "(Reference|Value)" ("(?:[^"\\]|\\.)*") \(at ([^)]*)\) ',
                  lambda m: f'(fp_text {m.group(1).lower()} {m.group(2)} (at {m.group(3)}) ', text)


# ---------------------------------------------------------------- footprints we generate
def _all_footprints(d, random_specs=30):
    """Write every example spec, density A and C of the IPC parts and some random advanced specs into d/gen.pretty."""
    from tests.test_footprint_review import _random_spec
    lib = Path(d) / "gen.pretty"
    lib.mkdir(parents=True, exist_ok=True)
    for p in SPECS:
        s = json.loads(p.read_text())
        variants = [(s, None)] + ([(dict(s, name=f"{s['name']}_d{x}"), x) for x in "AC"] if "advanced" not in p.parts else [])
        for spec, dens in variants:
            (lib / f"{spec['name']}.kicad_mod").write_text(F.build(spec, dens)[0])
    R = random.Random(2026)
    for i in range(random_specs):
        s = _random_spec(R)
        s["name"] = f"Rnd{i:02d}_{s['name']}"
        (lib / f"{s['name']}.kicad_mod").write_text(F.build(s)[0])
    return lib


def _expand(layers):
    out = set()
    for l in layers:
        if l == "*.Cu":
            out |= {"F.Cu", "B.Cu"}
        elif l.startswith(("*.", "F&B.")):
            out |= {"F." + l.split(".", 1)[1], "B." + l.split(".", 1)[1]}
        else:
            out.add(l)
    return out


KNAME = {"F.SilkS": "F.Silkscreen", "B.SilkS": "B.Silkscreen", "F.CrtYd": "F.Courtyard", "B.CrtYd": "B.Courtyard"}


def test_generated_footprints_load_in_pcbnew():
    if not pcbnew_python():
        return _skip("pcbnew (KiCad's Python module)")
    with tempfile.TemporaryDirectory() as d:
        lib = _all_footprints(d)
        got = pcbnew_job("load", lib=str(lib))
        files = sorted(lib.glob("*.kicad_mod"))
        assert len(got) == len(files) >= 120, len(got)
        npads = 0
        for f in files:
            k = got[f.stem]
            assert k is not None, f"KiCad could not load {f.name}"
            t = sexpr.parse(f.read_text())
            ours = C._pad_rects(t)
            assert len(k["pads"]) == len(ours), (f.stem, len(k["pads"]), len(ours))
            for kp, op in zip(k["pads"], ours):
                npads += 1
                w, h = (kp["h"], kp["w"]) if round(kp["rot"]) % 180 else (kp["w"], kp["h"])
                assert kp["num"] == op["num"], (f.stem, kp, op)
                assert kp["custom"] or (abs(kp["x"] - op["x"]) < 1e-4 and abs(kp["y"] - op["y"]) < 1e-4 and   # custom: anchor
                                        abs(w - op["w"]) < 1e-4 and abs(h - op["h"]) < 1e-4), (f.stem, kp, op)
                want = {KNAME.get(l, l) for l in _expand(op["layers"])}
                assert {l for l in kp["layers"] if not l.startswith("In")} == want, (f.stem, kp["num"], kp["layers"], op["layers"])
            assert k["courtyard_outlines"] == 1, (f.stem, "courtyard is not one closed outline")
            cb = sexpr.courtyard_bbox(t)
            if (kicad_major() or 7) < 8:
                assert max(abs(a - b) for a, b in zip(k["courtyard"], cb)) < 1e-3, (f.stem, k["courtyard"], cb)
            else:                                                 # KiCad 8+ counts the 0.05 mm line in the outline
                grow = [cb[0] - k["courtyard"][0], cb[1] - k["courtyard"][1], k["courtyard"][2] - cb[2], k["courtyard"][3] - cb[3]]
                assert all(-1e-3 < g < 0.05 + 1e-3 for g in grow), (f.stem, k["courtyard"], cb)
            mine = {}
            for node in t[1:]:
                if isinstance(node, list) and node and node[0] in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc", "fp_text") \
                        and not (node[0] == "fp_text" and node[1] in ("reference", "value")):
                    ln = KNAME.get(sexpr.first(node, "layer")[1], sexpr.first(node, "layer")[1])
                    mine[ln] = mine.get(ln, 0) + 1
            assert k["graphics"] == mine, (f.stem, k["graphics"], mine)
            assert k["zones"] == len(list(sexpr.find(t, "zone"))) and k["value"] == f.stem and k["reference"] == "REF**", f.stem
        print(f"      pcbnew loaded {len(files)} footprints, {npads} pads as written")


def test_kicad_cli_upgrades_and_plots_footprints():
    if not kicad_cli():
        return _skip("kicad-cli")
    with tempfile.TemporaryDirectory() as d:
        lib = _all_footprints(d, random_specs=10)
        r = cli("fp", "upgrade", lib, "-o", Path(d) / "up.pretty", "--force")
        assert r.returncode == 0, r.stdout + r.stderr
        for f in lib.glob("*.kicad_mod"):                        # KiCad's own writer: same pads, drills and courtyard
            a, b = B.numbers(f.read_text()), B.numbers((Path(d) / "up.pretty" / f.name).read_text())
            assert sorted(a["pads"]) == sorted(b["pads"]) and a["courtyard"] == b["courtyard"], f.name   # KiCad sorts pads
        (Path(d) / "svg").mkdir()
        r = cli("fp", "export", "svg", lib, "-o", Path(d) / "svg")
        assert r.returncode == 0 and len(list((Path(d) / "svg").glob("*.svg"))) == len(list(lib.glob("*.kicad_mod"))), r.stdout + r.stderr


def test_a_through_hole_polygon_pads_hole_is_where_the_table_puts_it_in_pcbnew():
    # reviewer 2026-10-31: the custom pad's anchor (and so its drill) moved to the polygon's interior point
    if not pcbnew_python():
        return _skip("pcbnew (KiCad's Python module)")
    from tests.test_footprints_advanced import _poly_tht
    with tempfile.TemporaryDirectory() as d:
        lib = Path(d) / "poly.pretty"
        lib.mkdir()
        at = {"P0": (2.0, 0.0), "P1": (-2.0, 3.0)}
        for name, (x, y) in at.items():
            (lib / f"{name}.kicad_mod").write_text(F.build(dict(_poly_tht(x, y), name=name))[0])
        got = pcbnew_job("load", lib=str(lib))
        for name, (x, y) in at.items():
            p1 = next(p for p in got[name]["pads"] if p["num"] == "1")
            assert p1["custom"] and abs(p1["x"] - x) < 1e-4 and abs(p1["y"] - y) < 1e-4, (name, p1)
            assert p1["drill"] == [1.0, 1.0] and p1["w"] >= 1.0 + 0.3 - 1e-6, (name, p1)


def test_drc_on_a_board_of_generated_footprints():
    if not pcbnew_python():
        return _skip("pcbnew (KiCad's Python module)")
    with tempfile.TemporaryDirectory() as d:
        lib = _all_footprints(d, random_specs=0)
        found = pcbnew_job("drc", lib=str(lib), board=str(Path(d) / "drc.kicad_pcb"))
        assert found, "no DRC findings read: the known ones (thermal-via drills, USB-C) are missing, so the report " \
                      "was not parsed"
        bad = {}
        for name, kinds in found.items():
            t = sexpr.parse((lib / f"{name}.kicad_mod").read_text())
            nums = [p[1] for p in sexpr.find(t, "pad") if p[1]]
            ok = set().union(*[k for rx, k in ALLOWED if re.search(rx, name)])
            if len(nums) != len(set(nums)):                       # repeated pad numbers: KiCad wants them joined
                ok.add("unconnected_items")
            if set(kinds) - ok:
                bad[name] = kinds
        assert not bad, bad
        print(f"      DRC: {len(list(lib.glob('*.kicad_mod')))} footprints, findings only where KiCad's library has them too")


# ---------------------------------------------------------------- kicad_export
def _export(d):
    from tests import test_kicad_export as TX
    plan, items, http = TX._plan(d, api_key="sk-test")
    out = Path(d) / "k"
    rep = X.export(plan, out, "lib", include_held=True, http=http, log=lambda *a: None)
    assert not rep["errors"], rep["errors"]
    return out, rep


def test_exported_fp_lib_table_resolves_in_a_kicad_project():
    if not pcbnew_python():
        return _skip("pcbnew (KiCad's Python module)")
    with tempfile.TemporaryDirectory() as d:
        out, rep = _export(d)
        fps = sorted(rep["footprints"])
        assert fps
        if (kicad_major() or 7) < 8:                             # converted-back footprints are KiCad 8 format
            for f in (out / "lib.pretty").glob("*.kicad_mod"):
                f.write_text(kicad7_footprint(f.read_text()))
        got = pcbnew_job("libcheck", dir=str(out), lib="lib", fps=fps, bogus=True)
        assert all(not got.get(f"lib:{n}") for n in fps), (got, kicad_major())   # every footprint found and equal
        assert "lib_footprint_issues" in got.get("not_a_lib:X", {}) and "lib_footprint_issues" in got.get("lib:NOT_IN_LIB", {}), got


def _sym_facts(text):
    t = sexpr.parse(text)
    out = {}
    for s in sexpr.find(t, "symbol"):
        pins = sorted((str(u[1]), p[1], p[2], tuple(float(v) for v in sexpr.first(p, "at")[1:]), float(sexpr.first(p, "length")[1]),
                       sexpr.first(p, "name")[1], sexpr.first(p, "number")[1], "hide" in p or bool(sexpr.first(p, "hide")))
                      for u in sexpr.find(s, "symbol") for p in sexpr.find(u, "pin"))
        gfx = sorted((str(u[1]), c[0]) for u in sexpr.find(s, "symbol") for c in u[2:] if isinstance(c, list) and c[0] != "pin")
        out[s[1]] = (pins, gfx, {p[1]: p[2] for p in sexpr.find(s, "property")})
    return out


def test_exported_symbols_load_in_kicad():
    if not kicad_cli():
        return _skip("kicad-cli")
    with tempfile.TemporaryDirectory() as d:
        out, rep = _export(d)
        src = out / "lib.kicad_sym"
        if (kicad_major() or 7) >= 8:
            r = cli("sym", "upgrade", src, "-o", Path(d) / "up.kicad_sym", "--force")
            assert r.returncode == 0, r.stdout + r.stderr
            ours = _sym_facts(src.read_text())
        else:                                                    # KiCad 7 refuses the KiCad 8 format as such ...
            assert cli("sym", "upgrade", src, "-o", Path(d) / "x.kicad_sym").returncode != 0
            k7 = Path(d) / "k7.kicad_sym"                        # ... and must load everything else we write
            k7.write_text(kicad7_symbols(src.read_text()))
            r = cli("sym", "upgrade", k7, "-o", Path(d) / "up.kicad_sym", "--force")
            assert r.returncode == 0, r.stdout + r.stderr
            src, ours = k7, _sym_facts(k7.read_text())
        back = _sym_facts((Path(d) / "up.kicad_sym").read_text())
        assert ours == back and len(ours) == rep["symbols"] >= 4, (set(ours) ^ set(back))
        (Path(d) / "svg").mkdir()
        r = cli("sym", "export", "svg", src, "-o", Path(d) / "svg")
        assert r.returncode == 0 and len(list((Path(d) / "svg").glob("*.svg"))) == len(ours), r.stdout + r.stderr


def test_easyeda_symbols_load_in_kicad():
    if not kicad_cli():
        return _skip("kicad-cli")
    data = json.loads((ROOT / "tests" / "fixtures" / "easyeda" / "C2002.json").read_text())
    ds = dict((data.get("result") or data)["dataStr"])
    ds["shape"] = list(ds["shape"]) + [SLANTED_PIN]
    text = EC.symbol_to_kicad(ds, "EE", "")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "ee.kicad_sym"
        f.write_text(text if (kicad_major() or 7) >= 8 else kicad7_symbols(text))
        r = cli("sym", "upgrade", f, "-o", Path(d) / "up.kicad_sym", "--force")
        assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------- kicad_import and the PcbLib round trip
def test_kicad_import_reads_pads_like_pcbnew():
    if not pcbnew_python():
        return _skip("pcbnew (KiCad's Python module)")
    with tempfile.TemporaryDirectory() as d:
        lib = _all_footprints(d)
        got = pcbnew_job("load", lib=str(lib))
        n = 0
        for f in sorted(lib.glob("*.kicad_mod")):
            fp, _ = K.convert_footprint(f.read_text())
            kp = [p for p in got[f.stem]["pads"] if p["attr"] != 1 or {"F.Cu", "B.Cu"} & set(p["layers"])]   # 1 = SMD
            assert len(kp) == len(fp["pads"]), (f.stem, len(kp), len(fp["pads"]))
            for k, a in zip(kp, fp["pads"]):
                n += 1
                assert k["num"] == a["designator"] and abs(k["x"] - a["x"]) < 1e-3 and abs(-k["y"] - a["y"]) < 1e-3, (f.stem, k, a)
                if k["attr"] in (0, 3):                                   # PTH / NPTH: same hole
                    assert abs(min(k["drill"]) - a["hole"]) < 1e-3 and (k["attr"] == 0) == a["plated"], (f.stem, k, a)
        print(f"      kicad_import: {n} pads as pcbnew reads them")


def test_pcblib_converted_back_loads_in_pcbnew():
    if not pcbnew_python():
        return _skip("pcbnew (KiCad's Python module)")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "gen.pretty").mkdir()
        (d / "back.pretty").mkdir()
        for p in SPECS:
            s = json.loads(p.read_text())
            (d / "gen.pretty" / f"{s['name']}.kicad_mod").write_text(F.build(s)[0])
            fp, _ = AL.footprint_from_spec(s)
            AL.write_pcblib(d / "x.PcbLib", [fp])
            txt = X.footprint_from_pcblib(d / "x.PcbLib", s["name"], [])
            (d / "back.pretty" / f"{s['name']}.kicad_mod").write_text(txt if (kicad_major() or 7) >= 8 else kicad7_footprint(txt))
        a, b = pcbnew_job("load", lib=str(d / "gen.pretty")), pcbnew_job("load", lib=str(d / "back.pretty"))
        key = lambda p: (p["num"], round(p["x"], 3), round(p["y"], 3))
        for name in a:
            assert b[name] is not None, name
            pa = sorted((p for p in a[name]["pads"] if {"F.Cu", "B.Cu"} & set(p["layers"])), key=key)
            pb = sorted((p for p in b[name]["pads"] if {"F.Cu", "B.Cu"} & set(p["layers"])), key=key)
            assert [key(p) for p in pa] == [key(p) for p in pb], name
            for x, y in zip(pa, pb):
                assert x["attr"] == y["attr"] and abs(min(x["drill"]) - min(y["drill"])) < 1e-3, (name, x, y)


# ---------------------------------------------------------------- STEP through KiCad's OpenCascade reader
STEP_SET = ["R_0603_1608Metric", "D_SMA", "SOIC-8_3.9x4.9mm_P1.27mm", "SOT-223-3_TabPin2", "LQFP-48_7x7mm_P0.5mm",
            "QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm", "BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD",
            "PinHeader_2x05_P2.54mm_Vertical", "JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical", "USB_C_Receptacle_HRO_TYPE-C-31-M-12"]


def test_step_models_load_in_kicad():
    if not (kicad_cli() and pcbnew_python()):
        return _skip("kicad-cli / pcbnew")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "gen.pretty").mkdir()
        models, solids = {}, 0
        for name in STEP_SET:
            s = json.loads(B.spec_path(name).read_text())
            (d / "gen.pretty" / f"{name}.kicad_mod").write_text(F.build(s)[0])
            text, _ = SG.generate(s)
            (d / f"{name}.step").write_text(text)
            models[name] = str(d / f"{name}.step")
            solids += text.count("MANIFOLD_SOLID_BREP(")
        pcbnew_job("step", lib=str(d / "gen.pretty"), board=str(d / "m.kicad_pcb"), models=models)
        r = cli("pcb", "export", "step", d / "m.kicad_pcb", "-o", d / "out.step", "-f")
        log = r.stdout + r.stderr
        assert r.returncode == 0 and "readSTEP() failed" not in log and "No model" not in log, log[-3000:]
        assert (d / "out.step").read_text(errors="replace").count("MANIFOLD_SOLID_BREP(") == solids + 1   # + the board


# ---------------------------------------------------------------- offline regressions for what the KiCad runs found
SLANTED_PIN = ("P~show~1~9~380~315~0~gge9p~0^^380~315^^M 380 315 L 389 320~#880000^^1~0~0~0~SL~start~~~#0000FF"
               "^^1~0~0~0~9~end~~~#0000FF")


def test_import_takes_unlocked_footprint_text():
    """KiCad 6/7 write (at x y [rot] unlocked) on footprint text (48 of 2291 KiCad 7.0.11 library footprints sampled):
    kicad_import raised ValueError on every one of them."""
    src = ('(footprint "T" (version 20221018) (layer "F.Cu")\n'
           '  (fp_text reference "REF**" (at 0 -3.7 unlocked) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))\n'
           '  (fp_text value "T" (at 0 3.7 90 unlocked) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))\n'
           '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))\n')
    fp, _ = K.convert_footprint(src)
    rot = {t[0]: t[5] for t in fp["texts"]}
    assert rot == {".Designator": 0.0, ".Comment": 90.0}, fp["texts"]


def test_import_takes_a_degenerate_arc():
    """KiCad's USB_A_CUI_UJ2-ADH-TH_Horizontal_Stacked has an fp_arc with start = mid = end; the import failed."""
    src = ('(footprint "T" (version 20221018) (layer "F.Cu")\n'
           '  (fp_arc (start 2.97 10.07) (mid 2.97 10.07) (end 2.97 10.07) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))\n'
           '  (fp_arc (start 0 0) (mid 1 0) (end 2 0) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))\n'
           '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))\n')
    notes = []
    fp, _ = K.convert_footprint(src, notes=notes)
    assert [t[:4] for t in fp["tracks"]] == [(0.0, 0.0, 2.0, 0.0)] and not fp["arcs"], fp["tracks"]
    assert any("degenerate arc" in n for n in notes), notes


def test_kicad7_symbol_description():
    """KiCad 7 symbol libraries keep the description in ki_description (all 2548 symbols sampled lost it)."""
    lib = sexpr.parse('(kicad_symbol_lib (version 20220914) (symbol "X" (property "Reference" "U" (at 0 0 0)) '
                      '(property "Value" "X" (at 0 0 0)) (property "ki_description" "Quad op-amp" (at 0 0 0)) '
                      '(symbol "X_1_1" (pin input line (at 0 0 0) (length 2.54) (name "A") (number "1")))))')
    sym = K.convert_symbol([K._find_symbol(lib, "X")])
    assert sym["description"] == "Quad op-amp" and ("Description", "Quad op-amp") in sym["params"], sym
    assert dict(X.plan_fields({"name": "X", "parameters": []}, "lib", None, {"ki_description": "Quad op-amp"}))["Description"] == "Quad op-amp"


def test_easyeda_slanted_pin_is_drawn_square():
    """KiCad only takes pin angles 0/90/180/270; one slanted EasyEDA pin path made the whole library fail to load
    (kicad-cli: "Unable to load library")."""
    notes = []
    text = EC.symbol_to_kicad({"head": {"x": 400, "y": 300}, "shape": [SLANTED_PIN]}, "S", notes=notes)
    at = sexpr.first(sexpr.first(sexpr.first(sexpr.first(sexpr.parse(text), "symbol"), "symbol"), "pin"), "at")
    assert at[3] == "0" and any("slanted" in n for n in notes), (at, notes)


def test_bga_smd_land_copper_spacing_is_refused():
    """The 0.1 mm pad-gap guard looked at the land, not the copper an SMD-defined land grows to: 0.28 mm land at
    0.4 mm pitch passed with 0.38 mm copper (0.02 mm gaps), and `footprint gen` wrote it without a word."""
    s = {"name": "B", "package_type": "bga", "rows": 4, "cols": 4, "pitch": 0.4, "land_diameter": 0.28, "land_style": "SMD",
         "body_length": {"nom": 2}, "body_width": {"nom": 2}, "pin_count": 16}
    # The land itself fits the pitch, so it is built copper-defined, and the footprint's notes say why
    # (TPS628436YKA, live benchmark 2026-10-19: 0.2 mm land at 0.35 mm read as SMD-defined)
    rep = F.build(s)[1]
    assert rep["copper"] == 0.28 and rep["land_style"] == "NSMD", rep
    assert any("needs 0.38 mm copper" in n for n in rep["notes"]), rep["notes"]
    try:
        F.build(dict(s, land_diameter=0.32))                    # 0.32 at 0.4 mm leaves 0.08 mm even copper-defined
        raise AssertionError("built")
    except ValueError as e:
        assert "copper 0.42" in str(e), e


def test_reference_text_clears_silk_past_the_courtyard():
    """Density C pulls the courtyard inside the silk outline; REF** sat on the silk line (KiCad DRC: silk_overlap)."""
    s = json.loads(B.spec_path("LED_0603_1608Metric").read_text())
    for dens in "ABC":
        t = sexpr.parse(F.build(s, dens)[0])
        ref = next(g for g in sexpr.find(t, "fp_text") if g[1] == "reference")
        y = float(sexpr.first(ref, "at")[2])
        silk = max(abs(float(sexpr.first(g, k)[2])) for g in sexpr.find(t, "fp_line") if sexpr.first(g, "layer")[1] == "F.SilkS" for k in ("start", "end"))
        assert abs(y) - 0.5 - 0.075 >= silk + F.SILK_W / 2, (dens, y, silk)
    t = sexpr.parse(F.build(json.loads(B.spec_path("SOT-23").read_text()))[0])     # density B: KiCad's spot (-2.4) sat on
    y = float(sexpr.first(next(g for g in sexpr.find(t, "fp_text") if g[1] == "reference"), "at")[2])   # the pin-1 triangle
    assert -2.6 < y < -2.4, y                                    # (top -1.90): just clear of it, not moved further


def test_bench_numbers_take_a_drill_offset():
    """(drill 0.4 (offset x y)) in KiCad's USB_C_Receptacle_CNCTech_C-ARA1-AK51X crashed footprint_bench.numbers."""
    src = '(footprint "T" (pad "1" thru_hole circle (at 0 0) (size 0.65 0.65) (drill 0.4 (offset 0.1 0)) (layers "*.Cu" "*.Mask")))'
    assert B.numbers(src)["pads"] == [["1", 0.0, 0.0, 0.65, 0.65, 0.4]]


if __name__ == "__main__":
    from tests import run_all
    sys.exit(run_all.main(["test_kicad_real"]))
