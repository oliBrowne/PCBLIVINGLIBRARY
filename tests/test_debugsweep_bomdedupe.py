"""Regression tests for the dedupe / BOM debug sweep (DS2-xx).
Run:  python3 -m tests.run_all test_debugsweep_bomdedupe"""
import contextlib, io, json
from pathlib import Path

from ecadlib import altium_export as X, dedupe as DD, librarian as L
from tests.fixtures_lite import lite


@lite
def test_ds2_01_mpnless_passives_10x_apart_are_not_one_part(tmp_path):
    """Passives without an MPN are named by value ('CAP-0603-2.2u'); their names are not MPNs. Stripping the
    punctuation made 2.2u == 22u and 4.7k == 47k, and --merge marked one a duplicate of the other: from the plan
    itself, from ecadlib's own DbLib of the same BOM (Part Number, empty MPN) and from a check-a365 --state file
    (names only) once both parts are in the workspace."""
    bom = tmp_path / "caps.csv"
    bom.write_text("Designator,Qty,Value,Footprint\nC1,1,2.2u,0603\nC2,1,22u,0603\nR1,1,4.7k,0603\nR2,1,47k,0603\n")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache", log=lambda *a, **k: None)
    plan = tmp_path / "lib" / "plan.json"
    names = sorted(i["name"] for i in json.loads(plan.read_text(encoding="utf-8")))
    assert names == ["CAP-0603-2.2u", "CAP-0603-22u", "RES-0603-4.7k", "RES-0603-47k"], names
    with contextlib.redirect_stdout(io.StringIO()):
        assert X.main([str(bom), "-o", str(tmp_path / "dbl")]) in (0, None)
    (tmp_path / "lib" / "a365_state.json").write_text(json.dumps(
        {n: {"revision": "r1", "lifecycle": None, "symbols": [], "footprints": []} for n in names}))
    r = DD.run(plan, libs=[tmp_path / "dbl"], do_merge=True, kinds=("mpn",), log=lambda *a, **k: None)
    mpn = [(g["key"], [(m["name"], m["origin"]) for m in g["members"]]) for g in r["groups"] if g["kind"] == "mpn"]
    assert mpn == [], mpn
    after = {i["name"]: i for i in json.loads(plan.read_text(encoding="utf-8"))}
    assert not [n for n, i in after.items() if i.get("status") == "duplicate" or i.get("duplicate_of")], after


def _mpn_groups(r):
    return [(g["key"], sorted((m["name"], m["origin"]) for m in g["members"])) for g in r["groups"] if g["kind"] == "mpn"]


@lite
def test_ds2_01_part_named_by_its_part_number_keeps_its_mpn_key(tmp_path):
    """Only a passive named by its value is no MPN. A Comment-only line (Altium's usual BOM) names the part after its
    part number and gives it no MPN parameter (Value = the name), in the plan and in ecadlib's own DbLib of it (empty
    MPN column): LM358DR and LM358DR/TR (a packaging suffix) are still one part."""
    bom = tmp_path / "ics.csv"
    bom.write_text("Designator,Qty,Comment,Footprint\nU1,1,LM358DR,SOIC-8\nU2,1,LM358DR/TR,SOIC-8\n")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache", log=lambda *a, **k: None)
    plan = tmp_path / "lib" / "plan.json"
    r = DD.run(plan, kinds=("mpn",), log=lambda *a, **k: None)
    assert _mpn_groups(r) == [("LM358DR", [("LM358DR", "plan"), ("LM358DR/TR", "plan")])], _mpn_groups(r)
    with contextlib.redirect_stdout(io.StringIO()):
        assert X.main([str(bom), "-o", str(tmp_path / "dbl")]) in (0, None)
    r = DD.run(plan, libs=[tmp_path / "dbl"], kinds=("mpn",), log=lambda *a, **k: None)
    assert _mpn_groups(r) == [("LM358DR", [("LM358DR", "dblib"), ("LM358DR/TR", "dblib")])], _mpn_groups(r)


