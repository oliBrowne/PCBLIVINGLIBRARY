"""Review 2026-10-17: official part APIs first and the unofficial fence on every path (web UI librarian).
Offline. Run:  python3 -m pytest -q tests/test_review_1017_official_first.py"""
from pathlib import Path

from ecadlib.app import Workspace
from tests.fixtures_lite import lite


@lite
def test_web_librarian_defaults_to_the_cli_order(tmp_path):
    # no --providers and no [providers] order: the web UI / MCP librarian used only JLCPCB + EasyEDA
    ws = Workspace(Path(tmp_path) / "ws", offline=True)
    assert ws._librarian_providers() is None                     # -> librarian.run uses P.DEFAULT_ORDER (Nexar first)
    assert Workspace(Path(tmp_path) / "w2", providers=["nexar", "easyeda"], offline=True)._librarian_providers() == "nexar,easyeda"


