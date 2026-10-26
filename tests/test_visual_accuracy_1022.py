"""Visual accuracy review 2026-10-22: every generated footprint rendered and compared with KiCad's own file on every
layer (copper, paste, mask, silk, fab, courtyard, texts, 3D). These tests pin the general fixes that review led to."""
import re, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, kicad_export as X
from ecadlib import easyeda_convert as EC, footprint_check as FC, kicad_import as K, sexpr, stdparts

# EasyEDA's SOT-89 middle pad: a 0.9 mm lead pad joined to a 1.73 mm tab (an L / T shape), in 10 mil units around 4000
SOT89_EE = {"head": {"x": 4000, "y": 3000}, "shape": [
    "PAD~RECT~3992.323~2994.094~5.118~3.543~1~~1~0~3989.764 2992.323 3994.882 2992.323 3994.882 2995.866 3989.764 2995.866~0~g1~0~~Y~0",
    "PAD~POLYGON~3998.819~3000~18.11~6.823~1~~2~0~3989.764 2998.228 3995.571 2998.228 3995.571 2996.589 4007.874 2996.589 "
    "4007.874 3003.411 3995.571 3003.411 3995.571 3001.772 3989.764 3001.772~0~g2~0~~Y~0",
    "PAD~RECT~3992.323~3005.906~5.118~3.543~1~~3~0~3989.764 3004.134 3994.882 3004.134 3994.882 3007.677 3989.764 3007.677~0~g3~0~~Y~0"]}


def _pads(text):
    return {p[1]: p for p in sexpr.find(sexpr.parse(text), "pad")}


def test_easyeda_polygon_pad_keeps_its_shape():
    """A non-rectangular EasyEDA polygon pad was written as its bounding rectangle: on SOT-89 that put pad 2's
    1.73 mm tab height along the lead between pins 1 and 3 (0.18 mm from them instead of 0.6). It is now a KiCad
    custom pad with the polygon itself."""
    notes = []
    text = EC.footprint_to_kicad(SOT89_EE, "SOT-89-3_EE", notes)
    p2 = _pads(text)["2"]
    assert p2[3] == "custom", p2
    poly = sexpr.first(sexpr.first(p2, "primitives"), "gr_poly")
    ax, ay = (float(v) for v in sexpr.first(p2, "at")[1:3])
    pts = [(round(float(q[1]) + ax, 2), round(float(q[2]) + ay, 2)) for q in sexpr.first(poly, "pts")[1:]]
    assert len(pts) == 8 and (-2.6, -0.45) in pts and (2.0, 0.87) in pts, pts
    fc = [c for c in FC.check(text) if c[0] == "error"]
    assert not fc, fc
    assert not any("bounding rectangle" in n for n in notes), notes
    fp, _ = K.convert_footprint(text, "SOT89_EE")          # Altium: anchor pad + copper / mask / paste regions
    assert len([p for p in fp["pads"] if p["designator"] == "2"]) == 1 and fp.get("regions"), fp.keys()


def test_easyeda_rectangular_polygon_is_still_a_plain_pad():
    data = {"head": {"x": 100, "y": 100}, "shape": [
        "PAD~POLYGON~140~100~4~4~1~~3~0~138 98 142 98 142 102 138 102~0~g3~0~~~0"]}
    p = _pads(EC.footprint_to_kicad(data, "X"))["3"]
    assert p[3] == "rect" and sexpr.first(p, "size")[1:] == ["1.016", "1.016"], p


