"""Compartmental testing 2026-11-12, datasheet post-processing: findings 20-23 were reads that reached the reviewer
looking fine ('ok', nothing held) and built a wrong land:
  20. a lead width or a chip's width read half or twice under a standard package name;
  21. a QFN named -1EP whose pin table lists the pad's pin, with no exposed pad read (built with no thermal pad);
  22. a pitch that is not the one the read's own name states;
  23. a 1206's body under the names C_0603, R_0603, 0603 or CAPC1608X90N (only ..._1608Metric was read as a size).
Each new hold was run against the 88 saved real reads of the 2026-10-25 benchmark (none newly held) before it went in.
Offline: no key, no network, no model.
    python3 -m tests.run_all test_reader_checks1112"""
import copy

from ecadlib import datasheet as D, ds_route as DR, stdparts as SP
from tests import test_fix1026_rd as T
from tests.test_datasheet import GOOD, dim


def held(t, **kw):
    return D.read_hold(T.read(copy.deepcopy(t), **kw))


def good(**pkg):
    e = copy.deepcopy(GOOD)
    e["package"].update(pkg)
    return D.process(e, "TESTPART", "ds.pdf")


TRUTHS = (T.R1206, T.R0402, T.TANT_B, T.SMA, T.SMB, T.SMC, T.MELF, T.SOIC8, T.TSSOP14, T.SSOP28, T.MSOP8, T.SOT89,
          T.SOT23, T.SOT223, T.DPAK, T.SOD123, T.SOD323, T.WSON6, T.DFN8, T.VQFN16)


def test_true_reads_are_not_held():
    for t in TRUTHS:
        assert held(t) is None, (t["name"], held(t))
    assert D.read_hold(good()) is None


# ---------------------------------------------------------------- 20: a lead width read half or twice
def test_every_regular_row_package_of_the_standard_table_keeps_the_lead_width_rule():
    """The rule's evidence: no gull-wing, QFN or QFP of the checked table is outside it (a false hold on a true
    read is a part a person has to rescue by hand)."""
    n = 0
    for name, s in SP.packages().items():
        if s.get("package_type") in ("gullwing", "qfn", "qfp"):
            n += 1
            assert D._lead_vs_pitch(s) == [], (name, D._lead_vs_pitch(s))
    assert n > 400, n


def test_a_lead_width_read_half_is_held():
    for t in (T.SOIC8, T.TSSOP14, T.SSOP28, T.MSOP8, T.DFN8, T.VQFN16):
        h = held(dict(t, terminal_width=T.sc(t["terminal_width"], 0.5)))
        assert h and "terminal_width" in h, (t["name"], h)
    h = D.read_hold(good(terminal_width=dim("b", 0.09, 0.125, 0.15)))
    assert h and "terminal_width" in h and "implausible" in h, h


def test_a_lead_width_read_twice_is_held():
    for t in (T.SOIC8, T.TSSOP14, T.SSOP28, T.MSOP8, T.DFN8, T.VQFN16):
        h = held(dict(t, terminal_width=T.sc(t["terminal_width"], 2)))
        assert h and "terminal_width" in h, (t["name"], h)


def test_the_lead_width_rule_leaves_wide_pitches_listed_layouts_and_j_leads_alone():
    opto = dict(package_type="gullwing", pitch=2.54, pin_count=4, terminal_width=T._dim((0.3, 0.4, 0.5)), name="SOP-4")
    assert D._lead_vs_pitch(opto) == []               # a 2.54 mm optocoupler's leads are 0.16 of the pitch
    sot = dict(package_type="gullwing", pitch=1.0, pin_count=3, terminal_width=T._dim((0.15, 0.22, 0.3)),
               name="SOT-523", pin_positions=[{"number": "1", "side": "L", "offset": -0.5}])
    assert D._lead_vs_pitch(sot) == []                # three listed leads: the 'pitch' is between two of them
    plcc = dict(package_type="gullwing", pitch=1.27, pin_count=28, terminal_width=T._dim((0.66, 0.74, 0.81)),
                name="PLCC-28_11.5x11.5mm_P1.27mm")
    assert D._lead_vs_pitch(plcc) == []               # the J lead's shoulder
    fine = dict(package_type="qfn", pitch=0.4, pin_count=48, terminal_width=T._dim((0.15, 0.2, 0.25)), name="QFN-48")
    assert D._lead_vs_pitch(fine) == []
    assert D._lead_vs_pitch(dict(fine, terminal_width=T._dim((0.075, 0.1, 0.125))))
    assert D._lead_vs_pitch(dict(fine, terminal_width=T._dim((0.25, 0.3, 0.35))))   # 0.35 of 0.4: under the pitch


