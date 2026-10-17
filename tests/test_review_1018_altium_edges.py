"""Regression tests from an edge-case hunt over the code that makes Altium files (2026-10-18): variants on multi-channel
and multi-part components, dblib-sync --prune and sheet names, the menu's build folder and job pruning, STEP files of
footprints whose names share a file name, pin numbers / pad designators cp1252 cannot hold. Run:
    python3 -m tests.run_all test_review_1018_altium_edges"""
import contextlib, io, os, tempfile, time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ecadlib import altium_dblib_sync as S, altium_lib as AL, altium_pkg as P, altium_menu as AM, altium_native as AN, altium_project as AP
from tests.test_altium_project import _channel_board, board
from tests.test_dblib_sync import _base_items, _item, _models, _plan


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


def _lines(p, variant):
    return {g["designator"]: (g["qty"], g["dnp"]) for g in AP.bom_groups(p, variant)}


# ---------------------------------------------------------------- altium_project: variants
def test_variant_on_one_channel_leaves_the_other_channels_fitted():
    """R1 not fitted in channel 2 only: Altium's variation names R1_CH2 (UniqueId = sheet symbol \\ component). The
    other channels' copies share the component's UniqueId but must stay fitted."""
    with tempfile.TemporaryDirectory() as td:
        prj, data = _channel_board(td)
        prj.write_text(prj.read_text() + "[ProjectVariant1]\r\nDescription=NoCh2\r\nVariationCount=1\r\n"
                       "Variation1=Designator=R1_CH2|UniqueId=\\SYMBOLAA\\RCHANAAA|Kind=1|AlternatePart=\r\n\r\n")
        with mock.patch.dict(os.environ, {"ECADLIB_DEVICE_SHEETS": str(data)}):
            p = AP.load_project(prj)
        got = _lines(p, "NoCh2")
        assert got["R1_CH2"] == ("0", True), got
        assert got["R1_CH1, R1_CH3, R2_CH1, R2_CH2, R2_CH3"] == ("5", False), got


def test_variant_names_any_part_of_a_multipart_component():
    """U2 has part A on Power.SchDoc and part B on IO.SchDoc (one physical part): a variation keyed by either part's
    UniqueId applies to it, whichever sheet the project lists first."""
    for uid in ("OPAPARTA", "OPAPARTB"):
        with tempfile.TemporaryDirectory() as td:
            prj = board(td, variants=False)
            txt = prj.read_bytes().decode("utf-8-sig") + ("[ProjectVariant1]\r\nDescription=NoOpamp\r\nVariationCount=1\r\n"
                                                          f"Variation1=Designator=U2|UniqueId=\\{uid}|Kind=1|AlternatePart=\r\n"
                                                          "ParamVariationCount=0\r\n\r\n")
            prj.write_bytes(txt.encode("utf-8"))
            p = _quiet(AP.load_project, prj)
            assert _lines(p, "NoOpamp")["U2"] == ("0", True), (uid, _lines(p, "NoOpamp"))
            assert _lines(p, None)["U2"] == ("1", False)



def test_alternate_part_is_its_design_item_id_whatever_the_key_order():
    """Kind=2 (alternate part): the first AltLibLink_* key matching 'designitemid|libref' won, so with
    AltLibLink_LibReference first the alternate became its symbol's name ('LDO') instead of its Design Item ID."""
    for keys in ("AltLibLink_LibReference=LDO|AltLibLink_DesignItemID=AP2112K-3.3TRG1",
                 "AltLibLink_DesignItemID=AP2112K-3.3TRG1|AltLibLink_LibReference=LDO"):
        with tempfile.TemporaryDirectory() as td:
            prj = board(td, variants=False)
            txt = prj.read_bytes().decode("utf-8-sig") + ("[ProjectVariant1]\r\nDescription=Alt\r\nVariationCount=1\r\n"
                                                          f"Variation1=Designator=U1|UniqueId=\\LDOAAAAA|Kind=2|AlternatePart=|{keys}\r\n\r\n")
            prj.write_bytes(txt.encode("utf-8"))
            p = _quiet(AP.load_project, prj)
            u1 = next(g for g in AP.bom_groups(p, "Alt") if g["designator"] == "U1")
            assert u1["design_item_id"] == "AP2112K-3.3TRG1" and u1["libref"] == "LDO", (keys, u1)
            assert u1["variant_notes"] == "U1: alternate part AP2112K-3.3TRG1", u1

