"""The free pin-table check on a table with one number column per package ('NAME SOT-23 SC70'), on names and numbers
the PDF's text layer splits over two lines, and on 'VS' as a supply's name.
Run:  python3 -m tests.test_package_columns_1119

A table that prints each pin's number for several packages used to leave the part unchecked (which column is this
part's is not in the text). When the table's own header names each column's package and exactly one of them is this
part's (its land or the part search's case), that column is checked; every other column is a package this part is not."""
from ecadlib import pin_text as PT

INA181 = [("1", ""), ("2", "GND"), ("3", "+"), ("4", "-"), ("5", "REF"), ("6", "V+")]
INA181_PAGE = """Table 5-1. Pin Functions: INA181 (Single Channel)
PIN
TYPE DESCRIPTION
NAME SOT-23 SC70
GND 2 2 Analog Ground
IN– 4 5 Analog input
Current-sense amplifier negative input.
IN+ 3 4 Analog input
Current-sense amplifier positive input.
OUT 1 6 Analog output Output voltage
REF 5 1 Analog input Reference input
VS 6 3 Analog Power supply, 2.7 V to 5.5 V"""

LP5907 = [("1", "IN"), ("2", "GND"), ("3", "EN"), ("4", "NC"), ("5", "OUT")]
LP5907_PAGE = """Table 4-2. Pin Functions: X2SON, SOT-23
PIN
TYPE DESCRIPTION
NAME X2SON SOT-23
EN 3 3 I Enable input.
GND 2 2 — Common ground.
IN 4 1 I Input voltage supply.
N/C — 4 — No internal connection.
OUT 1 5 O Regulated output voltage."""


def test_vs_is_a_positive_supply_name():
    assert PT.name_class("VS") == "pos"


def test_column_named_for_this_package_is_checked():
    v = PT.check({4: INA181_PAGE}, INA181, package=["SOT-23-6", "SOT-23-6"])
    assert v["status"] == "agreed" and v["column"] == 0 and "SOT-23" in v.get("where", ""), v
    v = PT.check({3: LP5907_PAGE}, LP5907, package=["Package_TO_SOT_SMD:SOT-23-5", "SOT-23-5"])
    assert v["status"] == "agreed" and v["column"] == 1, v


def test_without_a_package_the_table_stays_unchecked():
    assert PT.check({4: INA181_PAGE}, INA181)["status"] == "unchecked"


def test_the_other_packages_column_is_not_taken():
    # an SC70 part (KiCad's SOT-353 / SC-70 land) on the same table: its own column, which these pins do not fit
    v = PT.check({4: INA181_PAGE}, INA181, package=["SOT-353_SC-70-5"])
    assert v["status"] != "agreed", v
    # a package no header column names: unchecked as before
    assert PT.check({4: INA181_PAGE}, INA181, package=["SOIC-8_3.9x4.9mm"])["status"] == "unchecked"


def test_pins_in_the_wrong_column_do_not_agree():
    swapped = [("4", "+"), ("3", "-")] + [p for p in INA181 if p[0] not in "34"]
    assert PT.check({4: INA181_PAGE}, swapped, package=["SOT-23-6"])["status"] != "agreed"


def test_two_columns_of_one_family_settle_nothing():
    page = INA181_PAGE.replace("NAME SOT-23 SC70", "NAME SOT-23 SOT-23")
    assert PT.check({4: page}, INA181, package=["SOT-23-6"])["status"] == "unchecked"


def test_header_with_fewer_names_than_columns_is_not_used():
    page = INA181_PAGE.replace("NAME SOT-23 SC70", "NAME SOT-23 NO.")
    assert PT.check({4: page}, INA181, package=["SOT-23-6"])["status"] == "unchecked"


