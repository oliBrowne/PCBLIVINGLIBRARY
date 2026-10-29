"""Minimal S-expression reader/writer for KiCad files (.kicad_mod / .kicad_sym).

Only parses; never evaluates. Safe for untrusted input (bounded by file size). parse(text, keep_quotes=True)
returns quoted strings as Q (a str subclass) so dumps() can write a tree back with the same quoting."""
import math, re

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_ESC = {"n": "\n", "r": "\r", "t": "\t"}


class Q(str):
    """A string that was (or must be written) quoted."""
    __slots__ = ()


def quote(s):
    """KiCad string literal: backslash, double quote and line breaks escaped."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'


def parse(text: str, keep_quotes=False):
    """Return nested lists. Quoted strings -> str (Q with keep_quotes), bare atoms -> str (numbers kept as text)."""
    stack, cur = [], []
    pos, end = (1 if text[:1] == "﻿" else 0), len(text)     # a UTF-8 BOM (Windows editors) is not an atom
    while pos < end:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                bad = text[pos:].lstrip()[:20]
                raise ValueError(f"unterminated string or stray character near {bad!r}")
            break
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            if not stack:
                raise ValueError("unbalanced ')'")
            parent = stack.pop()
            parent.append(cur)
            cur = parent
        elif m.group(3) is not None:
            v = re.sub(r"\\(.)", lambda e: _ESC.get(e.group(1), e.group(1)), m.group(3), flags=re.S)
            cur.append(Q(v) if keep_quotes else v)
        else:
            cur.append(m.group(4))
    if stack:
        raise ValueError("unbalanced '(' - %d unclosed" % len(stack))
    return cur[0] if len(cur) == 1 else cur


_INLINE = {"pts", "at", "xyz", "font", "effects", "stroke", "fill", "layers", "size", "offset", "scale", "rotate", "justify"}


def dumps(node, indent=0, width=100):
    """Write a tree back as KiCad-style text (tab indented). Q atoms (and empty strings) are quoted, other atoms
    written bare; a list of atoms, or a short (at ...) / (effects ...) / (pts ...) style list, stays on one line."""
    atom = lambda a: quote(a) if isinstance(a, Q) or a == "" else str(a)
    if not isinstance(node, list):
        return atom(node)
    flat = "(" + " ".join(dumps(c, 0, 10 ** 9) if isinstance(c, list) else atom(c) for c in node) + ")"
    if all(not isinstance(c, list) for c in node) or (node and node[0] in _INLINE and len(flat) <= width):
        return flat
    pad = "\t" * (indent + 1)
    head, rest = [], list(node)
    while rest and not isinstance(rest[0], list):     # leading atoms stay on the first line
        head.append(atom(rest.pop(0)))
    out = [pad + (dumps(c, indent + 1, width) if isinstance(c, list) else atom(c)) for c in rest]
    return "(" + " ".join(head) + "\n" + "\n".join(out) + "\n" + "\t" * indent + ")"


def find(node, head):
    """Yield direct children lists whose first element == head."""
    for c in node:
        if isinstance(c, list) and c and c[0] == head:
            yield c


def first(node, head, default=None):
    return next(find(node, head), default)


def pads(mod_tree):
    """Extract pads from a parsed footprint: list of dicts (number, shape, x, y, w, h, layers)."""
    out = []
    for p in find(mod_tree, "pad"):
        at, size = first(p, "at"), first(p, "size")
        out.append({"number": p[1], "type": p[2], "shape": p[3],
                    "x": float(at[1]), "y": float(at[2]),
                    "w": float(size[1]), "h": float(size[2]),
                    "layers": (first(p, "layers") or ["layers"])[1:]})
    return out


def _arc_extent(s, m, e):
    """Points bounding a circular arc start -> mid -> end: its ends plus every axis extreme the sweep passes."""
    (ax, ay), (bx, by), (cx, cy) = s, m, e
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:                                # collinear: a straight segment
        return [s, m, e]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    ang = lambda x, y: math.atan2(y - uy, x - ux) % (2 * math.pi)
    a0, am, a1 = ang(ax, ay), ang(bx, by), ang(cx, cy)
    ccw = (am - a0) % (2 * math.pi) < (a1 - a0) % (2 * math.pi)       # the sweep that passes through mid
    lo, sweep = (a0, (a1 - a0) % (2 * math.pi)) if ccw else (a1, (a0 - a1) % (2 * math.pi))
    pts = [s, e]
    for k in range(4):
        t = k * math.pi / 2
        if (t - lo) % (2 * math.pi) <= sweep + 1e-12:
            pts.append((ux + r * math.cos(t), uy + r * math.sin(t)))
    return pts


def courtyard_bbox(mod_tree):
    """Bounding box of the F.CrtYd graphics: lines, rectangles, polygons (arc segments too), circles and arcs (their
    true extent)."""
    xs, ys = [], []
    add = lambda p: (xs.append(float(p[0])), ys.append(float(p[1])))
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for g in find(mod_tree, kind):
            if (first(g, "layer") or [None, None])[1] != "F.CrtYd":
                continue
            pt = lambda k: (float(first(g, k)[1]), float(first(g, k)[2])) if first(g, k) else None
            if kind == "fp_circle" and pt("center") and pt("end"):
                (cx, cy), (ex, ey) = pt("center"), pt("end")
                r = math.hypot(ex - cx, ey - cy)
                add((cx - r, cy - r)); add((cx + r, cy + r))
                continue
            if kind == "fp_arc" and pt("start") and pt("end"):
                if pt("mid"):
                    for p in _arc_extent(pt("start"), pt("mid"), pt("end")):
                        add(p)
                    continue
                if first(g, "angle"):                 # KiCad 5 arcs: start = centre, end = first point, angle in degrees
                    (cx, cy), (ex, ey) = pt("start"), pt("end")
                    a = math.radians(float(first(g, "angle")[1]))
                    rot = lambda t: (cx + (ex - cx) * math.cos(t) - (ey - cy) * math.sin(t),
                                     cy + (ex - cx) * math.sin(t) + (ey - cy) * math.cos(t))
                    for p in _arc_extent((ex, ey), rot(a / 2), rot(a)):
                        add(p)
                    continue
            for k in ("start", "end"):
                p = first(g, k)
                if p:
                    add((p[1], p[2]))
            for xy in find(first(g, "pts") or [], "xy"):
                add((xy[1], xy[2]))
            for a in find(first(g, "pts") or [], "arc"):     # KiCad 7+: (pts ... (arc (start) (mid) (end)) ...)
                s, m, e = (first(a, k) for k in ("start", "mid", "end"))
                if s and m and e:
                    for p in _arc_extent(*((float(q[1]), float(q[2])) for q in (s, m, e))):
                        add(p)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None