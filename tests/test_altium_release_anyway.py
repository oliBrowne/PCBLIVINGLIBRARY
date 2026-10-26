"""Release anyway in the Altium dialog (2026-10-20): a held part can be released from the ecadlib popup, not only from
the review queue. The dialog shows why the part is held, asks why it is fine, and ecadlib releases it the way the
queue's "release anyway" does (release-check --allow, who and why in the plan, revision 1 in the change-control
ledger), then puts it into the library. python3 -m tests.test_altium_release_anyway"""
import json, re
from unittest import mock

from ecadlib import altium_forms as AF, altium_menu as AM, ledger as LG, release_check as RC
from tests import test_altium_menu as TM

MPN = TM.CAP["mpn"]                                      # a standard part: ready offline, unless the check is told no
HEIGHT = ("height", "no 3D height (no 3D body and no HEIGHT)")


def _held_check(*fails):
    real = RC.check_item

    def check(it, base, rules):
        f, w, info = real(it, base, rules)
        return f + list(fails), w, info
    return mock.patch.object(RC, "check_item", check)


def _routine(text, name):
    m = re.search(rf"(?ms)^(Procedure|Function) {re.escape(name)}\b.*?^End;", text)
    assert m, name
    return m.group(0)


def _held(menu):
    r = AM.run_job(TM._job(menu, {"action": "add-part", **TM.CAP}))
    assert r["status"] == "held" and r["install"] == "", r
    return r


def test_held_part_offers_release_with_its_reasons():
    menu, cfg = TM._setup()
    with _held_check(HEIGHT):
        r = _held(menu)
    assert r["offer_release"] == "1" and r["release_part"] == "CAP-0402-100nF" and r["release_mpn"] == MPN, r
    assert r["release_bom"].endswith("parts.csv") and "3D height" in r["release_why1"], r
    assert "release_why2" not in r, r


def test_missing_files_or_held_changes_are_not_offered():
    menu, cfg = TM._setup()
    r = AM.run_job(TM._job(menu, {"action": "add-part", "mpn": "NE555DR", "manufacturer": "Texas Instruments"}))
    assert r["status"] == "held" and "offer_release" not in r, r          # no symbol: nothing to release
    assert AM.releasable({"hold_reasons": [f"{RC.CHANGE_HOLD} pads moved; a person approves it"]}) == []
    assert AM.releasable({"hold_reasons": ["height: no 3D height (no 3D body and no HEIGHT)",
                                           "files: no footprint model (cannot be overridden)"]}) == []
    assert AM.releasable({"hold_reasons": ["height: no 3D height (no 3D body and no HEIGHT)"]})


def test_release_needs_a_reason_and_then_releases_and_publishes():
    menu, cfg = TM._setup()
    with _held_check(HEIGHT):
        held = _held(menu)
        req = {"action": "release", "bom": held["release_bom"], "mpn": MPN, "part": held["release_part"],
               "release_key": held["release_key"]}                    # what the dialog showed (R-MN-2)
        r = AM.run_job(TM._job(menu, dict(req, reason="   ")))
        assert r["status"] == "error" and "needs a reason" in r["message"], r
        r = AM.run_job(TM._job(menu, dict(req, reason="height checked on the datasheet: 0.5 mm", user="Oliver <o@x>")))
    assert r["status"] == "ready" and r["install"].endswith(".DbLib"), r
    assert r["message"].startswith("Released anyway by Oliver <o@x>: height checked on the datasheet: 0.5 mm."), r
    plan = cfg["workspace"] / "librarian" / "parts" / "plan.json"
    plan = plan if plan.is_file() else next(cfg["workspace"].glob("librarian/*/plan.json"))
    it = next(i for i in json.loads(plan.read_text(encoding="utf-8")) if i["name"] == "CAP-0402-100nF")
    assert it["status"] == "ready" and it["release_check"]["result"] == "allowed", it["release_check"]
    assert it["allow"]["user"] == "Oliver <o@x>" and it["allow"]["reason"] == "height checked on the datasheet: 0.5 mm", it["allow"]
    assert any("3D height" in f for f in it["allow"]["failures"]), it["allow"]
    head = LG.Ledger(plan.parent).head("CAP-0402-100nF")            # change control: revision 1 is what was released
    assert head and head["revision"] == 1, head


