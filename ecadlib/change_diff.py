"""Change classifier: two fingerprints of a part -> the list of what changed, each with a class and a plain sentence.

    from ecadlib import change_diff as CD
    changes = CD.diff(old_fp, new_fp)       [{class, area, what, old, new, text}], most severe first
    CD.worst(changes)                       the most severe class, or None when nothing changed

Classes, most severe first (default policy in brackets, see change_gate.POLICY):
  identity        MPN or manufacturer changed; the value as a number changed (10k -> 1k)                    [hold]
  electrical      a pin's name (as a signal) or type changed, pins swapped names, a pin or pad added or removed,
                  pads renumbered, a pin lost its pad, a pin became hidden or moved to another unit       [hold]
  geometry-major  a pad moved, rotated, changed side, drill, copper offset or paste / mask on-off; a pad size changed
                  beyond SIZE_MINOR; pad shape changed (other than square <-> rounded corners); footprint copper;
                  the footprint changed to another land pattern; the 3D model's origin or rotation moved     [hold]
  geometry-minor  pad size within SIZE_MINOR, corner rounding, paste / mask margins and windows, courtyard, the
                  footprint renamed with the same land pattern, 3D model added / removed / height / file      [warn]
  cosmetic        silkscreen, fab drawing, texts, symbol graphics, pin positions / lengths / shapes, a pin name written
                  another way (RESET# -> ~{RESET}), the value written another way (100nF -> 100n), other fields  [pass]

Tolerances: a length change within TOL["noise"] (parity's position tolerance, 1 um) is no change; SIZE_MINOR is the
pad-size band that counts as minor (max(50 um, 10 % of that side)); 3D origin within TOL["model"] (10 um)."""
from collections import Counter

from . import parity as PA

CLASSES = ("identity", "electrical", "geometry-major", "geometry-minor", "cosmetic")
RANK = {c: i for i, c in enumerate(CLASSES)}
TOL = {"noise": PA.TOL["position"], "angle": PA.TOL["rotation"], "model": 0.01, "corner": PA.TOL["corner"]}
SIZE_MINOR = {"abs": 0.05, "rel": 0.10}


def _f(v):
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    if isinstance(v, (list, tuple)):
        return "(" + ", ".join(_f(x) for x in v) + ")"
    return "-" if v in (None, "") else str(v)


def _chg(out, cls, area, what, old, new, text):
    out.append({"class": cls, "area": area, "what": what, "old": old, "new": new, "text": text})


def _ne(a, b, tol):
    if a is None or b is None:
        return a != b
    return abs(a - b) > tol


def _few(xs, n=8):
    xs = list(xs)
    return ", ".join(xs[:n]) + (f" and {len(xs) - n} more" if len(xs) > n else "")


# ---------------------------------------------------------------- identity and fields
def _identity(o, n, out):
    io, inn = o.get("identity") or {}, n.get("identity") or {}
    for k, label in (("mpn", "MPN"), ("manufacturer", "manufacturer")):
        a, b = (io.get(k) or "").strip(), (inn.get(k) or "").strip()
        if a.upper() != b.upper():
            _chg(out, "identity", "identity", k, a, b, f"{label} changed from {_f(a)} to {_f(b)}")
        elif a != b:
            _chg(out, "cosmetic", "identity", k, a, b, f"{label} written {_f(a)} -> {_f(b)} (same part)")
    va, vb = io.get("value") or "", inn.get("value") or ""
    na, nb = io.get("value_num"), inn.get("value_num")
    if na is not None and nb is not None:
        if abs(na - nb) > 1e-9 * max(abs(na), abs(nb), 1e-30):
            _chg(out, "identity", "identity", "value", va, vb, f"value changed from {_f(va)} to {_f(vb)}")
        elif va != vb:
            _chg(out, "cosmetic", "identity", "value", va, vb, f"value written {_f(va)} -> {_f(vb)} (the same value)")
    elif va.strip().upper() != vb.strip().upper():
        same_mpn = (io.get("mpn") or "").strip() and (io.get("mpn") or "").strip().upper() == (inn.get("mpn") or "").strip().upper()
        if (na is None) != (nb is None) and not same_mpn:
            _chg(out, "identity", "identity", "value", va, vb, f"value changed from {_f(va)} to {_f(vb)}")
        else:
            _chg(out, "cosmetic" if same_mpn else "identity", "identity", "value", va, vb,
                 f"value changed from {_f(va)} to {_f(vb)}" + (" (the MPN is the same)" if same_mpn else ""))
    elif va != vb:
        _chg(out, "cosmetic", "identity", "value", va, vb, f"value written {_f(va)} -> {_f(vb)}")


