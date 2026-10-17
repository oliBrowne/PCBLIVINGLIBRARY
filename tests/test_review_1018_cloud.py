"""Regression tests from the overnight cloud pass (2026-10-18): `altium365 plan -o <new folder>` and the MCP
visual_check error when Pillow is missing. Run:
    python3 -m tests.run_all test_review_1018_cloud"""
import contextlib, io, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium365 as A, app as APP, mcp_server as M, visual_check as VC


def test_a365_plan_out_without_extension_is_a_folder():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "bom.csv"
        bom.write_text("Designator,Quantity,Manufacturer Part Number,Manufacturer\nR1,1,RC0603FR-0710KL,YAGEO\n")
        out = Path(d) / "a365"                       # does not exist yet: was written as a file called a365
        with contextlib.redirect_stdout(io.StringIO()):
            A.main(["plan", str(bom), "-o", str(out)])
        assert out.is_dir() and (out / "plan.json").is_file()


def test_mcp_visual_check_without_pillow_is_a_user_error():
    with tempfile.TemporaryDirectory() as d:
        W = APP.Workspace(Path(d) / "w", providers=["digikey"])
        err = VC.VisualCheckError("visual-check draws with Pillow: pip install 'ecadlib[vision]' (or pillow)")
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "visual_check", "arguments": {"target": "x"}}}
        stderr = io.StringIO()
        with mock.patch.object(APP.Workspace, "visual_check", side_effect=err), contextlib.redirect_stderr(stderr):
            r = M.handle(W, msg)["result"]
        assert r["isError"] and r["content"][0]["text"] == str(err), r
        assert "Traceback" not in stderr.getvalue()
