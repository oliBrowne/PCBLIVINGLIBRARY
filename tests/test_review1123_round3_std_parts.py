"""Round 3 of the 2026-11-23 review, standard parts: the second independent review of the fix branch (ROUND3_FINDINGS.md:
A1, A2, A3, A4, A8, B3, B4 first item and the Minor items on prefixes, a ceramic number on a polarized land, a later
line's Manufacturer cell and the symbol name's last digit), closed by structure and not by more spellings; and the
ordinary resistor and capacitor lines of real BOMs that the round-1 word rule held for a word that says nothing about
how the part is built (18 % of the ready parts of 1,449 open-source BOMs). Each part has must-hold and must-stay-ready
lines; the BOM lines are written here in the shapes found, none is copied.
Run:  python3 -m tests.run_all test_review1123_round3_std_parts"""
import json
import tempfile
from pathlib import Path

from ecadlib import bom_triage as T, librarian as L, stdparts as SP
from tests.fixtures_lite import lite

H = "Designator,Qty,Value,Footprint,Description\n"


def _run(tmp_path, bom_text, name="bom.csv"):
    """The librarian offline with the standard source only -> {part name: plan item}."""
    bom = tmp_path / name
    bom.write_text(bom_text, encoding="utf-8")
    L.run(bom, out=tmp_path / "lib", sources=["standard"], offline=True, cache=tmp_path / "cache",
          log=lambda *a, **k: None)
    return {i["name"]: i for i in json.loads((tmp_path / "lib" / "plan.json").read_text(encoding="utf-8"))}


def _why(item):
    return " | ".join(str(x) for x in (item.get("release_check") or {}).get("failures") or [])


def _fp(item):
    return str((item.get("source") or {}).get("footprint") or "")


def _status(plan):
    return {n: (i["status"], _why(i)[:260]) for n, i in plan.items()}


def _each(tmp_path, header, rows):
    """Every row alone under the header -> [(row, status, why, footprint source)]."""
    out = []
    for n, row in enumerate(r for r in rows.strip().splitlines() if r.strip()):
        d = tmp_path / f"b{len(list(tmp_path.iterdir()))}_{n}"
        d.mkdir()
        for i in _run(d, header + row + "\n").values():
            out.append((row, i["status"], _why(i), _fp(i)))
    return out


def _read(text):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "b.csv"
        p.write_text(text, encoding="utf-8")
        return T.read_bom(p)[0]


def _hold(bom):
    """generic_passive_hold for the first line of a BOM text, read as the librarian reads it."""
    ln = _read(bom)[0]
    cat = "Capacitors" if ln["designator"].startswith("C") else "Resistors"
    return SP.generic_passive_hold(cat, ln.get("value", ""), ln.get("description", ""), (), T.line_cells(ln))


# ---------------------------------------------------------------- A1 the prefix glued to a size code
def test_a_size_code_with_the_prefix_of_another_kind_is_not_understood():
    for kind, text in [("C", "CP1206"), ("C", "CP0805"), ("C", "cp1206"), ("C", "Capacitors:CP1206"), ("C", "LED1206"),
                       ("C", "D1206"), ("C", "L1206"), ("C", "R1206"), ("C", "F1206"), ("R", "C0603"), ("R", "L0603"),
                       ("R", "CC0603"), ("R", "LED0603"), ("R", "FB0603"), ("R", "D0603"), ("R", "CP0603"), ("L", "C0805"),
                       ("LED", "R0603")]:
        assert SP.unread_words(text, kind, footprint=True), (kind, text)
        r = SP.resolve([text], kind, "", "", value="10uF" if kind == "C" else "10k" if kind == "R" else "")
        assert r["footprint"] is None and (r["symbol"] is None or "no standard package" in r["fp_how"]), (kind, text, r)
    assert SP.polar_size_prefix("CP1206") and SP.polar_size_prefix("Capacitors:cp0805") and SP.polar_size_prefix("CP_EIA-3216-18_Kemet-A")
    assert not SP.polar_size_prefix("C1206") and not SP.polar_size_prefix("CAP1206") and not SP.polar_size_prefix("1206")
    assert "another kind" in SP.resolve(["LED1206"], "C", "", "", value="10uF")["fp_how"]
    for kind, text, want in [("C", "C1206", "C_1206_3216Metric"), ("C", "CAP1206", "C_1206_3216Metric"),
                             ("C", "CC1206", "C_1206_3216Metric"), ("C", "SMD1206", "C_1206_3216Metric"),
                             ("R", "R0603", "R_0603_1608Metric"), ("R", "RES0603", "R_0603_1608Metric"),
                             ("R", "RC0603", "R_0603_1608Metric"), ("R", "CHIP0603", "R_0603_1608Metric"),
                             ("LED", "LED0603", "LED_0603_1608Metric"), ("L", "L0805", "L_0805_2012Metric")]:
        assert SP.resolve([text], kind, "", "")["footprint"] == want, (kind, text)


@lite
def test_prefix_contradictions_end_to_end(tmp_path):
    got = _each(tmp_path, H, """
C1,1,10uF,CP1206,
C2,1,10uF,cp1206,
C3,1,10uF,Capacitors:CP1206,
C4,1,10uF,LED1206,
C5,1,10uF,R1206,
R1,1,10k,C0603,
R2,1,10k,LED0603,
R3,1,10k,CP0603,
C6,1,10uF CP1206,1206,
""")
    assert len(got) == 9 and all(s != "ready" and not fp.startswith("standard:") for _, s, _, fp in got), got
    got = _each(tmp_path, H, "C1,1,10uF,C1206,\nC2,1,10uF,CAP1206,\nR1,1,10k,R0603,\nR2,1,10k,RES0603,\n")
    assert len(got) == 4 and all(s == "ready" for _, s, _, _ in got), got


