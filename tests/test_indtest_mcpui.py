"""Regression tests from the 2026-10-20 independent testing pass: the MCP server and the local web UI. Run:
    python3 -m tests.run_all test_indtest_mcpui"""
import json
from pathlib import Path

from ecadlib.app import Workspace
from tests.fixtures_lite import lite

REPO = Path(__file__).resolve().parent.parent
DEMO_LIB = str(REPO / "examples" / "kicad_lib")


# T046
@lite
def test_offline_house_style_guide_makes_no_model_call(tmp_path, monkeypatch):
    """`ecadlib mcp --offline` priced and then sent house_style_guide to the model: --offline must make no model call."""
    from ecadlib import house_style as HS
    sent = []
    monkeypatch.setattr(HS, "write_guide", lambda *a, **k: sent.append(a) or {"usd": 0.0, "model": "x"})
    ws = Workspace(tmp_path / "ws", libs=[DEMO_LIB], offline=True)
    r = ws.learn_house_style(ai=True, ask_first=True)
    assert r["ai"]["offline"] is True and "--offline" in r["ai"]["text"]
    for call in (lambda: ws.style_guide(5, True, True), lambda: ws.learn_house_style(ai=True)):
        try:
            call()
            raise AssertionError("an offline workspace asked the model")
        except ValueError as e:
            assert "--offline" in str(e)
    assert sent == []


def _res_bom(path, n):
    pk = ["0402", "0603", "0805", "1206"]
    path.write_text("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #\n" + "".join(
        f"R{i},1,{i}k,R_{pk[i % 4]},ACME,RES{pk[i % 4]}-{i}K-T\n" for i in range(1, n + 1)))
    return str(path)


# T047
@lite
def test_cancelled_librarian_run_keeps_finished_parts(tmp_path):
    """A librarian_run job cancelled at part 6 of 12 (or stopped while logging its summary) saved no
    librarian_state.json: the re-run built all 12 again although the cancel answer said finished parts are kept."""
    from ecadlib import librarian as L
    bom, out = _res_bom(tmp_path / "bom.csv", 12), tmp_path / "out"

    class Stop(Exception):
        pass

    def stop_at(prefix):
        def log(*a, **k):
            if str(a[0] if a else "").lstrip().startswith(prefix):
                raise Stop
        return log
    for prefix, kept in (("[6/12]", 5), ("done ", 12)):
        o = out / prefix[:2]
        try:
            L.run(bom, [DEMO_LIB], str(o), ["standard"], offline=True, cache=str(tmp_path / "cache"), log=stop_at(prefix))
            raise AssertionError("not stopped")
        except Stop:
            pass
        state = json.loads((o / "librarian_state.json").read_text())
        assert len(state) == kept, (prefix, sorted(state))
        lines = []
        L.run(bom, [DEMO_LIB], str(o), ["standard"], offline=True, cache=str(tmp_path / "cache"), log=lines.append)
        assert f"finding symbols and footprints for {12 - kept} part(s)" in "\n".join(lines) or kept == 12, lines[:20]
        assert sum(x.lstrip().startswith("skip ") for x in lines) == kept, lines