def _fields(o, n, out):
    fo, fn = o.get("fields") or {}, n.get("fields") or {}
    for k in sorted(set(fo) | set(fn)):
        a, b = fo.get(k), fn.get(k)
        if a == b:
            continue
        txt = (f"field {k} added ({_f(b)})" if a is None else f"field {k} removed (was {_f(a)})" if b is None
               else f"field {k} changed from {_f(a)} to {_f(b)}")
        _chg(out, "cosmetic", "fields", k, a, b, txt)


# ---------------------------------------------------------------- symbol
def _symbol(o, n, out):
    so, sn = o.get("symbol"), n.get("symbol")
    if so is None and sn is None:
        return
    if so is None or sn is None:
        _chg(out, "electrical", "symbol", "symbol", so and so.get("name"), sn and sn.get("name"),
             "symbol added" if so is None else "symbol removed")
        return
    if so.get("name") != sn.get("name"):
        _chg(out, "cosmetic", "symbol", "name", so.get("name"), sn.get("name"),
             f"symbol renamed {_f(so.get('name'))} -> {_f(sn.get('name'))}")
    po, pn = so.get("pins") or {}, sn.get("pins") or {}
    gone, new = sorted(set(po) - set(pn), key=_natkey), sorted(set(pn) - set(po), key=_natkey)
    if gone and new and len(gone) == len(new) and sorted(po[g]["name"] for g in gone) == sorted(pn[x]["name"] for x in new):
        pairs = {g: next(x for x in new if pn[x]["name"] == po[g]["name"]) for g in gone}
        for g, x in pairs.items():
            _chg(out, "electrical", "symbol", f"pin {g}", g, x, f"pin {po[g]['raw'] or po[g]['name'] or '(no name)'} was pin {g}, now pin {x}")
    else:
        for g in gone:
            _chg(out, "electrical", "symbol", f"pin {g}", po[g]["name"], None, f"pin {g} ({po[g]['raw'] or 'no name'}) removed")
        for x in new:
            _chg(out, "electrical", "symbol", f"pin {x}", None, pn[x]["name"], f"pin {x} ({pn[x]['raw'] or 'no name'}) added")
    common = sorted(set(po) & set(pn), key=_natkey)
    renamed = [c for c in common if po[c]["name"] != pn[c]["name"]]
    swap = len(renamed) > 1 and Counter(po[c]["name"] for c in renamed) == Counter(pn[c]["name"] for c in renamed)
    for c in renamed:
        a, b = po[c], pn[c]
        _chg(out, "electrical", "symbol", f"pin {c}", a["name"], b["name"],
             f"pin {c} was {a['raw'] or '(no name)'} now {b['raw'] or '(no name)'}" + (" (pins swapped names)" if swap else ""))
    moved, art = [], []
    for c in common:
        a, b = po[c], pn[c]
        if a["name"] == b["name"] and a.get("raw") != b.get("raw"):
            _chg(out, "cosmetic", "symbol", f"pin {c}", a.get("raw"), b.get("raw"),
                 f"pin {c} name written {_f(a.get('raw'))} -> {_f(b.get('raw'))} (the same signal)")
        if a["type"] != b["type"] or (a.get("ktype") and b.get("ktype") and a["ktype"] != b["ktype"]):
            ta, tb = a.get("ktype") or a["type"], b.get("ktype") or b["type"]
            if not (a.get("ktype") and b.get("ktype")):
                ta, tb = a["type"], b["type"]
            _chg(out, "electrical", "symbol", f"pin {c}", ta, tb, f"pin {c} ({b['raw'] or b['name']}) type {ta} -> {tb}")
        if bool(a.get("hidden")) != bool(b.get("hidden")):
            _chg(out, "electrical", "symbol", f"pin {c}", a.get("hidden"), b.get("hidden"),
                 f"pin {c} ({b['raw'] or b['name']}) is {'hidden' if b.get('hidden') else 'visible'} now")
        elif (a.get("net") or None) != (b.get("net") or None):
            _chg(out, "electrical", "symbol", f"pin {c}", a.get("net"), b.get("net"),
                 f"pin {c} hidden net {_f(a.get('net'))} -> {_f(b.get('net'))}")
        if (a.get("unit") or 0) != (b.get("unit") or 0) and so.get("units", 1) > 1 and sn.get("units", 1) > 1:
            _chg(out, "electrical", "symbol", f"pin {c}", a.get("unit"), b.get("unit"),
                 f"pin {c} ({b['raw'] or b['name']}) moved from unit {a.get('unit')} to unit {b.get('unit')}")
        if [a.get("at"), a.get("orient"), a.get("len")] != [b.get("at"), b.get("orient"), b.get("len")]:
            moved.append(c)
        if a.get("shape") != b.get("shape"):
            art.append(c)
    if moved:
        _chg(out, "cosmetic", "symbol", "pin positions", None, moved, f"pin position / length / direction changed: {_few(moved)}")
    if art:
        _chg(out, "cosmetic", "symbol", "pin shapes", None, art, f"pin shape (bubble, clock) changed: {_few(art)}")
    if so.get("art") != sn.get("art"):
        _chg(out, "cosmetic", "symbol", "graphics", len(so.get("art") or []), len(sn.get("art") or []),
             f"symbol graphics changed ({_n_items(so.get('art'), sn.get('art'))})")
    for k, label in (("units", "units"), ("modes", "display modes")):
        if so.get(k, 1) != sn.get(k, 1):
            _chg(out, "cosmetic", "symbol", k, so.get(k, 1), sn.get(k, 1), f"symbol {label} {so.get(k, 1)} -> {sn.get(k, 1)}")