def test_release_of_a_part_already_released_just_publishes():
    menu, cfg = TM._setup()
    with _held_check(HEIGHT):
        held = _held(menu)
        req = {"action": "release", "bom": held["release_bom"], "mpn": MPN, "part": held["release_part"], "reason": "ok",
               "release_key": held["release_key"]}
        assert AM.run_job(TM._job(menu, req))["status"] == "ready"
        r = AM.run_job(TM._job(menu, req))                          # twice (the queue did it meanwhile): no new release
    assert r["status"] == "ready" and not r["message"].startswith("Released anyway"), r
    r = AM.run_job(TM._job(menu, dict(req, part="NOPE")))
    assert r["status"] == "error" and "not in the library plan" in r["message"], r


def test_a_missing_height_is_a_note_on_a_ready_part():
    """Oliver 2026-10-20: a missing 3D height is a note to check, not a hold. The part goes into the library and the
    dialog says so."""
    menu, cfg = TM._setup()
    real = RC.check_item

    def check(it, base, rules):
        f, w, info = real(it, base, rules)
        return f, w + ["height: no 3D height (no 3D body and no HEIGHT)"], info
    with mock.patch.object(RC, "check_item", check):
        r = AM.run_job(TM._job(menu, {"action": "add-part", **TM.CAP}))
    assert r["status"] == "ready" and r["install"].endswith(".DbLib"), r
    assert "Note: There is no 3D height" in r["message"] and "Check the 3D body in Altium." in r["message"], r


def test_dialog_shows_reasons_then_asks_why():
    pas, dfm = AF.dialog_pas_text(), AF.dialog_dfm_text()
    assert "object ReleaseBtn: TPanel" in dfm and "OnClick = ReleaseClick" in dfm
    btn = dfm[dfm.index("object ReleaseBtn: TPanel"):dfm.index("object Secondary: TPanel")]
    assert "Visible = False" in btn, btn                                                     # hidden until offered
    fin = _routine(pas, "Finish")
    held = fin[fin.index("Else If (St = 'held')"):]
    assert held.index("Buttons('Done', 'Open review queue', False)") < held.index("ShowRelease"), held
    assert "Answer(Job, 'offer_release') = '1'" in _routine(pas, "ShowRelease")
    ask = _routine(pas, "AskRelease")                   # step 1: the reasons, nothing released
    assert "release_why" in ask and "Phase := 'confirm_release'" in ask and "StartJob" not in ask, ask
    start = _routine(pas, "StartRelease")               # step 2: a reason, or nothing happens
    assert start.index("InputBox(") < start.index("If Why = '' Then") < start.index("StartJob('release')"), start
    assert "'Checked it: fine as it is'));" in start            # one Enter releases; the reason can still be changed
    assert "Ask(Job, 'reason', Why)" in start and "Run('release')" in start
    assert "StartRelease" in _routine(pas, "TEcadlibDialog.PrimaryClick")
    assert "AskRelease" in _routine(pas, "TEcadlibDialog.ReleaseClick")
    assert "ReleaseBtn.Visible := False" in _routine(pas, "ClearRows")       # never left over from another result
    assert "release" in AM.ACTIONS


def test_units_have_no_problems():
    from ecadlib import altium_native as AN
    files = {AM.UNIT: AM.pas_text(), AF.DIALOG_UNIT: AF.dialog_pas_text(), AF.DIALOG_FORM: AF.dialog_dfm_text(),
             AN.UNIT: AN.pas_text()}
    assert AM.project_problems(files) == [], AM.project_problems(files)


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_altium_release_anyway"]))
