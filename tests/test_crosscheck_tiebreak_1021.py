"""Cross-check tie-breaks, 2026-10-21: a difference that changes nothing built is set aside, everything else still
holds, including differences that looked like wording but hid a wrongly built part (checked by a free re-score). The numbers are the first and second reads of held parts from the 2026-10-21 live benchmark (data:
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


# ---------------------------------------------------------------- held on purpose: released, they were built wrong
# The first version of these tie-breaks released LTC2942-1, PSMN5R2-60YL, OPA569DWP and IP4234CZ6; the free re-score
# of the 2026-10-21 run built all four wrong (axes swapped, mirrored, thermal land, pad size). The cross-check's
# disagreement was the only sign, so these differences hold.
def test_a_qfn_body_and_exposed_pad_that_both_turn_against_a_second_read_still_hold():
    s = dfn(2.0, 3.0, (1.35, 1.65), n=6, pitch=0.5)    # LTC2942-1
    assert read(s, nominal(body_length=3.0, body_width=2.0, ep=(1.65, 1.35), pitch=0.5, pin_count=6))["status"] == "disagreed"
    assert read(s, nominal(body_length=3.0, body_width=2.0, ep=(1.35, 1.65), pitch=0.5, pin_count=6))["status"] == "disagreed"


def test_a_tab_counted_as_a_pin_in_one_read_and_a_pad_in_the_other_still_holds():
    first = {"package_type": "gullwing", "name": "LFPAK56", "pin_count": 5, "pitch": 1.27,
             "body_length": {"min": 4.8, "max": 5.0}, "body_width": {"min": 3.8, "max": 4.1}, "lead_span": {"min": 5.8, "max": 6.2}}
    second = {"package_type": "gullwing", "name": "LFPAK56", "pin_count": 4, "pitch": 1.27,
              "body_length": {"min": 3.8, "max": 4.1}, "body_width": {"min": 4.8, "max": 5.0}, "lead_span": {"min": 5.8, "max": 6.2},
              "exposed_pad": {"length": {"max": 4.2}, "width": {"min": 3.1, "max": 3.3}}}
    pins = [("1", "S"), ("2", "S"), ("3", "S"), ("4", "G"), ("5", "D")]
    c = read(first, second, pins, pins)                 # PSMN5R2-60YL
    assert c["status"] == "disagreed" and {d["field"] for d in c["disagreed"]} == {"pin_count", "has_ep"}, c


def test_pin_descriptions_and_pad_descriptions_are_not_names():
    first = [("1", "I/O1"), ("2", "GND"), ("3", "I/O1"), ("4", "I/O2"), ("5", "VCC"), ("6", "I/O2")]
    second = [("1", "ESD protection I/O channel 1"), ("2", "ground"), ("3", "ESD protection I/O channel 1"),
              ("4", "ESD protection I/O channel 2"), ("5", "supply voltage"), ("6", "ESD protection I/O channel 2")]
    assert CC.compare_pins(first, second) != []                                        # IP4234CZ6
    assert not CC.names_agree("V\u2013", "PowerPAD") and not CC.names_agree("VSS", "exposed center pad")   # OPA569DWP


# ---------------------------------------------------------------- package type words
def test_a_package_type_word_is_set_aside_when_the_pads_are_built_as_listed():
    tc = {"package_type": "custom", "name": "Tag-Connect", "pin_count": 6, "pitch": 1.27}
    c = read(tc, nominal(package_type="connector", pitch=1.27, pin_count=6))          # TC2030
    assert c["status"] == "agreed" and "a word only" in c["tie_breaks"][0], c
    assert read(dict(tc, package_type="connector"), nominal(package_type="custom", pitch=1.27, pin_count=6))["status"] == "agreed"


def test_a_package_type_word_still_holds_when_the_type_picks_the_builder_or_nothing_else_agrees():
    eh = {"package_type": "pin_header", "name": "JST_EH", "pin_count": 13, "pitch": 2.5}       # S13B-EH: a header
    assert read(eh, nominal(package_type="connector", pitch=2.5, pin_count=13))["status"] == "disagreed"   # has no MP
    gw = {"package_type": "gullwing", "name": "SOIC-8", "pin_count": 8, "pitch": 1.27}
    assert read(gw, nominal(package_type="qfn", pitch=1.27, pin_count=8))["status"] == "disagreed"
    sot = {"package_type": "custom", "name": "SOT-143", "pin_count": 4, "pitch": 1.9,                 # BCV61
           "pin_positions": [{"number": n, "x": 0, "y": 0} for n in "1234"]}
    assert read(sot, nominal(package_type="gullwing", pitch=1.9, pin_count=4))["status"] == "disagreed"
    tc = {"package_type": "custom", "name": "Tag-Connect"}
    assert read(tc, nominal(package_type="connector"))["status"] != "agreed"           # nothing else compared
    off = {"package_type": "custom", "name": "Tag-Connect", "pin_count": 6, "pitch": 1.27}
    c = read(off, nominal(package_type="connector", pitch=2.54, pin_count=6))           # the word, and the pitch
    assert c["status"] == "disagreed" and {d["field"] for d in c["disagreed"]} == {"pitch"}, c


# ---------------------------------------------------------------- pin lists
def test_a_place_one_read_marks_missing_is_no_pin():
    nums = [n for n in range(1, 17) if n not in (2, 13)]
    first = [(str(n), f"P{n}") for n in nums]                                    # UCC256301's SOIC-14-16
    second = sorted(first + [("2", "Missing"), ("13", "Missing")], key=lambda p: int(p[0]))
    assert CC.compare_pins(first, second) == []
    assert CC.compare_pins(first, second[:-1] + [("16", "NC")]) != []                # a named pin still counts
    assert CC.compare_pins(sorted(first + [("2", "NC")], key=lambda p: int(p[0])), second) != []   # there vs missing
    assert CC.compare_pins(first, first + [("17", "MISO")]) != []                     # (not "missing")


def test_spelled_out_no_connect_names_agree():
    assert CC.names_agree("DNC", "Exposed Pad (DO NOT CONNECT)") and CC.names_agree("NC", "no connect")
    assert CC.names_agree("NC", "not connected") and not CC.names_agree("NC", "DNC")
    assert not CC.names_agree("TAB", "VIN")


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



# ---------------------------------------------------------------- leads that are one metal with the pad
from ecadlib import datasheet as D


def power8(t="gullwing", **kw):
    s = {"package_type": t, "name": "PQFN-8", "pin_count": 8, "pitch": 1.27, "exposed_pad": {"length": 4.0, "width": 3.0}}
    s.update(kw)
    return s


def test_leads_joined_to_the_exposed_pad_hold_instead_of_building_separate_pads():
    pins = [{"num": str(i), "name": n} for i, n in enumerate("SSSGDDDD", 1)] + [{"num": "9", "name": "D"}]
    line = D.joined_to_pad(power8(), pins)                  # FDMT800100DC / BSC026N08NS5: drain 5-8 and pad 9
    assert line and line.startswith("no footprint: leads 5-8 and the exposed pad are all D"), line
    assert D.joined_to_pad(power8("qfn", pins_top_bottom=0), pins)


def test_a_pad_that_only_shares_a_net_with_some_leads_or_is_ground_still_builds():
    some = [{"num": str(i), "name": n} for i, n in enumerate("SSSGDDOD", 1)] + [{"num": "9", "name": "D"}]
    assert D.joined_to_pad(power8(), some) is None                        # pin 7 is not D
    gnd = [{"num": str(i), "name": n} for i, n in enumerate(["IN", "EN", "FB", "PG", "GND", "GND", "GND", "GND"], 1)]
    assert D.joined_to_pad(power8(), gnd + [{"num": "9", "name": "GND"}]) is None
    plain = [{"num": str(i), "name": n} for i, n in enumerate("SSSGDDDD", 1)]
    assert D.joined_to_pad(power8(), plain) is None                       # no pad pin listed: no sign
    assert D.joined_to_pad(power8(exposed_pad=None), plain + [{"num": "9", "name": "D"}]) is None
    assert D.joined_to_pad(power8("qfn", pins_top_bottom=2), plain + [{"num": "9", "name": "D"}]) is None   # 4 sides
