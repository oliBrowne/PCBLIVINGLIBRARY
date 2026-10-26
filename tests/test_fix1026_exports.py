"""Fixes 2026-10-26 (exports): the Altium 365 workspace guard on the UI/MCP push, the CLI `dblib` release gate, and
pulled Altium 365 parts through the release check on the KiCad / parts-table routes."""
from unittest import mock

from tests.fixtures_lite import lite


def _ws_with_rules(tmp_path, monkeypatch, workspace=None):
    from ecadlib.app import Workspace
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "w"
    root.mkdir()
    if workspace is not None:
        (root / "ecadlib.toml").write_text(f'[altium365]\nworkspace = "{workspace}"\n', encoding="utf-8")
    ws = Workspace(root)
    seen = []
    monkeypatch.setattr(Workspace, "_altium365_push",
                        lambda self, bom, url, dry_run, lib_plan: seen.append((url, dry_run)) or {"ok": True})
    return ws, seen


def _refused(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    raise AssertionError("not refused")


@lite
def test_real_push_to_another_workspace_than_the_rules_name_is_refused(tmp_path, monkeypatch):
    """The web UI / MCP push sent a real push to any workspace URL the token could see (e.g. the team workspace)."""
    ws, seen = _ws_with_rules(tmp_path, monkeypatch, "https://mine.365.altium.com")
    msg = _refused(lambda: ws.altium365_push("b.csv", "https://team.365.altium.com", dry_run=False))
    assert "https://team.365.altium.com" in msg and "https://mine.365.altium.com" in msg and "[altium365]" in msg, msg
    assert seen == []
    ws.altium365_push("b.csv", "https://team.365.altium.com", dry_run=True)         # a dry run sends nothing: allowed
    ws.altium365_push("b.csv", "https://Mine.365.Altium.com/", dry_run=False)       # same workspace, other spelling
    ws.altium365_push("b.csv", None, dry_run=False)                                 # no URL: the configured one
    assert seen == [("https://team.365.altium.com", True), ("https://Mine.365.Altium.com/", False),
                    ("https://mine.365.altium.com", False)], seen


@lite
def test_real_push_with_no_configured_workspace_is_refused(tmp_path, monkeypatch):
    ws, seen = _ws_with_rules(tmp_path, monkeypatch, None)
    msg = _refused(lambda: ws.altium365_push("b.csv", "https://team.365.altium.com", dry_run=False))
    assert "[altium365]" in msg and "workspace" in msg and "ecadlib.toml" in msg, msg
    ws.altium365_push("b.csv", "https://team.365.altium.com", dry_run=True)
    ws.altium365_push("b.csv", "", dry_run=True)
    assert seen == [("https://team.365.altium.com", True), ("", True)], seen


@lite
def test_mcp_real_push_goes_through_the_guard(tmp_path, monkeypatch):
    from ecadlib import mcp_server as M
    ws, seen = _ws_with_rules(tmp_path, monkeypatch, "https://mine.365.altium.com")
    ws.mcp_options = M.Options()
    from tests.test_bughunt_mcpcli import _call
    r = _call(ws, "altium365_push", {"bom": "b.csv", "workspace": "https://team.365.altium.com", "dry_run": False})
    assert r["isError"] and "[altium365]" in str(r), r
    assert seen == []


# ------------------------------------------------------------------ item 3: pulled Altium 365 parts are checked
def _pulled(d, name, fp_name):
    from tests.test_release_check import make
    it = make(d, name=name, fp_name=fp_name)
    it.update(status="existing", a365={"id": f"c-{name}", "revision": 1},
              source={"component": "a365", "symbol": f"a365:{name}", "footprint": f"a365:{fp_name}"})
    return it


def _three_routes(plan):
    from ecadlib import kicad_dblib as KD, kicad_export as KX, parts_export as PE
    rep = KX.export(plan, plan.parent / "kicad", "mylib", log=lambda *a, **k: None)
    tables, skipped = KD.part_rows(plan, "mylib")
    return ({p["part"] for p in rep["parts"]}, {s["part"]: s["why"] for s in rep["skipped"]},
            {r["Part ID"] for rs in tables.values() for r in rs}, {s["part"]: s["why"] for s in skipped},
            {r["Part ID"] for r in PE.rows(plan)})


@lite
def test_a_broken_pulled_part_is_skipped_with_its_reason_a_good_one_exports(tmp_path, monkeypatch):
    """Parts pulled from Altium 365 (status existing, a365 source) went out on kicad-export, kicad-dblib and
    export-parts with no release check: a pulled part whose footprint file is gone, or whose pins and pads disagree,
    was exported. Now they get the checks that apply to them; the field rules (datasheet, Description) do not: the
    workspace released those fields, so a good pulled part with none still exports."""
    import json
    from tests.test_release_check import make
    monkeypatch.setenv("ECADLIB_CONFIG", "")
    good = _pulled(tmp_path, "GOOD", "SOIC-8_good")
    good["description"] = ""
    good["parameters"] = [p for p in good["parameters"] if "Datasheet" not in p["name"] and "Link" not in p["name"]]
    gone = _pulled(tmp_path, "GONE", "SOIC-8_gone")
    (tmp_path / gone["footprint"]["file"]).unlink()
    pins = _pulled(tmp_path, "PINS", "SOIC-8_pins")
    other = make(tmp_path, name="PINS_SYM", fp_name="SOIC-8_pins_sym", pins=range(1, 7))   # a 6-pin symbol on 8 pads
    pins["symbol"] = dict(other["symbol"])
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([good, gone, pins]))
    kx, kx_skip, kd, kd_skip, pe = _three_routes(plan)
    assert kx == kd == pe == {"GOOD"}, (kx, kd, pe)
    assert "files: footprint file missing" in kx_skip["GONE"] and "files: footprint file missing" in kd_skip["GONE"], kx_skip
    assert "pins:" in kx_skip["PINS"] and "pins:" in kd_skip["PINS"], kx_skip


def test_pulled_part_check_leaves_out_only_the_field_rules():
    import tempfile
    from pathlib import Path
    from ecadlib import release_check as RC
    with tempfile.TemporaryDirectory() as d:
        it = _pulled(d, "P1", "SOIC-8_p1")
        it["description"] = ""
        assert RC.publish_problems(it, d) == []                       # the Altium 365 push: as before (no files sent)
        assert RC.publish_problems(it, d, writes_files=True) == []    # fields: not ecadlib's to check on a pulled part
        Path(d, it["symbol"]["file"]).unlink()
        assert RC.publish_problems(it, d) == []
        assert RC.publish_problems(it, d, writes_files=True)[0].startswith("files: symbol file missing")
        none = dict(it, symbol={"ref": "P1", "file": None}, footprint={"ref": "X", "file": None})   # pulled, no files
        assert RC.publish_problems(none, d, writes_files=True)[0].startswith("files: no symbol model")
