"""Property-based bug hunt (2026-10-30), state area: concurrent writers, the web UI / MCP front ends, Workspace
actions in random orders. Each test is a counterexample found by a hypothesis harness, made deterministic."""
import threading

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
    assert other[0].wait(30) == 0
    assert {"MINE", "OTHER"} <= set(UC.stored_names()), UC.stored_names()


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