def test_sot89_middle_pad_is_lead_plus_tab_not_one_block():
    """The standard table held SOT-89's pad 2 as the bounding box of KiCad's custom pad (4.6 x 1.73 mm), so the
    tab's height ran along the lead and the copper came 0.18 mm from pins 1 and 3. KiCad and the makers' lands keep
    the lead pad as narrow as pins 1 and 3 (0.6 mm gap along the lead, 0.25 mm at the tab's corner)."""
    text = stdparts.build("SOT-89-3")[0]
    pads = {p["num"]: p for p in FC._pad_rects(sexpr.parse(text))}
    assert pads["2"]["polys"], pads["2"]          # (footprint_check keeps a custom pad's copper as polygons)
    gap = min(FC._poly_gap(pads["2"], pads[n]) for n in ("1", "3"))
    assert gap >= 0.25, gap                     # KiCad's own corner gap; 0.18 before
    xs = [x for q in pads["2"]["polys"] for x, _ in q]
    assert abs(min(xs) + 2.6) < 0.01 and abs(max(xs) - 2.0) < 0.01, xs        # still runs from the lead to the tab


def _body(name):
    b = stdparts.spec(name)["pad_table"]["body"]
    return b["x1"], b["y1"], b["x2"], b["y2"]


def test_axial_diode_body_sits_between_its_holes():
    """DO-35 / DO-41 / DO-201AD 'Horizontal' bodies were the whole pad-to-pad span (the KiCad fab drawing's box with
    its leads), so the fab outline and the 3D body covered both holes. The body is the glass / plastic part only."""
    for n in stdparts.packages():
        if not (n.startswith("D_DO-") and n.endswith("_Horizontal")):
            continue
        x1, _, x2, _ = _body(n)
        holes = [float(sexpr.first(p, "at")[1]) for p in sexpr.find(sexpr.parse(stdparts.build(n)[0]), "pad")]
        assert all(not (x1 <= h <= x2) for h in holes), (n, (x1, x2), holes)
        m = re.search(r"P([\d.]+)mm", n)
        assert x2 - x1 < float(m.group(1)) - 1.0, (n, x2 - x1)


def test_dpak_body_leaves_the_leads_out():
    """TO-252 / TO-263 bodies ran out to the lead tips (-5.81 / -9.325 mm): the fab outline and the 3D body sat over
    pads 1 and 3. The body is the moulded part with its tab; the lead pads lie outside it."""
    for n in stdparts.packages():
        if not re.match(r"TO-2(52|63)-", n):
            continue
        x1, y1, x2, y2 = _body(n)
        pads = list(sexpr.find(sexpr.parse(stdparts.build(n)[0]), "pad"))
        big = max(pads, key=lambda p: float(sexpr.first(p, "size")[1]) * float(sexpr.first(p, "size")[2]))
        for p in pads:
            if p is big or "F.Cu" not in sexpr.first(p, "layers"):     # the tab, paste windows
                continue
            x, y = (float(v) for v in sexpr.first(p, "at")[1:3])
            w = float(sexpr.first(p, "size")[1])
            assert x + w / 2 <= x1 + 0.6, (n, p[1], x + w / 2, x1)        # the lead pad stays outside the body


def _paste_cover(text, num):
    """Fraction of pad num's copper covered by paste (its own paste layer, or the paste-only windows on it)."""
    pads = list(sexpr.find(sexpr.parse(text), "pad"))
    area = lambda q: float(sexpr.first(q, "size")[1]) * float(sexpr.first(q, "size")[2])
    p = max((q for q in pads if q[1] == num and "F.Cu" in sexpr.first(q, "layers")), key=area)     # the tab, not a lead
    x, y = (float(v) for v in sexpr.first(p, "at")[1:3])
    w, h = (float(v) for v in sexpr.first(p, "size")[1:3])
    if "F.Paste" in sexpr.first(p, "layers"):
        return 1.0
    cov = 0.0
    for q in pads:
        if list(sexpr.first(q, "layers")[1:]) == ["F.Paste"]:
            qx, qy = (float(v) for v in sexpr.first(q, "at")[1:3])
            if abs(qx - x) <= w / 2 and abs(qy - y) <= h / 2:
                qw, qh = (float(v) for v in sexpr.first(q, "size")[1:3])
                cov += qw * qh
    return cov / (w * h)


