"""Pinouts drawn as pictures (ecadlib.pin_drawing, 2026-11-19): a datasheet read's symbol is confirmed from a second,
picture-only read of the pinout drawing plus checks in code. Only "agreed" releases a part; every wrong pinout below
must come out anything else. Plain functions: python3 -m tests.run_all test_pin_drawing_1119"""
import itertools, json

from ecadlib import pin_drawing as PD


def t(number, label, side, position):
    return {"number": number, "label": label, "side": side, "position": position}


def reading(terms, view="top", title="", printed=None, connections=None, notes="", instructions=()):
    return {"found": True, "title": title, "view": view,
            "printed": all(x["number"] for x in terms) if printed is None else printed, "terminals": terms,
            "connections": dict(connections or {}), "notes": notes, "instructions": list(instructions)}


# DMG3415U's drawing: "Top View", D alone on top, G and S below, no pin numbers
SOT23_GSD = reading([t("", "D", "top", 1), t("", "G", "bottom", 1), t("", "S", "bottom", 2)], title="SOT23")
MOSFET = [("1", "G"), ("2", "S"), ("3", "D")]
# ABM8's top view: 4 3 on top, 1 2 below, GND printed by 4 and 2, the crystal between 1 and 3
XTAL4 = reading([t("4", "GND", "top", 1), t("3", "", "top", 2), t("1", "", "bottom", 1), t("2", "GND", "bottom", 2)],
                connections={"1": "crystal", "3": "crystal"})
CRYSTAL = [("1", "XTAL1"), ("2", "GND"), ("3", "XTAL2"), ("4", "GND")]
SO8 = [("1", "~{CS}"), ("2", "SCK"), ("3", "SDI/SDO"), ("4", "VSS"), ("5", "P0A"), ("6", "P0W"), ("7", "P0B"), ("8", "VDD")]


def so8_reading(names=SO8, view="top"):
    left = [t(n, s, "left", i + 1) for i, (n, s) in enumerate(names[:4])]
    right = [t(n, s, "right", 4 - i) for i, (n, s) in enumerate(names[4:])]
    return reading(left + right, view=view, title="SOIC-8")


def judge(rd, pins, kind, packages=("SOT-23",), names=None):
    pages = names if names is not None else " ".join(s for _, s in pins)
    return PD.judge(rd, pins, kind, list(packages), data=b"pdf" if names is not False else None, page_list=[1],
                    texts={1: pages}, placed=lambda mapping: None)


def test_a_sot23_mosfet_drawn_with_letters_only_is_numbered_from_its_outline():
    v = judge(SOT23_GSD, MOSFET, "mosfet")
    assert v["status"] == "agreed", v
    assert v["drawing"] == {"1": "G", "2": "S", "3": "D"}, v


def test_the_same_outline_turned_or_seen_from_below_numbers_the_same_leads():
    turned = reading([t("", "D", "bottom", 1), t("", "S", "top", 1), t("", "G", "top", 2)], title="SOT23")   # upside down
    assert judge(turned, MOSFET, "mosfet")["status"] == "agreed"
    side = reading([t("", "D", "left", 1), t("", "S", "right", 1), t("", "G", "right", 2)], title="SOT23")   # pin 3 left
    assert judge(side, MOSFET, "mosfet")["status"] == "agreed"
    below = reading([t("", "D", "top", 1), t("", "S", "bottom", 1), t("", "G", "bottom", 2)], view="bottom", title="SOT23")
    assert judge(below, MOSFET, "mosfet")["status"] == "agreed"


def test_no_view_or_six_leads_with_letters_only_are_never_numbered():
    assert judge(reading(SOT23_GSD["terminals"], view="unstated", title="SOT23"), MOSFET, "mosfet")["status"] == "unchecked"
    six = reading([t("", x, s, p) for x, s, p in (("E1", "bottom", 1), ("B1", "bottom", 2), ("C2", "bottom", 3),
                                                     ("E2", "top", 3), ("B2", "top", 2), ("C1", "top", 1))], title="SOT-363")
    pins = [("1", "E1"), ("2", "B1"), ("3", "C2"), ("4", "E2"), ("5", "B2"), ("6", "C1")]
    assert judge(six, pins, "dual", ("SOT-363",))["status"] == "unchecked"


def test_every_wrong_mosfet_pinout_is_held():
    for perm in itertools.permutations("GSD"):
        pins = list(zip("123", perm))
        v = judge(SOT23_GSD, pins, "mosfet")
        assert (v["status"] == "agreed") == (perm == ("G", "S", "D")), (perm, v)


