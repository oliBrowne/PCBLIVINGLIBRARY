"""Debug sweep, footprint group: regressions for the DS2-* footprint / 3D / pad-table bugs.
Run:  python -m tests.run_all test_debugsweep_footprint"""
from ecadlib import footprint_adv as A, footprint_gen as F, step_gen as S
from tests.fixtures_lite import lite


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


@lite
def test_ds2_28_footprint_names_differing_only_in_case_never_share_a_file_path(tmp_path):
    # Windows and macOS file systems ignore case: models/PcbLib/SOIC-8.PcbLib and soic-8.PcbLib are one file, so a
    # BOM 'soic-8' with other pads overwrote the 'SOIC-8' part's footprint. Same pads under another case: one file.
    from tests import test_fp_share as TFS, test_librarian as TL
    bom = TFS.BOM.replace("WIDE1,SOIC-8", "WIDE1,soic-8").replace("NARROW2,SOIC-8", "NARROW2,Soic-8")
    items = TL.run(tmp_path, TFS.http(), bom=bom, sources=["kicad"])[0]
    n1, w, n2 = (items[k]["footprint"] for k in ("NARROW1", "WIDE1", "NARROW2"))
    assert n1["file"] == "models/PcbLib/SOIC-8.PcbLib", n1
    assert w["file"].casefold() != n1["file"].casefold(), ("WIDE1 (other pads) writes over NARROW1's file", w["file"])
    assert w["file"] == "models/PcbLib/SOIC-8_5.3x5.3mm_P1.27mm.PcbLib", w
    assert (n2["file"], n2["ref"]) == ("models/PcbLib/SOIC-8.PcbLib", "SOIC-8"), n2     # same pads: NARROW1's file


def test_ds2_29_polygon_pin_1_with_origin_pin1_is_checked_not_a_crash():
    # check_pad_table read pin 1's x/y from a fresh expansion of the table, where a polygon pad has only its points:
    # KeyError 'x'. A polygon pad sits at the middle of its points, the same as table_layout places it.
    square = [{"x": -1, "y": -1}, {"x": 1, "y": -1}, {"x": 1, "y": 1}, {"x": -1, "y": 1}]
    pwr = {"name": "PWR_MOD", "package_type": "custom", "pin_count": 3,
           "pad_table": {"unit": "mm", "y_axis": "down", "origin": "pin1",
                         "pads": [{"number": "1", "polygon": square}, {"number": "2", "x": 4, "y": 0, "w": 1.5, "h": 1.5},
                                  {"number": "3", "x": 8, "y": 0, "w": 1.5, "h": 1.5}]}}
    F.validate(pwr)
    _, rep = F.build(pwr)                                     # pin 1's middle is the drawing's 0,0: no pin-1 problem
    assert not [c for c in rep.get("table_checks", []) if "pin 1" in str(c)], rep.get("table_checks")
    # the same pad 3 mm off the origin: the pin-1 error, not a crash
    off = dict(pwr, pad_table=dict(pwr["pad_table"], pads=[{"number": "1", "polygon": [dict(v, x=v["x"] + 3) for v in square]}]
                                   + pwr["pad_table"]["pads"][1:]))
    pin1 = [c["message"] for c in A.check_pad_table(off) if c["rule"] == "pin1" and c["level"] == "error"]
    assert pin1 and "(3, 0)" in pin1[0], pin1


@lite
def test_ds2_30_nan_or_inf_in_a_nested_money_setting_is_rejected(tmp_path):
    # [agent.proof] max_cost_usd = nan loaded as NaN, and every 'estimate > NaN' is False: no proof call was refused
    # on cost. The top-level [datasheet] key already refused nan / inf; the nested table did not.
    from ecadlib import rules as RU
    f = tmp_path / "ecadlib.toml"
    for v in ("nan", "inf", "+inf"):
        f.write_text(f"[agent.proof]\nmax_cost_usd = {v}\n")
        try:
            got = RU.load(f)["agent"]["proof"]["max_cost_usd"]
        except ValueError as e:
            assert "agent.proof.max_cost_usd" in str(e) and "nan or inf" in str(e), e
        else:
            raise AssertionError(f"[agent.proof] max_cost_usd = {v} accepted, loaded as {got!r}")
    f.write_text("[agent.proof]\nmax_cost_usd = 0.5\n")
    assert RU.load(f)["agent"]["proof"]["max_cost_usd"] == 0.5


