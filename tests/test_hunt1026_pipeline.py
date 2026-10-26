"""Bug hunt 2026-10-26 (pipeline): synthetic model answers that are internally inconsistent must fail the read (read
again or held), never build. Each case starts from a correct read (tests.test_datasheet / test_debugsweep_dsread
fixtures, all SYNTHETIC) and changes one thing the way a model misreads a drawing.
python3 -m tests.run_all test_hunt1026_pipeline"""
import copy

from ecadlib import datasheet as D, ds_route as DR
from tests import test_datasheet as TD
from tests.test_debugsweep_dsread import _sot223, _tab_read


def _sot223_ext():
    return copy.deepcopy(_sot223("2.30")["sidecar"]["extraction"])


def _verdict(ext, part="AMS1117-3.3"):
    res = D.process(ext, part, "ds.pdf")
    return DR.assess(res), res["needs_review"]


def _held(ext, *words, part="AMS1117-3.3"):
    (kind, why), rev = _verdict(ext, part)
    assert kind != "ok", rev
    if words:
        assert any(all(w in r for w in words) for r in why), (words, why)


def test_the_fixtures_pass():
    assert _verdict(_sot223_ext())[0] == ("ok", [])
    assert _verdict(copy.deepcopy(TD.GOOD), "TESTPART")[0] == ("ok", [])
    assert _verdict(copy.deepcopy(TD.INCH), "TESTPART")[0] == ("ok", [])


# ---- pipeline-01: pin_positions given mirrored (offsets "+ up", or the bottom view): pin 1 at the far end of its row.
# The cross-check compares the pins along each side free of the frame (crosscheck: seen turned or from the other end
# agree), so both reads making this mistake built a mirrored footprint (SOT-23: pins 1 and 2 swapped).
def test_mirrored_pin_positions_are_held():
    e = _sot223_ext()
    for p in e["package"]["pin_positions"]:
        p["offset"] = -p["offset"]
    _held(e, "pin_positions", "mirrored")


def test_mirrored_sot23_with_a_pin_alone_on_the_right_is_held_and_the_true_one_passes():
    e = _sot223_ext()
    pos = [{"number": "1", "side": "L", "offset": -2.3}, {"number": "2", "side": "R", "offset": 0},
           {"number": "3", "side": "L", "offset": 2.3}]
    e["package"]["pin_positions"] = pos               # 1 and 3 on one side, 2 on the other (SC59 style): counter-clockwise
    e["package"]["tab"] = None
    e["package"]["pin_count"] = 3
    e["pins"], e["pin_table"]["rows"] = e["pins"][:3], 3
    rev = _verdict(copy.deepcopy(e))[1]
    assert not any("mirrored" in r for r in rev), rev
    e["package"]["pin_positions"] = [dict(p, offset=-p["offset"]) for p in pos]
    rev = _verdict(e)[1]
    assert any("mirrored" in r for r in rev), rev


def test_a_lead_row_on_the_right_numbered_up_the_side_passes():
    # the same SOT-223 turned half a turn: leads on R, pin 1 at the bottom (+), the tab on L
    e = _sot223_ext()
    for p in e["package"]["pin_positions"]:
        p["side"], p["offset"] = "R", -p["offset"]
    e["package"]["tab"]["side"] = "L"
    assert _verdict(e)[0] == ("ok", []), _verdict(e)


# ---- pipeline-02: pin_positions offsets that are not whole pitches apart (half the pitch, or offsets of another
# package) built: the offsets place the pads, the pitch is not used
def test_pin_positions_not_on_the_pitch_are_held():
    e = _sot223_ext()
    for p in e["package"]["pin_positions"]:
        p["offset"] = p["offset"] / 2                 # leads 1.15 apart, pitch 2.30
    _held(e, "pin_positions", "pitch")
    e = _sot223_ext()                                 # SOT-143: the wide pin 1 sits 1.7 mm from pin 2 at e = 1.9
    e["package"]["pin_positions"][0]["offset"] = -2.1
    assert not any("whole pitches" in r for r in _verdict(e)[1]), _verdict(e)


def test_pin_positions_off_centre_are_held():
    e = _sot223_ext()
    for p in e["package"]["pin_positions"]:
        p["offset"] = round(p["offset"] + 2.3, 4)     # measured from pin 1, not from the centre
    _held(e, "pin_positions")


# ---- pipeline-03: a tab wider than the body, or past its end, built
def test_a_tab_bigger_than_the_body_or_past_its_end_is_held():
    e = _sot223_ext()
    e["package"]["tab"]["width"] = {"min": 7.9, "nom": 8.0, "max": 8.1}
    _held(e, "tab")
    e = _sot223_ext()
    e["package"]["tab"]["offset"] = 3.0
    _held(e, "tab")


def test_the_dpak_and_d2pak_fixtures_still_pass():
    for name, leads, tab, n in (("TO-252-2", ["1", "3"], "2", 3), ("TO-252-3_TabPin4", ["1", "2", "3"], "4", 4)):
        res = _tab_read(name, "2.29", leads, tab, n)
        assert DR.assess(res) == ("ok", []), (name, res["needs_review"])


# ---- pipeline-04: a lead span hardly wider than the body (E read near E1) or a foot longer than the room outside the
# body built a land with its pads under the plastic
def test_a_foot_that_does_not_fit_outside_the_body_is_held():
    e = copy.deepcopy(TD.INCH)                        # SOIC-8: E1 0.150-0.157 in, L 0.016-0.050 in
    e["package"]["lead_span"].update(min=0.158, nom=0.16, max=0.162)
    _held(e, "terminal_length", "outside the body", part="TESTPART")
    e = _sot223_ext()
    e["package"]["lead_span"].update(min=3.9, nom=4.0, max=4.1)
    _held(e, "terminal_length", "outside the body")


# ---- pipeline-05: the pads the footprint gets and the symbol's pins differ (a pin_positions number repeated or not in
# the pin table, a tab number of its own the table does not have, a pad table renumbered): the read passed, the
# release check found it only after the part was built
def test_pads_and_pins_that_differ_fail_the_read():
    e = _sot223_ext()
    e["package"]["pin_positions"][1]["number"] = "1"          # pads 1, 1, 3 + tab 4: no pad 2
    _held(e, "symbol:", "2")
    e = _sot223_ext()
    e["package"]["pin_positions"][2]["number"] = "5"          # pads 1, 2, 5 + tab 4
    _held(e, "symbol:", "5")
    e = _sot223_ext()
    e["package"]["tab"]["number"] = "5"                       # tab 5, the pin table's 4
    _held(e, "symbol:", "5")
    e = _sot223_ext()
    e["package"]["tab"]["number"] = "1"                       # tab on pin 1: pin 4 has no pad
    _held(e, "symbol:", "4")
