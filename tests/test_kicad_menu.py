"""The ecadlib window in KiCad's PCB editor (ecadlib/kicad_menu.py + assets/kicad_action_plugin.py): what the window
runs, offline on the KiCad schematic fixture: check the BOM, fill the library, add a part (footprint to place)."""
import json, shutil, struct, zlib
from pathlib import Path

from ecadlib import altium_menu as AM, kicad_menu as KM
from tests.fixtures_lite import clean_all, scratch

FIX = Path(__file__).parent / "fixtures" / "kicad_sch"
PARTS = """Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet
"R1, R2, R30",3,10k,Resistor_SMD:R_0603_1608Metric,Yageo,RC0603FR-0710KL,,RES SMD 10K OHM 1% 1/10W 0603,https://example.com/rc.pdf
"R10, R20",2,4k7,Resistor_SMD:R_0402_1005Metric,Yageo,RC0402FR-074K7L,,RES SMD 4.7K OHM 1% 1/16W 0402,https://example.com/rc.pdf
"""


def _project():
    d = scratch("ecadlib-kmenu-")
    shutil.copytree(FIX, d, dirs_exist_ok=True)
    (d / "board.kicad_pcb").write_text("(kicad_pcb)")
    bom = d / KM.WORK / AM.MENU_BOM
    bom.parent.mkdir(parents=True)
    bom.write_text(PARTS)                    # descriptions and datasheets the offline sources cannot supply
    return d


def _job(d, action, **kw):
    job = d / KM.WORK / "jobs" / action
    res = KM.run_job(job, action, str(d / "board.kicad_pcb"), offline=True, **kw)
    assert (job / "exit.txt").is_file() and AM.read_ini(job / "result.ini")["result"]["status"] == res["status"]
    return res


def test_config_and_schematic_of_a_board():
    d = _project()
    cfg = KM.config(d / "board.kicad_pcb")
    assert cfg["workspace"] == (d / "ecadlib-work").resolve() and cfg["library"] == (d / "ecadlib").resolve()
    assert cfg["tool"] == "kicad" and cfg["name"] == "ecadlib"
    assert KM.schematic_of(d / "board.kicad_pcb") == d / "board.kicad_sch"
    assert KM.schematic_of(d / "board.kicad_sch") == d / "board.kicad_sch"
    (d / "board.kicad_sch").rename(d / "x.kicad_sch")
    try:
        KM.schematic_of(d / "board.kicad_pcb")
        raise AssertionError
    except ValueError as e:
        assert "save the schematic" in str(e)


def test_check_bom_then_fill_then_add_part():
    d = _project()
    res = _job(d, "check-bom")
    assert res["big"] == "5 lines" and res["offer_fill"] == "1" and Path(res["report"]).is_file(), res
    assert "not fitted|1|0" in [res[f"row{i}"] for i in range(1, res["rows"] + 1)]
    res = _job(d, "fill")
    assert res["status"] == "ok" and int(res["ready"]) >= 2, res
    assert res["big"].endswith("ready") and res["offer_queue"] == "1"        # LM358DR has no symbol offline
    lib = d / "ecadlib"
    assert (lib / "ecadlib.kicad_sym").is_file() and (lib / "ecadlib.pretty" / "R_0603_1608Metric.kicad_mod").is_file()
    names = {p["part"] for p in json.loads((lib / "kicad_export.json").read_text())["parts"]}
    assert {"RES-0603-10k", "RES-0402-4k7"} <= names, names
    bom = (d / KM.WORK / AM.MENU_BOM).read_text()
    assert "GRM188R71C105KA12D" in bom and "LM358DR" in bom and "CL05B104KO5NNNC" not in bom   # the DNP line stays out
    res = _job(d, "add-part", mpn="RC0603FR-0710KL", manufacturer="Yageo")
    assert res["status"] == "ready" and res["footprint"] == "R_0603_1608Metric", res
    assert "KiCad library" in res["message"]
    # the library keeps every part: the add re-exported the whole list, not just the one part
    names2 = {p["part"] for p in json.loads((lib / "kicad_export.json").read_text())["parts"]}
    assert names <= names2, (names, names2)
    res = _job(d, "check-bom")
    assert res["row1"].startswith("already in your library|") and int(res["row1"].split("|")[1]) >= 2, res


