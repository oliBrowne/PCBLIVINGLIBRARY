"""2026-10-26 bug hunt "planlock": plan.json writers that raced with decisions recorded by other front ends (the web UI
/ MCP approve or reject, `ecadlib approve`), the lock order between the library lock and plan.lock, and non-atomic
state writes. Offline. Run:  python3 -m pytest -q tests/test_hunt1026_planlock.py   (or python -m tests.run_all)"""
import contextlib, io, json, os, threading, time
from pathlib import Path

from tests.fixtures_lite import lite

ANN = "Ann Lee <ann@example.com>"


def _items(plan):
    return {i["name"]: i for i in json.loads(Path(plan).read_text(encoding="utf-8"))}


def _cli_reject(plan, part):
    """`ecadlib approve PLAN reject --part PART`, as another process would run it while a long step works."""
    from ecadlib import approvals as AP
    with contextlib.redirect_stdout(io.StringIO()):
        AP.main([str(plan), "reject", "--part", part, "--user", ANN, "-m", "wrong pinout"])


@lite
def test_planlock_01_style_apply_keeps_a_reject_recorded_during_the_model_call(tmp_path, monkeypatch):
    """`ecadlib style apply` read plan.json at its start and wrote its copy at the end (with --ai a model call in
    between). A reject recorded meanwhile was overwritten: the rejected part came back ready for the next push."""
    from ecadlib import house_style as HS
    from ecadlib.app import Workspace
    from tests.ui_fixture import build
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    with contextlib.redirect_stdout(io.StringIO()):
        ws, bom = build(tmp_path / "ws")
    plan = ws.root / "librarian" / "sample_bom" / "plan.json"
    ready = [n for n, i in _items(plan).items() if i["status"] == "ready"]
    rejected, other = ready[0], ready[1]
    prof = tmp_path / "house_style.json"
    prof.write_text(json.dumps({"schema_version": HS.SCHEMA_VERSION, "direction": [], "learned_from": []}), encoding="utf-8")
    ui = Workspace(ws.root, [], providers=["digikey"], offline=True)          # another process in real life
    said = []

    def adapt(items, prof_, rules=None, only=None):     # the restyle; meanwhile (the model call) a reject comes in
        done = {}
        for it in items:
            if it["name"] in (rejected, other):
                was = it.get("description") or ""
                it["description"] = was + " (house style)"
                HS._record(it, [{"field": "description", "was": was, "now": it["description"], "by": "code", "why": "t"}], prof_)
                done[it["name"]] = [{"field": "description", "was": was, "now": it["description"], "by": "code"}]
        said.append(ui.approve_part(bom, rejected, "reject", "Ann Lee", comment="wrong pinout")["decision"])
        return done
    monkeypatch.setattr(HS, "adapt_items", adapt)
    with contextlib.redirect_stdout(io.StringIO()):
        rc = HS.main(["--profile", str(prof), "apply", str(plan)])
    assert rc == 0 and said
    items = _items(plan)
    assert [a["decision"] for a in items[rejected].get("approvals") or []] == ["reject"], items[rejected].get("approvals")
    assert items[rejected]["status"] != "ready", items[rejected]["status"]
    assert items[other]["description"].endswith("(house style)")          # the restyle itself still went in
    assert items[rejected]["description"].endswith("(house style)")


@lite
def test_planlock_02_style_apply_leaves_a_part_another_command_changed_meanwhile(tmp_path, monkeypatch):
    """A part whose fields another command changed while the restyle ran keeps that change (it is not restyled)."""
    from ecadlib import house_style as HS
    from tests.ui_fixture import build
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    with contextlib.redirect_stdout(io.StringIO()):
        ws, bom = build(tmp_path / "ws")
    plan = ws.root / "librarian" / "sample_bom" / "plan.json"
    name = next(n for n, i in _items(plan).items() if i["status"] == "ready")
    prof = tmp_path / "house_style.json"
    prof.write_text(json.dumps({"schema_version": HS.SCHEMA_VERSION, "direction": [], "learned_from": []}), encoding="utf-8")

    def adapt(items, prof_, rules=None, only=None):
        it = next(i for i in items if i["name"] == name)
        it["description"] = "restyled"
        disk = json.loads(plan.read_text(encoding="utf-8"))          # another command changes the part meanwhile
        next(i for i in disk if i["name"] == name)["comment"] = "changed by hand"
        plan.write_text(json.dumps(disk, indent=2), encoding="utf-8")
        return {name: [{"field": "description", "was": "", "now": "restyled", "by": "code"}]}
    monkeypatch.setattr(HS, "adapt_items", adapt)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        HS.main(["--profile", str(prof), "apply", str(plan)])
    it = _items(plan)[name]
    assert it["comment"] == "changed by hand" and it["description"] != "restyled", it
    assert "changed by another ecadlib command" in out.getvalue(), out.getvalue()


