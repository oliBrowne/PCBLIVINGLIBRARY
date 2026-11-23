"""Round 3 of the 2026-11-23 review, the free pin checks (ecadlib.pin_text). A7: a no-connect row for the same pin
number anywhere near the table hid the row that names the pin; a 'RESERVED' row that must be tied somewhere counted
as not connected. A5: another package's pin order was still given to this part whenever the other package's name was
not one the code knew (37 spellings): the order is now tied to the PART, not to a list of package names. B4: both
names of one outline on a one-package sheet, and capital prose ('UP TO 92 %'), were a doubt. Each has the inputs
that must hold and the ordinary ones that must stay ready.
Run:  python3 -m tests.test_review1123_round3_pin_checks"""
from ecadlib import librarian as L
from ecadlib import pin_text as PT
from tests.test_review1123_pin_checks import BUCK, BUCK_PINS, DIODE, OUTLINE, P1, ROW, SO8, SO8_ROW, Run, pdf, read, state

NC3 = [(n, "NC" if n == "3" else s) for n, s in BUCK_PINS]
ROWS = BUCK[1].split("\n")[2:-1]                       # the eight rows, 'BOOT 1 Bootstrap' ...
HEAD = "Pin Functions\nNAME NO. DESCRIPTION\n"


def table(rows, head=HEAD):
    return {1: head + "\n".join(rows) + "\n"}


def verdict(text, pins=NC3):
    return L.few_named(PT.check(text, pins, pin_count=8, blank=True), pins)


# ------------------------------------------------------------------ A7: every row of an NC pin is a no-connect row
def test_a_no_connect_row_does_not_hide_the_row_that_names_the_pin():
    # the reviewer's three: the NC row beside EN's in the table, in a second table lower on the page, above the table
    beside = table(ROWS[:3] + ["NC 3 No connection (fixed)"] + ROWS[3:])
    below = table(ROWS + ["", "SOT-23-5 version", "NC 3 No connection"])
    above = table(["NC 3 No connection", ""] + ROWS)
    for text in (beside, below, above, table(["NC 3 No connection"] + ROWS), table(ROWS + ["NC 3 No connection"])):
        v = verdict(text)
        assert v["status"] == "disagreed" and "pin 3 EN" in v["why"], (text, v)
    # left blank by the read: not compared, as before
    blank = [(n, "" if n == "3" else s) for n, s in BUCK_PINS]
    assert verdict(beside, blank)["status"] == "unchecked"


def test_a_no_connect_row_in_another_table_confirms_nothing():
    # this table's row for pin 3 is printed in a way the check does not read ('EN 3* Enable'); the no-connect row
    # that stands after a blank line, or under a heading, is another table's
    star = [r.replace("EN 3 ", "EN 3* ") for r in ROWS]
    for tail in (["", "NC 3 No connection"], ["SOT-23-5 version", "NC 3 No connection"],
                 ["Table 3. Pin Functions (fixed version)", "NAME NO. DESCRIPTION", "NC 3 No connection"],
                 ["", "SOT-23-5 version", "VIN 2 Supply input", "NC 3 No connection"]):
        v = verdict(table(star + tail))
        assert v["status"] == "unchecked" and "pin 3" in v["why"] and "no-connect" in v["why"], (tail, v)
    v = verdict(table(["NC 3 No connection", ""] + star))
    assert v["status"] == "unchecked" and "pin 3" in v["why"], v
    # two whole tables on the page, one naming pin 3 and one calling it not connected: which is this part's is not
    # in the text, whichever stands first
    fixed = [r.replace("EN 3 Enable", "NC 3 No connection") for r in ROWS]
    for rows in (fixed + ["", "Adjustable version"] + ROWS, ROWS + ["", "Fixed version"] + fixed):
        v = verdict(table(rows))
        assert v["status"] != "agreed" and "pin 3" in v["why"], (rows, v)


