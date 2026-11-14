"""Fake-BOM edge cases found 2026-11-14 (generated BOMs with known answers, run through the free pipeline).
Plain functions, no pytest needed: python3 -m tests.run_all test_fake_bom_1114"""
from pathlib import Path

from ecadlib import librarian as L
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def _run(tmp_path, text, sources=("standard",)):
    d = Path(tmp_path)
    bom = d / "bom.csv"
    bom.write_text(text, encoding="utf-8")
    items = L.run(bom, out=d / "out", sources=list(sources), offline=True, cache=d / "cache", rules={}, log=QUIET)
    return {next(p["value"] for p in it["parameters"] if p["name"] == "Manufacturer Part Number"): it for it in items}


SIZE_CLASH = """Designator,Qty,Value,Footprint,MPN,Description
C1,1,100nF,C_0402_1005Metric,CF-1,CAP CER 0.1UF 50V X7R 0603
C2,1,10uF,Capacitor_SMD:C_0805_2012Metric,CF-2,Multilayer Ceramic Capacitors MLCC - SMD/SMT 1206 10uF 25V
R1,1,10k,R_0603_1608Metric,CF-3,RES 10K OHM 1% 1/10W 0402
R2,1,4k7,R_1206_3216Metric,CF-4,Thick Film Resistors - SMD 4.7K 0805 1%
L1,1,10uH,L_0805_2012Metric,CF-5,Inductor 10uH 1210
D1,1,RED,LED_0603_1608Metric,CF-6,LED red 0805
C3,1,100nF 0603,C_0402_1005Metric,CF-7,Capacitor
"""

SIZE_AGREE = """Designator,Qty,Value,Footprint,MPN,Description
R3,1,10k,R_0201_0603Metric,OK-1,Resistor 0201 (0603 Metric)
R4,1,10k,R_0603_1608Metric,OK-2,Resistor 0603 (1608 Metric)
C4,1,1uF,C_0603_1608Metric,OK-3,CAP CER 1UF 16V X5R 0603 (1608 Metric)
R5,1,0R,R_0805_2012Metric,OK-4,Jumper 0805
C5,1,100nF 0402,C_0402_1005Metric,OK-5,CAP CER 0.1UF 1005
R6,1,10k,R_0603_1608Metric,OK-6,RES 10K 1608M
C6,1,1uF,C_0805_2012Metric,OK-7,Capacitor 1uF
R7,1,22R,R_0402_1005Metric,OK-8,RC0402FR-0722RL 22R
"""


@lite
def test_footprint_name_and_description_naming_other_chip_sizes_hold(tmp_path):
    """A BOM whose footprint column names one chip size and whose description or value names another (a copy-paste
    slip) used to come out ready on the footprint column's size: one of the two is wrong, so a person picks."""
    for src in (("standard",),):
        got = _run(tmp_path, SIZE_CLASH, src)
        for mpn, it in got.items():
            assert it["status"] == "held", (src, mpn, it["source"], it["hold_reasons"])
            assert any("size" in r and "description" in r or "value" in r for r in it["hold_reasons"]), it["hold_reasons"]


@lite
def test_agreeing_or_metric_marked_sizes_still_release(tmp_path):
    got = _run(tmp_path, SIZE_AGREE)
    for mpn, it in got.items():
        assert it["status"] == "ready", (mpn, it["source"], it["hold_reasons"])


NO_NUMBER = """Designator,Qty,Value,Footprint,MPN,Description
R1,1,see note,R_0603_1608Metric,W3,Resistor
R2,1,Resistor,R_0603_1608Metric,W4,Resistor
C1,1,Capacitor,C_0603_1608Metric,W5,MLCC
C2,1,=SUM(A1),C_0603_1608Metric,W6,Capacitor
R3,1,"=HYPERLINK(""http://x"")",R_0402_1005Metric,W8,Resistor
"""

