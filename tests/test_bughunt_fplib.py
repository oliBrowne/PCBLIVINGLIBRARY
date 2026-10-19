"""Regression tests for the 2026-10-19 bug hunt, footprint generator / checker and librarian / change control group.
Run:  python3 -m tests.test_bughunt_fplib   (or tests.run_all). Offline; no pytest needed."""
import contextlib, copy, io, json, os
from pathlib import Path
from unittest import mock

from ecadlib import footprint_adv as FA, footprint_check as FC, footprint_gen as F, footprint_rules as R, fp_edit as FE
from ecadlib import stdparts as S
from tests.fixtures_lite import lite

SPECS = Path(__file__).resolve().parent.parent / "ecadlib" / "package_specs"


def _spec(name):
    return json.loads((SPECS / f"{name}.json").read_text(encoding="utf-8"))


def _refused(spec, *words):
    try:
        F.build(spec)
    except ValueError as e:
        assert all(w in str(e) for w in words), e
        return str(e)
    raise AssertionError(f"{spec['name']} was built")


def _swap12(text):
    return text.replace('(pad "1" ', '(pad "TMP" ', 1).replace('(pad "2" ', '(pad "1" ', 1).replace('(pad "TMP" ', '(pad "2" ', 1)


# ---------------------------------------------------------------- footprint generator
def test_fp8_datasheet_land_on_a_tall_dual_row_dfn_keeps_every_pad_in_its_row():
    """FP-8: the end pads of a 2 x 4 mm DFN with 0.8 mm terminals were moved 0.4 mm along the row and turned."""
    spec = {"name": "DFN-6_2x4mm_P1.3mm", "package_type": "qfn", "pin_count": 6, "pitch": 1.3, "pins_left_right": 3,
            "pins_top_bottom": 0, "body_length": {"nom": 2.0, "tol": 0.05}, "body_width": {"nom": 4.0, "tol": 0.05},
            "terminal_width": {"min": 0.75, "max": 0.85}, "terminal_length": {"min": 0.2, "max": 0.3},
            "land": {"pad_length": 0.6, "pad_width": 0.9, "pad_center": 0.9}}
    _, rep = F.build(spec)
    got = [(p["num"], p["x"], p["y"], p["w"], p["h"]) for p in rep["pads"]]
    assert got == [("1", -0.9, -1.3, 0.6, 0.9), ("2", -0.9, 0.0, 0.6, 0.9), ("3", -0.9, 1.3, 0.6, 0.9),
                   ("4", 0.9, 1.3, 0.6, 0.9), ("5", 0.9, 0.0, 0.6, 0.9), ("6", 0.9, -1.3, 0.6, 0.9)], got


def test_fp3_rectangular_qfn_has_no_false_ep_gap_warning():
    """FP-3: the end pads of a 3 x 6 mm QFN's left row were measured against the EP's long edge (-0.750 mm)."""
    _, rep = F.build(S.spec("QFN-28-1EP_3x6mm_P0.5mm_EP1.7x4.75mm"))
    assert "warning" not in rep, rep.get("warning")
    spec = S.spec("QFN-28-1EP_3x6mm_P0.5mm_EP1.7x4.75mm")       # a real one is still found: a land 0.1 mm from the EP
    spec["land"] = {"pad_length": 0.5, "pad_width": 0.25, "pad_center": 1.2, "pad_center_y": 2.975}
    try:
        _, rep = F.build(spec)
    except ValueError as e:
        raise AssertionError(e)
    assert rep.get("warning") == "EP to pad gap 0.100 < 0.2", rep.get("warning")


def test_fp2_overlapping_or_touching_pads_are_refused_and_gen_exits_nonzero(tmp_path=None):
    """FP-2: a QFN typed with the wrong pitch (corner pads overlap), terminals longer than half the chip (pads touch)
    and an 0603 typed in inches were written and `footprint gen` exited 0."""
    q = _spec("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm")
    q.update(name="QFN32_P065", pitch=0.65)
    assert "pads 1 and 32 overlap" in (F.shorts(F.build(q)[1]) or ""), "the shorted corner pads are named"
    r = _spec("R_0603_1608Metric")
    r.update(name="R_longT", terminal_length={"min": 0.5, "max": 0.9})
    assert "0.000 mm apart" in (F.shorts(F.build(r)[1]) or "")
    assert F.shorts(F.build(_spec("R_0603_1608Metric"))[1]) is None
    c = _spec("R_0603_1608Metric")
    c.update(name="R_inches", body_length={"nom": 0.063, "tol": 0.004}, body_width={"nom": 0.031, "tol": 0.004},
             terminal_length={"nom": 0.012, "tol": 0.008})
    _refused(c, "inches")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "q.json").write_text(json.dumps(q))
        (Path(d) / "ok.json").write_text(json.dumps(_spec("R_0603_1608Metric")))
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                F.main(["gen", str(Path(d) / "q.json"), str(Path(d) / "ok.json"), "-o", str(Path(d) / "o.pretty")])
            except SystemExit as e:
                code = e.code
            else:
                code = 0
        assert code not in (0, None), code
        assert "QFN32_P065: not written" in err.getvalue(), err.getvalue()
        assert sorted(p.name for p in (Path(d) / "o.pretty").iterdir()) == ["R_0603_1608Metric.kicad_mod"]