def test_a_reserved_row_that_must_be_tied_is_not_a_no_connect_row():
    for row in ("RESERVED 3 Must be tied to VIN", "RSVD 3 Connect to GND", "RESERVED 3 Tie to VIN through 10 k",
                "NC 3 Must be connected to GND", "3 RESERVED Must be tied to VIN", "- 3 Enable input",
                "— 3 Enable"):
        rows = ROWS[:2] + [row] + ROWS[3:]
        if row[0] == "3":                              # a table that prints the number first
            rows = [" ".join([r.split()[1], r.split()[0]] + r.split()[2:]) if r != row else r for r in rows]
        v = verdict(table(rows))
        assert v["status"] != "agreed" and "pin 3" in v["why"], (row, v)


def test_ordinary_no_connect_rows_stay_confirmed():
    for row in ("NC 3 No connection", "N.C. 3 Not connected internally", "NC 3, 5 No connect", "RESERVED 3 Leave open",
                "— 3 No internal connection", "DNC 3 Do not connect", "N/C 3", "RESERVED 3", "- 3",
                "RESERVED 3 Do not connect", "RSVD 3 Reserved. Must be left floating", "NC 3 Not connected. May be tied to GND",
                "NC 3 No connect; can be connected to ground or left open", "RESERVED 3 Must not be connected",
                "NC 3 Not internally connected, leave unconnected"):
        v = verdict(table(ROWS[:2] + [row] + ROWS[3:]))
        assert v["status"] == "agreed" and v["pins"] == 7, (row, v)
    # the description of a row runs on to the next line; a table printed with the NC pins in two rows
    wrapped = table(ROWS[:2] + ["NC 3 No connection. This pin is not bonded", "to the die"] + ROWS[3:])
    assert verdict(wrapped)["status"] == "agreed"
    pins = [(n, "NC" if n in "35" else s) for n, s in BUCK_PINS]
    two = table(ROWS[:2] + ["NC 3 No connection"] + ROWS[3:4] + ["NC 5 No connection"] + ROWS[5:])
    assert verdict(two, pins)["status"] == "agreed"
    # a second table lower on the page that says the same of pin 3, and one that does not print pin 3 at all
    fixed = [r.replace("EN 3 Enable", "NC 3 No connection") for r in ROWS]
    assert verdict(table(fixed + ["", "Ordering information", "XYZ1234 3 000 pieces on a reel"]))["status"] == "agreed"
    assert verdict(table(fixed + ["", "Fixed version, 3.3 V"] + fixed))["status"] == "agreed"
    # the number first
    first = {1: "PIN NAME DESCRIPTION\n1 IN Input\n2 GND Ground\n3 NC No internal connection\n4 FB Feedback\n5 OUT Output\n"}
    assert PT.check(first, [("1", "IN"), ("2", "GND"), ("3", "NC"), ("4", "FB"), ("5", "OUT")], blank=True)["status"] == "agreed"
    assert PT.check(BUCK, BUCK_PINS, pin_count=8, blank=True)["status"] == "agreed"


def test_a_read_whose_nc_pin_is_named_by_a_row_is_not_released():
    real = PT.check_pdf
    beside = table(ROWS[:3] + ["NC 3 No connection (fixed)"] + ROWS[3:])
    tied = table(ROWS[:2] + ["RESERVED 3 Must be tied to VIN"] + ROWS[3:])
    good = table(ROWS[:2] + ["NC 3 No connection"] + ROWS[3:])
    try:
        for text in (beside, tied):
            PT.check_pdf = lambda data, pins, text=text, **kw: PT.check(text, pins, pin_count=kw.get("pin_count"), blank=kw.get("blank"))
            assert L.read_confirmed(read(NC3), {"data": b"x"}, ["symbol"]) is None, text
        PT.check_pdf = lambda data, pins, **kw: PT.check(good, pins, pin_count=kw.get("pin_count"), blank=kw.get("blank"))
        assert L.read_confirmed(read(NC3), {"data": b"x"}, ["symbol"])
    finally:
        PT.check_pdf = real