# ---------------------------------------------------------------- A2 the symbol's name, and what another column says
def test_a_symbol_cell_must_name_the_plain_symbol():
    for kind, cell in [("C", "Device:CP"), ("C", "Device:CP_Small"), ("C", "CP"), ("C", "CP1"), ("C", "Cap2"), ("C", "C2"),
                       ("C", "Cap Var"), ("C", "Cap Feed"), ("C", "Cap Pol1"), ("C", "Device:C_Polarized"),
                       ("C", "Device:C_Variable"), ("C", "Device:C_Feedthrough"), ("C", "Device:D"), ("C", "CPOL-EUC1206"),
                       ("R", "Res Adj1"), ("R", "Res Tap"), ("R", "Res Bridge"), ("R", "Res Thermal"), ("R", "Res Varistor"),
                       ("R", "Res Pack4"), ("R", "Device:Thermistor_NTC"), ("R", "Device:Varistor"), ("R", "Device:FerriteBead"),
                       ("R", "Device:LED"), ("R", "Device:R_Pack04"), ("R", "Device:R_Potentiometer"), ("R", "Device:Lamp"),
                       ("R", "R-EU_M0805"), ("R", "Fuse 1"), ("R", "R2")]:
        assert SP.cell_hold(kind, "symbol", "LibRef", cell), (kind, cell)
    for kind, cell, lands in [("C", "Device:C", ()), ("C", "Device:C_Small", ()), ("C", "Cap", ()), ("C", "Cap Semi", ()),
                              ("C", "Capacitor", ()), ("C", "pspice:C", ()), ("C", "C-EUC0603", ("C0603",)),
                              ("C", "C-USC0805", ("C0805",)), ("C", "CAP0603-CAP", ("0603-CAP",)), ("C", "NP_CAP0603", ()),
                              ("C", "CapCer", ()), ("C", "CAP_0603_16V", ()), ("C", "Capacitor - non polarized", ()),
                              ("R", "Device:R", ()), ("R", "Device:R_Small_US", ()), ("R", "Res1", ()), ("R", "Res2", ()),
                              ("R", "Res3", ()), ("R", "Resistor", ()), ("R", "RES_0603", ()), ("R", "R-EU_R0603", ("R0603",)),
                              ("R", "R-US_R0805", ("R0805",)), ("R", "RESISTOR0603", ("0603",)), ("R", "ResChip", ()),
                              ("R", "R_100R_0402_1%", ()), ("R", "CMP-1035-02800-1", ()), ("R", "libref", ()), ("R", "", ())]:
        assert SP.cell_hold(kind, "symbol", "LibRef", cell, lands) is None, (kind, cell, SP.cell_hold(kind, "symbol", "LibRef", cell, lands))


def test_the_last_digit_of_a_symbol_name_is_part_of_it():
    assert SP.plain_symbol_name("Res1", "R") and SP.plain_symbol_name("Res2", "R") and SP.plain_symbol_name("Res3", "R")
    assert SP.plain_symbol_name("Cap", "C") and SP.plain_symbol_name("C", "C") and SP.plain_symbol_name("Device:C", "C")
    for kind, name in [("C", "Cap2"), ("C", "C2"), ("C", "C1"), ("C", "CP1"), ("R", "R2"), ("R", "Res4"), ("R", "Res9")]:
        assert not SP.plain_symbol_name(name, kind), (kind, name)


SYMBOL_ROWS = [("Reference,Value,Footprint,LibPart\n", "C2,10uF,Capacitor_SMD:C_1206_3216Metric,Device:CP"),
               ("Reference,Value,Footprint,LibPart\n", "C3,10uF,Capacitor_SMD:C_1206_3216Metric,Device:CP_Small"),
               ("Reference,Value,Footprint,LibPart\n", "R2,10k,Resistor_SMD:R_0603_1608Metric,Device:Thermistor_NTC"),
               ("Reference,Value,Footprint,LibPart\n", "R3,10k,Resistor_SMD:R_0603_1608Metric,Device:Varistor"),
               ("Reference,Value,Footprint,LibPart\n", "C5,10pF,Capacitor_SMD:C_0603_1608Metric,Device:C_Variable"),
               ("Reference,Value,Footprint,LibPart\n", "R4,600R,Resistor_SMD:R_0603_1608Metric,Device:FerriteBead"),
               ("Reference,Value,Footprint,LibPart\n", "R5,10k,Resistor_SMD:R_0603_1608Metric,Device:LED"),
               ("Designator,Value,Footprint,Symbol\n", "C2,10uF,1206,CP"),
               ("Designator,Comment,Footprint,LibRef\n", "C2,10uF,1206,CP1"),
               ("Designator,Comment,Footprint,LibRef\n", "R2,10K,0603,Res Adj1"),
               ("Designator,Comment,Footprint,LibRef\n", "R3,10K,0603,Res Tap"),
               ("Designator,Comment,Footprint,LibRef\n", "R4,10K,0603,Res Bridge"),
               ("Designator,Comment,Footprint,LibRef\n", "R5,10K,0603,Res Thermal"),
               ("Designator,Comment,Footprint,LibRef\n", "R6,10K,0603,Res Varistor"),
               ("Designator,Comment,Footprint,LibRef\n", "C3,10pF,0603,Cap Var"),
               ("Designator,Comment,Footprint,LibRef\n", "C4,10pF,0603,Cap Feed"),
               ("Designator,Comment,Footprint,LibRef\n", "C6,10uF,1206,Cap2")]


@lite
def test_symbol_cells_end_to_end(tmp_path):
    for header, row in SYMBOL_ROWS:
        got = _each(tmp_path, header, row)
        assert got and all(s != "ready" and not fp.startswith("standard:") for _, s, _, fp in got), got
    assert "plain capacitor symbol" in _each(tmp_path, "Designator,Comment,Footprint,LibRef\n", "C3,10pF,0603,Cap Var")[0][2]
    for header, rows in [("Reference,Value,Footprint,LibPart\n",
                          "C2,10uF,Capacitor_SMD:C_1206_3216Metric,Device:C\nC3,100n,Capacitor_SMD:C_0603_1608Metric,Device:C_Small\n"
                          "R2,10k,Resistor_SMD:R_0603_1608Metric,Device:R\nR3,22k,Resistor_SMD:R_0603_1608Metric,Device:R_Small_US\n"),
                         ("Designator,Comment,Footprint,LibRef\n",
                          "R2,10K,0603,Res1\nR3,22K,0603,Res2\nR4,33K,0603,Res3\nC2,100nF,0603,Cap\nC3,1uF,0603,Cap Semi\n"
                          "R5,1K,0603,Resistor\nC4,10nF,0603,Capacitor\n")]:
        got = _each(tmp_path, header, rows)
        assert got and all(s == "ready" for _, s, _, _ in got), got


