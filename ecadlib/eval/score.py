"""Score one generated part against its reference: footprint (pads, pin 1, pitch, courtyard) and symbol pins.

Both footprints are footprint_bench.numbers() dicts (pads [num, x, y, w, h, drill], courtyard box). Thermal vias (a
drilled pad sitting inside a larger pad of the same number) are dropped on both sides first: they are a layout
choice, not part of the land pattern. Ours is then moved so the centre of its pad box lands on the reference's
(a different origin is reported in `origin` but does not fail the case: KiCad puts THT origins on pin 1, others
use the body centre). Fields, each {ok, error, ...}:
  pad_count     same number of pads and the same pad numbers (lists the missing / extra ones)
  pin1          pad 1 (A1 for ball grids) within TOL["pin1"] of the reference's: a mirrored or rotated part fails
  pitch         the most common nearest-neighbour spacing within TOL["pitch"]
  pads          every pad matched by number: centre within TOL["pos"], size within TOL["size"], drill within TOL["drill"]
  courtyard     every edge of the courtyard box within TOL["courtyard"]
  symbol_pins   (when the reference knows the pins) the same pin numbers
  symbol_names  (when the reference names its pins) at least TOL["names"] of the names agree after normalising
                (case, overbars ~{X} / X\\, spaces, '/', '#', leading 'n')
A case passes when every field present passes (origin is information only)."""
import math, re
from collections import Counter

TOL = {"pos": 0.05, "size": 0.10, "drill": 0.05, "pitch": 0.01, "courtyard": 0.25, "pin1": 0.25, "names": 0.8}
INFO_ONLY = ("origin",)


def drop_vias(num):
    """numbers() dict without thermal vias (drilled pads inside a bigger pad of the same number)."""
    pads = num["pads"]
    keep = []
    for p in pads:
        if p[5] and any(q is not p and q[0] == p[0] and q[3] * q[4] > p[3] * p[4] * 2
                        and abs(p[1] - q[1]) <= q[3] / 2 + 1e-6 and abs(p[2] - q[2]) <= q[4] / 2 + 1e-6 for q in pads):
            continue
        keep.append(p)
    return {"pads": keep, "courtyard": list(num.get("courtyard") or [])}


def _centre(pads):
    xs, ys = [p[1] for p in pads], [p[2] for p in pads]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2) if pads else (0.0, 0.0)


def _shift(num, dx, dy):
    c = num["courtyard"]
    return {"pads": [[p[0], p[1] + dx, p[2] + dy, p[3], p[4], p[5]] for p in num["pads"]],
            "courtyard": [c[0] + dx, c[1] + dy, c[2] + dx, c[3] + dy] if c else []}


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def pin1(pads):
    named = [p for p in pads if p[0] in ("1", "A1")]
    if named:
        return min(named, key=lambda p: (p[0] != "1", p[3] * p[4]))       # '1' before 'A1'; the land, not a big EP
    rest = sorted((p for p in pads if p[0]), key=lambda p: _natural(p[0]))
    return rest[0] if rest else None


def pitch(pads):
    """Most common nearest-neighbour centre spacing (0.005 mm bins) among numbered pads."""
    pts = [(p[1], p[2]) for p in pads if p[0]]
    if len(pts) < 2:
        return None
    nn = []
    for i, (x, y) in enumerate(pts):
        d = min(math.hypot(x - u, y - v) for j, (u, v) in enumerate(pts) if j != i)
        if d > 1e-6:
            nn.append(round(d / 0.005) * 0.005)
    if not nn:
        return None
    best = Counter(nn).most_common()
    top = max(n for _, n in best)
    return round(min(v for v, n in best if n == top), 4)            # ties: the smaller spacing


WORDS = {"ENABLE": "EN", "GROUND": "GND", "INPUT": "IN", "OUTPUT": "OUT", "CATHODE": "K", "ANODE": "A"}


def norm_name(s):
    s = str(s or "").upper().replace("~{", "").replace("_{", "").replace("^{", "").replace("}", "").replace("\\", "").replace(" ", "")
    s = s.replace("~", "")
    s = re.sub(r"^(N(?=[A-Z]{2,})|/|#)", "", s)
    s = re.sub(r"[/#*_-]", "", s)
    s = re.sub(r"^N\.C\.?$", "NC", s)                 # "N.C." is KiCad's "NC"
    return WORDS.get(s, s)                            # a pin spelled out ("Enable") is the same pin as KiCad's "EN"


