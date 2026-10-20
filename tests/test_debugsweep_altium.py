"""Regression tests for the debug-sweep findings on the Altium side (altium_native, dblib-sync, altium-menu, altium-pkg).
Run:  python3 -m tests.run_all test_debugsweep_altium"""
from ecadlib import altium_lib as AL, altium_native as AN, kicad_import as K
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- DS2-34: a pad's drill offset survives the rebuild
OFFSET_PADS = '''(footprint "OffsetPad" (layer "F.Cu")
  (pad "1" thru_hole rect (at 0 0) (size 3 1.6) (drill 1 (offset 0.7 0)) (layers "*.Cu" "*.Mask"))
  (pad "2" thru_hole oval (at 0 2.54) (size 3 1.6) (drill 1 (offset 0.7 0)) (layers "*.Cu" "*.Mask"))
)'''


@lite
def test_ds2_34_compare_reports_a_rebuild_that_dropped_the_drill_offset(tmp_path):
    fp, _ = K.convert_footprint(OFFSET_PADS, notes=[])
    lib = tmp_path / "OffsetPad.PcbLib"
    AL.write_pcblib(lib, [fp])
    rp = lib.with_name(lib.name + AN.EXT)
    AN.record(lib, AN._sha(lib.read_bytes()))
    kept = tmp_path / "kept.PcbLib"                     # a rebuild that kept the 0.7 mm copper offset: a match
    kept.write_bytes(AL.pcblib_bytes([fp], lib.name, ("02/02/2031", "01:01:01")))
    assert AN.compare_one(kept, rp) == []
    centred = [dict(fp, pads=[dict(p, offset=None) for p in fp["pads"]])]    # copper centred on the hole
    lib.write_bytes(AL.pcblib_bytes(centred, lib.name, ("02/02/2031", "01:01:01")))
    probs = AN.compare_one(lib, rp)
    assert len(probs) == 2 and all("offset (0, 0) != (275591, 0)" in p for p in probs), probs
    assert AN.lib_state(lib, save=False) == "edited"
