"""Through-hole, polygon-pad and BGA parts through approval into Altium, and the footprint preview's view box.
Run:  python3 -m tests.test_altium_tht

Every spec in ecadlib/package_specs/advanced/ is approved into a workspace; the written .PcbLib is read back (altium_lib's
reader and pyaltiumlib) and compared pad by pad with the generated .kicad_mod: count, designators, outline (position,
size, rotation), drill (round / slot, plated), layers, mask expansion, paste windows, polygon-pad regions, keep-outs."""
import json, math, re, struct, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, footprint_gen as F, kicad_import as K, sexpr
from ecadlib.app import Workspace, footprint_svg

ADV = Path(__file__).resolve().parents[1] / "ecadlib" / "package_specs" / "advanced"
CLASSIC = Path(__file__).resolve().parents[1] / "ecadlib" / "package_specs"
TOL = 2 * 254 / 100_000_000 * 10           # a few internal units (2.54 nm each), in mm
SHAPE = {"rect": 2, "roundrect": 9, "circle": 1, "oval": 1}


def _corners(x, y, w, h, rot):
    a = math.radians(rot)
    return sorted((round(x + u * math.cos(a) - v * math.sin(a), 4), round(y + u * math.sin(a) + v * math.cos(a), 4))
                  for u in (-w / 2, w / 2) for v in (-h / 2, h / 2))


def _hole_box(x, y, length, width, rot):
    """Axis-aligned box of a slot (length along rot) or round hole, mm."""
    a = math.radians(rot)
    ex = abs((length - width) / 2 * math.cos(a)) + width / 2
    ey = abs((length - width) / 2 * math.sin(a)) + width / 2
    return round(x - ex, 4), round(y - ey, 4), round(x + ex, 4), round(y + ey, 4)


def _approve_all(d):
    ws = Workspace(d, providers=["digikey"])
    out = {}
    for p in sorted(ADV.glob("*.json")):
        spec = json.loads(p.read_text())
        res = ws.approve_footprint(spec)
        assert not [w for w in res["warnings"] if "PcbLib" in w], (p.stem, res["warnings"])
        lib = next(f for f in res["files"] if f.endswith(".PcbLib"))
        out[p.stem] = (spec, (Path(d) / res["path"]).read_text(), Path(d) / lib)
    return out


