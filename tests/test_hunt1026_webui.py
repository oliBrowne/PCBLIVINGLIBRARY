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


@lite
def test_webui_02_bad_json_field_types_are_400s_not_crashed_jobs(tmp_path):
    """sources / refresh of /api/librarian/run went into the job unchecked (true -> TypeError in the job, "Something
    went wrong"); a JSON Infinity (Python's json reads it) crashed int() in a job with OverflowError."""
    import json
    from ecadlib.app import Workspace
    from tests.test_ui import serve, wait_job
    ws = Workspace(tmp_path / "ws", [], providers=[], offline=True)
    bom = ws.save_upload("bom.csv", b"Designator,Qty,Value,MPN\nR1,1,10k,RC0603FR-0710KL\n")
    srv, call = serve(ws)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            for body in ({"bom": bom, "sources": True}, {"bom": bom, "sources": "standard"}, {"bom": bom, "sources": [5]},
                         {"bom": bom, "refresh": 5}, {"bom": bom, "refresh": "R1"}, {"bom": bom, "refresh": [None]}):
                st, j = call("POST", "/api/librarian/run", json.dumps(body))
                assert st == 400 and "job" not in j, (body, st, j)
            for path, raw in (("/api/resolve/candidates", '{"bom": "%s", "part": "R1", "limit": Infinity}' % bom),
                              ("/api/ai/read", '{"bom": "%s", "approved_usd": NaN}' % bom),
                              ("/api/footprint/preview", '{"spec": {"name": "X", "body_length": Infinity}}')):
                st, j = call("POST", path, raw)
                assert st == 400 and "job" not in j and "JSON allows" in j["error"], (path, st, j)
            st, j = call("POST", "/api/librarian/run", json.dumps({"bom": bom, "sources": ["standard"], "refresh": True}))
            assert st == 200 and wait_job(call, j["job"])["state"] == "done", j
            st, j = call("POST", "/api/librarian/run", json.dumps({"bom": bom, "sources": None, "refresh": ["R1"]}))
            assert st == 200 and wait_job(call, j["job"])["state"] == "done", j
    finally:
        srv.shutdown()
        srv.server_close()


@lite
def test_webui_03_uploads_never_take_a_windows_device_name(tmp_path):
    """An upload named con.csv / NUL.xlsx / com1.csv / LPT9.tsv was stored under that name. On Windows those names are
    devices (CON = the console), so the BOM is lost on write and triage then reads the device."""
    import re
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws", [], providers=[], offline=True)
    data = b"Designator,Qty,Value,MPN\nR1,1,10k,RC0603FR-0710KL\n"
    for n in ("con.csv", "CON", "nul.xlsx", "Aux.csv", "prn.tsv", "com1.csv", "LPT9.txt", "con.tar.csv", "COM¹.csv"):
        name = ws.save_upload(n, data).rsplit("/", 1)[1]
        assert not re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?", name), (n, name)
        assert Path(n).suffix.lower() == Path(name).suffix.lower(), (n, name)     # the reader still picks by the suffix
    for n in ("console.csv", "bom_con.csv", "com10.csv", "nullable.csv"):          # not devices: kept
        assert ws.save_upload(n, data).endswith("/" + n), n