EAGLE = """Partlist

Exported from demo.sch at 22.11.2026 10:00

EAGLE Version 9.6.2 Copyright (c) 1988-2020 Autodesk, Inc.

Assembly variant:

Part  Value  Device           Package       Library       Sheet

C1    100n   C-EUC0603        C0603         rcl           1
C2    1u     C-USC0805        C0805         rcl           1
C5    22u    CPOL-EUC1206     C1206         rcl           1
R1    10k    R-EU_R0603       R0603         rcl           1
R2    4k7    R-US_R0805       R0805         resistor      1
R3    1k     RESISTOR0603     0603          SparkFun      1
"""


@lite
def test_eagle_device_names_end_to_end(tmp_path):
    """EAGLE's Device is the symbol's name with the package's glued on: 'C-EUC0603' is the plain C-EU on C0603."""
    st = {n: i["status"] for n, i in _run(tmp_path, EAGLE, "partlist.txt").items()}
    assert sorted(n for n, s in st.items() if s == "ready") == ["CAP-0603-100n", "CAP-0805-1u", "RES-0603-10k",
                                                                 "RES-0603-1k", "RES-0805-4k7"], st
    assert st["CAP-1206-22u"] != "ready", st


OTHER_CELL_HOLDS = [("Case", "A", "C"), ("Case Code", "B", "C"), ("Type", "SMD-A", "C"), ("Type", "solid", "C"),
                    ("Type", "wet", "C"), ("Type", "MELF", "R"), ("Type", "wide terminal", "R"), ("Type", "NTC", "R"),
                    ("Type", "thermistor", "R"), ("Type", "varistor", "R"), ("Mounting", "THT", "R"),
                    ("Mounting", "through hole", "R"), ("Remark", "anode marked", "C"), ("Remark", "stripe = cathode", "C"),
                    ("Polarity", "marked", "C"), ("Style", "C", "C"), ("Kind", "POL", "C"), ("Technology", "TAN", "C"),
                    ("Comment", "elko", "C"), ("Type", "Kelvin", "R"), ("Type", "trimmer", "R"), ("Config", "bussed", "R"),
                    ("Type", "Tantalkondensator", "C"), ("Package Type", "D", "C")]


def test_a_cell_of_a_column_on_what_the_part_is_is_read_whole():
    for h, c, kind in OTHER_CELL_HOLDS:
        row = "C1,1,10uF,1206," if kind == "C" else "R1,1,10k,1206,"
        assert _hold(f"Designator,Qty,Value,Footprint,{h}\n{row}{c}\n"), (h, c)
    for bom in ["Designator,Qty,Value,Footprint,Type,Mounting,Dielectric,Tolerance,Voltage\nC1,1,100nF,0603,Ceramic,SMD,X7R,10%,50V\n",
                "Designator,Qty,Value,Footprint,Type,Mounting,Tolerance,Voltage\nR1,1,10k,0603,Thick Film,SMT,1%,75V\n",
                "Designator,Qty,Value,Footprint,Type,Mounting,Dielectric\nC2,1,1uF,0805,MLCC,Surface Mount,X5R\n",
                "Designator,Qty,Value,Footprint,Pins,Height,Populate,Variant,Layer\nR1,1,10k,0603,2,0.45,Yes,A,Top\n",
                "Designator,Qty,Value,Footprint,Type,Mouser Part Number,Link\nR1,1,10k,0603,Basic,603-RC0603FR-0710KL,https://example.com/p/4-pin-array\n",
                "Designator,Qty,Value,Footprint,Case\nC1,1,100n,0603,0603\n"]:
        assert _hold(bom) is None, (bom, _hold(bom))


@lite
def test_other_cells_end_to_end(tmp_path):
    for h, c, kind in OTHER_CELL_HOLDS[:14]:
        row = "C1,1,10uF,1206," if kind == "C" else "R1,1,10k,1206,"
        got = _each(tmp_path, f"Designator,Qty,Value,Footprint,{h}\n", row + c)
        assert got and all(s != "ready" and not fp.startswith("standard:") for _, s, _, fp in got), (h, c, got)
    got = _each(tmp_path, "Designator,Qty,Value,Footprint,Type,Mounting,Dielectric,Tolerance,Voltage,Case\n",
                "C1,1,100nF,0603,Ceramic,SMD,X7R,10%,50V,0603\nR1,1,10k,0603,Thick Film,SMT,,1%,75V,0603\n")
    assert len(got) == 2 and all(s == "ready" for _, s, _, _ in got), got


# ---------------------------------------------------------------- A3 the header is read with every cell that is no clear no
HEADER_HOLDS = [("Polar", "1"), ("Total Pins", "8"), ("Total Elements", "4"), ("Total Resistors", "4"), ("# Pins", "8"),
                ("PinCount", "8"), ("NumPins", "8"), ("Pin count", "8.0"), ("Terminations", "4"), ("Sides", "4"),
                ("Gang", "4"), ("Units", "4"), ("Sections", "2"), ("Parts per package", "4"), ("Qty per package", "4"),
                ("Res per pkg", "4"), ("Signal Pins", "8"), ("Function", "+"), ("Polarized", "Yes."), ("Polarized", "YES!"),
                ("Polarized", "P"), ("Polarized", "yes (anode pin 1)"), ("Polarized", "ceramic"), ("Pin_Count", "8"),
                ("Elements per package", "4"), ("Channels", "4"), ("Leads", "4"), ("Has Polarity", "True"), ("Is Array", "Y")]
HEADER_READY = [("Polarized", "no"), ("Polarized", "No"), ("Polarized", "0"), ("Polarized", "FALSE"), ("Polarized", "-"),
                ("Polarity", "none"), ("Polarized", "N"), ("Polarized", "non-polarized"), ("Polarity", "NP"), ("Pins", "2"),
                ("Pin Count", "2"), ("Total Pins", "2"), ("Total", "12"), ("Total Qty", "40"), ("Total Price", "0.48"),
                ("Lead Time", "4"), ("Height", "4"), ("Stock", "4"), ("Unit Price", "4"), ("Terminations", "2"),
                ("Units", "pcs"), ("Price per unit", "4"), ("Qty per board", "4"), ("Lead Free", "Yes"), ("RoHS", "Yes"),
                ("Voltage", "16"), ("Tolerance", "5"), ("Item", "4"), ("Order Qty", "100"), ("MOQ", "4000")]


