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
