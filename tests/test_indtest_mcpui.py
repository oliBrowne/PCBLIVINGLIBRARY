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