def _header_row(h, c):
    kind = "C" if any(w in (h + c).lower() for w in ("pol", "+")) else "R"
    return f"Designator,Qty,Value,Footprint,{h}\n" + ("C1,1,1uF,0603," if kind == "C" else "R1,1,10k,0603,") + c + "\n"


def test_the_header_is_read_with_every_cell_that_is_no_clear_no():
    for h, c in HEADER_HOLDS:
        assert _hold(_header_row(h, c)), (h, c)
    for h, c in HEADER_READY:
        assert _hold(_header_row(h, c)) is None, (h, c, _hold(_header_row(h, c)))
    assert SP.header_words("PinCount") == "pin count" and SP.header_words("# Pins") == "number pins"
    assert SP.header_words("NumPins") == "num pins" and SP.header_words("Res_per_pkg") == "res per pkg"
    assert not T.admin_header("Total Pins") and not T.admin_header("Signal Pins") and T.admin_header("Total Price")


@lite
def test_headers_end_to_end(tmp_path):
    for h, c in HEADER_HOLDS[:20]:
        head, row = _header_row(h, c).split("\n")[:2]
        got = _each(tmp_path, head + "\n", row)
        assert got and all(s != "ready" and not fp.startswith("standard:") for _, s, _, fp in got), (h, c, got)
    for h, c in HEADER_READY[:16]:
        head, row = _header_row(h, c).split("\n")[:2]
        got = _each(tmp_path, head + "\n", row)
        assert got and all(s == "ready" for _, s, _, _ in got), (h, c, got)


def test_a_part_number_line_reads_a_polarized_header_too():
    """With a part number the line is not a generic part, but 'Polarized: Yes.' still makes it a polarized capacitor."""
    item = {"folder": "Components/Capacitors", "description": "", "symbol": {"ref": "Capacitor"},
            "footprint": {"ref": "C_1206_3216Metric", "kicad": "Capacitor_SMD:C_1206_3216Metric"},
            "parameters": [{"name": "Manufacturer Part Number", "value": "XQ7A106K016"}, {"name": "Value", "value": "10uF"}]}
    ln = {"mpn": "XQ7A106K016", "value": "10uF", "footprint": "Capacitor_SMD:C_1206_3216Metric"}
    for cells in ["Polarized: Yes.", "Polarized: P", "Polarized: yes (anode pin 1)", "Polar: 1"]:
        assert L.passive_kind(dict(item), dict(ln, other_cells=cells)) == "CP", cells
    for cells in ["Polarized: no", "Polarized: non-polarized", "Dielectric: X7R"]:
        assert L.passive_kind(dict(item), dict(ln, other_cells=cells, description="CAP CER X7R")) == "C", cells


# ---------------------------------------------------------------- A4 'bipolar' takes out only itself
def test_bipolar_and_non_polar_take_out_only_their_own_word():
    for text in ["Tantalum capacitor for bipolar supply", "bipolar electrolytic", "non-polar electrolytic",
                 "10uF NP aluminium", "tantalum, unpolarized alternative not allowed", "Elko bipolar 10uF"]:
        assert SP.says_polarized(text), text
    for text in ["Unpolarized capacitor", "non-polarized ceramic capacitor", "Capacitor, non polarised", "bipolar",
                 "CAP CER 10UF X5R without polarity"]:
        assert not SP.says_polarized(text), text
    assert SP.unread_words("bipolar", "C") == ["bipolar"] and SP.unread_words("Unpolarized capacitor", "C") == []
    assert SP.resolve(["1206"], "C", "XYZ123", "Tantalum capacitor for bipolar supply", value="10uF")["footprint"] is None


@lite
def test_bipolar_end_to_end(tmp_path):
    for bom in ["Designator,Qty,Value,Footprint,MPN,Manufacturer,Description\nC1,1,10uF,1206,XYZ123,,Tantalum capacitor for bipolar supply\n",
                "Designator,Qty,Value,Footprint,Type\nC1,1,10uF,1206,bipolar electrolytic\n",
                H + "C1,1,10uF,1206,non-polar electrolytic\n", H + "C1,1,10uF,1206,bipolar\n"]:
        d = tmp_path / str(len(list(tmp_path.iterdir())))
        d.mkdir()
        plan = _run(d, bom)
        assert plan and all(i["status"] != "ready" and not _fp(i).startswith("standard:") for i in plan.values()), (bom, _status(plan))
    got = _each(tmp_path, H, 'C1,1,1uF,0603,Unpolarized capacitor\nC2,1,100n,0603,"Capacitor, non polarised"\n')
    assert len(got) == 2 and all(s == "ready" for _, s, _, _ in got), got


# ---------------------------------------------------------------- A8 KiCad's chip name alone releases no unread number
def test_kicads_chip_name_alone_does_not_release_an_unread_capacitor_number():
    c1206 = "Capacitor_SMD:C_1206_3216Metric"
    for mpn in ["TMCMA1C106MTRF", "T58A0106M016C0200", "CA45-A-16V-10uF-K", "XQ7A106K016"]:   # (EMK107BJ225KA-T reads
                                                    # as a Taiyo Yuden 0603 MLCC since 2026-11-24: the size check holds it)
        r = SP.resolve([c1206], "C", mpn, "", value="10uF")
        assert r["footprint"] is None and r["symbol"] is None, (mpn, r)
    for mpn in ["TMCMA1C106MTRF", "T58A0106M016C0200", "CA45-A-16V-10uF-K"]:
        assert SP.POLAR_CAP.match(SP._norm(mpn)), mpn
    assert "ceramic" in SP.resolve([c1206], "C", "XQ7A106K016", "", value="10uF")["fp_how"]
    for mpn, desc, search in [("GRM31CR61C106KA88L", "", ""), ("XQ7A106K016", "CAP CER 10UF 16V X5R 1206", ""),
                              ("XQ7A106K016", "", "CAP CER 10UF 16V X5R 1206"), ("XQ7A106K016", "X7R", ""), ("", "", "")]:
        r = SP.resolve([c1206], "C", mpn, desc, value="10uF", search=search)
        assert r["footprint"] == "C_1206_3216Metric" and r["symbol"] == "C", (mpn, desc, search, r)
    item = {"folder": "Components/Capacitors", "description": "", "symbol": {"ref": "Capacitor"},
            "footprint": {"ref": "C_1206_3216Metric", "kicad": c1206},
            "parameters": [{"name": "Manufacturer Part Number", "value": "XQ7A106K016"}, {"name": "Value", "value": "10uF"}]}
    ln = {"mpn": "XQ7A106K016", "value": "10uF", "footprint": c1206}
    assert L.passive_kind(dict(item), ln) is None
    assert L.passive_kind(dict(item), ln, "CAP CER 10UF 16V X5R 1206") == "C"
    assert L.passive_kind(dict(item), dict(ln, description="MLCC X5R")) == "C"


