"""Value, size and kind read from a resistor's / capacitor's maker part number (ecadlib.mpn_decode).
Plain functions: python3 -m tests.run_all test_mpn_decode"""
import random
from pathlib import Path

from ecadlib import librarian as L, mpn_decode as MD, stdparts as SP
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None

# real part numbers with the value and size their makers' datasheets give them
KNOWN = [
    ("RC0603FR-0710KL", "R", 10e3, "0603"), ("RC0402FR-074K7L", "R", 4.7e3, "0402"), ("RC0805FR-07100RL", "R", 100, "0805"),
    ("RC0603JR-070RL", "R", 0, "0603"), ("RL0603FR-070R1L", "R", 0.1, "0603"), ("RT0603BRD0710KL", "R", 10e3, "0603"),
    ("AC0603FR-071KL", "R", 1e3, "0603"), ("RC0402FR-7W10KL", "R", 10e3, "0402"), ("RC1206FR-0749K9L", "R", 49.9e3, "1206"),
    ("RC0603FR-071ML", "R", 1e6, "0603"), ("ERJ-3EKF1002V", "R", 10e3, "0603"), ("ERJ-3GEYJ103V", "R", 10e3, "0603"),
    ("ERJ-2RKF4991X", "R", 4.99e3, "0402"), ("ERJ-6ENF1001V", "R", 1e3, "0805"), ("ERJ-PA3F1002V", "R", 10e3, "0603"),
    ("ERJ-3RQFR22V", "R", 0.22, "0603"), ("CRCW060310K0FKEA", "R", 10e3, "0603"), ("CRCW0805100RFKEA", "R", 100, "0805"),
    ("CRCW04024K70FKED", "R", 4.7e3, "0402"), ("TNPW060310K0BEEA", "R", 10e3, "0603"), ("RMCF0603FT10K0", "R", 10e3, "0603"),
    ("RMCF0805JT4K70", "R", 4.7e3, "0805"), ("RC0603F103CS", "R", 10e3, "0201"), ("RC1608F1002CS", "R", 10e3, "0603"),
    ("RK73H1JTTD1002F", "R", 10e3, "0603"), ("RK73B1JTTD103J", "R", 10e3, "0603"), ("CR0603-FX-1002ELF", "R", 10e3, "0603"),
    ("CR0603-JW-103ELF", "R", 10e3, "0603"), ("0603WAF1002T5E", "R", 10e3, "0603"), ("0603WAJ0103T5E", "R", 10e3, "0603"),
    ("GRM188R71H104KA93D", "C", 100e-9, "0603"), ("GRM155R71C104KA88D", "C", 100e-9, "0402"),
    ("GRM21BR61E106KA73L", "C", 10e-6, "0805"), ("GRM1555C1H101JA01D", "C", 100e-12, "0402"),
    ("GRM31CR61A226KE15L", "C", 22e-6, "1206"), ("GCM188R71H104KA57D", "C", 100e-9, "0603"),
    ("CL10A106KP8NNNC", "C", 10e-6, "0603"), ("CL05A105KA5NQNC", "C", 1e-6, "0402"), ("CL21B104KBCNNNC", "C", 100e-9, "0805"),
    ("CL05C1R0BB5NNNC", "C", 1e-12, "0402"), ("C1608X7R1H104K080AA", "C", 100e-9, "0603"),
    ("C1005X5R1A105K050BC", "C", 1e-6, "0402"), ("C0603C104K5RACTU", "C", 100e-9, "0603"),
    ("C0805C106K8PACTU", "C", 10e-6, "0805"), ("C0805C109C5GACTU", "C", 1e-12, "0805"), ("C0402C103K4RACTU", "C", 10e-9, "0402"),
    ("CC0603KRX7R9BB104", "C", 100e-9, "0603"), ("CC0402JRNPO9BN101", "C", 100e-12, "0402"),
    ("06035C104KAT2A", "C", 100e-9, "0603"), ("08053C105KAT2A", "C", 1e-6, "0805"),
]
TANTALUM = [("T491A106K016AT", 10e-6, "3216-18"), ("T491B475K025AT", 4.7e-6, "3528-21"), ("TAJB226K016RNJ", 22e-6, "3528-21"),
            ("TAJA106K016RNJ", 10e-6, "3216-18"), ("TAJC107K010RNJ", 100e-6, "6032-28"), ("T491D107K016AT", 100e-6, "7343-31")]
