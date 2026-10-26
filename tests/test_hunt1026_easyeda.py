"""2026-10-26 bug hunt (easyeda): EasyEDA conversion, part sources and the HTTP cache. Run:
    python -m tests.test_hunt1026_easyeda"""
import math

from ecadlib import easyeda_convert as EC, sexpr

HEAD = {"x": 4000, "y": 3000}


def _fp(shapes, notes=None):
    return EC.footprint_to_kicad({"head": HEAD, "shape": shapes}, "T", [] if notes is None else notes)


def _pads(text):
    return {str(p[1]): p for p in sexpr.find(sexpr.parse(text), "pad")}


def _rot(pad):
    at = sexpr.first(pad, "at")
    return float(at[3]) % 360 if len(at) > 3 else 0.0


def _rect_pts(cx, cy, w, h, kdeg):
    """The four corners of a w x h pad at (cx, cy), turned kdeg counter-clockwise on screen (KiCad's sense; the
    canvas is y down), in EasyEDA's order (local -x-y, +x-y, +x+y, -x+y)."""
    a = math.radians(kdeg)
    out = []
    for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out += [cx + x * math.cos(a) + y * math.sin(a), cy - x * math.sin(a) + y * math.cos(a)]
    return " ".join(f"{v:.4f}" for v in out)


def _mod(a, p=180):
    d = a % p
    return min(d, p - d)


def test_rotated_rect_pad_follows_its_own_outline():
    # EasyEDA writes a RECT pad's corners beside its rotation; a 45 degree pad whose rotation field says the other way
    # round must not be built mirrored (the outline is what EasyEDA draws)
    for kdeg, field in ((45, 315), (45, 45), (30, -30), (120, 60), (0, 0), (90, 90), (90, 0)):
        pts = _rect_pts(4000, 3000, 4, 2, kdeg)
        notes = []
        pad = _pads(_fp([f"PAD~RECT~4000~3000~4~2~1~~1~0~{pts}~{field}~g~0~~Y~0"], notes))["1"]
        assert _mod(_rot(pad) - kdeg) < 0.01, (kdeg, field, _rot(pad), notes)
        assert any("does not match the pad's own outline" in n for n in notes) == (_mod(kdeg - field) > 0.5), notes


def test_rotated_oval_pad_follows_its_axis():
    # an OVAL's points are its long axis' two ends
    for kdeg, field in ((45, 315), (45, 45), (0, 90)):
        a = math.radians(kdeg)
        ln = 6 - 2                                              # w - h: the axis between the two end circles
        x0, y0 = 4000 - ln / 2 * math.cos(a), 3000 + ln / 2 * math.sin(a)
        x1, y1 = 4000 + ln / 2 * math.cos(a), 3000 - ln / 2 * math.sin(a)
        pad = _pads(_fp([f"PAD~OVAL~4000~3000~6~2~1~~1~0~{x0:.4f} {y0:.4f} {x1:.4f} {y1:.4f}~{field}~g~0~~Y~0"]))["1"]
        assert _mod(_rot(pad) - kdeg) < 0.01, (kdeg, field, _rot(pad))


def test_pad_outline_of_another_size_is_ignored():
    # points that are not this pad's outline (another size): the rotation field stands
    pts = _rect_pts(4000, 3000, 8, 2, 0)
    pad = _pads(_fp([f"PAD~RECT~4000~3000~4~2~1~~1~0~{pts}~45~g~0~~Y~0"]))["1"]
    assert _rot(pad) == 45


def _live(lcsc):
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / "fixtures" / "easyeda_live" / f"{lcsc}.json").read_text(encoding="utf-8"))


def _xy(pad):
    at = sexpr.first(pad, "at")
    return float(at[1]), float(at[2])


def test_footprint_without_head_origin_is_centred_on_its_pads():
    # a head with no x / y put the origin at the canvas' 0, 0: every pad a metre away (4000 x 0.254 mm), unnoticed
    # by footprint_check; the pads' centre is the origin instead, and the notes say so
    import copy
    from ecadlib import easyeda_3d as E3
    ds = copy.deepcopy(_live("C5423")["result"]["packageDetail"]["dataStr"])
    good = _pads(EC.footprint_to_kicad(ds, "T", []))
    ref = E3.model_ref(ds)
    del ds["head"]["x"], ds["head"]["y"]
    notes = []
    bad = _pads(EC.footprint_to_kicad(ds, "T", notes))
    assert any("no origin" in n for n in notes), notes
    for k, p in good.items():
        (x0, y0), (x1, y1) = _xy(p), _xy(bad[k])
        assert abs(x0 - x1) < 0.01 and abs(y0 - y1) < 0.01, (k, _xy(p), _xy(bad[k]))
    r2 = E3.model_ref(ds)                        # the 3D model is placed from the same origin
    assert all(abs(a - b) < 0.01 for a, b in zip(ref["origin"], r2["origin"])), (ref["origin"], r2["origin"])


def _all_pins(node):
    out = []
    for c in node:
        if isinstance(c, list) and c:
            out += [c] if c[0] == "pin" else _all_pins(c)
    return out


def _sym_pins(shapes):
    t = sexpr.parse(EC.symbol_to_kicad({"head": {"x": 400, "y": 300}, "shape": shapes}, "X", "", {}, []))
    return {str(sexpr.first(p, "number")[1]): p for p in _all_pins(t)}


def test_symbol_pin_inversion_and_clock_marks_are_kept():
    # EasyEDA's pin: segment 5 is the inversion circle, segment 6 the clock mark, each "1" when shown (easyeda2kicad
    # reads them the same way); they were all written as plain lines, so an active-low pin lost its circle
    base = "P~show~1~{n}~355~285~180~gge{n}~0^^355~285^^M355,285h10~#880000^^1~368~289~0~P{n}~start~~~#0000FF^^" \
           "1~364~284~0~{n}~end~~~#0000FF^^{d}~362~285^^{c}~M 365 288 L 368 285 L 365 282"
    pins = _sym_pins([base.format(n=1, d=0, c=0), base.format(n=2, d=1, c=0), base.format(n=3, d=0, c=1),
                      base.format(n=4, d=1, c=1)])
    assert {k: str(p[2]) for k, p in pins.items()} == {"1": "line", "2": "inverted", "3": "clock", "4": "inverted_clock"}
    from ecadlib import kicad_import as K       # and Altium's converter reads them
    sym = K.convert_symbol([K._find_symbol(sexpr.parse(EC.symbol_to_kicad(
        {"head": {"x": 400, "y": 300}, "shape": [base.format(n=2, d=1, c=0)]}, "X", "", {}, [])), "X")], "X", None, [], None, [])
    assert [pr[1].get("shape") for pr in sym["prims"] if pr[0] == "pin"] == ["inverted"]


def test_real_symbols_keep_plain_pins():
    for lcsc in ("C5423", "C7593"):
        r = _live(lcsc)["result"]
        t = sexpr.parse(EC.symbol_to_kicad(r["dataStr"], "X", "", {}, []))
        assert {str(p[2]) for p in _all_pins(t)} == {"line"}, lcsc


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
