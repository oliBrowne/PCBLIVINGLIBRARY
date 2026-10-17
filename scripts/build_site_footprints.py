"""Writes site/footprints.json: a few land patterns made by ecadlib's own IPC-7351B generator, for the landing page.

The page draws them in the style of Altium's PCB editor, so what it shows is what `ecadlib footprint` makes.
Run it from the repository root after changing the generator: python3 scripts/build_site_footprints.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ecadlib import footprint_gen

SPECS = ["SOIC-8_3.9x4.9mm_P1.27mm", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm", "SOT-23",
         "LQFP-48_7x7mm_P0.5mm", "SOT-223-3_TabPin2", "R_0603_1608Metric"]
SHOWN = ["package_type", "pin_count", "pitch", "lead_span", "body_width", "body_length",
         "terminal_width", "terminal_length", "exposed_pad", "density"]
NUM = r"(-?[\d.]+)"


def xy(s):
    return [[float(a), float(b)] for a, b in re.findall(r"\(xy " + NUM + " " + NUM + r"\)", s)]


def one(name):
    spec = json.loads((ROOT / "ecadlib" / "package_specs" / f"{name}.json").read_text())
    text, rep = footprint_gen.build(spec)
    pads, fab, silk, marks, court = [], [], [], [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("(pad "):
            m = re.match(r'\(pad "([^"]*)" smd (\w+) \(at ' + NUM + " " + NUM + r"\) \(size " + NUM + " " + NUM + r"\) \(layers ([^)]*)\)", line)
            if m and '"F.Cu"' in m.group(7):
                pads.append({"num": m.group(1), "shape": m.group(2), "x": float(m.group(3)), "y": float(m.group(4)),
                             "w": float(m.group(5)), "h": float(m.group(6))})
        elif '(layer "F.Fab")' in line and line.startswith(("(fp_poly", "(fp_rect")):
            if line.startswith("(fp_rect"):
                (x0, y0), (x1, y1) = [map(float, p) for p in re.findall(r"\((?:start|end) " + NUM + " " + NUM + r"\)", line)]
                fab = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            else:
                fab = xy(line)
        elif '(layer "F.SilkS")' in line and line.startswith("(fp_line"):
            silk.append([list(map(float, p)) for p in re.findall(r"\((?:start|end) " + NUM + " " + NUM + r"\)", line)])
        elif '(layer "F.SilkS")' in line and line.startswith("(fp_poly"):
            marks.append(xy(line))
        elif '(layer "F.CrtYd")' in line:
            (x0, y0), (x1, y1) = [list(map(float, p)) for p in re.findall(r"\((?:start|end) " + NUM + " " + NUM + r"\)", line)]
            court = [x0, y0, x1, y1]
    ipc = {k: rep[k] for k in ("Z", "G", "X") if k in rep}
    return {"name": spec["name"], "spec": {k: spec[k] for k in SHOWN if k in spec}, "pads": pads, "fab": fab,
            "silk": silk, "marks": marks, "courtyard": court, "ipc": ipc, "density": rep["density"],
            "checks": len(rep["checks"])}


if __name__ == "__main__":
    out = ROOT / "site" / "footprints.json"
    out.write_text(json.dumps([one(n) for n in SPECS], separators=(",", ":")) + "\n")
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size} bytes)")
