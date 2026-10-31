"""Property-based bug hunt (2026-10-30), state area: concurrent writers, the web UI / MCP front ends, Workspace
actions in random orders. Each test is a counterexample found by a hypothesis harness, made deterministic."""
import threading, time

from ecadlib import ledger as LG
from tests.fixtures_lite import fresh_home, lite


@lite
def test_usage_index_keeps_every_project_when_two_scans_record_at_once(tmp_path):
    """ledger.record_usage read usage.json, changed one project's row and wrote it back with no lock: two where-used
    scans (or a scan and a librarian run) into one library at once kept only the last writer's row; the other project
    vanished from where-used, so a change to its parts showed no impact on it."""
    gate, real = threading.Barrier(2, timeout=2), LG.load_usage

    def load(folder):                          # both writers read before either writes (the race, every time)
        data = real(folder)
        try:
            gate.wait()
        except threading.BrokenBarrierError:   # fixed: the second writer reads only after the first one wrote
            pass
        return data
    LG.load_usage = load
    try:
        th = [threading.Thread(target=LG.record_usage, args=(tmp_path, f"proj{i}", {"R1": {"revision": 1}}))
              for i in (1, 2)]
        [t.start() for t in th]
        [t.join(10) for t in th]
    finally:
        LG.load_usage = real
    assert sorted(LG.load_usage(tmp_path)["projects"]) == ["proj1", "proj2"], LG.load_usage(tmp_path)


@lite
def test_a_key_stored_by_another_ecadlib_process_meanwhile_is_kept(tmp_path):
    """userconfig.set_key read keys.json, changed it and wrote it back under a lock of this process only: a key another
    ecadlib process stored meanwhile (the web UI's setup page while the MCP server refreshed the Altium 365 sign-in,
    which rotates its refresh token) was dropped, silently. Here the other process stores OTHER between our read and
    our write; once the lock covers processes, it waits for our write instead."""
    import os, subprocess, sys
    from pathlib import Path
    from ecadlib import userconfig as UC
    root = str(Path(__file__).resolve().parents[1])
    real, other = UC._read_for_write, []

    def read(path):
        data = real(path)
        if not other:                          # the other process, started after our read
            p = subprocess.Popen([sys.executable, "-c", "from ecadlib import userconfig as UC; UC.set_key('OTHER', 'x')"],
                                 cwd=root, env=dict(os.environ, PYTHONPATH=root))
            other.append(p)
            try:
                p.wait(5)                      # unfixed: it writes now, and our write below drops it
            except subprocess.TimeoutExpired:  # fixed: it waits for our lock
                pass
        return data
    UC._read_for_write = read
    try:
        UC.set_key("MINE", "y")
    finally:
        UC._read_for_write = real
    try:
        assert other[0].wait(30) == 0
        assert {"MINE", "OTHER"} <= set(UC.stored_names()), UC.stored_names()
    finally:                                   # the test home is shared by later tests: leave no stored key behind
        [UC.remove_key(k) for k in ("MINE", "OTHER") if k in UC.stored_names()]


@lite
@fresh_home
def test_a365_a_push_that_ran_meanwhile_keeps_its_claim_on_the_component(tmp_path):
    """altium365.push read this computer's (and the project's) ecadlib_publishers.json when it started and wrote its
    copy back at the end: plan B pushing to the same workspace while plan A's push ran lost its claim on the shared
    component, so A's next change made a new revision of the component B's boards use without B's sign-off."""
    from tests.test_change_control import FakeA365
    from tests.test_fix1026_fx15 import _a365_push, _new_land
    from tests.test_fix1026_fx18 import _two_projects
    nx = FakeA365()
    a, b = _two_projects(tmp_path)

    class Meanwhile:                           # B's whole push runs while A's push looks its part up
        def __getattr__(self, k):
            return getattr(nx, k)

        def find(self, ws, name):
            if not hasattr(self, "b"):
                self.b = _a365_push(b, nx)["RES-0603-10k"]
            return nx.find(ws, name)
    assert _a365_push(a, Meanwhile())["RES-0603-10k"].get("adopted")
    _new_land(tmp_path / "projA/A")
    r = _a365_push(a, nx)["RES-0603-10k"]
    assert r["action"] == "skip" and "projB" in r["detail"], r
    assert len(nx.comps["RES-0603-10k"]["revisions"]) == 1


