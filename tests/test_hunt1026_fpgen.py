"""Regression tests from the 2026-10-26 footprint-generation hunt (fpgen-NN in the hunt report)."""
import copy, math, random, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecadlib import footprint_adv as FA, footprint_check as FC, footprint_gen as FG, footprint_rules as FR, fp_tidy, sexpr
from ecadlib import step_gen as SG

SOIC8 = {"name": "SOIC-8_3.9x4.9mm_P1.27mm", "package_type": "gullwing", "pin_count": 8, "pitch": 1.27,
         "body_length": {"min": 4.8, "max": 5.0}, "body_width": {"min": 3.8, "max": 4.0},
         "lead_span": {"min": 5.8, "max": 6.2}, "terminal_length": {"min": 0.4, "max": 1.27},
         "terminal_width": {"min": 0.31, "max": 0.51}}
QFN16 = {"name": "QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm", "package_type": "qfn", "pin_count": 16, "pitch": 0.5,
         "body_length": {"nom": 3, "tol": 0.05}, "body_width": {"nom": 3, "tol": 0.05},
         "terminal_length": {"min": 0.3, "max": 0.5}, "terminal_width": {"min": 0.18, "max": 0.3},
         "exposed_pad": {"length": {"nom": 1.7, "tol": 0.05}, "width": {"nom": 1.7, "tol": 0.05}}}


def _refused(spec, *words):
    """build() refuses the spec with a ValueError (a hold with a reason), not another exception or a footprint."""
    try:
        FG.build(spec)
    except ValueError as e:
        assert all(w in str(e) for w in words), str(e)
        return str(e)
    raise AssertionError(f"built {spec['name']}")


def _gapped_soic(ep_number=None):
    """14 leads on 16 places (pins 13 and 14 cut, as SOIC-14-16 parts do) with an exposed pad."""
    pos = [{"number": str(i + 1), "side": "L", "offset": round((i - 3.5) * 1.27, 3)} for i in range(8)]
    pos += [{"number": str(9 + i), "side": "R", "offset": round((3.5 - i) * 1.27, 3)} for i in range(8) if 9 + i not in (13, 14)]
    s = dict(copy.deepcopy(SOIC8), name="SOIC-14-16-1EP_3.9x9.9mm_P1.27mm", pin_count=14, pin_positions=pos,
             body_length={"nom": 9.9}, exposed_pad={"length": {"nom": 2.4}, "width": {"nom": 4.0}})
    if ep_number is not None:
        s["exposed_pad"]["number"] = ep_number
    return s


# fpgen-01: the exposed pad's default number (pin_count + 1) landed on a real lead of a gap-numbered part, so the EP
# shared that lead's net (pad 15 = a signal pin and the thermal pad) and no check said so
def test_fpgen01_default_ep_number_never_lands_on_a_lead():
    s = _gapped_soic()
    FG.validate(s)
    _refused(s, "exposed pad", "15")
    txt, rep = FG.build(_gapped_soic(ep_number="17"))           # numbered by the read: built as given
    assert rep["ep_num"] == "17" and [p["num"] for p in rep["pads"]].count("15") == 1
    txt, rep = FG.build(copy.deepcopy(QFN16))                    # 1..16 + EP: the default still applies
    assert rep["ep_num"] == "17"


# fpgen-02: a NaN / infinite number (json.loads takes NaN and Infinity; the schema's minimum lets NaN through) was
# written into the .kicad_mod as "nan", and footprint_check passed it (every comparison with NaN is False)
def test_fpgen02_non_finite_numbers_are_refused():
    s = copy.deepcopy(SOIC8)
    s["body_length"]["min"] = float("nan")
    FG.validate(s)                                               # the schema does not stop it
    _refused(s, "body_length")
    s = copy.deepcopy(QFN16)
    s["exposed_pad"]["width"]["tol"] = float("inf")
    _refused(s, "exposed_pad")
    bga = {"name": "BGA-16", "package_type": "bga", "rows": 4, "cols": 4, "pitch": 0.8,
           "ball_diameter": {"nom": float("inf")}, "body_length": {"nom": 4}, "body_width": {"nom": 4}}
    _refused(bga, "ball_diameter")                               # was OverflowError
    # and a .kicad_mod that carries one (KiCad cannot load it) fails footprint_check instead of passing every rule
    good = FG.build(copy.deepcopy(SOIC8))[0]
    assert not [i for i in FC.check(good) if i[0] == "error"]
    for layer in ('"F.CrtYd"', '"F.Fab"', "(pad "):
        lines = good.splitlines()
        k = next(i for i, l in enumerate(lines) if layer in l and "fp_text" not in l)
        lines[k] = re.sub(r"\((start|at|xy) (-?[\d.]+) ", r"(\1 nan ", lines[k], count=1)
        bad = "\n".join(lines) + "\n"
        assert "nan" in bad
        assert any(l == "error" and r == "geometry" for l, r, _ in FC.check(bad)), (layer, FC.check(bad))


