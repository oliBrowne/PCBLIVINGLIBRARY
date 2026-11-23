"""Full code review 2026-11-23, group std-parts: the standard-part rules release parts with no person looking, so
each of these was a wrong part released. Evidence: reviews/2026-11-23_full_code_review_evidence/a4 and a6.
Run:  python3 -m tests.run_all test_review1123_std_parts"""
import json

from ecadlib import bom_triage as T, librarian as L, mpn_decode as MD, release_check as RC, stdparts as SP
from tests.fixtures_lite import lite


def _run(tmp_path, bom_text, name="bom.csv"):
    """The librarian offline with the standard source only -> {part name: plan item}."""
    bom = tmp_path / name
    bom.write_text(bom_text, encoding="utf-8")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache",
          log=lambda *a, **k: None)
    return {i["name"]: i for i in json.loads((tmp_path / "lib" / "plan.json").read_text(encoding="utf-8"))}


def _why(item):
    return " | ".join(str(x) for x in (item.get("release_check") or {}).get("failures") or [])


def _mpn(item):
    return next((p["value"] for p in item["parameters"] if p["name"] == "Manufacturer Part Number"), "")


def _fp(item):
    return str((item.get("source") or {}).get("footprint") or "")


# ---------------------------------------------------------------- 1.2 a size only from a part number read whole
def test_another_makers_number_gives_no_size():
    for mpn in ["C0603X7R500-104KNE", "C0402X5R100-105KNP", "C0402C0G500-220JNE",       # Venkel: imperial, not TDK metric
                "RC0603F1002", "RC0603FR-07", "GRM188", "GRM188R71H1Z4KA93D", "C1608X7R500-104K", "ERJ-3EKF10002V"]:
        assert SP.mpn_size(mpn) == (None, None), (mpn, SP.mpn_size(mpn))
    assert SP.mpn_size("AC0603KRX7R9BB104") == ("C", "0603")            # Yageo's automotive MLCC, not an AC resistor
    assert MD.decode("AC0603KRX7R9BB104")["kind"] == "C"


def test_whole_numbers_still_give_their_size():
    for mpn, want in [("RC0603FR-0710KL", ("R", "0603")), ("GRM188R71H104KA93D", ("C", "0603")),
                      ("CL10B104KB8NNNC", ("C", "0603")), ("C1608X7R1H104K080AA", ("C", "0603")),
                      ("C0603C104K5RACTU", ("C", "0603")), ("C0603C104K5RAC-TU", ("C", "0603")),
                      ("RC0603F103CS", ("R", "0201")), ("ERJ-3EKF1002V", ("R", "0603")), ("ERJ-3GEY0R00V", ("R", "0603")),
                      ("CRCW060310K0FKEA", ("R", "0603")), ("CRCW06030000Z0EA", ("R", "0603")),
                      ("RMCF0603ZT0R00", ("R", "0603")), ("CR0603-FX-1002ELF", ("R", "0603")),
                      ("RK73H1JTTD1002F", ("R", "0603")), ("0603WAF1002T5E", ("R", "0603")), ("0402WGF1002TCE", ("R", "0402")),
                      ("BLM18AG601SN1D", ("L", "0603")), ("LQG15HS4N7S02D", ("L", "0402")), ("LQM21PN4R7MC0D", ("L", "0805")),
                      ("0603YC104KAT2A", ("C", "0603")), ("AT0603BRD0710KL", ("R", "0603"))]:
        assert SP.mpn_size(mpn) == want, (mpn, SP.mpn_size(mpn))


def test_a_rule_of_another_maker_than_the_lines_is_not_used():
    assert SP.mpn_size("C1608X7R1H104K080AA", "TDK Corporation") == ("C", "0603")
    assert SP.mpn_size("C1608X7R1H104K080AA", "Venkel") == (None, None)
    assert SP.mpn_size("RC0603FR-0710KL", "YAGEO") == ("R", "0603")
    assert SP.mpn_size("RC0603FR-0710KL", "") == ("R", "0603")
    assert SP.mpn_size("CRCW060310K0FKEA", "Vishay / Dale") == ("R", "0603")
    assert SP.resolve([""], "C", "C1608X7R1H104K080AA", "CAP CER 0.1UF 50V X7R", maker="Venkel")["footprint"] is None
    assert SP.resolve([""], "C", "C1608X7R1H104K080AA", "CAP CER 0.1UF 50V X7R", maker="TDK")["footprint"] == "C_0603_1608Metric"


@lite
def test_venkel_capacitor_is_not_built_as_an_0201(tmp_path):
    plan = _run(tmp_path, "Designator,Value,Footprint,Description,MPN,Manufacturer\n"
                          "C11,100nF,,CAP CER 0.1UF 50V X7R,C0603X7R500-104KNE,Venkel\n"
                          "C12,100nF,,CAP CER 0.1UF 50V X7R,GRM188R71H104KA93D,Murata\n")
    by = {_mpn(i): i for i in plan.values()}
    assert by["C0603X7R500-104KNE"]["status"] != "ready"
    assert "0201" not in str((by["C0603X7R500-104KNE"].get("source") or {}).get("footprint")), by["C0603X7R500-104KNE"].get("source")
    assert str(by["GRM188R71H104KA93D"]["source"]["footprint"]).endswith("C_0603_1608Metric")


# ---------------------------------------------------------------- a4 M3 a bidirectional TVS never gets a cathode
def test_bidirectional_tvs_never_gets_a_symbol_with_a_cathode():
    for fp, mpn, desc in [("D_SMB", "P6SMB6.8CA", ""), ("D_SMC", "1.5SMC33CA", "Diode"), ("D_SMB", "SMBJ33CA", "Diode"),
                          ("D_SMA", "", "Bidirectional thyristor surge suppressor SMA"),
                          ("D_SOD-323", "SD05C-01FTG", "TVS zener bidirectional 5V"), ("D_SMA", "SMAJ5.0CA", "TVS DIODE 5VWM")]:
        sym, how = SP.symbol_kind("D", mpn, fp, desc)
        assert sym == "D_TVS_Bidirectional", (mpn, desc, sym, how)
    assert SP.symbol_kind("D", "SMAJ5.0A", "D_SMA", "TVS DIODE 5VWM")[0] == "D_TVS"
    assert SP.symbol_kind("D", "P6SMB6.8A", "D_SMB", "Diode")[0] == "D_TVS"
    assert SP.symbol_kind("D", "SMAJ5.0A", "D_SMA", "TVS diode bidirectional")[0] is None       # the two disagree
    assert SP.symbol_kind("D", "SS14", "D_SMA", "Schottky 1A 40V")[0] == "D_Schottky"
    assert SP.symbol_kind("D", "MMSZ5231B", "D_SOD-123", "Zener 5.1V")[0] == "D_Zener"
    assert SP.symbol_kind("D", "1N4148W", "D_SOD-123", "Diode")[0] == "D"
