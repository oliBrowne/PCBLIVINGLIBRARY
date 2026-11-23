"""Round 3 of the 2026-11-23 review, the free pin checks (ecadlib.pin_text). A7: a no-connect row for the same pin
number anywhere near the table hid the row that names the pin; a 'RESERVED' row that must be tied somewhere counted
as not connected. A5: another package's pin order was still given to this part whenever the other package's name was
not one the code knew (37 spellings): the order is now tied to the PART, not to a list of package names. B4: both
names of one outline on a one-package sheet, and capital prose ('UP TO 92 %'), were a doubt. Each has the inputs
that must hold and the ordinary ones that must stay ready.
Run:  python3 -m tests.test_review1123_round3_pin_checks"""
from ecadlib import librarian as L
from ecadlib import pin_text as PT
from tests.test_review1123_pin_checks import BUCK, BUCK_PINS, ROW, SO8, SO8_ROW, Run, pdf, read, state

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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