# ---------------------------------------------------------------- dblib-sync
def test_prune_removes_the_recipe_with_the_library():
    """--prune deletes a model file ecadlib wrote; its .ecadbuild recipe must go too, or Altium's rebuild
    (RebuildWithAltium) would overwrite a library someone later puts at that path with the pruned one."""
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); _models(d)
        items = _base_items()
        S.sync(_plan(d, items), d / "lib")
        lib = d / "lib"
        assert (lib / "PcbLib/R_0603_1608Metric.PcbLib.ecadbuild").is_file()
        out = S.sync(_plan(d, [i for i in items if i["name"] != "RES-0603-10k"]), lib, prune=True)
        assert "PcbLib/R_0603_1608Metric.PcbLib" in out["removed"]
        assert not (lib / "PcbLib/R_0603_1608Metric.PcbLib.ecadbuild").exists()
        assert not (lib / "SchLib/Resistor.SchLib.ecadbuild").exists()
        assert (lib / "PcbLib/C_0402_1005Metric.PcbLib.ecadbuild").is_file()      # kept parts keep theirs
        (lib / "PcbLib/R_0603_1608Metric.PcbLib").write_bytes(b"a library someone else made")
        assert "PcbLib/R_0603_1608Metric.PcbLib" not in [r.as_posix() for r, _ in AN.recipes(lib)]


def test_categories_differing_in_case_share_one_sheet():
    """Excel sheet names ignore case: 'Passives' and 'passives' made a workbook sheet 'passives1' while the DbLib named
    table 'passives$' (a table Altium cannot open), and every re-sync moved the row again."""
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); _models(d)
        items = [_item("RES-1", "Passives", "Resistor", "R_0603_1608Metric"), _item("CAP-1", "passives", "Capacitor", "C_0402_1005Metric")]
        S.sync(_plan(d, items), d / "lib")
        tabs = S.read_tables(d / "lib/ecadlib.xlsx")
        assert list(tabs) == ["Passives"] and len(tabs["Passives"][1]) == 2, tabs
        ini = (d / "lib/ecadlib.DbLib").read_text()
        assert [ln for ln in ini.splitlines() if ln.startswith("TableName=")] == ["TableName=Passives$"], ini
        again = S.sync(_plan(d, items), d / "lib")
        assert again["written"] == [] and {r["action"] for r in again["results"]} == {"up_to_date"}, again



def test_dblib_saved_by_altium_keeps_its_encoding():
    """An existing .DbLib was read as UTF-8: one Altium's editor saved in the ANSI code page ('Zoë' in its connection
    string) crashed dblib-sync (and altium-pkg's check), and one with a UTF-8 BOM lost its [OutputDatabaseLinkFile]
    section (the BOM hid the first section header)."""
    for enc, bom in (("cp1252", b""), ("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe")):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _models(d)
            plan = _plan(d, _base_items())
            S.sync(plan, d / "lib")
            db = d / "lib/ecadlib.DbLib"
            txt = db.read_bytes().decode("utf-8").replace("C:\\ecadlib\\library", "C:\\Users\\Zoë\\library")
            db.write_bytes(bom + txt.encode(enc))
            items = _base_items() + [_item("RES-0603-22k", "Resistors", "Resistor", "R_0603_1608Metric", Tolerance="1%")]
            S.sync(_plan(d, items), d / "lib")                  # a new column: the DbLib gets a field map
            data = db.read_bytes()
            assert data.startswith(bom), (enc, data[:8])
            back = data[len(bom):].decode(enc)
            assert back.startswith("[OutputDatabaseLinkFile]\r\nVersion=1.1\r\n[DatabaseLinks]\r\n"), (enc, back[:80])
            assert back.count("[OutputDatabaseLinkFile]") == 1 and "C:\\Users\\Zoë\\library" in back, enc
            assert "FieldNameOnly=Tolerance|" in back, enc
            (d / "lib/x.LibPkg").write_text("[Design]\r\n")       # altium-pkg's check reads the DbLib too
            probs, facts = P.check_folder(d / "lib", "x.LibPkg", ["ecadlib.DbLib"])
            assert probs == [] and facts["dblib_rows"] == 3, (enc, probs, facts)

# ---------------------------------------------------------------- altium-menu
def test_menu_build_where_the_folder_cannot_be_a_menu_entry():
    """On Windows the menu file's folder defaults to --out. A folder a menu entry cannot name (C:\\Users\\O'Brien\\...)
    must only leave the optional .DXPPrf out, not stop the build; an explicit bad --win-dir is still an error."""
    nt = SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")})
    nt.name = "nt"
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "O'Brien" / "menu"
        with mock.patch.object(AM, "os", nt):
            rep = AM.build(out)
        assert rep["menu_file"] is None and not (out / AM.PREFS).exists() and (out / AM.UNIT).is_file(), rep
        try:
            AM.build(Path(t) / "x", win_dir="C:\\Users\\O'Brien\\menu")
            raise AssertionError("explicit --win-dir with ' accepted")
        except ValueError as e:
            assert "--win-dir" in str(e)


