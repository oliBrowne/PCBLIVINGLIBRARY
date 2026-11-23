"""The free pin checks (the datasheet's own text layer, ecadlib.pin_text / pin_drawing) release a part with no person
looking: the false agreements the 2026-11-23 review found (1.4, 1.9, 1.10 and the series diode pair) are held.
Run:  python3 -m tests.test_review1123_pin_checks"""
import io

from ecadlib import librarian as L
from ecadlib import pin_text as PT
from tests.fixtures_lite import lite


def pdf(pages, bars=()):
    """A PDF of text lines, one list a page; bars: (page index, line index, word) drawn with a line over it."""
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b)
    for pi, lines in enumerate(pages):
        c.setFont("Helvetica", 10)
        y = 800
        for li, ln in enumerate(lines):
            c.drawString(50, y, ln)
            for p, l, word in bars:
                if (p, l) == (pi, li):
                    x = 50 + c.stringWidth(ln[:ln.index(word)], "Helvetica", 10)
                    c.line(x, y + 9, x + c.stringWidth(word, "Helvetica", 10), y + 9)
            y -= 16
        c.showPage()
    c.save()
    return b.getvalue()


# ------------------------------------------------------------------ 1.4: a pin order is this PACKAGE's, or it is none
HEAD = ["XYZ3904 / MMXYZ3904  NPN General Purpose Amplifier", "TO-92 (XYZ3904)   SOT-23 (MMXYZ3904)"]
FAMILY = [HEAD + ["Pin Description (TO-92)", "PIN  NAME", "1  E", "2  B", "3  C",
                  "SOT-23 (MMXYZ3904): see the drawing in figure 2"], ["Electrical characteristics", "VCEO 40 V"]]
ROW = {"src_mpn": "MMXYZ3904", "src_several": "", "src_package": "TO-236-3, SC-59, SOT-23-3",
       "src_package_device": "SOT-23-3", "src_description": "TRANS NPN 40V 0.2A SOT23-3"}


class Run(L.Run):
    def __init__(self, data):
        self.data = data

    def datasheet_pdf(self, item, ln, row, st, mpn):
        return self.data, "https://maker.example/sheet.pdf"


def state(land):
    return {"tried": [], "found": {}, "footprint": {"source": f"standard:{land}"}}


def test_package_words_are_read_with_their_lead_count():
    assert PT.package_words("SOT-23 (TO-236AB) plastic package") == {("SOT23", None), ("TO236AB", None)}
    assert PT.package_words("8-SOIC, SO-8 and SOT-23-5") == {("SOIC", 8), ("SO", 8), ("SOT23", 5)}
    assert PT.package_words("SOT\u221223 (TO\u2212236) CASE 318") == {("SOT23", None), ("TO236", None)}   # onsemi's minus sign
    assert PT.package_words("Storage from -55 TO 150, SO the DIP switch is set") == set()     # words, not packages
    assert PT.own_packages(["Package_TO_SOT_SMD:SOT-23"])[0] >= {"SOT23", "TO236", "TO236AB"}
    assert PT.own_packages(["SOIC-8_3.9x4.9mm_P1.27mm"]) == ({"SOIC", "SO", "SOP"}, 8)


def test_a_family_sheets_to92_table_gives_no_order_to_the_sot23_part():
    data = pdf(FAMILY)
    assert PT.pin_orders_pdf(data, "BEC") == ["EBC"]                         # the bare text: the TO-92 order
    why = []
    assert PT.pin_orders_pdf(data, "BEC", part="MMXYZ3904", package=("SOT-23",), doubts=why) == []
    assert "TO92" in why[0] and "SOT23" in why[0], why
    st = state("Package_TO_SOT_SMD:SOT-23")
    assert Run(data).pin_order(st, {}, {}, ROW, "MMXYZ3904", "NPN") is None
    assert "not headed for this part's package alone" in st["tried"][-1], st["tried"]
    # nor to the TO-92 part: the page names both packages, and the text does not say which the table is headed for
    assert PT.pin_orders_pdf(data, "BEC", package=("TO-92_Inline",)) == []


def test_a_sheet_that_names_one_package_only_still_gives_its_order():
    one = pdf([["MMXYZ3904  NPN General Purpose Amplifier", "SOT-23 (TO-236AB)", "Pin Description", "PIN  NAME",
                "1  B", "2  E", "3  C"], ["Electrical characteristics", "VCEO 40 V"]])
    assert PT.pin_orders_pdf(one, "BEC", package=("SOT-23",)) == ["BEC"]
    st = state("Package_TO_SOT_SMD:SOT-23")
    assert Run(one).pin_order(st, {}, {}, ROW, "MMXYZ3904", "NPN") == "BEC", st["tried"]
    # the five-lead SOT-23-5 is another package than the three-lead SOT-23
    five = pdf([["MMXYZ3904", "SOT-23 and SOT-23-5", "PIN  NAME", "1  B", "2  E", "3  C"]])
    assert PT.pin_orders_pdf(five, "BEC", package=("SOT-23",)) == []
    # a sheet that names no package at all cannot be tied to this one; nor can a part whose package is not known
    none = pdf([["MMXYZ3904", "PIN  NAME", "1  B", "2  E", "3  C"]])
    assert PT.pin_orders_pdf(none, "BEC", package=("SOT-23",)) == []
    assert PT.pin_orders_pdf(one, "BEC", package=()) == []