@lite
def test_dblib_two_plans_syncing_into_one_library_at_once_keep_both_rows(tmp_path):
    """dblib-sync held its own plan folder's library lock only: board B syncing into the shared DbLib folder while
    board A's sync ran (after A read the table and ecadlib_sync.json) was overwritten by A's copy: B's released row
    vanished from the DbLib with no message."""
    from ecadlib import altium_dblib_sync as S
    from tests.test_fix1026_pubtgt import _acts, _plan_folder, _res, _rows
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")])
    real, done = S.read_tables, []

    def read(*a_, **k):                        # A has read the state: B's whole sync runs now (in another thread, as
        out = real(*a_, **k)                   # another process would; it waits if the folder is locked)
        if not done:
            done.append(threading.Thread(target=lambda: done.append(S.sync(b, lib))))
            done[0].start()
            done[0].join(5)
        return out
    S.read_tables = read
    try:
        assert _acts(S.sync(a, lib)) == {"RES-0603-10k": "added"}
    finally:
        S.read_tables = real
    done[0].join(60)
    assert _acts(done[1]) == {"RES-0603-1k": "added"}, done[1]["results"]
    assert set(_rows(lib)) == {"RES-0603-10k", "RES-0603-1k"}, set(_rows(lib))


@lite
def test_dblib_sync_waiting_on_another_sync_gives_up_with_a_message(tmp_path):
    """Review: the DbLib folder's sync lock waited forever and silently (a sync hung on another PC of a shared drive
    blocked this one with no output); it gives up after SYNC_WAIT seconds, as the library lock does, and says why."""
    from ecadlib import altium_dblib_sync as S
    from ecadlib.fsutil import plan_lock
    from tests.test_fix1026_pubtgt import _plan_folder, _res
    lib, a = tmp_path / "dblib", _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    wait, out, hold = getattr(S, "SYNC_WAIT", None), [], threading.Event()
    S.SYNC_WAIT = 0.3

    def other():                               # another process's sync holds the folder
        with plan_lock(lib / ".ecadlib", name="sync.lock"):
            hold.set()
            time.sleep(3)
    t = threading.Thread(target=other)
    t.start()
    hold.wait(5)
    try:
        S.sync(a, lib)
    except ValueError as e:
        out.append(str(e))
    finally:
        S.SYNC_WAIT = wait
        t.join(10)
    assert out and "another" in out[0] and "sync" in out[0], out


@lite
def test_usage_scan_into_a_library_whose_lock_cannot_be_made_still_reads(tmp_path):
    """Review: record_usage took the library lock even for an unchanged rescan: a read-only library folder (a share
    this user may not write) now failed creating .ecadlib/lock, where it used to write nothing and succeed."""
    LG.record_usage(tmp_path, "proj1", {"R1": {"revision": 1}})
    real = LG.lock

    def denied(folder, wait=None):
        raise PermissionError(13, "Permission denied", str(folder))
    LG.lock = denied
    try:
        data = LG.record_usage(tmp_path, "proj1", {"R1": {"revision": 1}})   # unchanged: nothing to write
    finally:
        LG.lock = real
    assert "proj1" in data["projects"], data


def test_altium_file_cut_inside_its_header_is_a_clean_error():
    """Review: a .SchLib / .PcbLib cut after the OLE signature (8 to 33 bytes) raised struct.error, not ValueError."""
    import os, tempfile
    from ecadlib.altium_lib import ole_open
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cut.SchLib")
        with open(p, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 12)
        try:
            ole_open(p)
        except ValueError:
            return
        except Exception as e:                 # noqa: BLE001 - the bug: any other error type
            raise AssertionError(f"{type(e).__name__}: {e}")
        raise AssertionError("no error for a cut file")
