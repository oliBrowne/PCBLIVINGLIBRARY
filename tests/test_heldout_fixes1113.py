"""Fixes from the held-out test of 2026-11-12 (40 parts the benchmark never read): page picks, DIP pads the datasheet
leaves blank, a connector body read by size only. The page texts mirror the real PDFs' text layers."""
from ecadlib import ds_route as DR

FILL = "Electrical characteristics. Supply current 10 uA typical over the full temperature range of the device."


def _pdf(pages, n=None):
    out = [FILL] * (n or len(pages))
    for i, t in pages.items():
        out[i - 1] = t
    return out


# ---------------------------------------------------------------- package names
def test_ipc_land_name_gives_pin_count_not_pitch():
    assert DR.package_tokens("Reference pad layout (reference IPC SOIC127P1030X265-20M)") == {"SO20"}
    assert DR.package_tokens("SOT95P280X145-5N") == set()          # an outline's IPC name: no outline number


def test_soicw_with_pin_count():
    assert DR.package_tokens("LW Package, 20-Pin SOICW") == {"SO20"}


def test_outline_counts():
    assert DR.outline_counts("SOT-23-3") == {"SOT23": {3}}
    assert DR.outline_counts("TSOT-23-5 Surface Mount Package") == {"SOT23": {5}}
    assert DR.outline_counts("5-Lead SOT-23") == {"SOT23": {5}}
    assert DR.outline_counts("SOT-23 package") == {}


# ---------------------------------------------------------------- RT9742SNGV: SOT-23-3 drawing behind TSOT-23-5 ones
RT_TSOT = ("RT9742 17 DS9742-10 January 2020 www.richtek.com Outline Dimension TSOT-23-5 Surface Mount Package "
           "Dimensions In Millimeters Dimensions In Inches Symbol Min Max Min Max A 0.700 1.000 0.028 0.039 A1 0.000 "
           "0.100 B 1.397 1.803 b 0.300 0.559 D 2.591 3.000")
RT_TSOT_FC = ("RT9742 18 Outline Dimension TSOT-23-5 (FC) Surface Mount Package Dimensions In Millimeters Symbol Min "
              "Max Min Max A 0.700 1.000 A1 0.000 0.100 D 2.591 3.000")
RT_SOT3 = ("Richtek Technology Corporation reserves the right. No license is granted by implication or otherwise under "
           "any patent or patent rights of Richtek or its subsidiaries. A b A1 B D C e H L SOT-23-3 Surface Mount "
           "Package Dimensions In Millimeters Dimensions In Inches Symbol Min Max Min Max A 0.889 1.295 0.035 0.051 "
           "A1 0.000 0.152 B 1.397 1.803 D 2.591 3.048")


def test_outline_lead_count_picks_the_right_drawing():
    texts = _pdf({1: "RT9742 power switch. SOT-23-3 and TSOT-23-5 packages. Pin Configurations (TOP VIEW)",
                  17: RT_TSOT, 18: RT_TSOT_FC, 19: RT_SOT3}, 19)
    rows = DR.page_scores(texts, "RT9742SNGV", "SOT-23-3")[0]
    assert rows[16]["other"] and not rows[16]["own"]                 # TSOT-23-5: another package
    assert rows[18]["own"] and rows[18]["solo"]
    got, why = DR.pick_pages(texts, 6, "RT9742SNGV", "SOT-23-3")
    assert 19 in got and "drawing" in why[19]


def test_outline_without_count_unchanged():
    texts = _pdf({17: RT_TSOT, 19: RT_SOT3}, 19)
    rows = DR.page_scores(texts, "RT9742SNGV", "SOT-23")[0]            # no count asked: both stay this outline
    assert rows[16]["own"] and rows[18]["own"]


