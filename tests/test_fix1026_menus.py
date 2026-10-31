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
    monkeypatch.setattr(KM, "run_job", lambda job, action, project, mpn, manufacturer, path, offline=False, ai=None, kicad=None:
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



# ---- R-MN-3: check-bom counts a part as in the library only when the library has it (folder: its DbLib table;
# Altium 365: a push recorded in the ledger), never because some plan has it ready
def _checkbom_ws(d):
    ws, lib = d / "ws", d / "lib"
    lib.mkdir()
    pl = ws / "librarian" / "other_bom"
    pl.mkdir(parents=True)
    (pl / "plan.json").write_text(json.dumps([{"name": "LM358DR", "status": "ready",
                                               "parameters": [{"name": "Manufacturer Part Number", "value": "LM358DR"}]}]))
    bom = d / "bom.csv"
    bom.write_text("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #\nU1,1,LM358,SOIC-8,TI,LM358DR\n")
    return {"workspace": ws, "library": lib, "name": "ecadlib", "port": 8765, "offline": True}, bom, pl


@lite
def test_rmn3_folder_library_counts_only_its_dblib_table(tmp_path):
    from ecadlib import altium_menu as AM
    cfg, bom, _ = _checkbom_ws(tmp_path)
    res = AM.check_bom(cfg, bom, opener=False)
    assert res["missing"] == 1 and res["offer_fill"] == "1", res
    (cfg["library"] / "ecadlib.csv").write_text("Table,Part Number,Manufacturer Part Number\nICs,LM358DR,LM358DR\n")
    res = AM.check_bom(cfg, bom, opener=False)
    assert res["missing"] == 0 and res["offer_fill"] == "0", res


@lite
def test_rmn3_a365_counts_only_pushed_parts(tmp_path, monkeypatch):
    from ecadlib import altium_menu as AM, altium365 as A365, ledger as LG
    cfg, bom, pl = _checkbom_ws(tmp_path)
    url = "https://acme.365.altium.com"
    monkeypatch.setattr(AM, "_a365_url", lambda ws: url)
    res = AM.check_bom(cfg, bom, opener=False)
    assert res["missing"] == 1, res                          # ready, but never pushed (held at the push)
    LG.Ledger(pl).append({"event": "publish", "part": "LM358DR", "target": A365.ws_target(url), "revision": 1})
    res = AM.check_bom(cfg, bom, opener=False)
    assert res["missing"] == 0 and res["offer_fill"] == "0", res



# ---- R-MN-2: the Altium dialog's Release anyway releases only the reasons it showed
def test_rmn2_offer_lists_every_reason_with_a_key(monkeypatch=None):
    from ecadlib import altium_menu as AM
    reasons = ["fields: Description is empty", "pins: pads without a symbol pin: 9",
               "footprint: silkscreen shape covers pad 3", "footprint: no courtyard",
               "footprint: pads 1 and 2 overlap by 0.12 mm", "footprint: pad 4 sticks out of the courtyard"]
    it = {"name": "PART1", "status": "held", "hold_reasons": reasons}
    whys = AM.releasable(it)
    res = AM.offer_release({}, it, "uploads/altium-menu/parts.csv", "PART1")
    shown = [res[f"release_why{i}"] for i in range(1, len(whys) + 1)]
    assert shown == whys and len(whys) == 6, (whys, res)                     # all six, not the first four
    assert res["release_count"] == "6" and res["release_key"] == AM.release_key(it), res
    other = AM.offer_release({}, dict(it, hold_reasons=reasons + ["footprint: no 3D body"]), "b", "PART1")
    assert other["release_key"] != res["release_key"]


@lite
def test_rmn2_too_many_reasons_say_more_and_are_not_released(monkeypatch):
    from ecadlib import altium_menu as AM
    many = [f"reason {i}" for i in range(AM.RELEASE_SHOWN + 3)]
    monkeypatch.setattr(AM, "releasable", lambda it: list(many))
    res = AM.offer_release({}, {"name": "P", "hold_reasons": many}, "b", "P")
    assert f"release_why{AM.RELEASE_SHOWN}" in res and f"release_why{AM.RELEASE_SHOWN + 1}" not in res, res
    assert res["release_more"] == "3" and res["release_count"] == str(len(many)), res


def _held_plan_release(extra_reason=None, key="right", before=None):
    from ecadlib import altium_menu as AM
    from tests import test_altium_menu as TM, test_altium_release_anyway as TR
    menu, cfg = TM._setup()
    with TR._held_check(TR.HEIGHT):
        held = TR._held(menu)
        plan = next(cfg["workspace"].glob("librarian/*/plan.json"))
        if extra_reason:                                # a reason that came up after the dialog showed the others
            items = json.loads(plan.read_text(encoding="utf-8"))
            for i in items:
                if i["name"] == held["release_part"]:
                    i["hold_reasons"].append(extra_reason)
            plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
        req = {"action": "release", "bom": held["release_bom"], "mpn": TR.MPN, "part": held["release_part"],
               "reason": "checked", **({"release_key": held["release_key"]} if key == "right" else
                                       {"release_key": key} if key else {})}
        if before:
            before(AM)
        return AM.run_job(TM._job(menu, req)), plan, held


def test_rmn2_release_with_the_shown_key_releases():
    r, plan, held = _held_plan_release()
    assert r["status"] == "ready", r


def test_rmn2_new_reason_since_the_dialog_is_not_released():
    r, plan, held = _held_plan_release(extra_reason="footprint: pads 1 and 2 overlap by 0.12 mm")
    assert r["status"] == "error" and "changed" in r["message"], r
    it = next(i for i in json.loads(plan.read_text(encoding="utf-8")) if i["name"] == held["release_part"])
    assert it["status"] == "held", it.get("status")


def test_rmn2_more_reasons_than_shown_are_not_released():
    from unittest import mock
    many = [f"reason {i}" for i in range(12)]
    with mock.patch("ecadlib.altium_menu.releasable", lambda it: list(many)):
        r, plan, held = _held_plan_release()
    assert r["status"] == "error" and "review queue" in r["fix"], r


def test_rmn2_release_without_a_key_is_refused():
    for key in (None, "0000"):
        r, plan, held = _held_plan_release(key=key)
        assert r["status"] == "error", (key, r)


def test_rmn2_dialog_shows_all_reasons_and_sends_the_key_back():
    import re
    from ecadlib import altium_forms as AF, altium_menu as AM
    pas = AF.dialog_pas_text()
    ask = re.search(r"(?ms)^Procedure AskRelease\b.*?^End;", pas).group(0)
    start = re.search(r"(?ms)^Procedure StartRelease\b.*?^End;", pas).group(0)
    assert f"For i := 1 To {AM.RELEASE_SHOWN} Do" in ask and "release_more" in ask, ask
    assert "Ask(Job, 'release_key', Answer(Prev, 'release_key'))" in start, start
    assert pas.isascii()


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
