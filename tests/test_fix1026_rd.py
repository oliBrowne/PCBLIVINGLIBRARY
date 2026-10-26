"""Fixes 2026-10-26 (review 196 reads2, R-RD-1 .. R-RD-8): datasheet reads of non-QFP families that built a wrong
footprint with no hold. Offline: no key, no network, no model.
    python3 -m tests.run_all test_fix1026_rd"""
import copy

from ecadlib import datasheet as D, ds_route as DR, footprint_gen as F, footprint_adv as FA

PKG_KEYS = list(D.output_schema()["properties"]["package"]["properties"])


def _dim(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return {"label": "X", "min": None, "nom": v, "max": None, "unit": "mm", "page": 5, "figure": "Package"}
    lo, nom, hi = v
    return {"label": "X", "min": lo, "nom": nom, "max": hi, "unit": "mm", "page": 5, "figure": "Package"}


def read(t, pins=None):
    """A model's answer for package t (dims as (min, nom, max) in mm) -> datasheet.process's result."""
    pkg = {k: None for k in PKG_KEYS}
    pkg.update(unresolved=[], drawing="DWG", description=t["name"], height_max=_dim((None, None, 1.5)))
    for k, v in t.items():
        if k.startswith("_"):
            continue
        if k == "exposed_pad" and v:
            pkg[k] = {"length": _dim(v["length"]), "width": _dim(v["width"])}
        elif k == "tab" and v:
            strip = lambda d: None if d is None else dict(zip(("min", "nom", "max"), d))
            pkg[k] = dict(v, width=strip(v["width"]), length=strip(v.get("length")))
        elif k in D.DIMS or k in D.NUM_DIMS:
            pkg[k] = _dim(v)
        else:
            pkg[k] = copy.deepcopy(v)
    nums = t.get("_pins") or [str(i) for i in range(1, (t.get("pin_count") or 0) + 1)]
    pins = pins or [{"num": x, "name": f"P{x}", "type": "passive", "side": "left", "page": 3} for x in nums]
    if t.get("exposed_pad"):
        pins.append({"num": str(len(nums) + 1), "name": "EP", "type": "power", "side": "left", "page": 3})
    ext = {"package": pkg, "pin_table": {"page": 3, "figure": "Pins", "rows": len(nums)}, "pins": pins,
           "symbol_description": "test part", "notes": [], "embedded_instructions": []}
    return D.process(ext, "TESTPART", "ds.pdf")


def verdict(t):
    res = read(t)
    kind, why = DR.assess(res)
    return res, kind, why, DR.hold_reasons(res, True)


def assert_ok(t):
    res, kind, why, hold = verdict(t)
    assert kind == "ok" and not hold, (t["name"], kind, why)
    return res


def assert_held(t, *words):
    res, kind, why, hold = verdict(t)
    assert hold, (t["name"], kind, why)
    text = " | ".join(hold)
    assert all(w in text for w in words), (words, hold)
    return res


def sc(d, f):
    return tuple(round(x * f, 4) for x in d)


# ---------------------------------------------------------------- truths (maker drawings, as reads2 used them)
def two(name, t, L, W, T, b=None, **kw):
    d = dict(name=name, package_type=t, pin_count=2, body_length=L, body_width=W, terminal_length=T, **kw)
    if b:
        d["terminal_width"] = b
    return d


R1206 = two("R_1206_3216Metric", "chip", (3.0, 3.1, 3.2), (1.5, 1.6, 1.7), (0.25, 0.45, 0.65))
R0402 = two("R_0402_1005Metric", "chip", (0.95, 1.0, 1.05), (0.45, 0.5, 0.55), (0.15, 0.25, 0.35))
TANT_B = two("CP_EIA-3528-21_Kemet-B", "molded", (3.3, 3.5, 3.7), (2.6, 2.8, 3.0), (0.5, 0.8, 1.1), b=(2.1, 2.2, 2.3),
             polarized=True)
SMA = two("D_SMA", "molded", (4.8, 5.0, 5.28), (2.29, 2.6, 2.92), (0.76, 1.14, 1.52), b=(1.25, 1.45, 1.65), polarized=True)
SMB = two("D_SMB", "molded", (5.21, 5.4, 5.59), (3.3, 3.62, 3.94), (0.76, 1.14, 1.52), b=(1.96, 2.08, 2.21), polarized=True)
SMC = two("D_SMC", "molded", (7.75, 7.94, 8.13), (5.59, 5.9, 6.22), (0.76, 1.14, 1.52), b=(2.75, 2.96, 3.18), polarized=True)
MELF = two("D_MELF", "chip", (4.8, 5.0, 5.2), (2.41, 2.54, 2.67), (0.4, 0.5, 0.6), lead_type="melf", polarized=True)
TWO_TRUTHS = (R1206, R0402, TANT_B, SMA, SMB, SMC, MELF)


# ---------------------------------------------------------------- R-RD-1: two-terminal terminal plausibility
def test_rd1_true_two_terminal_reads_pass():
    for t in TWO_TRUTHS:
        assert_ok(t)


def test_rd1_terminal_width_in_inches_is_held():
    """Before: SMC terminal_width 0.108-0.125 'mm' (inches) passed and built 0.1 mm wide pads."""
    assert_held(dict(SMC, terminal_width=sc(SMC["terminal_width"], 1 / 25.4)), "terminal_width")
    assert_held(dict(TANT_B, terminal_width=sc(TANT_B["terminal_width"], 1 / 25.4)), "terminal_width")


def test_rd1_terminal_length_in_inches_at_the_0_050_boundary_is_held():
    """Before: SMA terminal_length 0.030-0.060 'mm' passed (0.06 is not < 0.05); pads shifted 1.27 mm."""
    assert_held(dict(SMA, terminal_length=sc(SMA["terminal_length"], 1 / 25.4)), "terminal_length")
    assert_held(dict(SMB, terminal_length=sc(SMB["terminal_length"], 1 / 25.4)), "terminal_length")


def test_rd1_terminal_width_wider_than_the_body_is_held():
    """Before: an SMC terminal_width 1.2 x the body width built 7.35 mm wide pads."""
    assert_held(dict(SMC, terminal_width=sc(SMC["body_width"], 1.2)), "terminal_width", "body_width")
    assert_held(dict(TANT_B, terminal_width=sc(TANT_B["body_width"], 1.2)), "terminal_width", "body_width")


def test_rd1_inner_span_as_long_as_the_body_is_held():
    """Before: inner_span = body_length pushed the pads out 1.3 mm."""
    for t in (SMA, R1206, MELF):
        assert_held(dict(t, inner_span=t["body_length"]), "inner_span", "body_length")


def test_rd1_molded_terminal_width_left_blank_is_held():
    """Before: a tantalum read with terminal_width blank built its pads as wide as the body."""
    res = assert_held(dict(TANT_B, terminal_width=None), "terminal_width")
    assert "terminal_width" in (res["spec"].get("unresolved") or []), res["spec"].get("unresolved")
    assert_held(dict(SMA, terminal_width=None), "terminal_width")
    assert_ok(R1206)                                  # a chip's end caps are as wide as the body: no terminal_width


def test_rd1_molded_with_the_makers_land_needs_no_terminal_width():
    t = dict(TANT_B, terminal_width=None, land={"pad_length": 1.8, "pad_width": 2.3, "pad_center": 1.4})
    res, kind, why, hold = verdict(t)
    assert not hold and "terminal_width" not in (res["spec"].get("unresolved") or []), (kind, why)


def test_rd1_melf_end_cap_narrower_than_the_body_is_held():
    """Before: a MELF read with a land-sized terminal_width (a third of the diameter) built 0.9 mm off."""
    assert_ok(dict(MELF, terminal_width=MELF["body_width"]))
    assert_held(dict(MELF, terminal_width=sc(MELF["body_width"], 0.33)), "terminal_width", "MELF")


# ---------------------------------------------------------------- R-RD-2: gull-wing foot length
def gw(name, n, e, D_, E1, E, b, L, **kw):
    return dict(name=name, package_type="gullwing", pin_count=n, pitch=e, body_length=D_, body_width=E1,
                lead_span=E, terminal_width=b, terminal_length=L, **kw)


SOIC8 = gw("SOIC-8_3.9x4.9mm_P1.27mm", 8, 1.27, (4.8, 4.9, 5.0), (3.8, 3.9, 4.0), (5.8, 6.0, 6.2), (0.31, 0.41, 0.51),
           (0.4, 0.835, 1.27))
TSSOP14 = gw("TSSOP-14_4.4x5mm_P0.65mm", 14, 0.65, (4.9, 5.0, 5.1), (4.3, 4.4, 4.5), (6.2, 6.4, 6.6), (0.19, 0.245, 0.3),
             (0.45, 0.6, 0.75))
SSOP28 = gw("SSOP-28_5.3x10.2mm_P0.65mm", 28, 0.65, (9.9, 10.2, 10.5), (5.0, 5.3, 5.6), (7.4, 7.8, 8.2), (0.22, 0.3, 0.38),
            (0.55, 0.75, 0.95))
MSOP8 = gw("MSOP-8_3x3mm_P0.65mm", 8, 0.65, (2.9, 3.0, 3.1), (2.9, 3.0, 3.1), (4.75, 4.9, 5.05), (0.25, 0.315, 0.38),
           (0.4, 0.55, 0.7))
POS89 = [{"number": "1", "side": "L", "offset": -1.5}, {"number": "2", "side": "L", "offset": 0.0},
         {"number": "3", "side": "L", "offset": 1.5}]
SOT89 = gw("SOT-89-3", 3, 1.5, (4.4, 4.5, 4.6), (2.4, 2.5, 2.6), (3.94, 4.095, 4.25), (0.36, 0.42, 0.48), (0.8, 0.95, 1.1),
           pin_positions=POS89, tab={"number": "2", "side": "R", "offset": 0.0, "width": (1.62, 1.725, 1.83), "length": None})


def test_rd2_true_gullwing_reads_pass():
    for t in (SOIC8, TSSOP14, SSOP28, MSOP8):
        assert_ok(t)


def test_rd2_foot_in_inches_at_the_0_050_boundary_is_held():
    """Before: SOIC-8 L 0.0157-0.050 'mm' passed (0.05 is not < 0.05); pads 0.95 long, 1.0 mm out."""
    assert_held(dict(SOIC8, terminal_length=sc(SOIC8["terminal_length"], 1 / 25.4)), "terminal_length")


def test_rd2_foot_too_long_for_the_room_outside_the_body_is_held():
    """Before: TSSOP-14 L x 2.54 (1.14-1.91) passed: only L min (1.14) was checked against the room outside the body."""
    for t in (TSSOP14, SOIC8, SSOP28, MSOP8):
        assert_held(dict(t, terminal_length=sc(t["terminal_length"], 2.54)), "terminal_length nom")


def test_rd2_j_leads_stay_exempt():
    soj = gw("SOJ-24_7.62x15.875mm_P1.27mm", 24, 1.27, (15.7, 15.875, 16.05), (7.49, 7.62, 7.75), (8.38, 8.51, 8.64),
             (0.38, 0.46, 0.54), (1.1, 1.3, 1.5))
    res, kind, why, hold = verdict(soj)
    assert not any("terminal_length nom" in w for w in why), why


def test_rd2_leads_on_one_side_keep_the_whole_room():
    """SOT-89: three leads on one side, the tab under the other: its 0.95 mm foot is not two feet in the room."""
    assert_ok(SOT89)
