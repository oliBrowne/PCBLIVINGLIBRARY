"""Fixes for the 2026-10-26 PR #196 review, KiCad writers (R-KW-1..4 in reviews/2026-10-26_review_196/kicadw.md)."""
import json, re, sqlite3, sys, tempfile, types
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecadlib import altium_lib as AL, httpcache, kicad_export as X, kicad_import as K, sexpr
from tests.fixtures_lite import lite

FIX = ROOT / "tests" / "fixtures" / "kicad"


def _models(folder, lib="ecadlib"):
    return {f.name: re.findall(r'\(model "([^"]+)"', f.read_text()) for f in sorted(Path(folder, f"{lib}.pretty").glob("*.kicad_mod"))}


# ---- R-KW-1: a ready part whose footprint is only a .PcbLib (no .kicad_mod) is exported (fp_source's fallback)
@lite
def test_rkw1_pcblib_only_footprint_is_exported(tmp_path):
    from tests import test_kicad_export as T
    from ecadlib import kicad_dblib as KD
    plan, items, http = T._plan(tmp_path)
    km = plan.parent / "models" / "KiCad" / "R_0603_1608Metric.kicad_mod"
    km.rename(tmp_path / "aside.kicad_mod")                 # an Altium-only footprint: no KiCad file at all
    its = json.loads(plan.read_text())
    res = next(it for it in its if it["name"] == "RES-0603-10k")
    res["footprint"].pop("kicad_mod", None)
    assert res.get("status", "ready") == "ready" and res["footprint"]["file"].endswith(".PcbLib")
    plan.write_text(json.dumps(its, indent=2))
    out = tmp_path / "kx"
    rep = KD.build(plan, out, "lib", export=True, http=http, log=lambda *a, **k: None)
    xr = json.loads((out / "kicad_export.json").read_text())
    assert xr["errors"] == [] and not xr.get("held_changes"), xr["errors"]          # kicad-export exits 0
    row = next(r for r in xr["parts"] if r["part"] == "RES-0603-10k")
    assert row["footprint"] == "R_0603_1608Metric", row
    assert "converted back" in xr["footprints"]["R_0603_1608Metric"]["from"]
    assert (out / "lib.pretty" / "R_0603_1608Metric.kicad_mod").is_file()
    with closing(sqlite3.connect(out / "lib.sqlite")) as con:
        got = [r for t, in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
               for r in con.execute(f'SELECT "Part ID", "Footprint" FROM "{t}"') if r[0] == "RES-0603-10k"]
    assert got == [("RES-0603-10k", "lib:R_0603_1608Metric")], got
    assert not any("RES-0603-10k" in w and "footprint" in w for w in rep["warnings"]), rep["warnings"]


# ---- R-KW-2: the 3D model paths point where the library is installed (KiCad menu: <project>/ecadlib/)
@lite
def test_rkw2_menu_library_models_resolve_from_the_project(tmp_path):
    from tests import test_kicad_export as T
    from ecadlib import kicad_menu as KM, parity as PA
    plan, items, http = T._plan(tmp_path)
    proj = tmp_path / "board"
    proj.mkdir()
    (proj / "board.kicad_pcb").write_text("(kicad_pcb)")
    cfg = KM.config(proj / "board.kicad_pcb")
    X.export(plan, cfg["library"], cfg["name"], http=http, log=lambda *a: None, project=proj)
    ms = _models(cfg["library"])
    paths = [m for v in ms.values() for m in v]
    assert paths, ms
    for m in paths:
        assert m.startswith("${KIPRJMOD}/ecadlib/ecadlib.3dshapes/"), m
        assert Path(m.replace("${KIPRJMOD}", str(proj))).is_file(), m            # KiCad: KIPRJMOD = the project folder
    assert all(PA.KiCadLib(cfg["library"], "ecadlib").model_ok(m) for m in paths)
    # a later export into the same folder with no project given (kicad-export / kicad-dblib --export) keeps the place
    X.export(plan, cfg["library"], cfg["name"], http=http, log=lambda *a: None)
    assert _models(cfg["library"]) == ms


