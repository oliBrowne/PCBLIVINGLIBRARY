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


def test_a_bigger_packages_table_on_another_page_is_dismissed_when_the_names_match_by_class_too():
    """LMV321 (5 pins, KiCad names +, -, V+, V-): page 3's table agrees; page 4 is the quad LMV324's 14-pin table,
    whose 1IN+ ... 4IN+, VCC+ and GND carry other numbers. Its numbers go above 5: another package, not a doubt."""
    pins = [("2", "V-"), ("5", "V+"), ("1", "+"), ("3", "-"), ("4", "")]
    p3 = "Table 5-2. Pin Functions: LMV321\nNAME NO.\n1IN+ 1 I Noninverting input\n1IN- 3 I Inverting input\n" \
         "GND 2 - Negative supply\nOUT 4 O Output\nVCC+ 5 - Positive supply\n"
    p4 = "Table 5-3. Pin Functions: LMV324\nNAME NO.\n1IN+ 3 I Noninverting input\n1IN- 2 I Inverting input\n" \
         "2IN+ 5 I Noninverting input\n2IN- 6 I Inverting input\n2OUT 7 O Output\n3IN+ 10 I Noninverting input\n" \
         "3IN- 9 I Inverting input\n4IN+ 12 I Noninverting input\n4IN- 13 I Inverting input\n4OUT 14 O Output\n" \
         "GND 11 - Negative supply\nVCC+ 4 - Positive supply\n"
    v = PT.check({3: p3, 4: p4}, pins)
    assert v["status"] == "agreed" and v["page"] == 3, v
    same = p4.replace(" 10 ", " 1 ").replace(" 9 ", " 1 ").replace(" 12 ", " 2 ").replace(" 13 ", " 2 ") \
             .replace(" 14 ", " 3 ").replace(" 11 ", " 5 ").replace(" 7 ", " 3 ").replace(" 6 ", " 4 ")
    v = PT.check({3: p3, 4: same}, pins)              # a table of the same size with other numbers: still a doubt
    assert v["status"] == "unchecked", v


def test_a_page_of_spec_lines_that_only_mentions_the_names_is_not_another_pin_table():
    """SN74LVC2G34 (SOT-23-6): page 3's table agrees; page 5 is Electrical Characteristics, where A, Y, VCC and GND
    stand in conditions ('VI = 5.5 V or GND 0 to 5.5 V') beside other numbers. No pin is named there as a table row
    names it (the name alone before its numbers): not a doubt about the table."""
    pins = [("1", "1A"), ("2", "GND"), ("3", "2A"), ("4", "2Y"), ("5", "VCC"), ("6", "1Y")]
    p3 = "NAME NO.\n1A 1 I Input 1\n1Y 6 O Output 1\n2A 3 I Input 2\n2Y 4 O Output 2\nGND 2 - Ground\nVCC 5 - Supply\n"
    p5 = "6.5 Electrical Characteristics\nII 1A inputs VI = 5.5 V or GND 0 to 5.5 V 5 uA\nICC VI = 5.5 V or GND, IO = 0 10 uA\n" \
         "tpd 1A 1Y 3 8 ns\ntpd 2A 2Y 1 4 ns\nVCC = 5 V 1 3 ns\n"
    v = PT.check({3: p3, 5: p5}, pins)
    assert v["status"] == "agreed" and v["page"] == 3, v
    other = "NAME NO.\n1A 1 I Input 1\n1Y 6 O Output 1\n2A 3 I Input 2\n2Y 4 O Output 2\nGND 5 - Ground\nVCC 2 - Supply\n"
    v = PT.check({3: p3, 5: other}, pins)             # a real table with GND and VCC the other way round: a doubt
    assert v["status"] == "unchecked" and "page 5" in v["why"], v


def test_a_singles_table_is_not_a_doubt_about_the_dual():
    """TL072 (two amplifiers: '+' on 3 and 5, '-' on 2 and 6): page 5's table agrees; page 3 is the single TL071's
    table, one IN+ row and one IN- row. A table with fewer input rows than the symbol has inputs is another device's."""
    pins = [("1", ""), ("2", "-"), ("3", "+"), ("4", "V-"), ("5", "+"), ("6", "-"), ("7", ""), ("8", "V+")]
    p5 = "Pin Functions: TL072x\nNAME NO.\n1IN- 2 Input\n1IN+ 3 Input\n1OUT 1 Output\n2IN- 6 Input\n2IN+ 5 Input\n" \
         "2OUT 7 Output\nVCC- 4 Power\nVCC+ 8 Power\n"
    p3 = "Pin Functions: TL071x\nNAME NO.\nIN- 2 Input\nIN+ 3 Input\nNC 1 -\nNC 5 -\nNC 8 -\nOUT 6 Output\nVCC- 4 Power\nVCC+ 7 Power\n"
    v = PT.check({3: p3, 5: p5}, pins)
    assert v["status"] == "agreed" and v["page"] == 5, v
    swapped = p5.replace("1IN- 2", "1IN- 3").replace("1IN+ 3", "1IN+ 2")
    v = PT.check({3: swapped, 5: p5}, pins)           # another dual's table with the inputs the other way: a doubt
    assert v["status"] in ("unchecked", "disagreed"), v


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            print("ok", k)
