"""When two parts may share one footprint file (librarian, `ecadlib datasheet batch`, `ecadlib resolve`).

The librarian writes one <out>/models/PcbLib/<name>.PcbLib per footprint name, and the name comes from the BOM's
footprint field. Two BOM lines can name the same package for different land patterns (`SOIC-14` on a 3.9 mm and on
a 5.3 mm body; `QFN-16` at 0.5 and 0.65 mm pitch). Before this module the second part silently got the first
part's file. Now a part shares a file only when the footprints are the same: the same pads, pad shapes, holes and
courtyard (the geometry hash `ecadlib dedupe` uses), and, when both came from a KiCad or standard footprint, the same
footprint name: KiCad's L_0603 and LED_0603 (C_0201 and LED_0201) have the same pads, but an inductor released on
the LED's polarity mark and 3D body is wrong, and the LED's symbol is chosen by its footprint. Otherwise it gets its own name, tried in this order:

  1. the real package name of its footprint (the KiCad / standard / EasyEDA footprint name), when it starts with
     the BOM name, else <BOM name>_<real name>; e.g. SOIC-14 -> SOIC-14_5.3x10.2mm_P1.27mm
  2. <BOM name>_<part name>

and a name already holding the same pads is shared again. The renamed name becomes the plan item's footprint ref
(`footprint.ref`; the BOM's name stays in `footprint.bom_ref`), the footprint's name inside the .PcbLib, the
symbol's footprint link, the Altium 365 footprint item and the KiCad export name, so every consumer that keys by
ref sees two footprints. The first part to take a name keeps it: re-runs never rename a file a part already has.

Workspaces written before this check: a part whose footprint record says "shared with ..." but has no hash was
never compared, so the librarian resolves it again (it shares again when the pads match)."""
import hashlib
from pathlib import Path

from . import altium_lib as AL, sexpr


# the digest's version, recorded with each hash ("hash_v"): 1 = dedupe's pad hash only; 2 = plus extra() (R-SP-4).
# A record from before (no hash_v) is compared by recomputing from its own .kicad_mod, else with version 1 of the
# new part's digest, so an upgrade never splits a footprint from the released part that shares it.
HASH_V = 2


def digest(text, version=HASH_V):
    """Geometry hash of a .kicad_mod: dedupe's footprint hash (pads, shapes, holes, courtyard), plus what that leaves
    out but the board gets (extra(): custom-pad copper, paste / mask windows, the copper side, copper graphics); a
    text hash when it has no pads. version 1: without extra(), as hashes recorded before HASH_V 2."""
    from . import dedupe as DD
    try:
        h = DD.fp_hash(DD.fp_geometry_kicad(text))
        more = extra(text) if h and version >= 2 else []
        if more:                               # only then: a plain footprint keeps the hash workspaces recorded
            h = DD._digest([h] + more)
    except Exception:                          # unparsable: only an identical file is "the same"
        h = None
    return h or "text:" + hashlib.sha256(str(text).encode()).hexdigest()[:16]


_PRIM_PTS = ("start", "end", "center", "mid")


def _shape_key(g):
    """A graphic (pad primitive or fp_* item) as a sortable list: its kind, layer, points (0.01 mm), width, fill."""
    from . import dedupe as DD
    q = lambda v: round(DD._q(float(v)), 2)
    pts = [[k, q(c[1]), q(c[2])] for k in _PRIM_PTS for c in [sexpr.first(g, k)] if c and len(c) > 2]
    pts += [[q(p[1]), q(p[2])] for p in sexpr.find(sexpr.first(g, "pts") or [], "xy")]
    w = sexpr.first(g, "width") or (sexpr.first(sexpr.first(g, "stroke") or [], "width"))
    fill = sexpr.first(g, "fill")
    return [str(g[0]), str((sexpr.first(g, "layer") or [None, ""])[1]), pts, q(w[1]) if w else 0,
            str(fill[1]) if fill and len(fill) > 1 else ""]


def extra(text):
    """What dedupe's pad hash leaves out of a .kicad_mod but a board gets: a custom pad's copper primitives, a copper
    pad without the usual paste / mask opening (or with paste / mask margins), paste- or mask-only apertures, a pad
    on the bottom copper, and copper / paste / mask graphics. Sorted; empty for a plain footprint."""
    from . import dedupe as DD
    t = sexpr.parse(text)
    out = []
    num = lambda v: round(float(v), 4)
    for k in ("solder_paste_margin", "solder_paste_margin_ratio", "solder_paste_ratio", "solder_mask_margin"):
        v = sexpr.first(t, k)
        if v and len(v) > 1 and float(v[1]):
            out.append(["fp", k, num(v[1])])
    for p in sexpr.find(t, "pad"):
        if len(p) < 4 or p[2] == "np_thru_hole":
            continue
        layers = [str(x) for x in (sexpr.first(p, "layers") or ["layers"])[1:]]
        cu = [x for x in layers if x.endswith(".Cu")]
        paste = any(x.endswith(".Paste") for x in layers)
        mask = any(x.endswith(".Mask") for x in layers)
        if not cu:                             # a paste- or mask-only aperture: a window the pad hash drops
            at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
            w, h = float(size[1]), float(size[2] if len(size) > 2 else size[1])
            out.append(["aperture", sorted(layers), str(p[3]),
                        list(DD._pad_tuple("", float(at[1]), -float(at[2]), w, h, float(at[3]) if len(at) > 3 else 0.0, False))])
            continue
        n = str(p[1])
        if p[2] == "smd" and cu != ["F.Cu"]:
            out.append(["side", n, sorted(cu)])
        if (paste, mask) != (p[2] == "smd", True):
            out.append(["openings", n, paste, mask])
        for k in ("solder_paste_margin", "solder_paste_margin_ratio", "solder_mask_margin"):
            v = sexpr.first(p, k)
            if v and len(v) > 1 and float(v[1]):
                out.append(["margin", n, k, num(v[1])])
        if p[3] == "custom":
            prims = sexpr.first(p, "primitives") or []
            out.append(["custom", n, sorted(_shape_key(g) for g in prims[1:] if isinstance(g, list))])
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for g in sexpr.find(t, kind):
            lay = str((sexpr.first(g, "layer") or [None, ""])[1])
            if lay.endswith((".Cu", ".Paste", ".Mask")):
                out.append(["graphic", _shape_key(g)])
    return sorted(out, key=lambda e: repr(e))