@lite
def test_planlock_03_part_approve_keeps_a_reject_recorded_while_it_ran(monkeypatch):
    """`ecadlib part approve` (and reject / revert / adopt, and publishers settling a hand edit) held the library lock,
    not plan.lock, read the plan at its start and wrote it at the end: a reject recorded meanwhile with `ecadlib
    approve` or the web UI was overwritten and the rejected part came back ready."""
    from ecadlib import change_cli as CC, release_check as RC
    from tests.test_change_control import demo
    from tests.test_change_control_workflow import SGD, held_hand_edit
    plan, lib = demo()
    with contextlib.redirect_stderr(io.StringIO()):
        digest = held_hand_edit(plan, lib, SGD)
    other = next(n for n, i in _items(plan).items() if i["status"] == "ready" and n != "AO3401A")
    real, said = RC.apply, []

    def apply(*a, **k):                                  # while part approve checks the version it releases
        if not said:
            said.append(1)
            _cli_reject(plan, other)
        return real(*a, **k)
    monkeypatch.setattr(RC, "apply", apply)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = CC.main(["approve", str(lib), "--part", "AO3401A", "--digest", digest[:8], "--user", ANN, "-m", "ok"])
    assert rc == 0 and said
    it = _items(plan)[other]
    assert [a["decision"] for a in it.get("approvals") or []] == ["reject"], it.get("approvals")
    assert it["status"] != "ready", it["status"]


@lite
def test_planlock_04_plan_lock_is_reentrant_in_a_thread_and_keeps_other_threads_out(tmp_path):
    from ecadlib.fsutil import plan_lock
    with plan_lock(tmp_path, wait=False) as a:
        with plan_lock(tmp_path, wait=False) as b:            # an inner plan write under an outer lock
            assert a and b
        got = []
        t = threading.Thread(target=lambda: got.append(plan_lock(tmp_path, wait=False).__enter__()))
        t.start(); t.join(5)
        assert got == [False]
    got = []

    def other():
        with plan_lock(tmp_path, wait=False) as g:
            got.append(g)
    t = threading.Thread(target=other)
    t.start(); t.join(5)
    assert got == [True]                                      # released after the outer lock


def _overridable(plan):
    """A held part that release anyway makes ready (so its release is recorded in the ledger: the library lock)."""
    return next(n for n, i in _items(plan).items() if i["status"] == "held" and i.get("hold_reasons")
                and all(r.startswith("verify:") for r in i["hold_reasons"]))


def _deadlock_case(tmp_path, monkeypatch, second):
    """A writer holds the library lock (a librarian run, `ecadlib part approve`, a publisher) and then writes plan.json
    under plan.lock; `second` starts while it holds the library lock. Both must finish (no wait for each other)."""
    from ecadlib import change_gate as CG, ledger as LG
    from tests.ui_fixture import build
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ECADLIB_LOCK_WAIT", "30")
    with contextlib.redirect_stdout(io.StringIO()):
        ws, bom = build(tmp_path / "ws")
    plan = ws.root / "librarian" / "sample_bom" / "plan.json"
    held, started, done, errors = threading.Event(), threading.Event(), [], []

    def writer():
        try:
            with LG.locked(plan.parent):
                items = json.loads(plan.read_text(encoding="utf-8"))
                held.set()
                started.wait(10)                              # the other front end has started
                time.sleep(0.3)
                CG.write_plan(plan, items)
            done.append("writer")
        except Exception as e:                               # pragma: no cover - reported below
            errors.append(e)

    def other():
        held.wait(10)
        started.set()
        try:
            second(ws, bom, plan)
            done.append("other")
        except ValueError as e:                              # a refusal ("try again") is fine; a hang is not
            done.append(f"refused: {e}")
    ts = [threading.Thread(target=writer, daemon=True), threading.Thread(target=other, daemon=True)]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join(20)
    assert not errors, errors
    assert len(done) == 2, f"deadlock: only {done} finished in {time.time() - t0:.1f} s"
    return done


@lite
def test_planlock_05_ui_release_anyway_and_a_library_writer_do_not_deadlock(tmp_path, monkeypatch):
    def second(ws, bom, plan):
        name = _overridable(plan)
        ws.release_check(bom, allow=[name], user="Ann Lee")
    done = _deadlock_case(tmp_path, monkeypatch, second)
    assert "other" in done, done                              # it waited the few seconds for the library, then ran