# ---------------------------------------------------------------- A2982: drawing named by its IPC land name
def test_soicw_drawing_is_own():
    p7 = ("8-Channel Source Driver A2982 7 Allegro MicroSystems LW Package, 20-Pin SOICW 21 20 A 2.65 MAX C SEATING "
          "PLANE C0.10 20X A Terminal #1 mark area GAUGE PLANE B 2.25 0.65 9.50 1.27 PCB Layout Reference View "
          "Reference pad layout (reference IPC SOIC127P1030X265-20M)")
    texts = _pdf({1: "A2982 8-channel source driver, 20-pin SOICW", 7: p7}, 8)
    rows = DR.page_scores(texts, "A2982SLWTR-T", "SOIC-20W_7.5x12.8mm_P1.27mm")[0]
    assert rows[6]["own"]
    assert 7 in DR.pick_pages(texts, 6, "A2982SLWTR-T", "SOIC-20W_7.5x12.8mm_P1.27mm")[0]


# ---------------------------------------------------------------- MSP430F5333IZQW: the ball-name table
SIG = ("7.2 Signal Descriptions Table 7-1 describes the signals for all device variants and packages. Table 7-1. "
       "Signal Descriptions TERMINAL I/O(1) DESCRIPTION NAME NO.(4) PZ ZCA, ZQW P6.4/CB4/A4 1 A1 I/O "
       "General-purpose digital I/O")
SIG_C = ("Table 7-1. Signal Descriptions (continued) TERMINAL I/O(1) DESCRIPTION NAME NO.(4) PZ ZCA, ZQW P1.0/TA0CLK "
         "21 J2 I/O General-purpose digital I/O")
IO = ("9.13.5 Port P5 (P5.0 and P5.1) Input/Output With Schmitt Trigger Figure 9-6 shows the pin diagram. Table "
      "9-53 summarizes how to select the pin function. P5.0/VREF+/VeREF+ Table 9-53. Port P5 (P5.0 and P5.1) Pin "
      "Functions PIN NAME (P5.x) x FUNCTION CONTROL BITS")


def test_mpn_tail_code():
    texts = _pdf({11: SIG}, 20)
    assert DR.mpn_tail_codes(texts, "MSP430F5333IZQW") == {"ZQW"}
    assert DR.mpn_tail_codes(_pdf({}, 20), "MSP430F5333IZQW") == set()     # printed nowhere
    assert DR.mpn_tail_codes(texts, "NE555P") == set()                    # too short to be a word on its own


def test_signal_description_table_is_the_pin_table():
    pages = {11: SIG, 12: SIG_C, 13: SIG_C, 14: SIG_C, 88: IO, 89: IO, 93: IO}
    texts = _pdf(pages, 121)
    rows = DR.page_scores(texts, "MSP430F5333IZQW", "BGA-113")[0]
    assert rows[10]["own"] and rows[10]["pinout"] > 0
    got, why = DR.pick_pages(texts, 18, "MSP430F5333IZQW", "BGA-113")
    assert {11, 12, 13, 14} <= set(got)
    assert "pinout" in why[11]


# ---------------------------------------------------------------- LTV-814 / 23AA02M-IP: DIP pads left blank
from ecadlib import datasheet as D, footprint_adv as FA


