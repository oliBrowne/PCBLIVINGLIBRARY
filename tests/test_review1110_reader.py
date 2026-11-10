"""Full review 2026-11-10, group 2 (reader holds): a datasheet read that is built wrong must be held, not handed to a
person as if it passed.
  * a read that contradicts its own package name by a wide margin was only a soft 'self-check:' line;
  * a lead span far past the foot, a lead wider than the body it sits on and a sliver of a body had no limit at all."""
import copy
from ecadlib import datasheet as D
from ecadlib import ds_route as DR
from tests import test_fix1026_rd as T


def held(t):
    return D.read_hold(T.read(copy.deepcopy(t)))


def test_the_true_reads_are_not_held():
    for t in (T.SOIC8, T.TSSOP14, T.SSOP28, T.MSOP8, T.SOD123, T.SOD323, T.WSON6, T.DFN8, T.VQFN16, T.R1206, T.R0402,
              T.TANT_B, T.SMA, T.SMB, T.SMC):
        assert held(t) is None, (t["name"], held(t))


def test_a_body_far_from_the_one_the_name_states_is_held():
    inch = dict(T.WSON6, body_length=T.sc(T.WSON6["body_length"], 25.4))      # 2.0 with the unit read as inches
    assert "too far apart" in (held(inch) or ""), held(inch)
    other_row = dict(T.WSON6, body_length=(3.9, 4.0, 4.1))                     # the 2 x 4 mm row of the same table
    assert "too far apart" in (held(other_row) or ""), held(other_row)
    # a small difference stays advice: a maker's rounded name (3.9 for a 3.91 mm body, 5 for 4.9) is not a wrong read
    near = dict(T.SOIC8, body_width=(3.9, 4.0, 4.1))
    assert held(near) is None, held(near)


def test_a_lead_span_far_past_the_foot_is_held():
    wide_row = dict(T.SOIC8, lead_span=(10.0, 10.3, 10.65))                    # the wide-body row: pads 2.2 mm too far out
    h = held(wide_row)
    assert h and "lead_span" in h and "implausib" in h, h


def test_a_lead_wider_than_the_body_it_sits_on_is_held():
    inch = dict(T.SOD123, terminal_width=T.sc(T.SOD123["terminal_width"], 25.4))   # 0.61 read as inches: 15.5 mm
    h = held(inch)
    assert h and "terminal_width" in h and "implausib" in h, h


def test_a_sliver_of_a_body_is_held():
    inch_as_mm = dict(T.R1206, body_width=T.sc(T.R1206["body_width"], 1 / 25.4))   # 0.063 "mm": pads 0.15 mm tall
    h = held(inch_as_mm)
    assert h and "body_width" in h and "implausib" in h, h


def test_the_reads_own_same_net_does_not_switch_off_the_overlap_check():
    from tests.test_datasheet import INCH
    e = copy.deepcopy(INCH)
    e["package"]["land"] = {"pad_length": 1.5, "pad_width": 2.0, "pad_center": 2.7, "pad_center_y": None}
    overlap = D.process(copy.deepcopy(e), "TESTPART", "https://example.com/t.pdf")
    assert "overlap" in (D.read_hold(overlap) or ""), D.read_hold(overlap)
    e["package"]["same_net"] = [[str(i) for i in range(1, 9)]]                 # eight different signals "on one net"
    res = D.process(copy.deepcopy(e), "TESTPART", "https://example.com/t.pdf")
    assert "overlap" in (D.read_hold(res) or ""), D.read_hold(res)
    assert not res["spec"].get("same_net") and any(r.startswith("same_net: ") for r in res["needs_review"])


def test_a_same_net_group_the_pin_table_bears_out_is_kept():
    pins = [{"num": "1", "name": "VIN"}, {"num": "2", "name": "vin"}, {"num": "3", "name": "GND"},
            {"num": "4", "name": "NC"}, {"num": "5", "name": "NC"}]
    kept, dropped = D.supported_nets(pins, [["1", "2", "TAB"], ["1", "3"], ["4", "5"], ["X", "Y"]])
    assert kept == [["1", "2", "TAB"]] and dropped == [["1", "3"], ["4", "5"], ["X", "Y"]]


def _process(e):
    return D.process(copy.deepcopy(e), "TESTPART", "https://example.com/t.pdf")


def test_what_the_read_could_not_read_is_not_built_from_a_default():
    from tests.test_datasheet import GOOD, INCH
    e = copy.deepcopy(GOOD)                                                    # a QFN whose thermal pad was not read
    e["package"]["exposed_pad"] = None
    e["package"]["unresolved"] = ["exposed_pad.length", "exposed_pad.width"]
    e["pins"] = e["pins"][:16]
    h = D.read_hold(_process(e))
    assert h and "unresolved: exposed_pad.length" in h, h
    e = copy.deepcopy(INCH)                                                    # a value the read calls a guess
    e["package"]["unresolved"] = ["lead_span (digits blurred; E could be 0.236 or 0.286)"]
    h = D.read_hold(_process(e))
    assert h and "lead_span" in h, h
    assert D.read_hold(_process(INCH)) is None and D.read_hold(_process(GOOD)) is None


def test_an_unresolved_entry_that_shapes_no_copper_is_not_a_hold():
    from tests.test_datasheet import INCH
    for u in ("height_max", "courtyard_excess", "marking code (not shown)", "description",
              "body_length min/max (not printed)", "terminal_width tolerance"):
        e = copy.deepcopy(INCH)
        e["package"]["unresolved"] = [u]
        assert D.read_hold(_process(e)) is None, (u, D.read_hold(_process(e)))
    # the symbol alone is wanted (the footprint comes from another source): the read's copper is not built at all
    e = copy.deepcopy(INCH)
    e["package"]["unresolved"] = ["lead_span (blurred)"]
    assert D.read_hold(_process(e), False) is None