@lite
def test_planlock_06_cli_release_anyway_and_a_library_writer_do_not_deadlock(tmp_path, monkeypatch):
    from ecadlib import release_check as RC

    def second(ws, bom, plan):
        name = _overridable(plan)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            RC.main([str(plan), "--allow", name, "--user", ANN, "-o", str(plan.parent / "rc")])
        assert _items(plan)[name].get("allow"), "the CLI allow was lost"
    done = _deadlock_case(tmp_path, monkeypatch, second)
    assert "other" in done, done


@lite
def test_planlock_07_ui_part_decision_and_a_library_writer_do_not_deadlock(tmp_path, monkeypatch):
    """The web UI's decide_change (`part approve / reject` from the part card) used to take plan.lock first and then
    the library lock inside, the reverse of a librarian run's order."""
    def second(ws, bom, plan):
        name = next(iter(_items(plan)))
        try:
            ws.decide_change(bom, name, "reject", ANN, comment="no")
        except ValueError as e:
            if "another ecadlib command" in str(e):
                raise AssertionError(f"refused although the library lock was free soon: {e}")
            # no held change for this part: a plain refusal from change control, after both locks were taken
    _deadlock_case(tmp_path, monkeypatch, second)


class _Crash(OSError):
    pass


def _crash_at_rename(monkeypatch, targets):
    """The process dies (here: an OSError) at the moment the new file would take the old one's place, for these paths
    only. An atomic writer leaves the old file whole; one that writes in place has already replaced it by then."""
    from ecadlib import fsutil
    real, targets = _REAL_REPLACE.setdefault("f", fsutil.replace), {Path(t).resolve() for t in targets}
    _HIT.clear()

    def replace(src, dst):
        if Path(dst).resolve() in targets:
            _HIT.append(str(dst))
            raise _Crash(f"crashed while writing {dst}")
        return real(src, dst)
    monkeypatch.setattr(fsutil, "replace", replace)


_REAL_REPLACE, _HIT = {}, []


def _kept(path, old):
    """The old file is whole. On the code before the fix (an in-place write_text) the file holds the new text here, and
    the crash at the rename never came (the writer did not write through write_text_atomic): both fail."""
    assert Path(path).read_text(encoding="utf-8") == old, f"{path} was written in place (a crash leaves half a file)"
    assert _HIT, f"{path}: not written through an atomic replace"


@lite
def test_planlock_08_state_files_are_written_atomically(tmp_path, monkeypatch):
    """Report and state JSON that other commands read back (a365 plan, check-a365 state, dblib-sync report, the
    datasheet read's spec / symbol / sidecar, the web UI's a365 plan) were written in place: a crash left half a file.
    Each writer runs here (offline, the network faked) with a crash at its final rename: the old file stays whole."""
    import copy
    from ecadlib import altium365 as A, altium_dblib_sync as DS, datasheet as D, upstream as U, userconfig as UC
    from ecadlib.app import Workspace
    from tests import test_datasheet as TD
    from tests.ui_fixture import BOM
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    old = '{"old": "whole"}\n'
    quiet = contextlib.redirect_stdout(io.StringIO())
    # ecadlib altium365 plan BOM -o plan.json
    out = tmp_path / "a365" / "plan.json"
    out.parent.mkdir()
    out.write_text(old, encoding="utf-8")
    _crash_at_rename(monkeypatch, [out])
    try:
        with quiet, contextlib.redirect_stderr(io.StringIO()):
            A.main(["plan", str(BOM), "-o", str(out)])
    except (_Crash, SystemExit):
        pass
    _kept(out, old)
    # the web UI's Altium 365 plan (no librarian plan yet: planned from the triage)
    ws = Workspace(tmp_path / "ws", [], providers=[], offline=True)
    bom = ws.save_upload("bom.csv", BOM.read_bytes())
    target = ws.root / "altium365" / f"plan_{ws._key(ws._bom_path(bom))}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(old, encoding="utf-8")
    _crash_at_rename(monkeypatch, [target])
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            ws.altium365_plan(bom)
    except _Crash:
        pass
    _kept(target, old)
    # the datasheet read's spec / symbol / sidecar / review
    res = D.process(copy.deepcopy(TD.GOOD), "TESTPART", "u")
    names = [p.name for p in D.write(res, tmp_path / "ds1").values()]
    for name in names:                                # a fresh folder each time: the file is not there yet
        d = tmp_path / f"ds_{name}"
        _crash_at_rename(monkeypatch, [d / name])
        try:
            D.write(copy.deepcopy(res), d)
        except _Crash:
            pass
        assert not (d / name).exists() and _HIT, f"{name}: written in place (a crash leaves half a file)"
    # upstream check-a365 --state (Nexar faked: no network)
    plan = tmp_path / "up" / "plan.json"
    plan.parent.mkdir()
    plan.write_text("[]", encoding="utf-8")
    state = tmp_path / "up" / "state.json"
    state.write_text(old, encoding="utf-8")
    monkeypatch.setattr(UC, "secret", lambda name: "token" if name == "NEXAR_TOKEN" else None)
    monkeypatch.setattr(A, "Nexar", lambda token: object())
    monkeypatch.setattr(U, "workspace_components", lambda client, w: [])
    _crash_at_rename(monkeypatch, [state])
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            U.main(["check-a365", str(plan), "--workspace", "https://ws.example", "--state", str(state),
                    "-o", str(tmp_path / "up" / "drift")])
    except (_Crash, SystemExit):
        pass
    _kept(state, old)
    # dblib-sync -o report.json
    rep = tmp_path / "dblib_report.json"
    rep.write_text(old, encoding="utf-8")
    _crash_at_rename(monkeypatch, [rep])
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            DS.main([str(plan), "--to", str(tmp_path / "dblib"), "-o", str(rep)])
    except (_Crash, SystemExit):
        pass
    _kept(rep, old)


