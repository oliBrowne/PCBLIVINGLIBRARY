"""Regression tests for the bug-hunt findings on the Altium writers (dblib-sync, altium_lib, altium_native, parity).
Run:  python3 -m tests.test_bughunt_expa"""
import json, tempfile
from pathlib import Path

from ecadlib import altium_dblib_sync as S, altium_lib as AL, altium_native as AN
from tests.fixtures_lite import lite
from tests.test_dblib_sync import _actions, _base_items, _item, _models, _plan

ROOT = Path(__file__).parent.parent


def _spec_fp(name="R_0603_1608Metric"):
    return AL.footprint_from_spec(json.loads((ROOT / "ecadlib/package_specs" / f"{name}.json").read_text()))[0]


# ---------------------------------------------------------------- EXP-12: recipe BODY paths stay inside the library
@lite
def test_exp12_recipe_body_path_cannot_leave_the_library_folder(tmp_path):
    src = tmp_path / "src" / "a" / "PcbLib"
    src.mkdir(parents=True)
    AL.write_pcblib(src / "R.PcbLib", [_spec_fp()])
    (tmp_path / "src" / "secret.txt").write_text("key")
    rp = src / "R.PcbLib.ecadbuild"
    evil = "BODY\t69\t100\t0\t{}\t0\t0\t0\t0\t4\t0\t0\t1\t0\t1\t1\t0\t1"
    rp.write_text(rp.read_text().replace("\nEND", "\n" + evil.format("../../secret.txt") + "\n"
                                         + evil.format("ecadlib_3d/../../../x.bat") + "\nEND"))
    dst = tmp_path / "share" / "lib" / "PcbLib"
    dst.mkdir(parents=True)
    (dst / "R.PcbLib").write_bytes((src / "R.PcbLib").read_bytes())
    AN.copy_recipe(src / "R.PcbLib", dst / "R.PcbLib")
    files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    assert not any(f.endswith(("secret.txt", "x.bat")) and not f.startswith("src") for f in files), files
    assert "secret" not in (dst / "R.PcbLib.ecadbuild").read_text()
    assert AN.recipe_steps(src / "R.PcbLib") == []                     # never names a file outside ecadlib_3d/
    bt = AN.build_text(tmp_path / "src" / "a")
    assert "secret" not in bt and "x.bat" not in bt, bt
    assert AN.safe_step("ecadlib_3d/R_0/R.step") and not AN.safe_step("ecadlib_3d/../R.step")
    assert not AN.safe_step("C:/x.step") and not AN.safe_step("/etc/passwd") and not AN.safe_step("ecadlib_3d\\..\\x")


# ---------------------------------------------------------------- EXP-13: unnumbered pads (NPTH holes) in compare
@lite
def test_exp13_compare_handles_unnumbered_pads(tmp_path):
    fp = _spec_fp()
    for x in (-2.0, 2.0):
        fp["pads"].append(dict(fp["pads"][0], designator="", x=x, y=1.0, w=1.0, h=1.0, shape="circle", layer=AL.MULTI,
                               hole=1.0, plated=False))
    lib = tmp_path / "USB.PcbLib"
    AL.write_pcblib(lib, [fp])
    assert AN.compare_one(lib, lib.with_name(lib.name + AN.EXT)) == []
    assert AN.compare(tmp_path)["checked"] == 1


# ---------------------------------------------------------------- EXP-75: an Altium edit of pad rotation / mask shows
@lite
def test_exp75_pad_rotation_and_mask_edit_is_an_edit(tmp_path):
    lib = tmp_path / "R.PcbLib"
    fp = _spec_fp()
    AL.write_pcblib(lib, [fp])
    rp = lib.with_name(lib.name + AN.EXT)
    AN.record(lib, AN._sha(lib.read_bytes()))
    built = tmp_path / "built.PcbLib"                                    # a rebuild: other bytes, same pads
    built.write_bytes(AL.pcblib_bytes([fp], lib.name, ("02/02/2030", "01:01:01")))
    assert AN.compare_one(built, rp) == []
    edited = [dict(fp, pads=[dict(p, rot=90.0, mask="none") for p in fp["pads"]])]
    lib.write_bytes(AL.pcblib_bytes(edited, lib.name, ("02/02/2030", "01:01:01")))
    rp.write_text(rp.read_text())                                         # the recipe is still ecadlib's
    probs = AN.compare_one(lib, rp)
    assert probs and "rot" in probs[0] and "mask" in probs[0], probs
    assert AN.lib_state(lib) == "edited"
    assert AN.replace_action(b"new bytes", lib) == "edited"


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main([Path(__file__).stem]))