def test_run_job_outside_a_jobs_folder_prunes_nothing():
    """`run-job FOLDER` pruned FOLDER's siblings that hold an old exit.txt: run on Documents\\myjob it deleted
    Documents\\<anything with an exit.txt>. Only the script's own jobs\\ folder is pruned."""
    from ecadlib import kicad_menu as KM
    with tempfile.TemporaryDirectory() as t:
        docs = Path(t) / "Documents"
        keep = docs / "project_backup"
        keep.mkdir(parents=True)
        (keep / "exit.txt").write_text("0")
        (keep / "board.PcbDoc").write_text("precious")
        old = time.time() - 30 * 86400
        os.utime(keep / "exit.txt", (old, old))
        job = docs / "myjob"
        job.mkdir()
        (job / "request.ini").write_text("[request]\naction=nonsense\n")
        assert AM.run_job(job)["status"] == "error"
        assert (keep / "board.PcbDoc").is_file()
        KM.run_job(docs / "kjob", "nonsense", str(Path(t) / "b.kicad_pcb"), offline=True)
        assert (keep / "board.PcbDoc").is_file()
        jobs = Path(t) / "menu" / "jobs"                   # the menu's own jobs folder is still pruned
        (jobs / "old").mkdir(parents=True)
        (jobs / "old" / "exit.txt").write_text("0")
        os.utime(jobs / "old" / "exit.txt", (old, old))
        (jobs / "new").mkdir()
        (jobs / "new" / "request.ini").write_text("[request]\naction=nonsense\n")
        AM.run_job(jobs / "new")
        assert not (jobs / "old").exists() and (jobs / "new" / "result.ini").is_file()


# ---------------------------------------------------------------- altium_native: STEP files beside the recipe
def _fp(name, step):
    return {"name": name, "description": "", "height_mm": 1.0, "tracks": [], "fills": [], "regions": [], "texts": [],
            "pads": [{"designator": "1", "x": 0, "y": 0, "w": 1.0, "h": 0.5, "shape": "rect"}],
            "bodies": [{"pts": [(-1, -1), (1, -1), (1, 1), (-1, 1)], "height": 1.0,
                        "step": {"name": "m.step", "data": step, "rot": (0, 0, 0)}}]}


def test_step_files_of_footprints_whose_names_share_a_file_stem():
    """'A/B' and 'A_B' both have file stem 'A_B': their STEP models went to the same ecadlib_3d/A_B_0/m.step, so
    Altium's rebuild embedded the second model in both footprints."""
    with tempfile.TemporaryDirectory() as t:
        lib = Path(t) / "x.PcbLib"
        AL.write_pcblib(lib, [_fp("A/B", b"ISO-10303-21; model A"), _fp("A_B", b"ISO-10303-21; model B")])
        rec = Path(t) / "x.PcbLib.ecadbuild"
        files = [ln.split("\t")[4] for ln in rec.read_text(encoding="utf-8").splitlines() if ln.startswith("BODY")]
        assert len(set(files)) == 2, files
        assert [(Path(t) / f).read_bytes() for f in files] == [b"ISO-10303-21; model A", b"ISO-10303-21; model B"]
        assert files[1] == "ecadlib_3d/A_B_0/m.step"                                  # plain names: path unchanged


# ---------------------------------------------------------------- altium_lib: cp1252-only binary strings
def test_pin_numbers_and_pad_designators_cp1252_cannot_hold_are_errors():
    """A pin number 'α' was written as '?': two such pins became the same pin and the pad mapping broke silently."""
    pins = [{"num": "α", "name": "A", "type": "io", "side": "left"}, {"num": "β", "name": "B", "type": "io", "side": "left"}]
    for make in (lambda: AL.schlib_bytes([AL.ic_symbol("U", pins, layout=False)]),
                 lambda: AL.pcblib_bytes([dict(_fp("P", b"x"), bodies=[], pads=[dict(_fp("P", b"x")["pads"][0], designator="α")])])):
        try:
            make()
            raise AssertionError("written")
        except ValueError as e:
            assert "'α'" in str(e) and "rename" in str(e), e
    ok = [{"num": "1", "name": "µA ±", "type": "io", "side": "left"}, {"num": "2", "name": "N" * 300, "type": "io", "side": "left"}]
    with tempfile.TemporaryDirectory() as t:                 # cp1252 text is fine; an over-long name is cut, not a crash
        _, (sym,) = AL.read_schlib(AL.write_schlib(Path(t) / "u.SchLib", [AL.ic_symbol("U", ok, layout=False)]))
        got = {r["num"]: r["name"] for r in sym["records"] if r["RECORD"] == "2"}
        assert got == {"1": "µA ±", "2": "N" * 255}, got


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_review_1018_altium_edges"]))
