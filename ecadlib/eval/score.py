"""Score one generated part against its reference: footprint (pads, pin 1, pitch, courtyard) and symbol pins.

Both footprints are footprint_bench.numbers() dicts (pads [num, x, y, w, h, drill], courtyard box). Thermal vias (a
drilled pad sitting inside a larger pad of the same number) are dropped on both sides first: they are a layout
choice, not part of the land pattern. Ours is then moved so the centre of its pad box lands on the reference's
(a different origin is reported in `origin` but does not fail the case: KiCad puts THT origins on pin 1, others
use the body centre). Fields, each {ok, error, ...}:
  pad_count     same number of pads and the same pad numbers (lists the missing / extra ones)
  rotation      (information only) ours turned by a quarter turn to lie as the reference does, when that matches its
                pads better: a footprint is placed at any angle, so a maker's land drawn another way round is the same
                land (C3D02065E: Wolfspeed draws the DPAK leads down, KiCad's TabPin1 left). Never mirrored
  renumbered    (information only) ours has a pad the reference numbers otherwise, joined to one of its pins (a tab
                numbered 3 on the cathode's net where KiCad numbers it 1, as the symbol rule for an exposed pad)
  pin1          pad 1 (A1 for ball grids) within TOL["pin1"] of the reference's: a mirrored part fails
  pitch         the most common nearest-neighbour spacing within TOL["pitch"]
  pads          every pad matched by number: centre within TOL["pos"], size within TOL["size"], drill within TOL["drill"]
  courtyard     every edge of the courtyard box within TOL["courtyard"]
  symbol_pins   (when the reference knows the pins) the same pin numbers
  symbol_names  (when the reference names its pins) at least TOL["names"] of the names agree after normalising
                (case, overbars ~{X} / X\\ / a combining bar, spaces, '/', '#', leading 'n')
A case passes when every field present passes (origin is information only)."""
import math, re
from collections import Counter

TOL = {"pos": 0.05, "size": 0.10, "drill": 0.05, "pitch": 0.01, "courtyard": 0.25, "pin1": 0.25, "names": 0.8}
INFO_ONLY = ("origin", "rotation", "renumbered")


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


def _turn(num, k):
    """numbers() dict turned k quarter turns about the origin ((x, y) -> (-y, x) each, KiCad's y down: a quarter turn
    clockwise on screen), pad sizes swapped with it. A turn, never a mirror: the pads keep their order around the part."""
    pads, c = [list(p) for p in num["pads"]], list(num.get("courtyard") or [])
    for _ in range(k % 4):
        pads = [[p[0], -p[2], p[1], p[4], p[3], p[5]] for p in pads]
        c = [-c[3], c[0], -c[1], c[2]] if c else []
    return {"pads": pads, "courtyard": c}


def _fit(mine, ref):
    """How far ours lies from the reference with the pad-box centres aligned: the sum, over the reference's pads, of the
    distance to our nearest pad of the same number (missing numbers cost nothing: pad_count reports them)."""
    (mx, my), (rx, ry) = _centre(mine["pads"]), _centre(ref["pads"])
    total = 0.0
    for r in ref["pads"]:
        d = [math.hypot(p[1] - mx - r[1] + rx, p[2] - my - r[2] + ry) for p in mine["pads"] if p[0] == r[0]]
        total += min(d) if d else 0.0
    return total


def _pin1_off(mine, ref):
    """How far our pad 1 lies from the reference's, pad-box centres aligned (inf when either has none)."""
    p, q = pin1(mine["pads"]), pin1(ref["pads"])
    if p is None or q is None:
        return math.inf
    (mx, my), (rx, ry) = _centre(mine["pads"]), _centre(ref["pads"])
    return max(abs(p[1] - mx - q[1] + rx), abs(p[2] - my - q[2] + ry))


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


OVERLINE = dict.fromkeys(map(ord, "\u0305\u0304"))     # a combining overline / macron: the bar PDF text gives (AM26LS31CN G̅)


NC_RX = re.compile(r"^(NC|DNC|N/C)$")                   # norm_name() of a no-connect pin's name
# one pin, two spellings (norm_name): the plain ground (a maker's VSS is KiCad's GND, ENS210; a GND exposed pad KiCad's
# GNDPAD, LTC4060EDHC; AGND, PGND, DGND ... are nets of their own and are not in it), and a regulator's input and output
# (AP1117's VIN / VOUT, KiCad's VI / VO), and the exposed pad with no net in its name (MC33063MNTXG: ours EP, KiCad's
# PAD); unswapped() keeps them apart where a symbol swaps two of them
PLAIN_GROUND = {"GND", "VSS", "GNDPAD"}
SPELLINGS = (PLAIN_GROUND, {"VIN", "VI"}, {"VOUT", "VO"}, {"PAD", "EP", "EPAD", "EXPOSEDPAD", "THERMALPAD"})


