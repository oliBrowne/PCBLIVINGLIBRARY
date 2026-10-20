"""Integration inputs and outputs (offline). Run:  python3 -m tests.test_integration_io

BOM inputs: KiCad schematics with hierarchical sheets, KiCad XML / S-expression netlists (fixtures written by KiCad
7.0.11's kicad-cli from tests/fixtures/kicad_sch/board.kicad_sch), CSV dialects, house column names, .xlsx without
openpyxl. Outputs: the KiCad database library (.kicad_dbl + SQLite) and the parts table export (CSV / JSON / XLSX).
Tests that need kicad-cli or KiCad's eeschema module skip cleanly when they are missing."""
import csv, json, shutil, sqlite3, subprocess, tempfile, zipfile
from pathlib import Path
from unittest import mock

import pandas as pd

from ecadlib import bom_inputs as BI, bom_triage as T, cli, kicad_dblib as KD, parts_export as PE, rules as RU, sexpr

FIX = Path(__file__).parent / "fixtures"
SCH = FIX / "kicad_sch"
BIN = FIX / "bom_inputs"
EESCHEMA = Path("/usr/lib/kicad/_eeschema.kiface")

# What KiCad 7.0.11 itself lists for board.kicad_sch (kicad-cli sch export netlist / python-bom), grouped
EXPECTED = [
    {"designator": "C1", "qty": "1", "value": "100nF", "footprint": "Capacitor_SMD:C_0402_1005Metric",
     "mpn": "CL05B104KO5NNNC", "manufacturer": "Samsung", "dnp": True},
    {"designator": "C10, C20", "qty": "2", "value": "1uF", "footprint": "Capacitor_SMD:C_0603_1608Metric",
     "mpn": "GRM188R71C105KA12D", "manufacturer": "Murata", "dnp": False},
    {"designator": "R1, R2, R30", "qty": "3", "value": "10k", "footprint": "Resistor_SMD:R_0603_1608Metric",
     "mpn": "RC0603FR-0710KL", "manufacturer": "Yageo", "dnp": False},
    {"designator": "R10, R20", "qty": "2", "value": "4k7", "footprint": "Resistor_SMD:R_0402_1005Metric",
     "mpn": "RC0402FR-074K7L", "manufacturer": "Yageo", "dnp": False},
    {"designator": "U1", "qty": "1", "value": "LM358", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
     "datasheet": "https://www.ti.com/lit/ds/symlink/lm358.pdf", "lcsc": "C7950", "mpn": "LM358DR",
     "manufacturer": "Texas Instruments", "dnp": False},
]


def _tmp():
    return Path(tempfile.mkdtemp(prefix="ecadlib_io_"))


# ------------------------------------------------------------------ KiCad schematic / netlist inputs
def test_kicad_sch_hierarchy_matches_kicad():
    lines, mapping = T.read_bom(SCH / "board.kicad_sch")
    assert lines == EXPECTED, lines                      # R10/R20: one sheet file used twice; U1: 2 units, once
    assert mapping["mpn"] == "MPN | Mfr Part #" and mapping["designator"] == "Reference", mapping
    refs = {r.strip() for ln in lines for r in ln["designator"].split(",")}
    assert "TP1" not in refs and not any(r.startswith("#") for r in refs)   # in_bom no; power symbol


def test_kicad_netlists_from_kicad_cli_read_the_same():
    want = T.read_bom(SCH / "board.kicad_sch")[0]
    for f in ("board_netlist_kicad7.xml", "board_netlist_kicad7.net", "board_pybom_kicad7.xml"):
        got, mapping = T.read_bom(SCH / f)
        assert got == want, (f, got)
        assert mapping["value"] == "Value"


def test_kicad_cli_agrees_with_our_schematic_reader():
    if not shutil.which("kicad-cli"):
        print("skip: kicad-cli not installed")
        return
    d = _tmp()
    shutil.copytree(SCH, d / "p")
    r = subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml", "-o", str(d / "n.xml"),
                        str(d / "p" / "board.kicad_sch")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert T.read_bom(d / "n.xml")[0] == T.read_bom(d / "p" / "board.kicad_sch")[0] == EXPECTED