# ------------------------------------------------------------------ A5: the order is tied to the part, not to a list
TABLE = "Pin Description\nPIN NAME\n1 E\n2 B\n3 C\n"
OTHERS = ["To 92", "to 92", "TO.92", "TO_92", "TO - 92", "TO -92", "T0-92", "(TO)92", "TO-5", "TO 5", "TO-3", "TO-3P",
          "SIP-3", "SIP3", "3-SIP", "DIP", "KT-26", "I-PAK", "IPAK", "DFN1006-3", "X1-DFN1006-3", "SOT-1123", "TSOT-23",
          "SOT-23 Flat", "SOT-23 FL", "CPH3", "MCPH3", "S-Mini", "SSMini3", "SMini3-G1", "USM", "SSM", "TUMT3", "PCP",
          "through-hole", "leaded", "radial"]                                # the reviewer's 37
MADE_UP = ["ZQX", "Qwerty-7", "Blob", "our new", "X"]


def family(other, part="XYZ3904"):
    """The reviewer's sheet: two devices, the other one's package spelt `other`, and one pin table (the other's)."""
    return {1: f"XYZ3904 / MMXYZ3904 NPN General Purpose Amplifier\n{other} package ({part})\nSOT-23 (MMXYZ3904)\n" + TABLE,
            2: "Electrical characteristics"}


def order(text, part="MMXYZ3904", package=("SOT-23",), names="BEC"):
    why = []
    return PT.pin_orders(text, names, part=part, package=package, doubts=why), why


def test_another_parts_package_is_a_doubt_whatever_it_is_called():
    assert len(OTHERS) == 37
    for other in OTHERS + MADE_UP:
        text = family(other)
        assert PT.pin_orders(text, "BEC") == ["EBC"], other                 # the bare text: the other package's order
        got, why = order(text)
        assert got == [] and why and "package" in why[0], (other, got, why)
        got, why = order(text, part=None)                                    # the part number not given: still two parts
        assert got == [] and why, (other, got, why)


def test_the_title_names_two_parts_wherever_the_other_package_is_named():
    lines = "\n".join(f"Feature line {i} of the part" for i in range(12))
    cases = {
        "the other part's package is not named at all": "XYZ3904 / MMXYZ3904 NPN General Purpose Amplifier\nSOT-23\n" + TABLE,
        "the two parts in a list, far above the table": "XYZ3904, MMXYZ3904\nNPN Amplifier\n" + lines + "\nSOT-23\n" + TABLE,
        "a base name and a variant of it": "MMXYZ3904 MMXYZ3904W NPN General Purpose Amplifier\nSOT-23\n" + TABLE,
        "the list is not at the top of the page": "NPN General Purpose Amplifier\nSOT-23\n" + lines + "\n" + TABLE
                                                  + "Order codes: XYZ3904 / MMXYZ3904\n",
    }
    for label, page in cases.items():
        got, why = order({1: page, 2: "Electrical characteristics"})
        assert got == [] and "part number" in why[0], (label, got, why)
    # the title page names two parts and the table's own page names none
    got, why = order({1: "XYZ3904 / MMXYZ3904 NPN General Purpose Amplifier\nSOT-23", 2: TABLE})
    assert got == [] and "part number" in why[0], (got, why)
    # the sheet's only part number is another part's
    got, why = order({1: "XYZ3904 NPN General Purpose Amplifier\nSOT-23\n" + TABLE})
    assert got == [] and "part number" in why[0], (got, why)


def test_a_package_claimed_beside_this_parts_number_must_be_its_own():
    # one part number, sold in two packages: the unknown name stands beside the number
    for line in ("ZQX package (MMXYZ3904)", "MMXYZ3904 (ZQX)", "MMXYZ3904 in a ZQX package", "ZQX (MMXYZ3904)",
                 "MMXYZ3904 in SOT-23 Flat package", "To 92 package (MMXYZ3904)", "MMXYZ3904 in ZQX and SOT-23 packages",
                 "through-hole package (MMXYZ3904)", "MMXYZ3904 (TO)92"):
        text = {1: f"MMXYZ3904 NPN General Purpose Amplifier\n{line}\nSOT-23 (MMXYZ3904)\n" + TABLE, 2: "Electrical characteristics"}
        assert PT.pin_orders(text, "BEC") == ["EBC"], line
        got, why = order(text)
        assert got == [] and why and "package" in why[0], (line, got, why)


