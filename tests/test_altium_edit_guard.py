"""Edits made in Altium survive the next ecadlib run (ecadlib.altium_native <file>.ecadstate, dblib-sync, altium-pkg).
Altium's build is simulated by writing the same footprint again with another header date (other bytes, same content
as the recipe); an edit, by writing a changed footprint. Offline. Run:  python3 -m tests.run_all test_altium_edit_guard"""
import copy, json, tempfile
from pathlib import Path

from ecadlib import altium_dblib_sync as S, altium_lib as AL, altium_native as AN, altium_pkg as PKG

ROOT = Path(__file__).resolve().parent.parent
FP = "R_0603_1608Metric"


def _fp():
    spec = json.loads((ROOT / "ecadlib/package_specs" / f"{FP}.json").read_text())
    return AL.footprint_from_spec(spec)[0]


def _src(d):
    (d / "models" / "SchLib").mkdir(parents=True)
    (d / "models" / "PcbLib").mkdir(parents=True)
    AL.write_schlib(d / "models/SchLib/Resistor.SchLib", [AL.resistor_symbol()])
    AL.write_pcblib(d / f"models/PcbLib/{FP}.PcbLib", [_fp()])
    item = {"name": "RES-0603-10k", "folder": "Components/Resistors", "description": "10k", "comment": "=Value",
            "parameters": [{"name": "Value", "value": "10k"}], "cleared": [], "status": "ready",
            "symbol": {"ref": "Resistor", "file": "models/SchLib/Resistor.SchLib"},
            "footprint": {"ref": FP, "file": f"models/PcbLib/{FP}.PcbLib"}, "hold_reasons": []}
    (d / "plan.json").write_text(json.dumps([item]))
    return d / "plan.json"


def _altium_builds(lib, fp=None):
    """What Altium's save does here: the same footprint (or fp) in a file of its own bytes."""
    lib.write_bytes(AL.pcblib_bytes([fp or _fp()], lib.name, ("2001-01-01", "01:02:03")))


def _edited():
    fp = copy.deepcopy(_fp())
    fp["pads"][0]["w"] += 0.2                     # someone widened pad 1 in Altium
    return fp


def test_altium_build_is_recognised_and_not_rebuilt():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        plan, lib = _src(d), d / "lib"
        S.sync(plan, lib)
        dst = lib / "PcbLib" / f"{FP}.PcbLib"
        assert AN.lib_state(dst) == "new"
        assert f"PcbLib/{FP}.PcbLib" in AN.write(lib)["libraries"]
        _altium_builds(dst)
        built = dst.read_bytes()
        rep = AN.write(lib)
        assert f"PcbLib/{FP}.PcbLib" in rep["up_to_date"] and f"PcbLib/{FP}.PcbLib" not in rep["libraries"], rep
        S.sync(plan, lib)                          # ecadlib's part has not changed: Altium's file stays
        assert dst.read_bytes() == built
        assert AN.lib_state(dst) == "built"


def test_edit_in_altium_is_kept_and_reported():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        plan, lib = _src(d), d / "lib"
        S.sync(plan, lib)
        dst = lib / "PcbLib" / f"{FP}.PcbLib"
        _altium_builds(dst)
        AN.write(lib)                              # the next run remembers Altium's build
        _altium_builds(dst, _edited())
        edited = dst.read_bytes()
        rep = AN.write(lib)
        assert rep["edited"] == [f"PcbLib/{FP}.PcbLib"] and f"PcbLib/{FP}.PcbLib" not in rep["libraries"], rep
        assert f"PcbLib\\{FP}.PcbLib" not in (lib / AN.BUILD).read_text(encoding="utf-8-sig")
        fp = _fp()
        fp["description"] = "a newer ecadlib version"
        AL.write_pcblib(d / f"models/PcbLib/{FP}.PcbLib", [fp])
        out = S.sync(plan, lib)                    # a newer ecadlib file does not go over the edit either
        assert dst.read_bytes() == edited
        assert "changed in Altium" in out["results"][0]["detail"], out["results"]
        S.sync(plan, lib, overwrite_edits=True)    # unless asked
        assert dst.read_bytes() == (d / f"models/PcbLib/{FP}.PcbLib").read_bytes()
        assert AN.lib_state(dst) == "new"
        assert f"PcbLib/{FP}.PcbLib" in AN.write(lib, rebuild_all=True)["libraries"]


def test_newer_ecadlib_version_replaces_an_unedited_build():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        plan, lib = _src(d), d / "lib"
        S.sync(plan, lib)
        dst = lib / "PcbLib" / f"{FP}.PcbLib"
        _altium_builds(dst)
        AN.write(lib)
        fp = _fp()
        fp["pads"][0]["h"] += 0.1                  # ecadlib fixed the footprint
        AL.write_pcblib(d / f"models/PcbLib/{FP}.PcbLib", [fp])
        S.sync(plan, lib)
        assert dst.read_bytes() == (d / f"models/PcbLib/{FP}.PcbLib").read_bytes()
        assert f"PcbLib/{FP}.PcbLib" in AN.write(lib)["libraries"]


def test_folder_without_state_builds_as_before():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _src(d)
        lib = d / "models"                          # libraries written in place: no .ecadstate
        _altium_builds(lib / "PcbLib" / f"{FP}.PcbLib", _edited())
        assert AN.lib_state(lib / "PcbLib" / f"{FP}.PcbLib") == "unknown"
        assert f"PcbLib/{FP}.PcbLib" in AN.write(lib)["libraries"]


def test_altium_pkg_from_a_folder_keeps_an_edit():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _src(d)
        out = d / "pkg"
        PKG.collect(d / "models", out)
        dst = out / "PcbLib" / f"{FP}.PcbLib"
        assert AN.lib_state(dst) == "new"
        _altium_builds(dst)
        assert AN.lib_state(dst) == "built"
        _altium_builds(dst, _edited())
        edited = dst.read_bytes()
        fp = _fp()
        fp["description"] = "newer"
        AL.write_pcblib(d / f"models/PcbLib/{FP}.PcbLib", [fp])
        kept = []
        PKG.collect(d / "models", out, kept)
        assert dst.read_bytes() == edited and kept and "changed in Altium" in kept[0], kept
        assert "newer" not in (out / "PcbLib" / f"{FP}.PcbLib.ecadbuild").read_text(encoding="utf-8")


def test_dry_run_records_nothing():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        plan, lib = _src(d), d / "lib"
        S.sync(plan, lib)
        dst = lib / "PcbLib" / f"{FP}.PcbLib"
        _altium_builds(dst)
        st = (dst.with_name(dst.name + AN.STATE_EXT)).read_text(encoding="utf-8")
        AL.write_pcblib(d / f"models/PcbLib/{FP}.PcbLib", [dict(_fp(), description="newer")])
        S.sync(plan, lib, dry_run=True)
        assert (dst.with_name(dst.name + AN.STATE_EXT)).read_text(encoding="utf-8") == st
