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
