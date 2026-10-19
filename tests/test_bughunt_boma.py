"""Bug-hunt regressions (group boma: BOM reading). Plain functions, no pytest needed:
    python3 -m tests.run_all test_bughunt_boma"""
import tempfile
from pathlib import Path

from ecadlib import bom_triage as T
from tests.fixtures_lite import lite


def _bom(text, name="bom.csv"):
    f = Path(tempfile.mkdtemp()) / name
    f.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return f


def _dnp(text):
    lines, mapping = T.read_bom(_bom(text))
    return [bool(ln.get("dnp")) for ln in lines], mapping


# ---------------------------------------------------------------- BOM-1, 2, 7, 8: one DNP / fitted interpreter
def test_bom1_stuff_column_has_positive_polarity():
    got, mapping = _dnp("Designator,Qty,Value,MPN,Stuff\nR1,1,10k,A1,Yes\nR2,1,1k,A2,No\nR3,1,1k,A3,\n")
    assert mapping.get("fitted") == "Stuff" and "dnp" not in mapping
    assert got == [False, True, False]


def test_bom2_fitted_populate_dni_columns_are_read():
    for hdr, fit, unfit in (("Fitted", "Fitted", "Not Fitted"), ("Populate", "Yes", "No"), ("DNI", "", "DNI"),
                            ("Do Not Install", "", "x"), ("No Pop", "", "yes"), ("Install", "Y", "N")):
        got, _ = _dnp(f"Designator,Qty,Value,MPN,{hdr}\nR1,1,10k,A1,{fit}\nR2,1,1k,A2,{unfit}\n")
        assert got == [False, True], (hdr, got)


def test_bom2_one_word_headers_do_not_fuzzy_match_dnp():
    for hdr in ("Stuff", "Mount Side", "Placement", "Notes"):
        m = T.map_headers(["Designator", "Value", "MPN", hdr])
        assert m.get("dnp") != hdr, (hdr, m)


def test_bom7_dnp_column_words_that_mean_fitted():
    rows = "".join(f"R{i},1,10k,A{i},{v}\n" for i, v in enumerate(
        ["Populated", "Installed", "Place", "N/A", "OK", "-", "Fitted (default)", "DNP", "x", "Yes", "NF"], 1))
    got, _ = _dnp("Designator,Qty,Value,MPN,DNP\n" + rows)
    assert got == [False] * 7 + [True] * 4


def test_bom8_value_cell_short_dnp_codes():
    vals = ["NF", "N/F", "NP", "No Pop", "Not Mounted", "DNP", "Not Installed", "10k", "100 NF", "10uF NP", "10nF"]
    rows = "".join(f"R{i},1,{v},\n" for i, v in enumerate(vals, 1))
    got, _ = _dnp("Designator,Qty,Value,MPN\n" + rows)
    assert got == [True] * 7 + [False] * 4, got


def test_dnp_cell_helper():
    assert T.dnp_cell("x") and T.dnp_cell("DNP") and not T.dnp_cell("") and not T.dnp_cell("no")
    assert T.dnp_cell("No", "fitted") and T.dnp_cell("Not Fitted", "fitted") and T.dnp_cell("0", "fitted")
    assert not T.dnp_cell("", "fitted") and not T.dnp_cell("Yes", "fitted") and not T.dnp_cell("Top", "fitted")


def test_kicad_house_fitted_field_uses_the_same_reading():
    from ecadlib import bom_inputs as BI
    parts = [{"ref": "R1", "fields": {"Value": "10k", "Fitted": "No"}, "dnp": False},
             {"ref": "R2", "fields": {"Value": "1k", "Fitted": "Yes"}, "dnp": False}]
    lines, _ = BI.group(parts)
    assert {ln["designator"]: ln["dnp"] for ln in lines} == {"R1": True, "R2": False}


# ---------------------------------------------------------------- BOM-15: DNP lines still get a symbol and footprint
def _librarian(text, **kw):
    from ecadlib import librarian as L
    d = Path(tempfile.mkdtemp())
    bom = d / "bom.csv"
    bom.write_text(text)
    return L.run(bom, out=d / "out", sources=kw.pop("sources", ["standard"]), offline=True, cache=d / "cache",
                 log=lambda *a, **k: None, **kw)


def test_bom15_dnp_line_is_built_not_held_without_symbol():
    items = _librarian("Designator,Qty,Value,Footprint,MPN,DNP\nR1,1,10k,R_0603_1608Metric,RC0603FR-0710KL,\n"
                       "R2,1,1k,R_0603_1608Metric,RC0603FR-071KL,DNP\n")
    it = next(i for i in items if i["name"] == "RES-0603-1k")
    assert it["source"]["symbol"] and it["source"]["footprint"], it["source"]