@lite
def test_planlock_09_library_lock_is_kept_between_settle_and_baseline(tmp_path, monkeypatch):
    # review C1: librarian.run / fill_plan took plan.lock, then the gate's steps each took and let go of the library
    # lock (settle closes it), so a CLI `part reject` (library lock, then plan.lock) got in between and the run's
    # baseline waited ECADLIB_LOCK_WAIT for a lock the CLI held while it waited for plan.lock. The writers now hold the
    # library lock across the block: the CLI waits for the whole block and nothing times out.
    import threading, time
    from ecadlib import ledger as LG, change_gate as CG
    from ecadlib.fsutil import plan_lock
    monkeypatch.setenv("ECADLIB_LOCK_WAIT", "3")
    ws = tmp_path / "ws"
    lib = ws / "librarian" / "bom"
    lib.mkdir(parents=True)
    (lib / "plan.json").write_text("[]")
    g = CG.Guard(lib, "librarian")
    g.begin([])
    log, started = [], threading.Event()

    def cli_reject():
        started.set()
        with LG.locked(lib):
            log.append("cli library lock")
            with plan_lock(ws, wait=True):
                log.append("cli plan lock")
    with LG.locked(lib), plan_lock(ws, wait=True):    # as librarian.run and datasheet.fill_plan now do
        g.settle([])
        t = threading.Thread(target=cli_reject, daemon=True)
        t.start()
        started.wait(2)
        time.sleep(0.3)
        g.baseline([])                                # did not wait for the library lock: no LedgerError
        log.append("run baseline")
    t.join(5)
    assert log == ["run baseline", "cli library lock", "cli plan lock"], log


@lite
def test_planlock_09b_librarian_and_datasheet_runs_keep_the_library_lock_into_baseline(tmp_path, monkeypatch):
    """The real writers: when a librarian run (Workspace.librarian_run) and a datasheet fill (datasheet.fill_plan) reach
    their baseline step, after settle let go of the gate's own hold, another thread still cannot take the library
    lock (the writers hold it across the block). Before the fix it was free there."""
    import threading
    from types import SimpleNamespace
    from ecadlib import change_gate as CG, datasheet as D, ledger as LG
    from tests.ui_fixture import build
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    with contextlib.redirect_stdout(io.StringIO()):
        ws, bom = build(tmp_path / "ws")
    plan = ws.root / "librarian" / "sample_bom" / "plan.json"
    seen, real = [], CG.Guard.baseline

    def baseline(self, items):
        got = []

        def probe():                                  # another thread: a CLI `part reject` wanting the library lock
            try:
                LG.unlock(LG.lock(self.folder, wait=0.3))
                got.append(True)
            except LG.LedgerError:
                got.append(False)
        t = threading.Thread(target=probe, daemon=True)
        t.start()
        t.join(5)
        seen.append((self.writer, got))
        return real(self, items)
    monkeypatch.setattr(CG.Guard, "baseline", baseline)
    with contextlib.redirect_stdout(io.StringIO()):
        ws.librarian_run(bom, ai=False)
        D.fill_plan(plan, SimpleNamespace(http=None, key=None), ws.rules(), log=lambda *a: None)
    writers = {w: g for w, g in seen}
    assert set(writers) >= {"librarian", "datasheet"}, seen
    for w, got in seen:
        assert got == [False], f"{w}: the library lock was free between settle and baseline ({seen})"
