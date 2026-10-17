"""Regression tests for the 2026-10-18 review of the KiCad output path (kicad_export, kicad_dblib, kicad_pcm).
Run:  python3 -m tests.test_review_1018_kicad"""
import json, math, shutil, sqlite3, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_lib as AL, kicad_dblib as KD, kicad_export as X, kicad_pcm as KP, sexpr

ROOT = Path(__file__).parent.parent
GOLDEN = ROOT / "tests" / "fixtures" / "kicad_golden"
_PLAN = {}


def _tmp():
    return Path(tempfile.mkdtemp(prefix="ecadlib-1018-kicad-"))


def _plan():
    """tests.test_kicad_export's librarian run (made once), copied per test: (plan.json, http)."""
    if "dir" not in _PLAN:
        from tests import test_kicad_export as TX
        plan, _, http = TX._plan(_tmp())
        _PLAN.update(dir=plan.parent, http=http)
    d = _tmp() / "plan"
    shutil.copytree(_PLAN["dir"], d)
    return d / "plan.json", _PLAN["http"]


def _quiet(*a, **k):
    pass


# ---------------------------------------------------------------- KiCad 10 (point ...) markers
def test_kicad10_footprint_points_are_not_written_for_kicad8_or_7():
    # KiCad 10 footprints (DIP-8, TO-220 in kicad-footprints master) carry (point (at ..) (size ..) (layer ..)) markers;
    # KiCad 7, 8 and 9 have no such token, so the whole footprint fails to load
    for name in ("DIP-8_W7.62mm", "TO-220-3_Vertical"):
        text = (GOLDEN / f"{name}.kicad_mod").read_text(encoding="utf-8")
        assert "(point" in text
        t, _ = X.footprint_tree(text, name, "lib", [])
        k8 = sexpr.dumps(t) + "\n"
        assert not list(sexpr.find(t, "point")), name
        assert "(point" not in KP.footprint_kicad7(k8), name
        assert sexpr.pads(t) == sexpr.pads(sexpr.parse(text)), name


def test_every_golden_footprint_loads_in_kicad7_pcbnew():
    from tests import test_kicad_real as R
    if not R.pcbnew_python() or (R.kicad_major() or 7) >= 8:
        return R._skip("KiCad 7's pcbnew")
    d = _tmp() / "k7.pretty"
    d.mkdir()
    names = []
    for f in sorted(GOLDEN.glob("*.kicad_mod")):
        t, _ = X.footprint_tree(f.read_text(encoding="utf-8"), f.stem, "lib", [])
        (d / f.name).write_text(KP.footprint_text(sexpr.dumps(t) + "\n", "lib", "KICAD7_3RD_PARTY", "x", 7), encoding="utf-8")
        names.append(f.stem)
    got = R.pcbnew_job("load", lib=str(d))
    assert sorted(got) == sorted(names) and not [k for k, v in got.items() if not v], [k for k, v in got.items() if not v]


# ---------------------------------------------------------------- Altium-origin footprints
def _pcblib(d, fp, fill_rot=None):
    """write_pcblib, optionally with every fill rotated by fill_rot degrees (Altium fills carry a rotation)."""
    if fill_rot is None:
        return AL.write_pcblib(d / "p.PcbLib", [fp])
    orig = AL.fill_rec
    with mock.patch.object(AL, "fill_rec", lambda *a, **k: orig(*a, rot=fill_rot)):
        return AL.write_pcblib(d / "p.PcbLib", [fp])


def _fp(**kw):
    fp = {"name": "PROBE", "description": "probe", "height_mm": 0, "tracks": [], "fills": [], "regions": [], "texts": [],
          "pads": [{"designator": "1", "x": 0, "y": 0, "w": 1, "h": 0.6, "rot": 0, "shape": "rect", "layer": AL.TOP}]}
    fp.update(kw)
    return fp


def test_altium_keepouts_on_copper_are_not_copper():
    # an Altium keep-out (flags bit 9) on the Top layer, here one around pad 1 and one beside it, is a routing
    # restriction: written as F.Cu copper it shorts whatever runs under it (and the one around pad 1 became its shape)
    d = _tmp()
    sq = lambda x, y, s: [(x - s, y - s), (x + s, y - s), (x + s, y + s), (x - s, y + s)]
    f = _pcblib(d, _fp(regions=[(sq(0, 0, 2), AL.TOP, 31), (sq(5, 5, 1), AL.TOP, 31), (sq(-5, -5, 1), AL.TOP)]))
    notes = []
    t = sexpr.parse(X.footprint_from_pcblib(f, "PROBE", notes))
    cu = [g for g in sexpr.find(t, "fp_poly") if sexpr.first(g, "layer")[1] == "F.Cu"]
    assert len(cu) == 1, cu                                              # only the plain copper region
    assert [p[3] for p in sexpr.find(t, "pad")] == ["rect"]              # pad 1 is not a custom pad
    assert any("keep-out" in n for n in notes), notes


def test_altium_rotated_fill_keeps_its_rotation():
    d = _tmp()
    f = _pcblib(d, _fp(fills=[(-2, -0.5, 2, 0.5, AL.TOP_OVERLAY), (3, -0.5, 5, 0.5, AL.TOP_PASTE)]), fill_rot=90.0)
    t = sexpr.parse(X.footprint_from_pcblib(f, "PROBE", []))
    silk = next(g for g in sexpr.find(t, "fp_poly") if sexpr.first(g, "layer")[1] == "F.SilkS")
    pts = [(float(p[1]), float(p[2])) for p in sexpr.find(sexpr.first(silk, "pts"), "xy")]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert math.isclose(max(xs) - min(xs), 1, abs_tol=1e-3) and math.isclose(max(ys) - min(ys), 4, abs_tol=1e-3), pts
    paste = next(p for p in sexpr.find(t, "pad") if p[1] == "")
    at, size = sexpr.first(paste, "at"), sexpr.first(paste, "size")
    w, h = float(size[1]), float(size[2])
    rot = float(at[3]) if len(at) > 3 else 0
    if round(rot) % 180 == 90:
        w, h = h, w
    assert (round(float(at[1]), 3), round(float(at[2]), 3), round(w, 3), round(h, 3)) == (4, 0, 1, 2), paste


def test_altium_bottom_paste_and_mask_are_kept():
    d = _tmp()
    pads = [{"designator": "1", "x": 0, "y": 0, "w": 1, "h": 0.6, "rot": 0, "shape": "rect", "layer": AL.BOTTOM}]
    f = _pcblib(d, _fp(pads=pads, fills=[(3, -0.5, 5, 0.5, AL.BOTTOM_PASTE)],
                       regions=[([(-5, -1), (-3, -1), (-3, 1), (-5, 1)], AL.BOTTOM_SOLDER)]))
    notes = []
    t = sexpr.parse(X.footprint_from_pcblib(f, "PROBE", notes))
    layers = {sexpr.first(g, "layer")[1] for g in sexpr.find(t, "fp_poly")}
    paste = [p for p in sexpr.find(t, "pad") if p[1] == ""]
    assert "B.Mask" in layers and paste and sexpr.first(paste[0], "layers")[1:] == ["B.Paste"], (layers, paste, notes)
    assert not [n for n in notes if "not converted" in n], notes


# ---------------------------------------------------------------- duplicate names
def _dup_plan(change):
    plan, http = _plan()
    items = json.loads(plan.read_text(encoding="utf-8"))
    change(items)
    plan.write_text(json.dumps(items), encoding="utf-8")
    return plan, http


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_review_1018_kicad"]))