@lite
def test_rkw2_plain_export_keeps_kiprjmod_lib_3dshapes(tmp_path):
    from tests import test_kicad_export as T
    plan, items, http = T._plan(tmp_path)
    out = tmp_path / "plain"
    X.export(plan, out, "ecadlib", http=http, log=lambda *a: None)
    paths = [m for v in _models(out).values() for m in v]
    assert paths and all(m.startswith("${KIPRJMOD}/ecadlib.3dshapes/") for m in paths), paths
    out2 = tmp_path / "proj"                                  # the project folder itself: the same paths
    X.export(plan, out2, "ecadlib", http=http, log=lambda *a: None, project=out2)
    assert [m for v in _models(out2).values() for m in v] == paths


def test_rkw2_publish_kicad_passes_the_project_folder():
    from ecadlib import altium_menu as AM, kicad_export as KX
    got = {}

    def fake(plan, library, name, **kw):
        got.update(kw, library=library)
        return {"errors": [], "skipped": [], "parts": []}
    ws = types.SimpleNamespace(_plan=lambda b: (None, Path("p/plan.json"), None), root=Path(tempfile.mkdtemp()) / "ws",
                               offline=True, rules_in_use=lambda: {})
    with mock.patch.object(KX, "export", fake):
        AM.publish_kicad(ws, "bom.csv", Path("/x/board/ecadlib"), "ecadlib")
    assert Path(got["project"]) == Path("/x/board"), got


def test_rkw2_pcm_rewrites_a_prefixed_model_path():
    from ecadlib import kicad_pcm as PCM
    t = '(footprint "X" (model "${KIPRJMOD}/ecadlib/ecadlib.3dshapes/X.step" (offset (xyz 0 0 0))))'
    out = PCM.footprint_text(t, "ecadlib", "KICAD8_3RD_PARTY", "com_x", 8)
    assert '(model "${KICAD8_3RD_PARTY}/3dmodels/com_x/ecadlib.3dshapes/X.step"' in out, out


# ---- R-KW-3: a KiCad-sourced symbol read back from its .SchLib keeps power_out / no_connect
def _ams1117_read_back(d, kicad_types):
    chain, _ = K.load_symbol(f"{FIX / 'Regulator_Linear_AMS1117.kicad_sym'}:AMS1117-3.3", None, keep_quotes=True)
    (d / "models" / "SchLib").mkdir(parents=True)
    AL.write_schlib(d / "models" / "SchLib" / "AMS1117.SchLib", [K.convert_symbol(chain, "AMS1117-3.3")])
    item = {"name": "AMS1117-3.3", "source": {"symbol": "kicad:Regulator_Linear:AMS1117-3.3"},
            "symbol": {"file": "models/SchLib/AMS1117.SchLib", "ref": "AMS1117-3.3"}, "footprint": {}}
    if kicad_types is not None:
        item["symbol"]["kicad_types"] = kicad_types
    sym, how = X.load_symbol(item, d, httpcache.CachedHTTP(d / "cache", offline=True), [], "AMS1117-3.3", [])
    assert "read back" in how, how
    return {str(sexpr.first(p, "number")[1]): p[1] for u in sexpr.find(sym, "symbol") for p in sexpr.find(u, "pin")}


@lite
def test_rkw3_librarian_records_kicad_types_of_a_kicad_symbol(tmp_path):
    from ecadlib import librarian as L
    chain, _ = K.load_symbol(f"{FIX / 'Regulator_Linear_AMS1117.kicad_sym'}:AMS1117-3.3", None)
    r = L.Run(tmp_path, None, None, [], [])
    st = {"name": "AMS1117-3.3", "symbol": None, "footprint": None}
    r.put_kicad_sym(st, chain, "AMS1117-3.3", None, "kicad:Regulator_Linear:AMS1117-3.3")
    assert st["symbol"].get("kicad_types") == {"2": "power_out"}, st["symbol"]


