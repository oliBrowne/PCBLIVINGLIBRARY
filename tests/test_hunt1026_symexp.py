"""Regression tests for the 2026-10-26 symbol / export bug hunt (symexp-*): symbol_layout, altium_lib, kicad_export,
kicad_import, sexpr. Plain functions (tests.run_all, no pytest)."""
import json, random, warnings
from pathlib import Path

from ecadlib import altium_lib as AL, kicad_export as X, rules as RU, sexpr, symbol_layout as SL
from tests.fixtures_lite import lite


def _pins(sym):
    return {pr[1]["num"]: pr for pr in sym["prims"] if pr[0] == "pin"}


# symexp-01: a JSON pin list (or symbol JSON) with a pin type spelled the way datasheets / KiCad write it crashed
# the writer with KeyError (ETYPE lookup); the CSV reader's aliases now apply to every pin list
@lite
def test_symexp01_json_pin_types_are_normalised(tmp_path, monkeypatch):
    pins = [{"num": "1", "name": "VCC", "type": "Power"}, {"num": "2", "name": "PG", "type": "Open Drain"},
            {"num": "3", "name": "GND", "type": "ground"}, {"num": "4", "name": "DIO", "type": "bidirectional"},
            {"num": "5", "name": "OE", "type": "Input"}, {"num": "6", "name": "Y", "type": "tri-state"}]
    src = tmp_path / "p.json"
    src.write_text(json.dumps(pins), encoding="utf-8")
    out = tmp_path / "p.SchLib"
    monkeypatch.chdir(tmp_path)
    assert SL.main([str(src), "-o", str(out)]) == 0
    _, syms = AL.read_schlib(out)
    et = {r["num"]: r["etype"] for r in syms[0]["records"] if r.get("RECORD") == "2"}
    assert et == {"1": AL.ETYPE["power"], "2": AL.ETYPE["open_collector"], "3": AL.ETYPE["ground"],
                  "4": AL.ETYPE["io"], "5": AL.ETYPE["input"], "6": AL.ETYPE["tristate"]}, et
    for layout in (True, False):                 # altium-lib's symbol JSON: both layouts
        sym = AL.symbol_from_json({"name": "X", "pins": [dict(p, side="left") for p in pins], "layout": layout})
        AL.schlib_bytes([sym])
        assert _pins(sym)["2"][1]["type"] == "open_collector"


def test_symexp01_csv_open_drain_and_tri_state():
    assert SL._ptype("Open Drain") == "open_collector" and SL._ptype("open-drain") == "open_collector"
    assert SL._ptype("OD") == "open_collector" and SL._ptype("tri-state") == "tristate" and SL._ptype("Hi-Z") == "hiz"
    assert SL._ptype("I/O") == "io" and SL._ptype("nonsense") == "passive"


# symexp-02: '_N' on a negative reference / differential leg is no active-low marker: VREF_N is the negative
# reference (ground group, bottom), as the ground rule's own VREF_?N alternative says
def test_symexp02_vref_n_is_ground():
    assert SL.classify({"name": "VREF_N", "type": "passive"}) == "ground"
    assert SL.classify({"name": "VREFN", "type": "passive"}) == "ground"
    assert SL.classify({"name": "VREF_P", "type": "passive"}) == "analog"
    assert SL.classify({"name": "RESET_N", "type": "input"}) == "reset"          # active low still recognised
    assert SL.classify({"name": "CS_N", "type": "input"}) == "spi"
    sym = SL.build("ADC", [{"num": "1", "name": "VREF_P", "type": "power"}, {"num": "2", "name": "VREF_N", "type": "passive"},
                           {"num": "3", "name": "AIN0", "type": "input"}, {"num": "4", "name": "DOUT", "type": "output"}])
    assert _pins(sym)["2"][2] == "bottom" and SL.check(sym) == []


# symexp-03: altium_lib.footprint_from_kicad (app.build_altium_models / the MCP tool convert KiCad library footprints
# with it) dropped every fp_circle / fp_arc (a radial part's courtyard circle, the pin-1 arc), refused a bottom-side
# pad as "non-copper" (thermal-via QFNs) and put an SMD pad with a (drill (offset ..)) at its anchor instead of where
# KiCad draws its copper; they now go through kicad_import's converter, as the librarian's footprints do
GOLDEN = Path(__file__).parent / "fixtures" / "kicad_golden"


@lite
def test_symexp03_kicad_library_footprint_keeps_circles_bottom_pads_offsets(tmp_path, monkeypatch):
    fp = AL.footprint_from_kicad((GOLDEN / "CP_Radial_D5.0mm_P2.00mm.kicad_mod").read_text(encoding="utf-8"))
    crt = [a for a in fp.get("arcs", []) if a[6] == AL.MECH15]
    assert len(crt) == 1 and abs(crt[0][2] - 2.75) < 1e-6, fp.get("arcs")          # courtyard circle r = 3.75 - 1
    AL.write_pcblib(tmp_path / "cp.PcbLib", [fp])
    back = X.footprint_from_pcblib(tmp_path / "cp.PcbLib", "CP", [])
    assert sexpr.courtyard_bbox(sexpr.parse(back)) is not None
    q = AL.footprint_from_kicad((GOLDEN / "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm_ThermalVias.kicad_mod").read_text(encoding="utf-8"))
    assert any(p["designator"] == "25" and p.get("layer") == AL.BOTTOM for p in q["pads"])
    text = ('(footprint "OFF" (layer "F.Cu") (attr smd)\n'
            '(pad "1" smd rect (at 1 2 90) (size 1 0.5) (drill 0 (offset 0.3 0)) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
            '(pad "2" smd rect (at -1 2) (size 1 0.5) (layers "F.Cu" "F.Paste" "F.Mask")))')
    o = {p["designator"]: p for p in AL.footprint_from_kicad(text)["pads"]}
    assert abs(o["1"]["x"] - 1) < 1e-9 and abs(o["1"]["y"] - (-2 + 0.3)) < 1e-9, o["1"]   # copper: (at) + offset
    assert (o["2"]["x"], o["2"]["y"]) == (-1.0, -2.0)                     # turned 90 deg counter-clockwise (y up here)
