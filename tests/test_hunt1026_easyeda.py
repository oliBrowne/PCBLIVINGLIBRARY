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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
