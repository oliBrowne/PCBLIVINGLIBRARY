"""Regression tests for the code review of the 2026-10-20 independent-testing fixes (T-ids of the fix each one
follows up in the comments). Nothing live. Run:
    python3 -m tests.test_indtest_review"""
import json
from pathlib import Path

from ecadlib import altium_lib as AL
from tests.fixtures_lite import lite, released_anyway
from tests.test_indtest_altium import _item, _models


# T092 follow-up: --no-native-build leaves the recipes out but keeps the .ecadstate edit records, so a library
# changed in Altium in the package is not copied over by the next run
@lite
def test_no_native_build_keeps_an_altium_edit(tmp_path, monkeypatch):
    from ecadlib import altium_native as AN, altium_pkg as PK
    d = tmp_path
    monkeypatch.setenv("ECADLIB_HOME", d / "home")
    _models(d)
    for f in (d / "models").rglob("*.*Lib"):
        f.with_name(f.name + AN.EXT).write_text("KIND\tx\n", encoding="utf-8")
    items = released_anyway([_item("RES-0603-10k", "Resistors", "Resistor", "R_0603_1608Metric", Value="10k")], d)
    (d / "plan.json").write_text(json.dumps(items, indent=1))
    for src in (d / "plan.json", d / "models"):
        pkg = d / f"pkg_{src.stem}"
        args = dict(plan=d / "plan.json") if src.is_dir() else {}
        PK.build(src, pkg, native=False, **args)
        assert list(pkg.rglob(f"*{AN.EXT}")) == []
        lib = next(pkg.rglob("R_0603_1608Metric.PcbLib"))
        data = bytearray(lib.read_bytes())
        data[-1] ^= 1                                       # changed in Altium
        lib.write_bytes(bytes(data))
        rep = PK.build(src, pkg, native=False, **args)
        assert lib.read_bytes() == bytes(data), src         # the edit is still there
        assert "changed in Altium" in json.dumps(rep), rep
        assert list(pkg.rglob(f"*{AN.EXT}")) == []


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