def same_name(ours, ref, groups=SPELLINGS):
    """Two norm_name() pin names for the same pin. groups: the SPELLINGS that count as one name here (unswapped)."""
    return ours == ref or any(ours in g and ref in g for g in groups)


def unswapped(pairs):
    """The SPELLINGS groups a symbol does not swap: [(ours, KiCad's)] per pin, norm_name()d. Two spellings of one group
    each on the other's pin (IR4301: KiCad's GND on pin 2 and VSS on pin 7, ours VSS on 2 and GND on 7) are two
    connections swapped, not one name spelled two ways; one of ours on two of KiCad's (LTC4060EDHC: GND on 16 and on
    the pad 17, KiCad's GND and GNDPAD) is one net."""
    moved = {(a, b) for a, b in pairs if a != b}
    return tuple(g for g in SPELLINGS if not any(a in g and b in g and (b, a) in moved for a, b in moved))


def norm_name(s):
    s = str(s or "").translate(OVERLINE).upper().replace("~{", "").replace("_{", "").replace("^{", "").replace("}", "").replace("\\", "").replace(" ", "")
    s = s.replace("~", "")
    s = re.sub(r"^(N(?=[A-Z]{2,})|/|#)", "", s)
    s = re.sub(r"[/#*_-]", "", s)
    s = re.sub(r"^N\.C\.?$", "NC", s)                 # "N.C." is KiCad's "NC"
    return WORDS.get(s, s)                            # a pin spelled out ("Enable") is the same pin as KiCad's "EN"


def _abbrev(word, short):
    """short is word cut down: its first letter, then letters of it in order (HS for HEATSINK, SW for SWITCH)."""
    if not short or not word or short[0] != word[0]:
        return False
    it = iter(word)
    return all(c in it for c in short)


def _from_words(words, short):
    """short built from every word in order, each giving a prefix or its trailing digits (SwC from Switch Collector,
    C2 from collector TR2, TC from Timing Capacitor)."""
    if not words:
        return not short
    w, rest = words[0], words[1:]
    digits = re.search(r"\d+$", w)
    cuts = [w[:i] for i in range(len(w), 0, -1)] + ([digits.group()] if digits else [])
    return any(short.startswith(c) and _from_words(rest, short[len(c):]) for c in cuts)


def short_form(ours, ref):
    """KiCad's name a short form of the maker's spelled-out one that our symbol carries (MC33063 "Switch Collector" /
    SwC, TPS70345 "GND/HEATSINK" / GND/HS, AP1117 "Adj (GND)" / GND, BCV61 "collector TR2" / C2). Never the same
    length: a name of the same size is another name, not a short form of it."""
    raw, r = str(ours or "").upper(), norm_name(ref)
    if not raw or not r or (rail(r) and rail(r) != rail(norm_name(raw)) and "(" not in raw and ";" not in raw):
        return False                                  # VSS is ground: letters of VSENSE in order are no short form
    parts = [x.strip() for x in re.split(r"[();]", raw) if x.strip()]
    if len(parts) > 1:                                # "Adj (GND)": the name the datasheet gives in brackets; BCV61
        return any(norm_name(x) == r or short_form(x, ref) for x in parts)   # "collector TR2; base TR1 and TR2"
    if len(norm_name(raw)) <= len(r):
        return False
    rt, ot = re.split(r"[/]", str(ref).upper()), re.split(r"[/]", raw)
    if len(rt) > 1 and len(rt) == len(ot):            # GND/HS: part by part
        return all(norm_name(x) == norm_name(y) or short_form(x, y) for x, y in zip(ot, rt))
    words = [w for w in re.split(r"[\s_-]+", raw) if w]
    one = norm_name(raw)                              # one word: letters from across it (HS), not just its start (IN is
    return _from_words(words, r) if len(words) > 1 else _abbrev(one, r) and not one.startswith(r)   # not INHIBIT)


