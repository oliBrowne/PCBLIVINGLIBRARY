"""Writes site/hero-data.js: the TLC5951DAP footprint and symbol the landing page's hero opens up, made by ecadlib's
own generators (footprint_gen for the land pattern, symbol_layout for the pin layout).

The hero shows them as they come out, so what the page draws is what ecadlib builds for that part.
Run it from the repository root after changing either generator: python3 scripts/build_site_hero.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ecadlib import footprint_gen, symbol_layout

# TLC5951DAP (TI HTSSOP-38 PowerPAD) as the datasheet gives it: the package drawing and the land TI recommends
# (the same read tests/test_powerpad_retest_1022.py builds)
SPEC = {"name": "HTSSOP-38-1EP_6.1x12.5mm_P0.65mm_EP3.39x6.35mm", "package_type": "gullwing", "pin_count": 38,
        "pitch": 0.65, "lead_span": {"min": 7.9, "max": 8.3}, "body_width": {"min": 6.0, "max": 6.2},
        "body_length": {"min": 12.4, "max": 12.6}, "terminal_width": {"min": 0.19, "max": 0.30},
        "terminal_length": {"min": 0.50, "max": 0.75},
        "exposed_pad": {"length": {"min": 5.05, "max": 6.35}, "width": {"min": 2.69, "max": 3.39}},
        "land": {"pad_length": 1.8, "pad_width": 0.3, "pad_center": 3.7, "ep_across": 5.2, "ep_along": 12.5,
                 "ep_mask_across": 3.39, "ep_mask_along": 6.35}}
PART = "tssop-TLC5951DAP"            # its pin list, from the KiCad evaluation set
NUM = r"(-?[\d.]+)"


def _pts(line, key):
    return [[float(a), float(b)] for a, b in re.findall(r"\(" + key + " " + NUM + " " + NUM + r"\)", line)]


def footprint():
    text, rep = footprint_gen.build(SPEC)
    assert not rep["checks"], rep["checks"]                       # the hero only shows a part that passes its checks
    out = {"pads": [], "mask": None, "paste": [], "silk": [], "pin1": None, "fab": None, "crt": None}
    for line in (s.strip() for s in text.splitlines()):
        m = re.match(r'\(pad "([^"]*)" smd \w+ \(at ' + NUM + " " + NUM + r"(?: -?[\d.]+)?\) \(size " + NUM + " " + NUM + r"\) \(layers ([^)]*)\)", line)
        if m:
            pad = {"n": m.group(1), "x": float(m.group(2)), "y": float(m.group(3)), "w": float(m.group(4)), "h": float(m.group(5))}
            layers = m.group(6).split()
            if '"F.Cu"' in layers:
                out["pads"].append(pad)
            elif layers == ['"F.Mask"']:
                out["mask"] = pad
            elif layers == ['"F.Paste"']:
                out["paste"].append(pad)
        elif '(layer "F.SilkS")' in line and line.startswith("(fp_line"):
            out["silk"].append(_pts(line, "(?:start|end)"))
        elif '(layer "F.SilkS")' in line and line.startswith("(fp_poly"):
            out["pin1"] = _pts(line, "xy")
        elif '(layer "F.Fab")' in line and line.startswith("(fp_poly"):
            out["fab"] = _pts(line, "xy")
        elif '(layer "F.CrtYd")' in line and line.startswith("(fp_rect"):
            (x0, y0), (x1, y1) = _pts(line, "(?:start|end)")
            out["crt"] = [x0, y0, x1, y1]
    missing = [k for k, v in out.items() if not v]
    assert not missing, f"footprint_gen output has no {missing}"
    assert len(out["pads"]) == SPEC["pin_count"] + 1, f"{len(out['pads'])} copper pads, not 38 and the PowerPAD"
    return out


def _pin_type(name):
    """The evaluation set lists numbers and names only; the hero needs a side for each pin, so name the obvious ones."""
    if name.startswith("OUT") or name.endswith("SOUT"):
        return "output"
    if name in ("VCC", "GND"):
        return "power_in"
    return "passive" if name == "IREF" else "input"


def symbol():
    rows = [json.loads(line.strip().rstrip(",")) for line in (ROOT / "ecadlib" / "eval" / "sets" / "kicad-v1.json").open(encoding="utf-8")
            if f'"id":"{PART}"' in line.replace(" ", "")]
    assert len(rows) == 1, f"{PART}: {len(rows)} rows in kicad-v1.json"
    pins = [{"num": n, "name": name, "type": _pin_type(name)} for n, name in rows[0]["pins"]]
    sym = symbol_layout.build("TLC5951DAP", pins, description=rows[0]["description"])
    out = {"rect": None, "pins": []}
    for prim in sym["prims"]:
        if prim[0] == "rect":
            out["rect"] = list(prim[1:])
        else:
            pin, side, x, y, length = prim[1:]
            out["pins"].append({"n": pin["num"], "name": pin["name"], "side": side, "x": x, "y": y, "len": length})
    return out


def text():
    dump = lambda v: json.dumps(v, separators=(",", ":"))
    return ("// Made by scripts/build_site_hero.py from ecadlib's own footprint and symbol generators. Do not edit by hand.\n"
            f"export const FOOTPRINT = {dump(footprint())};\n"
            f"export const SYMBOL = {dump(symbol())};\n")


if __name__ == "__main__":
    out = ROOT / "site" / "hero-data.js"
    out.write_text(text(), encoding="utf-8", newline="\n")
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size} bytes)")