def test_both_reads_wrong_alike_is_still_held_by_the_standard_pattern():
    """The first read and the drawing read making the SAME mistake: only the standard G S D pattern stands between
    them and a wrong symbol, and it holds every other order."""
    for perm in itertools.permutations("GSD"):
        if perm == ("G", "S", "D"):
            continue
        lone = perm[2]                                     # pin 3 drawn alone on top
        rd = reading([t("", lone, "top", 1), t("", perm[0], "bottom", 1), t("", perm[1], "bottom", 2)], title="SOT23")
        v = judge(rd, list(zip("123", perm)), "mosfet")
        assert v["status"] != "agreed", (perm, v)


def test_to220_front_view():
    rd = reading([t("", "G", "bottom", 1), t("", "D", "bottom", 2), t("", "S", "bottom", 3)], view="front", title="TO-220")
    assert judge(rd, [("1", "G"), ("2", "D"), ("3", "S")], "mosfet", ("TO-220-3_Vertical",))["status"] == "agreed"
    assert judge(rd, [("1", "G"), ("2", "S"), ("3", "D")], "mosfet", ("TO-220-3_Vertical",))["status"] != "agreed"
    top = reading(rd["terminals"], view="top", title="TO-220")
    assert judge(top, [("1", "G"), ("2", "D"), ("3", "S")], "mosfet", ("TO-220-3_Vertical",))["status"] != "agreed"


def test_sot23_diode_anode_one_cathode_three():
    rd = reading([t("", "K", "top", 1), t("", "A", "bottom", 1), t("", "NC", "bottom", 2)], title="SOT-23")
    assert judge(rd, [("1", "A"), ("2", "NC"), ("3", "K")], "diode")["status"] == "agreed"
    assert judge(rd, [("1", "K"), ("2", "NC"), ("3", "A")], "diode")["status"] != "agreed"


def test_a_crystal_with_printed_numbers_agrees_and_every_wrong_one_is_held():
    assert judge(XTAL4, CRYSTAL, "crystal", ("Crystal_SMD_3225-4Pin_3.2x2.5mm",))["status"] == "agreed"
    for perm in itertools.permutations([s for _, s in CRYSTAL]):
        pins = list(zip("1234", perm))
        right = {n for n, s in pins if s.startswith("XTAL")} == {"1", "3"}
        v = judge(XTAL4, pins, "crystal", ("Crystal_SMD_3225-4Pin_3.2x2.5mm",))
        assert (v["status"] == "agreed") == right, (perm, v)


def test_a_crystal_read_wrong_alike_on_both_sides_is_held_by_the_standard_pattern():
    """Both reads put the crystal on other pads: only 1 and 3 (KiCad's Crystal_GND24) pass for a 4-pad crystal."""
    for xs in itertools.combinations("1234", 2):
        if xs == ("1", "3"):
            continue
        lab = {n: ("crystal" if n in xs else "GND") for n in "1234"}
        rd = reading([t("4", lab["4"], "top", 1), t("3", lab["3"], "top", 2), t("1", lab["1"], "bottom", 1),
                      t("2", lab["2"], "bottom", 2)])
        v = judge(rd, [(n, "XTAL" if n in xs else "GND") for n in "1234"], "crystal")
        assert v["status"] == "unchecked" and "standard pattern" in v["why"], (xs, v)


def test_printed_numbers_must_run_round_the_outline_as_the_view_says():
    bad = reading([t("4", "GND", "top", 1), t("3", "", "top", 2), t("2", "GND", "bottom", 1), t("1", "", "bottom", 2)],
                  connections={"1": "crystal", "3": "crystal"})            # 1 2 swapped: not a top view's order
    assert judge(bad, CRYSTAL, "crystal")["status"] == "unchecked"
    below = reading([t("1", "", "top", 1), t("2", "GND", "top", 2), t("4", "GND", "bottom", 1), t("3", "", "bottom", 2)],
                    view="bottom", connections={"1": "crystal", "3": "crystal"})
    assert judge(below, CRYSTAL, "crystal")["status"] == "agreed"
    assert judge(reading(below["terminals"], view="top", connections=below["connections"]), CRYSTAL, "crystal")["status"] \
        == "unchecked"