def _natkey(s):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


# ---------------------------------------------------------------- footprint
def _pos_key(p):
    return (round(p["x"] or 0, 4), round(p["y"] or 0, 4))


def _shape_key(p):
    return (p["w"], p["h"], p["rot"], p["shape"], p["side"], tuple(p["drill"] or ()))


def _renumbered(po, pn):
    """Same pads in the same places, numbered differently -> [(position, old number, new number)], else None."""
    if len(po) != len(pn) or sorted(p["num"] for p in po) != sorted(p["num"] for p in pn):
        return None
    a = sorted((_pos_key(p), _shape_key(p), p["num"]) for p in po)
    b = sorted((_pos_key(p), _shape_key(p), p["num"]) for p in pn)
    if [x[:2] for x in a] != [x[:2] for x in b]:
        return None
    moves = [(x[0], x[2], y[2]) for x, y in zip(a, b) if x[2] != y[2]]
    return moves or None


def _size_minor(a, b):
    if a is None or b is None:
        return a == b
    return abs(a - b) <= max(SIZE_MINOR["abs"], SIZE_MINOR["rel"] * max(abs(a), abs(b)))


def _pad(a, b, out, origin=None):
    what = f"pad {a['num'] or '(unnumbered)'}"
    dx, dy = (b["x"] or 0) - (a["x"] or 0), (b["y"] or 0) - (a["y"] or 0)
    if origin:
        dx, dy = dx - origin[0], dy - origin[1]
    if abs(dx) > TOL["noise"] or abs(dy) > TOL["noise"]:
        axis = (f"{_f(abs(dx))} mm in x" if abs(dy) <= TOL["noise"] else f"{_f(abs(dy))} mm in y" if abs(dx) <= TOL["noise"]
                else f"by ({_f(dx)}, {_f(dy)}) mm")
        _chg(out, "geometry-major", "footprint", what, [a["x"], a["y"]], [b["x"], b["y"]], f"{what} moved {axis}")
    if a["side"] != b["side"]:
        _chg(out, "geometry-major", "footprint", what, a["side"], b["side"], f"{what} side {a['side']} -> {b['side']}")
    if (a["drill"] is None) != (b["drill"] is None) or a["drill"] and (
            _ne(a["drill"][0], b["drill"][0], TOL["noise"]) or _ne(a["drill"][1], b["drill"][1], TOL["noise"]) or
            (a["drill"][2] is None) != (b["drill"][2] is None) or
            a["drill"][2] is not None and PA._angle_diff(a["drill"][2], b["drill"][2]) > TOL["angle"]):
        _chg(out, "geometry-major", "footprint", what, a["drill"], b["drill"], f"{what} drill {_f(a['drill'])} -> {_f(b['drill'])} mm")
    if any(abs(p - q) > TOL["noise"] for p, q in zip(a["offset"], b["offset"])):
        _chg(out, "geometry-major", "footprint", what, a["offset"], b["offset"],
             f"{what} copper offset from the hole {_f(a['offset'])} -> {_f(b['offset'])} mm")
    if a["side"] == "NPTH" and b["side"] == "NPTH":
        return
    sa, sb = a["shape"], b["shape"]
    rounding = {sa, sb} <= {"rect", "roundrect"}
    if sa != sb:
        _chg(out, "geometry-minor" if rounding else "geometry-major", "footprint", what, sa, sb, f"{what} shape {sa} -> {sb}")
    elif sa == "roundrect" and abs(a["corner"] - b["corner"]) > TOL["corner"]:
        _chg(out, "geometry-minor", "footprint", what, a["corner"], b["corner"], f"{what} corner rounding {a['corner']} % -> {b['corner']} %")
    if sa == sb == "custom" and a.get("outline") and b.get("outline") and \
            any(abs(p - q) > TOL["noise"] for p, q in zip(a["outline"], b["outline"])):
        _chg(out, "geometry-major", "footprint", what, a["outline"], b["outline"], f"{what} custom copper outline changed")
    if PA._angle_diff(a["rot"] or 0, b["rot"] or 0, 90.0) > TOL["angle"]:
        _chg(out, "geometry-major", "footprint", what, a["rot"], b["rot"], f"{what} rotated {_f(a['rot'])} -> {_f(b['rot'])} degrees")
    if _ne(a["w"], b["w"], TOL["noise"]) or _ne(a["h"], b["h"], TOL["noise"]):
        minor = _size_minor(a["w"], b["w"]) and _size_minor(a["h"], b["h"])
        _chg(out, "geometry-minor" if minor else "geometry-major", "footprint", what, [a["w"], a["h"]], [b["w"], b["h"]],
             f"{what} size {_f(a['w'])} x {_f(a['h'])} -> {_f(b['w'])} x {_f(b['h'])} mm")
    for k, label in (("paste", "solder paste"), ("mask", "solder mask opening")):
        if a.get(k) is not None and b.get(k) is not None and bool(a[k]) != bool(b[k]):
            _chg(out, "geometry-major", "footprint", what, a[k], b[k], f"{what} {'has' if b[k] else 'lost'} its {label}")
    for k, label in (("paste_margin", "paste margin"), ("mask_margin", "mask margin")):
        if _ne(a.get(k) or 0.0, b.get(k) or 0.0, TOL["noise"]):
            _chg(out, "geometry-minor", "footprint", what, a.get(k), b.get(k), f"{what} {label} {_f(a.get(k))} -> {_f(b.get(k))} mm")


