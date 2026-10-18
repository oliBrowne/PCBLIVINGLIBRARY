"""The ecadlib mark: one drawing (ecadlib/brand.py) behind the site files, the web UI, the KiCad icon and the .ico."""
import importlib.util
import struct
import sys
import zlib
from pathlib import Path

from ecadlib import brand as B

ROOT = Path(__file__).resolve().parent.parent


def _pixels(png, size):
    n = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41:41 + n])
    rows = [raw[y * (1 + 4 * size) + 1:(y + 1) * (1 + 4 * size)] for y in range(size)]
    return [tuple(r[x * 4:x * 4 + 4]) for r in rows for x in range(size)]


def test_site_brand_files_match_the_drawing():
    spec = importlib.util.spec_from_file_location("build_brand", ROOT / "scripts" / "build_brand.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for rel, data in mod.files().items():
        assert (ROOT / "site" / rel).read_bytes() == data, f"site/{rel} is stale: run python scripts/build_brand.py"


def test_copper_is_the_leaves_only():
    px = _pixels(B.mark_png(64, tile=True), 64)
    copper = tuple(int(B.COPPER_DARK[i:i + 2], 16) for i in (1, 3, 5))
    share = sum(1 for p in px if p[:3] == copper and p[3] == 255) / len(px)
    assert 0.04 < share < 0.2                                      # an accent, not a fill
    assert px[0][3] == 0 and px[32 * 64 + 1][3] == 255             # rounded corner clear, tile edge solid


def test_mark_without_tile_is_transparent_around_it():
    px = _pixels(B.mark_png(32, tile=False), 32)
    assert px[0][3] == 0 and px[-1][3] == 0 and any(p[3] == 255 for p in px)


def test_pages_use_the_mark_and_the_copper_token():
    for f in ("site/index.html", "site/privacy.html", "site/terms.html", "ecadlib/ui/static/index.html",
              "ecadlib/ui/static/setup.html"):
        s = (ROOT / f).read_text(encoding="utf-8")
        assert B.mark_svg_body() in s, f
    css = (ROOT / "site" / "site.css").read_text(encoding="utf-8")
    assert css.count("--copper: #e8834a") == 2 and "--copper: #c2622d" in css   # light, dark (media and toggle)


def test_svg_favicon_follows_the_tab_theme():
    s = B.mark_svg(adaptive=True)
    assert "prefers-color-scheme:dark" in s and B.INK_DARK in s and B.COPPER in s and "currentColor" not in s
