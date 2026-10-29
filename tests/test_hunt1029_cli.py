"""2026-10-29 superpowers bug hunt (cli): end-to-end command line and MCP server use, hostile BOMs.
Plain functions: python -m tests.run_all test_hunt1029_cli"""
import contextlib, io, json
from pathlib import Path

from tests.fixtures_lite import lite


def _held_plan(d):
    d.mkdir(parents=True, exist_ok=True)
    items = [{"name": "X1", "folder": "Components/Crystals", "description": "X1 desc", "comment": "=Value",
              "parameters": [], "cleared": [], "status": "held",
              "symbol": {"ref": None, "file": None}, "footprint": {"ref": "C_0402_1005Metric", "file": None},
              "hold_reasons": ["files: no symbol model (.SchLib)"]}]
    (d / "plan.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    return d / "plan.json"


# ---------------------------------------------------------------- cli-01: every part held -> no DbLib written
@lite
def test_dblib_sync_with_every_part_held_does_not_claim_a_dblib_it_never_wrote(tmp_path):
    # `ecadlib dblib-sync plan --to lib` with no ready part wrote nothing (the folder was not even made), yet printed
    # "written: lib/ecadlib.DbLib  open this in Altium" and exited 0
    from ecadlib import altium_dblib_sync as S
    plan = _held_plan(tmp_path / "p")
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            S.main([str(plan), "--to", str(tmp_path / "lib")])
        except SystemExit as e:
            code = e.code
    text = out.getvalue() + err.getvalue() + str(code)   # sys.exit(message): Python prints it to stderr
    assert not (tmp_path / "lib" / "ecadlib.DbLib").exists()
    assert "open this in Altium" not in text, text
    assert "nothing written" in text, text
    assert code not in (0, None), (code, text)


@lite
def test_export_altium_dblib_with_every_part_held_answers_instead_of_errno(tmp_path):
    # MCP export_altium_dblib / the web UI's DbLib export listed altium/<BOM>/ after the sync: with every part held
    # the folder was never made and the answer was "[Errno 2] No such file or directory: <full path>"
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "w", offline=True)
    bom = ws.root / "uploads" / "b.csv"
    bom.write_text("Designator,Qty,Value,Manufacturer,MPN\nY1,1,16MHz,Acme,X1\n", encoding="utf-8")
    _held_plan(ws._librarian_dir(bom))
    r = ws.export_dblib("uploads/b.csv")
    assert r["files"] == [] and r["held"] == ["X1"] and r["written"] == 0, r


def _ws_with_held_library(tmp_path):
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "w", offline=True)
    (ws.root / "uploads" / "b.csv").write_text("Designator,Qty,Value,Footprint,Manufacturer,MPN\n"
                                               "R1,1,10k,R_0603_1608Metric,UNI-ROYAL,0603WAF1002T5E\n", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = ws.librarian_run("uploads/b.csv", sources=["standard"], ai=False, offline=True)
    assert r["held"] >= 1 and not r.get("ready"), r       # no datasheet link offline: held
    return ws


# ---------------------------------------------------------------- cli-02: tool_packages with every part held
@lite
def test_tool_packages_include_held_returns_the_kicad_zip_when_altium_has_nothing_released(tmp_path):
    # MCP tool_packages include_held=true wrote the KiCad zip with the held parts, then the Altium side (release-checked
    # parts only) raised "nothing to package: all 1 part(s) are held" and the whole answer was that error: the zip
    # written on disk was never named
    ws = _ws_with_held_library(tmp_path)
    r = ws.tool_packages("uploads/b.csv", include_held=True)
    assert r["kicad"]["zip"] and (ws.root / r["kicad"]["zip"]).is_file(), r
    assert r["kicad"]["footprints"] >= 1, r
    assert r["altium"]["libpkg"] is None and any("held" in p for p in r["altium"]["problems"]), r