NOT_READ = ["", "LM358DR", "BLM18PG221SN1D", "CRCW06030000Z0EA", "RC0603FR-0710K", "RC0603FR-0710KLX", "GRM188R71H104",
            "ERJ-3EKF1002", "C0603C104K5RAC-TU", "RC0603FR-07-10KL", "RC0603XR-0710KL", "T491E107K016AT", "TAJH107K010RNJ",
            "STM32F103C8T6", "1N4148W-7-F", "0466.500NR", "SRN4018-100M", "RC0603FR-07L", "GRM188R71H1Z4KA93D",
            "ERJ-3EKF10002V", "T491A106K016", "0603WAF1002", "RC0603FR-0710KL 10K"]


def _close(a, b):
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1e-15)


def test_known_part_numbers_decode_to_their_datasheet_value_and_size():
    for mpn, kind, value, size in KNOWN:
        d = MD.decode(mpn)
        assert d and d["kind"] == kind and _close(d["value"], value) and d["size"] == size, (mpn, d)
        k2, s2 = SP.mpn_size(mpn)                    # the size rule the release check already uses agrees
        assert s2 in (None, size), (mpn, s2, size)
    for mpn, value, case in TANTALUM:
        d = MD.decode(mpn)
        assert d and d["kind"] == "CP" and _close(d["value"], value) and d["case"] == case and d["size"] is None, (mpn, d)


def test_anything_not_a_whole_known_code_reads_as_nothing():
    for mpn in NOT_READ:
        assert MD.decode(mpn) is None, (mpn, MD.decode(mpn))


def test_value_text():
    got = {m: MD.value_text(MD.decode(m)) for m in ("RC0603FR-0710KL", "RC0402FR-074K7L", "RC0603JR-070RL",
                                                    "RL0603FR-070R1L", "RC0805FR-07100RL", "GRM188R71H104KA93D",
                                                    "CL10A106KP8NNNC", "GRM1555C1H101JA01D", "T491A106K016AT")}
    assert got == {"RC0603FR-0710KL": "10kΩ", "RC0402FR-074K7L": "4.7kΩ", "RC0603JR-070RL": "0Ω",
                   "RL0603FR-070R1L": "0.1Ω", "RC0805FR-07100RL": "100Ω", "GRM188R71H104KA93D": "100nF",
                   "CL10A106KP8NNNC": "10uF", "GRM1555C1H101JA01D": "100pF", "T491A106K016AT": "10uF"}, got
    for t in got.values():                           # every text reads back as a value of its kind
        cat = "Resistors" if t.endswith("Ω") else "Capacitors"
        assert L.passive_value(t, cat) is not None, t


def test_generated_codes_round_trip():
    """Codes built from random values the way each maker writes them decode back to that value."""
    rnd = random.Random(1114)
    def rkm(v):                                      # 4K7, 10K, 49K9, 100R, 1M (IEC 60062)
        mant, letter = (v / 1e6, "M") if v >= 1e6 else (v / 1e3, "K") if v >= 1e3 else (v, "R")
        t = f"{mant:g}"
        return t.replace(".", letter) if "." in t else t + letter
    for _ in range(400):
        m = rnd.choice([10, 12, 15, 22, 33, 47, 49.9, 68, 100, 120, 470, 499])
        e = rnd.choice([0.1, 1, 10, 100, 1000, 10000])
        v = round(m * e, 6)
        code = rkm(v)
        mpn = f"RC0603FR-07{code}L"
        d = MD.decode(mpn)
        assert d and _close(d["value"], v), (mpn, v, d)
    for _ in range(400):
        sig, p = rnd.randint(10, 99), rnd.randint(0, 6)
        mpn = f"GRM188R71H{sig}{p}KA93D"
        d = MD.decode(mpn)
        assert d and _close(d["value"], sig * 10 ** p * 1e-12), (mpn, d)


def _line(**k):
    return {"designator": "R1", "qty": "1", **k}