@lite
def test_rkw3_kicad_symbol_read_back_restores_recorded_types(tmp_path):
    got = _ams1117_read_back(tmp_path, {"2": "power_out"})
    assert got["2"] == "power_out", got
    assert got["1"] == "power_in", got                      # GND keeps its type


@lite
def test_rkw3_kicad_symbol_read_back_is_not_guessed_by_name(tmp_path):
    got = _ams1117_read_back(tmp_path, None)               # a curated KiCad symbol: no name guessing without a record
    assert got["2"] == "power_in", got


@lite
def test_rkw3_plan_without_record_uses_the_kicad_publish_baseline(tmp_path):
    base = {"1": {"name": "GND", "ktype": "power_in", "alt": []}, "2": {"name": "VO", "ktype": "power_out", "alt": []},
            "3": {"name": "VI", "ktype": "power_in", "alt": []}}
    with mock.patch.object(X, "kicad_baseline", lambda item, plan_dir: base):   # a plan from before the record
        got = _ams1117_read_back(tmp_path, None)
    assert got == {"1": "power_in", "2": "power_out", "3": "power_in"}, got


def test_rkw3_chain_types_follow_the_pin_map():
    from ecadlib import librarian as L
    chain, _ = K.load_symbol(f"{FIX / 'Regulator_Linear_AMS1117.kicad_sym'}:AMS1117-3.3", None)
    assert L.kicad_chain_types(chain) == {"2": "power_out"}
    assert L.kicad_chain_types(chain, {"2": "4"}) == {"4": "power_out"}


# ---- R-KW-4: altium_model honours the body's own placement (MODEL.2D.X / Y, MODEL.3D.ROTZ)
def _placed_lib(d, repl):
    from ecadlib import step_gen as SG, stdparts as SP
    text, _ = SP.build("SOIC-8_3.9x4.9mm_P1.27mm")
    fp, _ = K.convert_footprint(text, "SOIC8")
    step, _ = SG.generate("SOIC-8_3.9x4.9mm_P1.27mm")
    fp["bodies"] = [{"pts": [(-2, -2.5), (2, -2.5), (2, 2.5), (-2, 2.5)], "height": 1.75,
                     "step": {"name": "soic8.step", "data": step.encode(), "rot": (0.0, 0.0, 0.0)}}]
    orig = AL.body_params

    def placed(*a, **k):
        s = orig(*a, **k)
        for old, new in repl:
            s = s.replace(old, new)
        return s
    with mock.patch.object(AL, "body_params", placed):
        return AL.write_pcblib(d / "a.PcbLib", [fp])


def _node(n, key):
    return [float(v) for v in sexpr.first(sexpr.first(n, key), "xyz")[1:]]


@lite
def test_rkw4_body_offset_and_rotation_are_honoured(tmp_path):
    lib = _placed_lib(tmp_path, [("MODEL.2D.X=0mil", "MODEL.2D.X=50mil"), ("MODEL.2D.Y=0mil", "MODEL.2D.Y=100mil"),
                                 ("MODEL.3D.ROTZ=0.000", "MODEL.3D.ROTZ=90.000")])
    notes = []
    got = X.altium_model(lib, "SOIC8", "lib", tmp_path / "lib.3dshapes", {}, notes)
    assert got, notes
    n = got[0][0]
    off, rot = _node(n, "offset"), _node(n, "rotate")
    # R-FX5-2: KiCad's Altium importer gives offset y = +MODEL.2D.Y (the model offset is in the y-up model frame)
    assert abs(off[0] - 1.27) < 1e-6 and abs(off[1] - 2.54) < 1e-6 and abs(off[2]) < 1e-6, off
    assert rot == [0.0, 0.0, -90.0], rot          # KiCad's importer: rotation z = -MODEL.3D.ROTZ