def test_fp12_gullwing_pin_outside_the_body_is_refused():
    """FP-12: SOT-23 with pin 1's offset misread as -5 (body 2.9 mm) was built with an 11 mm courtyard."""
    spec = S.spec("SOT-23")
    spec["pin_positions"] = [dict(p) for p in spec["pin_positions"]]
    spec["pin_positions"][0]["offset"] = -5
    _refused(spec, "pin 1 offset -5 is outside")
    F.build(S.spec("SOT-23"))                                   # the real part still builds


def test_fp4_bga_with_every_ball_depopulated_is_a_plain_error():
    spec = {"name": "BGA_T", "package_type": "bga", "rows": 5, "cols": 5, "pitch": 0.8, "ball_diameter": {"nom": 0.4},
            "body_length": {"nom": 5}, "body_width": {"nom": 5}, "depopulated": ["E5:A1"]}
    _refused(spec, "no balls left")


def test_fp7_zero_ball_diameter_is_refused_and_zero_size_copper_is_an_error():
    spec = {"name": "BGA_T0", "package_type": "bga", "rows": 5, "cols": 5, "pitch": 0.8, "ball_diameter": {"nom": 0},
            "body_length": {"nom": 5}, "body_width": {"nom": 5}}
    _refused(spec, "ball_diameter")
    good, _ = S.build("SOIC-8_3.9x4.9mm_P1.27mm")
    zero = good.replace("(size 1.95 0.6)", "(size 1.95 0)", 1)
    got = FC.check(zero)
    assert ("error", "copper", "pad 1 has no copper: size 1.95 x 0 mm") in got, got
    assert not any(r == "paste" and "exposed pad" in m for _, r, m in FC.check(zero.replace("1.95 0)", "0 0)", 1))), \
        "a zero-size pad is not an exposed pad"


def test_fp11_pad_table_with_every_pad_at_one_point_reports_the_overlap():
    """FP-11: arrays without dx / dy put four pads on one point; the symmetry check crashed (TypeError)."""
    spec = {"name": "TH_T", "package_type": "connector", "pin_count": 4, "pitch": 2.5,
            "pad_table": {"unit": "mm", "y_axis": "down", "origin": "pin1",
                          "arrays": [{"count": 4, "x": 0, "y": 0, "drill": 0.95, "type": "tht"}]}}
    errs = [p["message"] for p in FA.check_pad_table(spec) if p["level"] == "error"]
    assert any("overlap" in e for e in errs), errs
    _refused(spec, "overlap")


# ---------------------------------------------------------------- footprint checker
def test_fp5_swapped_diode_and_led_pads_are_an_error():
    """FP-5: LED / diode footprints with pads 1 and 2 swapped (pad 1 opposite the cathode mark) passed `check`."""
    for name in ("LED_0603_1608Metric", "D_SOD-123", "D_SMA"):
        good, _ = S.build(name)
        assert not [i for i in R.check(good, filename=name + ".kicad_mod") if i[1] == "polarity"], name
        bad = [i for i in R.check(_swap12(good), filename=name + ".kicad_mod") if i[0] == "error"]
        assert any(r == "polarity" and "pads 1 and 2 look swapped" in m for _, r, m in bad), (name, bad)
    good, _ = S.build("R_0603_1608Metric")                      # a resistor has no polarity: a swap is no finding
    assert not [i for i in R.check(_swap12(good), filename="R_0603_1608Metric.kicad_mod") if i[0] == "error"]