GROUND_RX = re.compile(r"^(?:[ADPS]?GND\w*|[ADP]?VSS\w*|VEE\w*|0V|V-)$")
SUPPLY_RX = re.compile(r"^(?:[ADP]?VDD\w*|[ADP]?VCC\w*|VBAT\w*|V\+)$")


def one_of(ours, ref):
    """KiCad's name lists the pin's several names joined by "/" and ours is one of them (TPS70345's exposed pad: ours
    GND, KiCad's GND/HS; VSENSE1/FB1 read as FB1). A leading "/" is an active-low bar, not a second name. One
    name spelled with a slash is not two (Greptile #184: N/C is no-connect, not N or C; I/O is not I or O): each
    part must be a name of two letters or more."""
    if NC_RX.match(norm_name(ref)):
        return False
    parts = [norm_name(x) for x in str(ref or "").split("/") if x.strip()]
    o = norm_name(ours)
    return bool(o) and len(parts) > 1 and all(len(x) > 1 for x in parts) and o in parts


def rail(name):
    """'gnd' / 'supply' for a normalised power pin name, else None."""
    return "gnd" if GROUND_RX.match(name or "") else "supply" if SUPPLY_RX.match(name or "") else None


def _f(ok, error="", **kw):
    return dict(ok=bool(ok), error="" if ok else error, **kw)


