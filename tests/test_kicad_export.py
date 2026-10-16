"""Offline tests for ecadlib.kicad_export (and sexpr's quote-keeping writer). Run:  python3 -m tests.test_kicad_export

Plans come from tests.test_librarian's fake KiCad library / EasyEDA / datasheet-reader run, so every symbol path is
covered: KiCad symbol reused, EasyEDA converted again, datasheet-reader SchLib read back. The KiCad 10 fixtures
check the KiCad 8 down-conversion. If kicad-cli is installed, the outputs are also loaded by KiCad itself."""
import copy, json, os, re, shutil, subprocess, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_lib as AL, cli, datasheet as D, footprint_check as FC, kicad_export as X, kicad_import as K, sexpr
from tests import test_datasheet as TD, test_librarian as TL

FIX = Path(__file__).parent / "fixtures" / "kicad"
FPS = Path(__file__).parent.parent / "out" / "footprints" / "ecadlib.pretty"


def _plan(d, api_key=None):
    """Librarian run on test_librarian's BOM; with api_key the datasheet reader is mocked (DSPART1 held: verify)."""
    ext = copy.deepcopy(TD.GOOD)
    with mock.patch.object(D, "call", lambda headers, body, key, timeout=600: (ext, TD.message(ext))):
        items, http = TL.run(d, api_key=api_key)
    return Path(d) / "out" / "plan.json", items, http


def _syms(path):
    t = sexpr.parse(Path(path).read_text())
    assert t[0] == "kicad_symbol_lib" and sexpr.first(t, "version")[1] == X.SYM_VERSION
    return {s[1]: s for s in sexpr.find(t, "symbol")}


def _props(sym):
    return {p[1]: p[2] for p in sexpr.find(sym, "property")}


def _pins(sym):
    return {sexpr.first(p, "number")[1]: (p[1], p[2], tuple(sexpr.first(p, "at")[1:]), sexpr.first(p, "length")[1], sexpr.first(p, "name")[1])
            for u in sexpr.find(sym, "symbol") for p in sexpr.find(u, "pin")}


def test_sexpr_keeps_quotes_and_escapes():
    text = '(a "x \\"q\\" \\\\ y" b (c "" d (e "line\\nbreak")) "(paren)")'
    t = sexpr.parse(text, keep_quotes=True)
    assert isinstance(t[1], sexpr.Q) and not isinstance(t[2], sexpr.Q) and t[1] == 'x "q" \\ y'
    again = sexpr.parse(sexpr.dumps(t), keep_quotes=True)
    assert again == t and [type(v) for v in again[3]] == [type(v) for v in t[3]]
    assert sexpr.first(again[3], "e")[1] == "line\nbreak" and again[4] == "(paren)"
    assert sexpr.parse(text) == t                                      # same values without keep_quotes
    assert sexpr.quote('a"b\\c\n') == '"a\\"b\\\\c\\n"'


def test_kicad10_symbol_to_kicad8():
    chain, _ = K.load_symbol(f"{FIX / 'Regulator_Linear_AMS1117.kicad_sym'}:AMS1117-3.3", None, keep_quotes=True)
    assert len(chain) == 2                                             # AMS1117-3.3 extends AP1117-15
    sym = X.sym_kicad8(X.flatten(chain, "MY PART"))
    text = sexpr.dumps(sym)
    for tok in ("(hide yes)", "show_name", "do_not_autoplace", "embedded_fonts", "in_pos_files", "extends"):
        assert tok not in text, tok
    t = sexpr.parse(text)
    assert t[1] == "MY PART" and all(u[1].startswith("MY PART_") for u in sexpr.find(t, "symbol"))
    props = _props(t)
    assert props["Value"] == "AMS1117-3.3" and props["Footprint"].endswith("SOT-223-3_TabPin2"), props
    fp = next(p for p in sexpr.find(t, "property") if p[1] == "Footprint")
    assert "hide" in sexpr.first(fp, "effects")                       # (hide yes) moved into effects
    base = chain[-1]
    n = lambda s: sum(1 for u in sexpr.find(s, "symbol") for _ in sexpr.find(u, "pin"))
    assert n(t) == n(base) == 3
    pin = X.sym_kicad8(["pin", "power_in", "line", ["at", "0", "0", "0"], ["length", "0"], ["hide", "yes"], ["name", sexpr.Q("V")]])
    assert pin[:6] == ["pin", "power_in", "line", ["at", "0", "0", "0"], ["length", "0"], "hide"], pin
    assert X.sym_kicad8(["pin_numbers", ["hide", "yes"]]) == ["pin_numbers", "hide"]
    assert X.sym_kicad8(["pin_names", ["offset", "0"], ["hide", "no"]]) == ["pin_names", ["offset", "0"]]
    assert X.sym_kicad8(["fill", ["type", "hatch"]]) == ["fill", ["type", "none"]]
    assert X.sym_kicad8(["symbol", "P", ["power", "global"]]) == ["symbol", "P", ["power"]]