def blank_dip(n=4, unit="mm", **kw):
    f = FA.UNIT[unit]
    s = {"package_type": "custom", "name": f"DIP-{n}", "pin_count": n, "pitch": 2.54,
         "unresolved": ["drill (hole size not given on the drawing)", "land pattern (pad size not given)"],
         "pad_table": {"unit": unit, "y_axis": "down", "origin": "pin1", "default_pad": {"type": "tht"},
                       "arrays": [{"count": n // 2, "x": 0, "y": 0, "dx": 0, "dy": 2.54 / f, "first_number": 1},
                                  {"count": n // 2, "x": 7.62 / f, "y": (n // 2 - 1) * 2.54 / f, "dx": 0,
                                   "dy": -2.54 / f, "first_number": n // 2 + 1}],
                       "body": {"x1": 0.635 / f, "y1": -0.9 / f, "x2": 6.985 / f, "y2": ((n // 2 - 1) * 2.54 + 0.9) / f}}}
    s.update(kw)
    return s


def test_blank_dip_pads_get_kicads():
    for s in (blank_dip(4), blank_dip(8, unit="in")):
        out = D.standard_dip_holes(s)
        assert out, s["name"]
        pads = FA.table_layout(out)[0]
        assert {(p["w"], p["h"], tuple(p["drill"])) for p in pads} == {(1.6, 1.6, (0.8, 0.8))}
        assert DR.unread_copper(s) == []                               # nothing held for the blank land
        assert "default_pad" in s["pad_table"] and "d" not in s["pad_table"]["default_pad"]   # read unchanged


def test_blank_pads_that_are_no_plain_dip_still_hold():
    s = blank_dip(4)
    s["pad_table"]["arrays"][1]["x"] = 12.0                           # rows 12 mm apart: no DIP
    assert D.standard_dip_holes(s) is None
    assert any("land pattern" in w for w in DR.unread_copper(s))
    smd = blank_dip(4)
    smd["pad_table"]["default_pad"] = {"type": "smd"}
    assert D.standard_dip_holes(smd) is None and DR.unread_copper(smd)


# ---------------------------------------------------------------- B20B-PUDSS: housing size read, position not
def pud(**tab):
    t = {"unit": "mm", "y_axis": "up", "origin": "pin1", "default_pad": {"type": "tht", "d": 1.35, "drill": 0.9},
         "arrays": [{"count": 10, "x": 0, "y": 0, "dx": -2.0, "dy": 0, "first_number": 1, "number_step": 2},
                    {"count": 10, "x": 0, "y": 2.0, "dx": -2.0, "dy": 0, "first_number": 2, "number_step": 2}]}
    t.update(tab)
    return {"package_type": "connector", "name": "B20B-PUDSS", "pin_count": 20, "pitch": 2.0,
            "body_length": 22.0, "body_width": 8.3, "pad_table": t}


def test_connector_body_size_without_position_is_held():
    why = D.body_unread(pud())
    assert why and why.startswith("no footprint:") and "where the body sits" in why


def test_connector_body_with_position_builds():
    assert D.body_unread(pud(body={"x1": -20, "y1": -3.9, "x2": 2, "y2": 4.4})) is None
    assert D.body_unread(pud(center_at={"x": -9.0, "y": 0.25})) is None
    assert D.body_unread(pud(origin="center")) is None                # the drawing's 0,0 is the package centre
    assert D.body_unread(dict(pud(), package_type="custom")) is None  # a DIP body sits between its rows


# ---------------------------------------------------------------- review cases (each built wrong or mis-picked before)
def test_dip_fill_keeps_holds_on_where_the_pins_are():
    for note in ("pad_table pin positions guessed", "pad_table row spacing assumed 10.16 (could be 7.62)",
                 "land pattern row spacing not printed"):
        s = blank_dip(8)
        s["unresolved"] = s["unresolved"] + [note]
        assert any(note in w for w in DR.unread_copper(s)), note


def test_typeless_blank_pads_are_never_taken_for_a_tht_dip():
    s = blank_dip(8)
    s["pad_table"]["default_pad"] = {}                                # an SMD gull-wing DIP read without types
    assert D.standard_dip_holes(s) is None
    assert any("land pattern" in w for w in DR.unread_copper(s))


def test_ipc_no_lead_name_counts_without_its_thermal_pad():
    assert DR.package_tokens("QFN50P500X500X100-33N") == {"QFN32"}
    assert DR.package_tokens("SON50P300X300X80-11N") == {"SON10"}


def test_mpn_tail_never_a_common_word_or_family():
    texts = _pdf({3: "ADJ version: VOUT set by R1 and R2. QFN package."}, 6)
    assert DR.mpn_tail_codes(texts, "AMS1117-ADJ") == set()
    assert DR.mpn_tail_codes(texts, "XYZ123QFN") == set()


def test_sot_lead_counts_one_apart_are_other_packages():
    texts = _pdf({5: "Package Outline Dimension SOT-23-6 Symbol Min Max A 0.9 1.45 A1 0 0.15 mm",
                  6: "Package Outline Dimension SOT-23-5 Symbol Min Max A 0.9 1.45 A1 0 0.15 mm"}, 8)
    rows = DR.page_scores(texts, "XC6206P332MR", "SOT-23-5")[0]
    assert rows[4]["other"] and rows[5]["own"]


def test_terminal_name_no_header_is_case_sensitive():
    assert not DR.TABLE["pinout"].search("Terminal block series. Part name: XYZ. No. of positions 4")
    assert DR.TABLE["pinout"].search("TERMINAL I/O(1) DESCRIPTION NAME NO.(4) PZ")


def test_connector_with_no_origin_is_held():
    s = pud()
    s["pad_table"].pop("origin")
    assert D.body_unread(s)


# ---------------------------------------------------------------- second held-out round (branch measured on the PC)
def test_smd_header_framed_by_mount_pads_is_not_held():
    s = {"package_type": "connector", "name": "SM04B-SRSS-TB", "pin_count": 4, "pitch": 1.0,
         "body_length": 6.0, "body_width": 4.25,
         "pad_table": {"unit": "mm", "y_axis": "down", "origin": "datum", "default_pad": {"type": "smd", "w": 0.6, "h": 1.55},
                       "arrays": [{"count": 4, "x": -1.5, "y": -2.0, "dx": 1.0, "dy": 0, "first_number": 1}],
                       "pads": [{"number": "MP", "x": -2.8, "y": 0.9, "w": 1.2, "h": 1.8},
                                {"number": "MP", "x": 2.8, "y": 0.9, "w": 1.2, "h": 1.8}]}}
    assert D.body_unread(s) is None
    one = dict(s, pad_table=dict(s["pad_table"], pads=s["pad_table"]["pads"][:1]))
    assert D.body_unread(one)                                         # one tab frames nothing


def test_courtyard_body_covers_the_read_maximum():
    s = blank_dip(8)
    s = D.standard_dip_holes(s)
    s["body_length"] = {"min": 8.84, "nom": 9.27, "max": 10.16}       # PDIP-8 D: .348 / .365 / .400 in
    s["body_width"] = {"min": 6.1, "nom": 6.35, "max": 7.11}
    s["pad_table"]["body"] = {"x1": 0.635, "y1": -0.825, "x2": 6.985, "y2": 8.445}   # the nominal 6.35 x 9.27 box
    x1, y1, x2, y2 = FA.table_layout(s)[1]
    assert round(y2 - y1, 2) == 10.16 and round(x2 - x1, 2) == 7.11
    s["pad_table"]["body"] = {"x1": 0.0, "y1": -2.0, "x2": 7.62, "y2": 9.62}         # another size: as read
    x1, y1, x2, y2 = FA.table_layout(s)[1]
    assert round(y2 - y1, 2) == 11.62 and round(x2 - x1, 2) == 7.62


def test_balls_left_unnamed_after_the_last_read_are_held():
    why = ["symbol: pins missing ['B9', 'B10'] (balls of the 12 x 12 grid with no symbol pin)"]
    res = {"spec": {"package_type": "bga"}, "needs_review": why}
    assert DR.hold_reasons(res) == why
    assert DR.hold_reasons(res, fp_needed=False) == why                # the symbol's own check


def test_mpn_tail_only_when_the_part_number_is_printed_nowhere():
    texts = _pdf({22: "PACKAGE OPTION ADDENDUM Orderable Device OPA569DWP ACTIVE SOIC DWP 20", 28: "DWP drawing"}, 30)
    assert DR.mpn_tail_codes(texts, "OPA569DWP") == set()
    texts = _pdf({22: "PACKAGE OPTION ADDENDUM Orderable Device OPA569AIDWP ACTIVE SOIC DWP 20", 28: "DWP"}, 30)
    assert DR.mpn_tail_codes(texts, "OPA569DWP") == set()             # printed with its option letters only
    assert DR.mpn_tail_codes(_pdf({11: SIG}, 20), "MSP430F5333IZQW") == {"ZQW"}
