"""The free pin-table check with names two sources spell differently, and tables that carry other packages' columns.
Run:  python3 -m tests.test_pin_names_1118

KiCad draws an op-amp's inputs as '+' and '-' and its rails as V+ / V-; the datasheet prints 1IN+, +IN A, VCC, GND.
The check sets the spelling aside for those names only, and never the number."""
from ecadlib import pin_text as PT

LM358 = [("1", ""), ("2", "-"), ("3", "+"), ("4", "V-"), ("5", "+"), ("6", "-"), ("7", ""), ("8", "V+")]
TI_DUAL = """Table 4-1. Pin Functions
NAME NO. I/O DESCRIPTION
1IN- 2 I Negative input
1IN+ 3 I Positive input
1OUT 1 O Output
2IN- 6 I Negative input
2IN+ 5 I Positive input
2OUT 7 O Output
GND 4 - Ground
VCC 8 - Power supply"""


def test_name_classes():
    for n in ("+", "IN+", "1IN+", "IN1+", "+INA", "+IN A", "INPUT2+", "IN+B"):
        assert PT.name_class(n) == "+", n
    for n in ("-", "IN-", "4IN-", "-IN B", "INPUT1-", "IN−"):
        assert PT.name_class(n) == "-", n
    assert PT.name_class("VCC") == PT.name_class("V+") == PT.name_class("VDD") == "pos"
    assert PT.name_class("GND") == PT.name_class("V-") == PT.name_class("VSS") == "neg"
    for n in ("OUT", "IN", "INA", "EN", "A0", "VREF+", "VIN", "12IN+", "1A+"):
        assert PT.name_class(n) is None, n


def test_bare_sign_inputs_and_single_rails_agree_by_number():
    v = PT.check({4: TI_DUAL}, LM358)
    assert v["status"] == "agreed" and v["pins"] == 6, v


def test_swapped_inputs_do_not_agree():
    swapped = [("2", "+"), ("3", "-")] + [p for p in LM358 if p[0] not in "23"]
    assert PT.check({4: TI_DUAL}, swapped)["status"] != "agreed"


def test_swapped_rails_do_not_agree():
    swapped = [("4", "V+"), ("8", "V-")] + [p for p in LM358 if p[0] not in "48"]
    assert PT.check({4: TI_DUAL}, swapped)["status"] != "agreed"


def test_a_name_the_page_prints_is_matched_exactly_not_by_class():
    # the datasheet prints GND itself on pin 4 and VEE on pin 5: a symbol's GND on pin 5 must not ride on VEE's line
    page = "IN+ 3 I\nIN- 2 I\nOUT 1 O\nGND 4 -\nVEE 5 -\nVCC 8 -"
    pins = [("3", "IN+"), ("2", "IN-"), ("1", "OUT"), ("5", "GND"), ("8", "VCC")]
    assert PT.check({1: page}, pins)["status"] != "agreed"


def test_two_rails_of_one_class_keep_their_exact_names():
    # a symbol with VCC and VDD: neither may match the other's line
    page = "A 1 I\nB 2 I\nGND 3 -\nVDD 4 -\nVCC 5 -"
    assert PT.loose_names([("4", "VCC"), ("5", "VDD"), ("3", "GND")]) == {"GND"}
    assert PT.check({1: page}, [("1", "A"), ("2", "B"), ("3", "GND"), ("4", "VCC"), ("5", "VDD")])["status"] != "agreed"
    assert PT.check({1: page}, [("1", "A"), ("2", "B"), ("3", "GND"), ("4", "VDD"), ("5", "VCC")])["status"] == "agreed"


LM339 = [("1", ""), ("2", ""), ("3", "V+"), ("4", "-"), ("5", "+"), ("6", "-"), ("7", "+"), ("8", "-"), ("9", "+"),
         ("10", "-"), ("11", "+"), ("12", "V-"), ("13", ""), ("14", "")]
TI_QUAD = """OUT1 (1) 1 16 Output Output pin of the comparator 2
OUT2 (1) 2 15 Output Output pin of the comparator 1
VCC 3 1 — Positive supply
IN2- (1) 4 5 Input Negative input pin of the comparator 1
IN2+ (1) 5 6 Input Positive input pin of the comparator 1
IN1- (1) 6 2 Input Negative input pin of the comparator 2
IN1+ (1) 7 4 Input Positive input pin of the comparator 2
IN3- 8 7 Input Negative input pin of the comparator 3
IN3+ 9 8 Input Positive input pin of the comparator 3
IN4- 10 9 Input Negative input pin of the comparator 4
IN4+ 11 11 Input Positive input pin of the comparator 4
GND 12 12 — Negative supply
OUT4 13 13 Output Output pin of the comparator 4
OUT3 14 14 Output Output pin of the comparator 3
NC — 3 — No Internal Connection
NC — 10 — No Internal Connection"""


def test_a_column_with_a_pin_number_above_the_parts_highest_is_another_package():
    # the 16-pin QFN column beside the 14-pin SOIC one: its 15 and 16 stand on rows the symbol leaves unnamed
    v = PT.check({4: TI_QUAD}, LM339)
    assert v["status"] == "agreed" and v["column"] == 0, v


def test_a_ball_column_is_another_package():
    page = "IN- 2 A1 I\nIN+ 3 B1 I\nOUT 1 A2 O\nGND 4 C1 -\nVCC 8 C2 -"
    pins = [("2", "-"), ("3", "+"), ("1", "OUT"), ("4", "V-"), ("8", "V+")]
    assert PT.check({1: page}, pins)["status"] == "agreed"


def test_a_same_size_variant_column_still_holds():
    # MCP6L01 / MCP6L01R, SOT-23 / X2SON: both columns could be this part's, the text does not say which
    page = "EN 3 3 I\nGND 2 2 -\nIN 4 1 I\nN/C — 4 —\nOUT 1 5 O"
    v = PT.check({3: page}, [("1", "IN"), ("2", "GND"), ("3", "EN"), ("4", "NC"), ("5", "OUT")])
    assert v["status"] == "unchecked" and "another column" in v["why"], v


def test_a_pin_named_like_a_ball_is_a_name():
    page = "A0 1 User Configurable Chip Select\nA1 2 User Configurable Chip Select\nA2 3 User Configurable Chip Select\n" \
           "VSS 4 Ground\nSDA 5 Serial Data\nSCL 6 Serial Clock\nWP 7 Write-Protect Input\nVCC 8 +1.8V to 5.5V"
    pins = [("1", "A0"), ("2", "A1"), ("3", "A2"), ("4", "GND"), ("5", "SDA"), ("6", "SCL"), ("7", "WP"), ("8", "VCC")]
    assert PT.check({5: page}, pins)["status"] == "agreed"
    wrong = [("2", "A0"), ("1", "A1")] + pins[2:]
    assert PT.check({5: page}, wrong)["status"] != "agreed"


def test_a_ball_table_still_reads_balls_as_numbers():
    page = "A1 IN I Input\nA2 OUT O Output\nB1 EN I Enable\nB2 GND — Ground"
    assert PT.check({3: page}, [("A1", "IN"), ("A2", "OUT"), ("B1", "EN"), ("B2", "GND")])["status"] == "agreed"


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            print("ok", k)
