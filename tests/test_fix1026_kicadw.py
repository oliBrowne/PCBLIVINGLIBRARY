"""Fixes for the 2026-10-26 PR #196 review, KiCad writers (R-KW-1..4 in reviews/2026-10-26_review_196/kicadw.md)."""
import json, re, sqlite3, sys, types
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
    ws = types.SimpleNamespace(_plan=lambda b: (None, Path("p/plan.json"), None), root=Path("/nonexistent/ws"),
                               offline=True, rules_in_use=lambda: {})
    with mock.patch.object(KX, "export", fake):
        AM.publish_kicad(ws, "bom.csv", Path("/x/board/ecadlib"), "ecadlib")
    assert Path(got["project"]) == Path("/x/board"), got


def test_rkw2_pcm_rewrites_a_prefixed_model_path():
    from ecadlib import kicad_pcm as PCM
    t = '(footprint "X" (model "${KIPRJMOD}/ecadlib/ecadlib.3dshapes/X.step" (offset (xyz 0 0 0))))'
    out = PCM.footprint_text(t, "ecadlib", "KICAD8_3RD_PARTY", "com_x", 8)
    assert '(model "${KICAD8_3RD_PARTY}/3dmodels/com_x/ecadlib.3dshapes/X.step"' in out, out


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