@lite
def test_rkw4_unplaced_body_unchanged(tmp_path):
    lib = _placed_lib(tmp_path, [])
    got = X.altium_model(lib, "SOIC8", "lib", tmp_path / "lib.3dshapes", {}, [])
    n = got[0][0]
    assert _node(n, "offset") == [0.0, 0.0, 0.0] and _node(n, "rotate") == [0.0, 0.0, 0.0], n


@lite
def test_rkw4_body_tilted_or_2d_rotated_is_not_written(tmp_path):
    for repl in ([("MODEL.3D.ROTX=0.000", "MODEL.3D.ROTX=90.000")], [("MODEL.2D.ROTATION=0.000", "MODEL.2D.ROTATION=45.000")]):
        d = tmp_path / str(len(list(tmp_path.iterdir())))
        d.mkdir()
        notes = []
        got = X.altium_model(_placed_lib(d, repl), "SOIC8", "lib", d / "lib.3dshapes", {}, notes)
        assert got is None and any("rotated in Altium" in n for n in notes), (repl, got, notes)



# ---- R-FX5-2: a body projected to the bottom side (BODYPROJECTION=1) is not written (KiCad would show it on top)
def _extruded_lib(d, repl):
    from ecadlib import stdparts as SP
    text, _ = SP.build("SOIC-8_3.9x4.9mm_P1.27mm")
    fp, _ = K.convert_footprint(text, "SOIC8")
    fp["bodies"] = [{"pts": [(-2, -2.5), (2, -2.5), (2, 2.5), (-2, 2.5)], "height": 1.75}]
    orig = AL.body_params

    def placed(*a, **k):
        s = orig(*a, **k)
        for o, n in repl:
            s = s.replace(o, n)
        return s
    with mock.patch.object(AL, "body_params", placed):
        return AL.write_pcblib(d / "e.PcbLib", [fp])


@lite
def test_rfx52_bottom_projected_body_is_not_written(tmp_path):
    for k, make in enumerate((_placed_lib, _extruded_lib)):
        d = tmp_path / str(k)
        d.mkdir()
        assert X.altium_model(make(d, []), "SOIC8", "lib", d / "s", {}, []), make     # top side: written
        notes = []
        got = X.altium_model(make(d, [("BODYPROJECTION=0", "BODYPROJECTION=1")]), "SOIC8", "lib", d / "s2", {}, notes)
        assert got is None and any("bottom" in n for n in notes), (make, got, notes)


# ---- R-FX5-8: parity's model_ok accepts the plain ${KIPRJMOD}/<lib>.3dshapes path or the export's own models_at, only
@lite
def test_rfx58_model_ok_only_the_real_prefix(tmp_path):
    from ecadlib import parity as PA
    (tmp_path / "ecadlib.kicad_sym").write_text("(kicad_symbol_lib (version 20231120))\n")
    (tmp_path / "ecadlib.3dshapes").mkdir()
    (tmp_path / "ecadlib.3dshapes" / "X.step").write_text("x")
    plain, sub, wrong = ("${KIPRJMOD}/ecadlib.3dshapes/X.step", "${KIPRJMOD}/ecadlib/ecadlib.3dshapes/X.step",
                         "${KIPRJMOD}/other/ecadlib.3dshapes/X.step")
    lib = PA.KiCadLib(tmp_path, "ecadlib")                      # no record: KIPRJMOD is the export folder
    assert lib.model_ok(plain) and not lib.model_ok(sub) and not lib.model_ok(wrong)
    (tmp_path / "kicad_export.json").write_text(json.dumps(
        {"library": "ecadlib", "libraries": {"ecadlib": {"models_at": "${KIPRJMOD}/ecadlib/ecadlib.3dshapes"}}}))
    lib = PA.KiCadLib(tmp_path, "ecadlib")                      # exported with --project: KIPRJMOD is the project
    assert lib.model_ok(sub) and not lib.model_ok(wrong) and not lib.model_ok(plain)
    assert not lib.model_ok("${KIPRJMOD}/ecadlib/ecadlib.3dshapes/Y.step")


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
