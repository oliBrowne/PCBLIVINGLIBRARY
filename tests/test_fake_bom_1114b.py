"""Fake-BOM round 2 (2026-11-14): bigger generated BOMs with maker part numbers, distributor descriptions, THT,
crystals, fuses, MELF, connectors. Plain functions: python3 -m tests.run_all test_fake_bom_1114b"""
from pathlib import Path

from ecadlib import librarian as L, release_check as RC
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def _run(tmp_path, text):
    d = Path(tmp_path)
    (d / "bom.csv").write_text(text, encoding="utf-8")
    items = L.run(d / "bom.csv", out=d / "out", sources=["standard"], offline=True, cache=d / "cache", rules={}, log=QUIET)
    return {next(p["value"] for p in it["parameters"] if p["name"] == "Manufacturer Part Number"): it for it in items}


RATING_ONLY = """Designator,Qty,Value,Footprint,MPN,Description
R1,1,50V,R_0603_1608Metric,V1,Resistor
R2,1,1%,R_0603_1608Metric,V2,Resistor
R3,1,1/10W,R_0603_1608Metric,V3,Resistor
C1,1,X7R,C_0603_1608Metric,V4,Capacitor
C2,1,25V X5R,C_0603_1608Metric,V5,Capacitor
C3,1,0603,C_0603_1608Metric,V6,Capacitor
C4,1,AEC-Q200,C_0603_1608Metric,V7,Capacitor
"""

VALUE_AND_RATING = """Designator,Qty,Value,Footprint,MPN,Description
R1,1,10k 1%,R_0603_1608Metric,W1,Resistor
R2,1,4k7 50V,R_0603_1608Metric,W2,Resistor
R3,1,100R 1/10W,R_0603_1608Metric,W3,Resistor
R4,1,R047,R_0603_1608Metric,W4,Resistor
C1,1,100nF/50V,C_0603_1608Metric,W5,Capacitor
C2,1,10uF X5R,C_0603_1608Metric,W6,Capacitor
C3,1,104,C_0603_1608Metric,W7,Capacitor
C4,1,"4,7µF",C_0603_1608Metric,W8,Capacitor
"""


@lite
def test_resistor_or_capacitor_value_holding_only_a_rating_holds(tmp_path):
    """A Value of '50V', 'X7R', '1%' or the chip size released a resistor or capacitor whose placed value says nothing
    about which part it is (the rating column pasted into Value)."""
    got = _run(tmp_path, RATING_ONLY)
    for mpn, it in got.items():
        assert it["status"] == "held", (mpn, it["parameters"])
        assert any("rating" in r for r in it["hold_reasons"]), it["hold_reasons"]


@lite
def test_value_with_a_rating_beside_it_still_releases(tmp_path):
    got = _run(tmp_path, VALUE_AND_RATING)
    for mpn, it in got.items():
        assert it["status"] == "ready", (mpn, it["hold_reasons"])


def test_names_value_spellings():
    for v in ("10k", "4k7", "4,7k", "4.7 kΩ", "47k Ohm", "R047", "0R", "1K0", "100m", "10K_1%_0603", "1M0", "１０ｋ",
              "2.2kohm0.1W", "1210", "0,047 0201/1%"):
        assert RC._names_value(v, "resistance"), v
    for v in ("50V", "1%", "0603", "1/10W", "±1%", "100ppm", "0.1W", "1/4W 5%"):
        assert not RC._names_value(v, "resistance"), v
    for v in ("100nF", "0.1uF", "104", "2u2", "1u 16V", "4,7µF", ".1u", "1n0", "2p2", "22p", "220", "10uF X5R"):
        assert RC._names_value(v, "capacitance"), v
    for v in ("50V", "X7R", "25V X5R", "0603", "C0G", "10% 50V"):
        assert not RC._names_value(v, "capacitance"), v


def test_names_value_packed_ratings_and_large_chip_sizes():
    """Review of #250: rating columns pasted with commas or hyphens ('50V,X7R,0603'), DC/AC and RKM voltages
    ('50VDC', '6V3'), NPO with the letter O, a bare wattage fraction and chip sizes 1206 and up released."""
    for word in ("resistance", "capacitance"):
        for v in ("50V,X7R,0603", "1%,0603", "X7R,0603", "50V,0603", "X7R-0603", "1206-0603", "0805/1206", "50V-0603", "0603-X7R", "１％，０６０３",
                  "50VDC", "16Vdc", "250VAC", "50 VDC", "6V3", "2V5", "NPO 50V", "50V NPO", "1/8", "1/4", "1206",
                  "2512", "1812", "2220", "2010", "1206 50V X7R"):
            assert not RC._names_value(v, word), (word, v)
    for v in ("1210", "10k-1%", "RC0603FR-0710KL", "1,5k", "0R", "1k/1%", "330R 1/4W", "121", "1k21", "CRCW060310K0FKEA"):
        assert RC._names_value(v, "resistance"), v
    for v in ("100n-50V", "GRM188R71H104KA93D", "47pF NPO", "6p8", "2u2 6V3", "0,1µF", "100nF 50VDC"):
        assert RC._names_value(v, "capacitance"), v


def test_not_fitted_word_beside_a_rating_gets_the_not_fitted_message():
    item = {"folder": "Components/Capacitors", "parameters": [{"name": "Value", "value": "NC 0603"}]}
    assert "not fitted" in RC.value_number_problem(item)
    item["parameters"][0]["value"] = "DNP"
    assert "not fitted" in RC.value_number_problem(item)
    item["parameters"][0]["value"] = "100nF 50V"
    assert RC.value_number_problem(item) is None
