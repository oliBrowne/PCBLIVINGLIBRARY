"""Through-hole parts whose datasheet prints no hole (live benchmark 2026-10-23: KTY83 axial glass sensor, GBU4A
flat-pin bridge, KBU4A): held, with the IPC-2222 level B hole suggested; a person confirms it with `ecadlib fp-edit
... hole` and the part builds with it (Oliver 2026-10-23: keep holding, suggest IPC-2222, release once confirmed).
Run:  python3 -m tests.test_ipc_hole_1023"""
import contextlib, io, json, tempfile
from pathlib import Path

from ecadlib import datasheet as D, footprint_adv as FA, footprint_gen as F, fp_edit as FE


def kbu(**kw):
    s = {"package_type": "custom", "name": "KBU", "pin_count": 4, "terminal_width": {"min": 1.02, "max": 1.27},
         "unresolved": ["land pattern (not given in datasheet)"],
         "pad_table": {"unit": "mm", "default_pad": {"type": "tht", "d": 2.2, "drill": 1.5, "shape": "circle"},
                       "pads": [{"number": str(i + 1), "x": 5.08 * i, "y": 0} for i in range(4)],
                       "body": {"x1": -2, "y1": -2, "x2": 17.24, "y2": 2}}}
    s.update(kw)
    return s


def test_the_ipc_2222_hole_is_the_largest_lead_plus_0_2_rounded_up():
    assert D.ipc_hole({"terminal_width": {"max": 0.55}}) == 0.75          # KTY83 wire
    assert D.ipc_hole({"terminal_width": {"min": 1.02, "max": 1.27}}) == 1.5   # GBU4A pin width
    assert D.ipc_hole({"terminal_width": {"nom": 0.5, "tol": 0.05}}) == 0.75
    assert D.ipc_hole({"terminal_width": 0.6}) == 0.8
    assert D.ipc_hole({}) is None and D.ipc_hole({"terminal_width": {"nom": 0}}) is None


def test_the_hole_hold_suggests_the_ipc_hole_and_how_to_confirm_it():
    why = D.tht_holes_unread(kbu())
    assert why.startswith("no footprint:") and "Suggested hole: 1.5 mm (IPC-2222 level B" in why, why
    assert "fp-edit PLAN --part PART hole 1.5" in why and "the part builds with it" in why and "diagonal" in why
    # no lead size read: held as before, nothing suggested
    why = D.tht_holes_unread(kbu(terminal_width=None))
    assert why and "Suggested hole" not in why


def test_a_blank_footprint_with_no_hole_suggests_it_and_names_what_else_is_blank():
    # KTY83 (read 2026-10-23): SOD68 body and a 0.55 mm lead, no bend spacing, no hole, no pad table
    s = {"package_type": "gullwing", "name": "D_DO-34_SOD68_Axial", "body_length": {"max": 3.04},
         "body_width": {"max": 1.6}, "terminal_width": {"max": 0.55},
         "unresolved": ["pitch", "drill", "height_max", "pad_table"]}
    why = D.no_footprint(s, KeyError("pitch"))
    assert "Suggested hole: 0.75 mm" in why and "the hole alone does not build it" in why, why
    assert "Suggested hole" not in D.no_footprint(dict(s, unresolved=["pitch"]), KeyError("pitch"))
    assert "Suggested hole" not in D.no_footprint(dict(s, drill={"nom": 0.8}), KeyError("pitch"))


def test_a_confirmed_hole_lifts_the_hold_and_builds_kicads_pad_round_it():
    res = {"spec": kbu(), "symbol": {"pins": []}}
    out, notes = FE.apply({"hole": 1.5, "by": "oliver", "at": "2026-10-23T23:40:00"}, res)
    spec = out["spec"]
    assert D.tht_holes_unread(spec) is None and not D.holes_unread(spec)
    assert res["spec"]["pad_table"]["default_pad"]["drill"] == 1.5 and res["spec"]["pad_table"]["default_pad"]["d"] == 2.2
    pads = FA.table_layout(spec)[0]
    FA.tht_pad_size(pads, spec.get("pitch"))
    assert {(p["w"], p["h"], tuple(p["drill"])) for p in pads} == {(2.5, 2.5, (1.5, 1.5))}, pads   # 1.5 + 2 x 0.5
    assert notes == ["footprint edited: hole confirmed by hand: 1.5 mm (the IPC-2222 suggestion), the pads sized "
                     "round it by KiCad's rule (oliver, 2026-10-23T23:40:00)"], notes
    F.build(spec, "B")                                # builds


def test_a_confirmed_hole_leaves_smd_pads_and_mounting_holes_alone():
    s = kbu()
    s["pad_table"]["pads"] += [{"number": "", "type": "npth", "x": 7.6, "y": 5, "drill": 3.2, "d": 3.2},
                               {"number": "5", "type": "smd", "x": 7.6, "y": -5, "w": 2, "h": 1}]
    out, _ = FE.apply({"hole": 1.6}, {"spec": s, "symbol": {}})
    t = out["spec"]["pad_table"]["pads"]
    assert t[-2]["drill"] == 3.2 and t[-2]["d"] == 3.2 and t[-1] == s["pad_table"]["pads"][-1]
    assert out["spec"]["unresolved"] == []


def test_a_confirmed_hole_on_a_read_with_no_pad_table_keeps_its_positions_blank():
    s = {"package_type": "custom", "terminal_width": {"max": 0.55}, "unresolved": ["pitch", "drill", "pad_table"]}
    out, _ = FE.apply({"hole": 0.75}, {"spec": s, "symbol": {}})
    assert out["spec"]["drill"] == 0.75 and out["spec"]["unresolved"] == ["pitch", "pad_table"]


def test_fp_edit_hole_defaults_to_the_ipc_suggestion():
    with tempfile.TemporaryDirectory() as d:
        ds = Path(d) / "datasheet"
        ds.mkdir()
        (ds / "GBU4A.json").write_text(json.dumps({"name": "GBU4A", "footprint": "KBU", "pins": [
            {"num": str(i), "name": n} for i, n in ((1, "+"), (2, "~"), (3, "~"), (4, "-"))]}))
        (ds / "KBU.json").write_text(json.dumps(kbu()))
        (Path(d) / "plan.json").write_text("{}")
        with contextlib.redirect_stdout(io.StringIO()) as o:
            rc = FE.main([str(Path(d) / "plan.json"), "--part", "GBU4A", "--user", "oliver", "hole"])
        assert rc == 0, o.getvalue()
        assert FE.load(d)["GBU4A"]["hole"] == 1.5 and "hole confirmed by hand: 1.5 mm" in o.getvalue()
        with contextlib.redirect_stdout(io.StringIO()):
            FE.main([str(Path(d) / "plan.json"), "--part", "GBU4A", "--user", "oliver", "hole", "1.7"])
        assert FE.load(d)["GBU4A"]["hole"] == 1.7


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("ok")
