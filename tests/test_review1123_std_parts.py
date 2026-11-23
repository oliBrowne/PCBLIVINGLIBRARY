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


# ---------------------------------------------------------------- 1.1 a no-MPN R / C is released only when understood
NO_MPN = """Designator,Qty,Value,Footprint
C1,1,10uF tantalum,1206
C2,1,22uF 16V Tant,1210
C3,1,47uF electrolytic,1210
C4,1,100uF 6.3V polymer,1206
C5,1,15uF/16V/TAN,1206
R1,1,1k x4,1206
R2,1,3k*4,1206
R3,1,4.7k array,1206
R4,1,10k,0603
C6,1,100nF,0402
C7,1,100nF X7R 50V,0402
R5,1,4k7 1%,0805
"""


@lite
def test_no_mpn_passive_word_in_the_value_cell_is_held(tmp_path):
    plan = _run(tmp_path, NO_MPN)
    ready = sorted(n for n, i in plan.items() if i["status"] == "ready")
    assert ready == ["CAP-0402-100nF", "CAP-0402-100nFX7R50V", "RES-0603-10k", "RES-0805-4k71%"], \
        {n: i["status"] for n, i in plan.items()}
    for n, i in plan.items():
        if n not in ready:
            assert i["status"] != "ready" and not str((i.get("source") or {}).get("footprint") or "").startswith(
                "standard:Resistor_SMD") and "C_1" not in str((i.get("source") or {}).get("footprint") or ""), (n, i.get("source"))


@lite
def test_no_mpn_hold_names_the_word(tmp_path):
    plan = _run(tmp_path, "Designator,Qty,Value,Footprint\nR1,1,1k x4,1206\nR2,1,3k*4,1206\nR3,1,10k frobnic,0603\n"
                          "C1,1,10uF tantalum,1206\n")
    said = {n: _why(i) for n, i in plan.items()}
    assert any("'tantalum'" in w and "+ side" in w for w in said.values()), said
    assert all(i["status"] != "ready" for i in plan.values()), said
    assert any("'x4'" in w for w in said.values()), said
    assert any("'3k*4'" in w for w in said.values()), said
    assert any("'frobnic'" in w and "Manufacturer Part Number is empty" in w for w in said.values()), said


NO_MPN_2 = """Reference,Qty,Value,Footprint,Description,Comment
C1,1,10uF Tant,1206,CAP 10uF 16V 1206,
C2,1,22uF,1210,CAP 22uF 16V 1210,tantalum - observe polarity
C3,1,33uF,1210,CAP TAN 33uF 10V 1210,
R1,1,10k x4,1206,RES 10k 1%,
R2,1,22k,1206,RES 22k 1%,4-resistor array
R9,1,10k,0603,RES SMD 10K OHM 1% 1/10W 0603,
C9,1,0.1uF,0603,CAP CER 0.1UF 50V X7R 0603,
"""


@lite
def test_no_mpn_passive_word_in_comment_or_description_is_held(tmp_path):
    plan = _run(tmp_path, NO_MPN_2)
    ready = sorted(n for n, i in plan.items() if i["status"] == "ready")
    assert len(plan) == 7 and len(ready) == 2 and all(n.startswith(("RES-0603-10k", "CAP-0603-0.1uF")) for n in ready), \
        {n: (i["status"], _why(i)) for n, i in plan.items()}


EAGLE = """Partlist

Exported from demo.sch at 22.11.2026 10:00

EAGLE Version 9.6.2 Copyright (c) 1988-2020 Autodesk, Inc.

Assembly variant:

Part  Value  Device           Package       Library       Sheet

C1    100n   C-EUC0603        C0603         rcl           1
C5    22u    CPOL-EUC1206     C1206         rcl           1
C6    4u7    CPOL-USC0805     C0805         rcl           1
R1    10k    R-EU_R0603       R0603         rcl           1
"""


@lite
def test_eagle_polarized_device_is_held(tmp_path):
    plan = _run(tmp_path, EAGLE, "partlist.txt")
    st = {n: i["status"] for n, i in plan.items()}
    assert sorted(n for n, s in st.items() if s == "ready") == ["CAP-0603-100n", "RES-0603-10k"], st


def test_read_bom_keeps_the_cells_of_unmapped_columns():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "b.csv"
        p.write_text("Reference,Value,Footprint,Comment,Dielectric\nC1,22uF,1210,tantalum - observe polarity,\nC2,1uF,0603,,X7R\n")
        lines, _ = T.read_bom(p)
    assert "tantalum - observe polarity" in lines[0]["other_cells"] and "X7R" in lines[1]["other_cells"], lines


