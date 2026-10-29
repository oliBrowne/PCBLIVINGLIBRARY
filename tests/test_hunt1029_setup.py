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


@lite
def test_two_boms_with_one_file_name_get_their_own_default_plan_folders(tmp_path, monkeypatch):
    """H-SETUP-5: since #202 `ecadlib librarian bom.csv` (no -o) in projA/ and then in projB/ both wrote
    <workspace>/librarian/bom: projB's run silently replaced projA's plan, sign-offs and models (the note about
    another BOM's plan compared only file names). Before, each project folder had its own ./out/librarian."""
    tmp_path = tmp_path.resolve()
    from ecadlib import firstrun as FR
    ws = _page_workspace(tmp_path, monkeypatch)
    a, b = tmp_path / "projA" / "bom.csv", tmp_path / "projB" / "bom.csv"
    for f in (a, b):
        f.parent.mkdir()
        f.write_text("Designator,MPN\nR1,X\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out_a = Path(FR.default_out("librarian", a))
    assert out_a == ws / "librarian" / "bom"
    out_a.mkdir(parents=True)                                            # projA's plan, as the librarian records it
    (out_a / "library.lock.json").write_text(json.dumps({"bom": {"file": "../../../projA/bom.csv"}}), encoding="utf-8")
    assert Path(FR.default_out("librarian", a)) == out_a                 # the same BOM again: the same plan
    monkeypatch.chdir(tmp_path / "projA")
    assert Path(FR.default_out("librarian", "bom.csv")) == out_a
    out_b = Path(FR.default_out("librarian", b))
    assert out_b != out_a and out_b.parent == out_a.parent and out_b.name.startswith("bom_"), out_b
    assert Path(FR.default_out("librarian", b)) == out_b                 # and stays put for projB


@lite
def test_nexar_status_says_when_the_sign_in_can_no_longer_be_refreshed(tmp_path, monkeypatch):
    """H-SETUP-6: the access token ran out and Nexar refuses the refresh token (revoked, app secret changed): every
    Altium 365 command stops with "could not refresh the Altium 365 sign-in", but `ecadlib nexar status` said "signed
    in ... access token valid until <a time already past>; ecadlib refreshes it by itself" and exit code 0."""
    import time
    from ecadlib import nexar_signin as NS, userconfig as UC
    from tests.test_nexar_signin import FakeIdentity, _home
    _home(tmp_path, monkeypatch)
    UC.set_key("NEXAR_CLIENT_ID", "my-app-1234")
    UC.set_key("NEXAR_CLIENT_SECRET", "s3cret")
    NS._save({"access_token": "OLD_AT", "expires_in": 3600, "refresh_token": "SECRET_RT"}, now=time.time() - 7200)
    fake = FakeIdentity((400, {"error": "invalid_grant"}))
    monkeypatch.setattr(NS, "_post", fake)
    out = []
    rc = NS.main(["status"], out=out.append)
    text = "\n".join(out)
    assert rc == 1, (rc, text)
    assert "nexar signin" in text and "invalid_grant" in text, text
    assert "SECRET" not in text and "s3cret" not in text and "OLD_AT" not in text
    NS._failed.clear()
    good = FakeIdentity((200, {"access_token": "NEW_AT", "expires_in": 3600, "refresh_token": "RT2"}))
    monkeypatch.setattr(NS, "_post", good)                               # a refresh that works: signed in, fresh expiry
    out.clear()
    assert NS.main(["status"], out=out.append) == 0, out
    assert "signed in" in out[0] and "NEW_AT" not in "\n".join(out)


@lite
def test_setup_page_takes_a_folder_pasted_with_quotes(tmp_path, monkeypatch):
    """H-SETUP-7: Windows Explorer's "Copy as path" puts the path in double quotes ("C:\\Users\\me\\libs"). Pasted on
    the setup page, the workspace was refused as "give the workspace as a full path" (it is one) and a library folder
    as "library folder not found"."""
    tmp_path = tmp_path.resolve()
    from ecadlib import easy_setup as ES
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    lib, ws = tmp_path / "my libs", tmp_path / "ws"
    lib.mkdir()
    res = ES.save(tmp_path, {"workspace": f' "{ws}" ', "libs": [f'"{lib}"', f"'{lib}'"], "provider": "none"})
    assert Path(res["workspace"]) == ws and ws.is_dir(), res
    assert ES.ws_libs(ws) == [str(lib), str(lib)], ES.ws_libs(ws)
    monkeypatch.setattr(ES, "RESTART", lambda w: None)
    assert ES.restart(f'"{ws}"') == {"restarting": True, "workspace": str(ws)}


@lite
def test_setup_page_library_folders_are_the_default_lib_where_its_workspace_is(tmp_path, monkeypatch):
    """H-SETUP-8: library folders given on the setup page were the default --lib only for commands run inside the
    workspace's folder. From any other folder `ecadlib librarian BOM` wrote into the page's workspace (#202) and used
    its rules, but searched none of the person's libraries, so it built parts they already have; `ecadlib ui` and
    `ecadlib doctor` from there did not see them either (`ecadlib setup` did)."""
    tmp_path = tmp_path.resolve()
    from ecadlib import easy_setup as ES, firstrun as FR
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    monkeypatch.setenv("ECADLIB_WORKSPACE", "")
    monkeypatch.setenv("ECADLIB_LIB", "")
    lib, ws = tmp_path / "mylib", tmp_path / "W"
    lib.mkdir()
    ES.save(tmp_path, {"workspace": str(ws), "libs": [str(lib)], "provider": "none"})
    (tmp_path / "bare").mkdir()
    monkeypatch.chdir(tmp_path / "bare")
    for cmd in ("librarian", "triage", "ui", "doctor"):
        argv, notes = FR.config_defaults(cmd, ["bom.csv"])
        assert argv.count("--lib") == 1 and argv[argv.index("--lib") + 1] == str(lib), (cmd, argv)
    assert FR.config_defaults("librarian", ["bom.csv", "--lib", "x"])[0].count("--lib") == 1
    (tmp_path / "bare" / "ecadlib.toml").write_text("[setup]\nlibs = []\n", encoding="utf-8")   # a project's own
    assert "--lib" not in FR.config_defaults("librarian", ["bom.csv"])[0]          # file: as it says


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_hunt1029_setup"]))


def _two_projects(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    ws = _page_workspace(tmp_path, monkeypatch)
    a, b = tmp_path / "projA" / "bom.csv", tmp_path / "projB" / "bom.csv"
    for f in (a, b):
        f.parent.mkdir()
        f.write_text("Designator,MPN\nR1,X\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out_a = ws / "librarian" / "bom"
    out_a.mkdir(parents=True)
    (out_a / "library.lock.json").write_text(json.dumps({"bom": {"file": "../../../projA/bom.csv"}}), encoding="utf-8")
    return tmp_path, ws, a, b, out_a


@lite
def test_a_moved_project_keeps_its_default_plan_folder(tmp_path, monkeypatch):
    """Review I2: the project folder renamed (projA -> projA_v2): the lock's recorded BOM no longer exists, so it is
    the same plan moving on, not another BOM's; the re-run must continue it (sign-offs, decisions, paid reads), not
    start a fresh bom_<hash> folder without a word."""
    from ecadlib import firstrun as FR
    tmp_path, ws, a, b, out_a = _two_projects(tmp_path, monkeypatch)
    (tmp_path / "projA").rename(tmp_path / "projA_v2")
    assert Path(FR.default_out("librarian", tmp_path / "projA_v2" / "bom.csv")) == out_a


@lite
def test_the_same_bom_by_another_name_keeps_its_default_plan_folder(tmp_path, monkeypatch):
    """Review I2: the same file reached by another spelling (macOS case-insensitive BOM.csv vs bom.csv, Windows subst,
    a hard link) is the same BOM: same plan folder (compared with samefile, not path strings)."""
    import os
    from ecadlib import firstrun as FR
    tmp_path, ws, a, b, out_a = _two_projects(tmp_path, monkeypatch)
    (tmp_path / "alias").mkdir()
    alias = tmp_path / "alias" / "bom.csv"
    os.link(a, alias)                                   # another path to the same file
    assert Path(FR.default_out("librarian", alias)) == out_a


@lite
def test_a_hashed_default_plan_folder_is_announced(tmp_path, monkeypatch):
    """Review I2: when another BOM's plan has the folder, the person is told where this plan goes and how to continue
    the old one."""
    from ecadlib import firstrun as FR
    tmp_path, ws, a, b, out_a = _two_projects(tmp_path, monkeypatch)
    notes = []
    out_b = Path(FR.default_out("librarian", b, note=notes.append))
    assert out_b.name.startswith("bom_") and len(notes) == 1, notes
    assert str(out_b) in notes[0] and "-o" in notes[0], notes
    notes.clear()
    FR.default_out("librarian", a, note=notes.append)
    assert not notes, notes


@lite
def test_librarian_without_o_says_where_a_second_bom_plan_went(tmp_path, monkeypatch):
    from unittest import mock
    from ecadlib import librarian as L
    tmp_path, ws, a, b, out_a = _two_projects(tmp_path, monkeypatch)
    err = io.StringIO()
    with mock.patch.object(L, "run", side_effect=ValueError("stop here")), contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        L.main([str(b), "--offline"])
    assert "holds the plan for" in err.getvalue() and "librarian/bom_" in err.getvalue().replace("\\", "/"), err.getvalue()


@lite
def test_status_last_triage_is_a_newer_cli_csv_over_an_old_web_json_of_the_same_name(tmp_path, monkeypatch):
    """Review M6 / Greptile P2: a CLI triage of ~/proj/bom.csv writes reports/triage_bom.csv, the same name as an old
    web-UI triage of uploads/bom.csv (triage_bom.json + .csv). The newest file is the CLI .csv, but status showed the
    old web .json's counts and time."""
    import os
    from ecadlib import ws_status as WS
    rep = tmp_path / "reports"
    rep.mkdir(parents=True)
    (rep / "triage_bom.json").write_text(json.dumps({"name": "bom.csv", "rows": [{}] * 7, "counts": {"in_library": 7},
                                                     "finished": "2023-11-14T22:13:20"}), encoding="utf-8")
    os.utime(rep / "triage_bom.json", (1_700_000_000, 1_700_000_000))
    (rep / "triage_bom.csv").write_text("﻿line,status\n1,in_library\n2,needs_footprint_generation\n", encoding="utf-8")
    t = WS.status(tmp_path)["last_triage"]
    assert t["report"] == "reports/triage_bom.csv" and t["lines"] == 2, t
    os.utime(rep / "triage_bom.csv", (1_700_000_001, 1_700_000_001))   # written with its .json: the .json's details
    t = WS.status(tmp_path)["last_triage"]
    assert t["report"] == "reports/triage_bom.json" and t["lines"] == 7, t
