"""Needless holds found by running the real open-source BOMs of the 2026-11-23 sweep again on main after the review
fixes (#294): ceramic capacitor series the part-number reader did not know (so "nothing says it is ceramic" held
them), and maker names that are the same company written another way. The BOMs here are written for the test, in the
same layout as the real exports. Plain functions, no pytest needed: python3 -m tests.run_all test_real_boms_1124"""
import json

from ecadlib import librarian as L, mpn_decode as MD, providers as P, stdparts as SP
from tests.fixtures_lite import lite


def _run(tmp_path, bom_text, name="bom.csv"):
    bom = tmp_path / name
    bom.write_text(bom_text, encoding="utf-8")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache",
          log=lambda *a, **k: None)
    return {i["name"]: i for i in json.loads((tmp_path / "lib" / "plan.json").read_text(encoding="utf-8"))}


def _why(item):
    return " | ".join(str(x) for x in (item.get("release_check") or {}).get("failures") or [])


def _each(tmp_path, header, rows):
    out = []
    for n, row in enumerate(r for r in rows.strip().splitlines() if r.strip()):
        d = tmp_path / f"b{n}"
        d.mkdir()
        for i in _run(d, header + row + "\n").values():
            out.append((row, i["status"], _why(i)))
    return out


# Each part number with the value and EIA size its maker's code gives.
CERAMIC = [
    ("EMK107BJ105KA-T", "1uF", "0603"),             # Taiyo Yuden
    ("LMK107BBJ106MALT", "10uF", "0603"),           # Taiyo Yuden, BB = thin
    ("CGA3E2X7R1H103M080AA", "10nF", "0603"),       # TDK automotive
    ("C2012X5R1V226MT000E", "22uF", "0805"),        # TDK, T000E ending
    ("GRM0225C1H100GA03L", "10pF", "01005"),        # Murata 01005
    ("ZRB15XR61A106ME01D", "10uF", "0402"),         # Murata low-noise
    ("VJ0603Y101MXQCW1BC", "100pF", "0603"),        # Vishay VJ
    ("C0805C102K5GECAUTO", "1nF", "0805"),          # KEMET automotive, E in the ending
]


def test_more_ceramic_series_are_read():
    for mpn, value, size in CERAMIC:
        d = MD.decode(mpn)
        assert d and d["kind"] == "C" and d["size"] == size, (mpn, d)
        assert MD.value_text(d) == value, (mpn, d)


def test_wurth_mlcc_size_is_read():
    assert SP.mpn_size("885012206046", "Wurth Elektronik") == ("C", "0603")
    assert SP.mpn_size("885012207098", "Würth Elektronik") == ("C", "0805")
    assert SP.mpn_size("885012205037", "WE")[1] == "0402"
    assert SP.mpn_size("885012209006", "Wurth")[1] == "1210"
    assert SP.mpn_size("885012206046", "Murata")[0] is None     # another maker named: not read as Würth's
    assert SP.mpn_size("885010206046", "Wurth Elektronik")[0] is None   # only the 885012 (WCAP-CSGP) series


def test_a_leaded_tdk_capacitor_is_not_read_as_a_chip():
    assert MD.decode("FA18X7R1E105KRU06") is None               # radial leaded: no chip size in the code


def test_maker_names_of_the_same_company():
    for a, b in [("UniOhm", "UNI-ROYAL"), ("Dale", "Vishay"), ("Yageo Group", "KEMET"), ("Yaego", "Yageo"),
                 ("Tayo Yuden", "Taiyo"), ("KEMET Tokin", "KEMET"), ("Phycomp", "Yageo"), ("TDK-EPC", "TDK")]:
        assert P.same_maker(a, b), (a, b)
    for a, b in [("KEMET", "Murata"), ("TDK-EPC", "Murata"), ("Dale", "Yageo"), ("Taiyo Yuden", "TDK"), ("Phycomp", "Murata")]:
        assert not P.same_maker(a, b), (a, b)


@lite
def test_new_ceramic_series_release_on_their_own_land(tmp_path):
    head = "Reference,Value,Footprint,MPN,Manufacturer,Description\n"
    got = _each(tmp_path, head, """
C1,1uF,Capacitor_SMD:C_0603_1608Metric,EMK107BJ105KA-T,Taiyo Yuden,
C2,10nF,Capacitor_SMD:C_0603_1608Metric,CGA3E2X7R1H103M080AA,TDK,
C3,100pF,Capacitor_SMD:C_0603_1608Metric,VJ0603Y101MXQCW1BC,Vishay,
C4,1nF,Capacitor_SMD:C_0805_2012Metric,C0805C102K5GECAUTO,KEMET,
""")
    assert len(got) == 4 and all(s == "ready" for _, s, _ in got), got