def _footprint(o, n, out):
    fo, fn = o.get("footprint"), n.get("footprint")
    if fo is None and fn is None:
        return
    if fo is None or fn is None:
        _chg(out, "geometry-major", "footprint", "footprint", fo and fo.get("name"), fn and fn.get("name"),
             "footprint added" if fo is None else "footprint removed")
        return
    po, pn = fo["pads"], fn["pads"]
    sub = []
    ren = _renumbered(po, pn)
    if ren:
        for pos, a, b in ren:
            _chg(sub, "electrical", "footprint", f"pad {a}", a, b, f"the pad at ({_f(pos[0])}, {_f(pos[1])}) was {a or '(unnumbered)'}, now {b or '(unnumbered)'}")
    else:
        pairs, oonly, nonly = PA._pair_pads(po, pn)
        for p in oonly:
            _chg(sub, "electrical", "footprint", f"pad {p['num']}", p["num"], None, f"pad {p['num'] or '(unnumbered)'} removed")
        for p in nonly:
            _chg(sub, "electrical", "footprint", f"pad {p['num']}", None, p["num"], f"pad {p['num'] or '(unnumbered)'} added")
        shifts = {(round((b["x"] or 0) - (a["x"] or 0), 4), round((b["y"] or 0) - (a["y"] or 0), 4)) for a, b in pairs}
        origin = next(iter(shifts)) if len(pairs) > 1 and len(shifts) == 1 else None
        if origin and (abs(origin[0]) > TOL["noise"] or abs(origin[1]) > TOL["noise"]):
            _chg(sub, "geometry-major", "footprint", "origin", None, list(origin),
                 f"footprint origin moved by ({_f(origin[0])}, {_f(origin[1])}) mm (every pad moved together: pick-and-place)")
        else:
            origin = None
        for a, b in pairs:
            _pad(a, b, sub, origin)
    if fo.get("windows") != fn.get("windows"):
        _chg(sub, "geometry-minor", "footprint", "windows", len(fo.get("windows") or []), len(fn.get("windows") or []),
             f"paste / mask windows changed ({len(fo.get('windows') or [])} -> {len(fn.get('windows') or [])})")
    if fo.get("copper") != fn.get("copper"):
        _chg(sub, "geometry-major", "footprint", "copper", len(fo.get("copper") or []), len(fn.get("copper") or []),
             "footprint copper (not pads) changed")
    ca, cb = fo.get("courtyard"), fn.get("courtyard")
    if (ca is None) != (cb is None) or ca and any(abs(p - q) > TOL["noise"] for p, q in zip(ca, cb)):
        _chg(sub, "geometry-minor", "footprint", "courtyard", ca, cb, f"courtyard {_f(ca)} -> {_f(cb)} mm")
    land_changed = any(c["class"] in ("electrical", "geometry-major") for c in sub)
    if fo.get("name") != fn.get("name"):
        if land_changed:
            _chg(out, "geometry-major", "footprint", "footprint", fo.get("name"), fn.get("name"),
                 f"footprint changed from {_f(fo.get('name'))} to {_f(fn.get('name'))}")
        else:
            _chg(out, "geometry-minor", "footprint", "footprint", fo.get("name"), fn.get("name"),
                 f"footprint renamed {_f(fo.get('name'))} -> {_f(fn.get('name'))} (the same land pattern)")
    out += sub
    ao, an = fo.get("art") or {}, fn.get("art") or {}
    for k, label in (("silk", "silkscreen"), ("fab", "fab drawing"), ("text", "footprint texts"), ("other", "other drawing layers")):
        if ao.get(k) != an.get(k):
            _chg(out, "cosmetic", "footprint", k, len(ao.get(k) or []), len(an.get(k) or []),
                 f"{label} changed ({_n_items(ao.get(k), an.get(k))})")