@lite
def test_ds2_01_company_dblib_without_mpn_column_keys_on_part_number(tmp_path):
    """A company DbLib with no MPN column keeps the MPN in Part Number: a plan part of that MPN is that row."""
    import pandas as pd
    bom = tmp_path / "res.csv"
    bom.write_text("Designator,Qty,Value,Manufacturer Part Number,Footprint\nR1,1,10k,RC0603FR-0710KL,0603\n")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache", log=lambda *a, **k: None)
    (tmp_path / "company").mkdir()
    pd.DataFrame({"Part Number": ["RC0603FR-0710KL"], "Value": ["10k"], "Library Ref": ["RES"], "Footprint Ref": ["R0603"]}
                 ).to_excel(tmp_path / "company" / "company.xlsx", sheet_name="Resistors", index=False)
    r = DD.run(tmp_path / "lib" / "plan.json", libs=[tmp_path / "company"], kinds=("mpn",), log=lambda *a, **k: None)
    assert _mpn_groups(r) == [("RC0603FR0710KL", [("RC0603FR-0710KL", "dblib"), ("RES-0603-10k", "plan")])], _mpn_groups(r)


def _merged_onto_company(tmp_path):
    """PART1 (SOIC-8) merged by dedupe onto the 2nd footprint / symbol of a company PcbLib / SchLib -> plan path."""
    from ecadlib import altium_lib as AL, kicad_import as K
    from tests.test_release_check import SOIC, make
    it = make(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([it]))
    lib = tmp_path / "company"
    lib.mkdir()
    soic16 = (Path(__file__).parent.parent / "out" / "footprints" / "ecadlib.pretty" / "SOIC-16_3.9x9.9mm_P1.27mm.kicad_mod").read_text()
    fps = []
    for text, name in ((soic16, "SO16_HOUSE"), (SOIC, "SO8_HOUSE")):
        fp, t = K.convert_footprint(text, name)
        K.add_body(fp, t, None, None)
        fps.append(fp)
    AL.write_pcblib(lib / "Company.PcbLib", fps)
    pins = lambda n: [{"num": str(p), "name": f"P{p}", "type": "io", "side": "left"} for p in range(1, n + 1)]
    AL.write_schlib(lib / "Company.SchLib", [AL.ic_symbol("QUAD_HOUSE", pins(14)), AL.ic_symbol("PART_HOUSE", pins(8))])
    DD.run(plan, libs=[lib], do_merge=True, kinds=("footprint", "symbol"), log=lambda *a, **k: None)
    return plan


@lite
def test_ds2_02_merge_onto_multi_entry_library_checks_the_named_entry(tmp_path):
    """dedupe --merge points a plan item at one footprint / symbol of a company library that holds many. The release
    check and resolve read the file's first footprint (a SOIC-16 here: pads 9-16 without a pin) and the pins of every
    symbol in the SchLib (pins 9-14 without a pad), so the merged part was held on another part's geometry."""
    from ecadlib import release_check as RC, resolve as R
    plan = _merged_onto_company(tmp_path)
    it = json.loads(plan.read_text(encoding="utf-8"))[0]
    assert it["footprint"] == {"ref": "SO8_HOUSE", "file": "company/Company.PcbLib"}, it["footprint"]
    assert it["symbol"] == {"ref": "PART_HOUSE", "file": "company/Company.SchLib"}, it["symbol"]
    f, _, _ = RC.check_item(it, tmp_path)
    assert f == [], f
    assert [p for p, _ in R.item_pads(it, tmp_path)] == [str(n) for n in range(1, 9)], R.item_pads(it, tmp_path)
    assert R.item_pins(it, tmp_path) == [str(n) for n in range(1, 9)], R.item_pins(it, tmp_path)
    it["footprint"]["ref"] = "SO8_RENAMED"                 # renamed in the company library since: held, not the first one
    f, _, _ = RC.check_item(it, tmp_path)
    assert [r for r, m in f] == ["files"] and "SO8_RENAMED" in f[0][1] and "Company.PcbLib" in f[0][1], f
    assert R.item_pads(it, tmp_path) is None


@lite
def test_ds2_02_item_pads_still_raises_on_a_file_that_is_not_a_pcblib(tmp_path):
    """Only a ref the file does not hold means 'no pads known'; a .SchLib named as the footprint file is the reader's
    error, as item_pins raises for a .PcbLib named as the symbol file."""
    from ecadlib import resolve as R
    from tests.test_release_check import make
    it = make(tmp_path, keep_kmod=False)
    it["footprint"] = {"ref": "PART1", "file": "models/SchLib/PART1.SchLib"}
    try:
        got = R.item_pads(it, tmp_path)
    except ValueError as e:
        assert "not a .PcbLib" in str(e), e
    else:
        raise AssertionError(f"item_pads returned {got!r}")