def test_fill_lines_fills_empty_values_and_holds_contradictions():
    lines = [_line(mpn="RC0603FR-0710KL"), _line(mpn="RC0603FR-0710KL", value="N/A"),
             _line(mpn="RC0603FR-0710KL", value="10k"), _line(mpn="RC0603FR-0710KL", value="4k7"),
             _line(designator="C1", mpn="RC0603FR-0710KL"), _line(designator="C2", mpn="GRM188R71H104KA93D", value="104"),
             _line(designator="C3", mpn="GRM188R71H104KA93D", value="10nF"), _line(designator="", mpn="RC0603FR-0710KL"),
             _line(designator="R5, C5", mpn="RC0603FR-0710KL"), _line(designator="C6", mpn="GRM188R71H104KA93D", value="100N"),
             _line(designator="C7", mpn="T491A106K016AT")]
    assert MD.fill_lines(lines) == 3
    assert lines[0]["value"] == "10kΩ" and "RC0603FR-0710KL" in lines[0]["value_from_mpn"]
    assert lines[1]["value"] == "10kΩ"
    assert lines[2]["value"] == "10k" and "bom_hold" not in lines[2]
    assert "4k7" in lines[3]["bom_hold"] and "10kΩ" in lines[3]["bom_hold"]
    assert "value" not in lines[4] and "bom_hold" not in lines[4]          # a resistor code on C1: not ours to fill
    assert lines[5]["value"] == "104" and "bom_hold" not in lines[5]       # an EIA code: left to the release check
    assert "bom_hold" in lines[6]
    assert "value" not in lines[7] and "value" not in lines[8]
    assert "bom_hold" not in lines[9]
    assert lines[10]["value"] == "10uF"


def _run(tmp_path, text):
    d = Path(tmp_path)
    (d / "bom.csv").write_text(text, encoding="utf-8")
    items = L.run(d / "bom.csv", out=d / "out", sources=["standard"], offline=True, cache=d / "cache", rules={}, log=QUIET)
    return {next(p["value"] for p in it["parameters"] if p["name"] == "Manufacturer Part Number"): it for it in items}


@lite
def test_part_number_only_resistors_and_capacitors_are_built_with_their_value(tmp_path):
    got = _run(tmp_path, "Designator,Qty,Value,Footprint,Manufacturer,MPN,Description\n"
                         "R1,1,,,Yageo,RC0603FR-0710KL,\nR2,1,,,Panasonic,ERJ-3EKF1002V,\n"
                         "C1,1,,,Murata,GRM188R71H104KA93D,\nC2,1,,,Samsung,CL10A106KP8NNNC,\n"
                         "R3,1,4k7,,Vishay,CRCW060310K0FKEA,\n")
    for mpn, value, desc in (("RC0603FR-0710KL", "10kΩ", "RES 10kΩ 0603"), ("ERJ-3EKF1002V", "10kΩ", "RES 10kΩ 0603"),
                             ("GRM188R71H104KA93D", "100nF", "CAP 100nF 0603"), ("CL10A106KP8NNNC", "10uF", "CAP 10uF 0603")):
        it = got[mpn]
        par = {p["name"]: p["value"] for p in it["parameters"]}
        assert it["status"] == "ready", (mpn, it["hold_reasons"])
        assert par["Value"] == value and it["description"] == desc, (mpn, par["Value"], it["description"])
        assert mpn in it["field_sources"]["Value"]
        assert it["source"]["footprint"].endswith("_0603_1608Metric"), it["source"]
    held = got["CRCW060310K0FKEA"]                    # Value 4k7 on a 10 kOhm part number: one of them is a slip
    assert held["status"] == "held" and any("CRCW060310K0FKEA" in r and "4k7" in r for r in held["hold_reasons"]), held["hold_reasons"]


def test_fill_lines_defers_to_and_checks_the_description():
    lines = [_line(mpn="RC0603FR-0710KL", description="RES 10K OHM 1% 0603"),      # says the same: its own spelling
             _line(mpn="RC0603FR-0710KL", description="RES 4.7K OHM 1% 0603"),     # says another value: held
             _line(mpn="RC0603FR-0710KL", description="RES SMD 1% 1/10W 0603")]     # says none: the code's value
    assert MD.fill_lines(lines) == 1
    assert "value" not in lines[0] and "bom_hold" not in lines[0]
    assert "value" not in lines[1] and "another value" in lines[1]["bom_hold"]
    assert lines[2]["value"] == "10kΩ"
