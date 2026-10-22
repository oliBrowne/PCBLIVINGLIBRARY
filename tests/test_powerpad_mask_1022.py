"""Solder-mask-defined thermal lands (TI PowerPAD): the copper spreads heat under the mask, a smaller opening of its
own takes the solder. TLC5951DAP p51 (HTSSOP-38, DAP): 5.2 x 12.5 copper, 3.39 x 6.35 open, as KiCad's
HTSSOP-38-1EP_6.1x12.5mm_P0.65mm_EP5.2x12.5mm_Mask3.39x6.35mm draws it."""
from ecadlib import altium_lib as AL, footprint_bench as B, footprint_gen as F, footprint_rules as R, sexpr
from ecadlib.eval import score as S

LAND = {"pad_length": 1.575, "pad_width": 0.4, "pad_center": 3.7125, "ep_across": 5.2, "ep_along": 12.5}
TLC = {"name": "HTSSOP-38-1EP_test", "package_type": "gullwing", "pin_count": 38, "pitch": 0.65,
       "lead_span": {"min": 7.9, "max": 8.3}, "body_width": {"min": 6.0, "max": 6.2},
       "body_length": {"min": 12.4, "max": 12.6}, "terminal_width": {"min": 0.17, "max": 0.27},
       "terminal_length": {"min": 0.45, "max": 0.75},
       "exposed_pad": {"length": {"min": 2.46, "max": 3.06}, "width": {"min": 5.13, "max": 5.73}},
       "land": dict(LAND, ep_mask_across=3.39, ep_mask_along=6.35),
       "source": {"datasheet": "https://www.ti.com/lit/ds/symlink/tlc5951.pdf"}}


def _pads(text, num=None):
    t = sexpr.parse(text)
    out = []
    for p in sexpr.find(t, "pad"):
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        out.append((p[1], float(at[1]), float(at[2]), float(size[1]), float(size[2]),
                    tuple((sexpr.first(p, "layers") or ["layers"])[1:])))
    return out if num is None else [p for p in out if p[0] == num]


def _ref():
    import json
    sets = json.load(open(S.__file__.replace("score.py", "sets/kicad-v1.json")))
    return sets["references"]["Package_SO:HTSSOP-38-1EP_6.1x12.5mm_P0.65mm_EP5.2x12.5mm_Mask3.39x6.35mm_ThermalVias"]


def test_mask_defined_land_opens_only_the_mask_window():
    text, rep = F.build(dict(TLC))
    pads = _pads(text)
    ep = [p for p in pads if p[0] == "39"]
    assert ep == [("39", 0.0, 0.0, 5.2, 12.5, ("F.Cu",))]                    # copper only: the mask covers its edge
    mask = [p for p in pads if p[5] == ("F.Mask",)]
    assert mask == [("", 0.0, 0.0, 3.39, 6.35, ("F.Mask",))]
    paste = [p for p in pads if p[5] == ("F.Paste",)]
    assert paste and all(abs(x) + w / 2 <= 3.39 / 2 + 1e-6 and abs(y) + h / 2 <= 6.35 / 2 + 1e-6
                         for _, x, y, w, h, _ in paste)                       # no paste on masked copper
    cov = sum(w * h for _, _, _, w, h, _ in paste) / (3.39 * 6.35)
    assert 0.6 < cov < 0.7                                                    # 65 % of the opening
    assert rep["ep_mask"] == (3.39, 6.35)


def test_mask_defined_land_passes_the_pad_checks_and_scores_against_kicad():
    text, _ = F.build(dict(TLC))
    num = B.numbers(text)
    assert num["layers"] == {"no_copper": [], "no_mask": [], "stray_copper": 0}
    fields = S.score_footprint(num, _ref())
    assert fields["layers"]["ok"], fields["layers"]["error"]
    assert fields["pads"]["ok"], fields["pads"]["error"]                     # the copper is KiCad's 5.2 x 12.5
    assert fields["pad_count"]["ok"], fields["pad_count"]["error"]          # (KiCad's thermal vias dropped)
    errs = [i for i in R.check(text) if i[0] == "error"]
    assert not errs, errs                                                    # the rule book takes the mask window