def test_understood_words():
    for kind, text in [("R", "10k"), ("R", "RES SMD 10K OHM 1% 1/10W 0603"), ("C", "CAP CER 0.1UF 50V X7R 0603"),
                       ("C", "100nF X7R 50V"), ("R", "4k7"), ("R", "0R"), ("R", "10 kOhm ±1 % 0.1 W"), ("C", "4u7"),
                       ("C", "0.1uF 16V 10% X5R 0402 (1005 Metric)"), ("R", "RES 10kΩ 1% 0603"), ("R", "4,7k"),
                       ("R", "Resistor, thick film, 0603, 10k, 1%, 100mW, ±100ppm/°C"), ("C", "22p C0G/NP0"),
                       ("C", "Capacitor MLCC 100nF 50V -55°C~125°C"), ("C", "Unpolarized capacitor, small symbol"),
                       ("R", "Resistor, US symbol"), ("C", "Capacitor (Semiconductor SIM Model)")]:
        assert SP.unread_words(text, kind) == [], (text, SP.unread_words(text, kind))
    for kind, text, word in [("C", "10uF tantalum", "tantalum"), ("C", "15uF/16V/TAN", "TAN"), ("R", "1k x4", "x4"),
                             ("R", "3k*4", "3k*4"), ("R", "4.7k array", "array"), ("C", "100uF 6.3V polymer", "polymer"),
                             ("C", "47uF electrolytic", "electrolytic"), ("C", "10uF chimique", "chimique"),
                             ("R", "10k 8P4R", "8P4R")]:
        assert SP.unread_words(text, kind) == [word], (text, SP.unread_words(text, kind))


def _item(value="10k", folder="Components/Resistors", sym="Resistor", **more):
    return dict({"name": "RES-0603-10k", "folder": folder, "symbol": {"ref": sym}, "librarian": {"passive": True},
                 "footprint": {"ref": "R_0603", "file": "x.PcbLib"},
                 "parameters": [{"name": "Value", "value": value}]}, **more)


def test_generic_passive_reads_the_whole_value():
    assert RC.generic_passive(_item())
    assert RC.generic_passive(_item("100nF X7R 50V", "Components/Capacitors", "Capacitor"))
    assert not RC.generic_passive(_item("10uF tantalum", "Components/Capacitors", "Capacitor"))
    assert not RC.generic_passive(_item("1k x4"))
    assert not RC.generic_passive(_item(generic_hold="the BOM line says 'tantalum'"))
    assert not RC.generic_passive(_item(description="RES ARRAY 4 RES 10K"))


def test_a_part_number_does_not_excuse_the_words_of_the_line():
    """With a part number the Value and the footprint cell are still read: an array is no two-pad chip."""
    assert SP.resolve(["1206"], "R", "RC1206FR-071KL", "", value="1k x4")["footprint"] is None
    assert SP.resolve(["1206 x4"], "R", "RC1206FR-071KL", "RES 1K")["footprint"] is None
    assert SP.resolve(["1206"], "R", "RC1206FR-071KL", "RES 1K 1%", value="1k")["footprint"] == "R_1206_3216Metric"


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


# ---------------------------------------------------------------- 1.3 multi-pad and variant words in the footprint text
def test_multi_pad_words_in_the_footprint_column_build_nothing():
    for kind, texts in [("R", ["RESNET 0603", "Resistor Network 0603", "R-PACK 0603", "0603 x4", "0603 X 4", "0603*4",
                               "0603 4R", "0603 8P4R", "QUAD 0603", "0603 ISO", "0603 bussed", "0603 network",
                               "0603 (4 resistors)", "0603 4-element", "1206 convex", "1206 concave", "0603 isolated x4",
                               "0603 4 PIN", "1206 4-terminal", "1206 4T", "1206 kelvin", "0805 4 pads", "2512 4-wire"]),
                        ("L", ["0805 common mode", "CMC 0805", "0805 CMC", "0805 2-line", "0805 4P", "0805 dual",
                               "1210 coupled", "0805 4 pin"]),
                        ("C", ["0603 feedthrough", "0603 3-terminal", "0603 3T", "0603 3 PAD", "0603 X2Y", "0603 x4"]),
                        ("LED", ["0603 bicolor", "0603 dual", "0603 2-color", "1210 RGB", "0603 R/G", "LED 0603 RG",
                                 "0603 side view", "0603 reverse mount", "0603 right angle"])]:
        for t in texts:
            r = SP.resolve([t], kind, "", "", value="10k" if kind == "R" else "")
            assert r["footprint"] is None and r["symbol"] is None, (kind, t, r)
    r = SP.resolve(["0603"], "LED", "", "LED RED CLEAR 0603 SMD R/A")
    assert r["footprint"] is None, r
    for kind, mpn, texts, desc in [("NTC", "", ["0603 4-pad"], "NTC"), ("NTC", "", ["0603 array"], "NTC")]:
        assert SP.resolve(texts, kind, mpn, desc)["footprint"] is None


