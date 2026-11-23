"""Review 2026-11-23, Group 2 (security and privacy), items 2.5, 2.9 and 2.6:
  2.5 the crash-report endpoint (api/crash-report.js): no word of the sender's choice in a public issue, one address
      cannot use up everyone's allowance, a store failure locks no address out; the client sends what it accepts;
  2.9 the privacy texts (PRIVACY.md, site/privacy.html) name every host ecadlib's code asks, and the address hash
      is keyed;
  2.6 the KiCad window, the Altium runner and ecadlib itself never start a program found in the current / project
      folder.
The endpoint checks need node (skipped without it)."""
import importlib.util, json, shutil, subprocess
from pathlib import Path

from tests.fixtures_lite import lite
from ecadlib import bugreport as B

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = ROOT / "api" / "crash-report.js"


def _node():
    node = shutil.which("node")
    if not node:
        print("      (node not installed: skipped)")
    return node


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------ 2.5 the endpoint
def test_25_endpoint_free_text_limits_and_store_failure():
    node = _node()
    if not node:
        return
    r = subprocess.run([node, str(ROOT / "tests" / "security1123_endpoint_check.js")], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_25_the_list_of_error_names_has_pythons_ecadlibs_and_the_libraries():
    allow = json.loads((ROOT / "api" / "_crash-report-allow.json").read_text(encoding="utf-8"))
    errors = set(allow["errors"])
    for name in ("KeyError", "RulesError", "JSONDecodeError", "CalledProcessError", "TimeoutExpired", "BadZipFile", "URLError"):
        assert name in errors, name
    m = _load(ROOT / "scripts" / "build_crash_report_allow.py", "allow1123")
    got = m.library_errors({"json", "subprocess", "no_such_module_1123"})
    assert {"JSONDecodeError", "CalledProcessError"} <= got and all(m.ERROR_NAME.fullmatch(n) for n in got)


def test_25_client_sends_the_system_in_a_fixed_form_and_the_endpoint_keeps_the_same():
    forms = {"Linux-6.8.0-45-generic-x86_64-with-glibc2.39": "Linux-6.8", "Windows-11": "Windows-11",
             "Windows-10-10.0.19045-SP0": "Windows-10", "Windows-2022Server": "Windows-2022Server",
             "Windows-8.1": "Windows-8.1", "macOS-14.5-arm64-arm-64bit": "macOS-14.5", "macOS-15.1": "macOS-15.1",
             "Linux-buildhost.acme-corp.example": "Linux", "Linux": "Linux", "Darwin-23.5.0": "Other-unknown",
             "Call Jane 555": "Other-unknown", "": "Other-unknown", None: "Other-unknown"}
    for text, want in forms.items():
        assert B.payload({"os": text})["os"] == want, text
    node = _node()
    if not node:
        return
    js = ("const ep=require(process.argv[1]);let d='';process.stdin.on('data',c=>d+=c).on('end',()=>{"
          "console.log(JSON.stringify(JSON.parse(d).map(o=>[ep.validate({schema:1,version:'0.4.1',installed:'pip',"
          "python:'3.12.1',where:'triage',error:'KeyError',frames:[],os:o}),ep.osForm(o)])))})")
    sent = sorted(set(forms.values()))
    r = subprocess.run([node, "-e", js, str(ENDPOINT)], input=json.dumps(sent), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    for o, (bad, kept) in zip(sent, json.loads(r.stdout)):
        assert bad is None and kept == ("Other" if o == "Other-unknown" else o), (o, bad, kept)


@lite
def test_25_a_crash_with_a_made_up_error_class_is_filed_as_other_error(tmp_path, monkeypatch):
    """What the client sends for an error class of the user's own code or an unknown library passes the endpoint's
    checks, and the public issue says OtherError."""
    node = _node()
    if not node:
        return
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)

    class AcmeRadarBoardSecretError(Exception):
        pass
    try:
        raise AcmeRadarBoardSecretError("x")
    except Exception as e:
        B.record("triage", e)
    p = B.payload(B.latest())
    js = ("const ep=require(process.argv[1]);let d='';process.stdin.on('data',c=>d+=c).on('end',async()=>{"
          "const made=[];const gh={search:async()=>({items:[]}),create:async(t,b)=>{made.push(t+b);return{number:1}},"
          "released:async()=>true};const r=await ep.handle(JSON.parse(d),gh);console.log(JSON.stringify([r.status,made]))})")
    r = subprocess.run([node, "-e", js, str(ENDPOINT)], input=json.dumps(p), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    status, made = json.loads(r.stdout)
    assert status == 201 and len(made) == 1 and "OtherError" in made[0] and "Acme" not in made[0], (status, made)


# ------------------------------------------------------------------ 2.9 the privacy texts
def _privacy_texts():
    import re
    return {f: re.sub(r"\s+", " ", (ROOT / f).read_text(encoding="utf-8")) for f in ("PRIVACY.md", "site/privacy.html")}


def _host(url):
    from urllib.parse import urlparse
    h = urlparse(url.replace("{}", "x")).hostname or ""
    return h[4:] if h.startswith("www.") else h


def test_29_privacy_texts_name_every_host_the_part_searches_and_downloads_ask():
    """Read from the code, so a part search added later with a host the texts do not name fails here."""
    from ecadlib import providers as P, easyeda_3d as E3, outline_docs as OD, datasheet as D
    hosts = set()
    for cls in P.ALL:                                        # every built-in part search: its URL constants
        for k, v in vars(cls).items():
            if k.endswith("URL") and isinstance(v, str) and v.startswith("http") and not getattr(cls, "env", None):
                hosts.add(_host(v))                          # (a search that needs your key is listed by its name)
    hosts.add(_host(E3.STEP_URL))
    hosts.add(_host(OD.ADI_PKG))
    for v in OD.LINKS.values():
        for u in (v if isinstance(v, list) else [u for us in v.values() for u in us]):
            hosts.add(_host(u))
    for row in D.MAKER_URLS:
        hosts.update(_host(x) for x in row if isinstance(x, str) and x.startswith("http"))
    hosts.add("lcsc.com")                                    # datasheet_urls: LCSC's datasheet URL pattern
    assert {"jlcpcb.com", "jlcsearch.tscircuit.com", "wmsc.lcsc.com", "easyeda.com", "modules.easyeda.com", "ti.com",
            "analog.com", "aosmd.com", "nxp.com", "pdfserv.maximintegrated.com"} <= hosts, hosts
    for f, t in _privacy_texts().items():
        gone = sorted(h for h in hosts if h not in t)
        assert not gone, f"{f} does not name {gone}"
        assert "third party" in t and "no key" in t, f     # whose server jlcsearch is; that it runs with no key set


def test_29_privacy_texts_say_what_the_address_hash_is_and_where_it_is_kept():
    for f, t in _privacy_texts().items():
        assert "one-way hash" not in t, f                    # a plain hash of an IPv4 address can be reversed
        assert "keyed hash" in t and "Upstash" in t and "IPv4" in t and "one day" in t, f
        assert "OtherError" in t, f
    js = ENDPOINT.read_text(encoding="utf-8")
    assert "createHmac" in js and 'createHash("sha256").update(`crash-report|' not in js


# ------------------------------------------------------------------ 2.6 nothing is started from the project folder
def _kicad_window():
    import sys, types
    from unittest import mock
    fake = types.ModuleType("pcbnew")
    fake.ActionPlugin = type("ActionPlugin", (), {"register": lambda self: None})
    with mock.patch.dict(sys.modules, {"pcbnew": fake}):
        src = ROOT / "ecadlib" / "assets" / "kicad_action_plugin.py"
        mod = types.ModuleType("ecadlib_kicad_action_plugin_1123")
        mod.__file__ = str(src)
        exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), mod.__dict__)
    return mod


