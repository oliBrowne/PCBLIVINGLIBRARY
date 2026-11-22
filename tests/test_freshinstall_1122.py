"""Real open-source BOMs run by a brand-new user (2026-11-22): what ecadlib read wrong, and the no-MPN passive rule.
Offline. Run:  python3 -m tests.test_freshinstall_1122"""
import tempfile
from pathlib import Path

from ecadlib import altium_export as AX, bom_triage as T, release_check as RC, rules as RU
from tests.test_release_check import fails, make


def _bom(text, name="bom.csv"):
    f = Path(tempfile.mkdtemp()) / name
    f.write_text(text, encoding="utf-8")
    return T.read_bom(f)


POCKETBEAGLE = '''Designator,Manufacturer/MPN,Qty,Description
"C1,C5,C7",YAGEO CC0402KRX7R7BB104,3,"Cap,100nF,10%,16V,X7R,0402,YAGEO"
POWER,C&K Components KMR231GLFS,1,SWITCH TACTILE SPST-NO 0.05A 32V
,"Nexperia 74LVC1G07GV,125",1,"IC,SINGLE BUFFER"
X2,"MTCONN MCTF-0403, Suntech ST-TF-003A",1,"Connector,MicroSD"
U9,LM358,1,op amp
'''


def test_maker_and_mpn_in_one_column():
    lines, mapping = _bom(POCKETBEAGLE)
    got = [(l.get("manufacturer", ""), l.get("mpn", "")) for l in lines]
    assert got == [("YAGEO", "CC0402KRX7R7BB104"), ("C&K Components", "KMR231GLFS"), ("Nexperia", "74LVC1G07GV,125"),
                   ("MTCONN", "MCTF-0403"), ("", "LM358")], got
    assert "alternates Suntech ST-TF-003A" in lines[3]["bom_note"], lines[3]


def test_plain_mpn_header_is_not_split():
    """'Manufacturer Part Number' is the MPN alone: a cell with a space keeps its old reading."""
    lines, _ = _bom("Designator,Manufacturer Part Number\nX1,FC-135 32.768KA-AG0\n")
    assert lines[0].get("manufacturer", "") == "", lines
    for h in ("Manufacturer/MPN", "Mfr / MPN", "Manufacturer & Part Number", "Mfr. / Part No.", "Mfr/PN"):
        assert T._MAKER_MPN_HEADER.fullmatch(h), h
    for h in ("Manufacturer Part Number", "MPN", "Mfr Part #", "Manufacturer"):
        assert not T._MAKER_MPN_HEADER.fullmatch(h), h


OCTOPART = '''Qty,Line Item,Manufacturer,MPN,Schematic Reference,Description,SKU,SKU,Unit Price (@ Batch Size: 1),Unit Price (@ Batch Size: 1)
,,,,,,Digi-Key,Mouser,Digi-Key,Mouser
2,WM50018-05-ND,Molex,22-28-4054,J1,,WM50018-05-ND,538-22-28-4054,0.45,0.41
1,IRLML6401TRPBFCT-ND,Infineon,IRLML6401TRPBF,Q1,,x,y,0.38,0.40
'''


def test_octopart_price_is_not_a_footprint():
    lines, mapping = _bom(OCTOPART)
    assert "footprint" not in mapping, mapping
    assert [l["mpn"] for l in lines] == ["22-28-4054", "IRLML6401TRPBF"], lines      # the supplier-name row is no part


def test_digikey_number_is_not_the_mpn():
    lines, _ = _bom("Designator,Value,Footprint,Manufacturer,Manufacturer Part Number\n"
                    "J1,GPIO1,Molex_Nano-Fit_105313-xx05,Molex,WM15144-ND\n"
                    "R1,10k,R_0603_1608Metric,,RC0603FR-0710KL\n")
    assert "mpn" not in lines[0] and lines[0]["digikey"] == "WM15144-ND", lines[0]
    assert "DigiKey order number" in lines[0]["bom_note"]
    assert lines[1]["mpn"] == "RC0603FR-0710KL"
    assert T.distributor_sku("490-3285-1-ND") == "DigiKey" and not T.distributor_sku("22-28-4054")
    rows = AX.to_rows(lines)
    row = next(r for df in rows.values() for _, r in df.iterrows() if r["Supplier Part Number 2"])
    assert row["Manufacturer Part Number"] == "" and row["Supplier 2"] == "DigiKey" and row["Supplier Part Number 2"] == "WM15144-ND"


def test_line_that_only_says_dnp_names_no_part():
    lines, _ = _bom("Line Item,Quantity,Designator,Footprint,Value,Manufacturer,Manufacturer Part Number\n"
                    '9,5,"J3,J6",DNP,DNP,DNP,DNP\n'
                    '12,2,"R1,R2",Resistor_SMD:R_0603_1608Metric,27R,Yageo,RC0603FR-0727RL\n')
    assert lines[0] == {"qty": "5", "designator": "J3,J6", "dnp": True}, lines[0]
    rows = AX.to_rows(lines)
    names = [r["Part Number"] for df in rows.values() for _, r in df.iterrows()]
    assert names == ["RES-0603-27R"], names


def test_any_maker_any_part_is_a_placeholder():
    lines, _ = _bom('Reference,Value,Footprint,MPN,Manufacturer\nC2,"1uF, 10V, X5R, 0402",C_0402,ANY,ANY\n')
    assert "bom_hold" not in lines[0] and "manufacturer" not in lines[0], lines[0]
    rows = AX.to_rows(lines)
    assert [r["Manufacturer Part Number"] for df in rows.values() for _, r in df.iterrows()] == [""]


def _passive(d, folder="Components/Resistors", sym="Resistor"):
    it = make(d, name="RES-0603-10k")
    it["folder"], it["symbol"]["ref"], it["librarian"] = folder, sym, {"passive": True}
    it["parameters"] = [p for p in it["parameters"] if p["name"] != "Manufacturer Part Number"]
    return it


def test_generic_resistor_without_mpn_is_a_note():
    hold_mpn = {**RU.DEFAULTS, "release": {**RU.DEFAULTS["release"], "mpn": "hold"}}
    with tempfile.TemporaryDirectory() as d:
        f, w = fails(_passive(d), d)
        assert "fields: Manufacturer Part Number is empty" not in f, f
        assert any(x.startswith("Manufacturer Part Number is empty: a generic part") for x in w), w
        assert "fields: Manufacturer Part Number is empty" in fails(_passive(d), d, hold_mpn)[0]
        assert "fields: Manufacturer Part Number is empty" in fails(_passive(d, "Components/Inductors", "Inductor"), d)[0]
        ic = _passive(d, "Components/ICs", "RES-0603-10k")
        ic["librarian"] = {"passive": False}
        assert "fields: Manufacturer Part Number is empty" in fails(ic, d)[0]


def test_release_mpn_rule_is_checked():
    assert RU.validate({"release": {"mpn": "hold"}})
    try:
        RU.validate({"release": {"mpn": "maybe"}})
    except RU.RulesError as e:
        assert "release.mpn" in str(e)
    else:
        raise AssertionError("release.mpn = 'maybe' was accepted")


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("ok")
