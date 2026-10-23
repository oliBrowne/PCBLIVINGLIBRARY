"""Free re-score of the 2026-10-21 Opus answers on main 3c3bd08 (2026-10-22): the parts built wrong are held instead.
- a read with no land of its own while the pages it was sent print the maker's recommended land (default run:
  BCR430UW6, NCP4308AMT, BCV61 built IPC pads that differ from that land; the zoom run read the land and matched);
- a custom pad table read without its body (default run: 27C256 DIP-28, the courtyard hugged the pads and missed the
  37 mm body at both ends).
The PDFs here are SYNTHETIC text laid out like those datasheets, not copies.   python3 -m tests.run_all test_holds_misses_1022"""
import copy, tempfile
from pathlib import Path

from ecadlib import datasheet as D, ds_route as DR
from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB, test_ds_route as TR

LAND_PAGE = ["Package Information", "Suggested Pad Layout", "Package Type: SOT26",
             "Dimensions Value (in mm)", "C 0.950", "X 0.550", "Y 0.800", "Y1 3.200", "Z 1.200", "G 1.600"]
POINTER = ["Thermal information", "For the land pattern, see the Mechanical, Packaging and Orderable Information section.",
           "Theta JA 120 C/W", "Theta JC 45 C/W"]


# ---------------------------------------------------------------- which pages print a land
def test_a_land_heading_with_its_sizes_is_a_land_page_and_a_pointer_to_it_is_not():
    texts = ["TESTPART 100 mA LED driver in SOT26", " ".join(POINTER), "\n".join(LAND_PAGE)]
    assert DR.land_pages(texts, [1, 2, 3], "TESTPART", "SOT-23-6") == [3]
    assert DR.land_pages(texts, [1, 2], "TESTPART", "SOT-23-6") == []          # the land page was not sent
    assert DR.land_pages(texts, [], "TESTPART") == [] and DR.land_pages(None, [1]) == []
    for head in ("RECOMMENDED SOLDERING FOOTPRINT*", "LAND PATTERN EXAMPLE", "Footprint information for reflow soldering",
                 "RECOMMENDED SOLDER PAD LAYOUT", "RECOMMENDED LAND PATTERN"):
        t = "\n".join([head] + LAND_PAGE[3:])
        assert DR.land_pages([t], [1]) == [1], head
    # "footprint" alone, or a layout section with sizes, is no land of the package
    for head in ("PCB footprint", "Layout Guidelines", "Board layout", "Thermal pad layout"):
        assert DR.land_pages(["\n".join([head] + LAND_PAGE[3:])], [1]) == [], head
    assert DR.land_pages(["\n".join(LAND_PAGE[:3] + ["see table"])], [1]) == []  # a heading with no sizes


def test_the_only_land_page_of_a_pdf_counts_even_when_it_names_the_package_otherwise():
    # Diodes BCR430UW6: the BOM says SC-74-6, the one pad layout page says SOT26 (the same package): page_scores calls it
    # another package, but a PDF with one land page has no other package to print a land for
    texts = ["TESTPART 100 mA LED driver in SOT26", " ".join(POINTER), "\n".join(LAND_PAGE)]
    assert DR.land_pages(texts, [3], "BCR430UW6", "SC-74-6") == [3]


def test_a_land_page_about_another_package_only_does_not_count():
    texts = ["\n".join(p) for p in TR.ldo_pages()]   # SOT223 / SOT89 pad layouts on p19 / p20 (3 and 2 sizes), as is
    assert DR.land_pages(texts, [19, 20], "TESTLDO-H33TRG1", "SOT223") == [19]
    assert DR.land_pages(texts, [19, 20], "TESTLDO-R33TRG1", "SOT89") == [20]
    # a family sheet whose only land page is another package's: not this part's land (Greptile, PR #171)
    del texts[18]
    assert DR.land_pages(texts, [19], "TESTLDO-H33TRG1", "SOT223") == []


def test_an_outline_page_that_points_to_the_land_elsewhere_is_no_land_page():
    # body dimensions on the page, and a sentence sending the reader to the land pattern section (Greptile, PR #171)
    outline = ["Package Outline Dimensions (All dimensions in mm)", "Dim Min Max", "A 0.90 1.10", "D 2.80 3.00",
               "E 1.50 1.70", "e 0.95 BSC", "For the land pattern, see the Mechanical, Packaging and Orderable "
               "Information section. Refer to the recommended land pattern 1.20 x 0.60 in the app note."]
    assert DR.land_pages(["\n".join(outline)], [1]) == []
    assert not DR.prints_land("Package Outline Dimensions D 2.80 3.00 E 1.50 1.70 A 0.90 1.10\nLand pattern\nsee page 9")