KICAD6 = """(kicad_sch (version 20211123) (generator eeschema)
  (uuid 00000000-0000-0000-0000-000000000001)
  (lib_symbols)
  (symbol (lib_id "Device:R") (at 50 50 0) (unit 1) (in_bom yes) (on_board yes)
    (uuid 00000000-0000-0000-0000-0000000000aa)
    (property "Reference" "R?" (id 0) (at 0 0 0))
    (property "Value" "10k" (id 1) (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (id 2) (at 0 0 0))
    (property "Mfr. Part No." "RC0603FR-0710KL" (id 4) (at 0 0 0)))
  (sheet (at 100 50) (size 20 10) (uuid 00000000-0000-0000-0000-0000000000s1)
    (property "Sheet name" "ch" (id 0) (at 0 0 0))
    (property "Sheet file" "leaf6.kicad_sch" (id 1) (at 0 0 0)))
  (sheet_instances (path "/" (page "1")) (path "/00000000-0000-0000-0000-0000000000s1" (page "2")))
  (symbol_instances
    (path "/00000000-0000-0000-0000-0000000000aa" (reference "R5") (unit 1) (value "10k") (footprint "Resistor_SMD:R_0603_1608Metric"))
    (path "/00000000-0000-0000-0000-0000000000s1/00000000-0000-0000-0000-0000000000bb" (reference "C7") (unit 1) (value "22pF") (footprint "C_0402"))))
"""
LEAF6 = """(kicad_sch (version 20211123) (generator eeschema) (uuid 00000000-0000-0000-0000-000000000002) (lib_symbols)
  (symbol (lib_id "Device:C") (at 50 50 0) (unit 1) (in_bom yes) (on_board yes) (uuid 00000000-0000-0000-0000-0000000000bb)
    (property "Reference" "C?" (id 0) (at 0 0 0)) (property "Value" "C" (id 1) (at 0 0 0))))
"""


def test_kicad6_symbol_instances_and_old_sheet_names():
    d = _tmp()
    (d / "top.kicad_sch").write_text(KICAD6)
    (d / "leaf6.kicad_sch").write_text(LEAF6)
    lines, mapping = BI.read_kicad_sch(d / "top.kicad_sch")
    assert [(l["designator"], l["value"], l.get("footprint"), l.get("mpn")) for l in lines] == [
        ("C7", "22pF", "C_0402", None), ("R5", "10k", "Resistor_SMD:R_0603_1608Metric", "RC0603FR-0710KL")], lines
    assert mapping["mpn"] == "Mfr. Part No."


def test_kicad_sch_clear_errors():
    d = _tmp()
    (d / "old.kicad_sch").write_text("EESchema Schematic File Version 4\nEELAYER 30 0\n")
    (d / "x.kicad_sch").write_text("(kicad_pcb (version 1))")
    (d / "loop.kicad_sch").write_text('(kicad_sch (version 20230121) (uuid u1) (sheet (uuid s1) (property "Sheetname" "a") '
                                      '(property "Sheetfile" "loop.kicad_sch")))')
    (d / "miss.kicad_sch").write_text('(kicad_sch (version 20230121) (uuid u1) (sheet (uuid s1) (property "Sheetname" "a") '
                                      '(property "Sheetfile" "gone.kicad_sch")))')
    (d / "power.kicad_sch").write_text('(kicad_sch (version 20230121) (uuid u1) (symbol (lib_id "power:GND") (in_bom yes) '
                                       '(uuid a) (property "Reference" "#PWR01") (property "Value" "GND")))')
    for f, want in (("old", "KiCad 5"), ("x", "not a KiCad schematic"), ("loop", "includes itself"),
                    ("miss", "gone.kicad_sch"), ("power", "no parts found")):
        try:
            T.read_bom(d / f"{f}.kicad_sch")
            raise AssertionError(f"{f}: no error")
        except ValueError as e:
            assert want in str(e), (f, e)