def test_open_only_reports_and_errors_are_one_line():
    d = _project()
    res = _job(d, "open", path=str(d / "board.kicad_sch"))
    assert res["status"] == "error" and "not a report" in res["message"]
    res = KM.run_job(d / KM.WORK / "jobs" / "x", "add-part", str(d / "board.kicad_pcb"), mpn="a;b", offline=True)
    assert res["status"] == "error" and "not a manufacturer part number" in res["message"] and "\n" not in res["message"]


def test_old_plugin_list_is_adopted():
    d = scratch("ecadlib-kmenu-")
    (d / KM.WORK).mkdir()
    (d / KM.WORK / "parts.csv").write_text("Designator,Qty,Value,Mfr. Part #\nP1,1,LM358DR,LM358DR\n")
    assert KM.adopt_old_list(KM.config(d)) == 1 and "LM358DR" in (d / KM.WORK / AM.MENU_BOM).read_text()
    assert KM.adopt_old_list(KM.config(d)) == 0


def test_toolbar_icon_is_a_png():
    for size in (24, 64):
        png = KM.icon_png(size)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (size, size)
        n = struct.unpack(">I", png[33:37])[0]
        raw = zlib.decompress(png[41:41 + n])
        assert len(raw) == size * (1 + 4 * size)


def test_cli_check_bom_for_the_schematic_editor():
    d = _project()
    import contextlib, io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert KM.main(["check-bom", str(d / "board.kicad_sch"), "out.csv", "--no-open", "--json"]) == 0
    assert json.loads(out.getvalue())["lines"] == 5


def _kicad_cfg(base, series="9.0"):
    c = Path(base) / series
    c.mkdir(parents=True)
    for kind, table in (("sym", "sym_lib_table"), ("fp", "fp_lib_table")):
        (c / f"{kind}-lib-table").write_text(f'({table}\n  (version 7)\n  (lib (name "Own")(type "KiCad")'
                                              f'(uri "/x/Own")(options "")(descr ""))\n)\n')
    return c


def test_use_in_every_project_puts_the_library_into_kicad():
    """The window's "Use in every project": the project's library goes into KiCad's own tables as ecadlib_<project>
    with a path variable, its 3D models are re-pointed at that variable (and stay so on the next fill), and the
    project's own ${KIPRJMOD} rows stay."""
    import os, re
    d = _project()
    base = d / "kicad-config"
    c = _kicad_cfg(base)
    old = os.environ.get("KICAD_CONFIG_HOME")
    os.environ["KICAD_CONFIG_HOME"] = str(base)
    try:
        res = _job(d, "install")
        assert res["status"] == "error" and "Nothing is in" in res["message"], res      # nothing built yet
        _job(d, "fill")
        res = _job(d, "install")
        assert res["status"] == "ready" and res["big"] == "ecadlib_board", res
        assert "ecadlib_board" in res["message"] and "Close KiCad" in res["fix"], res
        fp = (c / "fp-lib-table").read_text()
        assert '(name "Own")' in fp and '(uri "${ECADLIB_DIR_BOARD}/ecadlib.pretty")' in fp and '(name "ecadlib_board")' in fp, fp
        assert '(uri "${ECADLIB_DIR_BOARD}/ecadlib.kicad_sym")' in (c / "sym-lib-table").read_text()
        assert json.loads((c / "kicad_common.json").read_text())["environment"]["vars"]["ECADLIB_DIR_BOARD"] == \
            (d / "ecadlib").resolve().as_posix()
        models = lambda: [m for f in (d / "ecadlib" / "ecadlib.pretty").glob("*.kicad_mod")
                          for m in re.findall(r'\(model "([^"]+)"', f.read_text())]
        assert models() and all(m.startswith("${ECADLIB_DIR_BOARD}/ecadlib.3dshapes/") for m in models()), models()
        assert "${KIPRJMOD}/ecadlib/ecadlib.pretty" in (d / "fp-lib-table").read_text()     # the project's own row
        res = _job(d, "add-part", mpn="RC0603FR-0710KL", manufacturer="Yageo")         # a later build keeps them
        assert res["status"] == "ready", res
        assert all(m.startswith("${ECADLIB_DIR_BOARD}/ecadlib.3dshapes/") for m in models()), models()
        assert _job(d, "install")["status"] == "ready"                                   # again: no second row
        assert (c / "fp-lib-table").read_text().count('(name "ecadlib_board")') == 1
    finally:
        if old is None:
            os.environ.pop("KICAD_CONFIG_HOME", None)
        else:
            os.environ["KICAD_CONFIG_HOME"] = old