@lite
def test_ds2_02_parity_export_mode_compares_the_named_entry(tmp_path):
    """kicad-export writes the footprint / symbol the plan names (SO8_HOUSE, PART_HOUSE); parity compared them with the
    file's first entries (the SOIC-16, the 14-pin symbol) and reported the merged part as a mismatch. A ref the file
    does not hold is an error, not the first entry."""
    from ecadlib import kicad_export as KX, parity as P
    plan = _merged_onto_company(tmp_path)
    KX.export(plan, tmp_path / "kicad", "house", log=lambda *a, **k: None)
    rep = P.check_plan(plan, kicad_dir=tmp_path / "kicad", second_reader=False)
    r = {p["part"]: p for p in rep["parts"]}["PART1"]
    assert r["result"] == "match", [d["msg"] for d in r["diffs"]]
    assert "footprint (8 pads)" in r["checked"] and "symbol (8 pins)" in r["checked"], r["checked"]
    items = json.loads(plan.read_text(encoding="utf-8"))
    items[0]["footprint"]["ref"] = "SO8_RENAMED"           # renamed in the company library since
    plan.write_text(json.dumps(items))
    r = P.check_plan(plan, kicad_dir=tmp_path / "kicad", second_reader=False)["parts"][0]
    assert r["result"] == "mismatch" and [d["level"] for d in r["diffs"]] == ["error"] \
        and "SO8_RENAMED" in r["diffs"][0]["msg"] and "Company.PcbLib" in r["diffs"][0]["msg"], r

@lite
def test_ds2_06_remap_bom_finds_house_header_sheet_behind_cover(tmp_path):
    """The BOM sheet has the house headers of ecadlib.toml [bom.columns]; a cover sheet with 'Part Number /
    Description / Qty' comes first. read_bom reads the BOM sheet; dedupe --bom picked the sheet without the house
    columns, wrote the cover sheet as the deduped BOM and remapped nothing."""
    import pandas as pd
    (tmp_path / "ecadlib.toml").write_text('[bom.columns]\ndesignator = "Bauteil"\nvalue = "Wert"\nmpn = "Herst.-Nr."\n'
                                           'footprint = "Gehäuse"\n', encoding="utf-8")
    x = tmp_path / "bom.xlsx"
    with pd.ExcelWriter(x) as w:
        pd.DataFrame({"Part Number": ["PCB-1234"], "Description": ["Sensor board assembly"], "Qty": [1]}).to_excel(
            w, sheet_name="Deckblatt", index=False)
        pd.DataFrame({"Bauteil": ["R1", "C1"], "Wert": ["10k", "100n"], "Herst.-Nr.": ["RC0603FR-0710KL", "GRM188R71H104KA93D"],
                      "Gehäuse": ["R0603_OLD", "C_0603_1608Metric"]}).to_excel(w, sheet_name="Stueckliste", index=False)
    out, n = DD.remap_bom(x, [{"kind": "footprint", "from": "R0603_OLD", "to": "R_0603_1608Metric"}], tmp_path / "deduped.csv")
    rows = out.read_text(encoding="utf-8-sig").splitlines()
    assert n == 1 and rows == ["Bauteil,Wert,Herst.-Nr.,Gehäuse", "R1,10k,RC0603FR-0710KL,R_0603_1608Metric",
                               "C1,100n,GRM188R71H104KA93D,C_0603_1608Metric"], (n, rows)


def _hidden_col_xlsx(path, rows, hidden="C"):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    for r in rows:
        ws.append(r)
    ws.column_dimensions[hidden].hidden = True            # an old column, kept hidden
    wb.save(path)
    return path


def _read_back(bom):
    from ecadlib import bom_triage as T
    return [(l["designator"], l.get("mpn"), l.get("footprint")) for l in T.read_bom(bom, {})[0]]