def score_footprint(mine, ref, tol=None, renumber=None):
    """mine/ref: numbers() dicts -> {field: {...}}. renumber: {our pad number: the reference's} (joined_pads)."""
    t = dict(TOL, **(tol or {}))
    lay = mine.get("layers")
    mine, ref = drop_vias(mine), drop_vias(ref)
    out = {}
    if renumber:
        mine = dict(mine, pads=[[renumber.get(p[0], p[0])] + list(p[1:]) for p in mine["pads"]])
        out["renumbered"] = _f(False, "; ".join(f"our pad {a} scored as KiCad's {b} (the same pin's net)"
                                                for a, b in sorted(renumber.items())), pads=dict(renumber))
    if mine["pads"] and ref["pads"]:                  # the quarter turn that lays ours as the reference lies, if any:
        fits = [_fit(_turn(mine, k), ref) for k in range(4)]   # one that also brings pad 1 no further from KiCad's (a
        k = min(range(4), key=lambda i: (round(fits[i], 6), i))   # part with two pads swapped, AH1806-W, is not turned
        if k and fits[k] < fits[0] - 1e-6 and _pin1_off(_turn(mine, k), ref) <= _pin1_off(mine, ref) + 1e-6:   # round)
            mine = _turn(mine, k)
            out["rotation"] = _f(False, f"ours turned {90 * k} degrees to lie as KiCad's does", turns=k)
    if lay is not None:                               # ours only: the references keep no layers
        why = ([f"pads {lay['no_copper'][:8]} have no copper"] if lay["no_copper"] else []) + \
              ([f"pads {lay['no_mask'][:8]} have no solder-mask opening"] if lay["no_mask"] else []) + \
              ([f"{lay['stray_copper']} unnumbered copper pad(s)"] if lay["stray_copper"] else [])
        out["layers"] = _f(not why, "; ".join(why))
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
    row = 0.0                                         # worst offset along the pad rows, across the leads (only_maker_land)
    compared = 0
    for p in m["pads"]:
        cands = [r for r in left if r[0] == p[0]]
        if not cands:
            continue
        compared += 1
        r = min(cands, key=lambda r: math.hypot(r[1] - p[1], r[2] - p[2]))
        left.remove(r)
        e = {"pos": max(abs(p[1] - r[1]), abs(p[2] - r[2])), "size": max(abs(p[3] - r[3]), abs(p[4] - r[4])),
             "drill": abs(p[5] - r[5])}
        for k, v in e.items():
            worst[k] = max(worst[k], v)
        ex, ey = abs(p[1] - r[1]), abs(p[2] - r[2])     # KiCad's pad longer in x: its lead runs along x, the row along y
        row = max(row, ey if r[3] > r[4] + 1e-9 else ex if r[4] > r[3] + 1e-9 else max(ex, ey))
        why = [f"{k} off {v:.3f}" for k, v in e.items() if v > t[k] + 1e-9]
        if why:
            bad.append(f"pad {p[0] or '(hole)'}: " + ", ".join(why))
            bad_pads.append(p[0])
    out["pads"] = _f(not bad, f"{len(bad)} pads out of tolerance: " + "; ".join(bad[:4]) + (" ..." if len(bad) > 4 else ""),
                     worst={**{k: round(v, 4) for k, v in worst.items()}, "row": round(row, 4)}, bad=len(bad), bad_pads=bad_pads,
                     right=compared - len(bad), of=len(ref["pads"]))     # pad match: right / of (KiCad's pads)
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
    # a no-connect pin the datasheet lists and KiCad's symbol leaves out is not a wrong pin: KiCad's symbols keep NC
    # pins on 54 of the 331 kicad-v1 parts and drop them on others (LTC7810: 15 NC pins, live benchmark 2026-10-21)
    nc = [n for n in extra if NC_RX.match(norm_name(a[n]) or "")]
    extra = [n for n in extra if n not in nc]
    # the exposed pad as one more pin, on the net of a pin KiCad has (PCA9557BS: 17 VSS, with VSS on 6): KiCad's
    # symbol leaves the pad out because the pad joins that pin's net; a pad on a net of its own would be a real pin
    # One pad only, numbered right after KiCad's last pin: two extra pins (7 VSS, 8 VSS) are invented pins
    top = max((int(n) for n in b if n.isdecimal()), default=0)
    ep = [n for n in extra if n.isdecimal() and int(n) > top and norm_name(a[n])
          and any(norm_name(a.get(m)) == norm_name(a[n]) for m in b)]
    ep = ep if len(ep) == 1 and int(ep[0]) == top + 1 and len([n for n in extra if n.isdecimal() and int(n) > top]) == 1 else []
    extra = [n for n in extra if n not in ep]
    names = {}
    for n, s in mine_pins:                            # one pin number with two different names: two pins on one pad
        names.setdefault(str(n), set()).add(norm_name(s))
    twice = sorted((n for n, v in names.items() if len(v) > 1), key=_natural)
    out = {"symbol_pins": _f(not missing and not extra and not twice, f"ours {len(a)} pins, KiCad {len(b)}"
                             + (f"; missing {missing[:8]}" if missing else "") + (f"; extra {extra[:8]}" if extra else "")
                             + (f"; pin number used twice {twice[:8]}" if twice else ""),
                             ours=len(a), ref=len(b), **({"nc_not_in_kicad": nc} if nc else {}),
                             **({"ep_not_in_kicad": ep} if ep else {}))}
    named = {n: norm_name(s) for n, s in b.items() if norm_name(s) and not re.fullmatch(r"PIN\d+", norm_name(s))}
    if named:
        groups = unswapped([(norm_name(a.get(n)), s) for n, s in named.items()])
        same = [n for n, s in named.items() if same_name(norm_name(a.get(n)), s, groups) or short_form(a.get(n), b[n])
                or one_of(a.get(n), b[n])]
        diff = [f"{n}: {a.get(n)!r} vs {b[n]!r}" for n in sorted(set(named) - set(same), key=_natural)]
        once = Counter(named.values())
        moved = [n for n in set(named) - set(same) if once.get(norm_name(a.get(n))) == 1   # a name KiCad has on another pin:
                 or rail(named[n]) and rail(norm_name(a.get(n))) not in (None, rail(named[n]))]  # supply <-> ground: a short
        out["symbol_names"] = _f(len(same) >= t["names"] * len(named) and not moved,       # swapped pins, not naming noise
                                 f"{len(same)}/{len(named)} pin names agree"
                                 + (f" ({len(moved)} on the wrong pin)" if moved else "") + "; " + "; ".join(diff[:4]),
                                 matched=len(same), named=len(named), moved=len(moved))
    return out