@lite
def test_unread_capacitor_numbers_on_kicads_chip_name_end_to_end(tmp_path):
    head = "Reference,Value,Footprint,MPN,Manufacturer,Description\n"
    got = _each(tmp_path, head, """
C5,10uF,Capacitor_SMD:C_1206_3216Metric,TMCMA1C106MTRF,Vishay,
C6,10uF,Capacitor_SMD:C_1206_3216Metric,T58A0106M016C0200,Vishay,
C7,10uF,Capacitor_SMD:C_1206_3216Metric,CA45-A-16V-10uF-K,,
C8,10uF,Capacitor_SMD:C_1206_3216Metric,XQ7A106K016,Acme,
""")
    assert len(got) == 4 and all(s != "ready" and "C_1206_3216Metric" not in fp for _, s, _, fp in got), got
    got = _each(tmp_path, head, """
C1,100n,Capacitor_SMD:C_0603_1608Metric,GRM188R71H104KA93D,Murata,
C2,2.2uF,Capacitor_SMD:C_0603_1608Metric,XQ7A225K016,Acme,CAP CER 2.2UF 16V X5R 0603
C4,10n,Capacitor_SMD:C_0603_1608Metric,,,
""")
    assert len(got) == 3 and all(s == "ready" for _, s, _, _ in got), got


def test_a_ceramic_part_number_on_a_polarized_land_is_a_contradiction():
    r = SP.resolve(["CP_EIA-3216-18_Kemet-A"], "C", "GRM31CR61C106KA88L", "", value="10uF", maker="Murata")
    assert r["footprint"] is None and r["symbol"] is None and "contradicts" in r["fp_how"], r
    r = SP.resolve(["CP_EIA-3216-18_Kemet-A"], "CP", "GRM31CR61C106KA88L", "", value="10uF", maker="Murata")
    assert r["footprint"] is None and r["symbol"] is None, r
    r = SP.resolve(["CP_EIA-3216-18_Kemet-A"], "C", "T491A106K016AT", "", value="10uF", maker="KEMET")
    assert r["footprint"] == "CP_EIA-3216-18_Kemet-A" and r["symbol"] == "CP", r


@lite
def test_ceramic_number_on_a_tantalum_land_end_to_end(tmp_path):
    plan = _run(tmp_path, "Reference,Value,Footprint,Description,MPN,Manufacturer\n"
                          "C9,10uF,CP_EIA-3216-18_Kemet-A,,GRM31CR61C106KA88L,Murata\n")
    assert plan and all(i["status"] != "ready" and not _fp(i).startswith("standard:") for i in plan.values()), _status(plan)


# ---------------------------------------------------------------- B3 / B4 columns on how the part is bought or used
FUNCTION_NOTES = """
R1,1,10k,0603,feedback voltage divider
R2,1,100R,0603,RC filter
R3,1,10k,0603,bias network
R5,1,10k,0603,pull-up for 4 pin header
R6,1,1k,0603,LED array current limit
C1,1,100nF,0603,LC filter
C3,1,100nF,0603,decoupling for dual op-amp
C5,1,100nF,0603,ADC 3 channel filter
R7,1,10k,0603,I2C pull-up (2 lines)
R8,1,0R,0603,isolated ground link
R9,1,10k,0603,RGB LED resistor
C8,1,10nF,0603,common mode choke snubber
"""


def test_a_use_column_is_read_for_technology_words_only():
    for col in ("Function", "Purpose", "Used for"):
        for row in FUNCTION_NOTES.strip().splitlines():
            assert _hold(f"Designator,Qty,Value,Footprint,{col}\n{row}\n") is None, (col, row)
        for row in ["C1,1,10uF,1206,tantalum bulk cap", "C4,1,1uF,0603,polymer LDO output", "C2,1,10uF,0805,electrolytic reservoir",
                    "C3,1,10uF,1206,+", "C5,1,10uF,1206,Elko"]:
            assert _hold(f"Designator,Qty,Value,Footprint,{col}\n{row}\n"), (col, row)
    for h in ("RoHS", "REACH", "Lifecycle", "Life Cycle", "Status", "Compliance"):
        assert T.admin_header(h), h
    for h in ("Supplier Package", "Case", "Total Pins", "Order Code", "Supplier Part Number"):
        assert not T.admin_header(h), h
    item = {"folder": "Components/Resistors", "description": "", "symbol": {"ref": "Resistor"},
            "footprint": {"ref": "R_0603_1608Metric", "kicad": "Resistor_SMD:R_0603_1608Metric"},
            "parameters": [{"name": "Manufacturer Part Number", "value": "RC0603FR-0710KL"}, {"name": "Value", "value": "10k"}]}
    ln = {"mpn": "RC0603FR-0710KL", "value": "10k", "footprint": "0603", "other_cells": "Function: pull-up for 4 pin header"}
    assert L.passive_kind(dict(item), ln) == "R"                  # (with a part number too: what it is for is not the part)
    assert L.passive_kind(dict(item), dict(ln, other_cells="Comment: 4 resistor array")) is None