@lite
def test_ds2_07_remap_bom_keeps_hidden_columns(tmp_path):
    """remap_bom promises a copy with only LibRef / Footprint changed. It wrote triage's view of the sheet, where a
    hidden column is blanked (so an old hidden MPN or footprint is not read): the copy lost that column's header and
    every value. The hidden column is still not the one remapped, and a CSV cannot hide a column: the copy marks it,
    so read_bom (triage, the librarian) reads the copy as it reads the workbook, not the old values under the same
    header (first matching column wins)."""
    x = _hidden_col_xlsx(tmp_path / "bom.xlsx", (["Designator", "Value", "Footprint", "MPN", "Footprint"],
                                                 ["R1", "10k", "R0603_OLD", "RC0603FR-0710KL", "R0603_OLD"],
                                                 ["C1", "100n", "C0603_OLD", "GRM188R71H104KA93D", "C_0603_1608Metric"]))
    out, n = DD.remap_bom(x, [{"kind": "footprint", "from": "R0603_OLD", "to": "R_0603_1608Metric"}], tmp_path / "d.csv",
                          columns={})
    rows = out.read_text(encoding="utf-8-sig").splitlines()
    assert n == 1 and rows == ["Designator,Value,<hidden> Footprint,MPN,Footprint",
                               "R1,10k,R0603_OLD,RC0603FR-0710KL,R_0603_1608Metric",
                               "C1,100n,C0603_OLD,GRM188R71H104KA93D,C_0603_1608Metric"], (n, rows)
    assert _read_back(out) == [("R1", "RC0603FR-0710KL", "R_0603_1608Metric"),
                               ("C1", "GRM188R71H104KA93D", "C_0603_1608Metric")], _read_back(out)


@lite
def test_ds2_07_remap_bom_hidden_mpn_is_not_read_from_the_copy(tmp_path):
    """An old MPN kept in a hidden column: under the live column's own header (read first) and as the only MPN column
    (a header that merely contains 'MPN' is still mapped to the MPN). The copy reads back as the workbook does."""
    x = _hidden_col_xlsx(tmp_path / "dup.xlsx", (
        ["Designator", "Value", "Manufacturer Part Number", "Manufacturer Part Number", "Footprint"],
        ["U1", "LM358", "OLD-OBSOLETE-1", "LM358DR", "SOIC-8_OLD"]))
    out, n = DD.remap_bom(x, [{"kind": "footprint", "from": "SOIC-8_OLD", "to": "SOIC-8_HOUSE"}], tmp_path / "dup.csv",
                          columns={})
    assert n == 1 and _read_back(out) == [("U1", "LM358DR", "SOIC-8_HOUSE")], (n, _read_back(out))
    x = _hidden_col_xlsx(tmp_path / "only.xlsx", (["Designator", "Value", "MPN", "Footprint"],
                                                  ["R1", "10k", "OLD-OBSOLETE-2", "R0603_OLD"]))
    assert _read_back(x) == [("R1", None, "R0603_OLD")], _read_back(x)
    out, n = DD.remap_bom(x, [{"kind": "footprint", "from": "R0603_OLD", "to": "R_0603_1608Metric"}], tmp_path / "only.csv",
                          columns={})
    assert n == 1 and _read_back(out) == [("R1", None, "R_0603_1608Metric")], (n, _read_back(out))


def test_ds2_08_chip_size_before_a_word_starting_with_r_k_v_w():
    """A bare size code followed by a unit is a value ('1206 V', '1210 K'), not a size. The unit letters matched the
    first letter of any next word, so '0603 Resistor' or '0805 Vishay' named no size and the part found no
    standard footprint ('RES 0603' did)."""
    from ecadlib import stdparts as SP
    for text, want in (("0603 Resistor", "0603"), ("RES 0603 RoHS", "0603"), ("CAP 0805 Kemet", "0805"),
                       ("0805 Vishay", "0805"), ("1206 Walsin", "1206"),
                       ("1206 V", None), ("2010 W", None), ("1210 K", None), ("0603 R", None),   # a value and its unit
                       ("1210 kΩ", None), ("1210 kOhm", None)):
        assert SP.chip_size(text) == want, (text, SP.chip_size(text), want)
    assert str(SP.chip_size("0603 / 1005 Resistor")).startswith("conflict"), SP.chip_size("0603 / 1005 Resistor")
    r = SP.resolve(["0603 Resistor"], "R", "", "")
    assert r["footprint"] == "R_0603_1608Metric", r
