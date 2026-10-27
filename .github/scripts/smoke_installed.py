"""Smoke-test an installed ecadlib (wheel or sdist), run with the venv's python from a directory
that is NOT the checkout, so the source tree can't shadow the installed package.
    python smoke_installed.py [expected-version]"""
import json, os, subprocess, sys, tempfile
from importlib import metadata, resources
from pathlib import Path

import ecadlib

expected = sys.argv[1] if len(sys.argv) > 1 else None
pkg = Path(ecadlib.__file__).resolve().parent
assert "site-packages" in pkg.parts, f"ecadlib imported from {pkg}, not from site-packages"
assert metadata.version("ecadlib") == ecadlib.__version__, (metadata.version("ecadlib"), ecadlib.__version__)
if expected:
    assert ecadlib.__version__ == expected.lstrip("v"), f"tag {expected} != ecadlib {ecadlib.__version__}"

root = resources.files("ecadlib")
for rel in ("package_schema.json", "assets/pcblib_template.json.gz", "ui/static/index.html", "eval/sets/kicad-v1.json",
            "eval/baselines/kicad-v1.json", "eval/replay/kicad-v1.json.gz", "eval/golden/specs.txt",
            "altium_scripts/ecadlib_menu.pas", "altium_scripts/ecadlib_dialog.pas", "altium_scripts/ecadlib_build.pas"):
    assert root.joinpath(rel).is_file(), f"missing package data: {rel}"
specs = [p for p in root.joinpath("package_specs").iterdir() if p.name.endswith(".json")]
assert specs, "no package_specs/*.json in the installed package"

bindir = Path(sys.executable).parent
exe = bindir / ("ecadlib.exe" if sys.platform == "win32" else "ecadlib")
out = subprocess.run([sys.executable, "-m", "ecadlib", "--version"], check=True, capture_output=True, text=True).stdout
assert out.strip() == f"ecadlib {ecadlib.__version__}", out
out = subprocess.run([str(exe), "--help"], check=True, capture_output=True, text=True).stdout
assert "ecadlib ui" in out and "release-check" in out, out
try:                                     # needs the runtime deps (skipped for a --no-deps install)
    import jsonschema  # noqa: F401
except ImportError:
    print("skip  footprint gen: jsonschema not installed")
else:
    with tempfile.TemporaryDirectory() as d:
        spec = root.joinpath("package_specs", "SOIC-8_3.9x4.9mm_P1.27mm.json")
        with resources.as_file(spec) as sp:
            subprocess.run([str(exe), "footprint", "gen", str(sp), "-o", d], check=True, capture_output=True)
        assert list(Path(d).glob("*.kicad_mod")), "footprint gen wrote nothing"
    # what a new user runs first (README, START_HERE): the demo, the setup check and `init --yes`, with an empty home
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "ECADLIB_HOME": str(Path(d, "home")), "HOME": d, "USERPROFILE": d}
        env.pop("ECADLIB_WORKSPACE", None)
        run = lambda *a: subprocess.run([str(exe), *a], cwd=d, env=env, capture_output=True, text=True)
        r = run("demo", "-o", "demo")
        assert r.returncode == 0 and "ready" in r.stdout, r.stdout + r.stderr
        assert list(Path(d, "demo").rglob("plan.json")) and list(Path(d, "demo").rglob("*.kicad_sym")), "demo wrote no library"
        r = run("init", "--yes")
        assert r.returncode == 0 and Path(d, "ecadlib.toml").is_file(), r.stdout + r.stderr
        r = run("doctor", "--json")
        assert r.returncode == 0, r.stdout + r.stderr
        bad = [c for c in json.loads(r.stdout)["checks"] if c["level"] == "FAIL"]
        assert not bad, bad
print(f"ok: ecadlib {ecadlib.__version__} from {pkg}, {len(specs)} package specs")