def _f(ok, error="", **kw):
    return dict(ok=bool(ok), error="" if ok else error, **kw)


def score_footprint(mine, ref, tol=None):
    """mine/ref: numbers() dicts -> {field: {...}}."""
    t = dict(TOL, **(tol or {}))
    mine, ref = drop_vias(mine), drop_vias(ref)
    out = {}
    nums = lambda n: Counter(p[0] or "(hole)" for p in n["pads"])
    a, b = nums(mine), nums(ref)
    missing, extra = sorted((b - a).elements(), key=_natural), sorted((a - b).elements(), key=_natural)
    out["pad_count"] = _f(not missing and not extra, f"ours {len(mine['pads'])} pads, KiCad {len(ref['pads'])}"
                          + (f"; missing {missing[:8]}" if missing else "") + (f"; extra {extra[:8]}" if extra else ""),
                          ours=len(mine["pads"]), ref=len(ref["pads"]))
    (mx, my), (rx, ry) = _centre(mine["pads"]), _centre(ref["pads"])
    dx, dy = rx - mx, ry - my
    out["origin"] = _f(max(abs(dx), abs(dy)) <= t["pos"], f"origin differs by ({-dx:+.3f}, {-dy:+.3f}) mm", dx=round(-dx, 4), dy=round(-dy, 4))
    m = _shift(mine, dx, dy)
    p, q = pin1(m["pads"]), pin1(ref["pads"])
    if p is None or q is None:
        out["pin1"] = _f(False, "no pad 1 in " + ("ours" if p is None else "KiCad's"))
    else:
        d = max(abs(p[1] - q[1]), abs(p[2] - q[2]))
        out["pin1"] = _f(d <= t["pin1"], f"pad {p[0]} at ({p[1]:.3f}, {p[2]:.3f}), KiCad's at ({q[1]:.3f}, {q[2]:.3f}) "
                         "(pad-box centres aligned): mirrored or rotated?", off=round(d, 4))
    pa, pb = pitch(m["pads"]), pitch(ref["pads"])
    if pb is None:
        out["pitch"] = _f(True)
    else:
        out["pitch"] = _f(pa is not None and abs(pa - pb) <= t["pitch"] + 1e-9, f"pitch {pa} mm, KiCad {pb} mm", ours=pa, ref=pb)
    left, worst, bad, bad_pads = list(ref["pads"]), {"pos": 0.0, "size": 0.0, "drill": 0.0}, [], []
    for p in m["pads"]:
        cands = [r for r in left if r[0] == p[0]]
        if not cands:
            continue
        r = min(cands, key=lambda r: math.hypot(r[1] - p[1], r[2] - p[2]))
        left.remove(r)
        e = {"pos": max(abs(p[1] - r[1]), abs(p[2] - r[2])), "size": max(abs(p[3] - r[3]), abs(p[4] - r[4])),
             "drill": abs(p[5] - r[5])}
        for k, v in e.items():
            worst[k] = max(worst[k], v)
        why = [f"{k} off {v:.3f}" for k, v in e.items() if v > t[k] + 1e-9]
        if why:
            bad.append(f"pad {p[0] or '(hole)'}: " + ", ".join(why))
            bad_pads.append(p[0])
    out["pads"] = _f(not bad, f"{len(bad)} pads out of tolerance: " + "; ".join(bad[:4]) + (" ..." if len(bad) > 4 else ""),
                     worst={k: round(v, 4) for k, v in worst.items()}, bad=len(bad), bad_pads=bad_pads)
    ca, cb = m["courtyard"], ref["courtyard"]
    if not cb:
        out["courtyard"] = _f(True)
    elif not ca:
        out["courtyard"] = _f(False, "no courtyard in ours")
    else:
        d = max(abs(x - y) for x, y in zip(ca, cb))
        out["courtyard"] = _f(d <= t["courtyard"], f"courtyard edge off by {d:.3f} mm (ours {[round(v, 3) for v in ca]}, "
                              f"KiCad {[round(v, 3) for v in cb]})", off=round(d, 4))
    return out