def test_plain_footprint_texts_still_build():
    for kind, t, want in [("R", "0603", "R_0603_1608Metric"), ("R", "R_0603_1608Metric", "R_0603_1608Metric"),
                          ("R", "Resistor_SMD:R_0603_1608Metric", "R_0603_1608Metric"), ("C", "C0805", "C_0805_2012Metric"),
                          ("C", "0603 (1608 Metric)", "C_0603_1608Metric"), ("R", "RESC1608X55N", "R_0603_1608Metric"),
                          ("C", "CAP 0402", "C_0402_1005Metric"), ("R", "SMD 0603", "R_0603_1608Metric"),
                          ("LED", "LED 0603", "LED_0603_1608Metric"), ("L", "0805", "L_0805_2012Metric")]:
        r = SP.resolve([t], kind, "", "")
        assert r["footprint"] == want, (kind, t, r)


def test_package_variant_words_beside_the_name_build_nothing():
    for kind, t in [("D", "DO-215AA, SMB Gull Wing"), ("D", "DO-215AC, SMA Gull Wing"), ("Q", "TO-220 Full Pack"),
                    ("Q", "TO-220 FullPak"), ("U", "SOT-23 Thin"), ("D", "SMA FLAT"), ("Q", "TO-92 Formed Leads"),
                    ("D", "SOD-123 frobnic")]:
        r = SP.resolve([t], kind, "", "Diode" if kind == "D" else "")
        assert r["footprint"] is None, (kind, t, r)
    for kind, t, want in [("D", "DO-214AC, SMA", "D_SMA"), ("D", "SMA", "D_SMA"), ("D", "SOD-123", "D_SOD-123"),
                          ("D", "SMB (DO-214AA)", "D_SMB"), ("Q", "SOT-23", "SOT-23"), ("D", "SC-76, SOD-323", "D_SOD-323"),
                          ("D", "Diode_SMD:D_SOD-123", "D_SOD-123"), ("D", "SOD-123 SMD", "D_SOD-123")]:
        r = SP.resolve([t], kind, "", "Diode" if kind == "D" else "")
        assert r["footprint"] == want, (kind, t, r)


@lite
def test_footprint_column_words_end_to_end(tmp_path):
    plan = _run(tmp_path, """Designator,Value,Footprint,Description,MPN,Manufacturer
R10,10k,0603 x4,RES 10K 5%,CAT16-103J4LF,Bourns
R11,10k,Resistor Network 0603,RES 10K 5%,,
R12,0R01,1206 4-terminal,RES 0.01 OHM 1%,WSK1206R0100FEA,Vishay
R13,10k,0603 8P4R,RES 10K 5%,,
L10,90R,0805 CMC,90 OHM 400MA,ACM2012-900-2P-T002,TDK
C10,100nF,0603 3-terminal,CAP 0.1UF 16V,NFM18PC104R1C3D,Murata
D21,,"DO-215AA, SMB Gull Wing",TVS DIODE 5VWM unidirectional,SMBG5.0A,Vishay
R1,22k,0603,RES 22K OHM 1% 0603,,
""")
    st = {n: (i["status"], _fp(i)) for n, i in plan.items()}
    assert len(st) == 8 and [n for n, (s, _) in st.items() if s == "ready"] == ["RES-0603-22k"], st
    assert not [n for n, (s, fp) in st.items() if n != "RES-0603-22k" and fp.startswith("standard:")], st


# ---------------------------------------------------------------- 1.12 an unpolarized chip needs something to say ceramic
def test_capacitor_with_an_unread_part_number_needs_a_ceramic_word():
    for texts, mpn, desc in [(["1206"], "TMCMA1C106MTRF", "CAP 10uF 16V"), (["1206"], "T58A0106M016C0200", "CAP 10uF"),
                             (["0805"], "TACR106M010RTA", "CAP 10uF"), (["1206"], "CA45-A016K106T", "CAP 10uF 16V 1206"),
                             (["1206"], "TCA1C106M8R", "CAP 10uF"), (["1206"], "T110A106K016AT", "CAP"),
                             (["1206"], "ECS-T1CY106R", "CAP 10uF 16V"), (["0805"], "", "CAP TAN 10UF 16V"),
                             (["0805"], "", "CAP 10UF 16V POL"), (["1206"], "", "C-POL 10uF"), (["1206"], "", "CPOL 10uF")]:
        r = SP.resolve(texts, "C", mpn, desc)
        assert r["footprint"] is None and r["symbol"] is None, (mpn, desc, r)
    r = SP.resolve(["1206"], "C", "TMCMA1C106MTRF", "CAP 10uF 16V")
    assert "ceramic" in r["fp_how"] and "TMCMA1C106MTRF" in r["fp_how"], r


