"""2026-10-26 bug hunt, web UI and the Workspace API behind it (ecadlib/ui/server.py, ecadlib/app.py, easy_setup,
userconfig). Offline. Run:  python3 -m pytest -q tests/test_hunt1026_webui.py   (or python -m tests.run_all)"""
import contextlib, io
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]


@lite
def test_webui_01_altium_models_carry_a_height_and_a_3d_body(tmp_path):
    """The web UI's / MCP's "Build Altium models" converted KiCad footprints with HEIGHT 0 and no 3D body, unlike the
    librarian and `ecadlib kicad-import` (same footprint, same tables)."""
    from ecadlib import altium_lib as AL
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws", [str(ROOT / "out" / "footprints")], providers=[], offline=True)
    up = ws.save_upload("bom.csv", b"Designator,Qty,Value,Footprint,MPN\n"
                                   b"R1,1,10k,ecadlib:R_0603_1608Metric,RC0603FR-0710KL\n"
                                   b"U1,1,X,ecadlib:SOIC-8_3.9x4.9mm_P1.27mm,NE555DR\n")
    with contextlib.redirect_stdout(io.StringIO()):
        r = ws.build_altium_models(up)
    assert set(r["footprints"]) == {"R_0603_1608Metric", "SOIC-8_3.9x4.9mm_P1.27mm"}, r
    for name, h in (("R_0603_1608Metric", 0.9), ("SOIC-8_3.9x4.9mm_P1.27mm", 1.75)):
        fp = AL.read_pcblib(ws.models / "PcbLib" / f"{name}.PcbLib")[0]
        assert float(str(fp["params"]["HEIGHT"]).replace("mil", "")) > 0, (name, fp["params"])
        assert any(rec["type"] == "body" for rec in fp["records"]), (name, [rec["type"] for rec in fp["records"]])