def test_kicad8_netlist_fields_and_house_dnp():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<export version="E"><components>
  <comp ref="R1"><value>1k</value><footprint>R_0402</footprint><description>Thick film</description>
    <fields><field name="Footprint">R_0402</field><field name="Datasheet"></field><field name="Description">Thick film</field>
      <field name="Mfr Part #">RC0402FR-071KL</field><field name="Mfr">Yageo</field><field name="DNP">yes</field></fields>
    <property name="ki_description" value="Resistor"/></comp>
  <comp ref="R2"><value>1k</value><footprint>R_0402</footprint>
    <fields><field name="Mfr Part #">RC0402FR-071KL</field><field name="Mfr">Yageo</field></fields></comp>
  <comp ref="#FLG01"><value>PWR_FLAG</value></comp>
</components></export>"""
    d = _tmp()
    (d / "k8.xml").write_text(xml)
    lines, mapping = T.read_bom(d / "k8.xml")
    assert [(l["designator"], l["dnp"], l.get("description"), l["mpn"], l["manufacturer"]) for l in lines] == [
        ("R1", True, "Thick film", "RC0402FR-071KL", "Yageo"), ("R2", False, None, "RC0402FR-071KL", "Yageo")], lines
    assert mapping["mpn"] == "Mfr Part #" and mapping["dnp"] == "DNP", mapping


def test_xml_with_doctype_refused_and_other_xml_not_taken_for_netlist():
    d = _tmp()
    (d / "evil.xml").write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><export><components/></export>')
    try:
        BI.read_kicad_netlist(d / "evil.xml")
        raise AssertionError("DOCTYPE accepted")
    except ValueError as e:
        assert "DOCTYPE" in str(e)
    (d / "other.xml").write_text("<bom><row/></bom>")
    assert not BI.is_kicad_netlist(d / "other.xml") and BI.is_kicad_netlist(SCH / "board_netlist_kicad7.xml")


def test_triage_command_on_a_kicad_schematic():
    d = _tmp()
    rc = T.main([str(SCH / "board.kicad_sch"), "-o", str(d / "tri"), "--providers", "digikey", "--offline",
                 "--cache", str(d / "c")])
    assert not rc
    rows = list(csv.DictReader(open(d / "tri.csv")))
    assert [r["designator"] for r in rows] == [l["designator"] for l in EXPECTED]
    assert rows[0]["status"] == "dnp (not fitted)" and rows[4]["lcsc"] == "C7950", rows


# ------------------------------------------------------------------ CSV / XLSX / house columns
def test_csv_dialects_fixtures():
    lines, m = T.read_bom(BIN / "semicolon_utf8bom.csv")        # Excel 'CSV UTF-8' with ; and a quoted ';'
    assert m["designator"] == "Designator" and m["mpn"] == "Mfr Part #", m      # (no BOM character in the header)
    assert lines[0]["value"] == "4,7µF; 10V" and lines[2]["mpn"] == "LM358DR", lines
    lines, m = T.read_bom(BIN / "tab_cp1252.txt")               # Excel 'Text (Tab delimited)' on Windows
    assert m["manufacturer"] == "Mfr" and lines[0]["designator"] == "R1–R4" and lines[1]["value"] == "1µF", lines
    assert T.count_refs(lines[0]["designator"]) == 4


def test_house_columns_with_a_title_row_and_aliases():
    rules = RU.load(BIN / "house_columns.toml")
    assert RU.bom_columns(rules)["mpn"] == ["Herst.-Nr.", "Mfr Part #"]
    try:
        T.read_bom(BIN / "house_columns.csv")
        raise AssertionError("German headers guessed without rules?")
    except ValueError as e:
        assert "no BOM header" in str(e)
    lines, m = T.read_bom(BIN / "house_columns.csv", RU.bom_columns(rules))      # header row found below the title
    assert m == {"designator": "Pos", "value": "Bauteilwert", "footprint": "Gehaeuse", "mpn": "Herst.-Nr.",
                 "manufacturer": "Hersteller", "qty": "Stk"}, m
    assert [(l["designator"], l["mpn"]) for l in lines] == [("R1", "RC0603FR-0710KL"), ("U2", "TPS62133RGTR")]
    # the second alias is used by a BOM that has it; the semicolon fixture names its MPN column 'Mfr Part #'
    assert T.read_bom(BIN / "semicolon_utf8bom.csv", {"mpn": ["Herst.-Nr.", "Mfr Part #"]})[1]["mpn"] == "Mfr Part #"


def test_rules_bom_columns_list_validation():
    ok = RU.load(RU.parse_text('[bom]\ncolumns = { mpn = ["MPN", "Mfr Part #"], value = "Wert" }\n'))
    assert ok["bom"]["columns"]["mpn"] == ["MPN", "Mfr Part #"]
    for bad in ('[bom]\ncolumns = { mpn = [1, 2] }\n', '[bom]\ncolumns = { mpn = [] }\n', '[bom]\ncolumns = { mpn = { a = "b" } }\n'):
        try:
            RU.load(RU.parse_text(bad))
            raise AssertionError(bad)
        except RU.RulesError as e:
            assert "bom.columns" in str(e), e


def _no_openpyxl(*a, **k):
    raise ImportError("Missing optional dependency 'openpyxl'.")


def test_xlsx_without_openpyxl_reads_like_pandas():
    f = BIN / "workbook_title_rows.xlsx"                       # notes sheet first, title rows, 1e-07 F, Total footer
    want = T.read_bom(f)
    with mock.patch.object(pd, "read_excel", _no_openpyxl):
        got = T.read_bom(f)
    assert got == want, (got, want)
    assert want[0][0]["value"] == "100n" and want[0][2]["mpn"] == "1050170001" and want[0][2]["dnp"] is True, want


def test_xlsx_inline_strings_and_sparse_cells():
    """Some ERP / PLM exporters write inline strings and skip empty cells (cell refs jump); no openpyxl needed."""
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             '<row r="1"><c r="A1" t="inlineStr"><is><t>Designator</t></is></c><c r="C1" t="inlineStr"><is><t>MPN</t></is></c>'
             '<c r="D1" t="inlineStr"><is><t>Qty</t></is></c></row>'
             '<row r="3"><c r="A3" t="inlineStr"><is><t>U1</t></is></c><c r="C3" t="inlineStr"><is><r><t>LM</t></r><r><t>358DR</t></r></is></c>'
             '<c r="D3"><v>1</v></c></row></sheetData></worksheet>')
    wb = ('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          '<sheet name="BOM" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
    d = _tmp()
    with zipfile.ZipFile(d / "erp.xlsx", "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    assert BI.xlsx_sheets(d / "erp.xlsx") == {"BOM": [["Designator", "", "MPN", "Qty"], ["", "", "", ""], ["U1", "", "LM358DR", "1"]]}
    with mock.patch.object(pd, "read_excel", _no_openpyxl):
        lines, m = T.read_bom(d / "erp.xlsx")
    assert lines == [{"designator": "U1", "mpn": "LM358DR", "qty": "1"}], lines


# ------------------------------------------------------------------ KiCad database library + parts export
_PLAN = {}


def _plan():
    """A real librarian plan (tests.test_kicad_export's fake-library run), made once and copied per test."""
    if "dir" not in _PLAN:
        from tests import test_kicad_export as TX
        d = _tmp()
        plan, items, http = TX._plan(d)
        _PLAN.update(dir=plan.parent, http=http)
    d = _tmp() / "plan"
    shutil.copytree(_PLAN["dir"], d)
    return d / "plan.json", _PLAN["http"]


KICAD_DBL_KEYS = ["source.connection_string", "source.timeout_seconds", "source.dsn", "cache.max_size", "cache.max_age",
                  "visible_on_add", "visible_in_chooser", "inherit_properties", "footprint_filters", "exclude_from_board"]


def test_kicad_dblib_build():
    plan, http = _plan()
    out = plan.parent.parent / "kicad"
    rep = KD.build(plan, out, "My Lib", export=True, http=http, log=lambda *a: None)
    assert rep["tables"] == {"Diodes": 1, "ICs": 1, "Resistors": 1, "Transistors": 1}, rep
    assert {s["part"] for s in rep["skipped"]} == {"DSPART1", "NOWHERE1"} and not rep["warnings"], rep
    doc = json.loads((out / "My_Lib.kicad_dbl").read_text())
    assert set(doc) == {"meta", "name", "description", "source", "cache", "libraries"}
    assert doc["source"]["type"] == "odbc" and doc["source"]["connection_string"] == \
        "Driver={SQLite3 ODBC Driver};Database=${CWD}/My_Lib.sqlite"
    lib = next(l for l in doc["libraries"] if l["name"] == "ICs")
    assert (lib["table"], lib["key"], lib["symbols"], lib["footprints"]) == ("ICs", "Part ID", "Symbol", "Footprint")
    assert lib["properties"] == {"description": "Description"}
    assert {f["column"] for f in lib["fields"]} >= {"Value", "Datasheet", "Manufacturer", "MPN", "LCSC"}
    for f in lib["fields"]:
        assert set(f) == {"column", "name", "visible_on_add", "visible_in_chooser", "show_name", "inherit_properties"}
    # what KiCad's ODBC queries see: SELECT * FROM "ICs" / ... WHERE "Part ID" = ?
    con = sqlite3.connect(out / "My_Lib.sqlite")
    row = dict(zip([c[1] for c in con.execute('PRAGMA table_info("ICs")')],
                   con.execute('SELECT * FROM "ICs" WHERE "Part ID" = ?', ("TPS1234DSGR",)).fetchone()))
    con.close()
    assert row["Symbol"] == "My_Lib:TPS1234DSGR" and row["Footprint"] == "My_Lib:SOIC-8_3.9x4.9mm_P1.27mm", row
    sym = {s[1] for s in sexpr.find(sexpr.parse((out / "My_Lib.kicad_sym").read_text()), "symbol")}
    assert row["Symbol"].split(":")[1] in sym and (out / "My_Lib.pretty" / "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod").is_file()
    t = sexpr.parse((out / "sym-lib-table").read_text())
    libs = {sexpr.first(l, "name")[1]: sexpr.first(l, "type")[1] for l in sexpr.find(t, "lib")}
    assert libs == {"My_Lib": "KiCad", "My_Lib-db": "Database"}, libs
    KD.build(plan, out, "My Lib", log=lambda *a: None)                                # again: one Database row, kept file
    t = sexpr.parse((out / "sym-lib-table").read_text())
    assert [sexpr.first(l, "name")[1] for l in sexpr.find(t, "lib")] == ["My_Lib", "My_Lib-db"]


def test_kicad_dblib_warns_without_kicad_files_and_dsn():
    plan, _ = _plan()
    out = plan.parent.parent / "k2"
    rep = KD.build(plan, out, "lib", dsn="PartsDB", log=lambda *a: None)
    assert any("kicad_export.json" in w for w in rep["warnings"]), rep
    doc = json.loads((out / "lib.kicad_dbl").read_text())
    assert doc["source"]["dsn"] == "PartsDB" and doc["source"]["connection_string"] == ""
    assert sexpr.first(sexpr.first(sexpr.parse((out / "sym-lib-table").read_text()), "lib"), "type")[1] == "Database"


def test_kicad_dbl_keys_are_the_ones_kicad_reads():
    """The settings keys we write appear in KiCad's own eeschema module (DATABASE_LIB_SETTINGS)."""
    if not EESCHEMA.is_file():
        print("skip: KiCad's _eeschema.kiface not installed")
        return
    data = EESCHEMA.read_bytes()
    missing = [k for k in KICAD_DBL_KEYS if k.encode() not in data]
    assert not missing, missing
    assert "Database".encode("utf-32-le") in data            # the sym-lib-table plugin type name (wide string)


def test_export_parts_formats():
    plan, _ = _plan()
    items = json.loads(plan.read_text())
    tps = next(i for i in items if i["name"] == "TPS1234DSGR")
    tps["description"] = "=HYPERLINK(\"http://x\")"                                 # must not become a formula
    tps["parameters"].append({"name": "Temperature Range", "value": "-40..125 C"})
    plan.write_text(json.dumps(items))
    rs = PE.rows(plan, "lib")
    assert [r["Part ID"] for r in rs] == ["EEPART1", "LED-RED-0603", "RES-0603-10k", "TPS1234DSGR"]
    assert all(list(r) == PE.COLUMNS for r in rs)
    t = rs[3]
    assert (t["Category"], t["Manufacturer Part Number"], t["KiCad Symbol"], t["Release Check"]) == \
        ("ICs", "TPS1234DSGR", "lib:TPS1234DSGR", "allowed"), t      # its near-name symbol: released anyway (--allow)
    assert rs[2]["Release Check"] == "pass", rs[2]                   # RES-0603-10k passes on its own
    assert t["Other Parameters"]["Temperature Range"] == "-40..125 C"
    assert len(PE.rows(plan, "lib", include_held=True)) == 6
    d = plan.parent
    for fmt in ("csv", "json", "xlsx"):
        assert not cli.main(["export-parts", str(plan), "-o", str(d / f"p.{fmt}"), "--name", "lib"])
    with open(d / "p.csv", encoding="utf-8-sig") as f:
        got = list(csv.DictReader(f))
    assert list(got[0]) == PE.COLUMNS and got[3]["Description"].startswith("'=") and \
        "Temperature Range=-40..125 C" in got[3]["Other Parameters"], got[3]
    j = json.loads((d / "p.json").read_text())
    assert j["format"] == "ecadlib-parts" and j["columns"] == PE.COLUMNS and j["parts"][3]["Description"].startswith("=")
    x = pd.read_excel(d / "p.xlsx", dtype=str).fillna("")
    assert list(x.columns) == PE.COLUMNS and x.iloc[3]["Description"].startswith("'=") and x.iloc[2]["Part ID"] == "RES-0603-10k"
    assert cli.main(["export-parts", str(d / "nope.json")]) == 2


def test_cli_kicad_dblib_and_workspace_mcp():
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    plan, _ = _plan()
    assert not cli.main(["kicad-dblib", str(plan), "-o", str(plan.parent / "kd"), "--name", "x", "--driver", "SQLite3"])
    assert "Driver={SQLite3};" in json.loads((plan.parent / "kd" / "x.kicad_dbl").read_text())["source"]["connection_string"]
    assert {"export_parts", "kicad_dblib"} <= {t["name"] for t in M.TOOLS}
    with tempfile.TemporaryDirectory() as w:
        ws = Workspace(w, offline=True)
        bom = ws.save_upload("bom.csv", b"Designator,MPN\nR1,X\n")
        shutil.copytree(plan.parent, ws._librarian_dir(ws._bom_path(bom)))
        r = M.call_tool(ws, "export_parts", {"bom": bom, "format": "json"}) if hasattr(M, "call_tool") else ws.export_parts(bom, "json")
        assert r["parts"] == 4 and (Path(w) / r["file"]).is_file(), r
        r = ws.kicad_dblib(bom, "Lib")
        assert r["tables"] and any(f.endswith("Lib.kicad_dbl") for f in r["files"]), r
        try:
            ws.export_parts(bom, "pdf")
            raise AssertionError("pdf accepted")
        except ValueError:
            pass


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
