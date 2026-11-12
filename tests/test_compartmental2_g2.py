"""Compartmental testing round 2 (2026-11-12), group 2: datasheet reading checks and footprint / symbol details.
Each component is called directly with small hand-made inputs. Offline: no key, no network, no model.
    python3 -m tests.run_all test_compartmental2_g2"""
import copy

from ecadlib import datasheet as D, ds_route as DR, stdparts as SP
from tests import test_fix1026_rd as T
from tests.test_fix1026_rd import gw, two

IN = 25.4


def held(t, package=None):
    res = T.read(copy.deepcopy(t))
    if package is not None:
        ext = _ext(t)
        res = D.process(ext, "TESTPART", "ds.pdf", package=package)
    return D.read_hold(res)


def _ext(t):
    pkg = {k: None for k in T.PKG_KEYS}
    pkg.update(unresolved=[], drawing="DWG", description=t.get("description") or t["name"],
               height_max=T._dim((None, None, 1.5)))
    for k, v in t.items():
        pkg[k] = T._dim(v) if k in D.DIMS or k in D.NUM_DIMS else copy.deepcopy(v)
    pins = [{"num": str(i), "name": str(i), "type": "passive", "side": "left", "page": 3} for i in (1, 2)]
    return {"package": pkg, "pin_table": {"page": 3, "figure": "t", "rows": 2}, "pins": pins,
            "symbol_description": t.get("description") or "x", "notes": [], "embedded_instructions": []}


# ---------------------------------------------------------------- pitch against the name: an inch pitch's metric name
def ssop(name, n, e, D_, E1, E):
    return gw(name, n, e, D_, E1, E, (0.2, 0.25, 0.34), (0.5, 0.75, 1.02))


SSOP36 = ssop("SSOP-36_5.3x12.6mm_P0.65mm", 36, 0.635, (12.5, 12.6, 12.7), (5.2, 5.3, 5.4), (7.6, 7.8, 8.0))
SSOP48 = ssop("SSOP-48_7.5x15.9mm_P0.65mm", 48, 0.635, (15.75, 15.88, 16.0), (7.4, 7.5, 7.6), (10.03, 10.3, 10.67))


def test_a_25_mil_row_named_p0_65_is_not_held_on_long_rows():
    """A 0.635 mm (25 mil) row under a name rounded to P0.65mm: 18 and 24 leads a side moved the end lead 0.13 and
    0.17 mm 'from the name's', past the 0.1 mm limit, and a true part was held (review of #243). The pads are built
    at the drawing's 0.635, which is right; the name only rounds it."""
    for t in (SSOP36, SSOP48):
        res = T.read(copy.deepcopy(t))
        assert any("name says pitch 0.65 mm" in r for r in res["needs_review"]), res["needs_review"]   # still advice
        assert not DR.hold_reasons(res), (t["name"], DR.hold_reasons(res))


def test_a_pitch_printed_in_inches_is_not_held():
    """0.026 in (0.6604 mm) is how an inch drawing prints a 0.65 mm pitch: on 24 leads a side it was held."""
    for t in (SSOP48, SSOP36, T.SSOP28):
        assert held(dict(t, pitch=round(0.026 * IN, 4))) is None, t["name"]
    assert D._same_pitch(0.65, 0.635) and D._same_pitch(0.65, 0.6604) and D._same_pitch(0.5, 0.5004)
    assert D._same_pitch(0.75, 0.762)                 # 30 mil


def test_a_pitch_of_another_variant_is_still_held():
    for e in (0.5, 0.8, 0.4):
        h = held(dict(SSOP48, pitch=e))
        assert h and "pitch" in h, (e, h)
    tssop48 = gw("TSSOP-48_6.1x12.5mm_P0.5mm", 48, 0.5, (12.4, 12.5, 12.6), (6.0, 6.1, 6.2), (7.9, 8.1, 8.3),
                 (0.17, 0.22, 0.27), (0.45, 0.6, 0.75))
    assert held(tssop48) is None
    h = held(dict(tssop48, pitch=0.4))
    assert h and "pitch" in h, h
    for a, b in ((0.5, 0.4), (0.65, 0.5), (1.0, 0.95), (0.8, 0.75), (0.65, 0.8), (1.27, 1.0)):
        assert not D._same_pitch(a, b), (a, b)


# ---------------------------------------------------------------- chip size codes: wire-wound inductors, 3640
def ind(name, L, W, Tl=(0.3, 0.5, 0.7)):
    return two(name, "chip", L, W, Tl, description="wirewound chip inductor")


CS1008 = ind("1008CS", (2.7, 2.92, 3.0), (2.6, 2.79, 2.9))
CS0805 = ind("0805CS", (2.0, 2.29, 2.4), (1.5, 1.73, 1.8), (0.3, 0.45, 0.6))


def test_a_wirewound_inductor_body_over_its_eia_code_is_not_held():
    """Coilcraft's 1008CS is 2.92 x 2.79 mm and its 0805CS 2.29 x 1.73 mm: wire-wound chip inductors are sold by
    the EIA code and are larger than the code's body. Both were held against the BOM's '1008' / '0805' and against
    the names L_1008_2520Metric / L_0805_2012Metric (review of #243)."""
    for t, code in ((CS1008, "1008"), (CS0805, "0805")):
        assert held(t, code) is None, (t["name"], held(t, code))
        named = dict(t, name=f"L_{code}_{SP.IMP2MET[code]}Metric")
        assert held(named) is None and held(named, code) is None, held(named, code)


def test_an_inductor_read_half_twice_or_from_a_far_size_is_still_held():
    for t, code in ((CS1008, "1008"), (CS0805, "0805")):
        for f in (2, 0.5):
            h = held(dict(t, body_length=T.sc(t["body_length"], f), body_width=T.sc(t["body_width"], f)), code)
            assert h and "too far apart" in h, (t["name"], f, h)
    big = ind("1812CS", (4.5, 4.95, 5.1), (3.2, 3.81, 3.9))         # a 1812's row read for a 1008
    h = held(dict(big, name="1008CS"), "1008")
    assert h and "too far apart" in h, h
    small = ind("0603CS", (1.6, 1.8, 1.9), (1.0, 1.12, 1.2))       # a smaller size's row: judged as before
    h = held(dict(small, name="1008CS"), "1008")
    assert h and "too far apart" in h, h


def test_the_inductor_allowance_is_for_inductors_only():
    cap = dict(CS1008, name="C_1008", description="ceramic capacitor")
    h = held(cap, "1008")
    assert h and "too far apart" in h, h


def test_a_3640_body_under_its_own_codes_is_not_held():
    """KiCad's C_3640_9110Metric read 9.1 x 1.0 mm from its code and the BOM's '3640' 9.1 x 1.6 mm (from 9116): a
    true 9.14 x 10.16 mm read was held."""
    c = two("C_3640", "chip", (8.9, 9.14, 9.4), (9.9, 10.16, 10.4), (0.25, 0.6, 0.95))
    for name in ("C_3640", "3640", "C_3640_9110Metric"):
        assert held(dict(c, name=name)) is None, (name, held(dict(c, name=name)))
        assert held(dict(c, name=name), "3640") is None, name
    assert D.name_facts("C_3640_9110Metric")["code_body"] == (9.1, 10.0)
    assert SP.chip_size("C_3640_9110Metric") == "3640"
    h = held(dict(c, name="C_3640_9110Metric", body_width=T.sc(c["body_width"], 0.5)))
    assert h and "too far apart" in h, h