WITH_NUMBER = """Designator,Qty,Value,Footprint,MPN,Description
R1,1,10k,R_0603_1608Metric,N1,Resistor
R2,1,0R,R_0603_1608Metric,N2,Jumper
C1,1,100nF,C_0603_1608Metric,N3,MLCC
R3,1,,R_0603_1608Metric,N4,Resistor 4k7
C2,1,1u0,C_0603_1608Metric,N5,MLCC
"""


@lite
def test_resistor_or_capacitor_value_with_no_number_holds(tmp_path):
    """A generic resistor or capacitor is only its value: 'see note', 'Resistor' or a spreadsheet formula as the Value
    released a part whose placed value says nothing (or runs in a spreadsheet the parts table is opened in)."""
    got = _run(tmp_path, NO_NUMBER)
    assert set(got) == {"W3", "W4", "W5", "W6", "W8"}, sorted(got)
    for mpn, it in got.items():
        assert it["status"] == "held", (mpn, it["parameters"], it["hold_reasons"])
        assert any("Value" in r for r in it["hold_reasons"]), it["hold_reasons"]


@lite
def test_resistor_or_capacitor_values_with_numbers_still_release(tmp_path):
    got = _run(tmp_path, WITH_NUMBER)
    for mpn, it in got.items():
        assert it["status"] == "ready", (mpn, it["hold_reasons"])


def _read(tmp_path, text, name="bom.csv"):
    from ecadlib import bom_triage as T
    f = Path(tmp_path) / name
    f.write_bytes(text.encode("utf-8"))
    return T.read_bom(f)


FOREIGN = {
    "de": "Bezeichner;Anzahl;Wert;Gehäuse;Hersteller;Herstellernummer;Beschreibung\n"
          "R1;1;10k;0603;Yageo;RC0603FR-0710KL;Widerstand 10k 0603\n",
    "fr": "Repère;Quantité;Valeur;Boîtier;Fabricant;Référence fabricant;Description\n"
          "R1;1;10k;0603;Yageo;RC0603FR-0710KL;Résistance 10k 0603\n",
    "es": "Designador,Cantidad,Valor,Encapsulado,Fabricante,Número de parte del fabricante,Descripción\n"
          "R1,1,10k,0603,Yageo,RC0603FR-0710KL,Resistencia 10k 0603\n",
    "zh": "位号,数量,值,封装,制造商,型号,描述\nR1,1,10k,0603,Yageo,RC0603FR-0710KL,电阻 10k 0603\n",
    "ja": "リファレンス,数量,定数,パッケージ,メーカー,型番,説明\nR1,1,10k,0603,Yageo,RC0603FR-0710KL,抵抗 10k\n",
}


@lite
def test_bom_headers_in_other_languages_are_read(tmp_path):
    """German, French and Chinese BOMs were refused ('no BOM header found'); a Spanish one kept only the designator
    and description, so its parts had no value and no part number."""
    for lang, text in FOREIGN.items():
        d = Path(tmp_path) / lang
        d.mkdir()
        lines, mapping = _read(d, text)
        assert {"designator", "qty", "value", "footprint", "manufacturer", "mpn", "description"} <= set(mapping), (lang, mapping)
        ln = lines[0]
        assert (ln["designator"], ln["value"], ln["mpn"], ln["manufacturer"]) == ("R1", "10k", "RC0603FR-0710KL", "Yageo"), (lang, ln)


@lite
def test_house_words_in_other_languages_are_not_read_as_the_mpn(tmp_path):
    lines, mapping = _read(tmp_path, "Designator,Value,Artikelnummer,料号,Référence\nR1,10k,4711,A-1,RC0603\n")
    assert "mpn" not in mapping or mapping["mpn"] not in ("Artikelnummer", "料号"), mapping