def _pins_pads(o, n, out):
    """A pin whose pad went away without the pad being reported (the footprint swapped under the symbol)."""
    from .fingerprint import pin_pad_map
    mo, _ = pin_pad_map(o)
    mn, orphans_n = pin_pad_map(n)
    _, orphans_o = pin_pad_map(o)
    told = {c["what"] for c in out}
    for pin, cnt in mn.items():
        if cnt == 0 and mo.get(pin, 0) > 0 and f"pad {pin}" not in told and f"pin {pin}" not in told:
            _chg(out, "electrical", "pins", f"pin {pin}", mo[pin], 0, f"pin {pin} has no pad now")
    for p in sorted(set(orphans_n) - set(orphans_o)):
        if f"pad {p}" not in told and f"pin {p}" not in told:
            _chg(out, "electrical", "pins", f"pad {p}", None, p, f"pad {p} has no pin now")


# ---------------------------------------------------------------- 3D model
def _model(o, n, out):
    mo, mn = o.get("model"), n.get("model")
    if not mo and not mn:
        return
    if bool(mo and mo.get("models")) != bool(mn and mn.get("models")):
        has = bool(mn and mn.get("models"))
        _chg(out, "geometry-minor", "model", "3D model", None, None, "3D model added" if has else "3D model removed")
        return
    if not mo or not mn or mo.get("tool") != mn.get("tool"):
        return                                 # KiCad vs Altium 3D: not comparable here
    a, b = mo["models"], mn["models"]
    if len(a) != len(b):
        _chg(out, "geometry-minor", "model", "3D model", len(a), len(b), f"3D bodies {len(a)} -> {len(b)}")
        return
    for x, y in zip(a, b):
        if mo["tool"] == "altium":
            if any(_ne(x.get(k), y.get(k), TOL["model"]) for k in ("x", "y", "dz")) or \
                    any(_ne(x.get(k), y.get(k), TOL["angle"]) for k in ("rot", "rx", "ry", "rz")):
                _chg(out, "geometry-major", "model", "3D origin", [x.get(k) for k in ("x", "y", "dz", "rot")],
                     [y.get(k) for k in ("x", "y", "dz", "rot")],
                     f"3D model placement moved: offset ({_f(x.get('x'))}, {_f(x.get('y'))}, z {_f(x.get('dz'))}) -> "
                     f"({_f(y.get('x'))}, {_f(y.get('y'))}, z {_f(y.get('dz'))}) mm, rotation {_f(x.get('rot'))} -> {_f(y.get('rot'))}")
            for k, label in (("height", "3D height"), ("standoff", "3D standoff")):
                if _ne(x.get(k), y.get(k), TOL["model"]):
                    _chg(out, "geometry-minor", "model", label, x.get(k), y.get(k), f"{label} {_f(x.get(k))} -> {_f(y.get(k))} mm")
            if (x.get("step"), x.get("checksum")) != (y.get("step"), y.get("checksum")):
                _chg(out, "geometry-minor", "model", "3D file", x.get("step"), y.get("step"),
                     f"embedded 3D model changed ({_f(x.get('step'))} -> {_f(y.get('step'))})")
            if x.get("outline") != y.get("outline") and x.get("outline") and y.get("outline") and \
                    any(abs(p - q) > TOL["model"] for p, q in zip(x["outline"], y["outline"])):
                _chg(out, "geometry-minor", "model", "3D outline", x.get("outline"), y.get("outline"), "3D body outline changed")
        else:
            if x.get("file") != y.get("file"):
                _chg(out, "geometry-minor", "model", "3D file", x.get("file"), y.get("file"),
                     f"3D model file {_f(x.get('file'))} -> {_f(y.get('file'))}")
            for k, label, cls in (("offset", "3D model offset", "geometry-major"), ("rotate", "3D model rotation", "geometry-major"),
                                  ("scale", "3D model scale", "geometry-minor")):
                unit = [1, 1, 1] if k == "scale" else [0, 0, 0]
                xa, ya = x.get(k) or unit, y.get(k) or unit
                tol = TOL["model"] if k == "offset" else TOL["angle"] if k == "rotate" else 1e-4
                if any(abs(p - q) > tol for p, q in zip(xa, ya)):
                    _chg(out, cls, "model", label, xa, ya, f"{label} {_f(xa)} -> {_f(ya)}")