# ---------------------------------------------------------------- BOM-3, 10, 13, 27: BOM lines -> plan items
def _plan(text):
    from ecadlib import altium365 as A
    lines, _ = T.read_bom(_bom(text))
    return A.plan(lines)


def test_bom3_no_mpn_lines_with_different_footprints_stay_two_parts():
    items = _plan("Designator,Qty,Value,Footprint\nR1,1,10k,MyLib:RES_SMALL\nR2,1,10k,MyLib:RES_AXIAL_TH_10MM\n"
                  "R3,1,10k,R_0603_1608Metric\nR4,1,10k,RESC1608X55N\n")
    fps = sorted(i["footprint"]["ref"] for i in items)
    assert "RES_SMALL" in fps and "RES_AXIAL_TH_10MM" in fps, fps
    assert sum(1 for i in items if "0603" in i["name"]) == 1, [i["name"] for i in items]   # one 0603 10k, as before
    items = _plan("Designator,Qty,Value,Footprint,MPN\nU1,1,LM358,SOIC-8,N/A\nU2,1,LM358,DIP-8,N/A\n")
    assert sorted(i["footprint"]["ref"] for i in items) == ["DIP-8", "SOIC-8"]


def test_bom10_greek_mu_and_zero_width_do_not_abort_the_plan():
    items = _plan("Designator,Qty,Value,Footprint,MPN\nU1,1,μA741,SOIC-8,\nU2,1,LM358,SOIC-8,LM358DR​\n"
                  "U3,1,X,SOIC-8,中文123\nR1,1,10k,R_0603_1608Metric,RC0603FR-0710KL\n")
    names = sorted(i["name"] for i in items)
    assert "µA741" in names and "LM358DR" in names and len(items) == 4, names
    odd = next(i for i in items if i["name"] not in ("µA741", "LM358DR") and "10k" not in i["name"])
    assert odd["name"].encode("cp1252") and any("can't store" in n for n in odd.get("bom_notes", [])), odd


def test_bom13_mpn_spellings_are_one_part():
    items = _plan("Designator,Qty,Value,Footprint,MPN\nU1,1,TPS62133,VQFN-16,TPS62133RGTR\n"
                  "U2,1,TPS62133,VQFN-16,tps62133rgtr\nU3,1,TPS62133,VQFN-16,TPS62133–RGTR\n"
                  "U4,1,TPS62133,VQFN-16,ＴＰＳ６２１３３RGTR\n"
                  "R1,1,10k,R_0603_1608Metric,RC0603FR-0710KL\nR2,1,10k,R_0603_1608Metric,rc0603fr-0710kl\n")
    assert sorted(i["name"] for i in items) == ["RES-0603-10k", "TPS62133RGTR"], [i["name"] for i in items]
    assert T.clean_part_number("TPS62133–RGTR ") == "TPS62133-RGTR"


def test_bom27_same_mpn_different_footprint_is_reported():
    items = _plan("Designator,Qty,Value,Footprint,MPN,Manufacturer\nU1,1,LM358,SOIC-8,LM358DR,TI\n"
                  "U2,1,LM358,TSSOP-8,LM358DR,ST\n")
    assert len(items) == 1 and items[0]["footprint"]["ref"] == "SOIC-8"
    note = " ".join(items[0].get("bom_notes", []))
    assert "U2" in note and "TSSOP-8" in note and "ST" in note, note


# ---------------------------------------------------------------- workbooks (BOM-4, BOM-9)
def _xlsx(path, sheets, hidden_sheets=(), hidden_cols=()):
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
        if name in hidden_sheets:
            ws.sheet_state = "hidden"
        for c in hidden_cols:
            ws.column_dimensions[c].hidden = True
    wb.save(path)
    return path


H = ["Designator", "Qty", "Value", "Footprint", "MPN"]


