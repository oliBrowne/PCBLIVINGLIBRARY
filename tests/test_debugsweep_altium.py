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


# ---------------------------------------------------------------- DS2-35: a hand-added workbook row with a bad ref
@lite
def test_ds2_35_hand_row_with_a_ref_altium_cannot_store_is_reported_not_fatal(tmp_path):
    import openpyxl
    from ecadlib import altium_dblib_sync as S
    from tests.test_dblib_sync import _base_items, _models, _plan
    _models(tmp_path)
    plan = _plan(tmp_path, _base_items())
    S.sync(plan, tmp_path / "lib")
    x = tmp_path / "lib/ecadlib.xlsx"
    wb = openpyxl.load_workbook(x)
    ws = wb["Resistors"]
    hdr = [c.value for c in ws[1]]
    row = dict.fromkeys(hdr, "")
    row.update({"Part Number": "HAND-R1", "Library Ref": "Res", "Footprint Ref": "R_0603→"})
    ws.append([row[h] for h in hdr])
    wb.save(x)
    out = S.sync(plan, tmp_path / "lib")
    acts = {r["name"]: r["action"] for r in out["results"]}
    assert acts["RES-0603-10k"] == "up_to_date" and acts["CAP-0402-100nF"] == "up_to_date", out["results"]
    bad = [r for r in out["results"] if r["name"] == "HAND-R1" and r["action"] == "error"]
    assert len(bad) == 1 and "Footprint Ref 'R_0603→'" in bad[0]["detail"] and bad[0]["table"] == "Resistors", out["results"]
    kept = {r["Part Number"]: r for r in S.read_tables(x)["Resistors"][1]}
    assert kept["HAND-R1"]["Footprint Ref"] == "R_0603→", kept