def _program_file(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_text("@echo planted\n" if n.endswith((".bat", ".cmd")) else "#!/bin/sh\necho planted\n")
        (folder / n).chmod(0o755)


def _received_project(tmp_path):
    """A project folder as it could arrive from someone else: programs named like the ones ecadlib starts."""
    proj = tmp_path / "received project"
    _program_file(proj, "ecadlib", "ecadlib.bat", "ecadlib.exe", "python.bat", "python", "python3", "python3.bat", "py.bat",
                  "py", "git", "git.exe", "git.bat")
    return proj


@lite
def test_26_kicad_window_never_takes_a_program_from_the_project_folder(tmp_path, monkeypatch):
    import os
    mod = _kicad_window()
    proj, real = _received_project(tmp_path), tmp_path / "real bin"
    _program_file(real, "ecadlib", "ecadlib.exe")
    monkeypatch.chdir(proj)                                      # KiCad's working folder: the open project's

    def never(*a, **k):
        raise AssertionError(f"started {a}")
    for nt in (True, False, None):
        sep = ";" if nt or (nt is None and os.name == "nt") else os.pathsep
        bad = sep.join([".", "", str(proj), "bin", os.path.join(str(proj), ".")])
        env = {"PATH": bad, "PATHEXT": ".com;.exe;.bat;.cmd"}
        assert mod.safe_which("ecadlib", env, nt=nt) is None, nt
        assert mod.safe_which("python", env, nt=nt) is None and mod.safe_which("python3", env, nt=nt) is None, nt
        env["PATH"] = bad + sep + str(real)
        got = mod.safe_which("ecadlib", env, nt=nt)
        assert got and os.path.isabs(got) and os.path.dirname(got) == str(real), (nt, got)
        for odd in ("", ".", "..", os.path.join("sub", "ecadlib"), str(proj / "ecadlib.bat")):
            assert mod.safe_which(odd, env, nt=nt) is None, odd   # a name only, never a path
    # from another folder, with the project's folder on PATH: `avoid` keeps it out
    monkeypatch.chdir(tmp_path)
    env = {"PATH": str(proj), "PATHEXT": ".com;.exe;.bat;.cmd"}
    assert mod.safe_which("ecadlib", env) is not None and mod.safe_which("ecadlib", env, avoid=[str(proj)]) is None
    assert mod.ecadlib_command(env, run=never, cmd_file=str(tmp_path / "none.json"), project=str(proj)) is None
    # the whole look-up, in the project's folder: nothing found there is ever the command, and nothing is started
    monkeypatch.chdir(proj)
    env = {"PATH": os.pathsep.join([".", str(proj)]), "PATHEXT": ".com;.exe;.bat;.cmd"}
    assert mod.ecadlib_command(env, run=never, cmd_file=str(tmp_path / "none.json"), project=str(proj)) is None
    for cmd in ("ecadlib", "python -m ecadlib", "py -3 -m ecadlib"):   # ECADLIB_CMD with a bare name: PATH only
        assert mod.ecadlib_command(dict(env, ECADLIB_CMD=cmd), run=never, project=str(proj)) is None, cmd
    env["PATH"] += os.pathsep + str(real)
    got = mod.ecadlib_command(dict(env, ECADLIB_CMD="ecadlib --offline"), run=never, project=str(proj))
    assert got and os.path.dirname(got[0]) == str(real) and got[1:] == ["--offline"], got
    got = mod.ecadlib_command(env, run=never, cmd_file=str(tmp_path / "none.json"), project=str(proj))
    assert got and len(got) == 1 and os.path.dirname(got[0]) == str(real), got
    # a saved command that is not a full path (a package built by hand) is not read from KiCad's folder
    saved = tmp_path / "ecadlib_cmd.json"
    saved.write_text(json.dumps({"command": ["ecadlib.bat"]}), encoding="utf-8")
    assert mod.ecadlib_command({"PATH": ""}, run=never, cmd_file=str(saved), project=str(proj)) is None
    saved.write_text(json.dumps({"command": [str(real / "ecadlib.exe"), "--x"]}), encoding="utf-8")
    assert mod.ecadlib_command({"PATH": ""}, run=never, cmd_file=str(saved)) == [str(real / "ecadlib.exe"), "--x"]


@lite
def test_26_ecadlib_never_starts_a_git_that_lies_in_the_current_folder(tmp_path, monkeypatch):
    import os
    from ecadlib import approvals, progpath as PP, upkeep, altium_dblib_sync as DS, pdf_render
    proj = _received_project(tmp_path)
    _program_file(proj, "svn", "gh", "pdftoppm", "tasklist", "powershell")
    marker = tmp_path / "ran.txt"
    for n in ("git", "svn", "gh", "pdftoppm"):                   # what the planted program would do
        (proj / n).write_text(f'#!/bin/sh\necho "$0" >> "{marker}"\necho planted\n')
    monkeypatch.chdir(proj)
    old = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(proj)]))
    for name in ("git", "svn", "gh", "pdftoppm", "tasklist", "powershell", "python"):
        assert PP.find(name) is None, name
        assert PP.find(name, nt=True, environ={"PATH": ".;;" + str(proj), "PATHEXT": ".exe;.bat"}) is None, name
    try:
        PP.resolved(["git", "config", "user.name"])
        raise AssertionError("a git in the current folder was taken")
    except FileNotFoundError as e:
        assert "git" in str(e)
    assert PP.resolved([str(proj / "git"), "x"]) == [str(proj / "git"), "x"]      # a path someone gave is theirs
    assert approvals._git("user.name") == ""
    assert upkeep.default_runner(["git", "--version"])[0] == 127 and upkeep.default_runner(["gh", "--version"])[0] == 127
    assert DS.run_cmd(["svn", "info"]).returncode == 127
    assert pdf_render._pdftoppm() is None
    assert not marker.exists(), marker.read_text()
    # with the real PATH after those entries, the real program is the one found (when this machine has one)
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(proj), old]))
    got = PP.find("git")
    if got:
        assert os.path.isabs(got) and os.path.dirname(os.path.realpath(got)) != os.path.realpath(str(proj)), got
        code, out, _ = upkeep.default_runner(["git", "--version"])
        assert code == 0 and "planted" not in out, out
    assert not marker.exists(), marker.read_text()