@lite
def test_bom4_hidden_sheets_and_columns_and_split_sheets(tmp_path):
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("  (openpyxl not installed; skipped)")
        return
    a = _xlsx(tmp_path / "a.xlsx", [("OLD", [H, ["R1", 1, "1k", "R_0603", "OLD-PART"]]),
                                    ("BOM", [H, ["R1", 1, "10k", "R_0603", "NEW1"], ["R2", 1, "10k", "R_0603", "NEW2"]])],
              hidden_sheets=("OLD",))
    assert [l["mpn"] for l in T.read_bom(a)[0]] == ["NEW1", "NEW2"]
    b = _xlsx(tmp_path / "b.xlsx", [("Top", [H, ["R1", 1, "10k", "R_0603", "T1"]]),
                                    ("Bottom", [H, ["R2", 1, "10k", "R_0603", "B1"], ["R3", 1, "1k", "R_0603", "B2"]])])
    assert sorted(l["mpn"] for l in T.read_bom(b)[0]) == ["B1", "B2", "T1"]
    c = _xlsx(tmp_path / "c.xlsx", [("S", [["Designator", "Qty", "Value", "MPN", "MPN"], ["U1", 1, "LM358", "OLD", "LM358DR"]])],
              hidden_cols=("D",))
    assert T.read_bom(c)[0][0]["mpn"] == "LM358DR"


@lite
def test_bom9_header_below_title_block_and_blank_rows(tmp_path):
    rows = [[f"Title {i}"] for i in range(8)] + [[]] * 6 + [H, ["R1", 1, "10k", "R_0603", "RC1"]]
    f = tmp_path / "t.csv"
    f.write_text("\n".join(",".join(map(str, r)) for r in rows) + "\n")
    assert T.read_bom(f)[0][0]["mpn"] == "RC1"
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return
    x = _xlsx(tmp_path / "t.xlsx", [("S", rows)])
    assert T.read_bom(x)[0][0]["mpn"] == "RC1"


# ---------------------------------------------------------------- row filter and counting (BOM-12, 17, 18, 19)
def test_bom12_section_headings_notes_and_footers_are_not_parts():
    lines, _ = T.read_bom(_bom('Designator,Qty,Value,Footprint,MPN\nResistors,,,,\n"R1,R2",2,10k,R_0603,RC1\n'
                               'Capacitors,,,,\nC1,1,100n,C_0402,GRM1\n,,,,Notes: all parts RoHS\nSum,3,,,\nGesamt,3,,,\n'
                               'Prepared by: J. Smith,,,,\nApproved,,,,\n'))
    assert [l["designator"] for l in lines] == ["R1,R2", "C1"], lines


def test_bom17_wrap_only_continues_the_same_kind_of_designator():
    lines, _ = T.read_bom(_bom('Designator,Qty,Value,MPN\n"R1,R2",3,10k,RC1\nJ5,,,\nC1,1,100n,GRM1\n'
                               '"R5,R6",3,1k,RC2\nR7,,,\n'))
    assert [l["designator"] for l in lines] == ["R1,R2", "J5", "C1", "R5,R6, R7"], lines


def test_bom18_count_refs_ranges_and_separators():
    for s, n in (("R5-R1", 5), ("R10-R1", 10), ("R1..R5", 5), ("R1:R5", 5), ("R1 to R5", 5), ("R1/R2", 2),
                 ("R1+R2", 2), ("R1&R2", 2), ("C1/C2/C3/C4", 4), ("R1, R2, R5-R8", 6), ("U1A, U1B", 1)):
        assert T.count_refs(s) == n, (s, T.count_refs(s))


def test_bom19_qty_mismatch_note_and_decimal_comma_thousand():
    from ecadlib.kicad_index import KiCadIndex
    lines, _ = T.read_bom(_bom("Designator;Qty;Value;MPN\nR1,R2,R3;2;4,7k;RC1\nC2;1,000;100n;GRM1\nR9-R1008;1,000;1k;RC9\n"))
    assert [l["qty"] for l in lines] == ["2", "1", "1,000"], lines
    rows = T.triage(lines, KiCadIndex([]), [], None)
    assert "Qty 2 but 3 designator" in rows[0]["notes"] and "Qty" not in rows[1]["notes"], rows


# ---------------------------------------------------------------- header mapping (BOM-20, BOM-21)
def test_bom20_second_manufacturer_part_number():
    lines, _ = T.read_bom(_bom("Designator,Quantity,Comment,Manufacturer 1,Manufacturer Part Number 1,Manufacturer 2,"
                               "Manufacturer Part Number 2\nU1,1,OPAMP,,,Texas Instruments,LM358DR\nU2,1,MCU,ST,STM32F1,,\n"))
    assert (lines[0]["mpn"], lines[0]["manufacturer"]) == ("LM358DR", "Texas Instruments")
    assert (lines[1]["mpn"], lines[1]["manufacturer"]) == ("STM32F1", "ST")


