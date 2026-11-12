"""Open items of the 2026-11-12 deep dive, release side:
  * a hold lived only in plan.json: emptying a part's `verify` list by hand released it at the next export;
  * a pad changed to a custom shape with no outline passed the release check with no finding."""
import json
from ecadlib import release_check as RC, rules as RU

HOLD = "datasheet reader output (footprint): compare with the datasheet"


def item(verify=()):
    return {"name": "P1", "description": "part", "parameters": [], "symbol": {"ref": "P1", "file": None},
            "footprint": {"ref": "P1", "file": None}, "status": "ready", "verify": list(verify)}


def verify_fails(it, base):
    return [m for r, m in RC.check_item(it, base, RU.load(None))[0] if r == "verify"]


def record(tmp, verify):
    (tmp / "librarian_state.json").write_text(json.dumps({"P1": {"name": "P1", "verify": list(verify)}}), encoding="utf-8")


def test_a_hold_emptied_in_the_plan_alone_still_holds(tmp_path=None):
    import tempfile, pathlib
    tmp = pathlib.Path(tmp_path or tempfile.mkdtemp())
    record(tmp, [HOLD])
    f = verify_fails(item(), tmp)
    assert len(f) == 1 and f[0].startswith(HOLD) and "build record" in f[0], f
    assert any("build record" in p for p in RC.publish_problems(item(), tmp, RU.load(None), policy={}))


def test_a_hold_in_both_files_is_said_once_as_itself(tmp_path=None):
    import tempfile, pathlib
    tmp = pathlib.Path(tmp_path or tempfile.mkdtemp())
    record(tmp, [HOLD])
    assert verify_fails(item([HOLD]), tmp) == [HOLD]


def test_no_record_or_a_cleared_record_adds_nothing(tmp_path=None):
    import tempfile, pathlib
    tmp = pathlib.Path(tmp_path or tempfile.mkdtemp())
    assert verify_fails(item(), tmp) == []
    record(tmp, [])
    assert verify_fails(item(), tmp) == []
    (tmp / "librarian_state.json").write_text("{ not json", encoding="utf-8")
    assert verify_fails(item(), tmp) == []


def test_the_record_is_read_again_after_it_changes(tmp_path=None):
    import tempfile, pathlib
    tmp = pathlib.Path(tmp_path or tempfile.mkdtemp())
    record(tmp, [HOLD])
    assert verify_fails(item(), tmp)
    record(tmp, [])
    assert verify_fails(item(), tmp) == []


PAD = 'smd roundrect (at -0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25)'
POLY = "(primitives (gr_poly (pts (xy -0.4 -0.475) (xy 0.4 -0.475) (xy 0.4 0.475) (xy -0.4 0.475)) (width 0) (fill yes)))"


def _with_pad(new):
    from ecadlib import stdparts as SP
    txt = SP.build("R_0603_1608Metric")[0]
    assert PAD in txt
    return txt.replace(PAD, new, 1)


def test_a_custom_pad_with_no_outline_is_an_error():
    from ecadlib import footprint_check as FC
    head = 'smd custom (at -0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask")'
    for new in (head, head + " (options (clearance outline) (anchor circle)) (primitives)"):
        got = [m for lvl, rule, m in FC.check(_with_pad(new)) if lvl == "error" and rule == "copper"]
        assert got and "no outline" in got[0] and "pad 1" in got[0], got


def test_a_custom_pad_with_an_outline_and_a_plain_pad_are_not():
    from ecadlib import footprint_check as FC
    head = 'smd custom (at -0.825 0) (size 0.1 0.1) (layers "F.Cu" "F.Paste" "F.Mask") (options (clearance outline) (anchor rect)) '
    for new in (head + POLY, PAD):
        assert not [m for lvl, rule, m in FC.check(_with_pad(new)) if lvl == "error"], new
