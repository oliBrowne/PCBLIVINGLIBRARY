"""2026-10-29 independent injection review: each test reproduces a confirmed bug on main c7a276d."""
import json

from ecadlib.app import Workspace
from ecadlib import mcp_server as M
from tests.fixtures_lite import lite
from tests.test_altium_project import write_schdoc, comp, mpn


def _confined_ws(tmp_path):
    outside = tmp_path / "outside_private"
    outside.mkdir()
    write_schdoc(outside / "Secret.SchDoc", [comp("U9", "SecretChip", "SECRETAA", mpn("TOPSECRET-MPN-123", "AcmeCorp"),
                                                  ["QFN-48"], desc="Unreleased product controller")])
    ws = Workspace(str(tmp_path / "ws"), [], None, True)
    ws.mcp_options = M.Options(confine=True)             # `ecadlib mcp --http` default
    return ws


def _call(ws, name, args):
    r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r["result"]["content"][0]["text"]


def _upload_project(ws):
    prj = "[Design]\nVersion=1.0\n[Document1]\nDocumentPath=..\\..\\..\\outside_private\\Secret.SchDoc\n"
    return json.loads(_call(ws, "upload_bom", {"filename": "Board.PrjPcb", "content": prj}))["bom"]


@lite
def test_project_bom_does_not_read_schematics_outside_the_workspace(tmp_path):
    ws = _confined_ws(tmp_path)
    out = _call(ws, "project_bom", {"project": _upload_project(ws)})
    assert "TOPSECRET" not in out and "AcmeCorp" not in out


@lite
def test_where_used_does_not_read_schematics_outside_the_workspace(tmp_path):
    ws = _confined_ws(tmp_path)
    out = _call(ws, "where_used", {"projects": [_upload_project(ws)]})
    assert "TOPSECRET" not in out and "U9" not in out