def test_bom21_value_beats_comment_and_part_is_not_a_designator():
    lines, m = T.read_bom(_bom("Designator,Comment,Value,Quantity\nR1,Resistor,10k,1\nU1,LM358DR,,1\n"))
    assert m["value"] == "Value" and [l["value"] for l in lines] == ["10k", "LM358DR"], (m, lines)
    m = T.map_headers(["Item", "Qty", "Part", "Description", "Manufacturer"])
    assert m.get("mpn") == "Part" and "designator" not in m, m
    m = T.map_headers(["Designator", "Part Type", "MPN"])
    assert m.get("mpn") == "MPN"
    assert T.map_headers(["Comment", "Designator"]).get("value") == "Comment"


# ---------------------------------------------------------------- KiCad inputs (BOM-11, 16, 22, 24)
def _sch(body):
    return f'(kicad_sch (version 20231120) (generator "eeschema") (uuid "aaaa")\n{body}\n)\n'


def _sym(ref, value, lib="Device:R", unit=1, fp="Resistor_SMD:R_0603_1608Metric"):
    return (f'(symbol (lib_id "{lib}") (at 0 0 0) (unit {unit}) (in_bom yes) (on_board yes) (uuid "u-{ref}-{value}-{unit}")\n'
            f'  (property "Reference" "{ref}" (at 0 0 0)) (property "Value" "{value}" (at 0 0 0))\n'
            f'  (property "Footprint" "{fp}" (at 0 0 0)))\n')


@lite
def test_bom11_duplicate_reference_keeps_both_parts(tmp_path):
    f = tmp_path / "d.kicad_sch"
    f.write_text(_sch(_sym("R1", "10k") + _sym("R1", "1k", fp="Resistor_SMD:R_0402_1005Metric")
                      + _sym("U1", "LM358", "Amplifier_Operational:LM358", 1) + _sym("U1", "LM358", "Amplifier_Operational:LM358", 2)))
    lines, _ = T.read_bom(f)
    got = sorted((l["designator"], l["value"], l["qty"]) for l in lines)
    assert got == [("R1", "10k", "1"), ("R1", "1k", "1"), ("U1", "LM358", "1")], got


@lite
def test_bom16_byte_order_mark(tmp_path):
    f = tmp_path / "b.kicad_sch"
    f.write_bytes(b"\xef\xbb\xbf" + _sch(_sym("R1", "10k")).encode())
    assert T.read_bom(f)[0][0]["value"] == "10k"
    n = tmp_path / "b.xml"
    n.write_bytes(b'\xef\xbb\xbf<?xml version="1.0"?>\n<export version="E"><components><comp ref="R1"><value>10k</value>'
                  b'<footprint>R_0603</footprint></comp></components></export>')
    assert T.read_bom(n)[0][0]["value"] == "10k"


@lite
def test_bom22_sub_sheet_outside_the_workspace_is_refused(tmp_path):
    from ecadlib.app import Workspace
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "p.kicad_sch").write_text(_sch(_sym("U9", "SECRET")))
    ws = Workspace(tmp_path / "ws", offline=True)
    msgs = []
    for target in (str(tmp_path / "outside" / "p.kicad_sch"), "../../../outside/p.kicad_sch", "/etc/no-such-file"):
        body = _sch(_sym("R1", "10k") + f'(sheet (at 0 0) (size 1 1) (uuid "s1") (property "Sheetname" "x" (at 0 0 0))'
                    f' (property "Sheetfile" "{target}" (at 0 0 0)))')
        p = ws.save_upload("evil.kicad_sch", body.encode())
        try:
            ws._read_bom(ws.inside(p))
            raise AssertionError(f"{target} was read")
        except ValueError as e:
            assert "SECRET" not in str(e)
            msgs.append(str(e).replace(target, "X"))
    assert len(set(msgs)) == 1, msgs                    # one message: no file-existence oracle
    # a local schematic (no workspace) still follows its sheets wherever they are
    top = tmp_path / "top.kicad_sch"
    top.write_text(_sch(_sym("R1", "10k") + '(sheet (at 0 0) (size 1 1) (uuid "s1") (property "Sheetname" "x" (at 0 0 0))'
                        ' (property "Sheetfile" "outside/p.kicad_sch" (at 0 0 0)))'))
    assert {l["designator"] for l in T.read_bom(top)[0]} == {"R1", "U9"}