def test_dpak_tab_paste_is_split_into_windows():
    """A 6.4 x 5.8 mm DPAK tab printed with full paste floats the part and voids (KiCad splits it; KLC F6.3 and
    IPC-7525 ask 50-80 % on thermal pads). Pad-table builds now split any tab / exposed pad whose paste was not given."""
    for n in ("TO-252-2", "TO-263-2", "TO-252-5_TabPin3"):
        text = stdparts.build(n)[0]
        tab = max(((p[1], float(sexpr.first(p, "size")[1]) * float(sexpr.first(p, "size")[2])) for p in sexpr.find(sexpr.parse(text), "pad")
                   if "F.Cu" in sexpr.first(p, "layers")), key=lambda t: t[1])[0]
        c = _paste_cover(text, tab)
        assert 0.5 <= c <= 0.8, (n, c)
    assert _paste_cover(stdparts.build("SOT-223")[0], "4" if "4" in _pads(stdparts.build("SOT-223")[0]) else "2") == 1.0   # 2.15 mm tab: full


def test_pad_table_exposed_pad_gets_windows_unless_paste_is_given():
    from ecadlib import footprint_gen as F
    pads = [{"number": str(i + 1), "x": -1.4625 if i < 4 else 1.4625, "y": -0.75 + 0.5 * (i % 4), "w": 0.8, "h": 0.25} for i in range(8)]
    ep = {"number": "9", "x": 0, "y": 0, "w": 1.7, "h": 2.4}
    spec = {"name": "DFN-8-1EP_T", "package_type": "custom", "pin_count": 9,
            "pad_table": {"unit": "mm", "origin": "center", "pads": pads + [ep], "body": {"x1": -1.5, "y1": -1.5, "x2": 1.5, "y2": 1.5}}}
    text = F.build(spec)[0]
    assert 0.5 <= _paste_cover(text, "9") <= 0.8, _paste_cover(text, "9")
    spec["pad_table"]["pads"][-1] = dict(ep, paste="full")                 # the datasheet's stencil says full: kept
    assert _paste_cover(F.build(spec)[0], "9") == 1.0
    assert all(_paste_cover(text, str(i)) == 1.0 for i in range(1, 9))     # signal pads keep their paste


def _silk_top_and_ref(text):
    """(top of the silkscreen under the Reference text's width, bottom edge of the Reference) in KiCad y (down)."""
    t = sexpr.parse(text)
    ref = next(g for g in t if isinstance(g, list) and g[:2] == ["fp_text", "reference"])
    x, y = (float(v) for v in sexpr.first(ref, "at")[1:3])
    size = float(sexpr.first(sexpr.first(sexpr.first(ref, "effects"), "font"), "size")[2])
    half = 0.6 * size * 5 / 2                                     # "REF**" in KiCad's stroke font
    ys = []
    for g in t:
        if isinstance(g, list) and g and g[0] in ("fp_line", "fp_poly", "fp_rect") and sexpr.first(g, "layer")[1] == "F.SilkS":
            w = float(sexpr.first(sexpr.first(g, "stroke"), "width")[1])
            pts = [sexpr.first(g, k) for k in ("start", "end")] if g[0] != "fp_poly" else sexpr.find(sexpr.first(g, "pts"), "xy")
            pts = [(float(p[1]), float(p[2])) for p in pts if p]
            if min(a for a, _ in pts) - w / 2 < x + half and max(a for a, _ in pts) + w / 2 > x - half:
                ys += [b - w / 2 for _, b in pts]
    return min(ys), y + size / 2


