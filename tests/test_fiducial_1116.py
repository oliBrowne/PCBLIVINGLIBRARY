"""Fiducials and unplated mounting holes (1000-part BOM run, 2026-11-16: 47 such lines were held). KiCad's
Mechanical:Fiducial and Mechanical:MountingHole have no pins, so ecadlib built no symbol, and the release check held a
part with no pin and no pad numbered 1. They now get a pinless symbol and release with nothing to buy. A land with a
numbered pad still needs its pin, and a line that is not a fiducial's or a hole's keeps its hold."""
import json
import tempfile
from pathlib import Path

from ecadlib import kicad_export as X, librarian as L, stdparts as SP
from tests import test_librarian as TL

FIX = Path(__file__).parent / "fixtures" / "kicad_mech"     # KiCad's own files (kicad-footprints, 2026-02)
MECH = {"Fiducial:Fiducial_1mm_Mask2mm": (FIX / "Fiducial_1mm_Mask2mm.kicad_mod").read_text(encoding="utf-8"),
        "MountingHole:MountingHole_3.2mm_M3": (FIX / "MountingHole_3.2mm_M3.kicad_mod").read_text(encoding="utf-8")}
NUMBERED = MECH["Fiducial:Fiducial_1mm_Mask2mm"].replace('(pad ""', '(pad "1"')    # a fiducial land with a pin's pad


def http(foots):
    """The fake KiCad library with these footprints added."""
    h = TL.FakeHTTP()
    tree = [{"type": "tree", "path": f"{r.split(':')[0]}.pretty"} for r in {**TL.FOOTS, **foots}]
    tree += [{"type": "blob", "path": f"{r.split(':')[0]}.pretty/{r.split(':')[1]}.kicad_mod"} for r in {**TL.FOOTS, **foots}]
    h.routes[L.TREE.format(repo="kicad-footprints", page=1)] = json.dumps(tree)
    for r, text in foots.items():
        lib, name = r.split(":")
        h.routes[L.K.GITLAB.format(repo="kicad-footprints", path=f"{lib}.pretty/{name}.kicad_mod")] = text
    return h


BOM = ("Designator,Qty,Value,Footprint,MPN\n"
       '"FID1,FID2,FID3",3,Fiducial,Fiducial:Fiducial_1mm_Mask2mm,\n'
       '"H1,H2",2,MountingHole,MountingHole:MountingHole_3.2mm_M3,\n')


def test_pinless_symbols_have_no_pins_and_stay_out_of_the_bom():
    for key in SP.PINLESS:
        text, name = SP.symbol_text(key)
        assert name == key and "(pin " not in text and "(in_bom no)" in text, key


def test_a_fiducial_and_an_unplated_hole_release_with_nothing_to_buy():
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, http(MECH), bom=BOM, sources=["kicad", "standard"])
        fid = next(i for i in items.values() if i["source"]["footprint"].endswith("Fiducial_1mm_Mask2mm"))
        hole = next(i for i in items.values() if i["source"]["footprint"].endswith("MountingHole_3.2mm_M3"))
        for it, sym, desc in ((fid, "standard:Fiducial", "Fiducial marker (Fiducial_1mm_Mask2mm)"),
                              (hole, "standard:MountingHole", "Mounting hole, not connected (MountingHole_3.2mm_M3)")):
            assert it["status"] == "ready", (it["name"], it["hold_reasons"])
            assert it["source"]["symbol"] == sym and it["no_bom_part"], it["source"]
            assert it["description"] == desc, it["description"]      # the land's own case kept (was lowercased)
        out = Path(d) / "kx"                                          # the KiCad export takes the pinless symbols
        X.export(Path(d) / "out" / "plan.json", out, "ecadlib", log=lambda *a, **k: None)
        sym = (out / "ecadlib.kicad_sym").read_text(encoding="utf-8")
        assert '(symbol "Fiducial"' in sym and '(symbol "MountingHole"' in sym, sym[:400]


def test_a_numbered_pad_or_another_designator_keeps_the_hold():
    bom = ("Designator,Qty,Value,Footprint,MPN\n"
           "FID1,1,Fiducial,Fiducial:Fiducial_1mm_Mask2mm,\n"
           "U9,1,Fiducial,MountingHole:MountingHole_3.2mm_M3,\n")
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, http({**MECH, "Fiducial:Fiducial_1mm_Mask2mm": NUMBERED}), bom=bom, sources=["kicad", "standard"])
        assert all(i["status"] == "held" for i in items.values()), [(i["name"], i["status"]) for i in items.values()]
        assert not any(i["source"]["symbol"] for i in items.values()), [i["source"] for i in items.values()]
