"""Build the assembly test boards with KiCad itself (its pcbnew Python module, KiCad 7+), so the boards, the flips and
the angles are KiCad's own, not ecadlib's idea of them. Not run by the tests; run it again to rebuild the fixtures:

    /usr/bin/python3 tests/fixtures/assembly/make_boards.py        (the python that has KiCad's pcbnew)

For each board it writes, in boards/:
  <name>.kicad_pcb        the board, saved by pcbnew
  <name>.pos.csv          KiCad's own position file (kicad-cli pcb export pos, both sides, mm, drill/place origin)
  <name>.truth.json       every pad's centre on the board as pcbnew reports it (mm, KiCad's +y-down page frame),
                          the board's aux origin, and each part's side / angle: the ground truth the round-trip tests
                          place the assembler's footprint against
The footprints are KiCad's own library (kicad7.pretty: KiCad 7.0.11 kicad-footprints, CC-BY-SA 4.0 with the KiCad
library exception)."""
import json, subprocess, sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
LIB = str(HERE / "kicad7.pretty")
OUT = HERE / "boards"
MM = pcbnew.FromMM

# ref, footprint, value, x, y, angle, side, fields, pin functions {pad: name}, attributes
BOARDS = {
    "two_sided": {
        "aux": (100.0, 100.0), "use_aux": True,
        "parts": [
            ("R1", "R_0603_1608Metric", "10k", 110, 110, 0, "top", {"LCSC": "C25804"}, None, ()),
            ("R2", "R_0603_1608Metric", "10k", 115, 110, 90, "top", {"LCSC": "C25804"}, None, ()),
            ("R3", "R_0402_1005Metric", "10k", 120, 110, 45, "top", {"LCSC": "C25744"}, None, ()),
            ("R4", "R_0603_1608Metric", "10k", 125, 110, 0, "top", {"LCSC": "C25804"}, None, ("exclude_from_pos",)),
            ("R5", "R_0805_2012Metric", "4k7", 130, 110, 0, "top", {}, None, ()),
            ("C1", "C_0805_2012Metric", "10u", 110, 120, 0, "bottom", {"LCSC Part #": "C15850"}, None, ()),
            ("C2", "C_0805_2012Metric", "10u", 115, 120, 270, "bottom", {"LCSC Part #": "C15850"}, None, ()),
            ("C3", "C_0402_1005Metric", "100n", 120, 120, 30, "bottom", {"LCSC": "C1525"}, None, ()),
            ("D1", "LED_0603_1608Metric", "RED", 110, 130, 180, "top", {"LCSC": "C2286"}, {"1": "K", "2": "A"}, ()),
            ("D2", "LED_0603_1608Metric", "GREEN", 115, 130, 90, "bottom", {"LCSC": "C72043"}, None, ()),
            ("D3", "LED_0603_1608Metric", "RED", 120, 130, 0, "top", {"LCSC": "C2286"}, {"1": "K", "2": "A"}, ()),
            ("U1", "SOIC-8_3.9x4.9mm_P1.27mm", "LM358", 110, 145, 0, "top", {"LCSC": "C5423"}, None, ()),
            ("U2", "SOIC-8_3.9x4.9mm_P1.27mm", "NE555", 125, 145, 30, "bottom", {"LCSC": "C7593"}, None, ()),
            ("U3", "SOIC-8_3.9x4.9mm_P1.27mm", "NE555", 140, 145, 90, "top", {"LCSC": "C7593"}, None, ()),
            ("Q1", "SOT-23", "MMBT3904", 135, 130, 90, "top", {}, None, ()),
            ("L1", "L_0603_1608Metric", "FB", 140, 120, 180, "bottom", {"LCSC": "C80165"}, None, ()),
        ],
    },
    "page_origin": {
        "aux": (50.0, 50.0), "use_aux": False,
        "parts": [
            ("R1", "R_0603_1608Metric", "10k", 60, 70, 0, "top", {"LCSC": "C25804"}, None, ()),
            ("U1", "SOIC-8_3.9x4.9mm_P1.27mm", "LM358", 70, 70, 270, "bottom", {"LCSC": "C5423"}, None, ()),
        ],
    },
}


def build(name, spec):
    b = pcbnew.BOARD()
    ds = b.GetDesignSettings()
    ds.SetAuxOrigin(pcbnew.VECTOR2I(MM(spec["aux"][0]), MM(spec["aux"][1])))
    po = b.GetPlotOptions()
    po.SetUseAuxOrigin(spec["use_aux"])
    b.SetPlotOptions(po)
    for ref, fpn, val, x, y, ang, side, fields, funcs, attrs in spec["parts"]:
        fp = pcbnew.FootprintLoad(LIB, fpn)
        fp.SetFPID(pcbnew.LIB_ID("kicad7", fpn))
        fp.SetReference(ref)
        fp.SetValue(val)
        for k, v in fields.items():
            fp.SetProperty(k, v)
        if funcs:
            for pad in fp.Pads():
                if pad.GetNumber() in funcs:
                    pad.SetPinFunction(funcs[pad.GetNumber()])
        if "exclude_from_pos" in attrs:
            fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
        b.Add(fp)
        fp.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
        if side == "bottom":
            fp.Flip(fp.GetPosition(), True)                # KiCad's default flip (left / right)
        fp.SetOrientationDegrees(ang)
    OUT.mkdir(exist_ok=True)
    f = OUT / f"{name}.kicad_pcb"
    pcbnew.SaveBoard(str(f), b)
    b = pcbnew.LoadBoard(str(f))                           # the truth is read back from the saved file
    truth = {"aux": list(spec["aux"]), "use_aux": spec["use_aux"], "kicad": pcbnew.Version(), "parts": {}}
    for fp in b.GetFootprints():
        pads = {}
        for pad in fp.Pads():
            c = pad.GetPosition()
            bb = pad.GetBoundingBox()
            pads.setdefault(pad.GetNumber() or "?", []).append(
                {"x": round(pcbnew.ToMM(c.x), 6), "y": round(pcbnew.ToMM(c.y), 6),
                 "w": round(pcbnew.ToMM(bb.GetWidth()), 6), "h": round(pcbnew.ToMM(bb.GetHeight()), 6)})
        truth["parts"][fp.GetReference()] = {"side": "bottom" if fp.IsFlipped() else "top",
                                             "angle": fp.GetOrientationDegrees(), "footprint": str(fp.GetFPID().GetLibItemName()),
                                             "at": [pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y)], "pads": pads}
    (OUT / f"{name}.truth.json").write_text(json.dumps(truth, indent=1, sort_keys=True), encoding="utf-8")
    cmd = ["kicad-cli", "pcb", "export", "pos", str(f), "--format", "csv", "--units", "mm", "--side", "both",
           "-o", str(OUT / f"{name}.pos.csv")] + (["--use-drill-file-origin"] if spec["use_aux"] else [])
    subprocess.run(cmd, check=True, capture_output=True)
    print("wrote", f.name, len(truth["parts"]), "parts")


if __name__ == "__main__":
    for n, s in BOARDS.items():
        if len(sys.argv) < 2 or n in sys.argv[1:]:
            build(n, s)
