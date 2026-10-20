"""Debug sweep, footprint group: regressions for the DS2-* footprint / 3D / pad-table bugs.
Run:  python -m tests.run_all test_debugsweep_footprint"""
from ecadlib import footprint_gen as F, step_gen as S


def _bga(name, rows, cols, pitch, ball, length, width):
    return {"name": name, "package_type": "bga", "rows": rows, "cols": cols, "pitch": pitch, "pin_count": rows * cols,
            "ball_diameter": {"nom": ball}, "body_length": {"nom": length}, "body_width": {"nom": width}, "height_max": 0.5}


def test_ds2_26_generated_bga_step_body_turns_with_the_footprint_body():
    # The footprint turns a BGA body a quarter turn so it runs with / fits the ball grid. The STEP model generated
    # from the same spec must be turned the same way, or the 3D body sticks out of the courtyard on two sides.
    cases = [
        # CY14U256LA-BA: 8 rows x 6 columns at 0.75 mm, body read as 10 x 6 mm -> drawn 6 wide (x) and 10 tall (y)
        (_bga("TFBGA-48", 8, 6, 0.75, 0.3, 10.0, 6.0), (3.0, 5.0)),
        # TPS628436YKA: 3 rows x 2 columns at 0.35 mm, body given as 1.05 x 0.797 mm -> drawn 0.797 wide, 1.05 tall
        (_bga("WCSP-6", 3, 2, 0.35, 0.2, 1.05, 0.797), (0.3985, 0.525)),
    ]
    for spec, (hx, hy) in cases:
        F.validate(spec)
        txt, rep = F.build(spec)
        assert any("turned" in n for n in rep.get("notes", [])), (spec["name"], rep.get("notes"))
        _, info = S.generate(spec)
        x0, y0, _, x1, y1, _ = info["bbox"]
        got = (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))
        assert got == (-hx, -hy, hx, hy), (spec["name"], "STEP body", got, "footprint body", (-hx, -hy, hx, hy))
