"""Smoke-test an installed ecadlib (wheel or sdist), run with the venv's python from a directory
that is NOT the checkout, so the source tree can't shadow the installed package.
    python smoke_installed.py [expected-version]"""
import subprocess, sys, tempfile
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
for rel in ("package_schema.json", "assets/pcblib_template.json.gz", "ui/static/index.html"):
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
print(f"ok: ecadlib {ecadlib.__version__} from {pkg}, {len(specs)} package specs")