def test_reference_clears_the_pin1_triangle():
    """KiCad DRC (silk_overlap) on every generated footprint: on 42 small parts (SC-70, SOT-363, SOT-883, 2 x 2 mm
    DFN, WLCSP ...) the REF** text sat on the pin-1 triangle, which stands above pad 1 past the courtyard. The
    Reference now goes above the highest silkscreen line or mark."""
    from ecadlib import footprint_gen as F
    for n in ("SOT-363_SC-70-6", "SOT-323_SC-70", "SOT-883", "DFN-8-1EP_2x2mm_P0.5mm_EP0.6x1.2mm", "TDFN-8_1.4x1.6mm_P0.4mm",
              "SOIC-8_3.9x4.9mm_P1.27mm", "TO-252-2"):
        top, ref_bottom = _silk_top_and_ref(stdparts.build(n)[0])
        assert ref_bottom <= top - 0.1, (n, ref_bottom, top)
    pads = [{"number": "1", "x": -0.5, "y": -0.5, "w": 0.3, "h": 0.6}, {"number": "2", "x": 0.5, "y": -0.5, "w": 0.3, "h": 0.6},
            {"number": "3", "x": 0.5, "y": 0.5, "w": 0.3, "h": 0.6}, {"number": "4", "x": -0.5, "y": 0.5, "w": 0.3, "h": 0.6}]
    spec = {"name": "TINY-4", "package_type": "custom", "pin_count": 4,
            "pad_table": {"unit": "mm", "origin": "center", "pads": pads, "body": {"x1": -0.6, "y1": -0.6, "x2": 0.6, "y2": 0.6}}}
    top, ref_bottom = _silk_top_and_ref(F.build(spec)[0])
    assert ref_bottom <= top - 0.1, (ref_bottom, top)


def _std_symbol(mpn, fp, kind=None):
    k, _ = stdparts.symbol_kind(kind, mpn, fp)
    text, name = stdparts.symbol_text(k, mpn)
    return text, K.convert_symbol([K._find_symbol(sexpr.parse(text), name)], mpn, fp)


def test_transistor_pin_names_are_hidden():
    """KiCad 8 render of AO3401A: the G / S / D pin names were drawn across the MOSFET's gate and channel lines.
    KiCad's own Q_*_GSD symbols hide them; the pin numbers stay."""
    for mpn in ("AO3401A", "2N7002", "MMBT3904"):
        text, sym = _std_symbol(mpn, "SOT-23")
        sym_node = sexpr.first(sexpr.parse(text), "symbol")
        names = sexpr.first(sym_node, "pin_names")
        assert names and "hide" in names, (mpn, names)
        assert "pin_numbers" not in str(sym_node[:4]), mpn


def test_value_label_clears_the_bottom_pins():
    """KiCad 8 render of AMS1117-3.3: the Value text sat on the GND pin at the bottom (the label was put 8 units
    under the body, but the pin runs 10 units further). Labels now clear the pin ends, top and bottom."""
    for mpn, fp in (("AMS1117-3.3", "SOT-223-3_TabPin2"), ("AO3401A", "SOT-23")):
        _, sym = _std_symbol(mpn, fp)
        (rx, ry), (vx, vy) = sym["label_at"]
        ends = []
        for pr in sym["prims"]:
            if pr[0] == "pin":
                _, p, side, x, y, L, *_ = pr
                ends.append(y - L if side == "bottom" else y + L if side == "top" else y)
        assert vy < min(ends) - 2, (mpn, vy, min(ends))
        assert ry > max(ends), (mpn, ry, max(ends))


def test_vertical_passive_labels_sit_beside_the_body():
    """KiCad 8 render of the demo's 10k / 100nF: R? and the value were written across the vertical pin line. Two-pin
    parts with top / bottom pins get their labels to the right of the body, as KiCad's Device:R does."""
    for kind, mpn, fp in (("R", "RC0603FR-0710KL", "R_0603_1608Metric"), ("C", "CL05B104KO5NNNC", "C_0402_1005Metric")):
        _, sym = _std_symbol(mpn, fp, kind)
        pins = [pr for pr in sym["prims"] if pr[0] == "pin"]
        assert len(pins) == 2 and all(pr[2] in ("top", "bottom") for pr in pins), (mpn, pins)
        right = max(v for pr in sym["prims"] if pr[0] != "pin" for v in K._prim_xs(pr)[0])
        (rx, ry), (vx, vy) = sym["label_at"]
        assert rx > right and vx > right and ry > vy, (mpn, sym["label_at"], right)