def test_26_altium_runner_works_from_its_own_folder_and_looks_on_path_only():
    from ecadlib import altium_menu as AM
    lines = AM.run_cmd_text().split("\r\n")
    code = [ln for ln in lines if ln and not ln.lower().startswith("rem")]
    guard, cd = code.index('set "NoDefaultCurrentDirectoryInExePath=1"'), code.index('cd /d "%~dp0" 2>nul || cd /d "%SystemRoot%"')
    starts = [i for i, ln in enumerate(code) if re_search(r"\bwhere\b|^py |^python |%ECADLIB% ", ln)]
    assert starts and max(guard, cd) < min(starts), code
    assert "where $PATH:ecadlib >nul 2>nul && set \"ECADLIB=ecadlib\"" in code     # PATH only: where's own look-up
    assert not any(re_search(r"^where ecadlib\b", ln) for ln in code)              # ... starts in the current folder


def re_search(rx, text):
    import re
    return re.search(rx, text)


@lite
def test_26_kicad_bom_helper_runs_ecadlib_from_its_own_folder(tmp_path):
    """The helper KiCad's Generate BOM starts (kicad_install.HOOK_SCRIPT): `python -m ecadlib` imports from the folder
    it starts in first, and KiCad's is the open project's."""
    import sys
    from ecadlib import kicad_install as KI
    proj = _received_project(tmp_path)
    (proj / "ecadlib.py").write_text("raise SystemExit('planted module ran')\n")
    (proj / "board.xml").write_text("<export/>")
    conf = tmp_path / "kicad config"
    conf.mkdir()
    (conf / KI.HOOK_FILE).write_text(KI.HOOK_SCRIPT, encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text("import json, os, sys\nopen(sys.argv[1], 'w').write(json.dumps([os.getcwd(), sys.argv[2:]]))\n")
    seen = tmp_path / "seen.json"
    (conf / "ecadlib_cmd.json").write_text(json.dumps({"command": [sys.executable, str(probe), str(seen)]}), encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(conf / KI.HOOK_FILE), "check", "board.xml", "--kicad", "7"], cwd=str(proj),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    cwd, argv = json.loads(seen.read_text())
    assert Path(cwd).resolve() == conf.resolve(), cwd
    assert argv[:2] == ["kicad-menu", "check-bom"] and Path(argv[2]).resolve() == (proj / "board.xml").resolve() and argv[3:] == ["--kicad", "7"], argv
    # a saved command that is not a full path would be looked for in the project's folder: refused, nothing started
    seen.unlink()
    (conf / "ecadlib_cmd.json").write_text(json.dumps({"command": ["python", str(probe), str(seen)]}), encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(conf / KI.HOOK_FILE), "check", "board.xml"], cwd=str(proj),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "full path" in r.stderr and not seen.exists(), r.stdout + r.stderr
