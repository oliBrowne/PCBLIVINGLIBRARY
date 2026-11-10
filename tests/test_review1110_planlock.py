"""Full review 2026-11-10: the plan lock covered only workspace plans (<ws>/librarian/<name>/plan.json). With the
documented default out/librarian/plan.json there was no lock, so a reject recorded while release-check held the plan in
memory was overwritten by its write and the rejected part was exported. Every plan now has a lock: the workspace's
for a workspace plan (the one the web UI takes), else plan.lock beside plan.json."""
import threading

from ecadlib import approvals as AP, librarian as L
from ecadlib.fsutil import plan_lock
from tests.fixtures_lite import lite


def _held_elsewhere(folder):
    """Is <folder>/plan.lock held right now? Asked from another thread (the lock is re-entrant within one)."""
    got = []

    def ask():
        with plan_lock(folder, wait=False) as free:
            got.append(free)
    t = threading.Thread(target=ask)
    t.start()
    t.join()
    return not got[0]


@lite
def test_a_plan_outside_a_workspace_is_locked_beside_plan_json(tmp_path):
    plan = tmp_path / "out" / "librarian" / "plan.json"         # the default layout without `ecadlib init`: its
    plan.parent.mkdir(parents=True)                             # grandparent is not named "librarian"
    plan.write_text("[]", encoding="utf-8")
    assert AP.plan_workspace(plan) is None
    assert AP.plan_lock_dir(plan) == plan.parent.resolve()
    assert not _held_elsewhere(plan.parent)
    with AP.cli_plan_lock(plan):                                # release-check, review, resolve, dedupe --merge
        assert _held_elsewhere(plan.parent)
    assert not _held_elsewhere(plan.parent)
    with L._decisions_lock(plan):                               # the librarian's and the datasheet batch's plan write
        assert _held_elsewhere(plan.parent)


@lite
def test_a_workspace_plan_keeps_the_workspace_lock_the_web_ui_takes(tmp_path):
    plan = tmp_path / "ws" / "librarian" / "board" / "plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text("[]", encoding="utf-8")
    assert AP.plan_lock_dir(plan) == (tmp_path / "ws").resolve()
    with AP.cli_plan_lock(plan):
        assert _held_elsewhere(tmp_path / "ws")


@lite
def test_approve_waits_for_the_lock_and_gives_up_instead_of_overwriting(tmp_path, monkeypatch):
    plan = tmp_path / "out" / "plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(AP, "DECISION_WAIT", 0.2)
    ran, out = [], []
    monkeypatch.setattr(AP, "_run", lambda a: ran.append(1) or 0)

    def decide():
        try:
            out.append(AP.main([str(plan), "reject", "--part", "X", "-m", "no"]))
        except SystemExit as e:
            out.append(str(e))
    with AP.cli_plan_lock(plan):                                # another command is rewriting the plan
        t = threading.Thread(target=decide)
        t.start()
        t.join()
    assert not ran and "another ecadlib command is changing this plan" in out[0], (ran, out)
    assert AP.main([str(plan), "reject", "--part", "X", "-m", "no"]) == 0 and ran == [1]
