"""Bug-hunt regressions (BOM reading, group bomb). Plain functions: python3 -m tests.run_all test_bughunt_bomb"""
import tempfile
from pathlib import Path

from ecadlib import bom_triage as T


def _write(text, suffix=".csv", enc="utf-8"):
    f = Path(tempfile.mkdtemp()) / f"bom{suffix}"
    f.write_bytes(text.encode(enc) if isinstance(text, str) else text)
    return f


def _item(name, fp_ref, value="10k", mpn=""):
    pars = [{"name": "Value", "value": value}] + ([{"name": "Manufacturer Part Number", "value": mpn}] if mpn else [])
    return {"name": name, "parameters": pars, "footprint": {"ref": fp_ref}}


def test_bom59_dnp_line_not_built_from_fitted_line_of_other_package():
    from ecadlib.librarian import _line_for
    for mpn in ("RC0603FR-0710KL", ""):
        lines = [{"designator": "R5", "value": "10k", "footprint": "R_0402_1005Metric", "mpn": mpn, "dnp": True},
                 {"designator": "R1", "value": "10k", "footprint": "R_0603_1608Metric", "mpn": mpn, "dnp": False}]
        rows = [{"status": "dnp (not fitted)", "local_footprint": ""}, {"status": "needs_footprint_generation",
                                                                          "local_footprint": ""}]
        ln, row = _line_for(_item("RES-0402-10k", "R_0402_1005Metric", mpn=mpn), lines, rows)
        assert ln["designator"] == "R5", (mpn, ln)
        ln, _ = _line_for(_item("RES-0603-10k", "R_0603_1608Metric", mpn=mpn), lines, rows)
        assert ln["designator"] == "R1"
    # same footprint on both lines: the fitted one still wins
    lines = [{"designator": "R5", "value": "10k", "footprint": "R_0603_1608Metric", "dnp": True},
             {"designator": "R1", "value": "10k", "footprint": "R_0603_1608Metric", "dnp": False}]
    rows = [{"status": "dnp (not fitted)"}, {"status": "needs_footprint_generation"}]
    assert _line_for(_item("RES-0603-10k", "R_0603_1608Metric"), lines, rows)[0]["designator"] == "R1"