def test_a_chips_width_read_half_is_held():
    h = held(dict(T.R1206, body_width=T.sc(T.R1206["body_width"], 0.5)))
    assert h and "too far apart" in h, h
    c0603 = T.two("C_0603_1608Metric", "chip", (1.45, 1.6, 1.75), (0.65, 0.8, 0.95), (0.1, 0.3, 0.5))
    assert held(c0603) is None
    h = held(dict(c0603, body_width=T.sc(c0603["body_width"], 0.5)))
    assert h and "too far apart" in h, h


def test_a_chips_length_and_width_the_other_way_round_is_held():
    """A 0402 read 0.5 long and 1.0 wide fits the same box as the true one, so the pad comparison let it through
    (deep-dive open item 2026-11-12)."""
    h = held(dict(T.R0402, body_length=T.R0402["body_width"], body_width=T.R0402["body_length"]))
    assert h and "too far apart" in h, h


def test_std_dims_compares_only_the_same_named_package_and_no_tab():
    assert D.std_dims({"name": "no such package", "package_type": "gullwing"}) is None
    spec = T.read(copy.deepcopy(T.SOIC8))["spec"]
    assert D.std_dims(spec) is None
    assert D.std_dims(dict(spec, package_type="qfn")) is None          # the table's SOIC is a gull-wing
    half = dict(spec, terminal_width={"min": 0.155, "nom": 0.205, "max": 0.255})
    assert D.std_dims(half)[0] == "terminal_width"
    assert D.std_dims(dict(half, tab={"width": {"nom": 5}})) is None
    lo, hi = D.STD_SIZE                                               # the edges: a maker's own tolerance is inside
    w = D._nom(SP.packages()[spec["name"]]["terminal_width"])
    at = lambda f: dict(spec, terminal_width=dict.fromkeys(("min", "nom", "max"), round(w * f, 4)))
    assert D.std_dims(at(lo + 0.02)) is None and D.std_dims(at(hi - 0.02)) is None
    assert D.std_dims(at(lo - 0.02)) and D.std_dims(at(hi + 0.02))


def test_a_makers_own_foot_length_is_not_held():
    # one package, several makers: a SOIC's foot is 0.4-1.27 in JEDEC's drawing, 0.4-0.8 or 0.62-0.82 in others
    for foot in ((0.4, 0.835, 1.27), (0.4, 0.6, 0.8), (0.62, 0.72, 0.82), (0.5, 0.7, 0.9)):
        assert held(dict(T.SOIC8, terminal_length=foot)) is None, foot


# ---------------------------------------------------------------- 21: the exposed pad the name and pin table give
def test_a_named_exposed_pad_with_its_pin_in_the_table_and_no_pad_read_is_held():
    res = good(exposed_pad=None)                      # GOOD: VQFN-16-1EP, pin 17 PGND in the pin table
    hold = DR.hold_reasons(res)
    assert hold and D.NO_EP in hold[0] and "pin 17" in hold[0], (hold, res["needs_review"])
    assert D.read_hold(res)


def test_a_pad_pin_named_as_the_pad_counts_whatever_its_number():
    e = copy.deepcopy(GOOD)
    e["package"]["exposed_pad"] = None
    e["pins"][-1].update(num="EP", name="Thermal Pad")
    hold = DR.hold_reasons(D.process(e, "TESTPART", "ds.pdf"))
    assert hold and D.NO_EP in hold[0], hold