def test_every_advanced_spec_approves_into_a_pcblib_matching_the_kicad_footprint():
    import pyaltiumlib
    totals = {"pads": 0, "tht": 0, "npth": 0, "slots": 0, "windows": 0, "polygons": 0}
    with tempfile.TemporaryDirectory() as d:
        parts = _approve_all(d)
        assert len(parts) == 20, len(parts)
        for stem, (spec, kmod, lib) in parts.items():
            t = sexpr.parse(kmod)
            recs = AL.read_pcblib(lib)[0]["records"]
            apads = [r for r in recs if r["type"] == "pad"]
            regions = [r for r in recs if r["type"] == "region"]
            fills = [r for r in recs if r["type"] == "fill"]
            kpads = list(sexpr.find(t, "pad"))
            copper = [p for p in kpads if p[2] != "smd" or "F.Cu" in sexpr.first(p, "layers")[1:]]
            windows = [p for p in kpads if p not in copper]
            assert len(apads) == len(copper), (stem, len(apads), len(copper))
            for kp, ap in zip(copper, apads):                    # same order: pads are written as KiCad lists them
                at, size = sexpr.first(kp, "at"), sexpr.first(kp, "size")
                x, y, rot = float(at[1]), -float(at[2]), float(at[3]) if len(at) > 3 else 0.0
                w, h = float(size[1]), float(size[2])
                layers = sexpr.first(kp, "layers")[1:]
                ax, ay, aw, ah = (AL.to_mm(ap[k]) for k in ("x", "y", "w", "h"))
                assert ap["designator"] == kp[1], (stem, ap["designator"], kp[1])
                assert abs(ax - x) < TOL and abs(ay - y) < TOL, (stem, kp[1], (ax, ay), (x, y))
                if kp[3] == "custom":                            # the anchor pad (the shape is checked below)
                    assert ap["shape"] == 1 and abs(aw - w) < TOL and abs(ah - h) < TOL, (stem, kp[1])
                elif kp[2] != "np_thru_hole":
                    assert _corners(ax, ay, aw, ah, ap["rot"]) == _corners(x, y, w, h, rot), (stem, kp[1], (aw, ah, ap["rot"]), (w, h, rot))
                    assert ap["shape"] == SHAPE[kp[3]], (stem, kp[1], ap["shape"], kp[3])
                if kp[2] == "smd":
                    assert ap["layer"] == AL.TOP and ap["hole"] == 0, (stem, kp[1])
                    mm_ = sexpr.first(kp, "solder_mask_margin")
                    m = ap["raw"][4]
                    if mm_ or kp[3] == "custom":                 # manual mask expansion = KiCad's margin (0 when none)
                        assert m[102] == 2 and struct.unpack_from("<i", m, 90)[0] == AL.mm(mm_[1] if mm_ else 0), (stem, kp[1])
                    else:
                        assert m[102] == 1, (stem, kp[1])        # from the board rule, as before
                    paste_suppressed = ap["paste_mode"] == 2 and ap["paste"] < -max(ap["w"], ap["h"]) // 2
                    assert paste_suppressed == ("F.Paste" not in layers), (stem, kp[1], layers)
                    totals["pads"] += 1
                    continue
                d_ = sexpr.first(kp, "drill")
                dv = [float(v) for v in d_[1:] if v != "oval"]
                dw, dh = dv[0], dv[-1]
                assert ap["layer"] == AL.MULTI and ap["plated"] == (kp[2] == "thru_hole"), (stem, kp[1])
                assert abs(AL.to_mm(ap["hole"]) - min(dw, dh)) < TOL, (stem, kp[1])
                if abs(dw - dh) > 1e-9:
                    assert ap["hole_shape"] == 2 and abs(AL.to_mm(ap["slot"]) - max(dw, dh)) < TOL, (stem, kp[1])
                    kbox = _hole_box(x, y, max(dw, dh), min(dw, dh), rot + (0 if dw > dh else 90))
                    abox = _hole_box(ax, ay, AL.to_mm(ap["slot"]), AL.to_mm(ap["hole"]), ap["rot"] + ap["hole_rot"])
                    assert kbox == abox, (stem, kp[1], kbox, abox)
                    totals["slots"] += 1
                else:
                    assert ap["hole_shape"] == 0, (stem, kp[1])
                if kp[2] == "np_thru_hole":
                    assert abs(aw - dw) < TOL and abs(ah - dh) < TOL, (stem, kp[1])      # no copper ring: pad = hole
                    totals["npth"] += 1
                else:
                    totals["tht"] += 1
                m = ap["raw"][4]
                if not any("Mask" in l for l in layers):         # thermal vias: tented (mask opening closed)
                    assert m[102] == 2 and struct.unpack_from("<i", m, 90)[0] < -max(ap["w"], ap["h"]) // 2, (stem, kp[1])
                totals["pads"] += 1
            # paste windows -> Top Paste fills at the same place
            assert len([f for f in fills if f["layer"] == AL.TOP_PASTE]) == len(windows), stem
            for kp, f in zip(windows, [f for f in fills if f["layer"] == AL.TOP_PASTE]):
                at, size = sexpr.first(kp, "at"), sexpr.first(kp, "size")
                x, y, w, h = float(at[1]), -float(at[2]), float(size[1]), float(size[2])
                assert abs(AL.to_mm(f["x1"]) - (x - w / 2)) < TOL and abs(AL.to_mm(f["y2"]) - (y + h / 2)) < TOL, stem
                totals["windows"] += 1
            # polygon pads -> copper + solder-mask regions with the KiCad outline
            for kp in [p for p in copper if p[3] == "custom"]:
                at = sexpr.first(kp, "at")
                ox, oy = float(at[1]), float(at[2])
                prim = sexpr.first(sexpr.first(kp, "primitives"), "gr_poly")
                want = [(round(ox + float(c[1]), 4), round(-(oy + float(c[2])), 4)) for c in sexpr.find(sexpr.first(prim, "pts"), "xy")]
                for layer in (AL.TOP, AL.TOP_SOLDER):
                    got = [[(round(AL.to_mm(a), 4), round(AL.to_mm(b), 4)) for a, b in r["pts"]] for r in regions if r["layer"] == layer]
                    assert want in got, (stem, layer, want, got)
                totals["polygons"] += 1
            zones = list(sexpr.find(t, "zone"))
            assert len([r for r in regions if r["layer"] == AL.KEEPOUT]) == len(zones), stem
            # independent reader: pads, designators and drills
            pr = [r for r in pyaltiumlib.read(str(lib)).Parts[0].Records if type(r).__name__ == "PcbPad"]
            assert [r.designator for r in pr] == [p[1] for p in copper], stem
            assert all(abs(float(r.hole_size) - ap["hole"] / 10000) < 0.01 for r, ap in zip(pr, apads)), stem
    # 1069 pads in 20 parts: 49 plated through-hole (4 of them thermal vias), 3 NPTH, 5 slots, 14 paste windows, 1 polygon
    assert totals == {"pads": 1069, "tht": 49, "npth": 3, "slots": 5, "windows": 14, "polygons": 1}, totals