@lite
def test_ds2_31_gullwing_or_qfn_spec_with_inner_span_only_is_refused_and_the_batch_goes_on(tmp_path):
    # The schema let gullwing / qfp / qfn give inner_span instead of terminal_length (inner_span is a chip / molded
    # field); the builders need terminal_length: KeyError, 'internal error', and the specs after it never ran.
    import json
    from tests import e2e_support as E
    sod = {"name": "D_SOD-123_test", "package_type": "gullwing", "polarized": True,
           "body_length": {"min": 1.4, "max": 1.8}, "body_width": {"min": 2.55, "max": 2.85},
           "lead_span": {"min": 3.55, "max": 3.85}, "terminal_width": {"min": 0.45, "max": 0.65},
           "inner_span": {"min": 2.6, "max": 2.9},
           "pin_positions": [{"number": "1", "side": "L", "offset": 0}, {"number": "2", "side": "R", "offset": 0}]}
    qfn = {"name": "QFN-16_test", "package_type": "qfn", "pin_count": 16, "pitch": 0.5,
           "body_length": {"nom": 3.0, "tol": 0.1}, "body_width": {"nom": 3.0, "tol": 0.1},
           "terminal_width": {"min": 0.18, "max": 0.3}, "inner_span": {"min": 2.0, "max": 2.2}}
    good = {"name": "R_0603_good", "package_type": "chip", "body_length": {"nom": 1.6, "tol": 0.1},
            "body_width": {"nom": 0.8, "tol": 0.1}, "terminal_length": {"nom": 0.3, "tol": 0.2}}
    for s in (sod, qfn, good):
        (tmp_path / f"{s['name']}.json").write_text(json.dumps(s), encoding="utf-8")
    p = E.ecadlib(tmp_path, "footprint", "gen", "D_SOD-123_test.json", "QFN-16_test.json", "R_0603_good.json",
                  "-o", "out.pretty", check=False)
    assert "internal error" not in p.stderr, p.stderr
    for name in ("D_SOD-123_test", "QFN-16_test"):
        assert f"{name}: not written" in p.stderr and "terminal_length" in p.stderr, p.stderr
    assert (tmp_path / "out.pretty" / "R_0603_good.kicad_mod").is_file(), (p.stdout, p.stderr)
    assert p.returncode == 1, p.returncode


@lite
def test_ds2_32_footprint_gen_reads_a_utf8_spec_whatever_the_locale(tmp_path):
    # The spec was read with the locale's encoding: on Windows (cp1252) '±' became 'Â±' in the footprint's descr and
    # CJK text failed with 'save the file as UTF-8', which it already was. Here the locale is C (ASCII) instead.
    import json
    from pathlib import Path
    from tests import e2e_support as E
    root = Path(__file__).resolve().parent.parent
    spec = json.loads((root / "ecadlib" / "package_specs" / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text(encoding="utf-8"))
    spec["description"] = "SOIC-8 ±1 % tolerance — 封装 (Müller)"
    (tmp_path / "soic8.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    env = {k: v for k, v in E.clean_env().items() if not k.startswith(("LC_", "LANG", "PYTHONUTF8", "PYTHONIOENCODING"))}
    env.update(LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="utf-8")
    p = E.ecadlib(tmp_path, "footprint", "gen", "soic8.json", "-o", "out.pretty", env=env, check=False)
    assert p.returncode == 0, p.stderr
    mod = (tmp_path / "out.pretty" / f"{spec['name']}.kicad_mod").read_text(encoding="utf-8")
    assert '(descr "SOIC-8 ±1 % tolerance — 封装 (Müller)' in mod, mod[:300]


def test_ds2_33_round_pads_whose_boxes_overlap_only_at_the_corners_are_not_an_overlap():
    # Two 1.5 mm round pads staggered by (1.02, 1.27) mm are 1.629 mm apart centre to centre: 0.129 mm of real copper
    # gap. The check returned the bounding boxes' overlap (-0.23 mm) before applying the pads' radii.
    from ecadlib import footprint_check as FC
    pads = lambda x, y: ('(footprint "T" (layer "F.Cu")\n'
                         '  (pad "1" thru_hole circle (at 0 0) (size 1.5 1.5) (drill 0.9) (layers "*.Cu" "*.Mask"))\n'
                         f'  (pad "2" thru_hole circle (at {x} {y}) (size 1.5 1.5) (drill 0.9) (layers "*.Cu" "*.Mask"))\n)')
    copper = lambda txt: [(lvl, msg) for lvl, rule, msg in FC.check(txt) if rule == "copper"]
    assert not [c for c in copper(pads(1.02, 1.27)) if "overlap" in c[1]], copper(pads(1.02, 1.27))
    # really touching: 1.0, 1.0 apart -> 1.414 mm between centres, 0.086 mm of overlap
    assert ("error", "pads 1 and 2 overlap by 0.086 mm") in copper(pads(1.0, 1.0)), copper(pads(1.0, 1.0))