def test_package_families():
    assert PT.package_families(["Package_TO_SOT_SMD:SOT-23-5", "SOT-23-5"]) == {"SOT23"}
    assert PT.package_families(["SOT-353_SC-70-5"]) == {"SOT353", "SC70"}
    assert PT.package_families(['8-SOIC (0.154", 3.90mm Width)', "SOIC-8_3.9x4.9mm_P1.27mm"]) == {"SOIC"}
    assert PT.package_families(["TSOT-23-6"]) == {"TSOT23"}
    assert PT.package_families(["X2SON"]) == {"X2SON"}


SN65 = [("1", "D"), ("2", "GND"), ("3", "VCC"), ("4", "R"), ("5", "Vref"), ("6", "CANL"), ("7", "CANH"), ("8", "Rs")]
SN65_PAGE = """Pin Functions
PIN
TYPE DESCRIPTION
NAME NO.
D 1 I CAN transmit data input
GND 2 GND Ground connection
VCC 3 Supply Transceiver 3.3V supply voltage
R 4 O CAN receive data output
Vref
5
O SN65HVD230 and SN65HVD231: VCC / 2 reference output pin
NC NC SN65HVD232: No Connect
CANL 6 I/O Low level CAN bus line
CANH 7 I/O High level CAN bus line
RS
8
I Mode select pin"""


def test_a_name_and_its_number_on_two_lines_are_one_row():
    v = PT.check({5: SN65_PAGE}, SN65)
    assert v["status"] == "agreed" and v["pins"] == 8, v
    swapped = [("5", "Rs"), ("8", "Vref")] + [p for p in SN65 if p[0] not in "58"]
    assert PT.check({5: SN65_PAGE}, swapped)["status"] != "agreed"


def test_a_lone_number_after_a_sentence_is_not_a_row():
    page = SN65_PAGE.replace("Vref\n5\n", "the reference output is\n5\n")
    assert PT.check({5: page}, SN65)["status"] != "agreed"


REG = [("1", "GND"), ("2", "VO"), ("3", "VI")]
TLV1117 = """Table 5-1. Pin Functions
PIN
I/O DESCRIPTION
NAME NO.
IN 3 I Input pin. See the Input and Output Capacitor Requirements section.
OUT 2, Tab O Regulated output voltage pin.
GND 1 — Ground pin."""


def test_input_and_output_spellings_and_the_tab():
    # a regulator's VI / VO (a family table's names) are the datasheet's IN / OUT; '2, Tab' is pin 2 (and the tab)
    v = PT.check({3: TLV1117}, REG)
    assert v["status"] == "agreed" and v["pins"] == 3, v
    assert PT.check({3: TLV1117}, [("1", "GND"), ("3", "VO"), ("2", "VI")])["status"] != "agreed"
    assert PT.check({3: TLV1117.replace("GND 1", "ADJ/GND 1")}, REG)["status"] == "agreed"


def test_io_spellings_only_for_a_single_pin_of_that_kind():
    # a part with OUT and VOUT pins keeps them apart by their exact names
    assert "OUT" not in PT.loose_names([("1", "OUT"), ("2", "VOUT"), ("3", "IN")])
    assert "IN" in PT.loose_names([("1", "OUT"), ("2", "VOUT"), ("3", "IN")])
    assert PT.loose_class("VIN") == PT.loose_class("IN") == PT.loose_class("VI") == "vin"
    assert PT.loose_class("VOUT") == PT.loose_class("OUTPUT") == "vout"
    assert PT.loose_class("EN") is None and PT.name_class("OUT") is None


LAYOUT_FIGURE = """10.2 Layout Example
2
GND
2
VOUTA
2
IN+B
2
IN+A 2
IN+D
2
IN+C
1
GND
2
V+
1
IN-A
1
IN-A
1
VINA"""


def test_a_drawings_labels_and_values_are_not_joined_into_rows():
    rows = PT._rows(LAYOUT_FIGURE)
    assert not any(c[0] in ("IN+B", "IN-A", "GND", "V+") and len(c) == 2 for c, _ in rows), rows


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:
                fails += 1
                print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