def test_kicad_export_keeps_altium_label_corner():
    """An Altium parameter is placed by its bottom-left corner; KiCad centres a property unless told otherwise, so the
    exported labels moved half their width left, back over the body. They are written justify left bottom."""
    _, sym = _std_symbol("RC0603FR-0710KL", "R_0603_1608Metric", "R")
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d) / "R.SchLib", [sym])
        k = X.symbol_from_schlib(Path(d) / "R.SchLib", sym["name"], [])
    for prop in ("Reference", "Value"):
        node = next(p for p in sexpr.find(k, "property") if p[1] == prop)
        assert sexpr.first(sexpr.first(node, "effects"), "justify")[1:] == ["left", "bottom"], node


def test_easyeda_drilled_polygon_keeps_its_hole():
    """Greptile on #162: the custom pad's anchor moved to the polygon's interior point, and KiCad drills at the anchor,
    so a drilled polygon pad's hole left EasyEDA's spot. The anchor stays on the hole for a drilled pad."""
    f = SOT89_EE["shape"][1].split("~")
    f[6], f[9] = "11", "1.5"                                 # multi-layer, hole radius 1.5 (x 10 mil)
    data = dict(SOT89_EE, shape=[SOT89_EE["shape"][0], "~".join(f), SOT89_EE["shape"][2]])
    p2 = _pads(EC.footprint_to_kicad(data, "EE_THT"))["2"]
    assert p2[2] == "thru_hole" and p2[3] == "custom", p2
    x, y = (float(v) for v in sexpr.first(p2, "at")[1:3])
    assert abs(x - (3998.819 - 4000) * 0.254) < 1e-3 and abs(y) < 1e-3, (x, y)
    assert abs(float(sexpr.first(p2, "size")[1]) - 0.762) < 1e-3, p2


def test_thermal_paste_rule_leaves_a_given_margin_and_rotated_pads():
    """Greptile on #162: a big pad with the source's paste_margin, or one turned 45 deg (axis-aligned windows would
    fall off its copper), went to windows. Both keep their full, source-given paste."""
    from ecadlib import footprint_gen as F
    pads = [{"number": str(i + 1), "x": -1.4625 if i < 4 else 1.4625, "y": -0.75 + 0.5 * (i % 4), "w": 0.8, "h": 0.25} for i in range(8)]
    spec = {"name": "DFN-8-1EP_T", "package_type": "custom", "pin_count": 9,
            "pad_table": {"unit": "mm", "origin": "center", "body": {"x1": -1.5, "y1": -1.5, "x2": 1.5, "y2": 1.5},
                          "pads": pads + [{"number": "9", "x": 0, "y": 0, "w": 1.7, "h": 2.4, "paste_margin": -0.1}]}}
    text = F.build(spec)[0]
    p9 = [p for p in sexpr.find(sexpr.parse(text), "pad") if p[1] == "9"]
    assert len(p9) == 1 and "F.Paste" in str(p9[0]), p9
    assert sexpr.first(p9[0], "solder_paste_margin")[1] == "-0.1", p9
    tab = {"number": "3", "x": 0, "y": 0, "w": 4, "h": 1, "rotation": 45}
    spec2 = {"name": "ROT_T", "package_type": "custom", "pin_count": 3,
             "pad_table": {"unit": "mm", "origin": "center", "body": {"x1": -3, "y1": -3, "x2": 3, "y2": 3},
                           "pads": [{"number": "1", "x": -4, "y": 0, "w": 1, "h": 1}, {"number": "2", "x": 4, "y": 0, "w": 1, "h": 1}, tab]}}
    p3 = [p for p in sexpr.find(sexpr.parse(F.build(spec2)[0]), "pad") if p[1] == "3"]
    assert len(p3) == 1 and "F.Paste" in str(p3[0]), p3