def test_use_in_every_project_without_kicad_tables_says_so():
    import os
    d = _project()
    base = d / "kicad-config"
    (base / "9.0").mkdir(parents=True)
    old = os.environ.get("KICAD_CONFIG_HOME")
    os.environ["KICAD_CONFIG_HOME"] = str(base)
    try:
        _job(d, "fill")
        cfg = KM.config(d / "board.kicad_pcb", offline=True)
        res = KM.install_everywhere(cfg, str(d / "board.kicad_pcb"), templates=[])
        assert res["status"] == "error" and "start KiCad" in res["fix"] and "sym-lib-table" in res["message"], res
    finally:
        if old is None:
            os.environ.pop("KICAD_CONFIG_HOME", None)
        else:
            os.environ["KICAD_CONFIG_HOME"] = old


def test_cli_fill_for_the_schematic_editor():
    """Tools > Generate Legacy Bill of Materials > ecadlib: fill library runs `kicad-menu fill "%I"` with the
    netlist KiCad wrote: the parts are built and the library is listed in the project's tables."""
    d = _project()
    import contextlib, io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = KM.main(["fill", str(d / "board_netlist_kicad7.xml"), str(d / "board"), "--offline"])
    text = out.getvalue()
    assert rc == 0 and "ready" in text and "Place > Add Symbol" in text, text
    assert (d / "ecadlib" / "ecadlib.kicad_sym").is_file() and "${KIPRJMOD}/ecadlib/ecadlib.pretty" in (d / "fp-lib-table").read_text()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert KM.main(["fill", str(d / "board_netlist_kicad7.xml"), "--offline", "--kicad", "7", "--json"]) == 0
    assert "(version 20220914)" in (d / "ecadlib" / "ecadlib.kicad_sym").read_text()           # KiCad 7's format


def _placed(text):
    """KiCad's schematic clipboard text -> (lib_symbols, the placed symbol)."""
    from ecadlib import sexpr
    tree = sexpr.parse("(clip " + text + ")")
    assert [c[0] for c in tree[1:]] == ["lib_symbols", "symbol"], text[:200]
    return tree[1], tree[2]


def test_add_part_job_leaves_the_symbol_for_the_schematic_editors_paste():
    """KiCad 9+'s schematic editor window copies JOB/paste.txt to the clipboard: KiCad's own Copy format (lib_symbols,
    then the symbol), linked to ecadlib:<symbol>, not annotated, footprint and part fields set, pins with uuids. Pasted
    with Ctrl+V in real KiCad 9.0.9 and 10.0.8 (the symbol placed as ecadlib:RES-0603-10k, R? / R3)."""
    from ecadlib import sexpr
    d = _project()
    _job(d, "fill")
    res = _job(d, "add-part", mpn="RC0603FR-0710KL", manufacturer="Yageo")
    job = d / KM.WORK / "jobs" / "add-part"
    assert res["paste"] == KM.PASTE and res["units"] == 1 and res["symbol"] == "RES-0603-10k", res
    assert AM.read_ini(job / "result.ini")["result"]["paste"] == KM.PASTE
    libs, sym = _placed((job / KM.PASTE).read_text(encoding="utf-8"))
    assert [str(s[1]) for s in sexpr.find(libs, "symbol")] == ["ecadlib:RES-0603-10k"]
    assert sexpr.first(sym, "lib_id")[1] == "ecadlib:RES-0603-10k" and sexpr.first(sym, "unit")[1] == "1"
    props = {str(p[1]): p for p in sexpr.find(sym, "property")}
    assert props["Reference"][2] == "R?" and props["Value"][2] == "10k"
    assert props["Footprint"][2] == "ecadlib:R_0603_1608Metric" and props["MPN"][2] == "RC0603FR-0710KL"
    assert not any(k.startswith("ki_") for k in props)
    x, y = (float(v) for v in sexpr.first(sym, "at")[1:3])
    lib_ref = next(p for p in sexpr.find(next(sexpr.find(libs, "symbol")), "property") if p[1] == "Reference")
    ref_at = sexpr.first(props["Reference"], "at")
    assert abs(float(ref_at[1]) - x - float(sexpr.first(lib_ref, "at")[1])) < 1e-6
    assert abs(float(ref_at[2]) - y + float(sexpr.first(lib_ref, "at")[2])) < 1e-6       # a library's y points up
    pins = list(sexpr.find(sym, "pin"))
    assert [p[1] for p in pins] == ["1", "2"] and all(sexpr.first(p, "uuid") for p in pins)
    # a held part leaves nothing to paste, and an old paste.txt of the same job folder goes
    res = KM.run_job(job, "add-part", str(d / "board.kicad_pcb"), mpn="NOPE-123", offline=True)
    assert res["status"] != "ready" and "paste" not in res and not (job / KM.PASTE).exists(), res