def test_ceramic_capacitors_still_build():
    for texts, mpn, desc, kw in [(["0603"], "GRM188R71H104KA93D", "", {}), (["0603"], "CL10B104KB8NNNC", "", {}),
                                 ([""], "GRM188R71H104KA93D", "", {}), (["0603"], "0603B104K500CT", "CAP CER 0.1UF 50V X7R 0603", {}),
                                 (["0603"], "885012206095", "100nF X7R", {}), (["0603"], "UMK107B7104KA-T", "MLCC 100nF", {}),
                                 (["0603"], "UMK107B7104KA-T", "", {"value": "100nF X7R"}),
                                 (["0603"], "UMK107B7104KA-T", "", {"search": "Multilayer Ceramic Capacitors MLCC - SMD/SMT"}),
                                 (["0402"], "", "", {"value": "100nF"}), (["0402"], "", "CAP 100nF 16V", {})]:
        r = SP.resolve(texts, "C", mpn, desc, **kw)
        assert r["footprint"] and r["footprint"].startswith("C_0") and r["symbol"] == "C", (mpn, desc, kw, r)


@lite
def test_unlisted_tantalum_series_end_to_end(tmp_path):
    plan = _run(tmp_path, "Designator,Value,Footprint,Description,MPN,Manufacturer\n"
                          "C12,10uF,1206,CAP 10UF 16V 10% 1206,TMCMA1C106MTRF,Vishay\n"
                          "C13,100nF,0603,CAP CER 0.1UF 50V X7R 0603,CL10B104KB8NNNC,Samsung\n"
                          "C14,100nF,0603,,GRM188R71H104KA93D,Murata\n"
                          "R14,10k,,,RC0603FR-0710KL,Yageo\n")
    st = {_mpn(i): (i["status"], _fp(i)) for i in plan.values()}
    assert sorted(n for n, (s, _) in st.items() if s == "ready") == ["CL10B104KB8NNNC", "GRM188R71H104KA93D", "RC0603FR-0710KL"], st
    assert not st["TMCMA1C106MTRF"][1].startswith("standard:"), st


# ---------------------------------------------------------------- a4 M1 the Value cell names no land by itself
def test_a_bare_number_in_the_value_cell_is_not_a_size():
    assert SP.resolve([""], "R", "", "RES 1% 1/4W", value="1210")["footprint"] is None
    assert SP.resolve([""], "R", "", "", value="2010")["footprint"] is None
    assert SP.resolve([""], "R", "", "", value="10k 0603")["footprint"] == "R_0603_1608Metric"
    assert SP.resolve([""], "C", "", "", value="100nF 0402")["footprint"] == "C_0402_1005Metric"
    assert SP.resolve(["0603"], "R", "", "", value="1210")["footprint"] == "R_0603_1608Metric"


@lite
def test_resistor_value_1210_without_a_footprint_is_held(tmp_path):
    plan = _run(tmp_path, "Designator,Value,Footprint,Description\nR3,1210,,RES 1% 1/4W\nR4,10k 0603,,\n")
    st = {n: (i["status"], str((i.get("source") or {}).get("footprint"))) for n, i in plan.items()}
    assert len(st) == 2 and not any("1210" in fp for _, fp in st.values()), st
    assert [fp for s, fp in st.values() if s == "ready"] == ["standard:Resistor_SMD:R_0603_1608Metric"], st


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


# ---------------------------------------------------------------- the ready count: ordinary lines stay ready
ORDINARY = """Designator,Value,Footprint,Description,MPN,Manufacturer
R1,10k,0603,,,
R2,22k,,,RC0603FR-0722KL,Yageo
C2,100nF,,,GRM188R71H104KA93D,Murata
C3,100nF,,,CL10B104KB8NNNC,Samsung
C6,10nF,0603,,,
C4,,0603,CAP CER 0.1UF 50V X7R 0603,,
R3,,0603,RES SMD 47K OHM 1% 1/10W 0603,,
R4,4k7,0805,RES 4.7K OHM 1% 1/8W 0805,RC0805FR-074K7L,YAGEO
C5,1uF,0603,CAP CER 1UF 16V X5R 0603,CL10A105KO8NNNC,Samsung Electro-Mechanics
L1,600R,0603,FERRITE BEAD 600 OHM 0603,BLM18AG601SN1D,Murata
D1,,SOD-123,DIODE GEN PURP 100V 150MA SOD123,1N4148W,
"""


@lite
def test_ordinary_lines_stay_ready(tmp_path):
    plan = _run(tmp_path, ORDINARY)
    st = {n: (i["status"], _why(i)) for n, i in plan.items()}
    assert len(plan) == 11 and all(s == "ready" for s, _ in st.values()), st
    assert all(_fp(i).startswith("standard:") for i in plan.values()), {n: _fp(i) for n, i in plan.items()}