# fpgen-03: an exposed pad of size 0 (schema minimum 0) crashed build() with ZeroDivisionError, which `footprint gen`
# does not catch (the rest of the batch stopped); now a refusal like any other spec that doesn't add up
def test_fpgen03_zero_exposed_pad_is_refused_not_a_crash():
    for k in ("length", "width"):
        for v in (0, 1e-9):
            s = copy.deepcopy(QFN16)
            s["exposed_pad"][k] = {"nom": v}
            FG.validate(s)
            _refused(s, "exposed pad")


# fpgen-04: a pin header whose body is read with its length along the pin row (body_length 25.4 for a 1 x 10) drew the
# body, silk and 3D model across the pins: the courtyard became a 26 x 26 mm cross and no check said so
def test_fpgen04_header_body_runs_with_the_pin_rows():
    for cols, rows in ((1, 10), (2, 5), (1, 2)):
        e = 2.54
        long_, short = rows * e, cols * e
        good = {"name": f"PinHeader_{cols}x{rows:02d}_P2.54mm_Vertical", "package_type": "pin_header", "pin_count": cols * rows,
                "rows": rows, "cols": cols, "pitch": e, "body_length": {"nom": short}, "body_width": {"nom": long_},
                "height_max": 8.5}
        turned = dict(good, body_length={"nom": long_}, body_width={"nom": short})
        ta, ra = FG.build(good)
        tb, rb = FG.build(turned)
        assert ra["courtyard"] == rb["courtyard"], (cols, rows, ra["courtyard"], rb["courtyard"])
        fab = lambda t: FC._bbox(FC._graphics(sexpr.parse(t), "F.Fab")[0])
        assert fab(ta) == fab(tb)
        bb = lambda s: (lambda b: (round(max(v[0] for v in b.verts) - min(v[0] for v in b.verts), 3),
                                   round(max(v[1] for v in b.verts) - min(v[1] for v in b.verts), 3)))(
            next(so for so in SG.build(s, 8.5)[0] if so.name == "body"))
        assert bb(good) == bb(turned) == (round(short, 3), round(long_, 3)), (bb(good), bb(turned))


# fpgen-05: the pinmap rule (diode / LED cathode on pad 1) ran only for footprints named D_ / LED_; a polarized
# two-terminal footprint ecadlib generated under another name (SOD-123F from a datasheet read) with the symbol's anode
# on pin 1 passed the release check, the cathode bracket on the anode
def test_fpgen05_pinmap_follows_the_polarity_mark_not_only_the_name():
    spec = {"name": "SOD-123F_Custom", "package_type": "molded", "pin_count": 2, "polarized": True,
            "body_length": {"min": 3.4, "max": 3.8}, "body_width": {"min": 1.5, "max": 1.7},
            "terminal_length": {"min": 0.6, "max": 1.0}, "terminal_width": {"min": 0.7, "max": 0.9}}
    txt, _ = FG.build(spec)
    pinmap = lambda pins: [m for l, r, m in FR.check(txt, symbol_pins=pins, geometry=False) if r == "pinmap" and l == "error"]
    assert pinmap({"1": "A", "2": "K"}), "anode on the marked pad"
    assert not pinmap({"1": "K", "2": "A"})
    assert not pinmap({"1": "+", "2": "-"})                    # a tantalum's + on pad 1 is right
    plain = FG.build(dict(spec, polarized=False))[0]          # no mark: nothing to compare with
    assert not [m for l, r, m in FR.check(plain, symbol_pins={"1": "A", "2": "K"}, geometry=False) if r == "pinmap"]