def ep_numbers(num):
    """The exposed / thermal pads of a footprint: SMD pads more than twice the median pad's area."""
    pads = [p for p in (num or {}).get("pads") or [] if not p[5]]
    if len(pads) < 3:
        return []
    areas = sorted(p[3] * p[4] for p in pads)
    big = areas[len(areas) // 2] * 2
    return [p[0] for p in pads if p[3] * p[4] > big]


def _top_bottom(spec):
    """The top / bottom pins as the builder lays them out (footprint_gen.rows: 0 for a DFN read with only
    pins_left_right), or 1 (checked) when the read's pin count can't say."""
    from .. import footprint_gen as F
    try:
        return F.rows(spec)[1]
    except (KeyError, TypeError):
        return spec.get("pins_top_bottom", 1)


def maker_land(mine, spec, tol=None):
    """Does our footprint use the datasheet's recommended land (spec["land"]: pad_length x pad_width at +-pad_center,
    the maker's example board layout) rather than an IPC-7351B land worked out from the package dimensions? KiCad
    builds its land from the dimensions, so a maker's land can miss KiCad's pads while being what the datasheet asks
    for. -> None (no land given or no footprint), else {followed, pads: the pads that use it, ep: the exposed pad
    numbers (not part of the land), ep_land: those that are the land's thermal pad}. A land that sizes only the
    thermal pad (thermal_only) is followed when the exposed pad is it."""
    from .. import footprint_gen as F
    land = (spec or {}).get("land") or {}
    pl, pw, pc = (land.get(k) for k in ("pad_length", "pad_width", "pad_center"))
    ea, eb = land.get("ep_across"), land.get("ep_along")
    lead = all(isinstance(v, (int, float)) for v in (pl, pw, pc))
    thermal = isinstance(ea, (int, float)) and isinstance(eb, (int, float))
    if mine is None or not (lead or thermal):        # a land of the thermal pad only: the signal pads are IPC's
        return None
    t = dict(TOL, **(tol or {}))
    pcy = land.get("pad_center_y") if isinstance(land.get("pad_center_y"), (int, float)) else pc
    pads = [p for p in mine["pads"] if not p[5]]
    if not pads:
        return None
    cx, cy = _centre(pads)
    ep = [p for p in pads if p[0] in ep_numbers(mine)]
    near = lambda a, b, k: abs(a - b) <= t[k] + 1e-9
    tab = (spec.get("tab") or {}) if isinstance(spec.get("tab"), dict) else {}
    tl = [land.get(k) for k in F.TAB_LAND] if tab and all(isinstance(land.get(k), (int, float)) for k in F.TAB_LAND) else None
    tp = [p for p in pads if tl and str(p[0]) == str(tab.get("number"))]
    if tp and lead:                                   # the part's centre from the land, not the pads' box: the leads at
        t_ = max(tp, key=lambda p: p[3] * p[4])       # pad_center one side, the tab at its own tab_center the other
        xl = [p[1] for p in pads if p is not t_]
        if xl and (max(xl) < t_[1] or min(xl) > t_[1]):
            sg = 1 if t_[1] > xl[0] else -1
            cx = (sum(xl) / len(xl) + sg * pc + t_[1] - sg * tl[2]) / 2

    def on_tab_land(p):                               # the tab on the maker's tab land (SOT-223, DPAK)
        return bool(tl) and str(p[0]) == str(tab.get("number")) and near(p[3], tl[0], "size") and \
            near(p[4], tl[1], "size") and near(abs(p[1] - cx), tl[2], "pos")

    def fits(p):
        x, y = abs(p[1] - cx), abs(p[2] - cy)
        if on_tab_land(p):
            return True
        l, w, c, cyy = pl, pw, pc, pcy
        if str(p[0]) == "1" and (land.get("pin1_length") or land.get("pin1_width")):   # pin 1 drawn another size
            l1 = land.get("pin1_length") or pl                                        # (its outer end with the row's)
            c, cyy, l, w = pc + (pl - l1) / 2, pcy + (pl - l1) / 2, l1, land.get("pin1_width") or pw
        return (near(p[3], l, "size") and near(p[4], w, "size") and near(x, c, "pos")) or \
               (near(p[4], l, "size") and near(p[3], w, "size") and near(y, cyy, "pos"))
    rest = [p for p in pads if p not in ep]
    if not lead:                                      # the signal pads are IPC's, scored against KiCad as any;
        used = _ep_land(ep, rest, ea, eb, cx, cy, near)   # only the thermal pad is the maker's, when it can be:
        small = F.thermal_land_small(spec)                # never smaller than the package's pad
        return {"followed": bool(used) and not small, "pads": [], "ep": [p[0] for p in ep],
                "ep_land": [] if small else used, "thermal_only": True, **({"implausible": small} if small else {})}
    used = [p[0] for p in rest if fits(p)]
    tab_used = [p[0] for p in ep if on_tab_land(p)]   # a tab big enough to pass for an exposed pad
    # the land is the model's own reading, so "followed" alone cannot tell it from a misread: it must at least solder
    # the leads it read (reach the toe, start inside the shortest foot, be no narrower than the lead)
    rng = lambda k: [v for v in ((spec.get(k) or {}).get(x) for x in ("min", "nom", "max")) if isinstance(v, (int, float))] \
        if isinstance(spec.get(k), dict) else []
    # the toes at the span the builder uses: lead_span, or for a no-lead quad / dual (QFN, DFN: no lead_span) the
    # body's edge, body_length for the left / right rows and lead_span_y or body_width for the top / bottom ones
    quad = spec.get("package_type") in ("qfn", "qfp")
    span = rng("lead_span") or (rng("body_length") if quad else [])
    span_y = (rng("lead_span_y") or span) if spec.get("lead_span") else rng("body_width") if quad else []
    T, b = rng("terminal_length"), rng("terminal_width")
    covers = lambda l, w, c, s=span: c + l / 2 >= min(s) / 2 - t["pos"] and c - l / 2 <= max(s) / 2 - min(T) + t["pos"] \
        and w >= min(b) - 1e-9
    l1 = land.get("pin1_length") or pl                # a pin 1 of its own size must solder its lead too
    tb = quad and _top_bottom(spec) and span_y        # top / bottom rows (a DFN has none)
    sane = not (span and T and b) or (covers(pl, pw, pc) and covers(l1, land.get("pin1_width") or pw, pc + (pl - l1) / 2)
                                      and (not tb or covers(pl, pw, pcy, span_y)))
    # the maker's thermal land, when the land gives one: centred on the pads, ep_across running across the pad rows
    # (the lead pads' long side runs across the rows: pads wider than tall are rows at +-x, so across is the pad's
    # width; taller than wide, its height; both kinds, a quad package: either way round. Not the pads' extent: a long
    # TSSOP's rows reach further along y than the rows sit apart, TPS70345 PWP 3.575 vs 2.9)
    ep_used = _ep_land(ep, rest, ea, eb, cx, cy, near) if thermal and not F.thermal_land_small(spec) else []
    return {"followed": bool(rest) and len(used) == len(rest) and sane, "pads": used + tab_used, "ep": [p[0] for p in ep],
            "ep_land": ep_used,
            **({} if sane else {"implausible": "the land does not cover the leads read from the same drawing"})}


def _ep_land(ep, rest, ea, eb, cx, cy, near):
    """The exposed pads that are the land's thermal pad (ep_across x ep_along, centred on the pads)."""
    if not rest:
        return []
    wide, tall = sum(p[3] > p[4] for p in rest), sum(p[4] > p[3] for p in rest)
    ways = [(ea, eb)] if wide and not tall else [(eb, ea)] if tall and not wide else [(ea, eb), (eb, ea)]
    return [p[0] for p in ep if near(p[1], cx, "pos") and near(p[2], cy, "pos")
            and any(near(p[3], w, "size") and near(p[4], h, "size") for w, h in ways)]


def only_maker_land(fields, ml, body=None):
    """A KiCad miss explained by the maker's land: only pads (and the courtyard, which follows the pads) differ, and
    every pad that differs is one of the land's pads. An exposed pad that differs is a real miss unless it is the
    land's own thermal pad (ep_across x ep_along: ISL8117FVEZ, Renesas M16.173A 3 x 3, where KiCad's reference is a
    TI HTSSOP-16 with 3.4 x 5). The courtyard follows the body read too: when it differs, a body that body_evidence
    contradicts (body False) is a misread, not the land."""
    if not ml or not ml["followed"]:
        return False
    failed = {k for k, f in fields.items() if not f["ok"] and k not in INFO_ONLY}
    if not failed or not failed <= {"pads", "courtyard"} or ("courtyard" in failed and body is False):
        return False
    pads = fields.get("pads") or {}
    # a maker's land moves pads along their lead (pad_center), never into a neighbour's place: a pad off by half the
    # pitch or more along its row is on the wrong lead (AH1806-W 10-21: pads 2 and 3 swapped, 2.4 mm off, are not a
    # land choice). Either way: along the row only would miss pads swapped across the rows (an SOIC-8's 2 and 7 sit
    # at one y), and a fine-pitch land moved half the pitch out is then not counted (never a wrong part counted right)
    pitch, w = (fields.get("pitch") or {}).get("ref"), pads.get("worst") or {}
    if w.get("pos", 0) >= (pitch / 2 if pitch else 0.5):
        return False
    return set(pads.get("bad_pads") or []) <= set(ml["pads"]) | set(ml.get("ep_land") or [])


def _table_body(spec):
    """A pad table's body box (SM13B-GHS-TB: JST's B = 22.00 mm), in mm -> (size x, size y, the body's centre less
    the pads' centre), or None. The offset keeps a body that is not centred on the pads (a connector's housing
    reaching past one row) where the table put it."""
    bd = (spec.get("pad_table") or {}).get("body") or {}
    if not all(isinstance(bd.get(k), (int, float)) for k in ("x1", "x2", "y1", "y2")):
        return None
    from .. import footprint_adv as FA
    try:
        pads, body, *_ = FA.table_layout(spec)
    except (KeyError, TypeError, ValueError):
        return None
    pads = [p for p in pads if p["kind"] == "smd" or p["num"]]
    if not (body and pads):
        return None
    cx, cy = _centre([(p["num"], p["x"], p["y"]) for p in pads])
    return (body[2] - body[0], body[3] - body[1], (body[0] + body[2]) / 2 - cx, (body[1] + body[3]) / 2 - cy)


BODY_EXCESS_MAX = 0.6     # IPC-7351B courtyard excess is 0.1-0.5 mm, plus rounding out to 0.01 mm
NAME_BODY_TOL = 0.15      # KiCad names round the body (TSSOP-16_4.4x5mm for 4.9-5.1)
NAME_BODY_RX = re.compile(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:x\d+(?:\.\d+)?)?mm(?=_|$)")


def _body_ways(spec):
    """The body read from the datasheet -> [(along x, along y, the body's centre less the pads' centre)], each size
    (min, nom, max), either way round; or None. body_length / body_width centred on the pads, else a pad table's
    body box where the table puts it."""
    def rng(k):
        d = spec.get(k)
        v = [d.get(x) for x in ("min", "nom", "max")] if isinstance(d, dict) else []
        v = [x for x in v if isinstance(x, (int, float))]
        return (min(v), (d["nom"] if isinstance(d.get("nom"), (int, float)) else (min(v) + max(v)) / 2), max(v)) if v else None
    L, W = rng("body_length"), rng("body_width")
    if L and W:
        return [(L, W, 0.0, 0.0), (W, L, 0.0, 0.0)]
    box = _table_body(spec)
    if not box:
        return None
    (bx, by, ox, oy) = box
    L, W = (bx,) * 3, (by,) * 3
    # the table's frame may be mirrored or turned against ours: the offset goes with the body, either sign
    return [(L, W, sx * ox, sy * oy) for sx in (1, -1) for sy in (1, -1)] + \
           [(W, L, sx * oy, sy * ox) for sx in (1, -1) for sy in (1, -1)]


def body_evidence(spec, ref_name=None, ref=None):
    """Does the body we read agree with evidence that is not our own reading? The courtyard always fits the body we
    read, so a misread body (SOIC-16 read 12.0 long, really 9.9) would otherwise count as right by the datasheet.
    1. KiCad's reference name gives the body (SOIC-16_3.9x9.9mm, TSSOP-16_4.4x5mm, MSOP-8_3x3mm): the read's
       min..max must hold it, either way round, within NAME_BODY_TOL.
    2. Else KiCad's courtyard (its pads plus some body) must be no larger than its pads and the largest body we read
       plus the IPC excess: a body read too small is the dangerous misread. (A body read too large is not caught
       here: KiCad's courtyard can be smaller than the real part, SM13B-GHS-TB, so it is no bound from below.)
    -> True (agrees), False (contradicted), None: no independent evidence (no body read, no body in the name, no
    KiCad courtyard), where the read body is taken on its own, as before."""
    ways = _body_ways(spec or {})
    if not ways:
        return None
    m = NAME_BODY_RX.search(str(ref_name or "").split(":")[-1])
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        holds = lambda r, v: r[0] - NAME_BODY_TOL <= v <= r[2] + NAME_BODY_TOL
        return any(holds(bx, a) and holds(by, b) or holds(bx, b) and holds(by, a) for bx, by, _, _ in ways)
    pads = [p for p in ((ref or {}).get("pads") or []) if not p[5] or p[0]]
    rc = (ref or {}).get("courtyard")
    if not (pads and rc):
        return None
    x0, y0 = min(p[1] - p[3] / 2 for p in pads), min(p[2] - p[4] / 2 for p in pads)
    x1, y1 = max(p[1] + p[3] / 2 for p in pads), max(p[2] + p[4] / 2 for p in pads)
    pcx, pcy = _centre(pads)
    e = BODY_EXCESS_MAX + TOL["courtyard"]
    for bx, by, ox, oy in ways:
        cx, cy = pcx + ox, pcy + oy
        hi = [min(x0, cx - bx[2] / 2), min(y0, cy - by[2] / 2), max(x1, cx + bx[2] / 2), max(y1, cy + by[2] / 2)]
        if rc[0] >= hi[0] - e - 1e-6 and rc[1] >= hi[1] - e - 1e-6 and rc[2] <= hi[2] + e + 1e-6 and rc[3] <= hi[3] + e + 1e-6:
            return True
    return False


def datasheet_body(fields, mine, spec, ref_name=None, ref=None):
    """A KiCad miss on the courtyard alone, where ours is the IPC courtyard around our pads and the body the datasheet
    gives (from body_length / body_width, centred on the pads, either way round; or a pad table's body box where the
    table puts it). KiCad sizes some courtyards from a nominal body per pin count
    (every DIP-16 is 20.32 mm long, every DIP-28 35.56 mm), which can be smaller or larger than the real part.
    The read body earns the credit only when body_evidence (KiCad's name, or its courtyard; ref_name / ref) does not
    contradict it. -> None when it does not apply, else {"body": [length, width] used}."""
    failed = {k for k, f in fields.items() if not f["ok"] and k not in INFO_ONLY}
    if failed != {"courtyard"} or not mine or not mine.get("courtyard") or not spec:
        return None
    ways = _body_ways(spec)
    if not ways or body_evidence(spec, ref_name, ref) is False:
        return None
    pads = [p for p in mine["pads"] if not p[5] or p[0]]
    if not pads:
        return None
    x0, y0 = min(p[1] - p[3] / 2 for p in pads), min(p[2] - p[4] / 2 for p in pads)
    x1, y1 = max(p[1] + p[3] / 2 for p in pads), max(p[2] + p[4] / 2 for p in pads)
    pcx, pcy = _centre(pads)
    ca = mine["courtyard"]
    for bx, by, ox, oy in ways:
        cx, cy = pcx + ox, pcy + oy
        need = lambda i: [min(x0, cx - bx[i] / 2), min(y0, cy - by[i] / 2), max(x1, cx + bx[i] / 2), max(y1, cy + by[i] / 2)]
        hi, nom = need(2), need(1)               # must hold the largest body; may grow past the nominal by the excess
        out = [hi[0] - ca[0], hi[1] - ca[1], ca[2] - hi[2], ca[3] - hi[3]]
        over = [nom[0] - ca[0], nom[1] - ca[1], ca[2] - nom[2], ca[3] - nom[3]]
        if min(out) >= -1e-6 and max(over) <= BODY_EXCESS_MAX + 1e-6:
            return {"body": [bx[1], by[1]]}
    return None


def joined_pads(mine_fp, ref_fp, mine_pins, ref_pins, sym):
    """{our pad: the reference's} for the one pin the symbol score took as a pad on another pin's net (symbol_pins
    ep_not_in_kicad: C3D02065E's tab 3, CATHODE like pin 1) when the reference has no pad of that number and puts a
    second pad on the pin of that name (KiCad's TO-252-2_TabPin1: the tab is pad 1). None otherwise."""
    ep = ((sym or {}).get("symbol_pins") or {}).get("ep_not_in_kicad") or []
    if len(ep) != 1 or not mine_fp or not ref_fp:
        return None
    mine_names = {str(n): norm_name(s) for n, s in mine_pins or []}
    ref_nums = [str(n) for n, s in ref_pins or [] if norm_name(s) == mine_names.get(ep[0])]
    counts = Counter(p[0] for p in ref_fp["pads"])
    if len(ref_nums) != 1 or counts.get(ep[0]) or counts.get(ref_nums[0], 0) < 2:
        return None
    return {ep[0]: ref_nums[0]}


def verdict(fields):
    return all(f["ok"] for k, f in fields.items() if k not in INFO_ONLY)


def score(mine_fp, ref_fp, mine_pins=None, ref_pins=None, tol=None):
    """One case -> {pass, fields, errors}. mine_fp None = no footprint made."""
    sym = score_symbol(mine_pins, ref_pins, tol)
    if mine_fp is None:
        fields = {"footprint": _f(False, "no footprint")}
    else:
        fields = score_footprint(mine_fp, ref_fp, tol, joined_pads(mine_fp, ref_fp, mine_pins, ref_pins, sym))
    fields.update(sym)
    ok = verdict(fields)
    return {"pass": ok, "fields": fields, "errors": [f"{k}: {f['error']}" for k, f in fields.items() if not f["ok"] and k not in INFO_ONLY]}