def test_a_named_exposed_pad_with_no_pin_for_it_stays_advice():
    """One fact only (the name, which the read wrote itself): a line for the reviewer as before, not a hold."""
    e = copy.deepcopy(GOOD)
    e["package"]["exposed_pad"] = None
    e["pins"] = e["pins"][:16]
    res = D.process(e, "TESTPART", "ds.pdf")
    assert any("says exposed pad, none was read" in r for r in res["needs_review"]), res["needs_review"]
    assert not any(D.NO_EP in r for r in res["needs_review"]) and not DR.hold_reasons(res), res["needs_review"]


# ---------------------------------------------------------------- 22: a pitch that is not the name's
def test_a_pitch_that_moves_the_rows_end_leads_off_the_names_is_held():
    res = good(pitch=dim("e", None, 0.4, None))       # named ..._P0.5mm: the end leads 0.15 mm off
    hold = DR.hold_reasons(res)
    assert hold and "name says pitch 0.5 mm" in hold[0] and D.TOO_FAR in hold[0], (hold, res["needs_review"])
    h = held(dict(T.SSOP28, pitch=0.5))               # 14 leads a side at 0.5 for 0.65: 0.98 mm off
    assert h and "pitch" in h, h


def test_a_rounded_pitch_in_the_name_is_not_held():
    """P0.65mm for a 0.635 mm (25 mil) row moves an 8-lead row's end 0.02 mm: advice, as before."""
    t = T.gw("SSOP-16_3.9x4.9mm_P0.65mm", 16, 0.635, (4.8, 4.9, 5.0), (3.8, 3.9, 4.0), (5.8, 6.0, 6.2),
             (0.2, 0.25, 0.3), (0.4, 0.835, 1.27))
    res = T.read(t)
    assert any("name says pitch 0.65 mm" in r for r in res["needs_review"]), res["needs_review"]
    assert not any("pitch" in h for h in DR.hold_reasons(res)), DR.hold_reasons(res)
    assert D._pitch_shift({"pitch": 0.635, "pin_count": 16, "package_type": "gullwing"}, 0.65) < D.PITCH_SHIFT
    assert D._pitch_shift({"pitch": 2.5, "pin_count": 40, "package_type": "gullwing"}, 2.54) > D.PITCH_SHIFT


# ---------------------------------------------------------------- 23: a chip size code with no 'Metric' in the name
BIG = dict(package_type="chip", pin_count=2, body_length=(3.0, 3.2, 3.4), body_width=(1.4, 1.6, 1.8),
           terminal_length=(0.25, 0.5, 0.75))


def test_a_1206_body_under_any_spelling_of_0603_is_held():
    for name in ("C_0603_1608Metric", "C_0603", "R_0603", "0603", "CAPC1608X90N", "RESC1608X55N"):
        h = held(dict(BIG, name=name))
        assert h and "too far apart" in h, (name, h)


def test_the_true_body_under_those_names_is_not_held():
    ok = dict(package_type="chip", pin_count=2, body_length=(1.45, 1.6, 1.75), body_width=(0.65, 0.8, 0.95),
              terminal_length=(0.1, 0.3, 0.5))
    for name in ("C_0603_1608Metric", "C_0603", "R_0603", "0603", "CAPC1608X90N"):
        assert held(dict(ok, name=name)) is None, (name, held(dict(ok, name=name)))
    for name in ("C_1206", "1206", "RESC3216X60N"):
        assert held(dict(BIG, name=name)) is None, (name, held(dict(BIG, name=name)))


def test_a_bare_code_that_is_also_a_metric_code_fits_either_body():
    """'0603' is an imperial 0603 (1.6 x 0.8) by habit and the metric code of an 0201 (0.6 x 0.3): a read named so
    is held only when its body is neither."""
    tiny = dict(package_type="chip", pin_count=2, body_length=(0.57, 0.6, 0.63), body_width=(0.27, 0.3, 0.33),
                terminal_length=(0.1, 0.15, 0.2), name="0603")
    assert held(tiny) is None, held(tiny)
    assert D._chip_code_bodies("0603") == [("name 0603", [(1.6, 0.8), (0.6, 0.3)])]
    assert D._chip_code_bodies("CAPC1608X90N") == [("name CAPC1608X90N", [(1.6, 0.8)])]
    assert D._chip_code_bodies("C_0603_1608Metric") == []             # name_facts reads the Metric code itself
    assert D._chip_code_bodies("SOIC-8_3.9x4.9mm_P1.27mm") == []
    assert D._chip_code_bodies("LT1206CS8") == []                     # a code inside a part number is no size