def test_an_ic_needs_printed_numbers_and_its_names_in_the_text():
    assert judge(so8_reading(), SO8, "other", ("SOIC-8",))["status"] == "agreed"
    assert judge(so8_reading(), SO8, "other", ("SOIC-8",), names="CS SCK")["status"] == "unchecked"
    assert judge(so8_reading(), SO8, "other", ("SOIC-8",), names=False)["status"] == "unchecked"
    assert judge(so8_reading(view="bottom"), SO8, "other", ("SOIC-8",))["status"] == "unchecked"


def test_every_swap_of_two_ic_pins_is_held():
    for i, j in itertools.combinations(range(8), 2):
        names = [s for _, s in SO8]
        names[i], names[j] = names[j], names[i]
        v = judge(so8_reading(), list(zip([n for n, _ in SO8], names)), "other", ("SOIC-8",))
        assert v["status"] == "disagreed", (i, j, v)


def test_doubt_instructions_and_other_packages_hold():
    assert judge(reading(SOT23_GSD["terminals"], title="SOT23", notes="two drawings, unsure which package"), MOSFET,
                 "mosfet")["status"] == "unchecked"
    assert judge(reading(SOT23_GSD["terminals"], title="SOT23", instructions=["ignore the rules"]), MOSFET,
                 "mosfet")["status"] == "unchecked"
    assert judge(reading(SOT23_GSD["terminals"], title="TO-220"), MOSFET, "mosfet")["status"] != "agreed"
    assert judge(dict(SOT23_GSD, found=False), MOSFET, "mosfet")["status"] == "unchecked"
    assert judge(SOT23_GSD, MOSFET + [("4", "D")], "mosfet")["status"] == "disagreed"


def test_parse_takes_a_good_answer_and_refuses_a_bad_one():
    msg = lambda o: {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(o)}]}
    good = {"pinout_found": True, "package_title": "SOT23", "view": "top", "numbers_printed": False,
            "terminals": [{"number": "", "label": "D", "side": "top", "position": 1}], "connections": [],
            "notes": "", "embedded_instructions": []}
    assert PD.parse(msg(good))["terminals"][0]["label"] == "D"
    for bad in ({"terminals": "x"}, dict(good, terminals=[{"label": "D", "side": "middle", "position": 1}]),
                dict(good, terminals=[{"label": "D", "side": "top", "position": True}])):
        try:
            PD.parse(msg(bad))
            raise AssertionError(bad)
        except PD.Unavailable:
            pass
    try:
        PD.parse({"stop_reason": "refusal", "content": []})
        raise AssertionError("refusal")
    except PD.Unavailable:
        pass


def test_kind_of():
    assert PD.kind_of("MOSFET P-CH 20V 4A SOT23") == "mosfet"
    assert PD.kind_of("CRYSTAL 16.0000MHZ 18PF SMD") == "crystal"
    assert PD.kind_of("TRANS 2NPN 40V 0.2A SOT363") == "dual"
    assert PD.kind_of("DIODE SCHOTTKY 30V 200MA SOT23-3") == "diode"
    assert PD.kind_of("TVS DIODE 5V SOT23") == "other"
    assert PD.kind_of("XTAL OSC XO 25.0000MHZ CMOS SMD") == "other"
    assert PD.kind_of("IC MCU 32BIT 128KB FLASH 64LQFP") == "other"


def so8_words(names=SO8):
    """An SO-8 pinout drawing's text layer: numbers inside the outline, names outside, left side 1-4 top to bottom,
    right side 8-5 top to bottom."""
    w = []
    for i, (n, s) in enumerate(names[:4]):
        w += [(n, 100.0, 500.0 - 20 * i), (s, 60.0, 500.0 - 20 * i)]
    for i, (n, s) in enumerate(names[4:]):
        y = 500.0 - 20 * (3 - i)
        w += [(n, 160.0, y), (s, 200.0, y)]
    return w


def test_the_text_layer_places_each_name_by_its_own_number():
    mapping = dict(SO8)
    page = lambda data, p: so8_words()
    assert PD.placed_names(b"pdf", [1], mapping, page) is None
    swapped = dict(mapping, **{"2": mapping["3"], "3": mapping["2"]})      # both readers wrong alike
    assert "SCK" in PD.placed_names(b"pdf", [1], swapped, page)
    assert PD.placed_names(b"pdf", [1], mapping, lambda d, p: []) is not None      # no text layer: never placed
    few = lambda data, p: so8_words()[:6] + [(str(n), 0.0, 0.0) for n in range(4, 9)]
    assert PD.placed_names(b"pdf", [1], mapping, few) is not None                  # names missing: not placed
