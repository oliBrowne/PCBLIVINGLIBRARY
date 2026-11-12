"""Open items of the 2026-11-12 deep dive, reader side: misreads the numbers alone could not show.
  * a terminal read twice too long, a pad half as wide, or a two-pad part's length and width the other way round under
    the name of a standard package: the box around the leads stayed the same, so nothing was said;
  * an exposed pad half the size its own name states was a note, and the part was built;
  * a body twice the size its name's size code states (EIA-3528) on a package the table does not hold."""
import copy
from ecadlib import datasheet as D
from tests import test_fix1026_rd as T


def held(t):
    return D.read_hold(T.read(copy.deepcopy(t)))


def ep(t, **k):
    return dict(t, exposed_pad=dict(t["exposed_pad"], **k))


def test_true_reads_are_not_held():
    for t in (T.R1206, T.R0402, T.TANT_B, T.SMA, T.SMB, T.SMC, T.MELF, T.SOIC8, T.TSSOP14, T.SSOP28, T.MSOP8, T.SOT89,
              T.SOT23, T.DPAK, T.WSON6, T.DFN8, T.VQFN16, T.SOD123, T.SOD323):
        assert held(t) is None, (t["name"], held(t))


def test_a_pad_far_from_the_named_standard_packages_pad_is_held():
    for t in (dict(T.SMC, terminal_length=T.sc(T.SMC["terminal_length"], 2)),      # pads grow inwards: same box
              dict(T.SSOP28, terminal_length=T.sc(T.SSOP28["terminal_length"], 2)),
              dict(T.SMA, terminal_length=T.sc(T.SMA["terminal_length"], 0.5)),
              dict(T.R1206, body_length=T.R1206["body_width"], body_width=T.R1206["body_length"]),   # the other way round
              dict(T.SMC, body_length=T.SMC["body_width"], body_width=T.SMC["body_length"])):
        h = held(t)
        assert h and "too far apart" in h and "pad" in h, (t["name"], h)


def test_a_makers_land_a_little_off_the_table_pad_is_not_held():
    assert held(dict(T.MSOP8, lead_span=(4.5, 4.65, 4.8))) is None
    assert held(dict(T.R1206, body_length=(3.2, 3.4, 3.6))) is None
    assert held(dict(T.SMA, body_width=(2.5, 2.9, 3.2))) is None
    assert held(dict(T.SOIC8, terminal_length=T.sc(T.SOIC8["terminal_length"], 1.3))) is None
    assert held(dict(T.SOT23, terminal_width=T.sc(T.SOT23["terminal_width"], 1.25))) is None


def test_an_exposed_pad_far_from_the_size_in_its_name_is_held():
    for t in (ep(T.DFN8, width=T.sc(T.DFN8["exposed_pad"]["width"], 0.5)),
              ep(T.VQFN16, length=T.sc(T.VQFN16["exposed_pad"]["length"], 0.5)),
              ep(T.WSON6, width=T.sc(T.WSON6["exposed_pad"]["width"], 0.5))):
        h = held(t)
        assert h and "name says EP" in h and "too far apart" in h, (t["name"], h)


def test_an_exposed_pad_near_the_size_in_its_name_is_not_held():
    for t in (ep(T.DFN8, width=T.sc(T.DFN8["exposed_pad"]["width"], 0.9)),
              ep(T.VQFN16, length=T.sc(T.VQFN16["exposed_pad"]["length"], 1.15))):
        assert held(t) is None, (t["name"], held(t))


def test_a_body_far_from_the_size_code_in_its_name_is_held():
    assert D.name_facts("CP_EIA-3528-21_Kemet-B")["code_body"] == (3.5, 2.8)
    assert D.name_facts("R_1206_3216Metric")["code_body"] == (3.2, 1.6)
    assert D.name_facts("SOIC-8_3.9x4.9mm_P1.27mm")["code_body"] is None
    h = held(dict(T.TANT_B, body_length=T.sc(T.TANT_B["body_length"], 2)))
    assert h and "size code" in h and "too far apart" in h, h
    assert held(dict(T.TANT_B, body_length=T.sc(T.TANT_B["body_length"], 1.08))) is None    # a code is a rounded size
