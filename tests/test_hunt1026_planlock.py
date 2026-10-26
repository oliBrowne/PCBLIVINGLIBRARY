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


def test_planlock_08_state_files_are_written_atomically():
    """Report and state JSON that other commands read back (a365 plan, check-a365 state, dblib-sync report, the
    datasheet read's spec / symbol / sidecar, the web UI's a365 plan) were written in place: a crash left half a file.
    (A source check: these writes sit at the end of CLI commands that need a network or Altium to reach.)"""
    from ecadlib import fsutil
    src = {m: (Path(fsutil.__file__).parent / f"{m}.py").read_text(encoding="utf-8")
           for m in ("altium365", "upstream", "altium_dblib_sync", "datasheet", "app")}
    assert "out.write_text(json.dumps(items, indent=2))" not in src["altium365"]
    assert "write_text_atomic(out, json.dumps(items, indent=2))" in src["altium365"]
    assert "Path(a.state).write_text(" not in src["upstream"]
    assert "Path(a.out).write_text(json.dumps(out" not in src["altium_dblib_sync"]
    assert "paths[k].write_text(json.dumps(res[k]" not in src["datasheet"]
    assert 'out.write_text(json.dumps(items, indent=2), encoding="utf-8")' not in src["app"]


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
    import inspect
    from ecadlib import librarian as L, datasheet as D
    assert "LG.locked(out), _decisions_lock(plan_f)" in inspect.getsource(L.run)
    assert "LG.locked(out), L._decisions_lock(plan_f)" in inspect.getsource(D.fill_plan)
