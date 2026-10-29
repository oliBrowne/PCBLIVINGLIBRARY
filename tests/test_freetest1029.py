"""2026-10-29 free test pass: the setup page's Altium 365 address and folder errors, and bug-report scrubbing.
Run:  python3 -m tests.test_freetest1029"""
from ecadlib import bugreport as B, easy_setup as ES, rules as RU
from tests.fixtures_lite import lite
from tests.test_easy_setup import _call, _home, _server


def test_a365_address_is_the_workspace_root_without_a_password_or_stray_lines():
    """A pasted line break and text after the address was saved into ecadlib.toml as the workspace (Nexar then knows
    no such workspace), and https://user:password@host/ kept the password there as plain text."""
    assert ES.a365_address("https://Acme.365.altium.com/projects/abc?x=1") == "https://acme.365.altium.com/"
    assert ES.a365_address("https://acme.365.altium.com") == "https://acme.365.altium.com/"
    assert ES.a365_address("https://vault.acme.local:9780/") == "https://vault.acme.local:9780/"   # on-prem server
    for bad in ("https://acme.365.altium.com/\n[x]\nk=1", "https://acme.365.altium.com/\tx",
                "https://user:pw@acme.365.altium.com/", "https://user@acme.365.altium.com/",
                "https://acme.365.altium.com:99999/", "https://acme.365.altium.com:x/"):
        try:
            ES.a365_address(bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert "Altium 365" in str(e) and "pw" not in str(e).split("copy")[1], e
    with _home() as d:
        ws = d / "ws"
        ES.save(ws, {"provider": "none", "tool": "altium", "workspace": str(ws),
                     "a365_workspace": "https://acme.365.altium.com/components"})
        assert RU.load(ws / "ecadlib.toml")["altium365"]["workspace"] == "https://acme.365.altium.com/"


def test_setup_page_errors_name_the_full_folder_the_person_typed():
    """The page shortened every path in its error to the last name: "give the workspace as a full path, e.g. ecadlib"
    and "library folder not found: nolib". A workspace that cannot be made answered "Something went wrong"."""
    with _home() as d, _server(d / "ws") as port:
        st, j = _call(port, "POST", "/api/setup", {"provider": "none", "workspace": "relative/x"})
        assert st == 400 and str(ES.UC.default_workspace()) in j["error"], j
        missing = d / "nolib"
        st, j = _call(port, "POST", "/api/setup", {"provider": "none", "workspace": str(d / "ws"), "libs": [str(missing)]})
        assert st == 400 and str(missing) in j["error"], j
        blocker = d / "afile"
        blocker.write_text("x")
        target = blocker / "ws"                        # under a file: no folder can be made there
        st, j = _call(port, "POST", "/api/setup", {"provider": "none", "workspace": str(target)})
        assert st == 400 and "Something went wrong" not in j["error"] and str(target) in j["error"], j


@lite
def test_scrub_blanks_quoted_values_client_ids_and_the_altium365_company(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    out = B.scrub("password='my pass phrase' api_key = \"two words\" client_id=AbCdEfGhIjKlMnOpQrStUvWx "
                  "at https://acme-corp.365.altium.com/x {'X-API-KEY': 'q8z7y6x5w4v3'}")
    for bad in ("pass phrase", "two words", "AbCdEfGh", "acme-corp", "q8z7"):
        assert bad not in out, (bad, out)
    assert "[secret]]" not in out and "https://[workspace].365.altium.com/x" in out, out
    out = B.scrub('password="first \\"second phrase\\"" token=\'it\\\'s mine\' next')   # escaped quotes inside
    assert "second phrase" not in out and "s mine" not in out and out.endswith(" next"), out


@lite
def test_download_cache_defaults_to_the_workspace_the_web_ui_uses(tmp_path, monkeypatch):
    """With a workspace set up, librarian / triage wrote their output there (#202) but kept the download cache, and with
    it the paid datasheet answers, in out/http_cache of whatever folder the terminal was in: run from another folder,
    or from the web UI (<workspace>/http_cache), the same datasheets were downloaded and paid for again."""
    import contextlib, io
    from pathlib import Path
    from ecadlib import firstrun as FR, librarian as L, bom_triage as BT, datasheet as D
    tmp_path = tmp_path.resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    monkeypatch.setenv("ECADLIB_WORKSPACE", "")
    assert FR.default_cache() == "out/http_cache"                       # nothing set up: as before
    ws = tmp_path / "ws"
    monkeypatch.setenv("ECADLIB_WORKSPACE", str(ws))
    assert FR.default_cache() == "out/http_cache" and not ws.exists()  # a stale setting: not created for a cache
    ws.mkdir()
    assert Path(FR.default_cache()) == ws / "http_cache"
    (tmp_path / "out" / "http_cache").mkdir(parents=True)                # downloads (paid answers) from before
    assert FR.default_cache() == "out/http_cache"                       # kept until the workspace has its own
    (ws / "http_cache").mkdir()
    assert Path(FR.default_cache()) == ws / "http_cache"
    seen = {}
    for name, mod, argv in (("librarian", L, ["x.csv"]), ("triage", BT, ["x.csv"]), ("datasheet", D, ["batch", "x.csv"])):
        real = mod.argparse.ArgumentParser.parse_args
        def grab(self, args=None, namespace=None, _real=real, _name=name):
            a = _real(self, args, namespace)
            seen[_name] = getattr(a, "cache", None)
            raise SystemExit(0)
        monkeypatch.setattr(mod.argparse.ArgumentParser, "parse_args", grab)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                mod.main(argv)
            except SystemExit:
                pass
        monkeypatch.setattr(mod.argparse.ArgumentParser, "parse_args", real)
    assert {k: Path(v) for k, v in seen.items() if v} == {k: ws / "http_cache" for k in ("librarian", "triage", "datasheet")}, seen


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_freetest1029"]))
