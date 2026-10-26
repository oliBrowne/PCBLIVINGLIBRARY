"""Fixes for the 2026-10-26 PR #196 review, menus round (R-MN-1..6 in reviews/2026-10-26_review_196/menus.md)."""
import json, sqlite3, sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixtures_lite import lite


# ---- R-MN-4: kicad-dblib re-adds a kept held part whose old row had an empty Footprint (was KeyError 'Footprint')
@lite
def test_rmn4_kept_part_with_empty_footprint_rebuilds(tmp_path):
    from ecadlib import kicad_dblib as KD
    out = tmp_path / "out"
    out.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([{"name": "LOGO1", "status": "held", "hold_reasons": ["fields: Description is empty"],
                                 "symbol": {}, "footprint": {}}]))
    (out / "kicad_export.json").write_text(json.dumps({"library": "lib", "parts": [], "footprints": {}, "written": {},
        "kept": [{"part": "LOGO1", "symbol_name": "LOGO1", "symbol": True, "footprint": None}]}))
    with closing(sqlite3.connect(out / "lib.sqlite")) as con, con:
        con.execute('CREATE TABLE "Parts" ("Part ID" TEXT PRIMARY KEY NOT NULL, "Symbol" TEXT, "Footprint" TEXT, "Value" TEXT)')
        con.execute('INSERT INTO "Parts" VALUES (?,?,?,?)', ("LOGO1", "lib:LOGO1", "", "LOGO1"))
    rep = KD.build(plan, out, "lib", log=lambda *a: None)
    assert rep["tables"] == {"Parts": 1}, rep
    with closing(sqlite3.connect(out / "lib.sqlite")) as con:
        assert con.execute('SELECT "Part ID", "Symbol", "Footprint" FROM "Parts"').fetchall() == [("LOGO1", "lib:LOGO1", "")]



# ---- R-MN-5: user text starting with '-' (Manufacturer "-TI") reaches run-job intact (was an argparse exit 2)
@lite
def test_rmn5_dash_values_reach_run_job(monkeypatch):
    from ecadlib import kicad_menu as KM
    from tests.test_tool_packages import _plugin_module
    mod, _ = _plugin_module()
    got = {}
    monkeypatch.setattr(KM, "run_job", lambda job, action, project, mpn, manufacturer, path, offline=False, ai=None:
                        got.update(job=job, project=project, mpn=mpn, manufacturer=manufacturer, path=path, ai=ai)
                        or {"status": "ok"})
    for maker in ("TI", "-TI", "--", "-"):
        argv = mod.job_command(["ecadlib"], "/j/job", "add-part", "-b.kicad_pcb", mpn="LM358DR", manufacturer=maker)
        assert KM.main(argv[2:]) == 0, argv
        assert got["manufacturer"] == maker and got["project"] == "-b.kicad_pcb" and got["mpn"] == "LM358DR", (maker, got)
    argv = mod.job_command(["ecadlib"], "/j/job", "open", "b.kicad_pcb", path="-r.html")
    assert KM.main(argv[2:]) == 0 and got["path"] == "-r.html", got
    argv = mod.job_command(["ecadlib"], "/j/job", "ai-read", "b.kicad_pcb", bom="-x.csv", part="-P1", approved_usd="0.4")
    assert KM.main(argv[2:]) == 0 and got["ai"]["bom"] == "-x.csv" and got["ai"]["part"] == "-P1", got



# ---- R-MN-6: altium-menu build refuses a non-ASCII --command (the menu reads command= in the ANSI code page)
@lite
def test_rmn6_non_ascii_command_is_refused(tmp_path):
    from ecadlib import altium_menu as AM
    cmd = '"C:\\Users\\José\\venv\\Scripts\\python.exe" -m ecadlib'
    try:
        AM.build(tmp_path / "m", command=cmd, win_dir="D:\\ecadlib-menu")
        raise AssertionError("built")
    except ValueError as e:
        assert "--command" in str(e) and "ASCII" in str(e), e
    assert not (tmp_path / "m" / AM.INI).exists()
    AM.build(tmp_path / "m2", command='"C:\\venv\\Scripts\\python.exe" -m ecadlib', win_dir="D:\\ecadlib-menu")
    assert b'command="C:\\venv\\Scripts\\python.exe" -m ecadlib' in (tmp_path / "m2" / AM.INI).read_bytes()



# ---- R-MN-1: kicad-pcm on a kicad-export --include-held folder refuses the held parts unless --include-held
@lite
def test_rmn1_pcm_refuses_held_parts_of_an_include_held_export(tmp_path):
    import zipfile
    from ecadlib import kicad_export as X, kicad_pcm as KP
    from tests import test_kicad_export as TKX
    plan, items, http = TKX._plan(tmp_path, api_key="sk-test")
    held = [n for n, i in items.items() if i["status"] == "held"]
    assert held
    out = tmp_path / "kx"
    X.export(plan, out, "lib", include_held=True, http=http, log=lambda *a: None)
    try:
        KP.build(out, tmp_path / "pcm", log=lambda *a: None)
        raise AssertionError("packaged held parts")
    except ValueError as e:
        assert "held" in str(e) and held[0] in str(e) and "--include-held" in str(e), e
    assert not list((tmp_path / "pcm").glob("*.zip")) if (tmp_path / "pcm").exists() else True
    rep = KP.build(out, tmp_path / "pcm", log=lambda *a: None, include_held=True)
    assert any("held" in p and held[0] in p for p in rep["problems"]), rep["problems"]
    with zipfile.ZipFile(rep["zip"]) as z:
        notice = z.read("NOTICE.txt").decode()
    assert "HELD" in notice and held[0] in notice, notice
    clean = tmp_path / "kx2"                                   # a plain export: no held part, nothing said
    X.export(plan, clean, "lib", http=http, log=lambda *a: None)
    rep = KP.build(clean, tmp_path / "pcm2", log=lambda *a: None)
    assert not any("held" in p for p in rep["problems"]), rep["problems"]


if __name__ == "__main__":
    fails = 0
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            try:
                f()
                print("ok", k)
            except Exception as e:
                fails += 1
                print("FAIL", k, type(e).__name__, e)
    sys.exit(1 if fails else 0)