@lite
def test_use_columns_end_to_end(tmp_path):
    got = _each(tmp_path, "Designator,Qty,Value,Footprint,Function\n", FUNCTION_NOTES)
    assert len(got) == 12 and all(s == "ready" for _, s, _, _ in got), [g for g in got if g[1] != "ready"]
    got = _each(tmp_path, "Designator,Qty,Value,Footprint,Function\n", "C1,1,10uF,1206,tantalum bulk cap\nC3,1,10uF,1206,+\n")
    assert len(got) == 2 and all(s != "ready" and not fp.startswith("standard:") for _, s, _, fp in got), got


MERGED = [("Designator,Value,Footprint,RoHS,Lifecycle\nR1,10k,0603,Yes,Active\nR2,10k,0603,Compliant,Production\n", True),
          ("Designator,Value,Footprint,Function\nR1,10k,0603,pull-up\nR2,10k,0603,pull-down\n", True),
          ("Designator,Value,Footprint,Symbol\nR1,10k,0603,Res1\nR2,10k,0603,Res2\n", True),
          ("Designator,Value,Footprint,Manufacturer\nC1,1uF,0603,Samsung Electro-Mechanics\nC2,1uF,0603,Samsung\n", True),
          ("Designator,Value,Footprint,Supplier Package\nC1,10uF,1206,1206\nC2,10uF,1206,CASE-A 3216-18\n", False),
          ("Designator,Value,Footprint,Usage\nR1,10k,0603,0.1% reference\nR2,10k,0603,5% pull-up\n", False),
          ("Designator,Value,Footprint,Function\nC1,10uF,1206,16V rail\nC2,10uF,1206,50V rail\n", False),
          ("Designator,Value,Footprint,Manufacturer\nC1,10uF,1206,\nC2,10uF,1206,AVX TAJ series\n", False),
          ("Designator,Value,Footprint,Manufacturer\nC1,10uF,1206,AVX TAJ series\n", False),
          ("Designator,Value,Footprint,Symbol\nC1,10uF,1206,Cap\nC2,10uF,1206,Cap2\n", False)]


@lite
def test_merged_lines_and_administrative_columns_end_to_end(tmp_path):
    for n, (bom, ready) in enumerate(MERGED):
        d = tmp_path / str(n)
        d.mkdir()
        st = _status(_run(d, bom))
        assert len(st) == 1 and all((s == "ready") == ready for s, _ in st.values()), (bom, st)


# ---------------------------------------------------------------- Part 2: words that say nothing about the part's build
ORDINARY = """
C1,1,100n,0603,"CAPACITOR, European symbol"
R1,1,10k,0603,"RESISTOR, American symbol"
R2,1,4k7,0603,desc
R3,1,1k,0805,Resistor Fixed - ANSI
C2,1,1.5nF,0603,CAP 1.5nF 6.3V ±5% 0603 (1608 Metric) Thickness 1mm SMD
R4,1,10k,0603,DNP
C3,1,100nF,0402,"Capacitor SMD 0402 (1005 Metric), square (rectangular) end terminal, IPC-7351 nominal"
R5,1,10k,0603,贴片电阻
C4,1,100nF,0603,贴片电容
C5,1,100nF,0603,去耦/隔直
R6,1,22R,0603,源端阻尼
R7,1,10k,0603,Library:  Passives
R8,1,100k,0805,UNI-ROYAL 100k 1% 0805. Verified.
C6,1,1uF,0805,Samsung CL21B105KBFNNNE 1uF 50V X7R. Verified.
R9,1,1.5k,0603,RC0603FR-071K5L
C7,1,100nF,0402,GRM155R71E104KE14
R10,1,10k - RC0805FR-0710KL,0805,
R11,1,2k2,0603,Farnell 8819742
C8,1,100n,0603,decoupling
R12,1,4k7,0603,pull-up
R13,1,10k,0603,General purpose resistor
C9,1,100n,0603,0603 Cpacitor
R14,1,10k,0603,General pourpouse resistor
R15,1,0R,0603,Yageo - RC0603JR-070RL - RES SMD 0 OHM JUMPER 1/10W 0603
C10,1,0.1uF,0603,Kemet - C0603C104K5RACTU - CAP CER 0.1UF 50V X7R 0603
C11,1,1uF,0603,0603 1uF 50V X5R (Murata GRM188R61H105KAALD)
R16,1,100 kOm,0603,
R17,1,1 Meg,0603,
R18,1,10k,0603,from: somebody
C12,1,2.2uF,0805,"Chip Capacitor, 2.2 uF, +/- 10%, 10 V, -55 to 85 degC, 0805"
R19,1,10k,0603,Host-side pull-up; remove if the host already has it
C13,1,100nF,0603,"C14663, CAP CER 0.1UF 25V X7R 0603"
R20,1,0 Ohm,0603,0 Ohm link
R21,1,10k,0603,optional
R22,1,10k,0603,RES 10K 1% 0603 Yageo
R23,1,10k frobnic,0603,
"""
ORDINARY_LANDS = """
R1,1,10k,MC_S6/SM/R_0603_IPC,
C1,1,100n,MC_S6/SM/C_0402_IPC,
R2,1,10k,0603-NO,
C2,1,100n,C_0603_REFLOW,
C3,1,100n,My Board Lib-import-fps:0603-NO,
R4,1,1k,PERFECT_0603_1608,
R5,1,1k,RES-0402-SML,
R6,1,1k,R0603_med_density,
"""
NOT_ORDINARY = """
C1,1,10uF,1206,Tantalkondensator
C2,1,10uF,1206,electrolitic capacitor
C3,1,10uF,1206,钽电容
C4,1,10uF,1206,TAJA106K016RNJ
C5,1,10uF,1206,10uF 16V T491A106K016AT
R1,1,10k,0603,RC0805FR-0710KL
R2,1,10k,0603,RC0603FR-0722KL
R3,1,10k,0603,排阻
R4,1,10k,1206,Widerstandsnetzwerk
R5,1,10k,0603,10k fusible
R6,1,10k,0603,wirewound
R7,1,10k,1206,MELF
C6,1,100nF,0603,3 x 100nF
C7,1,10uF,1206,10uF A
C8,1,10uF,1206,10uF POL
R8,1,10k,R_0603_Optional_Bridged,
C9,1,10uF,1206,solid capacitor
C10,1,100n,0603,film capacitor
R9,1,10k,0603 x4,
R10,1,10k,0603_A,
C11,1,10uF,1206,XQ7A106K016
R11,1,10k,0603,10k TAN
C12,1,10uF,1206,10uF CMC
R12,1,10k,0603,8P4R
R13,1,10k,0603,резисторная сборка
"""


