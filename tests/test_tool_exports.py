"""Offline tests for the Fusion Electronics / EAGLE (.lbr) and EasyEDA Pro (.elibz) exports. Run:
    python3 -m tests.test_tool_exports

Each writer is checked by reading its own output back and comparing it with the KiCad source pad by pad
(ecadlib.parity); the footprints in fixtures/kicad/tool_export are the hard cases a survey of KiCad's whole library
turned up. With a Python that can import pcbnew, KiCad's own importers read the files too (Eagle: KiCad 7+,
EasyEDA Pro: KiCad 8+); those checks skip elsewhere."""
import io, json, re, shutil, tempfile, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from ecadlib import cad_model as CM, eagle_export as EG, easyeda_pro_export as EZ, kicad_export as X, rules as RU

FIX = Path(__file__).parent / "fixtures" / "kicad"
HARD = sorted((FIX / "tool_export").glob("*.kicad_mod")) + [FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod"]
_RUN = {}


def _errors(diffs):
    return [d["msg"] for d in diffs if d["level"] == "error"]


def _export():
    """test_kicad_export's librarian plan, its kicad-export, then both tool exports from that folder (made once)."""
    if not _RUN:
        from tests import test_kicad_export as TX
        d = Path(tempfile.mkdtemp(prefix="ecadlib-tools-"))
        plan, _, http = TX._plan(d)
        krep = X.export(plan, d / "kicad", "lib", http=http, log=lambda *a: None)
        assert not krep["errors"], krep["errors"]
        eg = EG.export(plan, d / "fusion", "lib", http=http, log=lambda *a: None, kicad_dir=d / "kicad", second_reader=False)
        ez = EZ.export(plan, d / "easyeda", "lib", http=http, log=lambda *a: None, kicad_dir=d / "kicad", second_reader=False)
        _RUN.update(d=d, plan=plan, http=http, krep=krep, eagle=eg, easyeda=ez)
    return _RUN


# ---------------------------------------------------------------- footprints, one by one
def test_hard_footprints_read_back_the_same_in_eagle():
    assert len(HARD) >= 10
    for f in HARD:
        diffs = EG.check_footprint(f.read_text(), f.stem)
        assert not _errors(diffs), (f.name, _errors(diffs))


def test_hard_footprints_read_back_the_same_in_easyeda():
    for f in HARD:
        diffs = EZ.check_footprint(f.read_text(), f.stem)
        assert not _errors(diffs), (f.name, _errors(diffs))


def test_checks_catch_a_changed_pad():
    """The read-back is a real check: a pad moved, resized or tented after writing is reported."""
    text = (FIX / "tool_export" / "SOT-89-3.kicad_mod").read_text()
    fp = CM.footprint(text)
    pk, _, approx = EG.package(fp, "SOT89")
    smd = next(e for e in pk if e.tag == "smd" and e.get("name") == "1")
    smd.set("x", str(float(smd.get("x")) + 0.05))
    got = EG.read_package(ET.fromstring(ET.tostring(pk, encoding="unicode")))
    assert any("pad 1 position" in m for m in _errors(EG.compare_package(text, got, approx)))
    lines, approx = EZ.footprint_lines(fp, "SOT89")
    pad = next(l for l in lines if l[0] == "PAD" and l[5] == "3")
    pad[10] = ["RECT", pad[10][1] * 1.1, pad[10][2], pad[10][3]]
    pad[17] = EZ.NO_OPENING                    # tented
    errs = _errors(EZ.compare_footprint(text, EZ.parse_lines(EZ._jl(lines)), approx))
    assert any("pad 3 size" in m for m in errs) and any("pad 3 solder mask" in m for m in errs), errs


def test_layer_coverage_settles_openings_given_to_another_pad():
    """Mounting_Wuerth: paste windows sit on pads without paste; pad by pad they look like the pads' own paste,
    the layers themselves are the same, so it is not an error; a missing window on the layer is one."""
    text = (FIX / "tool_export" / "Mounting_Wuerth_WA-SMSE-ExternalM3_H14mm_9771140360.kicad_mod").read_text()
    pk, _, approx = EG.package(CM.footprint(text), "M")
    assert not _errors(EG.compare_package(text, EG.read_package(ET.fromstring(ET.tostring(pk))), approx))
    for e in [e for e in pk if e.tag == "polygon" and e.get("layer") == "31"][::2]:
        pk.remove(e)
    diffs = EG.compare_package(text, EG.read_package(ET.fromstring(ET.tostring(pk))), approx)
    # its pads are written approximately (curved custom pads as rectangles), so the layer difference is a warning
    assert any("paste F layer coverage" in d["msg"] for d in diffs), diffs


def test_eagle_package_conventions():
    text = (FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text()
    pk, names, approx = EG.package(CM.footprint(text), "USBC")
    tags = [e.tag for e in pk]
    assert tags.count("hole") == 2 and tags.count("pad") >= 4 and tags.count("smd") >= 12
    assert any(e.get("layer") == "46" for e in pk if e.tag == "wire")          # slots on Milling
    assert any("slot" in a for a in approx)
    assert names["A1"] and all(re.fullmatch(r"[A-Z0-9_@]+", n) for v in names.values() for n in v)
    text = (FIX / "tool_export" / "Banana_Cliff_FCR7350R_S16N-PC_Horizontal.kicad_mod").read_text()
    pk, _, _ = EG.package(CM.footprint(text), "B")
    hole = next(e for e in pk if e.tag == "hole")
    rings = [e for e in pk if e.tag == "circle" and e.get("x") == hole.get("x") and e.get("y") == hole.get("y")]
    assert {e.get("layer") for e in rings} == {"1", "16", "29", "30"}            # copper and opening bigger than the hole


def test_easyeda_footprint_records():
    text = (FIX / "tool_export" / "D_SMC-RM10_Universal_Handsoldering.kicad_mod").read_text()
    lines, approx = EZ.footprint_lines(CM.footprint(text), "D")
    assert lines[0] == ["DOCTYPE", "FOOTPRINT", "1.8"] and lines[1][0] == "HEAD"
    pads = [l for l in lines if l[0] == "PAD"]
    assert all(len(l) == 27 for l in pads)
    assert all(l[4] == 12 and l[9][0] == "ROUND" and l[15] == 1 for l in pads)    # multi-layer, plated
    assert all(l[10][0] == "POLY" for l in pads)                # copper off the hole: the copper outline itself
    text = (FIX / "tool_export" / "SOT-89-3.kicad_mod").read_text()
    smd = [l for l in EZ.footprint_lines(CM.footprint(text), "S")[0] if l[0] == "PAD"]
    assert smd and all(l[4] == 1 and l[9] is None for l in smd)          # top copper, no hole
    text = (FIX / "tool_export" / "D_SMC-RM10_Universal_Handsoldering.kicad_mod").read_text()
    attrs = {l[7]: l[8] for l in lines if l[0] == "ATTR"}
    assert attrs["Footprint"] == "D" and attrs["Designator"] == "U?"
    # units: mil, y up
    k = CM.footprint(text)["pads"][0]
    first = pads[0]
    assert abs(first[6] * EZ.MIL - k["x"]) < 1e-6 and abs(first[7] * EZ.MIL - k["y"]) < 1e-6
    # NPTH: unplated, a hole on the multi layer
    text = (FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text()
    lines, _ = EZ.footprint_lines(CM.footprint(text), "U")
    npth = [l for l in lines if l[0] == "PAD" and l[15] == 0]
    assert len(npth) == 2 and all(l[4] == 12 and l[9][0] == "ROUND" for l in npth)
    slots = [l for l in lines if l[0] == "PAD" and l[9] and l[9][0] == "SLOT"]
    assert len(slots) == 4 and all(l[14] == 0 for l in slots)


def test_easyeda_paths_and_arcs():
    """Arcs go counter-clockwise for a positive sweep (y up), as EasyEDA's own oval paste windows are drawn."""
    path = [255.872, -111.642, "ARC", -180, 314.93, -111.642, "L", 314.93, -158.887, "ARC", -180, 255.872, -158.887,
            "L", 255.872, -111.642]                          # from an EasyEDA Pro project: an oval pad's paste
    pts = EZ.path_parts(path)[0][0]
    ys = [p[1] / EZ.MIL for p in pts]
    assert max(ys) > -111.642 + 29 and min(ys) < -158.887 - 29        # both ends bulge outward: a stadium
    rr = EZ.path_round_rect(0, 0, 2, 1, 0.25, 30)
    back = EZ.path_parts(rr)[0][0]
    area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(back, back[1:] + back[:1]))) / 2
    assert abs(area - (2 * 1 - (4 - 3.14159265) * 0.25 ** 2)) < 2e-3
    g = CM.grown([(0, 0), (1, 0), (1, 1), (0, 1)], 0.1)
    assert len(g) == 8 and sum(1 for _, s in g if s) == 4 and all(abs(s - 90) < 1e-9 for _, s in g if s)


# ---------------------------------------------------------------- whole libraries
def test_eagle_library_from_a_plan():
    r = _export()
    rep, out = r["eagle"], r["d"] / "fusion"
    assert not rep["errors"], rep["errors"]
    text = (out / "lib.lbr").read_text()
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE eagle SYSTEM "eagle.dtd">')
    root = ET.fromstring(text.split("\n", 2)[2])
    assert root.tag == "eagle" and root.get("version") == EG.EAGLE_VERSION
    lib = root.find("drawing/library")
    assert len(lib.findall("packages/package")) == rep["counts"]["packages"] > 0
    assert len(lib.findall("devicesets/deviceset")) == rep["counts"]["devicesets"] > 0
    for ds in lib.findall("devicesets/deviceset"):
        for dev in ds.findall("devices/device"):
            assert dev.get("package") and dev.findall("connects/connect")
    back = EG.read_lbr(out / "lib.lbr")
    assert set(back["packages"]) == set(rep["packages"])
    assert json.loads((out / "eagle_export.json").read_text())["counts"] == rep["counts"]


def test_easyeda_library_from_a_plan():
    r = _export()
    rep, out = r["easyeda"], r["d"] / "easyeda"
    assert not rep["errors"], rep["errors"]
    with zipfile.ZipFile(out / "lib.elibz") as z:
        names = z.namelist()
        meta = json.loads(z.read("device.json"))
    assert set(meta) == {"symbols", "footprints", "devices"}
    assert len(meta["devices"]) == rep["counts"]["devices"] > 0
    for u, d in meta["devices"].items():                          # what KiCad's importer requires of every entry
        assert {"source", "version", "tags", "attributes", "title"} <= set(d) and re.fullmatch(r"[0-9a-f]{32}", u)
        a = d["attributes"]
        assert a["Symbol"] in meta["symbols"] and f"SYMBOL/{a['Symbol']}.esym" in names
        if a["Footprint"]:
            assert a["Footprint"] in meta["footprints"] and f"FOOTPRINT/{a['Footprint']}.efoo" in names
    for kind in ("symbols", "footprints"):
        assert all({"source", "version", "type", "tags", "title"} <= set(d) for d in meta[kind].values())
    back = EZ.read_elibz(out / "lib.elibz")
    assert set(back["footprints"]) == set(rep["packages"])
    # every line of every file is a JSON array; pins carry NAME / NUMBER
    for title, lines in back["symbols"].items():
        pins = EZ.symbol_pins(lines)
        assert pins and all(p["name"] is not None for p in pins.values()), title
    # the same plan writes the same file
    again = EZ.export(r["plan"], r["d"] / "easyeda2", "lib", http=r["http"], log=lambda *a: None,
                      kicad_dir=r["d"] / "kicad", second_reader=False)
    assert not again["errors"]
    with zipfile.ZipFile(out / "lib.elibz") as a, zipfile.ZipFile(r["d"] / "easyeda2" / "lib.elibz") as b:
        assert {n: a.read(n) for n in a.namelist()} == {n: b.read(n) for n in b.namelist()}


def test_easyeda_device_attributes():
    attrs = EZ.device_attrs({"Value": "10k", "MPN": "RC0603", "Manufacturer": "Yageo", "LCSC": "C25804",
                             "Datasheet": "https://x/y.pdf", "Tolerance": "1%", "Footprint": "lib:R", "ki_keywords": "r"},
                            "s" * 32, "f" * 32, "R?", "10k")
    assert attrs["Manufacturer Part"] == "RC0603" and attrs["Manufacturer"] == "Yageo"
    assert attrs["Supplier"] == "LCSC" and attrs["Supplier Part"] == "C25804" and attrs["Datasheet"] == "https://x/y.pdf"
    assert attrs["Tolerance"] == "1%" and "ki_keywords" not in attrs and attrs["Designator"] == "R?"
    assert attrs["Add into BOM"] == "yes" and attrs["Footprint"] == "f" * 32


def test_symbols_pin_by_pin():
    from ecadlib import sexpr
    t = sexpr.parse((FIX / "ecadlib_demorgan.kicad_sym").read_text())
    for sym in sexpr.find(t, "symbol"):
        if not re.search(r"_\d+_\d+$", str(sym[1])):
            cs = CM.symbol(sym)
            lines, approx = EZ.symbol_lines(cs, str(sym[1]), "U?")
            assert not _errors(EZ.compare_symbol(cs, EZ.symbol_pins(EZ.parse_lines(EZ._jl(lines))))), sym[1]
            gates, approx = EG.symbols(cs, EG.ename(str(sym[1])))
            assert gates


def test_cli_rules_release_gate_and_mcp():
    from ecadlib import cli, mcp_server as M, release_check as RC
    assert {"eagle-export", "easyeda-export"} <= set(cli.COMMANDS)
    r = _export()
    with mock.patch("sys.stdout", io.StringIO()) as so:
        assert cli.main(["easyeda-export", str(r["plan"]), "-o", str(r["d"] / "c1"), "--kicad", str(r["d"] / "kicad"),
                         "--no-kicad-check", "--name", "lib"]) == 0
        assert cli.main(["eagle-export", str(r["plan"]), "-o", str(r["d"] / "c2"), "--kicad", str(r["d"] / "kicad"),
                         "--no-kicad-check", "--name", "lib", "--json"]) == 0
    assert "lib.elibz" in so.getvalue() and '"file": "lib.lbr"' in so.getvalue()
    assert {"tool_export"} <= {t["name"] for t in M.TOOLS}
    # rules: hold | warn | off, default warn
    assert RU.DEFAULTS["release"]["eagle"] == RU.DEFAULTS["release"]["easyeda"] == "warn"
    d = Path(tempfile.mkdtemp())
    (d / "ecadlib.toml").write_text('[release]\neagle = "maybe"\n')
    try:
        RU.load(d / "ecadlib.toml")
        raise AssertionError("bad release.eagle accepted")
    except ValueError as e:
        assert "release.eagle" in str(e)
    # the release gate: a footprint that reads back differently is held (hold) or warned about (warn)
    bad = [{"level": "error", "msg": "pad 1 position: KiCad (0, 0) / EAGLE (1, 0)"}]
    plan = json.loads(r["plan"].read_text())
    items = [i for i in plan if i.get("status") == "ready"][:1]
    assert items
    for mode, held in (("hold", True), ("warn", False)):
        rules = RU.resolve(None, r["plan"].parent)
        rules["release"] = dict(rules["release"], eagle=mode, easyeda="off")
        with mock.patch.object(EG, "check_footprint", lambda *a, **k: bad):
            fails, warns, info = RC.check_item(json.loads(json.dumps(items[0])), r["plan"].parent, rules)
        assert any("pad 1 position" in str(f) for f in fails) == held, (mode, fails)
        assert held or any("eagle: pad 1 position" in w for w in warns)
        assert info.get("eagle") == "mismatch"


# ---------------------------------------------------------------- KiCad's own importers (skip without pcbnew)
def test_kicad_importers_read_the_written_libraries():
    if not EG.pcbnew_python():
        print("skip: no Python with pcbnew")
        return
    r = _export()
    rr = EG.kicad_reread(r["d"] / "fusion" / "lib.lbr", r["d"] / "rr1")
    assert rr and not rr.get("error"), rr
    assert set(rr["footprints"]) == set(r["eagle"]["packages"]), rr.get("errors")
    for name, (fp) in rr["footprints"].items():
        src = (r["d"] / "kicad" / "lib.pretty" / f"{r['eagle']['packages'][name]['from']}.kicad_mod").read_text()
        assert not _errors(EG.compare_kicad_reread(src, fp, r["eagle"]["packages"][name]["approximations"])), name
    rr = EG.kicad_reread(r["d"] / "easyeda" / "lib.elibz", r["d"] / "rr2")
    if rr.get("error") and "cannot read" in rr["error"]:
        print(f"skip: KiCad {rr.get('kicad')} has no EasyEDA Pro importer (KiCad 8+)")
        return
    assert not rr.get("error"), rr
    for name, fp in rr["footprints"].items():
        src = (r["d"] / "kicad" / "lib.pretty" / f"{name}.kicad_mod").read_text()
        assert not _errors(EZ.compare_kicad_reread(src, fp, r["easyeda"]["packages"][name]["approximations"])), name
    for f in HARD:                                                # the hard ones through KiCad's EasyEDA Pro importer
        lines, approx = EZ.footprint_lines(CM.footprint(f.read_text()), f.stem)
        lib = r["d"] / "hard" / f"{f.stem[:40]}.elibz"
        lib.parent.mkdir(exist_ok=True)
        EZ.write_elibz(lib, {}, {EZ.uid("fp", f.stem): (f.stem, lines)}, {})
        got = EG.kicad_reread(lib, r["d"] / "rr3" / f.stem[:40])
        assert f.stem in got["footprints"], (f.name, got.get("errors"))
        assert not _errors(EZ.compare_kicad_reread(f.read_text(), got["footprints"][f.stem], approx)), f.name


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