def test_a_family_sheet_gives_the_order_of_the_page_headed_for_this_package():
    fam = pdf([["XYZ3904 / MMXYZ3904", "TO-92 and SOT-23 packages"],
               ["MMXYZ3904 in SOT-23", "PIN  NAME", "1  B", "2  E", "3  C"]])
    assert PT.pin_orders_pdf(fam, "BEC", package=("SOT-23",)) == ["BEC"]
    assert PT.pin_orders_pdf(fam, "BEC", package=("TO-92_Inline",)) == []


@lite
def test_the_sot23_part_of_a_family_sheet_is_not_released_with_the_to92_order(tmp_path):
    from ecadlib import bom_triage as T
    data, out = pdf(FAMILY), tmp_path
    bom = out / "bom.csv"
    bom.write_text("Designator,Qty,Value,Footprint,MPN,Manufacturer\nQ1,1,,,MMXYZ3904,Acme Semi\n")
    real, real_pdf = T.triage, L.Run.datasheet_pdf

    def triage(lines, index, provs, http):
        rows = real(lines, index, [], http)
        for r in rows:                                  # what a DigiKey answer for this exact MPN puts in the row
            r.update(ROW, src="digikey", src_package_from="digikey", src_description_from="digikey",
                     src_manufacturer="Acme Semi", src_manufacturer_from="digikey",
                     src_datasheet="https://maker.example/XYZ3904.pdf", src_datasheet_from="digikey")
        return rows
    T.triage = triage
    L.Run.datasheet_pdf = lambda self, item, ln, row, st, mpn: (data, "https://maker.example/XYZ3904.pdf")
    try:
        items = L.run(str(bom), [], out / "lib", ["standard"], None, False, [], True, out / "cache", 0.0, [],
                      log=lambda *a, **k: None, user="Rev <rev@x.com>")
    finally:
        T.triage, L.Run.datasheet_pdf = real, real_pdf
    it = items[0]
    assert it["status"] != "ready" and "Q_NPN_EBC" not in str(it.get("source")), (it["status"], it.get("source"))
    assert any("not headed for this part's package alone" in t for t in it["librarian"]["tried"]), it["librarian"]["tried"]


SO8 = ["PIN  NAME", "1  S", "2  S", "3  S", "4  G", "5  D", "6  D", "7  D", "8  D"]
SO8_ROW = {"src_mpn": "XYF7400", "src_several": "", "src_package": "8-SOIC (0.154\", 3.90mm Width)",
           "src_description": "MOSFET N-CH 30V 10A 8SOIC"}


def test_an_so8_mosfet_order_is_this_packages_or_none():
    st = state("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    one = pdf([["XYF7400 N-channel MOSFET", "SO-8 package"] + SO8])
    assert Run(one).so8_order(st, {}, {}, SO8_ROW, "XYF7400", "NMOS") == "SSSGDDDD", st["tried"]
    fam = pdf([["XYF7400 / XYF7400T N-channel MOSFET", "SO-8 (XYF7400)   TSSOP-8 (XYF7400T)", "Pin Description (TSSOP-8)"] + SO8])
    assert Run(fam).so8_order(st, {}, {}, SO8_ROW, "XYF7400", "NMOS") is None
    assert "not headed for this part's package alone" in st["tried"][-1], st["tried"]


DIODE = [(413, 589, "3", 8.0), (396, 579, "CATHODE", 8.0), (495, 589, "1", 8.0), (480, 579, "ANODE", 8.0),
         (434, 673, "1", 7.0), (453, 663, "2", 7.0), (474, 696, "3", 7.0)]            # as onsemi draws BAS16LT1G
P1 = "BAT54L Schottky Barrier Diodes ... SOT-23 (TO-236) CASE 318 STYLE 8"
OUTLINE = "SOT-23 (TO-236) CASE 318 ISSUE AU STYLE 8: PIN 1. ANODE 2. NO CONNECTION 3. CATHODE STYLE 9: PIN 1. ANODE 2. ANODE 3. CATHODE"


def test_a_diodes_lead_order_is_this_packages_or_none():
    assert PT.diode_orders({1: P1, 5: OUTLINE}, [(1, DIODE)], package=("SOT-23",)) == ["ANK"]
    assert PT.diode_orders({1: P1}, [(1, DIODE)], package=("SOT-323_SC-70",)) == []
    why = []
    fam = {1: "BAT54 SOT-23 / BAT54W SOT-323 Schottky Barrier Diodes"}
    assert PT.diode_orders(fam, [(1, DIODE)], package=("SOT-23",), doubts=why) == [] and "SOT323" in why[0], why
    assert PT.diode_orders({1: "x"}, [(1, DIODE)], package=("SOT-23",)) == []        # no package named: not tied


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
