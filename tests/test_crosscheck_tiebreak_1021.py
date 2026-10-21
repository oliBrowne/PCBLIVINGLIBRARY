"""Cross-check tie-breaks, 2026-10-21: a difference that changes nothing built is set aside, everything else still
holds. The numbers are the first and second reads of held parts from the 2026-10-21 live benchmark (data:
/mnt/project-files/benchmark/oct21/held/); every rule also has cases built to sneak a wrong part through, which must
stay held.   python3 -m tests.run_all test_crosscheck_tiebreak_1021"""
from ecadlib import crosscheck as CC, footprint_gen as F


def r(mn, nom, mx):
    return {"min": mn, "nom": nom, "max": mx}


def dfn(L, W, ep, T=(0.3, 0.4, 0.5), n=12, pitch=0.45, **kw):
    s = {"package_type": "qfn", "name": "DFN", "pin_count": n, "pitch": pitch, "body_length": r(L - 0.1, L, L + 0.1),
         "body_width": r(W - 0.1, W, W + 0.1), "terminal_length": r(*T), "terminal_width": r(0.18, 0.23, 0.28),
         "exposed_pad": {"length": r(ep[0] - 0.1, ep[0], ep[0] + 0.1), "width": r(ep[1] - 0.1, ep[1], ep[1] + 0.1)},
         "pins_left_right": n // 2, "pins_top_bottom": 0}
    s.update(kw)
    return s


def nominal(**dims):
    """A second read's numbers: nominals only, as printed."""
    out = {"package_type": dims.pop("package_type", "qfn"), "name": "x", "pin_count": dims.pop("pin_count", None)}
    ep = dims.pop("ep", None)
    if ep:
        out["exposed_pad"] = {"length": {"nom": ep[0]}, "width": {"nom": ep[1]}}
    out.update({k: ({"nom": v} if k != "pitch" else v) for k, v in dims.items()})
    return out


def table(first_spec, name):
    return CC.against_table(CC.critical(first_spec), name, "BOM package")


def read(first_spec, second_spec, first_pins=None, pins=None):
    return CC.against_read(CC.critical(first_spec), CC.critical(second_spec), first_pins, pins)


# ---------------------------------------------------------------- exposed pad the way it is built
def test_a_dfn_exposed_pad_that_cannot_sit_between_the_rows_is_turned_and_then_agrees_with_the_table():
    s = dfn(3.0, 3.0, (2.38, 1.65))                    # LT3652IDD: 2.38 + 2 x 0.4 > 3.0
    turned, notes = F.orient_ep(s)
    assert F.dim(turned["exposed_pad"]["length"])[1] == 1.65 and notes and "clear the terminals" in notes[0], notes
    assert table(s, "DFN-12-1EP_3x3mm_P0.45mm_EP1.65x2.38mm")["status"] == "agreed"
    assert "clear the terminals" in F.build(s)[1]["notes"][0]          # the build lays it the same way


def test_a_dfn_exposed_pad_that_fits_either_way_or_only_just_is_not_turned():
    assert F.orient_ep(dfn(3.0, 3.0, (1.8, 1.5), T=(0.3, 0.35, 0.4)))[1] == []          # both fit: as read
    # AT24CS01-MAHM: 1.5 across 2.0 with 0.35 terminals does not fit, 1.3 turned leaves no MIN_EP_CLEAR: as read, held
    s = dfn(2.0, 3.0, (1.5, 1.3), T=(0.3, 0.35, 0.4), n=8, pitch=0.5)
    assert F.orient_ep(s)[1] == []
    assert table(s, "DFN-8-1EP_3x2mm_P0.5mm_EP1.3x1.5mm")["status"] == "disagreed"


def test_a_gullwing_exposed_pad_in_a_square_body_still_holds_when_only_the_pad_is_swapped():
    s = {"package_type": "gullwing", "name": "MSOP-8-1EP", "pin_count": 8, "pitch": 0.65, "body_length": r(2.9, 3.0, 3.1),
         "body_width": r(2.9, 3.0, 3.1), "lead_span": r(4.7, 4.9, 5.1), "terminal_length": r(0.4, 0.6, 0.8),
         "terminal_width": r(0.22, 0.3, 0.38), "exposed_pad": {"length": r(1.6, 1.8, 2.0), "width": r(1.3, 1.5, 1.7)}}
    c = table(s, "MSOP-8-1EP_3x3mm_P0.65mm_EP1.5x1.8mm")      # AL8860MP: nothing in the read says which way
    assert c["status"] == "disagreed" and "would not turn" in c["disagreed"][0]["note"], c


def test_a_gullwing_exposed_pad_long_side_first_is_compared_as_the_build_turns_it():
    s = {"package_type": "gullwing", "name": "MSOP-16-1EP", "pin_count": 16, "pitch": 0.5, "body_length": r(3.937, 4.039, 4.141),
         "body_width": r(2.898, 3.0, 3.102), "lead_span": r(4.748, 4.9, 5.052), "terminal_length": r(0.378, 0.53, 0.682),
         "terminal_width": {"min": 0.17, "max": 0.27},
         "exposed_pad": {"length": r(2.743, 2.845, 2.947), "width": r(1.549, 1.651, 1.753)}}
    assert table(s, "MSOP-16-1EP_3x4.039mm_P0.5mm_EP1.651x2.845mm")["status"] == "agreed"      # LT8610


def test_orient_ep_never_raises_on_one_sided_numbers():
    s = nominal(package_type="gullwing", body_length=2.6, ep=(2.0, 1.9))
    s["exposed_pad"] = {"length": {"min": 1.9, "max": 2.35}, "width": {"max": 2.45}}
    assert F.orient_ep(s)[0] is s


# ---------------------------------------------------------------- a whole package written the other way
def test_a_body_and_exposed_pad_that_both_turn_agree_with_a_second_read():
    s = dfn(2.0, 3.0, (1.35, 1.65), n=6, pitch=0.5)    # LTC2942-1
    assert read(s, nominal(body_length=3.0, body_width=2.0, ep=(1.65, 1.35), pitch=0.5, pin_count=6))["status"] == "agreed"


def test_a_body_that_turns_while_its_exposed_pad_does_not_still_holds():
    s = dfn(2.0, 3.0, (1.35, 1.65), n=6, pitch=0.5)
    c = read(s, nominal(body_length=3.0, body_width=2.0, ep=(1.35, 1.65), pitch=0.5, pin_count=6))
    assert c["status"] == "disagreed", c


# ---------------------------------------------------------------- package type words
def test_a_package_type_word_is_set_aside_when_the_pads_are_built_as_listed():
    tc = {"package_type": "custom", "name": "Tag-Connect", "pin_count": 6, "pitch": 1.27}
    c = read(tc, nominal(package_type="connector", pitch=1.27, pin_count=6))          # TC2030
    assert c["status"] == "agreed" and "a word only" in c["tie_breaks"][0], c
    sot = {"package_type": "custom", "name": "SOT-143", "pin_count": 4, "pitch": 1.9,
           "pin_positions": [{"number": n, "x": 0, "y": 0} for n in "1234"]}
    assert read(sot, nominal(package_type="gullwing", pitch=1.9, pin_count=4))["status"] == "agreed"   # BCV61's pads


def test_a_package_type_word_still_holds_when_the_type_picks_the_builder_or_nothing_else_agrees():
    eh = {"package_type": "pin_header", "name": "JST_EH", "pin_count": 13, "pitch": 2.5}       # S13B-EH: a header
    assert read(eh, nominal(package_type="connector", pitch=2.5, pin_count=13))["status"] == "disagreed"   # has no MP
    gw = {"package_type": "gullwing", "name": "SOIC-8", "pin_count": 8, "pitch": 1.27}
    assert read(gw, nominal(package_type="qfn", pitch=1.27, pin_count=8))["status"] == "disagreed"
    bare = {"package_type": "custom", "name": "SOT-143", "pin_count": 4, "pitch": 1.9}              # no pads listed
    assert read(bare, nominal(package_type="gullwing", pitch=1.9, pin_count=4))["status"] == "disagreed"
    tc = {"package_type": "custom", "name": "Tag-Connect"}
    assert read(tc, nominal(package_type="connector"))["status"] != "agreed"           # nothing else compared
    off = {"package_type": "custom", "name": "Tag-Connect", "pin_count": 6, "pitch": 1.27}
    c = read(off, nominal(package_type="connector", pitch=2.54, pin_count=6))           # the word, and the pitch
    assert c["status"] == "disagreed" and {d["field"] for d in c["disagreed"]} == {"pitch"}, c


# ---------------------------------------------------------------- a tab counted as a pin or as a pad
LFPAK = {"package_type": "gullwing", "name": "LFPAK33", "pin_count": 5, "pitch": 0.65, "body_length": {"min": 3.2, "max": 3.4},
         "body_width": {"nom": 2.6}, "lead_span": {"min": 3.2, "max": 3.4}}
SSSGD = [("1", "S"), ("2", "S"), ("3", "S"), ("4", "G"), ("5", "D")]


def lfpak_second(**kw):
    s = {"package_type": "gullwing", "name": "LFPAK33", "pin_count": 4, "pitch": 0.65, "body_length": {"nom": 2.6},
         "body_width": {"min": 3.2, "max": 3.4}, "lead_span": {"min": 3.2, "max": 3.4},
         "exposed_pad": {"length": {"min": 1.9, "max": 2.35}, "width": {"min": 2.0, "max": 2.45}}}
    s.update(kw)
    return s


def test_a_tab_one_read_numbers_and_the_other_calls_an_exposed_pad_is_the_same_pin():
    c = read(LFPAK, lfpak_second(), SSSGD, SSSGD)                                    # BUK7M10-40EX
    assert c["status"] == "agreed" and "counted two ways" in c["tie_breaks"][0], c


def test_a_tab_count_still_holds_when_the_pin_lists_differ_or_the_count_is_off_by_more():
    c = read(LFPAK, lfpak_second(), SSSGD, SSSGD[:4])                     # the second read has no pin 5
    assert c["status"] == "disagreed", c
    c = read(LFPAK, lfpak_second(), SSSGD, SSSGD[:4] + [("5", "S")])     # pin 5 named differently
    assert c["status"] == "disagreed", c
    c = read(dict(LFPAK, pin_count=6), lfpak_second(), SSSGD + [("6", "D")], SSSGD + [("6", "D")])
    assert c["status"] == "disagreed", c                                     # 6 vs 4 + pad
    c = read(LFPAK, lfpak_second(exposed_pad=None), SSSGD, SSSGD)            # 5 vs 4, no pad anywhere
    assert c["status"] == "disagreed", c


# ---------------------------------------------------------------- pin lists
def test_a_place_one_read_marks_missing_is_no_pin():
    nums = [n for n in range(1, 17) if n not in (2, 13)]
    first = [(str(n), f"P{n}") for n in nums]                                    # UCC256301's SOIC-14-16
    second = sorted(first + [("2", "Missing"), ("13", "Missing")], key=lambda p: int(p[0]))
    assert CC.compare_pins(first, second) == []
    assert CC.compare_pins(first, second[:-1] + [("16", "NC")]) != []                # a named pin still counts
    assert CC.compare_pins(sorted(first + [("2", "NC")], key=lambda p: int(p[0])), second) != []   # there vs missing
    assert CC.compare_pins(first, first + [("17", "MISO")]) != []                     # (not "missing")


def test_pin_descriptions_agree_with_names_only_when_they_pin_one_pin_down():
    first = [("1", "I/O1"), ("2", "GND"), ("3", "I/O1"), ("4", "I/O2"), ("5", "VCC"), ("6", "I/O2")]
    second = [("1", "ESD protection I/O channel 1"), ("2", "ground"), ("3", "ESD protection I/O channel 1"),
              ("4", "ESD protection I/O channel 2"), ("5", "supply voltage"), ("6", "ESD protection I/O channel 2")]
    assert CC.compare_pins(first, second) == []                                        # IP4234CZ6
    swapped = second[:3] + [("4", "ESD protection I/O channel 1")] + second[4:]
    assert CC.compare_pins(first, swapped) != []
    two = [("1", "VCC"), ("2", "VDDIO"), ("3", "GND")]
    assert CC.compare_pins(two, [("1", "supply voltage"), ("2", "I/O supply voltage"), ("3", "ground")]) != []
    assert not CC.names_agree("VCC", "supply voltage")                               # alone, a word is not a name
    assert not CC.names_agree("I/O1", "ESD protection I/O channel 12")


def test_exposed_pad_names_and_described_pads_agree_with_the_pad_net_only():
    assert CC.names_agree("V–", "PowerPAD") and CC.names_agree("VEE", "thermal pad")        # OPA569DWP
    assert CC.names_agree("VSS", "exposed center pad")                                           # PCA9557BS
    assert CC.names_agree("DNC", "Exposed Pad (DO NOT CONNECT)") and CC.names_agree("NC", "no connect")   # LTC2942-1
    assert not CC.names_agree("V+", "PowerPAD") and not CC.names_agree("VIN", "exposed pad")
    assert not CC.names_agree("TAB", "VIN") and not CC.names_agree("NC", "DNC")


def test_a_minus_sign_from_pdf_text_is_a_minus():
    assert CC.names_agree("SENSE-", "SENSE−") and CC.names_agree("SENSE-", "SENSE–")
    assert not CC.names_agree("SENSE+", "SENSE−")


# ---------------------------------------------------------------- said where a person looks
def test_a_tie_break_is_said_in_the_summary_and_the_review_report():
    tc = {"package_type": "custom", "name": "Tag-Connect", "pin_count": 6, "pitch": 1.27}
    c = dict(read(tc, nominal(package_type="connector", pitch=1.27, pin_count=6)), source="second read",
             against="second read (m)")
    rec = {"mode": "auto", "status": "agreed", "checks": [c]}
    assert "set aside: package type read custom" in CC.summary_line(rec)
    assert any("set aside:" in line for line in CC.report_md(rec))

