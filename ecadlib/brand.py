"""The ecadlib mark ("Sprout Trace", bold weight): a copper-leaved trace growing out of a via.

One drawing, used everywhere: the SVGs below for web pages, and mark_png() for the KiCad toolbar button and the
Windows icon. Pure Python (no Pillow) so the KiCad plugin can draw it. docs/brand.md has the usage rules.
Copper is an accent only: the leaves of the mark, focus rings and small highlights, never large fills."""
import struct
import zlib

INK, INK_DARK = "#232327", "#ececf0"
COPPER, COPPER_DARK = "#c2622d", "#e8834a"           # copper on light grounds / on dark grounds

# 32-unit grid. Traces (round caps), the via (a ring), two leaves (cubic Béziers).
STROKE = 3.4
TRACES = (((16, 23.5), (16, 13.5)), ((16, 20), (10, 14)), ((16, 15), (22, 9)))
VIA = (16, 26.8, 1.5, 4.0)                          # centre x, y, drill radius, ring radius
_LEAF_L = ((10.5, 15), ((5.5, 15), (3.8, 11.2), (4, 7.6)), ((7.6, 7.4), (11.2, 9.4), (10.5, 15)))
_LEAF_R = ((21.5, 10), ((21.3, 5.6), (24.2, 3.4), (28, 3.6)), ((28.2, 7.6), (25.8, 10.4), (21.5, 10)))


def _leaf(spec, base, scale=1.2):
    """The leaf as a polygon, scaled about its stem end and moved onto `base`."""
    (x0, y0), *curves = spec
    pts, cur = [], (x0, y0)
    for c1, c2, end in curves:
        for i in range(1, 13):
            t = i / 12
            a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
            pts.append((a * cur[0] + b * c1[0] + c * c2[0] + d * end[0], a * cur[1] + b * c1[1] + c * c2[1] + d * end[1]))
        cur = end
    return [(base[0] + scale * (x - x0), base[1] + scale * (y - y0)) for x, y in pts]


LEAVES = (_leaf(_LEAF_L, (10, 14)), _leaf(_LEAF_R, (22, 9)))


def _d(poly):
    return "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in poly) + "Z"


def mark_svg_body(ink="currentColor", copper=f"var(--copper, {COPPER})"):
    """The mark's SVG elements (viewBox 0 0 32 32), for inlining in a page."""
    cx, cy, r0, r1 = VIA
    lines = "".join(f'<path d="M{a[0]} {a[1]}L{b[0]} {b[1]}"/>' for a, b in TRACES)
    return (f'<g fill="none" stroke="{ink}" stroke-width="{STROKE}" stroke-linecap="round">{lines}'
            f'<circle cx="{cx}" cy="{cy}" r="{(r0 + r1) / 2}" stroke-width="{r1 - r0}"/></g>'
            + "".join(f'<path fill="{copper}" d="{_d(p)}"/>' for p in LEAVES))


def mark_svg(ink=INK, copper=COPPER, size=None, adaptive=False):
    """A standalone SVG file. adaptive=True flips the ink for dark browser tabs (for favicons)."""
    wh = f' width="{size}" height="{size}"' if size else ""
    if not adaptive:
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"{wh}>{mark_svg_body(ink, copper)}</svg>\n'
    style = (f"<style>.i{{stroke:{INK}}}.c{{fill:{COPPER}}}@media (prefers-color-scheme:dark)"
             f"{{.i{{stroke:{INK_DARK}}}.c{{fill:{COPPER_DARK}}}}}</style>")
    body = mark_svg_body("X", "Y").replace('stroke="X"', 'class="i"').replace('fill="Y"', 'class="c"')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"{wh}>{style}{body}</svg>\n'


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _seg_dist2(px, py, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    qx, qy = ax + t * dx - px, ay + t * dy - py
    return qx * qx + qy * qy


def _inside(px, py, poly):
    hit, j = False, len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def mark_rgba(size, tile=True):
    """The mark as size*size RGBA rows (bytes per row, no filter byte). tile=True puts it on a dark rounded tile
    (light trace, copper leaves) so it reads on light and dark toolbars alike; tile=False is ink on transparent."""
    ink = _rgb(INK_DARK if tile else INK) + (255,)
    leaf = _rgb(COPPER_DARK if tile else COPPER) + (255,)
    ground = _rgb(INK) + (255,) if tile else (0, 0, 0, 0)
    k = 0.78 if tile else 1.0                       # the mark sits inside the tile's margin
    cx, cy, r0, r1 = VIA
    half = STROKE / 2
    n = 4 if size >= 48 else 6                      # sub-samples per axis
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(n):
                for sx in range(n):
                    u, v = (x + (sx + .5) / n) * 32 / size, (y + (sy + .5) / n) * 32 / size
                    c = (0, 0, 0, 0)
                    if tile:
                        rr = 7.0
                        ex, ey = max(rr - u, 0, u - (32 - rr)), max(rr - v, 0, v - (32 - rr))
                        if ex * ex + ey * ey <= rr * rr:
                            c = ground
                    mu, mv = 16 + (u - 16) / k, 15.6 + (v - 16) / k
                    if any(_seg_dist2(mu, mv, a, b) <= half * half for a, b in TRACES):
                        c = ink
                    dv = (mu - cx) ** 2 + (mv - cy) ** 2
                    if r0 * r0 < dv <= r1 * r1:
                        c = ink
                    elif dv <= r0 * r0:
                        c = ground
                    if any(_inside(mu, mv, p) for p in LEAVES):
                        c = leaf
                    a = c[3]
                    acc[0] += c[0] * a; acc[1] += c[1] * a; acc[2] += c[2] * a; acc[3] += a
            a = acc[3]
            row += bytes((round(acc[0] / a), round(acc[1] / a), round(acc[2] / a), round(a / (n * n)))) if a else bytes(4)
        rows.append(bytes(row))
    return rows


def mark_png(size=24, tile=True):
    """The mark as PNG bytes (see mark_rgba)."""
    raw = b"".join(b"\x00" + r for r in mark_rgba(size, tile))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