# ---------------------------------------------------------------- the hold
def gull():
    return {"package_type": "gullwing", "name": "SOT-23-6", "pin_count": 6, "pitch": 0.95}


def test_no_land_read_while_the_pages_print_one_is_held():
    line = D.land_unread(gull(), [6])
    assert line and line.startswith("no footprint:") and "recommended land" in line and "page 6" in line, line
    for t in ("qfn", "qfp", "chip", "molded"):
        assert D.land_unread(dict(gull(), package_type=t), [6]), t


def test_a_land_read_or_no_land_page_or_a_pad_table_builds():
    assert D.land_unread(gull(), []) is None and D.land_unread(gull(), None) is None
    assert D.land_unread(dict(gull(), land={"pad_length": 0.8, "pad_width": 0.55, "pad_center": 1.2}), [6]) is None
    # a land page that sizes only the thermal land is the maker's land too; the signal pads come from IPC
    assert D.land_unread(dict(gull(), land={"ep_across": 3.4, "ep_along": 5.0}), [6]) is None
    assert D.land_unread(dict(gull(), package_type="custom", pad_table={"pads": [{"number": "1"}]}), [6]) is None
    assert D.land_unread(dict(gull(), pad_table={"pads": [{"number": "1"}]}), [6]) is None
    for t in ("bga", "connector", "pin_header", "custom"):        # these never size pads by IPC from the body
        assert D.land_unread(dict(gull(), package_type=t), [6]) is None, t


def read_through_librarian(ext, pdf):
    api = TC.API(lambda k, m, b: TR.on_page(TD._complete(copy.deepcopy(ext))))
    with tempfile.TemporaryDirectory() as d:
        http = TB.PdfHTTP({"https://example.com/ds.pdf": pdf, "https://www.lcsc.com/datasheet/C9999.pdf": pdf})
        items, _, _ = TB.run2(d, api, http=http, reader_kw={"batch": False, "rules": TR.no_cross_check({})})
        a = items["DSPART1"]
        side = Path(d) / "out" / "datasheet"
        return a, sorted(side.glob("*.extraction.json"))


def odd_ep():
    """GOOD with an exposed pad no checked VQFN-16 has: stdparts.identify names no package for it."""
    e = copy.deepcopy(TD.GOOD)
    e["package"]["exposed_pad"] = {"length": TD.dim("D2", None, 1.37, None, page=32), "width": TD.dim("E2", None, 1.21, None, page=32)}
    e["package"]["name"] = "VQFN-16-1EP_3x3mm_P0.5mm_EP1.37x1.21mm"
    return e


def test_the_librarian_holds_a_read_that_missed_the_land_on_its_pages():
    pdf = TR.pdf_lines([["TESTPART 3 A buck converter", "VQFN-16 3x3 mm"], POINTER, LAND_PAGE])
    a, _ = read_through_librarian(odd_ep(), pdf)
    assert not (a.get("footprint") or {}).get("file"), a.get("footprint")
    tried = [t for t in a["librarian"]["tried"] if t.startswith("datasheet")]
    assert any("recommended land for this package (page 3)" in t for t in tried), tried
    # the same read with the land block builds from it
    with_land = odd_ep()
    keys = D.output_schema()["properties"]["package"]["properties"]["land"]["anyOf"][0]["properties"]
    with_land["package"]["land"] = dict(dict.fromkeys(keys), pad_length=0.8, pad_width=0.3, pad_center=1.45)
    a, _ = read_through_librarian(with_land, pdf)
    assert (a.get("footprint") or {}).get("file"), a["librarian"]["tried"]
    # and a PDF without the land page builds the IPC land as before
    a, _ = read_through_librarian(odd_ep(), TR.pdf_lines([["TESTPART 3 A buck converter", "VQFN-16 3x3 mm"], POINTER]))
    assert (a.get("footprint") or {}).get("file") and a["source"]["footprint"].startswith("datasheet:"), a["librarian"]["tried"]


