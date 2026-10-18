"""When two parts may share one footprint file (librarian, `ecadlib datasheet batch`, `ecadlib resolve`).

The librarian writes one <out>/models/PcbLib/<name>.PcbLib per footprint name, and the name comes from the BOM's
footprint field. Two BOM lines can name the same package for different land patterns (`SOIC-14` on a 3.9 mm and on
a 5.3 mm body; `QFN-16` at 0.5 and 0.65 mm pitch). Before this module the second part silently got the first
part's file. Now a part shares a file only when the footprints are the same: the same pads, pad shapes, holes and
courtyard (the geometry hash `ecadlib dedupe` uses). Otherwise it gets its own name, tried in this order:

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


def digest(text):
    """Geometry hash of a .kicad_mod: dedupe's footprint hash (pads, shapes, holes, courtyard); a text hash when it has no pads."""
    from . import dedupe as DD
    try:
        h = DD.fp_hash(DD.fp_geometry_kicad(text))
    except Exception:                          # unparsable: only an identical file is "the same"
        h = None
    return h or "text:" + hashlib.sha256(str(text).encode()).hexdigest()[:16]


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
    """A footprint record's geometry hash: recorded, else from its .kicad_mod (workspaces from before this check)."""
    if rec.get("hash"):
        return rec["hash"]
    k = rec.get("kicad_mod") or ""
    if k not in cache:
        p = Path(out) / k if k else None
        cache[k] = digest(p.read_text(encoding="utf-8")) if p and p.is_file() else None
    return cache[k]


def unchecked_share(rec):
    """A footprint record that got another part's file by name only (written before the pads were compared)."""
    rec = rec or {}
    return not rec.get("hash") and not rec.get("chosen") and any(str(n).startswith("shared with ") for n in rec.get("notes", []))


def pick(written, models, name, text, source, part, out, cache):
    """-> (name, path, record to share or None). written: {PcbLib path: footprint record} of this run."""
    h, real = digest(text), real_name(text, source)
    for rec in written.values():               # same pads under a name already made for this BOM name: share it
        f = str(rec.get("file") or "")
        if f.endswith(".PcbLib") and AL.file_stem(base_of(rec)) == AL.file_stem(name) and hash_of(rec, out, cache) == h:
            return rec.get("name") or Path(f).stem, str(Path(out) / f), rec, h
    cands = candidates(name, real, part)
    for i, c in enumerate(cands):
        pcb = str(Path(models) / "PcbLib" / f"{AL.file_stem(c)}.PcbLib")
        rec = written.get(pcb)
        if rec is None:                        # nobody in this run has it (a file left from an earlier run of
            return c, pcb, None, h             # this part, or of a part no longer in the BOM, is rewritten)
        if hash_of(rec, out, cache) == h:
            return c, pcb, rec, h
    c = cands[-1]                              # <name>_<part> is this part's own: rewrite it
    return c, str(Path(models) / "PcbLib" / f"{AL.file_stem(c)}.PcbLib"), None, h
