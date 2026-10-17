"""The landing page (site/): checks a browser test would miss on Chromium alone."""
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


def test_svg_stroke_widths_are_plain_numbers():
    """Oliver saw SOT-23 silk drawn as 1 mm slabs over the pads (17 Oct): stroke-width: calc(var(--sw) * 1.2) is a
    unitless calc(), which Chromium takes but Safari and Firefox drop, falling back to 1 user unit = 1 mm. Every
    stroke-width in the footprint player must be a plain number, a length or a var() of one."""
    css = (SITE / "site.css").read_text()
    bad = [m.group(0) for m in re.finditer(r"stroke-width:\s*calc\([^;]*\)", css)]
    assert not bad, bad
    js = (SITE / "site.js").read_text()
    used = set(re.findall(r"var\((--sw[\w-]*)\)", css))
    assert used and all(f'"{v}"' in js for v in used), used          # each one is set from site.js