def test_schematic_paste_of_a_multi_unit_symbol_is_unit_a():
    d = scratch("ecadlib-kpaste-")
    lib = d / "ecadlib"
    lib.mkdir()
    (lib / "kicad_export.json").write_text(json.dumps({"parts": [
        {"part": "DUAL", "symbol_name": "DUAL-OPAMP"}, {"part": "KID", "symbol_name": "KID"}]}))
    pin = '(pin input line (at 0 0 0) (length 2.54) (name "~") (number "{}"))'
    (lib / "ecadlib.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20231120) (generator "ecadlib")\n'
        '(symbol "DUAL-OPAMP" (in_bom yes) (on_board no)'
        ' (property "Reference" "U" (at 0 5 0) (effects (font (size 1.27 1.27))))'
        ' (property "Value" "DUAL" (at 0 -5 90) (effects (font (size 1.27 1.27))))'
        ' (property "ki_keywords" "opamp" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))'
        f' (symbol "DUAL-OPAMP_0_1" {pin.format(8)} {pin.format(4)})'
        f' (symbol "DUAL-OPAMP_1_1" {pin.format(1)} {pin.format(2)} {pin.format(3)})'
        f' (symbol "DUAL-OPAMP_1_2" {pin.format(1)} {pin.format(2)} {pin.format(3)})'
        f' (symbol "DUAL-OPAMP_2_1" {pin.format(5)} {pin.format(6)} {pin.format(7)}))\n'
        '(symbol "KID" (extends "DUAL-OPAMP") (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))))')
    from ecadlib import sexpr
    text, units, name = KM.schematic_paste(KM.config(d), {"DUAL"})
    assert units == 2 and name == "DUAL-OPAMP"
    libs, sym = _placed(text)
    assert sorted(p[1] for p in sexpr.find(sym, "pin")) == ["1", "2", "3", "4", "8"]     # unit A and the shared pins
    assert sexpr.first(sym, "on_board")[1] == "no" and sexpr.first(sym, "in_bom")[1] == "yes"
    props = {str(p[1]): p for p in sexpr.find(sym, "property")}
    assert props["Reference"][2] == "U?" and "ki_keywords" not in props
    assert sexpr.first(props["Value"], "at")[1:] == ["25.4", "30.4", "90"]
    assert len(list(sexpr.find(next(sexpr.find(libs, "symbol")), "symbol"))) == 4          # the whole symbol, all units
    assert KM.schematic_paste(KM.config(d), {"KID"}) == ("", 0, "")                      # derived: not pasted
    assert KM.schematic_paste(KM.config(d), {"GONE"}) == ("", 0, "")
    assert KM.schematic_paste(KM.config(scratch("ecadlib-kpaste-")), {"DUAL"}) == ("", 0, "")   # no library


def test_global_nick():
    assert KM.global_nick("/p/My Board.kicad_pcb") == "ecadlib_my_board"
    assert KM.global_nick("/p/x/board.kicad_sch") == "ecadlib_board"


clean_all(globals())        # R-G2-8: the scratch folders a test makes go when it ends