# fpgen-06: fp_tidy took a pad stacked on pin 1 (USB-C A1 / B12) for "another pad nearer the mark", so it never saw a
# pin-1 mark there and added another dot on every run
def test_fpgen06_tidy_is_idempotent_with_a_stacked_pin1():
    import json
    spec = json.loads((ROOT / "ecadlib" / "package_specs" / "advanced" / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.json")
                      .read_text(encoding="utf-8"))
    once = fp_tidy.tidy(FG.build(spec)[0], [])
    notes = []
    assert fp_tidy.tidy(once, notes) == once, notes


# property: generated footprints across the families keep their basic promises (seeded, small)
def test_fpgen_property_generated_families():
    R = random.Random(1026)
    built = 0
    for _ in range(60):
        e = R.choice([0.5, 0.65, 0.8, 1.27])
        per = R.randint(2, 10)
        D = (per - 1) * e + e + 0.5
        E1 = R.uniform(2.0, 6.0)
        T = R.uniform(0.4, 0.8)
        s = {"name": "G", "package_type": "gullwing", "pin_count": 2 * per, "pitch": e,
             "body_length": {"nom": round(D, 3), "tol": 0.1}, "body_width": {"nom": round(E1, 3), "tol": 0.1},
             "lead_span": {"nom": round(E1 + 2 * T + 0.6, 3), "tol": 0.2}, "terminal_length": {"nom": round(T, 3), "tol": 0.15},
             "terminal_width": {"nom": round(0.42 * e, 3), "tol": 0.04}}
        txt, rep = FG.build(s)
        built += 1
        nums = [p["num"] for p in rep["pads"]]
        assert sorted(nums, key=int) == [str(i) for i in range(1, 2 * per + 1)]
        assert not [c for c in rep["checks"] if c["level"] == "error"], rep["checks"]
        pos = {(round(p["x"], 3), round(p["y"], 3)) for p in rep["pads"]}
        assert pos == {(round(-x, 3) + 0.0, round(-y, 3) + 0.0) for x, y in pos}       # symmetric about the origin
        p1 = next(p for p in rep["pads"] if p["num"] == "1")
        assert p1["x"] < 0 and p1["y"] == min(p["y"] for p in rep["pads"])            # pin 1 top left
        span = FG.dim(s["lead_span"])
        assert abs(p1["x"]) + p1["w"] / 2 >= span[0] / 2 - 1e-6                      # the land reaches the lead tip
    assert built == 60


# fpgen-07: a pad table in inches typed as unit mm (the mirror of mils typed as mm, which check_pad_table refuses) built
# a footprint 25.4 times too small with no warning: two 0.08 x 0.12 mm pads 0.2 mm apart inside an 8 x 5 mm body
def test_fpgen07_inches_typed_as_mm_are_refused():
    def conn(unit, pads, pitch=5.08):
        s = {"name": "C", "package_type": "connector", "pin_count": len(pads), "pitch": pitch,
             "body_length": {"nom": 8}, "body_width": {"nom": 5}, "pad_table": {"unit": unit, "pads": pads}}
        return s if pitch else {k: v for k, v in s.items() if k != "pitch"}
    two = [{"number": "1", "x": -0.1, "y": 0, "w": 0.08, "h": 0.12}, {"number": "2", "x": 0.1, "y": 0, "w": 0.08, "h": 0.12}]
    _refused(conn("mm", two), "inches")
    txt, rep = FG.build(conn("in", two))                         # the same table with its unit: 5.08 mm apart
    assert {round(p["x"], 3) for p in rep["pads"]} == {-2.54, 2.54}
    _refused(conn("mm", two, pitch=None), "inches")             # no pitch to compare: no real pad is that small
    # a real fine-pitch mm table is not taken for inches (0.2 mm WLCSP-size pads, 0.4 mm apart)
    fine = [{"number": str(i + 1), "x": round(i * 0.4, 3), "y": 0, "w": 0.2, "h": 0.6} for i in range(4)]
    FG.build(conn("mm", fine, pitch=0.4))


# fpgen-08: a ball grid read wholly in mils (pitch 31.5, ball 15.7, body 394) built a 394 mm BGA with no warning (and
# slowly); the parametric families refuse anything over 60 mm as mils, the ball grid did not
def test_fpgen08_bga_in_mils_is_refused():
    mm = {"name": "BGA-16", "package_type": "bga", "rows": 4, "cols": 4, "pitch": 0.8, "ball_diameter": {"nom": 0.4},
          "body_length": {"nom": 4}, "body_width": {"nom": 4}}
    FG.build(mm)
    mil = dict(mm, pitch=31.5, ball_diameter={"nom": 15.75}, body_length={"nom": 157.5}, body_width={"nom": 157.5})
    _refused(mil, "mils")
    big = dict(mm, rows=8, cols=8, pitch=1.27, ball_diameter={"nom": 0.75}, body_length={"nom": 52.5},
               body_width={"nom": 52.5})                     # the biggest real bodies (52.5 mm FPGAs) still build
    assert len(FG.build(big)[1]["pads"]) == 64
