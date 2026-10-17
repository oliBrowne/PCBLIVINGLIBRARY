"""Review 2026-10-17: official part APIs first and the unofficial fence on every path (web UI librarian, upkeep).
Offline. Run:  python3 -m pytest -q tests/test_review_1017_official_first.py"""
from pathlib import Path

from ecadlib import providers as P, sourcing as S, upkeep as U
from ecadlib.app import Workspace
from tests.fixtures_lite import lite


@lite
def test_web_librarian_defaults_to_the_cli_order(tmp_path):
    # no --providers and no [providers] order: the web UI / MCP librarian used only JLCPCB + EasyEDA
    ws = Workspace(Path(tmp_path) / "ws", offline=True)
    assert ws._librarian_providers() is None                     # -> librarian.run uses P.DEFAULT_ORDER (Nexar first)
    assert Workspace(Path(tmp_path) / "w2", providers=["nexar", "easyeda"], offline=True)._librarian_providers() == "nexar,easyeda"


@lite
def test_upkeep_sourcing_respects_unofficial_false(tmp_path, monkeypatch):
    bom = tmp_path / "bom.csv"
    bom.write_text("Designator,Quantity,Manufacturer Part Number\nR1,1,RC0603FR-0710KL\n", encoding="utf-8")
    seen = {}

    def fake_source_bom(lines, provs, *a, **k):
        seen["names"] = [p.name for p in provs]
        return [], []
    monkeypatch.setattr(S, "source_bom", fake_source_bom)
    src = U.Source(bom)
    U.check_sourcing(src, tmp_path, U.Ctx(env={}), U.upkeep_rules({}), "now", {"providers": {"unofficial": False}})
    assert "jlcpcb_search" not in seen["names"] and "nexar" in seen["names"]
    U.check_sourcing(src, tmp_path, U.Ctx(env={}), U.upkeep_rules({}), "now", {})
    assert "jlcpcb_search" in seen["names"]


def test_hub_search_has_no_short_q():
    # the CLI wrapper strips -q as --quiet everywhere, so `ecadlib hub -q LM358` lost its search words
    import inspect
    from ecadlib import hub
    assert '"-q", "--search"' not in inspect.getsource(hub.main)