def test_through_hole_header_was_refused_before_and_now_writes_a_pcblib():
    spec = json.loads((ADV / "PinHeader_2x05_P2.54mm_Vertical.json").read_text())
    fp, notes = F.altium_footprint(spec)
    pads = fp["pads"]
    assert len(pads) == 10 and all(p["layer"] == AL.MULTI and p["hole"] == 1.0 and p["plated"] for p in pads), pads
    assert pads[0]["shape"] == "rect" and all(p["shape"] == "round" for p in pads[1:])
    assert (pads[0]["x"], pads[0]["y"], pads[1]["x"], pads[2]["y"]) == (0, 0, 2.54, -2.54)    # pin 1 at 0,0; y up
    fp2, rep = AL.footprint_from_spec(spec)                   # the function approval calls
    assert fp2["pads"] == pads


def test_polygon_pad_is_an_anchor_pad_plus_regions_not_a_bounding_rectangle():
    spec = json.loads((ADV / "Example_Custom_LPad_Slot.json").read_text())
    fp, notes = F.altium_footprint(spec)
    p1 = fp["pads"][0]
    assert p1["designator"] == "1" and p1["w"] < 1.1 and p1["shape"] == "round" and p1["mask"] == 0.0, p1
    copper = [r for r in fp["regions"] if r[1] == AL.TOP]
    assert len(copper) == 1 and len(copper[0][0]) == 6                         # the L, all six corners
    xs, ys = [x for x, _ in copper[0][0]], [y for _, y in copper[0][0]]
    assert (min(xs), max(xs), min(ys), max(ys)) == (-3, 1, -2.5, 2.5)          # y up, as the spec's table
    assert not [n for n in notes if "bounding" in n], notes
    vias = [p for p in fp["pads"] if p["designator"] == "1" and p.get("hole")]
    assert len(vias) == 4 and all(v["mask"] == "none" for v in vias)            # tented, as in KiCad (*.Cu only)


def test_kicad_import_offset_and_curved_custom_fallback():
    sq = [(0, 0), (2, 0), (2, 1), (0, 1)]
    assert [tuple(round(v, 6) for v in q) for q in K._offset(sq, 0.1)] == [(-0.1, -0.1), (2.1, -0.1), (2.1, 1.1), (-0.1, 1.1)]
    assert [tuple(round(v, 6) for v in q) for q in K._offset(sq[::-1], 0.1)] == [(-0.1, 1.1), (2.1, 1.1), (2.1, -0.1), (-0.1, -0.1)]
    notes = []
    fp = {"pads": [], "regions": [], "fills": [], "tracks": []}
    arc = sexpr.parse('(pad "5" smd custom (at 0 0) (size 0.5 0.5) (layers "F.Cu" "F.Mask") '
                      '(primitives (gr_arc (start 0 0) (mid 1 1) (end 2 0) (width 0.2))))')
    K._pad(fp, arc, notes)
    assert fp["pads"][0]["shape"] == "rect" and any("bounding" in n for n in notes), notes


