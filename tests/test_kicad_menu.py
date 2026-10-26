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


clean_all(globals())        # R-G2-8: the scratch folders a test makes go when it ends