def test_which_words_hold_a_resistor_or_capacitor():
    for kind, text in [("C", "CAPACITOR, European symbol"), ("R", "RESISTOR, American symbol"), ("R", "desc"),
                       ("R", "Resistor Fixed - ANSI"), ("C", "0603 (1608 Metric) Thickness 1mm SMD"), ("R", "DNP"),
                       ("C", "square (rectangular) end terminal"), ("R", "贴片电阻"), ("C", "贴片电容"), ("C", "去耦/隔直"),
                       ("R", "Library:  Passives"), ("R", "UNI-ROYAL 100k 1% 0805. Verified."), ("R", "Farnell 8819742"),
                       ("C", "decoupling"), ("R", "pull-up"), ("R", "General purpose resistor"), ("C", "0603 Cpacitor"),
                       ("R", "General pourpouse resistor"), ("R", "100 kOm"), ("R", "1 Meg"), ("R", "5.1 kR"),
                       ("R", "Buck feedback (with R4 sets the 5 V rail)"), ("C", "C14663"), ("R", "10K*"), ("R", "NO POP"),
                       ("C", "IPC-7351 nominal, see https://example.com/tantalum-array.pdf"), ("R", "TDK"), ("C", "AVX X7R"),
                       ("C", "for a button"), ("R", "10k frobnic")]:
        assert SP.unread_words(text, kind) == [], (kind, text, SP.unread_words(text, kind))
    for kind, text, word in [("C", "10uF Tantalkondensator", "Tantalkondensator"), ("C", "electrolitic capacitor", "electrolitic"),
                             ("C", "钽电容", "钽电容"), ("R", "排阻", "排阻"), ("R", "Widerstandsnetzwerk", "Widerstandsnetzwerk"),
                             ("R", "10k NTC", "NTC"), ("R", "wirewound", "wirewound"), ("R", "MELF", "MELF"), ("C", "10uF A", "A"),
                             ("C", "10uF POL", "POL"), ("C", "solid capacitor", "solid"), ("C", "film capacitor", "film"),
                             ("R", "10k TAN", "TAN"), ("C", "10uF CMC", "CMC"), ("R", "8P4R", "8P4R"), ("R", "1k x4", "x4"),
                             ("C", "100nF array", "array"), ("C", "10uF 16V a", "a"), ("C", "C7343 100uF", "C7343"),
                             ("C", "T491 10uF", "T491"), ("R", "thermistor", "thermistor"), ("R", "trimmer 10k", "trimmer"),
                             ("R", "leaded", "leaded"), ("C", "XQ7A106K016", "XQ7A106K016"), ("R", "сборка", "сборка"),
                             ("C", "タンタル", "タンタル"), ("C", "Feedthrough", "Feedthrough"), ("R", "potentiometer", "potentiometer")]:
        assert SP.unread_words(text, kind) == [word], (kind, text, SP.unread_words(text, kind))
    for kind, text in [("LED", "0603 frobnic"), ("L", "0805 Yageo"), ("D", "0603 european")]:    # other kinds: every word
        assert SP.unread_words(text, kind, footprint=True), (kind, text)
    for kind, text in [("R", "MC_S6/SM/R_0603_IPC"), ("C", "C_0603_REFLOW"), ("R", "0603-NO"), ("C", "Lib one-import:0603-NO"),
                       ("R", "PERFECT_0603_1608"), ("R", "0603 frobnic"), ("R", "RES-0603-SML"), ("R", "R0603_med_density"),
                       ("C", "CAP, 0805, HD"), ("LED", "0603 (1608 metric), polarized"), ("LED", "MC_S6/SM/LED_0603"),
                       ("C", "MURATA_HI_DENSITY_0402_CAP")]:
        assert SP.unread_words(text, kind, footprint=True) == [], (kind, text, SP.unread_words(text, kind, footprint=True))
        assert SP.resolve([text], kind, "", "")["footprint"], (kind, text)
    for kind, text in [("R", "0603_A"), ("C", "1206 L"), ("R", "0603 x4"), ("R", "R_0603_Optional_Bridged"), ("C", "C0603_N"),
                       ("R", "RES-0603-XYZ"), ("C", "1206 TANT"), ("R", "0603 ARRAY"), ("R", "MiniMELF 0603"),
                       ("R", "Arrays/SM/R_0603"), ("C", "TANT/C_1206"), ("C", "POL/C_1206"), ("R", "ARV/R_0603"),
                       ("R", "RN/0603"), ("C", "lib/CP/1206"), ("R", "x4/R_0603")]:   # (a folder's name is read too)
        assert SP.unread_words(text, kind, footprint=True) or SP.resolve([text], kind, "", "")["footprint"] is None, (kind, text)
        assert SP.resolve([text], kind, "", "")["footprint"] is None, (kind, text)
    for text in ["Samsung Electro-Mechanics CL21B105KBFNNNE", "Mouser Electronics"]:
        assert SP.unread_words(text, "C") == [], text