def real_name(text, source=""):
    """The footprint's own name: (footprint "X") / (module "X") in the text, else the KiCad / standard source id."""
    try:
        t = sexpr.parse(text)
        if isinstance(t, list) and len(t) > 1 and t[0] in ("footprint", "module") and isinstance(t[1], str):
            return t[1].split(":")[-1]
    except Exception:
        pass
    src = str(source or "")
    return src.rsplit(":", 1)[-1] if src.startswith(("kicad:", "standard:", "local:")) else ""


def candidates(name, real, part):
    """File names to try for a footprint the BOM calls `name`, in order (see the module doc)."""
    out = [name]
    if real and AL.file_stem(real).lower() != AL.file_stem(name).lower():
        out.append(real if real.lower().startswith(name.lower()) else f"{name}_{real}")
    out.append(f"{name}_{part}")
    return list(dict.fromkeys(out))


def base_of(rec):
    """The BOM footprint name a footprint record was written for."""
    return rec.get("renamed_from") or rec.get("name") or Path(str(rec.get("file") or "")).stem


def hash_of(rec, out, cache):
    """A footprint record's geometry hash -> (hash, version): recorded at this HASH_V; else from its .kicad_mod
    (workspaces from before this check, or a hash of an older version); else the older recorded hash, version 1."""
    if rec.get("hash") and int(rec.get("hash_v") or 1) >= HASH_V:
        return rec["hash"], HASH_V
    k = rec.get("kicad_mod") or ""
    if k not in cache:
        p = Path(out) / k if k else None
        cache[k] = digest(p.read_text(encoding="utf-8")) if p and p.is_file() else None
    if cache[k] or not rec.get("hash"):
        return cache[k], HASH_V
    return rec["hash"], int(rec.get("hash_v") or 1)


def unchecked_share(rec):
    """A footprint record that got another part's file by name only (written before the pads were compared)."""
    rec = rec or {}
    return not rec.get("hash") and not rec.get("chosen") and any(str(n).startswith("shared with ") for n in rec.get("notes", []))


def lib_name(source):
    """The library footprint a KiCad / standard source names (Inductor_SMD:L_0603_1608Metric -> L_0603_1608Metric), else ""."""
    src = str(source or "")
    return src.rsplit(":", 1)[-1].lower() if src.startswith(("kicad:", "standard:")) else ""


def same_fp(rec, h, source, out, cache, text=None):
    """A footprint record holds the same footprint: same pads, and the same library footprint when both name one.
    h: the new footprint's digest; text: its .kicad_mod, for a record hashed by an older digest version."""
    a, b = lib_name(rec.get("source")), lib_name(source)
    rh, v = hash_of(rec, out, cache)
    if v != HASH_V and text is not None:
        h = digest(text, v)
    return rh == h and (not a or not b or a == b)


def pick(written, models, name, text, source, part, out, cache):
    """-> (name, path, record to share or None). written: {PcbLib path: footprint record} of this run."""
    h, real = digest(text), real_name(text, source)
    fold = lambda s: AL.file_stem(s).casefold()  # Windows / macOS file systems ignore case: SOIC-8 and soic-8 are one file
    for rec in written.values():               # same pads under a name already made for this BOM name: share it
        f = str(rec.get("file") or "")
        if f.endswith(".PcbLib") and fold(base_of(rec)) == fold(name) and same_fp(rec, h, source, out, cache, text):
            return rec.get("name") or Path(f).stem, str(Path(out) / f), rec, h
    taken = {str(p).casefold(): (p, rec) for p, rec in written.items()}
    cands = candidates(name, real, part)
    for i, c in enumerate(cands):
        pcb = str(Path(models) / "PcbLib" / f"{AL.file_stem(c)}.PcbLib")
        have, rec = taken.get(pcb.casefold(), (pcb, None))
        if rec is None:                        # nobody in this run has it (a file left from an earlier run of
            return c, pcb, None, h             # this part, or of a part no longer in the BOM, is rewritten)
        if same_fp(rec, h, source, out, cache, text):   # its file under its name (the case it was written in)
            return rec.get("name") or c, have, rec, h
    c = cands[-1]                              # <name>_<part> is this part's own: rewrite it
    return c, str(Path(models) / "PcbLib" / f"{AL.file_stem(c)}.PcbLib"), None, h