def test_the_boms_package_is_compared_with_a_chips_body():
    """The BOM line's own package is a fact the read did not write: '0603' on the line and a 1206's body in the read."""
    pkg = {k: None for k in T.PKG_KEYS}
    pkg.update(unresolved=[], drawing="d", description="chip", name="C_1206_3216Metric", package_type="chip", pin_count=2,
               body_length=T._dim(BIG["body_length"]), body_width=T._dim(BIG["body_width"]),
               terminal_length=T._dim(BIG["terminal_length"]), height_max=T._dim((None, None, 0.95)))
    ext = {"package": pkg, "pin_table": {"page": 3, "figure": "t", "rows": 2},
           "pins": [{"num": str(i), "name": str(i), "type": "passive", "side": "left", "page": 3} for i in (1, 2)],
           "symbol_description": "x", "notes": [], "embedded_instructions": []}
    assert D.read_hold(D.process(copy.deepcopy(ext), "TESTPART", "ds.pdf", package="1206")) is None
    h = D.read_hold(D.process(copy.deepcopy(ext), "TESTPART", "ds.pdf", package="0603"))
    assert h and "BOM package 0603" in h and "too far apart" in h, h
    assert D.read_hold(D.process(copy.deepcopy(ext), "TESTPART", "ds.pdf", package="SOIC-8")) is None


def test_small_sizes_one_step_apart_are_far():
    """0.5 mm is the whole part on the small sizes: a 0402's body under an 0201's code was inside the old rule."""
    assert D._far_body((0.6, 0.3), (1.0, 0.5)) and D._far_body((1.0, 0.5), (1.6, 0.8))
    assert not D._far_body((2.0, 1.2), (2.0, 1.25)) and not D._far_body((1.6, 0.8), (1.75, 0.95))
    assert not D._far_body((3.2, 1.6), (3.4, 1.8))


# ---------------------------------------------------------------- the held-out split of the benchmark set
def test_skip_runs_the_cases_after_the_first_n_of_the_same_order():
    """`eval run --limit 110` is the benchmark; `--skip 110` is every part it never read, in the same interleaved
    order, so a held-out run shares no part with the 110 the reader's fixes were made on."""
    from ecadlib.eval import runner as R
    cases = R.load_set("kicad-v1")["cases"]
    every, first, rest = R.select(cases), R.select(cases, limit=110), R.select(cases, skip=110)
    assert first + rest == every and len(rest) == len(cases) - 110
    assert not {c["id"] for c in first} & {c["id"] for c in rest}
    assert R.select(cases, skip=110, limit=40) == every[110:150]
    assert R.select(cases, skip=0) == every and R.select(cases, skip=None) == every


def test_a_foot_printed_as_one_limit_is_not_compared():
    """'L 0.016 MIN' is the foot's low limit: half the table's nominal, and a true read (test_one_sided_dims)."""
    spec = T.read(copy.deepcopy(T.SOIC8))["spec"]
    assert D.std_dims(dict(spec, terminal_length={"min": 0.4064, "nom": 0.4064})) is None
    assert D.std_dims(dict(spec, terminal_length={"min": 0.2, "nom": 0.3, "max": 0.4}))[0] == "terminal_length"


def test_listed_pads_are_not_placed_by_the_pitch():
    listed = {"pitch": 1.9, "pin_count": 3, "package_type": "gullwing", "pin_positions": [{"number": "1"}]}
    assert D._pitch_shift(listed, 0.95) == 0          # a SOT's e1 given as the pitch: its pads have their own offsets