def test_mask_expansion_bytes_read_by_pyaltiumlib():
    import pyaltiumlib
    with tempfile.TemporaryDirectory() as d:
        fp = {"name": "M", "pads": [{"designator": "1", "x": 0, "y": 0, "w": 1, "h": 1, "shape": "round", "mask": 0.05},
                                    {"designator": "2", "x": 2, "y": 0, "w": 1, "h": 1, "shape": "round", "layer": AL.MULTI, "hole": 0.5,
                                     "mask": "none"},
                                    {"designator": "3", "x": 4, "y": 0, "w": 1, "h": 1, "shape": "round"}],
              "tracks": [], "fills": [], "regions": []}
        path = AL.write_pcblib(Path(d) / "m.PcbLib", [fp])
        pr = [r for r in pyaltiumlib.read(str(path)).Parts[0].Records if type(r).__name__ == "PcbPad"]
        assert pr[0].expansion_manual_solder_mask == 2 and abs(float(pr[0].expansion_solder_mask) - 0.05 / 0.0254) < 0.01
        assert pr[1].expansion_manual_solder_mask == 2 and float(pr[1].expansion_solder_mask) < -0.5 / 0.0254
        assert pr[2].expansion_manual_solder_mask == 1                            # unchanged default: from the rule


def test_classic_smd_footprints_keep_rule_mask_and_have_no_notes():
    for p in sorted(CLASSIC.glob("*.json")):
        spec = json.loads(p.read_text())
        fp, rep = AL.footprint_from_spec(spec)
        assert "altium_notes" not in rep and not any("mask" in q for q in fp["pads"]), p.stem
        assert not any(r[1] in (AL.TOP, AL.TOP_SOLDER, AL.KEEPOUT) for r in fp["regions"]), p.stem


# ---------------------------------------------------------------- preview
def _viewbox(svg):
    x, y, w, h = map(float, re.search(r'viewBox="([^"]+)"', svg).group(1).split())
    return x, y, x + w, y + h


def test_preview_fits_through_hole_connectors_and_matches_smd():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, providers=["digikey"])
        for name in ("JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical", "PinHeader_2x05_P2.54mm_Vertical", "Example_Custom_LPad_Slot",
                     "USB_C_Receptacle_HRO_TYPE-C-31-M-12"):
            spec = json.loads((ADV / f"{name}.json").read_text())
            res = ws.preview_footprint(spec)
            bb, vb = res["report"]["courtyard_bbox"], _viewbox(res["svg"])
            t = sexpr.parse(res["kicad_mod"])
            assert vb[0] <= bb[0] and vb[1] <= bb[1] and vb[2] >= bb[2] and vb[3] >= bb[3], (name, vb, bb)
            for p in sexpr.pads(t):                                       # every pad inside the view
                assert vb[0] < p["x"] - p["w"] / 2 and p["x"] + p["w"] / 2 < vb[2], (name, p)
                assert vb[1] < p["y"] - p["h"] / 2 and p["y"] + p["h"] / 2 < vb[3], (name, p)
            # tight: no more than the 0.6 mm margin around the courtyard (which holds every pad and the body)
            assert all(abs(a - b) <= 0.6 + 1e-6 for a, b in zip(vb, (bb[0], bb[1], bb[2], bb[3]))), (name, vb, bb)
            assert res["svg"].count('class="hole"') == len([p for p in sexpr.find(t, "pad") if sexpr.first(p, "drill")]), name
        # the JST XH has pin 1 at the origin: the view is not centred on 0,0
        res = ws.preview_footprint(json.loads((ADV / "JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical.json").read_text()))
        vb = _viewbox(res["svg"])
        assert (vb[0] + vb[2]) / 2 > 3, vb
        # polygon pad drawn as its polygon
        res = ws.preview_footprint(json.loads((ADV / "Example_Custom_LPad_Slot.json").read_text()))
        assert '<g class="pad p1"><polygon points="-3,-2.5 1,-2.5 1,-0.5 -1,-0.5 -1,2.5 -3,2.5"/>' in res["svg"], res["svg"][:600]
        # classic SMD: centred courtyard, same box as before
        spec = json.loads((CLASSIC / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
        res = ws.preview_footprint(spec)
        cx, cy = (float(v) / 2 for v in res["report"]["courtyard"])
        assert all(abs(a - b) < 1e-3 for a, b in zip(_viewbox(res["svg"]), (-cx - 0.6, -cy - 0.6, cx + 0.6, cy + 0.6))), _viewbox(res["svg"])


def test_report_only_svg_uses_the_courtyard_box():
    spec = json.loads((ADV / "JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical.json").read_text())
    _, rep = F.build(spec)
    bb, vb = rep["courtyard_bbox"], _viewbox(footprint_svg(rep))
    assert all(abs(a - b) <= 0.6 + 1e-6 for a, b in zip(vb, bb)), (vb, bb)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
