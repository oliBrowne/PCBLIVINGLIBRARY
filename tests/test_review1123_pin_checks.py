"""The free pin checks (the datasheet's own text layer, ecadlib.pin_text / pin_drawing) release a part with no person
looking: the false agreements the 2026-11-23 review found (1.4, 1.9, 1.10 and the series diode pair) are held.
Run:  python3 -m tests.test_review1123_pin_checks"""
import io

from ecadlib import librarian as L
from ecadlib import pin_text as PT
from tests.fixtures_lite import lite


WIDE = {"C": 7.22, "E": 6.67, "L": 5.56, "N": 7.22, "P": 6.67, "Q": 7.78, "R": 7.22}     # Helvetica at 10 pt


def pdf(pages, bars=()):
    """A PDF of text lines (Helvetica 10 pt), one list a page, written by hand: no PDF library is needed to make it.
    bars: (page index, line index): the first word of that line is drawn with a line over it, as a PDF draws the bar
    of an active-low name (the text layer then has the bare letters)."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for pi, lines in enumerate(pages):
        ops, y = [], 800
        for li, ln in enumerate(lines):
            text = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 10 Tf 50 {y} Td ({text}) Tj ET")
            if (pi, li) in bars:
                ops.append(f"0.5 w 50 {y + 9} m {50 + sum(WIDE[ch] for ch in ln.split()[0]):.2f} {y + 9} l S")
            y -= 16
        content = "\n".join(ops).encode("latin-1")
        kids.append(len(objs) + 1)
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents %d 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
                    % (len(objs) + 2))
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)
    out, offs = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    x = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs))
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x))
    return out.getvalue()


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


# ------------------------------------------------------------------ 1.9: the active-low mark is compared on its own
# A D flip-flop's pin table as a PDF prints it: the bar over a name is a drawn line, so the text has "Q" on two rows
FLOP = ["Pin Functions", "NAME NO. I/O DESCRIPTION", "CLK 1 I Clock input", "D 2 I Data input", "Q 3 O Inverted output",
        "GND 4 - Ground", "Q 5 O Output", "CLR 6 I Clear, active low", "PRE 7 I Preset, active low", "VCC 8 - Supply"]
FLOP_BARS = [(0, 4), (0, 7), (0, 8)]
RIGHT = [("1", "CLK"), ("2", "D"), ("3", "~{Q}"), ("4", "GND"), ("5", "Q"), ("6", "~{CLR}"), ("7", "~{PRE}"), ("8", "VCC")]
WRONG = [("1", "CLK"), ("2", "D"), ("3", "Q"), ("4", "GND"), ("5", "~{Q}"), ("6", "~{CLR}"), ("7", "~{PRE}"), ("8", "VCC")]
BARE = [(n, s.strip("~{}")) for n, s in RIGHT]


def test_q_and_not_q_on_each_others_pins_do_not_agree_with_the_text_layer():
    data = pdf([FLOP], FLOP_BARS)
    assert PT.page_texts(data)[1].count("Q ") >= 2                           # the text layer has Q twice, no bar
    ok = PT.check_pdf(data, RIGHT, pin_count=8)
    assert ok["status"] == "agreed" and ok["pins"] == 8, ok                  # the drawn bars settle which row is barred
    bad = PT.check_pdf(data, WRONG, pin_count=8)
    assert bad["status"] == "disagreed" and "pin 3" in bad["why"], bad
    assert PT.check_pdf(data, BARE, pin_count=8)["status"] == "disagreed"    # no bars at all in the symbol
    res = lambda pins: {"symbol": {"pins": [{"num": n, "name": s} for n, s in pins]}, "spec": {"pin_count": 8},
                        "needs_review": []}
    assert L.read_confirmed(res(RIGHT), {"data": data}, ["symbol"])
    assert L.read_confirmed(res(WRONG), {"data": data}, ["symbol"]) is None
    assert L.read_confirmed(res(BARE), {"data": data}, ["symbol"]) is None


def test_the_text_alone_does_not_settle_a_pin_with_an_active_low_mark():
    text = {1: "\n".join(FLOP)}
    v = PT.check(text, RIGHT, pin_count=8)                                   # no PDF: the bars cannot be seen
    assert v["status"] == "unchecked" and not v.get("doubt") and "active-low" in v["why"], v
    assert PT.check(text, WRONG, pin_count=8)["status"] == "unchecked"
    assert PT.check(text, BARE, pin_count=8)["status"] == "agreed"           # nothing in the text says otherwise
    plain = pdf([FLOP])                                                      # a PDF that draws no bar over these rows
    v = PT.check_pdf(plain, RIGHT, pin_count=8)
    assert v["status"] == "unchecked" and "pin 3" in v["why"], v
    # two pins with one name and the same sense (a part's two grounds) are no doubt
    twice = {1: "NAME NO.\nIN 1\nGND 2\nEN 3\nGND 4\nOUT 5"}
    assert PT.check(twice, [("1", "IN"), ("2", "GND"), ("3", "EN"), ("4", "GND"), ("5", "OUT")])["status"] == "agreed"


def test_a_mark_printed_in_the_text_is_compared():
    text = {1: "NAME NO.\nIN 1\nGND 2\n/EN 3\nFB 4\nOUT 5\nRESET# 6"}
    pins = lambda en, rst: [("1", "IN"), ("2", "GND"), ("3", en), ("4", "FB"), ("5", "OUT"), ("6", rst)]
    assert PT.check(text, pins("~{EN}", "~{RESET}"))["status"] == "agreed"
    v = PT.check(text, pins("EN", "~{RESET}"))
    assert v["status"] == "disagreed" and "pin 3" in v["why"], v
    both = {1: "NAME NO.\nCLK 1\nD 2\n/Q 3\nGND 4\nQ 5"}                     # the text marks one of the pair: settled
    assert PT.check(both, [("1", "CLK"), ("2", "D"), ("3", "~{Q}"), ("4", "GND"), ("5", "Q")])["status"] == "agreed"
    assert PT.check(both, [("1", "CLK"), ("2", "D"), ("3", "Q"), ("4", "GND"), ("5", "~{Q}")])["status"] == "disagreed"


def test_en_where_the_datasheet_draws_a_bar_over_en_does_not_agree():
    rows = ["Pin Functions", "NAME NO. DESCRIPTION", "IN 1 Input", "GND 2 Ground", "EN 3 Enable, active low",
            "FB 4 Feedback", "OUT 5 Output"]
    data = pdf([rows], [(0, 4)])
    pins = lambda en: [("1", "IN"), ("2", "GND"), ("3", en), ("4", "FB"), ("5", "OUT")]
    assert PT.check_pdf(data, pins("~{EN}"))["status"] == "agreed"
    v = PT.check_pdf(data, pins("EN"))
    assert v["status"] == "disagreed" and "pin 3" in v["why"], v
    assert PT.check_pdf(pdf([rows]), pins("EN"))["status"] == "agreed"       # no bar drawn: EN is EN


def test_the_pin_drawing_compares_the_mark_too():
    from ecadlib import pin_drawing as PD
    assert PD.same_pin("other", "3", "~{Q}", "~{Q}") and PD.same_pin("other", "3", "~{Q}", "/Q")
    assert not PD.same_pin("other", "3", "Q", "~{Q}") and not PD.same_pin("other", "3", "~{EN}", "EN")
    assert [d[0] for d in PD.compare("other", WRONG, dict(RIGHT))] == ["3", "5"]
    assert PD.compare("other", RIGHT, dict(RIGHT)) == []
    # the negative leg of a pair is no active-low pin
    pair = [("1", "CLK_P"), ("2", "CLK_N")]
    assert PD.compare("other", pair, {"1": "CLK_P", "2": "CLK_N"}) == []


# ------------------------------------------------------------------ 1.10: a pin the symbol calls NC is confirmed too
BUCK = {1: "Pin Functions\nNAME NO. DESCRIPTION\nBOOT 1 Bootstrap\nVIN 2 Supply input\nEN 3 Enable\nRT 4 Timing resistor\n"
           "FB 5 Feedback\nCOMP 6 Compensation\nGND 7 Ground\nSW 8 Switch node\n"}
BUCK_PINS = [("1", "BOOT"), ("2", "VIN"), ("3", "EN"), ("4", "RT"), ("5", "FB"), ("6", "COMP"), ("7", "GND"), ("8", "SW")]


def read(pins):
    return {"symbol": {"pins": [{"num": n, "name": s} for n, s in pins]}, "spec": {"pin_count": 8}, "needs_review": []}


def test_a_signal_pin_the_symbol_calls_nc_disagrees_with_the_table():
    assert PT.check(BUCK, BUCK_PINS, pin_count=8)["status"] == "agreed"
    v = PT.check(BUCK, [(n, "NC" if n in "35" else s) for n, s in BUCK_PINS], pin_count=8)
    assert v["status"] == "disagreed" and "pin 3" in v["why"] and "EN" in v["why"], v
    v = PT.check(BUCK, [(n, s if s in ("VIN", "GND") else "NC") for n, s in BUCK_PINS], pin_count=8)
    assert v["status"] == "disagreed", v
    blank = [(n, "" if n in "35" else s) for n, s in BUCK_PINS]              # left blank by a read: not confirmed
    assert PT.check(BUCK, blank, pin_count=8)["status"] == "agreed"          # (KiCad draws some pins unnamed)
    v = PT.check(BUCK, blank, pin_count=8, blank=True)
    assert v["status"] == "unchecked" and "pin 3" in v["why"], v
    # a table that prints the number first
    first = {1: "PIN NAME DESCRIPTION\n1 OUT1 output 1\n2 IN1- inverting input 1\n3 IN1+ non-inverting input 1\n4 GND ground\n"
                "5 IN2+ non-inverting input 2\n6 IN2- inverting input 2\n7 OUT2 output 2\n8 VCC supply"}
    amp = [("1", "OUT1"), ("2", "NC"), ("3", "NC"), ("4", "GND"), ("5", "NC"), ("6", "NC"), ("7", "NC"), ("8", "VCC")]
    v = PT.check(first, amp, pin_count=8)
    assert v["status"] == "disagreed" and "pin 2" in v["why"], v


def test_a_real_nc_pin_still_agrees():
    for nc in ("NC 3 No connection", "N.C. 3 Not connected internally", "NC 3, 5 No connect", "RESERVED 3 Leave open",
               "— 3 No internal connection"):
        text = {1: BUCK[1].replace("EN 3 Enable\n", nc + "\n")}
        v = PT.check(text, [(n, "NC" if n == "3" else s) for n, s in BUCK_PINS], pin_count=8)
        assert v["status"] == "agreed", (nc, v)
    # no row for pin 3 at all: not confirmed (round 2, C4: a row the check does not recognise looks the same as no
    # row; this case asserted 'agreed' before)
    v = PT.check({1: BUCK[1].replace("EN 3 Enable\n", "")}, [(n, "NC" if n == "3" else s) for n, s in BUCK_PINS], pin_count=8)
    assert v["status"] == "unchecked" and not v.get("doubt") and "pin 3" in v["why"], v
    first = {1: "PIN NAME DESCRIPTION\n1 IN Input\n2 GND Ground\n3 No internal connection\n4 FB Feedback\n5 OUT Output\n"}
    pins = [("1", "IN"), ("2", "GND"), ("3", "NC"), ("4", "FB"), ("5", "OUT")]
    assert PT.check(first, pins)["status"] == "agreed"
    # a line of the description that begins with a number is no row of a table that prints the name first
    wrapped = {1: BUCK[1].replace("EN 3 Enable\n", "NC 3 No connection\n").replace("RT 4 Timing resistor\n",
                                                                                  "RT 4 Timing resistor, at least\n3 kOhm to ground\n")}
    assert PT.check(wrapped, [(n, "NC" if n == "3" else s) for n, s in BUCK_PINS], pin_count=8)["status"] == "agreed"


@lite
def test_a_read_that_calls_signal_pins_nc_is_not_confirmed(monkeypatch):
    monkeypatch.setattr(PT, "check_pdf", lambda data, pins, **kw: PT.check(BUCK, pins, pin_count=kw.get("pin_count"), blank=kw.get("blank")))
    assert L.read_confirmed(read(BUCK_PINS), {"data": b"x"}, ["symbol"])
    for pins in ([(n, "NC" if n in "35" else s) for n, s in BUCK_PINS],
                 [(n, s if s in ("VIN", "GND") else "NC") for n, s in BUCK_PINS],
                 [(n, s if s in ("VIN", "GND") else "") for n, s in BUCK_PINS]):
        assert L.read_confirmed(read(pins), {"data": b"x"}, ["symbol"]) is None, pins
    # few_named, as its two sibling callers apply it: a table that prints two of the eight pins compared too little
    two = {1: "Pin Functions\nNAME NO. DESCRIPTION\nVIN 2 Supply input\nGND 7 Ground\n"}
    monkeypatch.setattr(PT, "check_pdf", lambda data, pins, **kw: PT.check(two, pins, pin_count=kw.get("pin_count"), blank=kw.get("blank")))
    pins = [(n, s if s in ("VIN", "GND") else "NC") for n, s in BUCK_PINS]
    assert PT.check(two, pins[1:2] + pins[6:7], pin_count=8)["status"] == "agreed"
    assert PT.check(two, pins, pin_count=8)["status"] == "unchecked"       # round 2, C4: six NC pins with no row
    assert L.read_confirmed(read(pins), {"data": b"x"}, ["symbol"]) is None
    with_rows = {1: two[1] + "NC 1, 3, 4, 5, 6, 8 No connection\n"}       # the NC rows printed: few_named holds it
    monkeypatch.setattr(PT, "check_pdf", lambda data, pins, **kw: PT.check(with_rows, pins, pin_count=kw.get("pin_count"), blank=kw.get("blank")))
    assert PT.check(with_rows, pins, pin_count=8)["status"] == "agreed"
    assert L.read_confirmed(read(pins), {"data": b"x"}, ["symbol"]) is None


# ------------------------------------------------------------------ a series diode pair is not one diode
# onsemi draws BAT54S / BAV99 as 'ANODE 1', 'CATHODE 2', '3 CATHODE/ANODE'
PAIR = [(413, 589, "1", 8.0), (396, 579, "ANODE", 8.0), (495, 589, "2", 8.0), (480, 579, "CATHODE", 8.0),
        (455, 620, "3", 8.0), (430, 630, "CATHODE/ANODE", 8.0),
        (434, 673, "1", 7.0), (453, 663, "2", 7.0), (474, 696, "3", 7.0)]
PAIR_P1 = "BAT54S Schottky Barrier Diodes ... SOT-23 (TO-236) CASE 318 STYLE 11"
PAIR_OUTLINE = ("CASE 318 ISSUE AU STYLE 9: PIN 1. ANODE 2. ANODE 3. CATHODE STYLE 11: PIN 1. ANODE 2. CATHODE "
                "3. CATHODE-ANODE STYLE 12: PIN 1. CATHODE 2. CATHODE 3. ANODE")


def test_a_series_pairs_joined_label_is_a_terminal():
    assert PT.diode_orders({1: PAIR_P1, 5: PAIR_OUTLINE}, [(1, PAIR)]) == []
    assert PT.diode_orders({1: PAIR_P1}, [(1, PAIR)]) == []                 # the style table not in the text
    assert PT.diode_orders({1: "BAT54S dual Schottky"}, [(1, PAIR)]) == []  # a maker with no CASE / STYLE pointer
    split = PAIR[:5] + [(430, 630, "CATHODE", 8.0), (470, 630, "/ANODE", 8.0)] + PAIR[6:]
    assert PT.diode_orders({1: "x"}, [(1, split)]) == []


def test_a_third_lead_with_a_label_that_is_not_no_connect_settles_nothing():
    third = lambda label: DIODE[:4] + [(455, 620, "2", 8.0), (440, 630, label, 8.0)]
    assert PT.diode_orders({1: "x"}, [(1, DIODE)]) == ["ANK"]               # no label by the third lead
    assert PT.diode_orders({1: "x"}, [(1, third("NC"))]) == ["ANK"]
    assert PT.diode_orders({1: "x"}, [(1, third("NO CONNECTION"))]) == ["ANK"]
    assert PT.diode_orders({1: "x"}, [(1, third("COMMON"))]) == []
    assert PT.diode_orders({1: "x"}, [(1, third("K1, A2"))]) == []


def test_a_style_named_up_front_whose_table_is_not_found_is_not_settled():
    assert PT.diode_orders({1: P1, 5: OUTLINE}, [(1, DIODE)]) == ["ANK"]
    assert PT.diode_orders({1: P1}, [(1, DIODE)]) == []                     # CASE 318 STYLE 8 named, its table missing
    assert PT.diode_orders({1: "BAT54L"}, [(1, DIODE)]) == ["ANK"]          # no style named: the drawing alone


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
