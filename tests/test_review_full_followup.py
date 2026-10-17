"""Regression tests for the issues the full end-to-end test run found after the review (kicad-export 3D models from a
local library, help examples that failed, MCP argument checks, upstream check-kicad without a manifest).
Run:  python3 -m tests.test_review_full_followup"""
import contextlib, io, json, os, shlex, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ecadlib(cwd, *args):
    env = dict(os.environ, PYTHONPATH=str(ROOT), ECADLIB_USER="t <t@example.com>")
    return subprocess.run([sys.executable, "-m", "ecadlib", *args], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=600)


def test_kicad_export_keeps_3d_models_of_a_local_kicad_library():
    """Footprints from ecadlib's own KiCad export point at ${KIPRJMOD}/<lib>.3dshapes/X.step: kicad-export reported
    0/5 STEP models and left the .kicad_mod pointing at nothing, and the Altium 3D body fallback never ran."""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "bom.csv").write_bytes((ROOT / "examples" / "sample_bom.csv").read_bytes())
        assert _ecadlib(d, "demo", "-o", "demo").returncode == 0
        r = _ecadlib(d, "librarian", "bom.csv", "-o", "lib", "--offline", "--lib", "demo")
        assert (Path(d) / "lib" / "plan.json").is_file(), r.stdout + r.stderr
        for out, extra in (("k1", []), ("k2", ["--lib", "demo"])):     # without --lib: the .PcbLib's STEP; with: KiCad's
            r = _ecadlib(d, "kicad-export", "lib/plan.json", "-o", out, "--name", "mylib", "--offline", *extra)
            rep = json.loads((Path(d) / out / "kicad_export.json").read_text())
            assert rep["models"] and all(rep["models"].values()), (out, rep["models"], r.stdout)
            for f in (Path(d) / out / "mylib.pretty").glob("*.kicad_mod"):
                text = f.read_text(encoding="utf-8")
                assert text.count("(model ") == 1, (f.name, text)
                step = text.split('(model "${KIPRJMOD}/', 1)[1].split('"', 1)[0]
                assert (Path(d) / out / step).is_file(), (out, step)
        shipped = Path(d) / "demo" / "kicad" / "demo_bom_demo" / "ecadlib_demo.3dshapes" / "SOT-23.step"
        assert (Path(d) / "k2" / "mylib.3dshapes" / "SOT-23.step").read_bytes() == shipped.read_bytes()
        r = _ecadlib(d, "parity", "lib/plan.json", "--kicad", "k1", "--name", "mylib")
        assert " 0 differ" in r.stdout, r.stdout[-2000:]


def test_help_examples_that_used_to_fail():
    from ecadlib.clihelp import EXAMPLES
    with tempfile.TemporaryDirectory() as d:
        # stdparts: "SOIC-8" alone is ambiguous (several bodies) and exited 1
        ex = next(e for e in EXAMPLES["stdparts"] if " resolve " in e)
        r = _ecadlib(d, *shlex.split(ex)[1:])
        assert r.returncode == 0 and "SOIC-8_3.9x4.9mm_P1.27mm" in r.stdout, (ex, r.stdout, r.stderr)
        # altium-lib pcblib takes package JSON specs, not a .kicad_mod
        ex = next(e for e in EXAMPLES["altium-lib"] if " pcblib " in e)
        args = shlex.split(ex)[1:]
        spec = Path(d) / args[2]
        spec.parent.mkdir(parents=True)
        spec.write_bytes((ROOT / "ecadlib" / "package_specs" / spec.name).read_bytes())
        r = _ecadlib(d, *args)
        assert r.returncode == 0 and (Path(d) / "out" / "altium" / "SOIC-8_3.9x4.9mm_P1.27mm.PcbLib").is_file(), r.stderr
        # altium365: plan -o is the plan file, and push --dry-run reads it back
        plan_ex, push_ex = EXAMPLES["altium365"]
        (Path(d) / "my_bom.csv").write_bytes((ROOT / "examples" / "sample_bom.csv").read_bytes())
        r = _ecadlib(d, *shlex.split(plan_ex)[1:])
        assert r.returncode == 0, r.stderr
        env_args = shlex.split(push_ex)[1:]
        r = _ecadlib(d, *env_args)
        assert r.returncode == 0 and "report ->" in r.stdout, (r.stdout, r.stderr)
        # a folder for -o now means the plan inside it; a missing plan says so instead of a NotADirectoryError
        (Path(d) / "dir_out").mkdir()
        assert _ecadlib(d, "altium365", "plan", "my_bom.csv", "-o", "dir_out").returncode == 0
        assert (Path(d) / "dir_out" / "plan.json").is_file()
        r = _ecadlib(d, "altium365", "push", "nowhere/plan.json", "--workspace", "https://x.365.altium.com", "--dry-run")
        assert r.returncode != 0 and "no such plan file" in r.stderr and "NotADirectoryError" not in r.stderr, r.stderr


def test_mcp_checks_arguments_against_the_schema():
    """A string where a list goes was iterated letter by letter (librarian_run sources, upkeep_run checks,
    agent_watch_scan sources): a list of names is now split, anything else that does not fit is refused clearly."""
    from ecadlib import mcp_server as M
    tool = lambda n: next(t for t in M.TOOLS if t["name"] == n)
    a = {"bom": "x.csv", "sources": "kicad, standard"}
    assert M._arg_errors(tool("librarian_run"), a) == "" and a["sources"] == ["kicad", "standard"]
    a = {"bom": "x.csv", "checks": "lifecycle"}
    assert M._arg_errors(tool("upkeep_run"), a) == "" and a["checks"] == ["lifecycle"]
    assert "not one of" in M._arg_errors(tool("librarian_run"), {"bom": "x.csv", "sources": "local"})
    assert "is not of type 'object'" in M._arg_errors(tool("agent_watch_scan"), {"sources": "bom_file:x.csv"})
    from ecadlib.app import Workspace
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        out = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "agent_watch_scan", "arguments": {"sources": "bom_file:x.csv"}}})
        res = out["result"]
        assert res["isError"] and "invalid argument(s): sources" in res["content"][0]["text"], out


def test_upstream_check_kicad_without_a_manifest_gives_a_hint():
    from ecadlib import upstream as U
    with tempfile.TemporaryDirectory() as d:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = U.main(["check-kicad", str(Path(d) / "manifest.json"), "--offline"])
        assert rc == 2 and "no manifest at" in err.getvalue() and "FileNotFoundError" not in err.getvalue(), err.getvalue()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