def test_the_sot23_part_is_not_released_with_an_unknown_packages_order():
    for other in ("To 92", "TO-5", "S-Mini", "leaded", "ZQX"):
        data = pdf([[ln for ln in family(other)[1].split("\n") if ln], ["Electrical characteristics"]])
        st = state("Package_TO_SOT_SMD:SOT-23")
        assert Run(data).pin_order(st, {}, {}, ROW, "MMXYZ3904", "NPN") is None, (other, st["tried"])
        assert "does not settle the pin order" in st["tried"][-1] and "package" in st["tried"][-1], st["tried"]
    # an SO-8 MOSFET and a diode: two part numbers in the title
    st = state("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    fam = pdf([["XYF7400 / XYF7400T N-channel MOSFET", "SO-8"] + SO8])
    assert Run(fam).so8_order(st, {}, {}, SO8_ROW, "XYF7400", "NMOS") is None
    assert "part number" in st["tried"][-1], st["tried"]
    why = []
    assert PT.diode_orders({1: "BAT54L / BAT54T Schottky Barrier Diodes SOT-23", 5: OUTLINE.replace("CASE 318", "")},
                           [(1, DIODE)], package=("SOT-23",), doubts=why) == [] and "part number" in why[0], why


def test_ordinary_one_part_one_package_sheets_stay_confirmed():
    heads = ["MMXYZ3904 NPN General Purpose Amplifier\nSOT-23",
             "MMXYZ3904\nNPN General Purpose Amplifier\nSOT-23 (TO-236AB)",
             "MMXYZ3904 NPN General Purpose Amplifier in a SOT-23 package",
             "MMXYZ3904 NPN General Purpose Amplifier\nSOT-23 (MMXYZ3904)",
             "MMXYZ3904 (NPN)\nSOT-23 plastic package",
             "MMXYZ3904 (SOT-23)\nNPN General Purpose Amplifier",
             "MMXYZ3904 NPN General Purpose Amplifier\nMMXYZ3904 is supplied in the SOT-23 surface mount package",
             "MMXYZ3904LT1G (Pb-Free)\nSOT-23 (TO-236) CASE 318",
             "MMXYZ3904 NPN Amplifier, AEC-Q101 qualified, UL94V-0\nSOT-23",
             "NPN General Purpose Amplifier\nSOT-23",                       # no part number in the text at all
             "MMXYZ3904x NPN General Purpose Amplifier\n3-pin SOT-23 package (see note 1)"]
    for head in heads:
        text = {1: head + "\n" + TABLE, 2: "Electrical characteristics\nSOT-23 package outline"}
        for part in ("MMXYZ3904LT1G", "MMXYZ3904LT1G-Q", None) + (("MMXYZ3904", "MMXYZ3904-7-F") if "LT1G" not in head else ()):
            got, why = order(text, part=part)
            assert got == ["EBC"], (head, part, why)
    # the BOM's number is the base name, the sheet prints the orderable one only
    got, why = order({1: "MMXYZ3904LT1G NPN Amplifier\nSOT-23\n" + TABLE})
    assert got == ["EBC"], why
    # a family sheet whose table is headed for this part and this package (the title page names both parts)
    fam = {1: "XYZ3904 / MMXYZ3904\nTO-92 and SOT-23 packages", 2: "MMXYZ3904 in SOT-23\n" + TABLE}
    assert order(fam) == (["EBC"], [])
    assert order(fam, part="XYZ3904", package=("TO-92_Inline",))[0] == []
    # through the librarian: a one-part sheet is still released
    data = pdf([["MMXYZ3904  NPN General Purpose Amplifier", "SOT-23 (MMXYZ3904)", "Pin Description", "PIN  NAME",
                 "1  B", "2  E", "3  C"], ["Electrical characteristics", "Tstg -55 TO 150"]])
    st = state("Package_TO_SOT_SMD:SOT-23")
    assert Run(data).pin_order(st, {}, {}, ROW, "MMXYZ3904", "NPN") == "BEC", st["tried"]
    st = state("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    one = pdf([["XYF7400 N-channel MOSFET", "XYF7400 in the SO-8 package"] + SO8])
    assert Run(one).so8_order(st, {}, {}, SO8_ROW, "XYF7400", "NMOS") == "SSSGDDDD", st["tried"]
    assert PT.diode_orders({1: P1, 5: OUTLINE}, [(1, DIODE)], package=("SOT-23",), part="BAT54LT1G") == ["ANK"]


def test_package_names_with_one_and_four_digits():
    words = lambda t: {b for b, _ in PT.package_words(t)}
    assert words("TO-3 and TO-5") == {"TO3", "TO5"} and words("TO-3P") == {"TO3P"}
    assert words("SOT-1123, SOT1061") == {"SOT1123", "SOT1061"} and words("DO-4") == {"DO4"}
    for plain in ("0 to 5 V", "up to -5 V", "TO 5", "SC18IS602", "DO3316P-103", "do 3 times"):
        assert words(plain) == set(), (plain, words(plain))


# ------------------------------------------------------------------ B4: both names of one outline; capital prose
def test_both_names_of_one_outline_are_one_package():
    sheets = [("SOT89 (SC-62)", ("SOT-89-3",)), ("SOT-89 (SC-62, TO-243AA)", ("SOT-89-3",)), ("SOT223 (SC-73)", ("SOT-223-3_TabPin2",)),
              ("SOT-223 (TO-261AA)", ("SOT-223",)), ("TO-92 (TO-226)", ("TO-92_Inline",)), ("TO-92 (TO-226AA)", ("TO-92_Inline",)),
              ("TO-92 (SOT54)", ("TO-92_Inline",)), ("TO-92, SC-43", ("TO-92_Inline",))]
    for head, land in sheets:
        text = {1: f"XYZ56 NPN transistor\n{head} package\n" + TABLE}
        got, why = order(text, part="XYZ56", package=land)
        assert got == ["EBC"], (head, why)
    own = lambda land: PT.own_packages([land])[0]
    assert {"SOD323", "SC76"} <= own("D_SOD-323") and {"SOD523", "SC79"} <= own("D_SOD-523")
    assert {"SOT363", "SC88"} <= own("SOT-363_SC-70-6") and {"SOT89", "SC62"} <= own("SOT-89-3")
    # another outline is still another package
    for head, land in (("SOT-89 (SC-59)", ("SOT-89-3",)), ("SOT-23 package (SC-59 compatible)", ("SOT-23",)),
                       ("TO-92 and SOT-223", ("TO-92_Inline",)), ("SOT-223 (SC-62)", ("SOT-223",))):
        got, why = order({1: f"XYZ56 NPN transistor\n{head}\n" + TABLE}, part="XYZ56", package=land)
        assert got == [] and why, (head, got)


def test_capital_prose_with_a_number_is_no_package():
    words = lambda t: {b for b, _ in PT.package_words(t)}
    for plain in ("EFFICIENCY UP TO 92 %", "CONNECT TO 220 OHM LOAD", "CAPACITANCE SC 70 pF", "UP TO 220 V", "TO 92%",
                  "RANGE FROM 18 TO 247 kHz", "DERATE TO 126 mW", "LIMIT TO 220 mA"):
        assert words(plain) == set(), (plain, words(plain))
        text = {1: f"MMXYZ3904 NPN General Purpose Amplifier\nSOT-23 package\n{plain}\n" + TABLE}
        got, why = order(text)
        assert got == ["EBC"], (plain, why)
    # a package printed in capitals with a space is still one
    assert words("TO 92 package") == {"TO92"} and words("AVAILABLE IN TO 220") == {"TO220"}
    assert words("TO 220 AB") == {"TO220"} and words("SOT 23") == {"SOT23"}
    got, why = order({1: "MMXYZ3904 NPN General Purpose Amplifier\nSOT-23 package\nALSO IN TO 92\n" + TABLE})
    assert got == [] and "TO92" in why[0], why


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
