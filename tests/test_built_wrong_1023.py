"""Live Opus zoom run on main 47328c9 (2026-10-23): 11 parts were built wrong. Each one's cause is held now, or built
from something that is not a guess. The PDFs here are SYNTHETIC text laid out like those datasheets, not copies.
- a web search's family sheet headed by a sibling part on every page (2ED21824S06J got the 2ED2184 sheet);
- a tab package whose lead land was read while the builder sizes the tab by IPC (LP38693MP-1.8, BAT160C);
- a TI PowerPAD thermal land read smaller than the exposed pad (OPA569DWP);
- through-hole holes the datasheet does not print: a plain DIP gets KiCad's DIP holes, anything else is held (16550,
  KBU4A, UCC1895J);
- a connector body outline that ends on its hole row (S13B-EH).
   python3 -m tests.run_all test_built_wrong_1023"""
import tempfile
from pathlib import Path

from ecadlib import datasheet as D, ds_route as DR, footprint_adv as FA
from tests import test_ds_route as TR
from tests.test_ds_search import Client, _answer, _reader
from tests.test_datasheet_batch import PdfHTTP


# ---------------------------------------------------------------- a sibling's family sheet from a web search
def family_sheet(header, body_part):
    """Pages headed like Infineon's 2ED2184 (4) S06F (J) sheet: the family table on page 1 lists the sibling parts."""
    first = [header, "650 V half-bridge gate driver", "Product family",
             f"{body_part}S06F DSO-8 Internal 400 ns", "2ED21824S06J DSO-14 200 ns", "2ED21844S06J DSO-14 400 ns"]
    rest = [[header, "650 V half-bridge gate driver", f"Page text {i}", "Pin configuration and functionality"]
            for i in range(2, 7)]
    return [first] + rest


def texts(pages):
    return DR.page_texts(TR.pdf_lines(pages))


def test_a_sheet_headed_by_a_sibling_on_every_page_is_not_the_parts():
    t = texts(family_sheet("2ED2184 (4) S06F (J)", "2ED2184"))
    assert D.mentions_part(t[:1], "2ED21824S06J") is True             # the family table names it on page 1
    why = D.sibling_sheet(t, "2ED21824S06J")
    assert why and "2ED2184" in why and "sibling" in why, why
    assert D.sibling_sheet(t, "2ED21844S06J") is None                  # the header's own part: its numeric core
    assert D.sibling_sheet(t, "2ED2184S06F") is None


def test_a_sheet_headed_by_the_part_or_with_no_running_header_is_kept():
    head = lambda h: [[h, "Schottky barrier double diode", f"page {i}"] for i in range(1, 9)]
    assert D.sibling_sheet(texts(head("Nexperia BAT160C")), "BAT160C") is None
    assert D.sibling_sheet(texts(head("KBU4A, KBU4B, KBU4D, KBU4G, KBU4J, KBU4K, KBU4M")), "KBU4A") is None
    assert D.sibling_sheet(texts(head("LP38691, LP38693, LP38691-Q1, LP38693-Q1")), "LP38693MP-1.8") is None
    assert D.sibling_sheet(texts(head("PC16550D")), "16550") is None
    # a family name the part starts with, x for any one character, or a range that holds it
    assert D.sibling_sheet(texts(head("TPS7A47 36-V, 1-A low-noise LDO")), "TPS7A4701RGWR") is None
    assert D.sibling_sheet(texts(head("C8051F32x full speed USB MCU")), "C8051F320-GQ") is None
    assert D.sibling_sheet(texts(head("MAX22201-MAX22207 motor drivers")), "MAX22205") is None
    assert D.sibling_sheet(texts(head("MAX22201 motor driver")), "MAX22205")          # one sibling alone: refused
    # a header with no part number (maker, document number) says nothing
    assert D.sibling_sheet(texts(head("www.example.com Rev. 7, November 2024 SLVS321F")), "LP38693MP-1.8") is None
    assert D.sibling_sheet(texts([["2ED2184 (4) S06F (J)"]] * 2), "2ED21824S06J") is None   # too few pages to tell


def test_a_web_search_skips_a_siblings_sheet_and_takes_the_next_link():
    sib, own = "https://maker.example/2ed2184.pdf", "https://maker.example/2ed21824.pdf"
    sheet = TR.pdf_lines(family_sheet("2ED2184 (4) S06F (J)", "2ED2184"))
    good = TR.pdf_lines([["2ED21824S06J 650 V half-bridge gate driver", f"page {i}"] for i in range(1, 6)])
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, Client(_answer([sib, own])), http=PdfHTTP({sib: sheet, own: good}), web_search=True)
        job = rd.job("2ED21824S06J", [], "SOIC-14")
        assert job["url"] == own, job["url"]
        miss = [a for a in job["base_prov"]["attempts"] if a["url"] == sib]
        assert miss and "sibling" in miss[0]["error"], job["base_prov"]["attempts"]