@lite
def test_bom23_prjpcb_document_outside_the_workspace_is_refused(tmp_path):
    from ecadlib import altium_project as AP
    (tmp_path / "ws" / "up").mkdir(parents=True)
    prj = tmp_path / "ws" / "up" / "evil.PrjPcb"
    prj.write_text("[Design]\nVersion=1.0\n[Document1]\nDocumentPath=..\\..\\outside\\Secret.SchDoc\n")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "Secret.SchDoc").write_bytes(b"x")
    assert AP.read_prjpcb(prj)["schdocs"]                                   # local use: found
    got = AP.read_prjpcb(prj, root=tmp_path / "ws")
    assert not got["schdocs"] and got["missing"] == ["..\\..\\outside\\Secret.SchDoc"], got


@lite
def test_bom24_malformed_netlist_is_a_clear_error(tmp_path):
    f = tmp_path / "a.net"
    f.write_text('(export (version "E") (components (comp (ref (x)) (value "10k")) (comp (ref "R2") (value (y)))))')
    try:
        T.read_bom(f)
    except ValueError as e:
        assert "a.net" in str(e) or "no parts" in str(e), e
    except Exception as e:
        raise AssertionError(f"internal error {type(e).__name__}: {e}")


# ---------------------------------------------------------------- dedupe, Excel cues, encodings, quotes (BOM-25, 26, 28, 29)
@lite
def test_bom25_remap_bom_uses_the_rules_columns_and_keeps_the_delimiter(tmp_path):
    from ecadlib import dedupe as D
    (tmp_path / "house.csv").write_text("Pos;Wert;Symbol-Name;Gehaeuse;Herst.-Nr.\nR1;4,7k;OLDSYM;OLDFP;RC0603FR-074K7L\n")
    (tmp_path / "ecadlib.toml").write_text('[bom]\ncolumns = { designator = "Pos", value = "Wert", libref = "Symbol-Name", '
                                           'footprint = "Gehaeuse", mpn = "Herst.-Nr." }\n')
    m = [{"kind": "footprint", "from": "OLDFP", "to": "NEWFP"}, {"kind": "symbol", "from": "OLDSYM", "to": "NEWSYM"}]
    out, n = D.remap_bom(tmp_path / "house.csv", m, tmp_path / "o.csv")
    text = out.read_text(encoding="utf-8-sig")
    assert n == 2 and "R1;4,7k;NEWSYM;NEWFP;RC0603FR-074K7L" in text, text


def test_bom26_date_shaped_mpn_is_flagged():
    from ecadlib.kicad_index import KiCadIndex
    lines, _ = T.read_bom(_bom("Designator,Qty,MPN\nJ1,1,2024-03-04\nJ2,1,4-Mar\nJ3,1,MAR4\n"))
    rows = T.triage(lines, KiCadIndex([]), [], None)
    assert ["date" in r["notes"] for r in rows] == [True, True, False], [r["notes"] for r in rows]


def test_bom28_gbk_csv_gets_a_warning():
    import contextlib, io
    data = "Designator,Value,Manufacturer\nR1,10k,UNI-ROYAL(厚声)\n".encode("gbk")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        T.read_bom(_bom(data))
    assert "not UTF-8" in err.getvalue()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        T.read_bom(_bom("Designator,Value,Description\nR1,10k,caf\xe9 10\xb5F\n".encode("cp1252")))
    assert "garbled" not in err.getvalue()                 # a plain Western cp1252 file is not nagged about


def test_bom29_unclosed_quote_does_not_swallow_the_rest():
    rows = ["Designator,Qty,Value,MPN,Description"] + [
        f"R{i},1,10k,RC{i}," + ('"0.1 inch header' if i == 5 else "Resistor") for i in range(1, 300)]
    lines, _ = T.read_bom(_bom("\n".join(rows) + "\n"))
    assert len(lines) == 299 and lines[4]["description"] == "0.1 inch header", len(lines)
    lines, _ = T.read_bom(_bom('Designator,Value,Description\nR1,10k,"two\nlines"\n"R2,R3",1k,x\n'))
    assert [l["designator"] for l in lines] == ["R1", "R2,R3"] and lines[0]["description"] == "two lines"


# ---------------------------------------------------------------- BOM-14: parts.csv cells are spreadsheet-safe
@lite
def test_bom14_menu_parts_csv_is_formula_safe(tmp_path):
    from ecadlib import altium_menu as AM
    f = AM.add_to_parts(tmp_path, "LM358DR", "=cmd|' /C calc'!A0", description='=HYPERLINK("http://evil","x")')
    AM.add_to_parts(tmp_path, "NE555", "TI")
    text = f.read_text(encoding="utf-8")
    assert "'=cmd" in text and "'=HYPERLINK" in text and "''=" not in text, text
