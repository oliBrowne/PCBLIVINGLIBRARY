"""A maker's land whose pin 1 pad is drawn another size (land.pin1_length / pin1_width): benchmark 2026-10-21 built
NCP4308AMT and BCV61 with every pad the same, while the maker (and KiCad, following it) gives pin 1 its own pad."""
from ecadlib import footprint_gen as F, footprint_bench as B
from ecadlib.eval import score as S


def pads(spec):
    return {p[0]: p[1:5] for p in B.numbers(F.build(spec)[0])["pads"]}


def near(a, b):
    return all(abs(x - y) < 1e-3 for x, y in zip(a, b))


DFN8 = {"name": "WDFN-8_2x2mm_P0.5mm", "package_type": "qfn", "pitch": 0.5, "body_length": {"nom": 2.0},
        "body_width": {"nom": 2.0}, "terminal_length": {"min": 0.25, "max": 0.35}, "terminal_width": {"min": 0.2, "max": 0.3},
        "height_max": 0.8, "pin_count": 8, "pins_left_right": 4, "pins_top_bottom": 0,
        "land": {"pad_length": 0.78, "pad_width": 0.3, "pad_center": 0.76, "pin1_length": 0.88}}


def test_a_longer_pin_1_keeps_the_rows_outer_end():
    # NCP4308AMT (onsemi WDFN8 2x2): pin 1 0.88 long, the others 0.78, centres at +-0.76; KiCad pad 1 sits at -0.71
    p = pads(DFN8)
    assert near(p["1"], (-0.71, -0.75, 0.88, 0.3))
    assert near(p["2"], (-0.76, -0.25, 0.78, 0.3))
    assert near(p["8"], (0.76, -0.75, 0.78, 0.3))
    assert abs((p["1"][0] - p["1"][2] / 2) - (p["2"][0] - p["2"][2] / 2)) < 1e-6     # same outer end


def test_a_wider_pin_1_stays_on_its_lead():
    # BCV61 (SOT-143B): pin 1 is the wide lead, its land 1.0 wide where the others are 0.6
    spec = {"name": "SOT-143", "package_type": "gullwing", "pitch": 1.9, "pin_count": 4,
            "body_length": {"min": 2.8, "max": 3.0}, "body_width": {"min": 1.2, "max": 1.4}, "lead_span": {"min": 2.1, "max": 2.5},
            "terminal_length": {"min": 0.3, "max": 0.5}, "terminal_width": {"min": 0.3, "max": 0.5}, "height_max": 1.1,
            "pin_positions": [{"number": "1", "side": "L", "offset": -0.75}, {"number": "2", "side": "L", "offset": 0.95},
                              {"number": "3", "side": "R", "offset": 0.95}, {"number": "4", "side": "R", "offset": -0.95}],
            "land": {"pad_length": 0.7, "pad_width": 0.6, "pad_center": 1.0, "pin1_width": 1.0}}
    p = pads(spec)
    assert near(p["1"], (-1.0, -0.75, 0.7, 1.0))
    assert near(p["2"], (-1.0, 0.95, 0.7, 0.6)) and near(p["4"], (1.0, -0.95, 0.7, 0.6))


def test_without_pin_1_sizes_every_pad_is_the_lands():
    spec = dict(DFN8, land={k: v for k, v in DFN8["land"].items() if k != "pin1_length"})
    assert near(pads(spec)["1"], (-0.76, -0.75, 0.78, 0.3))


def test_the_scorer_counts_a_pin_1_of_its_own_as_the_makers_land():
    spec = dict(DFN8, land=dict(DFN8["land"], pin1_length=1.1))
    mine = B.numbers(F.build(spec)[0])
    ml = S.maker_land(mine, spec)
    assert ml["followed"] and "1" in ml["pads"]
    plain = dict(DFN8, land={k: v for k, v in DFN8["land"].items() if k != "pin1_length"})
    assert "1" not in S.maker_land(mine, plain)["pads"]                        # pin 1 isn't the plain land's pad