def test_a_notice_or_a_board_sheet_is_not_a_datasheet():
    for first in ("Product Discontinuation PD_053_17 Last Order Date: 2018-01-15",
                  "8755 W. Example Road Suite 500 Dec 22th, 2021 Notification of Thyristor Package Part Numbers MOQ Change",
                  "Example Corporation Data Sheet DS-1 MAX II Development Board Features",
                  "Product Change Notice PCN-2021-04", "TESTPART Evaluation Kit User Guide"):
        assert D.not_a_datasheet([first, "page 2"]), first
    for first in ("TESTPART 600 W TVS diode, unidirectional and bidirectional types",
                  "TESTPART 3 A buck converter " + "x " * 250 + "evaluation board available"):   # past the title
        assert D.not_a_datasheet([first]) is None, first
    assert D.not_a_datasheet([]) is None


# ---------------------------------------------------------------- a tab package's land read for the leads only
def sot223(**kw):
    s = {"package_type": "gullwing", "name": "SOT-223-5", "pin_count": 5, "pitch": 1.5,
         "tab": {"number": 5, "side": "R", "offset": 0, "width": {"nom": 3.0, "tol": 0.1}},
         "land": {"pad_length": 1.5, "pad_width": 1.0, "pad_center": 3.15}}
    s.update(kw)
    return s


def test_a_tab_package_with_the_lead_land_only_is_held():
    why = D.tab_land_unread(sot223())
    assert why and why.startswith("no footprint:") and "tab" in why, why
    assert D.tab_land_unread(sot223(land={"pad_length": 1.5, "pad_width": 1.0, "pad_center": 3.15,
                                          "ep_across": 3.3, "ep_along": 1.5})), "the builder never sizes a tab from it"


def test_a_tab_package_with_no_land_or_a_package_with_no_tab_builds():
    assert D.tab_land_unread(sot223(land={})) is None                 # IPC pads all round, as KiCad's
    assert D.tab_land_unread({k: v for k, v in sot223().items() if k != "tab"}) is None
    assert D.tab_land_unread(sot223(land={"ep_across": 3.4, "ep_along": 5.0})) is None


# ---------------------------------------------------------------- a TI thermal land smaller than the pad
def powerpad(a, b):
    return {"package_type": "gullwing", "name": "HSOP-20", "pin_count": 21,
            "source": {"datasheet": "http://www.ti.com/lit/ds/symlink/testpart.pdf"},
            "exposed_pad": {"length": {"min": 3.49, "max": 4.32}, "width": {"min": 2.72, "max": 3.81}},
            "land": {"ep_across": a, "ep_along": b}}


def test_a_ti_thermal_land_that_does_not_cover_the_pad_is_held():
    why = D.thermal_land_missing(powerpad(3.56, 4.47))               # OPA569DWP: the figure's required via area
    assert why and "does not cover the exposed pad" in why, why
    assert D.thermal_land_missing(powerpad(4.47, 3.56))              # either way round


def test_a_ti_thermal_land_over_the_pad_builds():
    assert D.thermal_land_missing(powerpad(3.9, 4.4)) is None
    assert D.thermal_land_missing(powerpad(7.59, 12.95)) is None
    assert D.thermal_land_missing(dict(powerpad(3.56, 4.47), source={"datasheet": "https://maker.example/x.pdf"})) is None
    assert "was not read" in D.thermal_land_missing(powerpad(None, None))