# ---------------------------------------------------------------- entry points

def _n_items(a, b):
    a, b = len(a or []), len(b or [])
    return f"{a} -> {b} items" if a != b else f"{a} item{'s' if a != 1 else ''}, moved or restyled"

def diff(old, new):
    """Fingerprints (ecadlib.fingerprint) -> [{class, area, what, old, new, text}], most severe first."""
    if old is None or new is None:
        raise ValueError("diff needs two fingerprints")
    if (old.get("hashes") or {}).get("digest") and old["hashes"]["digest"] == (new.get("hashes") or {}).get("digest"):
        return []
    out = []
    _identity(old, new, out)
    _symbol(old, new, out)
    _footprint(old, new, out)
    _pins_pads(old, new, out)
    _model(old, new, out)
    _fields(old, new, out)
    seen, uniq = set(), []
    for c in out:
        k = (c["class"], c["text"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return sorted(uniq, key=lambda c: RANK[c["class"]])


def worst(changes):
    return min((c["class"] for c in changes), key=RANK.get, default=None)


def counts(changes):
    return {c: n for c in CLASSES if (n := sum(x["class"] == c for x in changes))}


def summary(changes, n=3):
    """One line for people: the most severe changes first, e.g. 'pin 2 was G now R; pad 3 moved 0.65 mm in x (+4 more)'."""
    if not changes:
        return "no change"
    head = "; ".join(c["text"] for c in changes[:n])
    return head + (f" (+{len(changes) - n} more)" if len(changes) > n else "")
