"""Regression tests for the 2026-10-20 independent testing pass, KiCad outputs and install / first run. Offline; no API keys."""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]
DEAD = {k: "http://127.0.0.1:9" for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}


def _run(argv, cwd=None, env=None):
    """cli.main(argv) in cwd -> (exit code, stdout, stderr)."""
    from ecadlib import cli
    out, err = io.StringIO(), io.StringIO()
    old_cwd, old_env = os.getcwd(), dict(os.environ)
    try:
        if cwd:
            os.chdir(cwd)
        for k in ("ECADLIB_LIB", "ECADLIB_WORKSPACE", "ECADLIB_CONFIG", "ECADLIB_DEBUG"):
            os.environ.pop(k, None)
        os.environ.update(DEAD)
        os.environ.update(env or {})
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(argv)
            except SystemExit as e:
                rc = e.code
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
    return rc, out.getvalue(), err.getvalue()


def _sub(args, cwd, env=None):
    """python -m ecadlib args in cwd, this checkout first on the path -> CompletedProcess."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "ECADLIB_"))}
    e.update(DEAD, PYTHONPATH=str(ROOT), NO_COLOR="1", ECADLIB_HOME=str(Path(cwd) / "home"))
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=str(cwd), env=e, capture_output=True,
                          text=True, timeout=300)


_DEMO = {}


def _demo():
    """One offline `ecadlib demo -o ws` for the tests that only read it -> the workspace folder (do not change it)."""
    if "ws" not in _DEMO:
        d = Path(tempfile.mkdtemp(prefix="indtest-kicad-"))
        rc, _, err = _run(["demo", "-o", "ws"], cwd=d, env={"ECADLIB_HOME": str(d / "home")})
        assert rc == 0, err
        _DEMO["ws"] = d / "ws"
    return _DEMO["ws"]


# T030
def test_standard_regulator_output_is_power_out_in_kicad():
    from ecadlib import kicad_export as KX, sexpr
    ws = _demo()
    plan_dir = ws / "librarian" / "demo_bom_demo"
    item = next(i for i in json.loads((plan_dir / "plan.json").read_text()) if i["name"] == "AMS1117-3.3")
    sym, how = KX.load_symbol(item, plan_dir, None, [], "AMS1117-3.3", [])
    got = {str(sexpr.first(p, "number")[1]).strip('"'): p[1] for u in sexpr.find(sym, "symbol") for p in sexpr.find(u, "pin")}
    assert got == {"1": "power_in", "2": "power_out", "3": "power_in"}, (got, how)


# T033
@lite
def test_kicad_pcm_refuses_a_license_kicads_pcm_refuses(tmp_path):
    from ecadlib import kicad_pcm as PCM
    m = PCM.metadata("mylib", "local.ecadlib.mylib", "1.0.0", 8, "Proprietary-X", "me")
    assert any("license" in p for p in PCM.check_metadata(m, schema_path=False)[0])
    assert not PCM.check_metadata(dict(m, license="CC-BY-SA-4.0"), schema_path=False)[0]
    rc, _, err = _run(["kicad-pcm", str(tmp_path / "nope" / "plan.json"), "--out", str(tmp_path / "pcm"), "--license", "mit"])
    assert rc == 2 and "did you mean MIT" in err and not (tmp_path / "pcm").exists(), err