# ---------------------------------------------------------------- holes the datasheet does not print
def dip(n=40, row=15.24, unit="mm", hole=0.889, pad=1.651, **kw):
    f = FA.UNIT[unit]
    s = {"package_type": "custom", "name": f"DIP-{n}", "pin_count": n, "pitch": 2.54, "unresolved": ["drill"],
         "pad_table": {"unit": unit, "y_axis": "down", "origin": "pin1",
                       "default_pad": {"type": "tht", "drill": hole / f, "d": pad / f, "shape": "circle"},
                       "arrays": [{"count": n // 2, "x": 0, "y": 0, "dx": 0, "dy": 2.54 / f, "first_number": 1},
                                  {"count": n // 2, "x": row / f, "y": (n // 2 - 1) * 2.54 / f, "dx": 0,
                                   "dy": -2.54 / f, "first_number": n // 2 + 1}],
                       "body": {"x1": 0.635 / f, "y1": -0.78 / f, "x2": (row - 0.635) / f,
                                "y2": ((n // 2 - 1) * 2.54 + 0.78) / f}}}
    s.update(kw)
    return s


def built_holes(spec):
    pads = FA.table_layout(spec)[0]
    return {(p["w"], p["h"], tuple(p["drill"])) for p in pads}


def test_a_dip_with_no_printed_holes_gets_kicads_dip_holes():
    s = dip(unit="in")                                                # 16550: 0.035 in holes, 0.065 in pads
    assert D.holes_unread(s) and D.dip_table(s) and D.tht_holes_unread(s) is None
    out = D.standard_dip_holes(s)
    assert built_holes(out) == {(1.6, 1.6, (0.8, 0.8))}, built_holes(out)
    assert FA.table_layout(out)[1] == FA.table_layout(s)[1]          # the body stays the read's
    assert s["pad_table"]["default_pad"]["drill"] == 0.035            # the read itself is not changed
    assert D.standard_dip_holes(dip(n=20, row=7.62))                  # UCC1895J, AM26LS31CN, 27C256 ...


def test_printed_holes_or_no_dip_are_left_alone_or_held():
    assert D.standard_dip_holes(dip(drill={"nom": 0.9})) is None      # printed: the read's own
    assert not D.holes_unread(dip(drill={"nom": 0.9}))
    # holes the read did not say it missed are the table's own (a connector's PC board layout prints them there)
    assert not D.holes_unread(dip(unresolved=[])) and D.standard_dip_holes(dip(unresolved=[])) is None
    assert not D.holes_unread(dip(unresolved=["pad copper size (only hole diameter given)"]))     # S10B-PH-K
    assert D.standard_dip_holes(dip(row=12.0)) is None and not D.dip_table(dip(row=12.0))
    assert D.standard_dip_holes(dip(terminal_width={"min": 0.9, "max": 1.0})) is None
    # KBU4A: four pins in a row, holes and pads "assumed from the lead diameter"
    kbu = {"package_type": "custom", "name": "KBU", "pin_count": 4, "unresolved": ["land pattern (not given in datasheet)"],
           "pad_table": {
        "unit": "mm", "default_pad": {"type": "tht", "d": 2.2, "drill": 1.5, "shape": "circle"},
        "pads": [{"number": str(i + 1), "x": 5.08 * i, "y": 0} for i in range(4)]}}
    why = D.tht_holes_unread(kbu)
    assert why and why.startswith("no footprint:") and "hole size" in why, why
    smd = dict(kbu, pad_table=dict(kbu["pad_table"], default_pad={"type": "smd", "w": 2, "h": 2}))
    assert not D.holes_unread(smd) and D.tht_holes_unread(smd) is None


def test_a_dip_numbered_otherwise_is_no_plain_dip():
    s = dip(n=8, row=7.62)
    s["pad_table"]["arrays"][1].update(first_number=5, y=0, dy=2.54)    # the second column numbered downwards too
    assert not D.dip_table(s)


# ---------------------------------------------------------------- a body outline ending on the hole row
def eh(y1=0.0, y2=6.7):
    return {"package_type": "connector", "name": "JST_EH_S13B-EH_1x13_P2.50mm_Horizontal", "pin_count": 13,
            "pitch": 2.5, "drill": {"min": 0.9, "nom": 0.9, "max": 1.0},
            "pad_table": {"unit": "mm", "y_axis": "up", "origin": "pin1",
                          "default_pad": {"type": "tht", "drill": 0.9},
                          "arrays": [{"count": 13, "x": 0, "y": 0, "dx": 2.5, "dy": 0, "first_number": 1}],
                          "body": {"x1": -2.5, "y1": y1, "x2": 32.5, "y2": y2}}}


def test_a_body_outline_through_the_hole_row_is_held():
    why = D.body_on_holes(eh())
    assert why and why.startswith("no footprint:") and "housing" in why, why


def test_a_body_around_the_holes_builds():
    assert D.body_on_holes(eh(y1=-1.35)) is None                        # S10B-PH-K: the housing reaches behind the row
    assert D.body_on_holes(dict(eh(), pad_table={k: v for k, v in eh()["pad_table"].items() if k != "body"})) is None
    # a DIP's body between its columns, its edge 0.635 mm from them
    assert D.body_on_holes(dip(drill={"nom": 0.8})) is None


def test_the_eval_set_scores_ucc1895j_against_the_plain_dip_20():
    import json
    from ecadlib.eval import runner as R
    sets = Path(R.__file__).parent / "sets"
    errata = json.loads((sets / "kicad-v1.errata.json").read_text(encoding="utf-8"))["cases"]
    refs = json.loads((sets / "kicad-v1.json").read_text(encoding="utf-8"))["references"]
    case = {"mpn": "UCC1895J", "footprint": "Package_DIP:DIP-20_W7.62mm_LongPads"}
    assert R.reference_for(case, errata) == "Package_DIP:DIP-20_W7.62mm"
    assert {tuple(p[3:]) for p in refs["Package_DIP:DIP-20_W7.62mm"]["pads"]} == {(1.6, 1.6, 0.8)}
    assert errata["SM6T100A"]["symbol_names"]["ours"] == [["1", "K"], ["2", "A"]]
