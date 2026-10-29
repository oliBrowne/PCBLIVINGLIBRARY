"""Bug hunt 2026-10-29, area setup: first run, the setup page, the Nexar sign-in, the default output of librarian /
triage (#202).  Run:  python3 -m tests.test_hunt1029_setup"""
import io, json, contextlib
from pathlib import Path

from tests.fixtures_lite import lite


def _page_workspace(tmp_path, monkeypatch):
    """A user who saved the setup page: settings.json names workspace W, and W has the page's own ecadlib.toml."""
    home, ws = tmp_path / "home", tmp_path / "W"
    ws.mkdir(parents=True)
    (ws / "ecadlib.toml").write_text('[sources]\norder = ["standard"]\n\n[setup]\ntool = "both"\nlibs = []\n'
                                     'workspace = "."\n', encoding="utf-8")
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"workspace": str(ws), "setup_done": True}), encoding="utf-8")
    monkeypatch.setenv("ECADLIB_HOME", str(home))
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    monkeypatch.setenv("ECADLIB_WORKSPACE", "")
    return ws


def _project(tmp_path):
    """A project folder with its own ecadlib.toml that maps the BOM's unusual headers."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "ecadlib.toml").write_text('[bom.columns]\nmpn = "Widget"\ndesignator = "Spot"\n', encoding="utf-8")
    (proj / "bom.csv").write_text("Spot,Widget,Qty\nR1,RC0603FR-0710KL,1\nC1,GRM188R71H104KA93D,1\n", encoding="utf-8")
    return proj


@lite
def test_triage_without_o_keeps_the_ecadlib_toml_of_the_current_folder(tmp_path, monkeypatch):
    """H-SETUP-1: after the setup page was saved, `ecadlib triage bom.csv` (no -o) wrote into the page's workspace but
    also took that workspace's rules instead of the ecadlib.toml in the folder it was run from, so a [bom.columns]
    mapping there was ignored: "no BOM header found ... map your headers in ecadlib.toml" (they were)."""
    tmp_path = tmp_path.resolve()
    ws = _page_workspace(tmp_path, monkeypatch)
    monkeypatch.chdir(_project(tmp_path))
    from ecadlib import bom_triage as BT
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = BT.main(["bom.csv", "--offline", "--json"])
    assert "no BOM header" not in err.getvalue(), err.getvalue()
    assert rc in (0, 1), (rc, err.getvalue())
    assert (ws / "reports" / "triage_bom.csv").is_file()                 # still lands in the workspace (#202)


@lite
def test_rules_start_prefers_the_current_folders_file_when_out_was_defaulted(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    ws = _page_workspace(tmp_path, monkeypatch)
    from ecadlib import firstrun as FR, rules as RU
    monkeypatch.chdir(_project(tmp_path))
    out = FR.default_out("librarian", "bom.csv")
    assert Path(RU.find(FR.rules_start(out, given_out=False))).resolve() == tmp_path / "proj" / "ecadlib.toml"
    assert FR.rules_start(out, given_out=True) == out                    # an explicit -o: its own rules, as before
    (tmp_path / "bare").mkdir()
    monkeypatch.chdir(tmp_path / "bare")                                 # no rules here: the workspace's own
    out = FR.default_out("librarian", "bom.csv")
    assert Path(RU.find(FR.rules_start(out, given_out=False))).resolve() == ws / "ecadlib.toml"


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@lite
def test_sign_in_again_after_signing_out_of_a_waiting_sign_in(tmp_path, monkeypatch):
    """H-SETUP-2: on the setup page, Sign in (browser not finished yet), Sign out, Sign in again: the first sign-in
    still listened on the redirect port for up to 5 minutes, so the second one failed with "port N on this computer
    is in use: close what uses it" (ecadlib itself)."""
    from ecadlib import easy_setup as ES
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NEXAR_REDIRECT_URI", f"http://localhost:{_free_port()}/login")
    try:
        first = ES.start_nexar_signin({"client_id": "abc", "client_secret": "def"})
        assert first["waiting"], first
        ES.nexar_signout({})
        again = ES.start_nexar_signin({"client_id": "abc", "client_secret": "def"})   # raised ValueError: port in use
        assert again["waiting"], again
    finally:
        ES.nexar_signout({})


@lite
def test_doctor_does_not_call_altium365_ready_on_a_stored_nexar_app_without_a_sign_in(tmp_path, monkeypatch):
    """H-SETUP-3: NEXAR_CLIENT_ID + NEXAR_CLIENT_SECRET stored (after `ecadlib nexar signout`, or for the supply
    lookups) but no sign-in and no NEXAR_TOKEN: `ecadlib doctor` said "key altium365 OK" and `ecadlib init` listed it
    as set, while every Altium 365 command stops with "no Altium 365 token" (they only use NEXAR_TOKEN / the sign-in)."""
    from ecadlib import doctor as DR, firstrun as FR, rules as RU, userconfig as UC
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    for k in ("NEXAR_TOKEN", "NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET", UC.NO_STORED):
        monkeypatch.setenv(k, "")
    UC.set_key("NEXAR_CLIENT_ID", "my-app-1234")
    UC.set_key("NEXAR_CLIENT_SECRET", "s3cret")
    rules = RU.load({"altium365": {"workspace": "https://acme.365.altium.com/"}})
    row = next(c for c in DR.check_keys(rules, {}) if c["check"] == "key altium365")
    assert row["level"] == "WARN", row
    assert "ecadlib nexar signin" in row["hint"], row
    a365 = next(r for r in FR.key_status(rules, {}) if "NEXAR" in r[0])
    assert a365[1] is False, a365


@lite
def test_status_last_triage_is_the_newest_one_cli_or_web_ui(tmp_path, monkeypatch):
    """H-SETUP-4: since #202 `ecadlib triage BOM` (no -o) writes reports/triage_<BOM>.csv/.md into the workspace, but
    `ecadlib status` (and the UI's status) took any web-UI triage .json first, however old, so the triage just run
    from the command line never showed as the last one."""
    import os
    from ecadlib import ws_status as WS
    rep = tmp_path / "reports"
    rep.mkdir(parents=True)
    old = rep / "triage_old_board.json"
    old.write_text(json.dumps({"name": "old_board.csv", "rows": [{}] * 7, "counts": {"in_library": 7}}), encoding="utf-8")
    (rep / "triage_old_board.csv").write_text("line,status\n", encoding="utf-8")
    for f in rep.iterdir():
        os.utime(f, (1_700_000_000, 1_700_000_000))
    (rep / "triage_bom.csv").write_text("\ufeffline,status,mpn\n1,in_library,A\n2,needs_footprint_generation,B\n"
                                        "3,in_library,C\n", encoding="utf-8")
    t = WS.status(tmp_path)["last_triage"]
    assert t["report"] == "reports/triage_bom.csv", t
    assert t["lines"] == 3 and t["counts"] == {"in_library": 2, "needs_footprint_generation": 1}, t
    os.utime(old, None)                                                  # the web UI's triage is the newest again
    t = WS.status(tmp_path)["last_triage"]
    assert t["report"] == "reports/triage_old_board.json" and t["name"] == "old_board.csv", t


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_hunt1029_setup"]))