def test_a_missed_land_on_a_checked_package_builds_the_checked_standard_land_and_says_so():
    # the read names exactly one checked package (stdparts.identify): its land, KiCad's, rather than IPC pads from the
    # body (default run 2026-10-22: LT3652IDD, LT8610, LTC7810 matched KiCad that way and would otherwise be held)
    pdf = TR.pdf_lines([["TESTPART 3 A buck converter", "VQFN-16 3x3 mm"], POINTER, LAND_PAGE])
    a, _ = read_through_librarian(TD.GOOD, pdf)
    assert (a.get("footprint") or {}).get("file") and a["source"]["footprint"].startswith("standard:"), a["source"]
    rv = [r for r in a["librarian"]["review"] if r.startswith("standard land:")]
    assert rv and "prints its own land (page 3) but the read did not return it" in rv[0], a["librarian"]["review"]


# ---------------------------------------------------------------- a custom pad table with no body
def dip28(**kw):
    s = {"package_type": "custom", "name": "DIP-28_W15.24mm", "pin_count": 28, "pitch": 2.54,
         "pad_table": {"unit": "mm", "default_pad": {"type": "tht", "drill": 0.8, "w": 1.6, "h": 1.6, "shape": "oval"},
                       "arrays": [{"count": 14, "x": 0, "y": 0, "dx": 0, "dy": 2.54, "first_number": 1},
                                  {"count": 14, "x": 15.24, "y": 33.02, "dx": 0, "dy": -2.54, "first_number": 15}]}}
    s.update(kw)
    return s


def test_a_custom_pad_table_with_no_body_is_held():
    line = D.body_unread(dip28())
    assert line and "left the part's body outline blank" in line, line


def test_a_custom_pad_table_with_a_body_or_a_bare_pad_pattern_still_builds():
    assert D.body_unread(dip28(body_length={"min": 36.703, "max": 37.338}, body_width={"min": 13.462, "max": 13.97})) is None
    t = dip28()
    t["pad_table"]["body"] = {"x1": -0.6, "y1": -1.8, "x2": 15.8, "y2": 34.8}
    assert D.body_unread(t) is None
    assert D.body_unread({"package_type": "custom", "name": "x", "pin_count": 6}) is None   # no pad table to build from
    pads = [{"number": str(i), "x": x, "y": y, "type": "smd", "w": 0.787, "h": 0.787, "shape": "circle"}
            for i, (x, y) in enumerate([(-1.27, -0.635), (-1.27, 0.635), (0, -0.635), (0, 0.635), (1.27, -0.635), (1.27, 0.635)], 1)]
    holes = [{"x": x, "y": y, "type": "npth", "drill": 0.991} for x, y in [(-2.54, 0), (2.54, 1.016), (2.54, -1.016)]]
    tc = {"package_type": "custom", "name": "Tag-Connect_TC2030", "pin_count": 6, "pad_table": {"unit": "mm", "pads": pads + holes}}
    assert D.bare_pads(tc) and D.body_unread(tc) is None                       # TC2030 (zoom run: passes)
    assert D.body_unread(dict(tc, height_max=4.5))                             # a seated height: a housing is there


# ---------------------------------------------------------------- a pad table with its body, called a pin header
def test_a_pad_table_with_its_body_called_a_pin_header_by_the_second_read_is_a_word_only():
    from ecadlib import crosscheck as CC
    from tests.test_crosscheck_tiebreak_1021 import read, nominal
    # AM26LS31CN (default run): DIP-16 read as a custom pad table with its body; the second read says pin_header
    dip16 = dip28(name="DIP-16_W7.62mm", pin_count=16, body_length={"min": 18.92, "max": 19.69},
                  body_width={"min": 6.1, "max": 6.6})
    c = read(dip16, nominal(package_type="pin_header", pitch=2.54, pin_count=16))
    assert c["status"] == "agreed" and c.get("tie_breaks"), c
    # with no body read it stays held (the type would decide nothing, but the read is held for its body anyway),
    # and a count that differs still holds
    assert read(dip28(), nominal(package_type="pin_header", pitch=2.54, pin_count=28))["status"] == "disagreed"
    assert read(dip16, nominal(package_type="pin_header", pitch=2.54, pin_count=14))["status"] == "disagreed"
    # the other way round, a read's pin header against a connector, still holds (S13B-EH, S10B-PH-K)
    hdr = {"package_type": "pin_header", "name": "JST_PH_S10B-PH-K_1x10_P2.00mm_Horizontal", "pin_count": 10, "pitch": 2.0}
    assert read(hdr, nominal(package_type="connector", pitch=2.0, pin_count=10))["status"] == "disagreed"
    assert not CC._label_only({"package_type": "pin_header", "unread_as": {"custom": False, "connector": False}},
                              {"package_type": "custom"})
