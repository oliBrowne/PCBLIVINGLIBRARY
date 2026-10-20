"""Debug sweep 2, group "checks": regression tests for project check / freeze / scan, parity, house style and messages.
Run:  python -m tests.run_all test_debugsweep_checks"""
import shutil
from pathlib import Path

from ecadlib import project_check as PJ
from tests.fixtures_lite import lite
from tests.test_project_check import make_project


@lite
def test_ds2_57_freeze_dotted_project_name_leaves_the_sibling_project_alone(tmp_path):
    """PSU_v1.1.kicad_pro next to an older PSU_v1 project: freeze must rewrite PSU_v1.1.kicad_sch, not PSU_v1's."""
    d = tmp_path.resolve()
    make_project(d)
    for ext in (".kicad_pro", ".kicad_sch", ".kicad_pcb"):
        shutil.copy(d / f"board{ext}", d / f"PSU_v1.1{ext}")
        shutil.copy(d / f"board{ext}", d / f"PSU_v1{ext}")
    sibling = {f: (d / f).read_bytes() for f in ("PSU_v1.kicad_sch", "PSU_v1.kicad_pcb")}
    PJ.freeze_kicad(d / "PSU_v1.1.kicad_pro")
    assert {f: (d / f).read_bytes() for f in sibling} == sibling, "freeze rewrote the PSU_v1 project"
    assert '"PSU_v1.1-frozen:QN"' in (d / "PSU_v1.1.kicad_sch").read_text()
    assert '"PSU_v1.1-frozen:SOT23"' in (d / "PSU_v1.1.kicad_pcb").read_text()
