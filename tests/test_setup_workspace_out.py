"""`ecadlib librarian BOM` / `ecadlib triage BOM` without -o after the setup page was saved: the output goes into the
workspace picked there (where `ecadlib status` and the web UI look), not ./out/... of whatever folder the terminal is
in (new-user install audit, 2026-10-27).  Run:  python3 -m tests.test_setup_workspace_out"""
import json
from pathlib import Path

from tests.fixtures_lite import lite


def _settings(home, ws):
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps({"workspace": str(ws), "setup_done": True}), encoding="utf-8")


@lite
def test_default_out_goes_into_the_setup_page_workspace(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()                                       # Windows: the long form, not RUNNER~1
    from ecadlib import bom_triage as BT, firstrun as FR
    ws = tmp_path / "Documents" / "ecadlib"
    ws.mkdir(parents=True)
    _settings(tmp_path / "home", ws)
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    monkeypatch.setenv("ECADLIB_WORKSPACE", "")
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    assert Path(FR.default_out("librarian", "a/board.csv")).resolve() == ws / "librarian" / "board"
    assert Path(FR.default_out("triage", "board.csv")).resolve() == ws / "reports" / "triage_board"
    assert Path(BT._lib_out("x/board.csv")).resolve() == ws / "librarian" / "board"


@lite
def test_the_status_order_wins_env_then_setup_section_then_setup_page(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    from ecadlib import firstrun as FR
    page = tmp_path / "page_ws"
    page.mkdir()
    _settings(tmp_path / "home", page)
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    monkeypatch.setenv("ECADLIB_WORKSPACE", "")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ecadlib.toml").write_text('[setup]\nworkspace = "tomlws"\n', encoding="utf-8")
    assert Path(FR.default_out("librarian", "b.csv")) == Path("tomlws/librarian/b")      # [setup] beats the page
    monkeypatch.setenv("ECADLIB_WORKSPACE", str(tmp_path / "envws"))
    assert Path(FR.default_out("librarian", "b.csv")).resolve() == tmp_path / "envws" / "librarian" / "b"


@lite
def test_a_missing_page_folder_keeps_out_and_a_local_ecadlib_workspace_wins(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    from ecadlib import firstrun as FR
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    monkeypatch.setenv("ECADLIB_WORKSPACE", "")
    monkeypatch.chdir(tmp_path)
    _settings(tmp_path / "home", tmp_path / "gone")                     # the page's folder was deleted
    assert FR.default_out("librarian", "b.csv") == "out/librarian"
    page = tmp_path / "page_ws"
    page.mkdir()
    _settings(tmp_path / "home", page)
    (tmp_path / "ecadlib_workspace").mkdir()                            # ./ecadlib_workspace keeps winning, as for status
    assert Path(FR.default_out("librarian", "b.csv")) == Path("ecadlib_workspace/librarian/b")


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_setup_workspace_out"]))