@lite
def test_eagle_part_column_of_designators_is_the_designator(tmp_path):
    """EAGLE's BOM export heads its designators 'Part' (Part, Value, Device, Package, Description): they were read as
    the MPN, and a 10k resistor was released with Manufacturer Part Number 'R1'."""
    lines, mapping = _read(tmp_path, "Part,Value,Device,Package,Description\nR1,10k,R-EU_R0603,R0603,RESISTOR\n"
                                     "C1,100n,C-EUC0402,C0402,CAPACITOR\nU1,ATMEGA328P-AU,ATMEGA328P-AU,TQFP32-08,MCU\n")
    assert mapping.get("designator") == "Part" and "mpn" not in mapping, mapping
    assert [ln["designator"] for ln in lines] == ["R1", "C1", "U1"]


@lite
def test_part_column_of_real_part_numbers_stays_the_mpn(tmp_path):
    lines, mapping = _read(tmp_path, "Ref,Part,Value\nU1,LM358DR,LM358\nQ1,BC847B,BC847\nD1,SS34,SS34\n")
    assert mapping.get("mpn") == "Part" and mapping.get("designator") == "Ref", mapping


# ---------------------------------------------------------------- from the PR's own review
@lite
def test_eagle_part_column_with_eagle_only_designators(tmp_path):
    """U$1 (EAGLE's name for a part with no prefix), SV1 (header), SJ1 (solder jumper) are designators too: with them
    the Part column fell back to the MPN and R1 / C1 were released as part numbers."""
    lines, mapping = _read(tmp_path, "Part,Value,Device,Package\nR1,10k,R-EU_R0603,R0603\nC1,100n,C-EUC0603,C0603\n"
                                     "U$1,ATMEGA328P,ATMEGA328P,TQFP32\nSV1,,MA03-1,MA03-1\nSJ1,,SJ,SJ\nU$2,LM358,LM358,SO08\n")
    assert mapping.get("designator") == "Part" and "mpn" not in mapping, mapping


@lite
def test_part_number_equal_to_its_own_designator_is_dropped(tmp_path):
    lines, _ = _read(tmp_path, "Designator,Value,MPN\nR1,10k,R1\nC1,100n,GRM155R71C104KA88D\n")
    assert not lines[0].get("mpn") and lines[1]["mpn"] == "GRM155R71C104KA88D", lines


TWO_SIZES = """Designator,Qty,Value,Footprint,MPN,Description
C1,1,10uF,C_0402_1005Metric,TS-1,CAP 10uF 25V 0805/1206
C2,1,100nF,C_0402_1005Metric,TS-2,CAP 100nF 0603 (1608 Metric) 0805
"""

NOT_A_CLASH = """Designator,Qty,Value,Footprint,MPN,Description
R1,1,1210,R_0603_1608Metric,NC-1,Resistor 1210 ohm
R2,1,Jumper,R_0603_1608Metric,NC-2,Zero ohm link
C1,1,1uF,C_0805_2012Metric,NC-3,CAP 1uF 0805/1206
"""


@lite
def test_description_naming_two_other_sizes_holds(tmp_path):
    got = _run(tmp_path, TWO_SIZES)
    for mpn, it in got.items():
        assert it["status"] == "held" and any("size" in r for r in it["hold_reasons"]), (mpn, it["hold_reasons"])


@lite
def test_value_numbers_jumpers_and_a_size_among_two_release(tmp_path):
    got = _run(tmp_path, NOT_A_CLASH)
    for mpn, it in got.items():
        assert it["status"] == "ready", (mpn, it["hold_reasons"])


def test_size_clash_reads_only_land_names_not_lcsc_numbers():
    """easyeda:C1206 is LCSC part C1206, not a 1206 land."""
    st = {"footprint": {"source": "easyeda:C1206", "name": "C_0402_1005Metric"}}
    assert L.chip_size_clash(st, {"description": ""}, {"description": "CAP 100nF 0402"}) is None
    st = {"footprint": {"source": "kicad:Capacitor_SMD:C_0402_1005Metric"}}
    assert "0603" in (L.chip_size_clash(st, {}, {"description": "CAP 100nF 0603"}) or "")
