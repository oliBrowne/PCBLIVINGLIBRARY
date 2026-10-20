"""Write the README's pictures (docs/readme/): the header and the flow diagram, each in a light and a dark version.
The README picks one with <picture> and prefers-color-scheme, so they follow GitHub's theme. The mark comes from
ecadlib/brand.py; copper stays an accent (the leaves and one highlight). Run after changing this file or brand.py:
python scripts/build_readme_art.py (tests/test_brand.py checks the files match)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ecadlib import brand as B  # noqa: E402

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace"

# GitHub's own light and dark page colours, so the pictures sit on the page instead of in a box.
THEMES = {
    "light": dict(ink=B.INK, muted="#59636e", line="#d1d9e0", faint="#eef1f4", copper=B.COPPER,
                  ok="#1a7f37", held="#9a6700", card="#ffffff"),
    "dark": dict(ink=B.INK_DARK, muted="#9198a1", line="#3d444d", faint="#1c2128", copper=B.COPPER_DARK,
                 ok="#3fb950", held="#d29922", card="#0d1117"),
}


def _svg(w, h, body, label):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{label}">{body}</svg>\n')


def _text(x, y, s, size, fill, weight=400, anchor="start", font=SANS, spacing=0):
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{ls}>{s}</text>')


def _traces(t, w, h):
    """Faint 45-degree board traces ending in vias, along both edges of the header."""
    out = []
    for flip in (False, True):
        for i, y in enumerate((38, 70, 102, 134, 166)):
            run = 60 + 26 * (i % 3)
            x0, x1 = (w, w - run) if flip else (0, run)
            dx = -22 if flip else 22
            dy = -22 if i < 2 else 22 if i > 2 else 0
            x2, y2 = x1 + dx, y + dy
            out.append(f'<path d="M{x0} {y}H{x1}L{x2} {y2}" fill="none" stroke="{t["line"]}" stroke-width="2" '
                       f'stroke-linecap="round" stroke-linejoin="round"/>'
                       f'<circle cx="{x2}" cy="{y2}" r="4" fill="none" stroke="{t["line"]}" stroke-width="2"/>')
    return "".join(out)


def header(t):
    w, h = 880, 200
    mark = f'<g transform="translate(222 46) scale(3.4)">{B.mark_svg_body(t["ink"], t["copper"])}</g>'
    body = (_traces(t, w, h) + mark
            + _text(352, 100, "ecadlib", 60, t["ink"], 700, spacing=-1)
            + _text(354, 132, "Your component library, kept alive.", 19, t["muted"])
            + _text(354, 160, "BOM in  ·  every part checked  ·  Altium 365 and KiCad out", 13, t["muted"], font=MONO))
    return _svg(w, h, body, "ecadlib: your component library, kept alive")


STEPS = (
    ("01", "Read the BOM", ("Altium, ActiveBOM,", "KiCad, CSV or XLSX")),
    ("02", "Find the part", ("your libraries, KiCad", "libraries, Nexar,", "DigiKey, Mouser")),
    ("03", "Draw the rest", ("a model reads the", "datasheet, code draws", "IPC-7351B pads")),
    ("04", "Check every part", ("pins match pads, rules,", "3D height, fields,", "house style")),
    ("05", "Release", ("Altium 365, SchLib,", "PcbLib, DbLib or", "KiCad libraries")),
)


def _arrow(x1, y1, x2, y2, color, dash=False):
    d = ' stroke-dasharray="5 5"' if dash else ""
    return (f'<path d="M{x1} {y1}L{x2} {y2}" stroke="{color}" stroke-width="1.6" fill="none"{d} '
            f'marker-end="url(#a)"/>')


def flow(t):
    w, h, bw, bh, gap, top = 960, 318, 160, 128, 30, 20
    xs = [20 + i * (bw + gap) for i in range(5)]
    out = [f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{t["muted"]}"/></marker></defs>']
    for i, (x, (num, title, lines)) in enumerate(zip(xs, STEPS)):
        check = i == 3
        stroke, sw = (t["copper"], 2) if check else (t["line"], 1.5)
        out.append(f'<rect x="{x}" y="{top}" width="{bw}" height="{bh}" rx="10" fill="{t["card"]}" '
                   f'stroke="{stroke}" stroke-width="{sw}"/>')
        out.append(_text(x + 16, top + 28, num, 12, t["copper"] if check else t["muted"], font=MONO))
        out.append(_text(x + 16, top + 52, title, 16, t["ink"], 600))
        for j, s in enumerate(lines):
            out.append(_text(x + 16, top + 78 + j * 17, s, 12.5, t["muted"]))
        if i < 4:
            out.append(_arrow(x + bw + 3, top + bh / 2, x + bw + gap - 3, top + bh / 2, t["muted"]))
    # the ready dot on Release
    out.append(f'<circle cx="{xs[4] + bw - 20}" cy="{top + 47}" r="5" fill="{t["ok"]}"/>')
    # held for a person, under Check, signed off into Release
    hx, hy, hw, hh = xs[3] - 20, top + bh + 60, bw + 40, 92
    out.append(_arrow(xs[3] + bw / 2, top + bh + 3, xs[3] + bw / 2, hy - 3, t["muted"]))
    out.append(_text(xs[3] + bw / 2 + 8, top + bh + 35, "in doubt", 12, t["muted"], font=MONO))
    out.append(f'<rect x="{hx}" y="{hy}" width="{hw}" height="{hh}" rx="10" fill="{t["faint"]}" '
               f'stroke="{t["line"]}" stroke-width="1.5" stroke-dasharray="5 4"/>')
    out.append(f'<circle cx="{hx + 22}" cy="{hy + 27}" r="5" fill="{t["held"]}"/>')
    out.append(_text(hx + 36, hy + 32, "Held for a person", 16, t["ink"], 600))
    out.append(_text(hx + 16, hy + 58, "why it is held, and the", 12.5, t["muted"]))
    out.append(_text(hx + 16, hy + 75, "next step, in plain words", 12.5, t["muted"]))
    rx = xs[4] + bw / 2
    out.append(f'<path d="M{hx + hw + 3} {hy + hh / 2}H{rx}V{top + bh + 4}" stroke="{t["muted"]}" '
               f'stroke-width="1.6" fill="none" stroke-dasharray="5 5" marker-end="url(#a)"/>')
    out.append(_text(rx + 10, hy + hh / 2 - 10, "signed off", 12, t["muted"], font=MONO))
    # nothing changes behind your back
    out.append(_text(20, top + bh + 60 + 32, "Released parts stay put:", 13, t["ink"], 600))
    out.append(_text(20, top + bh + 60 + 52, "a library update that changes pins,", 12.5, t["muted"]))
    out.append(_text(20, top + bh + 60 + 69, "pads or identity waits for a person.", 12.5, t["muted"]))
    return _svg(w, h, "".join(out), "BOM to library: read the BOM, find the part, draw the rest, check every part, "
                "release. Parts in doubt are held for a person, then signed off into the release.")


def files():
    """Path under docs/readme/ -> bytes."""
    out = {}
    for name, t in THEMES.items():
        out[f"header-{name}.svg"] = header(t).encode()
        out[f"flow-{name}.svg"] = flow(t).encode()
    return out


if __name__ == "__main__":
    for rel, data in files().items():
        p = ROOT / "docs" / "readme" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        print(f"wrote docs/readme/{rel}")