def test_a_copper_pad_with_no_mask_and_no_opening_is_still_caught():
    text, _ = F.build(dict(TLC))
    t = text.replace('(pad "" smd rect (at 0 0) (size 3.39 6.35) (layers "F.Mask"))', "")
    assert t != text
    assert B.numbers(t)["layers"]["no_mask"] == ["39"]


def test_without_a_smaller_mask_the_land_is_open_as_before():
    for land in (LAND, dict(LAND, ep_mask_across=5.2, ep_mask_along=12.5), dict(LAND, ep_mask_across=3.39),
                 dict(LAND, ep_mask_across=6.0, ep_mask_along=6.35)):            # none, equal, one size, larger
        text, rep = F.build(dict(TLC, land=land))
        assert _pads(text, "39") == [("39", 0.0, 0.0, 5.2, 12.5, ("F.Cu", "F.Mask"))]
        assert not [p for p in _pads(text) if p[5] == ("F.Mask",)] and "ep_mask" not in rep


def test_altium_gets_a_tented_pad_and_a_top_solder_window():
    text, _ = F.build(dict(TLC))
    fp = AL.footprint_from_kicad(text)
    ep = [p for p in fp["pads"] if p["designator"] == "39"]
    assert len(ep) == 1 and ep[0]["mask"] == "none"
    win = [pts for pts, layer in fp["regions"] if layer == AL.TOP_SOLDER]
    assert len(win) == 1
    xs, ys = [x for x, _ in win[0]], [y for _, y in win[0]]
    assert abs(max(xs) - min(xs) - 3.39) < 1e-6 and abs(max(ys) - min(ys) - 6.35) < 1e-6
    assert all(p.get("mask") != "none" for p in fp["pads"] if p["designator"] != "39")


OPA = {"name": "SO-20-1EP_test", "package_type": "gullwing", "pin_count": 20, "pitch": 1.27,
       "lead_span": {"min": 10.0, "max": 10.65}, "body_width": {"min": 7.4, "max": 7.6},
       "body_length": {"min": 12.6, "max": 13.0}, "terminal_width": {"min": 0.31, "max": 0.51},
       "terminal_length": {"min": 0.4, "max": 1.27}, "exposed_pad": {"length": {"nom": 3.0}, "width": {"nom": 4.0}}}


def test_a_minimum_copper_area_that_reaches_the_leads_is_held_with_its_reason():
    # OPA569DWP p19: "Thermal Land 299 mils x 510 mils, Minimum Area (7.59mm x 12.95mm) (Copper)" around a
    # "Thermal pad area: 140 mils x 176 mils (3.56mm x 4.47mm)": the minimum copper runs 0.17 mm into the SOIC-20W
    # lead pads, so it cannot be one pad (KiCad trims it to 6.045 x 12.09 by hand). Held, never built shorted
    p1 = [p for p in B.numbers(F.build(dict(OPA))[0])["pads"] if p[0] == "1"][0]
    land = {"pad_length": p1[3], "pad_width": p1[4], "pad_center": abs(p1[1]), "ep_across": 7.59, "ep_along": 12.95,
            "ep_mask_across": 3.56, "ep_mask_along": 4.47}
    _, rep = F.build(dict(OPA, land=land))
    why = F.shorts(rep)
    assert why.startswith("the pads would short: the maker's thermal land (7.59 x 12.95 mm) reaches the lead pads")
    assert "trimmed clear of the leads by hand" in why
    _, rep = F.build(dict(OPA, land=dict(land, ep_across=5.8, ep_along=12.09)))
    assert F.shorts(rep) is None and rep["ep_mask"] == (3.56, 4.47)              # clear of the leads: built


def test_other_shorts_keep_their_hint():
    rep = {"checks": [{"level": "error", "rule": "copper", "message": "pads 1 and 2 overlap by 0.100 mm"}],
           "ep_land": "datasheet", "ep_num": "21", "EP": (7.59, 12.95, "")}
    assert F.shorts(rep).endswith("(check pitch, pin_count and terminal_length against the drawing)")
