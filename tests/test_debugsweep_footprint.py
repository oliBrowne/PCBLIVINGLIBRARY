"""Debug sweep, footprint group: regressions for the DS2-* footprint / 3D / pad-table bugs.
Run:  python -m tests.run_all test_debugsweep_footprint"""
from ecadlib import footprint_adv as A, footprint_gen as F, step_gen as S


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


def _header_table(n, origin):
    """1 x n 2.54 mm THT header (1.7 mm pad, 1.0 mm drill), x from pin 1 or centred."""
    x0 = 0.0 if origin == "pin1" else -(n - 1) * 2.54 / 2
    return {"name": f"Conn_1x{n:02d}_P2.54mm", "package_type": "connector", "pin_count": n, "pitch": 2.54,
            "pad_table": {"unit": "mm", "y_axis": "down", "origin": origin,
                          "default_pad": {"type": "tht", "drill": 1.0, "d": 1.7},
                          "arrays": [{"count": n, "x": x0, "y": 0, "dx": 2.54, "dy": 0}]}}


def test_ds2_27_long_mm_pad_table_is_not_taken_for_mils():
    # The 'mils typed as mm' check compared pad positions with 60, so any mm table reaching 60 mm from its origin was
    # refused (and the advice, unit mil, shrank it 39 times). Length and origin say nothing about the unit.
    units = lambda spec: [c["message"] for c in A.check_pad_table(spec) if c["rule"] == "units"]
    for n, origin in ((25, "pin1"), (50, "center")):         # pins at x 0 .. 60.96 mm / -62.23 .. 62.23 mm
        assert units(_header_table(n, origin)) == [], (n, origin, units(_header_table(n, origin)))
    # DIN41612 C 3 x 32 at 2.54 mm, origin pin 1, mounting holes at x -7.62 and 86.36: builds
    arr = lambda y, prefix: {"count": 32, "x": 0, "y": y, "dx": 2.54, "dy": 0, "first_number": 1, "prefix": prefix}
    din = {"name": "DIN41612_C_3x32_P2.54mm", "package_type": "connector", "pin_count": 96, "pitch": 2.54,
           "pad_table": {"unit": "mm", "y_axis": "down", "origin": "pin1",
                         "default_pad": {"type": "tht", "drill": 1.0, "d": 1.6},
                         "arrays": [arr(0, "A"), arr(2.54, "B"), arr(5.08, "C")],
                         "pads": [{"number": "", "type": "npth", "x": -7.62, "y": 2.54, "drill": 2.8},
                                  {"number": "", "type": "npth", "x": 86.36, "y": 2.54, "drill": 2.8}]}}
    F.validate(din)
    _, rep = F.build(din)
    assert rep["courtyard"][0] > 90, rep["courtyard"]
    # a table really in mils typed as mm (100 mil pitch, 65 mil pad, 40 mil drill) is still refused
    mil = _header_table(4, "pin1")
    mil["pad_table"].update(default_pad={"type": "tht", "drill": 40, "d": 65},
                            arrays=[{"count": 4, "x": 0, "y": 0, "dx": 100, "dy": 0}])
    assert any("mils" in m for m in units(mil)), units(mil)