def score_symbol(mine_pins, ref_pins, tol=None):
    """Pin lists [(num, name)] -> {symbol_pins, symbol_names?}. mine_pins None = no symbol made."""
    t = dict(TOL, **(tol or {}))
    if not ref_pins:
        return {}
    if mine_pins is None:
        return {"symbol_pins": _f(False, "no symbol")}
    a, b = dict((str(n), s) for n, s in mine_pins), dict((str(n), s) for n, s in ref_pins)
    missing, extra = sorted(set(b) - set(a), key=_natural), sorted(set(a) - set(b), key=_natural)
    out = {"symbol_pins": _f(not missing and not extra, f"ours {len(a)} pins, KiCad {len(b)}"
                             + (f"; missing {missing[:8]}" if missing else "") + (f"; extra {extra[:8]}" if extra else ""),
                             ours=len(a), ref=len(b))}
    named = {n: norm_name(s) for n, s in b.items() if norm_name(s) and not re.fullmatch(r"PIN\d+", norm_name(s))}
    if named:
        same = [n for n, s in named.items() if norm_name(a.get(n)) == s]
        diff = [f"{n}: {a.get(n)!r} vs {b[n]!r}" for n in sorted(set(named) - set(same), key=_natural)]
        once = Counter(named.values())
        moved = [n for n in set(named) - set(same) if once.get(norm_name(a.get(n))) == 1]   # a name KiCad has on another pin:
        out["symbol_names"] = _f(len(same) >= t["names"] * len(named) and not moved,       # swapped pins, not naming noise
                                 f"{len(same)}/{len(named)} pin names agree"
                                 + (f" ({len(moved)} on the wrong pin)" if moved else "") + "; " + "; ".join(diff[:4]),
                                 matched=len(same), named=len(named), moved=len(moved))
    return out


def maker_land(mine, spec, tol=None):
    """Does our footprint use the datasheet's recommended land (spec["land"]: pad_length x pad_width at +-pad_center,
    the maker's example board layout) rather than an IPC-7351B land worked out from the package dimensions? KiCad
    builds its land from the dimensions, so a maker's land can miss KiCad's pads while being what the datasheet asks
    for. -> None (no land given or no footprint), else {followed, pads: the pads that use it, ep: the exposed pad
    numbers (not part of the land)}."""
    land = (spec or {}).get("land") or {}
    pl, pw, pc = (land.get(k) for k in ("pad_length", "pad_width", "pad_center"))
    if mine is None or not all(isinstance(v, (int, float)) for v in (pl, pw, pc)):
        return None
    t = dict(TOL, **(tol or {}))
    pcy = land.get("pad_center_y") if isinstance(land.get("pad_center_y"), (int, float)) else pc
    pads = [p for p in mine["pads"] if not p[5]]
    if not pads:
        return None
    cx, cy = _centre(pads)
    areas = sorted(p[3] * p[4] for p in pads)
    big = areas[len(areas) // 2] * 2
    ep = [p for p in pads if p[3] * p[4] > big]
    near = lambda a, b, k: abs(a - b) <= t[k] + 1e-9
    def fits(p):
        x, y = abs(p[1] - cx), abs(p[2] - cy)
        return (near(p[3], pl, "size") and near(p[4], pw, "size") and near(x, pc, "pos")) or \
               (near(p[4], pl, "size") and near(p[3], pw, "size") and near(y, pcy, "pos"))
    rest = [p for p in pads if p not in ep]
    used = [p[0] for p in rest if fits(p)]
    return {"followed": bool(rest) and len(used) == len(rest), "pads": used, "ep": [p[0] for p in ep]}


def only_maker_land(fields, ml):
    """A KiCad miss explained by the maker's land: only pads (and the courtyard, which follows the pads) differ, and
    every pad that differs is one of the land's pads (an exposed pad that differs is a real miss)."""
    if not ml or not ml["followed"]:
        return False
    failed = {k for k, f in fields.items() if not f["ok"] and k not in INFO_ONLY}
    if not failed or not failed <= {"pads", "courtyard"}:
        return False
    return set((fields.get("pads") or {}).get("bad_pads") or []) <= set(ml["pads"])


def verdict(fields):
    return all(f["ok"] for k, f in fields.items() if k not in INFO_ONLY)


def score(mine_fp, ref_fp, mine_pins=None, ref_pins=None, tol=None):
    """One case -> {pass, fields, errors}. mine_fp None = no footprint made."""
    if mine_fp is None:
        fields = {"footprint": _f(False, "no footprint")}
    else:
        fields = score_footprint(mine_fp, ref_fp, tol)
    fields.update(score_symbol(mine_pins, ref_pins, tol))
    ok = verdict(fields)
    return {"pass": ok, "fields": fields, "errors": [f"{k}: {f['error']}" for k, f in fields.items() if not f["ok"] and k not in INFO_ONLY]}