def test_fp6_wrong_side_closed_mask_and_oversized_paste_are_found():
    good, _ = S.build("SOIC-8_3.9x4.9mm_P1.27mm")
    fn = "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod"
    found = lambda t: [i for i in R.check(t, filename=fn) if i[1] != "model"]
    assert found(good) == [], found(good)
    side = found(good.replace('(layers "F.Cu" "F.Paste" "F.Mask")', '(layers "B.Cu" "B.Paste" "B.Mask")', 1))
    assert any(r == "layers" and "other side of the board" in m for _, r, m in side), side
    mask = found(good.replace("(roundrect_rratio 0.25))", "(roundrect_rratio 0.25) (solder_mask_margin -0.4))", 1))
    assert any(l == "error" and "mask would cover the copper" in m for l, _, m in mask), mask
    paste = found(good.replace("(roundrect_rratio 0.25))", "(roundrect_rratio 0.25) (solder_paste_margin 0.5))", 1))
    assert any(r == "paste" and "bigger than the copper" in m for _, r, m in paste), paste
    bridge = found(good.replace("(roundrect_rratio 0.25))", "(roundrect_rratio 0.25) (solder_paste_margin 0.8))", 1))
    assert any(l == "error" and "bridge" in m for l, r, m in bridge), bridge
    shrink = found(good.replace("(roundrect_rratio 0.25))", "(roundrect_rratio 0.25) (solder_paste_margin -0.05))", 1))
    assert shrink == [], shrink                                   # a smaller stencil opening is normal


def test_fp9_stdparts_docstring_says_a_bare_family_never_resolves():
    doc = S.__doc__
    assert "Plain SOIC-8/14/16 means" not in doc and "never resolves" in doc, doc
    for name in ("SOIC-8", "TSSOP-20", "SOIC-16W"):
        got, why = S.ic_package(name)
        assert got is None and "no body size" in why, (name, got, why)


# ---------------------------------------------------------------- fp-edit
def test_new2_fp_edit_rename_reaches_pads_made_by_arrays():
    """NEW-2: a rename of pads produced by pad_table.arrays renamed the symbol pins but not the pads."""
    spec = {"name": "T", "package_type": "custom",
            "pad_table": {"unit": "mm", "y_axis": "down", "view": "top", "origin": "datum",
                          "arrays": [{"count": 4, "x": -1.5, "y": 0, "dx": 1.0, "w": 0.5, "h": 1.0}],
                          "pads": [{"number": "EP", "x": 0, "y": 2.5, "w": 3, "h": 1}]}}
    res = {"spec": spec, "symbol": {"pins": [{"num": n, "name": f"P{n}"} for n in ("1", "2", "3", "4", "EP")]}}
    before = {p["num"]: (p["x"], p["y"]) for p in FA.table_layout(spec)[0]}
    out, notes = FE.apply({"rename": {"1": "4", "4": "1", "EP": "5"}}, res)
    after = {p["num"]: (p["x"], p["y"]) for p in FA.table_layout(out["spec"])[0]}
    pins = {p["name"]: p["num"] for p in out["symbol"]["pins"]}
    assert after["4"] == before["1"] and after["1"] == before["4"] and after["5"] == before["EP"], (before, after)
    assert pins == {"P1": "4", "P2": "2", "P3": "3", "P4": "1", "PEP": "5"}, pins
    for name, num in pins.items():                              # each pin still lands on the pad it was on
        assert after[num] == before[name[1:]], (name, num)
    assert res["spec"]["pad_table"]["arrays"], "the input is not changed"
    out, notes = FE.apply({"rename": {"9": "1"}}, res)          # no such pad: neither pad nor pin renamed
    assert [p["num"] for p in out["symbol"]["pins"]] == ["1", "2", "3", "4", "EP"] and "no such pad" in notes[0], notes


# ---------------------------------------------------------------- approvals policy
def test_lib6_lib7_approval_policy_types():
    from ecadlib import approvals as AP
    p = AP.normalize_policy({"required": 1, "parts": "AO3401A", "roles": "librarian"}, "a.json")
    assert p["parts"] == ["AO3401A"] and p["roles"] == ["librarian"], p
    assert AP.applies({"name": "AO3401A"}, p) and not AP.applies({"name": "A"}, p)
    assert AP.normalize_policy({"required": 2.0})["required"] == 2
    for raw, words in (({"required": 1, "users": ["ann@x.com"]}, "users must be"), ([], "must be an object"),
                       ({"required": "two"}, "required must be a whole number"), ({"parts": [["x"]]}, "parts must be")):
        try:
            AP.normalize_policy(raw, "lib/approvals.json")
        except ValueError as e:
            assert words in str(e) and "lib/approvals.json" in str(e), e
        else:
            raise AssertionError(raw)