def test_kicad10_footprint_to_kicad8_and_model_path():
    notes = []
    t, models = X.footprint_tree((FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text(), "USB_C_16P", "My_Lib", notes)
    text = sexpr.dumps(t)
    assert t[1] == "USB_C_16P" and sexpr.first(t, "version")[1] == str(X.FP_VERSION)
    for tok in ("embedded_fonts", "duplicate_pad_numbers_are_jumpers", "generator_version"):
        assert tok not in text, tok
    assert models == [("Connector_USB.3dshapes/USB_C_Receptacle_HRO_TYPE-C-31-M-12.step", "USB_C_Receptacle_HRO_TYPE-C-31-M-12.step")]
    assert sexpr.first(t, "model")[1] == "${KIPRJMOD}/My_Lib.3dshapes/USB_C_Receptacle_HRO_TYPE-C-31-M-12.step"
    assert _props(t)["Value"] == "USB_C_16P"
    orig = (FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text()
    assert sexpr.pads(sexpr.parse(text)) == sexpr.pads(sexpr.parse(orig)) and FC.check(text) == FC.check(orig)


def test_schlib_read_back_matches_kicad():
    """KiCad symbol -> Altium SchLib -> KiCad again: same pins (multi-unit + De Morgan), same body style split."""
    chain, _ = K.load_symbol(str(FIX / "ecadlib_demorgan.kicad_sym"), None)
    with tempfile.TemporaryDirectory() as d:
        f = AL.write_schlib(Path(d) / "x.SchLib", [K.convert_symbol(chain, "NAND2_DM")])
        notes = []
        back = sexpr.parse(sexpr.dumps(X.symbol_from_schlib(f, "NAND2_DM", notes)))
    orig, again = _pins(chain[0]), _pins(back)
    assert set(orig) == set(again), (orig, again)
    for num, (etype, shape, at, length, name) in orig.items():
        e2, s2, at2, l2, n2 = again[num]
        assert (shape, l2, n2) == (shape, length, name) and [float(v) for v in at2] == [float(v) for v in at], (num, orig[num], again[num])
        assert e2 == etype or (etype, e2) in (("power_in", "power_in"), ("unspecified", "passive")), (num, etype, e2)
    units = sorted(u[1] for u in sexpr.find(back, "symbol"))
    assert any(u.endswith("_1_2") for u in units) and any(u.startswith("NAND2_DM_2_") for u in units), units


def test_pcblib_read_back_matches_kicad_mod():
    """.kicad_mod -> PcbLib -> .kicad_mod again: same pads and the same footprint_check findings (SMD, NPTH, slots)."""
    for src in (FPS / "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod", FPS / "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm.kicad_mod",
                FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod"):
        text = src.read_text()
        fp, t = K.convert_footprint(text, src.stem)
        with tempfile.TemporaryDirectory() as d:
            f = AL.write_pcblib(Path(d) / "x.PcbLib", [fp])
            back = X.footprint_from_pcblib(f, src.stem, [])
        key = lambda ps: sorted((p["number"], p["type"], round(p["x"], 3), round(p["y"], 3), round(p["w"], 3), round(p["h"], 3)) for p in ps)
        a, b = key(sexpr.pads(sexpr.parse(text))), key(sexpr.pads(sexpr.parse(back)))
        assert a == b, (src.name, [x for x in a if x not in b], [x for x in b if x not in a])
        assert sorted(FC.check(back)) == sorted(FC.check(text)), (src.name, FC.check(back), FC.check(text))


def test_export_plan():
    with tempfile.TemporaryDirectory() as d:
        plan, items, http = _plan(d)
        before = plan.read_bytes()
        out = Path(d) / "kicad"
        rep = X.export(plan, out, "My Lib", http=http, log=lambda *a: None)
        assert plan.read_bytes() == before                                      # the plan is only read
        assert rep["library"] == "My_Lib" and not rep["errors"], rep["errors"]
        held = {n for n, i in items.items() if i["status"] == "held"}
        assert {s["part"] for s in rep["skipped"]} == held and "DSPART1" in held and "NOWHERE1" in held
        syms = _syms(out / "My_Lib.kicad_sym")
        assert list(syms) == sorted(syms) == sorted(X.kname(n) for n in items if n not in held)
        how = {r["part"]: r["symbol"] for r in rep["parts"]}
        assert how["RES-0603-10k"].startswith("KiCad Device:R (reused") and how["EEPART1"].startswith("EasyEDA C2002"), how
        p = _props(syms["RES-0603-10k"])
        assert p["Reference"] == "R" and p["Value"] == "10k" and p["Footprint"] == "My_Lib:R_0603_1608Metric", p
        assert p["Datasheet"] == "https://example.com/r.pdf" and p["MPN"] == "RC0603-10K" and p["Manufacturer"] == "ACME", p
        assert p["Description"] == "Resistor 10k 0603" and p["LCSC"] == "C1001", p
        assert list(p)[:5] == ["Reference", "Value", "Footprint", "Datasheet", "Description"]
        assert all(u[1].startswith("RES-0603-10k_") for u in sexpr.find(syms["RES-0603-10k"], "symbol"))
        ee = _pins(syms["EEPART1"])                                             # EasyEDA: same pins as the SchLib
        _, sl = AL.read_schlib(Path(d) / "out" / items["EEPART1"]["symbol"]["file"])
        assert set(ee) == {r["num"] for r in sl[0]["records"] if r.get("RECORD") == "2"} == {"1", "2", "3"}
        assert _props(syms["EEPART1"])["Footprint"] == f"My_Lib:{X.kname(items['EEPART1']['footprint']['ref'])}"
        for n, it in items.items():                                             # each footprint file: name = stem, checks as released
            if n in held:
                continue
            f = out / "My_Lib.pretty" / f"{X.kname(it['footprint']['ref'])}.kicad_mod"
            t = sexpr.parse(f.read_text())
            assert t[1] == f.stem and rep["footprints"][f.stem]["check"] == "same as release check", (n, rep["footprints"].get(f.stem))
            assert set(_pins(syms[X.kname(n)])) <= {p[1] for p in sexpr.find(t, "pad")}, n
        for k, uri in (("sym", "My_Lib.kicad_sym"), ("fp", "My_Lib.pretty")):
            tab = sexpr.parse((out / f"{k}-lib-table").read_text())
            assert tab[0] == f"{k}_lib_table" and sexpr.first(sexpr.first(tab, "lib"), "uri")[1] == "${KIPRJMOD}/" + uri
        assert (out / "NOTICE.txt").exists() and json.loads((out / "kicad_export.json").read_text())["symbols"] == len(syms)
        out2 = Path(d) / "kicad2"                                               # deterministic
        X.export(plan, out2, "My Lib", http=http, log=lambda *a: None)
        files = lambda o: {str(f.relative_to(o)): f.read_bytes() for f in sorted(o.rglob("*")) if f.is_file()}
        assert files(out) == files(out2)


def test_include_held_reads_datasheet_schlib_back():
    with tempfile.TemporaryDirectory() as d:
        plan, items, http = _plan(d, api_key="sk-test")
        assert items["DSPART1"]["status"] == "held" and items["DSPART1"]["source"]["symbol"].startswith("datasheet:")
        out = Path(d) / "kicad"
        rep = X.export(plan, out, "lib", include_held=True, http=http, log=lambda *a: None)
        assert not rep["errors"] and not rep["skipped"], (rep["errors"], rep["skipped"])
        row = next(r for r in rep["parts"] if r["part"] == "DSPART1")
        assert "SchLib read back" in row["symbol"], row
        sym = _syms(out / "lib.kicad_sym")["DSPART1"]
        pins = _pins(sym)
        want = {str(p["num"]): p for p in TD.GOOD["pins"]}
        assert set(pins) == set(want), (pins, want)
        for num, p in want.items():                                             # name and direction survive the round trip
            assert pins[num][4] == p["name"], (num, pins[num], p)
            side = {"0": "left", "180": "right", "270": "top", "90": "bottom"}[pins[num][2][2]]
            assert side == p.get("side", "left"), (num, pins[num], p)
        assert _props(sym)["Footprint"] == f"lib:{X.kname(items['DSPART1']['footprint']['ref'])}"
        nowhere = next(r for r in rep["parts"] if r["part"] == "NOWHERE1")
        assert nowhere["symbol"] is None and any("no symbol in the plan" in n for n in nowhere["notes"]), nowhere
        rep2 = X.export(plan, out, "lib", http=http, log=lambda *a: None)       # re-export without held: their files go
        assert not (out / "lib.pretty" / f"{X.kname(items['DSPART1']['footprint']['ref'])}.kicad_mod").exists()
        assert "DSPART1" not in _syms(out / "lib.kicad_sym") and not rep2["errors"]


def test_plan_fields_escaping_and_names():
    with tempfile.TemporaryDirectory() as d:
        plan, items, http = _plan(d)
        data = json.loads(plan.read_text())
        it = next(i for i in data if i["name"] == "RES-0603-10k")
        it["name"] = "RES 10k/0603"
        it["description"] = 'Resistor "10k" C:\\path\nsecond line'
        it["parameters"].append({"name": "Note (int'l)", "value": "a\\b \"c\""})
        p2 = Path(d) / "out" / "plan2.json"
        p2.write_text(json.dumps(data))
        X.export(p2, Path(d) / "k", "x", http=http, log=lambda *a: None)
        syms = _syms(Path(d) / "k" / "x.kicad_sym")
        p = _props(syms["RES_10k_0603"])
        assert p["Description"] == 'Resistor "10k" C:\\path\nsecond line' and p["Note (int'l)"] == 'a\\b "c"', p
    assert X.kname("A:B/C\\D E") == "A_B_C_D_E"


def test_cli_registered_and_runs():
    assert cli.COMMANDS["kicad-export"][0] == "ecadlib.kicad_export"
    with tempfile.TemporaryDirectory() as d:
        plan, items, http = _plan(d)
        with mock.patch.object(X, "CachedHTTP", lambda *a, **k: http):
            rc = cli.main(["kicad-export", str(plan), "-o", str(Path(d) / "k"), "--name", "cli_lib", "--offline", "--cache", str(Path(d) / "c")])
        assert rc == 0 and (Path(d) / "k" / "cli_lib.kicad_sym").exists() and (Path(d) / "k" / "cli_lib.pretty").is_dir()


def test_kicad_cli_loads_outputs():
    """Only when kicad-cli is installed: KiCad itself must load the symbol library and every footprint.
    The export is KiCad 8 format: KiCad 7 refuses the symbol library as such (and footprints converted back from a
    PcbLib), so with KiCad 7 the KiCad 7 copies from tests.test_kicad_real are loaded instead (that file checks more)."""
    from tests import test_kicad_real as KR
    exe = shutil.which("kicad-cli")
    if not exe:
        print("      (kicad-cli not installed: skipped)")
        return
    with tempfile.TemporaryDirectory() as d:
        plan, _, http = _plan(d)
        out = Path(d) / "k"
        X.export(plan, out, "lib", http=http, log=lambda *a: None)
        sym = out / "lib.kicad_sym"
        if (KR.kicad_major() or 7) < 8:
            print(f"      (kicad-cli {KR.kicad_major()}: loading the KiCad 7 copies of the KiCad 8 export)")
            sym = Path(d) / "k7.kicad_sym"
            sym.write_text(KR.kicad7_symbols((out / "lib.kicad_sym").read_text()))
            for f in (out / "lib.pretty").glob("*.kicad_mod"):
                f.write_text(KR.kicad7_footprint(f.read_text()))
        r = subprocess.run([exe, "sym", "upgrade", str(sym), "-o", str(Path(d) / "up.kicad_sym")], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        r = subprocess.run([exe, "fp", "upgrade", str(out / "lib.pretty"), "-o", str(Path(d) / "up.pretty")], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr


def test_reexport_keeps_step_put_in_3dshapes():
    """A STEP model the export could not fetch (offline) and the person put into <lib>.3dshapes/, as the note asks,
    survives the next export."""
    from ecadlib import release_check as RC
    with tempfile.TemporaryDirectory() as d:
        plan, items, http = _plan(d)
        it = items["RES-0603-10k"]
        km = RC._kicad_mod(it, plan.parent, RC._path(plan.parent, it["footprint"].get("file")))
        txt = km.read_text().rstrip()
        km.write_text(txt[:-1] + '\t(model "${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603_1608Metric.wrl")\n)\n')
        out = Path(d) / "kicad"
        rep = X.export(plan, out, "L", http=http, log=lambda *a: None)
        assert rep["models"]["R_0603_1608Metric.step"] is None, rep["models"]    # others: the Altium models (parity)
        step = out / "L.3dshapes" / "R_0603_1608Metric.step"
        step.parent.mkdir(exist_ok=True)
        step.write_text("ISO-10303-21; hand-placed")
        (out / "L.3dshapes" / "stale.step").write_text("x")
        rep = X.export(plan, out, "L", http=http, log=lambda *a: None)
        assert step.read_text() == "ISO-10303-21; hand-placed" and rep["models"]["R_0603_1608Metric.step"], rep["models"]
        assert not (out / "L.3dshapes" / "stale.step").exists()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