def test_a_part_number_in_the_description_must_agree_with_the_line():
    hold = lambda cat, value, desc, fp: SP.generic_passive_hold(cat, value, desc, (), [("footprint", "Footprint", fp)])
    for cat, value, desc, fp in [("Resistors", "1.5k", "RC0603FR-071K5L", "0603"), ("Capacitors", "100nF", "GRM155R71E104KE14", "0402"),
                                 ("Resistors", "10k - RC0805FR-0710KL", "", "0805"), ("Capacitors", "1uF", "0603 1uF 50V X5R (Murata GRM188R61H105KAALD)", "0603"),
                                 ("Capacitors", "0.1uF", "Kemet - C0603C104K5RACTU - CAP CER 0.1UF 50V X7R 0603", "C_0603_1608Metric"),
                                 ("Resistors", "0R", "Yageo - RC0603JR-070RL - RES SMD 0 OHM JUMPER 1/10W 0603", "0603")]:
        assert hold(cat, value, desc, fp) is None, (value, desc, hold(cat, value, desc, fp))
    for cat, value, desc, fp, word in [("Resistors", "10k", "RC0805FR-0710KL", "0603", "two sizes"),
                                       ("Resistors", "10k", "RC0603FR-0722KL", "0603", "another value"),
                                       ("Resistors", "22k - RC0805FR-0710KL", "", "0805", "another value"),
                                       ("Capacitors", "10uF", "TAJA106K016RNJ", "1206", "TAJA106K016RNJ"),
                                       ("Capacitors", "10uF", "10uF 16V T491A106K016AT", "1206", "T491A106K016AT"),
                                       ("Capacitors", "100nF", "RC0603FR-0710KL", "0603", "RC0603FR"),
                                       ("Resistors", "10k", "GRM188R71H104KA93D", "0603", "GRM188R71H104KA93D")]:
        assert word in str(hold(cat, value, desc, fp)), (value, desc, hold(cat, value, desc, fp))
    assert SP.embedded_parts("10k - RC0805FR-0710KL", "R")[0][0][:3] == ("RC0805FR-0710KL", "R", "0805")
    assert SP.embedded_parts("TAJA106K016RNJ", "C") == ([], "TAJA106K016RNJ")


def test_more_chip_series_and_a_maker_in_brackets_are_read_for_the_size():
    """Seen in the real BOMs: Stackpole's RNCF / RNCS, TE's CPF, Fenghua's / Walsin's MLCC code; a part number cell
    'CL10C100JB8NNNC (Samsung)' or '(CL05B104KO5NNNC)'; Yageo's AC resistor under the maker name KEMET (one company)."""
    for mpn, maker, want in [("RNCF0805DTE23K2", "", ("R", "0805")), ("RNCS0603BKE100R", "Stackpole", ("R", "0603")),
                             ("CPF0201D7K68C1", "", ("R", "0201")), ("CPF0805B102RE1", "TE Connectivity", ("R", "0805")),
                             ("0805B104K500NT", "", ("C", "0805")), ("0603CG330J500NT", "Fenghua", ("C", "0603")),
                             ("0402N100J500CT", "Walsin", ("C", "0402")), ("CL10C100JB8NNNC (Samsung)", "", ("C", "0603")),
                             ("(CL05B104KO5NNNC)", "", ("C", "0402")), ("AC0402FR-070RL", "KEMET", ("R", "0402"))]:
        assert SP.mpn_size(mpn, maker) == want, (mpn, maker, SP.mpn_size(mpn, maker))
    for mpn, maker in [("CPF1206R00FKE14", ""), ("CPF0805B102RE1", "Vishay"), ("0805B104K500NT", "Murata"),
                       ("CL10C100JB8NNNC (Murata)", ""), ("T491A106K016AT (KEMET)", ""), ("0805B104K500", "")]:
        assert SP.mpn_size(mpn, maker) == (None, None), (mpn, maker, SP.mpn_size(mpn, maker))
    hold = lambda cat, value, desc, fp: SP.generic_passive_hold(cat, value, desc, (), [("footprint", "Footprint", fp)])
    assert hold("Resistors", "7.68k", "7.68k 0201 Thin Film Surface Mount Fixed Resistor +/-0.5% CPF0201D7K68C1", "0201") is None
    assert "two sizes" in hold("Resistors", "7.68k", "CPF0402D7K68C1", "0201")


def test_a_symbol_cell_that_names_a_chip_of_the_kind_or_a_rescued_symbol():
    for kind, cell, lands in [("C", "10NF-C0603C103K5RACTU", ("C0603",)), ("R", "RC0603FR-071K5L", ()), ("C", "GCM21BR71H104KA37K", ()),
                              ("R", "proj-cache:R-RESCUE-proj", ()), ("C", "C-SmartRelay-rescue", ()), ("C", "C25804", ()),
                              ("R", "MYLIB_EAGLE-LTSPICE_RR0402", ("MYLIB_EAGLE-LTSPICE_R0402",)),
                              ("C", "CAP_CERAMIC0805-NOOUTLINE", ("0805-NO",)), ("R", "RESISTOR0805_NOOUTLINE", ()),
                              ("C", "C0402-22uF-high-density", ())]:
        assert SP.cell_hold(kind, "symbol", "Device", cell, lands) is None, (kind, cell, SP.cell_hold(kind, "symbol", "Device", cell, lands))
    for kind, cell, lands in [("C", "10UF-T491A106K016AT", ()), ("C", "TAJA106K016RNJ", ()), ("R", "GRM188R71H104KA93D", ()),
                              ("C", "proj-cache:CP-RESCUE-proj", ()), ("C", "C_Polarized-proj-rescue", ()),
                              ("R", "MYLIB_EAGLE-LTSPICE_RTRIM0402", ("MYLIB_EAGLE-LTSPICE_R0402",)), ("R", "RES-S0603", ("R0603",)), ("R", "RESISTOR_ARRAY0603", ()),
                              ("C", "CAP_TANT0805-NOOUTLINE", ()), ("R", "NOOUTLINE", ()),
                              ("C", "XQ7A106K016", ())]:
        assert SP.cell_hold(kind, "symbol", "Device", cell, lands), (kind, cell)


@lite
def test_ordinary_lines_of_real_boms_stay_ready(tmp_path):
    got = _each(tmp_path, H, ORDINARY)
    assert len(got) == 36 and all(s == "ready" for _, s, _, _ in got), [g for g in got if g[1] != "ready"]
    got = _each(tmp_path, H, ORDINARY_LANDS)
    assert len(got) == 8 and all(s == "ready" for _, s, _, _ in got), [g for g in got if g[1] != "ready"]


@lite
def test_lines_that_name_another_build_are_held(tmp_path):
    got = _each(tmp_path, H, NOT_ORDINARY)
    assert len(got) == 25 and all(s != "ready" and not fp.startswith("standard:") for _, s, _, fp in got), \
        [g for g in got if g[1] == "ready" or g[3].startswith("standard:")]
