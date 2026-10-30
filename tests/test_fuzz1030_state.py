"""Property-based bug hunt (2026-10-30), state area: concurrent writers, the web UI / MCP front ends, Workspace
actions in random orders. Each test is a counterexample found by a hypothesis harness, made deterministic."""
import threading

from ecadlib import ledger as LG
from tests.fixtures_lite import lite


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