@lite
def test_new_ceramic_series_still_hold_a_wrong_size_or_value(tmp_path):
    head = "Reference,Value,Footprint,MPN,Manufacturer,Description\n"
    got = _each(tmp_path, head, """
C1,1uF,Capacitor_SMD:C_1206_3216Metric,EMK107BJ105KA-T,Taiyo Yuden,
C2,100nF,Capacitor_SMD:C_0603_1608Metric,CGA3E2X7R1H103M080AA,TDK,
C3,1uF,Capacitor_SMD:C_1206_3216Metric,885012206046,Wurth Elektronik,
""")
    assert len(got) == 3 and all(s != "ready" for _, s, _ in got), got


# KiCad's BOM export with the Simplified Chinese user interface: 指示符 = Designator (Traditional: 指定符).
CN_KICAD_SIMPLIFIED = """"ID";"指示符";"封装";"数量";"名称";"供应商和参考";
1;"R28,R26,R31";"0204_7";3;"10k";;;
2;"LED9,LED5";"LED3MM";2;"LED3MM";;;
3;"C20,C21";"E2,5-7";2;"100uF";;;
"""


@lite
def test_simplified_chinese_kicad_designator_header(tmp_path):
    from ecadlib import bom_triage as BT
    p = tmp_path / "bom.csv"
    p.write_text(CN_KICAD_SIMPLIFIED, encoding="utf-8")
    lines, mapping = BT.read_bom(p)
    assert mapping.get("designator") == "指示符" and mapping.get("footprint") == "封装", mapping
    assert [d["designator"] for d in lines] == ["R28,R26,R31", "LED9,LED5", "C20,C21"]
    assert "value" not in mapping                                   # 名称 (a name) is still not guessed


def test_a_maker_name_the_maker_check_accepts_gives_the_size_too():
    # 'Yaego' is now Yageo to the maker check, so the size reader must take it as Yageo's too: else the size check is
    # silent and a 0603 part on an 0402 land is released (found on a real BOM: 'Yaego RC0603JR-07240RL' on 0402-RES).
    assert SP.mpn_size("RC0603JR-07240RL", "Yaego") == ("R", "0603")
    assert SP.mpn_size("LMK212BBJ226MG-T", "Tayo Yuden") == ("C", "0805")
    assert SP.mpn_size("RC0603JR-07240RL", "Murata") == (None, None)
    for name in ["Yaego", "Tayo Yuden", "UniOhm", "Dale", "Yageo Group"]:
        for key in ("yageo", "kemet", "taiyo", "uni-royal", "vishay"):
            if P.same_maker(name, {"taiyo": "Taiyo Yuden"}.get(key, key)):
                assert SP.maker_is(key, name), (key, name)


@lite
def test_a_misspelt_maker_still_holds_a_size_clash(tmp_path):
    head = "Qty,Value,Parts,Device,Package,Description,MANUFACTURER AND PART NUMBER\n"
    plan = _run(tmp_path, head + '8,240,"R21, R22",RESISTOR0402-RES,0402-RES,Resistor,Yaego RC0603JR-07240RL\n')
    assert plan and all(i["status"] != "ready" for i in plan.values()), {n: (i["status"], _why(i)) for n, i in plan.items()}
    d = tmp_path / "ok"
    d.mkdir()
    plan = _run(d, head + '8,240,"R21, R22",RESISTOR0402-RES,0402-RES,Resistor,Yaego RC0402JR-07240RL\n')
    assert plan and all(i["status"] == "ready" for i in plan.values()), {n: (i["status"], _why(i)) for n, i in plan.items()}


# A parts list with no designators (a purchase list): the Value still has to agree with the part number's code.
NO_DESIGNATORS = """Category,Quantity,Part Number,Description,Manufacturer,Value,Package,Notes
Motor Control,8,ERJ-6ENF1002V,1kΩ Resistor,Panasonic,1kΩ,0805,RC filter for encoders
Sensors,2,ERJ-6ENF4701V,4.7kΩ Resistor,Panasonic,4.7kΩ,0805,I2C pull-up resistor
"""


def test_value_against_part_number_without_designators():
    L = [{"mpn": "ERJ-6ENF1002V", "value": "1kΩ"}, {"mpn": "ERJ-6ENF4701V", "value": "4.7kΩ"},
         {"mpn": "GRM188R71H104KA93D", "value": "10nF"}, {"mpn": "ERJ-6ENF1003V", "value": ""}]
    MD.fill_lines(L)
    assert "10kΩ" in L[0].get("bom_hold", "") and not L[1].get("bom_hold"), L
    assert "100nF" in L[2].get("bom_hold", ""), L
    assert L[3]["value"] == "" and not L[3].get("bom_hold")      # nothing is filled in without a designator


@lite
def test_value_against_part_number_without_designators_end_to_end(tmp_path):
    plan = _run(tmp_path, NO_DESIGNATORS)
    st = {n: i["status"] for n, i in plan.items()}
    assert any("1k" in n for n in st) and all(s != "ready" for n, s in st.items() if "-1k" in n), st
    assert any(s == "ready" for n, s in st.items() if "4.7k" in n), st