@lite
def test_lib7_release_check_says_the_policy_is_broken(tmp_path):
    from ecadlib import release_check as RC
    (tmp_path / "plan.json").write_text("[]")
    (tmp_path / "approvals.json").write_text('{"required": 1, "users": ["ann@x.com"]}')
    try:
        RC.main([str(tmp_path / "plan.json")])
    except SystemExit as e:
        assert str(e.code).startswith("approval policy:") and "users must be" in str(e.code), e.code
    else:
        raise AssertionError("release-check ran with a broken policy")


# ---------------------------------------------------------------- change control
def test_lib4_approving_a_change_of_a_part_that_left_the_plan_changes_nothing():
    """LIB-4: approve wrote the proposal over the released files and counted the approval, but released nothing."""
    from ecadlib import change_cli as CC, change_gate as CG, ledger as LG
    from tests.test_change_control import ANN, demo, gate, item, items_of, write_symbol
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "AO3401A")
    f = lib / it["symbol"]["file"]
    before = f.read_bytes()
    g = gate(lib)
    write_symbol(lib, it, [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    [row] = g.settle(items)
    assert row["action"] == "hold", row
    CG.write_plan(plan, [i for i in items if i["name"] != "AO3401A"])      # the next BOM no longer uses it
    try:
        CC.approve(plan, ["AO3401A"], who=ANN, comment="ok")
    except CG.ChangeError as e:
        assert "not in the plan any more" in str(e), e
    else:
        raise AssertionError("approved a part that is not in the plan")
    lg = LG.Ledger(lib)
    assert f.read_bytes() == before, "the released symbol is untouched"
    assert lg.head("AO3401A")["revision"] == 1 and lg.proposal("AO3401A")["approvals"] == []


def _export_plan(d):
    from tests.test_kicad_export import _plan
    return _plan(d)


def test_lib3_kicad_export_leaves_out_parts_waiting_for_sign_off():
    """LIB-3: parts awaiting approval (never released) were written to the KiCad library and the PCM package."""
    import tempfile
    from ecadlib import kicad_export as X
    with tempfile.TemporaryDirectory() as d:
        plan, items, http = _export_plan(d)
        data = json.loads(plan.read_text())
        for i in data:
            if i["name"] == "RES-0603-10k":
                i["status"] = "awaiting_approval"
        plan.write_text(json.dumps(data))
        out = Path(d) / "kicad"
        rep = X.export(plan, out, "lib", http=http, log=lambda *a: None)
        assert "RES-0603-10k" not in {r["part"] for r in rep["parts"]}
        sk = next(s for s in rep["skipped"] if s["part"] == "RES-0603-10k")
        assert sk["why"].startswith("awaiting_approval") and sk["says"][0]["text"].startswith("Waiting for sign-off"), sk
        assert "RES-0603-10k" not in (out / "lib.kicad_sym").read_text()
        rep2 = X.export(plan, Path(d) / "k2", "lib", include_held=True, http=http, log=lambda *a: None)
        assert "RES-0603-10k" in {s["part"] for s in rep2["skipped"]}, "--include-held is for held parts only"


def test_lib9_skipped_line_shows_one_reason_in_plain_words():
    from ecadlib import kicad_export as X, messages as M, term as TM
    why = ["pins: footprint pads 4-16 have no pin on the symbol", "height: 3D height 1.75 mm outside 0.8-1.45 mm for ^SOT-23"]
    says = [{k: e[k] for k in ("code", "text", "fix")} for e in M.explain_all(why)]
    rep = {"parts": [], "skipped": [{"part": "AO3401A", "why": "held: " + "; ".join(why), "says": says}], "footprints": {},
           "models": {}, "errors": [], "warnings": [], "symbols": 0, "library": "t"}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        X.print_result(rep, "kx", "lib/plan.json", TM.Term(buf, styled=False, width=400))
    line = next(l for l in buf.getvalue().splitlines() if "AO3401A" in l)
    assert len(says) == 2 and says[0]["text"] in line and "(+1 more)" in line and says[1]["text"] not in line, (line, says)


def test_int2_kicad_export_does_not_fetch_easyeda_when_unofficial_is_off():
    """INT-2: kicad-export re-fetched an EasyEDA symbol although [providers] unofficial = false."""
    import tempfile
    from ecadlib import kicad_export as X, rules as RU
    with tempfile.TemporaryDirectory() as d:
        plan, items, http = _export_plan(d)
        assert items["EEPART1"]["source"]["symbol"].startswith("easyeda:")
        seen = []

        class Rec:
            def __getattr__(self, k):
                return getattr(http, k)

            def get(self, url, *a, **k):
                seen.append(url)
                return http.get(url, *a, **k)
        rules = RU.resolve(None, plan.parent)
        rules = dict(rules, providers=dict(rules.get("providers") or {}, unofficial=False))
        rep = X.export(plan, Path(d) / "kicad", "lib", http=Rec(), log=lambda *a: None, rules=rules)
        assert not any("easyeda" in u.lower() for u in seen), seen
        row = next(r for r in rep["parts"] if r["part"] == "EEPART1")
        assert row["symbol"] and "read back" in row["symbol"] and any("unofficial = false" in n for n in row["notes"]), row
        rep = X.export(plan, Path(d) / "kicad2", "lib", http=Rec(), log=lambda *a: None)
        assert any("easyeda" in u.lower() for u in seen), "unofficial on (the default) still re-fetches"


# ---------------------------------------------------------------- part names (altium_export.to_rows)
def _lines(*rows):
    return [dict(zip(("designator", "value", "footprint", "manufacturer", "mpn", "description"), r)) for r in rows]


def _names(lines, prior=None):
    from ecadlib import altium_export as AX
    return {r["Part Number"]: r["Manufacturer Part Number"] for df in AX.to_rows(lines, prior=prior).values()
            for r in df.to_dict("records")}


def test_sys1_a_line_added_above_a_released_part_does_not_take_its_name():
    from ecadlib import altium_export as AX
    sam = ("C1,C2", "100nF", "C_0402_1005Metric", "Samsung", "CL05B104KO5NNNC", "Capacitor 100nF 16V X7R 0402")
    mur = ("C9", "100nF", "C_0402_1005Metric", "Murata", "GRM155R71H104KE14D", "Capacitor 100nF 50V X7R 0402")
    first = _names(_lines(sam))
    assert first == {"CAP-0402-100nF": "CL05B104KO5NNNC"}, first
    prior = AX.prior_names([{"name": n, "parameters": [{"name": "Manufacturer Part Number", "value": m}]} for n, m in first.items()])
    got = _names(_lines(mur, sam), prior)
    assert got == {"CAP-0402-100nF": "CL05B104KO5NNNC", "CAP-0402-100nF-GRM155R71H104KE14D": "GRM155R71H104KE14D"}, got
    assert _names(_lines(mur, sam)) == {"CAP-0402-100nF": "GRM155R71H104KE14D",          # no plan yet: first line,
                                        "CAP-0402-100nF-CL05B104KO5NNNC": "CL05B104KO5NNNC"}  # as before


def test_sys2_a_generic_line_never_gets_a_name_ending_in_a_dash():
    mur = ("C1,C2", "22pF", "C_0402_1005Metric", "Murata", "GRM1555C1H220JA01D", "Capacitor 22pF 50V C0G 0402")
    gen = ("C3", "22pF", "C_0402_1005Metric", "", "", "Capacitor 22pF 50V C0G 0402")
    want = {"CAP-0402-22pF": "", "CAP-0402-22pF-GRM1555C1H220JA01D": "GRM1555C1H220JA01D"}
    assert _names(_lines(mur, gen)) == want == _names(_lines(gen, mur)), (_names(_lines(mur, gen)), _names(_lines(gen, mur)))
    got = _names(_lines(mur, gen), {"CAP-0402-22pF": "GRM1555C1H220JA01D"})   # released the other way round: kept
    assert got == {"CAP-0402-22pF": "GRM1555C1H220JA01D", "CAP-0402-22pF-GENERIC": ""}, got


@lite
def test_sys3_second_librarian_run_does_not_say_a_part_shares_with_itself(tmp_path, monkeypatch):
    from ecadlib import librarian as L
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ECADLIB_USER", "t")
    for k in ("ANTHROPIC_API_KEY", "NEXAR_TOKEN"):
        monkeypatch.setenv(k, "")
        os.environ.pop(k, None)
    bom = tmp_path / "bom.csv"
    bom.write_text('Designator,Qty,Value,Footprint,Manufacturer,MPN,Description\n'
                   '"C1,C2",2,RC0805FR-0710KL,R_0805_2012Metric,Yageo,RC0805FR-0710KL,chip resistor\n'
                   'C5,1,1nF,C_0603_1608Metric,,,Capacitor 1nF 16V X7R 0603\n')
    plans = []
    for _ in range(2):
        L.run(bom, [], tmp_path / "lib", offline=True, cache=tmp_path / "cache", log=lambda *a: None)
        plans.append((tmp_path / "lib" / "plan.json").read_text())
    first = json.loads(plans[0])[0]
    assert not any(n.startswith(f"shared with {first['name']}") for n in json.loads(plans[1])[0]["librarian"]["notes"])
    assert plans[0] == plans[1], "a second run changes nothing"


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_bughunt_fplib"]))
