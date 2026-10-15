"""Minimal S-expression reader/writer for KiCad files (.kicad_mod / .kicad_sym).

Only parses; never evaluates. Safe for untrusted input (bounded by file size). parse(text, keep_quotes=True)
returns quoted strings as Q (a str subclass) so dumps() can write a tree back with the same quoting."""
import re

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
    pos, end = 0, len(text)
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


def courtyard_bbox(mod_tree):
    xs, ys = [], []
    for kind in ("fp_line", "fp_rect", "fp_poly"):
        for g in find(mod_tree, kind):
            if (first(g, "layer") or [None, None])[1] != "F.CrtYd":
                continue
            for k in ("start", "end"):
                pt = first(g, k)
                if pt:
                    xs.append(float(pt[1])); ys.append(float(pt[2]))
            for xy in find(first(g, "pts") or [], "xy"):
                xs.append(float(xy[1])); ys.append(float(xy[2]))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None
