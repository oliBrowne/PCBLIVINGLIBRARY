"""Change classifier: two fingerprints of a part -> the list of what changed, each with a class and a plain sentence.

    from ecadlib import change_diff as CD
    changes = CD.diff(old_fp, new_fp)       [{class, area, what, old, new, text}], most severe first
    CD.worst(changes)                       the most severe class, or None when nothing changed

Classes, most severe first (default policy in brackets, see change_gate.POLICY):
  identity        MPN or manufacturer changed; the value as a number changed (10k -> 1k), its unit (100nF -> 100nH)
                  or what follows it (100nF 50V -> 100nF 6.3V); a second MPN / manufacturer field names another part [hold]
  electrical      a pin's name (as a signal) or type changed, pins swapped names, a pin or pad added or removed,
                  pads renumbered, a pin lost its pad, a pin became hidden, its hidden net, it moved to another unit,
                  the number of units, alternate pin functions; for every copy of a pin number; pins that now share
                  a pin end (they connect), pins of other names or types that no longer share one               [hold]
  geometry-major  a pad moved, rotated, changed side, drill, copper offset, copper layers or paste / mask on-off
                  (also all paste windows over a pad gone); a pad size changed beyond SIZE_MINOR; pad shape changed
                  (other than square <-> rounded corners); custom pad copper shape; zone connection; footprint copper
                  and copper zones; Edge.Cuts; KiCad assembly flags (exclude_from_bom, dnp ...); the footprint changed
                  to another land pattern; the 3D model's origin or rotation moved                              [hold]
  geometry-minor  pad size within SIZE_MINOR, corner rounding, paste / mask margins and windows, courtyard, inner-layer
                  copper of a THT pad, keepouts, footprint type and settings, pad property / pin function, the
                  footprint renamed with the same land pattern, 3D model added / removed / height / file; a symbol
                  pin end moved; power pins of one net and type no longer sharing an end, the number of display
                  modes (body styles) (both a warn at least); in a new body style or unit, pins that share an end
                  where body style 1 does not, or a pin that is another signal than in body style 1: electrical [warn]
  cosmetic        silkscreen, fab drawing, texts, symbol graphics, pin positions / lengths that keep the pin end in
                  place, pin shapes, a pin name written
                  another way (RESET# -> ~{RESET}), the value written another way (100nF -> 100n), other fields                                                                                  [pass]

Tolerances: a length change within TOL["noise"] (parity's position tolerance, 1 um) is no change; SIZE_MINOR is the
pad-size band that counts as minor (max(50 um, 10 % of that side)); 3D origin within TOL["model"] (10 um)."""
import json, re
from collections import Counter

from . import parity as PA

CLASSES = ("identity", "electrical", "geometry-major", "geometry-minor", "cosmetic")
RANK = {c: i for i, c in enumerate(CLASSES)}
TOL = {"noise": PA.TOL["position"], "angle": PA.TOL["rotation"], "model": 0.01, "corner": PA.TOL["corner"]}
SIZE_MINOR = {"abs": 0.05, "rel": 0.10}


def shown(s):
    """Text for a message with what cannot be seen made visible: a zero-width or other format character, a control
    character or an unusual space as <U+200B>, and the whole text quoted when it starts or ends with a space."""
    import unicodedata
    s = str(s)
    out = "".join(f"<U+{ord(ch):04X}>" if unicodedata.category(ch) in ("Cf", "Cc", "Zl", "Zp", "Co", "Cn") or
                  (unicodedata.category(ch) == "Zs" and ch != " ") else ch for ch in s)
    return f'"{out}"' if out != out.strip() else out


def _f(v):
    if isinstance(v, str):
        return shown(v) if v else "-"
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
DIELECTRICS = {"X5R", "X6S", "X7R", "X7S", "X7T", "X8R", "Y5V", "Z5U", "C0G", "COG", "NP0", "NPO", "U2J"}
PACKAGE_SIZES = {"01005", "0201", "0402", "0603", "0805", "1206", "1210", "1812", "2010", "2512", "1005", "1608",
                 "2012", "3216", "3225", "4532", "6332"}
KIND_UNIT = (("resistor", "R"), ("capacitor", "F"), ("inductor", "H"), ("ferrite", "H"), ("crystal", "Hz"),
             ("oscillator", "Hz"), ("resonator", "Hz"), ("zener", "V"), ("fuse", "A"))


def _value_tokens(v):
    """The text after a value's number, as canonical tokens: '10uF 25 V X7R' -> ['25V', 'X7R'], '10k 1/4W' ->
    ['0.25W']. A number and its unit written apart ('25 V', '1 %') are one token."""
    from .kicad_index import split_value
    try:
        rest = split_value(v)[2]
    except Exception:
        return []
    rest = re.sub(r"(\d+)\s*/\s*(\d+)\s*W\b", lambda m: f"{int(m.group(1)) / max(int(m.group(2)), 1):g}W", rest)
    rest = re.sub(r"(?i)(\d)\s+(%|ppm|°C|mV|kV|V|mW|W|mA|A)(?![A-Za-z0-9])", r"\1\2", rest.replace("+/-", "±"))
    toks = []
    for t in re.split(r"[\s/,;]+", rest):
        if not t:
            continue
        if t.upper() in PACKAGE_SIZES or t.upper() in DIELECTRICS:
            toks.append(t.upper())
            continue
        try:
            y, u, left = split_value(t)
        except Exception:
            y, u, left = None, "", t
        toks.append(f"{y:.6g}{u}" if y is not None and not left else t.upper())
    return toks


def _detail_token(t):
    """A token that only says more about the same part: a tolerance, a voltage, power or current rating, a temperature
    coefficient or rating, a dielectric, a package size. Anything else after a value (DNF, NC, ||1k, 0R) is not."""
    if t in DIELECTRICS or t in PACKAGE_SIZES:
        return True
    if re.fullmatch(r"[±+-]?\d+(\.\d+)?%|[±+-]?\d+(\.\d+)?PPM(/°?C|/K)?|[+-]?\d+(\.\d+)?°C", t):
        return True
    return bool(re.fullmatch(r"\d+(\.\d+)?(e[+-]?\d+)?[VWA]", t))


def _kind_unit(fp):
    """The unit a part's value is in, from what it is ('Resistor' symbol, 'Ceramic capacitor' description), or None."""
    fields = fp.get("fields") or {}
    text = " ".join([str((fp.get("symbol") or {}).get("name") or ""), str(fields.get("Description") or ""),
                     str(fields.get("Comment") or "")]).lower()
    return next((u for k, u in KIND_UNIT if k in text), None)


OHM_TOKENS = ("OHM", "OHMS", "Ω", "R")


def _unit_and_tokens(v, unit):
    """A value's unit and the tokens after it, an ohm sign written apart ('10k Ohm', '10k Ω') read as the unit."""
    toks = _value_tokens(v)
    if not unit and toks and toks[0] in OHM_TOKENS:
        return "R", toks[1:]
    return unit, toks


def _rest_change(ta, tb, same_mpn):
    """Value tokens after the number, a -> b: None when they say the same, 'more' when b only adds detail tokens,
    'less' when b drops some and the MPN is the same, else 'other' (an identity change)."""
    ta, tb = Counter(ta), Counter(tb)
    if ta == tb:
        return None
    added, dropped = tb - ta, ta - tb
    if not dropped and all(_detail_token(t) for t in added):
        return "more"
    if not added and same_mpn and all(_detail_token(t) for t in dropped):
        return "less"
    return "other"


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
    (ua, ta), (ub, tb) = _unit_and_tokens(va, io.get("value_unit")), _unit_and_tokens(vb, inn.get("value_unit"))
    mpn = (io.get("mpn") or "").strip().upper()
    same_mpn = bool(mpn) and mpn == (inn.get("mpn") or "").strip().upper()
    rest = _rest_change(ta, tb, same_mpn) if na is not None and nb is not None else None
    ra, rb = _readings(va, na), _readings(vb, nb)
    if na is not None and nb is not None and va != vb and max(len(ra), len(rb)) > 1 and ra != rb:
        # '100,000nF' is 100 nF read as a decimal comma and 100 uF read as a thousands separator: when the two
        # values differ under either reading, it is a change (the MPN being the same does not decide which one)
        _chg(out, "identity", "identity", "value", va, vb, f"value changed from {_f(va)} to {_f(vb)} (a comma before "
             "3 digits reads two ways)")
    elif na is not None and nb is not None:
        if abs(na - nb) > 1e-9 * max(abs(na), abs(nb), 1e-30):
            _chg(out, "identity", "identity", "value", va, vb, f"value changed from {_f(va)} to {_f(vb)}")
        elif ua and ub and ua != ub:          # 100nF -> 100nH
            _chg(out, "identity", "identity", "value", va, vb, f"value changed from {_f(va)} to {_f(vb)} (another unit)")
        elif ub and not ua and ub != (_kind_unit(n) or _kind_unit(o) or ("R" if ub == "R" else None)):
            # 100n -> 100nF only says the unit of a capacitor; 10 -> 10V on a resistor (or a part of unknown kind) is
            # another value; an ohm sign (10k -> 10k Ohm / 10kΩ) says resistor, unless the part is known to be another
            _chg(out, "identity", "identity", "value", va, vb, f"value changed from {_f(va)} to {_f(vb)} (a unit "
                 "that is not this part's)")
        elif rest == "other":                 # a rating after the value changed (50V -> 6.3V), or DNF / ||1k / 0R
            _chg(out, "identity", "identity", "value", va, vb, f"value changed from {_f(va)} to {_f(vb)}")
        elif rest:                            # 10k -> 10k 1%: more said, nothing contradicted
            _chg(out, "cosmetic", "identity", "value", va, vb, f"value written {_f(va)} -> {_f(vb)} (the same value, " +
                 ("more detail)" if rest == "more" else "the MPN is the same)"))
        elif va != vb:
            _chg(out, "cosmetic", "identity", "value", va, vb, f"value written {_f(va)} -> {_f(vb)} (the same value)")
    elif _plain(va) != _plain(vb):
        # a side ecadlib cannot read as a number (47kR, 1E4, 1uFd, C 1uF): the text is all there is to compare, so
        # any other text is another value, whatever the MPN says (an MPN can stay while the value field is retyped)
        _chg(out, "identity", "identity", "value", va, vb, f"value changed from {_f(va)} to {_f(vb)}"
             + (" (not read as a number, so compared as text)" if na is None or nb is None else ""))
    elif va != vb:
        _chg(out, "cosmetic", "identity", "value", va, vb, f"value written {_f(va)} -> {_f(vb)}")


def _plain(v):
    """A value's text with spaces and case folded: '10 kOhm' and '10KOHM' say the same."""
    return re.sub(r"\s+", "", str(v or "")).casefold()


def _readings(v, num):
    """The numbers a value can mean: {num}, and with a leading '100,000' (a comma before exactly 3 digits, which is a
    decimal comma in Europe and a thousands separator elsewhere) also the value read with the comma left out."""
    out = {num}
    m = re.match(r"\s*\d+,\d{3}(?![\d,.])", str(v or ""))
    if m:
        from .fingerprint import value_number
        alt = value_number(str(v).strip().replace(",", "", 1))
        if alt is not None:
            out.add(alt)
    return frozenset(out)


GENERIC_PN_KEYS = ("part number", "partnumber")
# numbered second sources (Altium's "Manufacturer Part Number 2", "Manufacturer 2"; also MPN2, Mfr PN 3 ...)
SECOND_SOURCE_RE = re.compile(r"(manufacturer part number|manufacturer_part_number|mfr\.? part number|mfr_?pn|mfr pn|"
                              r"mpn|manufacturer|mfr|mfg)[ _#-]*([2-9]|\d\d+)")


def _fields(o, n, out):
    """Other fields are cosmetic, except a second MPN or manufacturer column (MPN next to Manufacturer Part Number)
    whose new value is another part than the identity says: that is an identity change."""
    from .fingerprint import MFR_KEYS, MPN_KEYS
    from .kicad_index import norm_mpn
    fo, fn = o.get("fields") or {}, n.get("fields") or {}
    inn = n.get("identity") or {}
    for k in sorted(set(fo) | set(fn)):
        a, b = fo.get(k), fn.get(k)
        if a == b:
            continue
        txt = (f"field {k} added ({_f(b)})" if a is None else f"field {k} removed (was {_f(a)})" if b is None
               else f"field {k} changed from {_f(a)} to {_f(b)}")
        cls = "cosmetic"
        kl = str(k).strip().lower()
        filled = str(b or "").strip() not in ("", "~", "*")
        for keys, ident in ((MPN_KEYS, "mpn"), (MFR_KEYS, "manufacturer")):
            # a house "Part Number" next to the MPN is the library's own number, not a second MPN
            if kl in keys and kl not in GENERIC_PN_KEYS and filled and \
                    norm_mpn(b) != norm_mpn(inn.get(ident)) and norm_mpn(a) != norm_mpn(b):
                cls = "identity"
                txt += f" (the {'MPN' if ident == 'mpn' else 'manufacturer'} is {_f(inn.get(ident))})"
        if cls == "cosmetic" and filled and norm_mpn(a) != norm_mpn(b) and SECOND_SOURCE_RE.fullmatch(kl):
            cls = "identity"                   # Manufacturer Part Number 2: another part that may be fitted
            txt += " (a second source)"
        _chg(out, cls, "fields", k, a, b, txt)


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
    styles = _styles(po) & _styles(pn)        # a body style only one side has is the display modes line below
    pairs = []
    for c in common:
        io = [x for x in _insts(po[c]) if (x.get("style") or 1) in styles]
        inn = [x for x in _insts(pn[c]) if (x.get("style") or 1) in styles]
        multi = len(io) > 1 or len(inn) > 1
        both, gone_i, new_i = _pair_insts(io, inn)
        for a, b in both:
            pairs.append((c, a, b, _pin_label(c, b, multi)))
        for a in gone_i:
            _chg(out, "electrical", "symbol", f"pin {c}", _unit_s(a), None,
                 f"{_pin_label(c, a, True)} ({a.get('raw') or a['name'] or 'no name'}) removed")
        for b in new_i:
            _chg(out, "electrical", "symbol", f"pin {c}", None, _unit_s(b),
                 f"{_pin_label(c, b, True)} ({b.get('raw') or b['name'] or 'no name'}) added")
    renamed = [x for x in pairs if x[1]["name"] != x[2]["name"]]
    swap = len(renamed) > 1 and Counter(a["name"] for _, a, _, _ in renamed) == Counter(b["name"] for _, _, b, _ in renamed)
    for c, a, b, lab in renamed:
        _chg(out, "electrical", "symbol", f"pin {c}", a["name"], b["name"],
             f"{lab} was {a['raw'] or '(no name)'} now {b['raw'] or '(no name)'}" + (" (pins swapped names)" if swap else ""))
    moved, art, ends = [], [], []
    for c, a, b, lab in pairs:
        if a["name"] == b["name"] and a.get("raw") != b.get("raw"):
            if _net_pin(a) or _net_pin(b):     # a hidden or power pin joins the net of its exact name (case too)
                _chg(out, "electrical", "symbol", f"pin {c}", a.get("raw"), b.get("raw"),
                     f"{lab} name {_f(a.get('raw'))} -> {_f(b.get('raw'))} (a "
                     f"{'hidden' if a.get('hidden') or b.get('hidden') else 'power'} pin's name is the net it joins)")
            else:
                _chg(out, "cosmetic", "symbol", f"pin {c}", a.get("raw"), b.get("raw"),
                     f"{lab} name written {_f(a.get('raw'))} -> {_f(b.get('raw'))} (the same signal)")
        if a["type"] != b["type"] or (a.get("ktype") and b.get("ktype") and a["ktype"] != b["ktype"]):
            ta, tb = a.get("ktype") or a["type"], b.get("ktype") or b["type"]
            if not (a.get("ktype") and b.get("ktype")):
                ta, tb = a["type"], b["type"]
            _chg(out, "electrical", "symbol", f"pin {c}", ta, tb, f"{lab} ({b['raw'] or b['name']}) type {ta} -> {tb}")
        if bool(a.get("hidden")) != bool(b.get("hidden")):
            _chg(out, "electrical", "symbol", f"pin {c}", a.get("hidden"), b.get("hidden"),
                 f"{lab} ({b['raw'] or b['name']}) is {'hidden' if b.get('hidden') else 'visible'} now")
        elif (a.get("net") or None) != (b.get("net") or None):
            _chg(out, "electrical", "symbol", f"pin {c}", a.get("net"), b.get("net"),
                 f"{lab} hidden net {_f(a.get('net'))} -> {_f(b.get('net'))}")
        if (a.get("unit") or 0) != (b.get("unit") or 0):
            _chg(out, "electrical", "symbol", f"pin {c}", a.get("unit"), b.get("unit"),
                 f"pin {c} ({b['raw'] or b['name']}) moved from unit {_unit_n(a)} to unit {_unit_n(b)}")
        aa, ab = a.get("alt") or [], b.get("alt") or []
        if aa != ab:                           # KiCad alternate pin functions: another name and type on the same pin
            alts = lambda xs: ", ".join(f"{x[0] or '(no name)'} ({x[2] if len(x) > 2 else x[1]})" for x in xs) or "none"
            _chg(out, "electrical", "symbol", f"pin {c}", aa, ab,
                 f"{lab} ({b['raw'] or b['name']}) alternate functions {alts(a.get('alt'))} -> {alts(b.get('alt'))}")
        if a.get("end") is not None and b.get("end") is not None and a["end"] != b["end"]:
            ends.append(c)                     # where a placed schematic's wire meets the pin
        elif [a.get("at"), a.get("orient"), a.get("len")] != [b.get("at"), b.get("orient"), b.get("len")]:
            moved.append(c)
        if a.get("shape") != b.get("shape"):
            art.append(c)
    _stacked(so, sn, out)
    ends = list(dict.fromkeys(ends))
    moved, art = [c for c in dict.fromkeys(moved) if c not in ends], list(dict.fromkeys(art))
    if ends:
        _chg(out, "geometry-minor", "symbol", "pin ends", None, ends,
             f"pin end moved: {'pins' if len(ends) > 1 else 'pin'} {_few(ends)} (where a wire meets the pin; wires in "
             f"placed schematics may need moving)")
        out[-1]["drawing"] = True              # how the symbol is drawn: kicad_export.released_differs leaves it out
    if moved:
        _chg(out, "cosmetic", "symbol", "pin positions", None, moved, f"pin position / length / direction changed: {_few(moved)}")
    if art:
        _chg(out, "cosmetic", "symbol", "pin shapes", None, art, f"pin shape (bubble, clock) changed: {_few(art)}")
    if so.get("art") != sn.get("art"):
        _chg(out, "cosmetic", "symbol", "graphics", len(so.get("art") or []), len(sn.get("art") or []),
             f"symbol graphics changed ({_n_items(so.get('art'), sn.get('art'))})")
    if so.get("units", 1) != sn.get("units", 1):  # units are electrical: a placed unit gains or loses pins
        _chg(out, "electrical", "symbol", "units", so.get("units", 1), sn.get("units", 1),
             f"symbol units {so.get('units', 1)} -> {sn.get('units', 1)}")
    if so.get("modes", 1) != sn.get("modes", 1):  # a view placed schematics can switch to: a warn at least
        _chg(out, "geometry-minor", "symbol", "modes", so.get("modes", 1), sn.get("modes", 1),
             f"symbol display modes (body styles) {so.get('modes', 1)} -> {sn.get('modes', 1)}")
        out[-1]["least"] = "warn"
    _new_views(po, pn, out)


def _new_views(po, pn, out):
    """A body style only the new symbol has (an added De Morgan view): each pin's copy in it must be the same signal
    as its copy in body style 1 of the same unit (a placed symbol switched to the view keeps its wires)."""
    added = _styles(pn) - _styles(po)
    if not added:
        return
    for c in sorted(set(po) & set(pn), key=_natkey):
        base = {(x.get("unit") or 0): x for x in _insts(pn[c]) if (x.get("style") or 1) == 1}
        for x in _insts(pn[c]):
            b = base.get(x.get("unit") or 0)
            if (x.get("style") or 1) in added and b is not None and _esig(b) != _esig(x):
                show = lambda p: (f"{p.get('raw') or p['name'] or '(no name)'} ({p.get('ktype') or p['type']}"
                                  f"{', hidden' if p.get('hidden') else ''})")
                _chg(out, "electrical", "symbol", f"pin {c}", list(_esig(b)[:2]), list(_esig(x)[:2]),
                     f"pin {c} in body style {x.get('style')} (a new body style) is {show(x)}, in body style 1 {show(b)}")


def _joined(ends, keep):
    """{(unit, style, x, y): {pin numbers}} -> {(unit, style): [sets of pin numbers that are connected]}: pins on one
    end, and through a pin number with copies on two ends (one pin, one pad), the pins on both."""
    out = {}
    for k, v in ends.items():
        v = v & keep
        if not v:
            continue
        comps, merged = out.setdefault(k[:2], []), set(v)
        for c in [c for c in comps if c & merged]:
            comps.remove(c)
            merged |= c
        comps.append(merged)
    return out


def _pins_text(nums, pins):
    nums = sorted(nums, key=_natkey)
    names = {pins[n].get("raw") or pins[n].get("name") or "no name" for n in nums}
    if len(names) == 1:
        return f"{'pins' if len(nums) > 1 else 'pin'} {_few(nums)} ({names.pop()})"
    return _few([f"pin {n} ({pins[n].get('raw') or pins[n].get('name') or 'no name'})" for n in nums])


def _power_alike(nums, pins):
    """Pins of one net and one type, power (or hidden: joined by name): split apart they join no wrong net. The net is
    a hidden pin's net (Altium's HiddenNetName: AGND and DGND pins both named GND are two nets), else the name."""
    xs = [x for n in nums for x in _insts(pins[n])]
    return all(_net_pin(x) for x in xs) and len({(x.get("net") or x.get("raw"), x["type"], x.get("ktype")) for x in xs}) == 1


def _one_net(nums, pins):
    """Pins of one net name, at least one a power (or hidden) pin, whatever their types: the KLC stack of a power net
    (a visible power_in pin, passive copies of the same name). Drawn on one end they join no other net."""
    xs = [x for n in nums for x in _insts(pins[n])]
    return any(_net_pin(x) for x in xs) and len({x.get("net") or x.get("raw") for x in xs}) == 1


def _stacked(so, sn, out):
    """Pins that share a pin end are connected (KiCad and Altium join pins on one spot). Pins that now share an end are
    a new connection: electrical, always. Pins that no longer share one: power pins of one name and type (a stack the
    house rules redraw as separate pins) each need their own wire now, a warn at least whatever the policy; any other
    split loses a connection: electrical."""
    if "stacks" not in so or "stacks" not in sn:     # a fingerprint stored before schema 4 has no pin ends
        return
    from .fingerprint import pin_ends
    po, pn = so.get("pins") or {}, sn.get("pins") or {}
    keep = set(po) & set(pn)                          # an added or removed pin is reported as that
    eo, en = pin_ends(po, so.get("units", 1)), pin_ends(pn, sn.get("units", 1))
    go, gn = _joined(eo, keep), _joined(en, keep)
    multi = max(so.get("units", 1), sn.get("units", 1)) > 1
    for us in sorted(set(gn)):
        where = (f" in unit {us[0]}" if multi else "") + (f" (body style {us[1]})" if us[1] > 1 else "")
        new = gn[us]
        if us in go:
            old = go[us]
        else:                                         # a view or unit that is new: against body style 1 of the
            old = go.get((us[0], 1)) or []            # unit; in a new unit every stack is a new connection
            where = (f" in unit {us[0]}" if multi else "") + (f" (body style {us[1]}, a new one)" if (us[0], 1) in go
                                                              else " (a new unit)")
        for comp in new:                              # connected now: was it in pieces before?
            parts = [c & comp for c in old if c & comp]
            parts += [{n} for n in comp if not any(n in p for p in parts)]     # pins on no shared end before
            if len(parts) > 1:
                parts.sort(key=lambda c: (-len(c), _natkey(min(c, key=_natkey))))
                for p in parts[1:]:
                    _chg(out, "electrical", "symbol", "pin ends", None, sorted(comp, key=_natkey),
                         f"{_pins_text(p, pn)}{where} now {'share' if len(p) > 1 else 'shares'} a pin end with "
                         f"{_pins_text(parts[0], pn)}: they connect")
                    if _one_net(comp, pn):            # one net's pins drawn on one end (a KLC stack: a visible
                        out[-1]["drawing"] = True     # power_in pin, hidden passive copies): no wrong connection
        for comp in (old if us in go else []):        # connected before: in pieces now?
            parts = [c & comp for c in new if c & comp]
            if len(parts) <= 1:
                continue
            if _power_alike(comp, po) and _power_alike(comp, pn):
                spot = {k[2:] for k, v in eo.items() if k[:2] == us and v & comp}
                stay = next((p for p in parts if any(k[:2] == us and k[2:] in spot and v & p for k, v in en.items())), None)
                need = sorted(set(comp) - (stay or set()), key=_natkey)
                each = "each now needs its own wire" if len(need) == len(comp) else \
                    f"{'pins' if len(need) > 1 else 'pin'} {_few(need)} now {'need their own wires' if len(need) > 1 else 'needs its own wire'}"
                _chg(out, "geometry-minor", "symbol", "pin ends", sorted(comp, key=_natkey), need,
                     f"{_pins_text(comp, pn)}{where} no longer share one pin end: {each} in placed schematics "
                     f"(a separate pin connects only where it is wired)")
                out[-1].update(least="warn", drawing=True)    # never passes silently, whatever the policy
            else:
                parts.sort(key=lambda c: (-len(c), _natkey(min(c, key=_natkey))))
                for p in parts[1:]:
                    _chg(out, "electrical", "symbol", "pin ends", sorted(comp, key=_natkey), None,
                         f"{_pins_text(p, pn)}{where} no longer {'share' if len(p) > 1 else 'shares'} a pin end with "
                         f"{_pins_text(parts[0], pn)}: they are no longer connected")


def _net_pin(x):
    """A pin whose name is a net: hidden (KiCad connects a hidden power input to the net of its name, Altium a hidden
    pin to its HiddenNetName) or a power pin. Its exact spelling is electrical: KiCad nets are case-sensitive, and
    VCC, vcc, 'VCC ' and VCC# are four nets."""
    return bool(x.get("hidden")) or x.get("type") == "power" or str(x.get("ktype") or "").startswith("power")


def _insts(p):
    """Every copy of a pin number (a fingerprint from before copies were kept has the one)."""
    return p.get("inst") or [p]


def _styles(pins):
    return {x.get("style") or 1 for p in pins.values() for x in _insts(p)} or {1}


def _esig(x):
    return (x["name"], x["type"], x.get("ktype"), bool(x.get("hidden")), x.get("net") or None, json.dumps(x.get("alt") or []))


def _pair_insts(io, inn):
    """Copies of one pin number, old and new -> (pairs, old only, new only). One copy each side is a pair (a unit move
    shows as one); otherwise the same unit and body style pair first (an unchanged copy before a changed one), then
    what is left in the same body style, in order."""
    if len(io) == 1 and len(inn) == 1:
        return [(io[0], inn[0])], [], []
    pairs, ra, rb = [], list(io), list(inn)
    for key in (lambda x: (x.get("unit") or 0, x.get("style") or 1, _esig(x)), lambda x: (x.get("unit") or 0, x.get("style") or 1),
                lambda x: x.get("style") or 1):
        left = []
        for a in ra:
            k = next((i for i, b in enumerate(rb) if key(b) == key(a)), None)
            if k is None:
                left.append(a)
            else:
                pairs.append((a, rb.pop(k)))
        ra = left
    return pairs, ra, rb


def _unit_n(x):
    u = x.get("unit") or 0
    return "0 (common to all units)" if u == 0 else str(u)


def _unit_s(x):
    return x.get("unit")


def _pin_label(c, x, multi):
    lab = f"pin {c}"
    if multi:
        lab += f" in unit {_unit_n(x)}"
    if (x.get("style") or 1) > 1:
        lab += f" (body style {x.get('style')})"
    return lab


def _natkey(s):
    import re
    return [int(t) if t.isdecimal() else t for t in re.split(r"(\d+)", str(s))]


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
    _pad_more(a, b, out, what)


def _pad_more(a, b, out, what):
    """Copper layers, the custom copper shape, and the KiCad pad details (compared when both sides have them: another
    tool, or a fingerprint from before they were read, has None)."""
    ca, cb = a.get("cu"), b.get("cu")
    if ca is not None and cb is not None and ca != cb and a["side"] == b["side"]:   # (a side change says it)
        _chg(out, "geometry-major", "footprint", what, ca, cb, f"{what} copper layers {_layers(ca)} -> {_layers(cb)}")
    ia, ib = a.get("cu_in"), b.get("cu_in")
    if ia is not None and ib is not None and ia != ib and a["side"] == b["side"] == "THT":
        _chg(out, "geometry-minor", "footprint", what, ia, ib,
             f"{what} {'has' if ib else 'lost'} its copper on the inner layers (*.Cu -> F&B.Cu)" if not ib else
             f"{what} has copper on the inner layers now (F&B.Cu -> *.Cu)")
    pa, pb = a.get("prim"), b.get("prim")
    if pa and pb and pa[:2] == pb[:2] and pa != pb and a["shape"] == b["shape"] == "custom" and \
            not any(c["what"] == what and "outline" in c["text"] for c in out):
        _chg(out, "geometry-major", "footprint", what, None, None, f"{what} custom copper shape changed")
    za, zb = a.get("zone"), b.get("zone")
    if za is not None and zb is not None and za != zb:
        _chg(out, "geometry-major", "footprint", what, za, zb,
             f"{what} zone connection {_ZONE.get(za, za)} -> {_ZONE.get(zb, zb)}")
    for k, label in (("pinfunc", "pin function"), ("pintype", "pin type")):
        if a.get(k) and b.get(k) and a[k] != b[k]:      # boards fill these in from the symbol, libraries mostly do not
            _chg(out, "geometry-minor", "footprint", what, a[k], b[k], f"{what} {label} {a[k]} -> {b[k]}")
    if a.get("prop") is not None and b.get("prop") is not None and a["prop"] != b["prop"]:
        _chg(out, "geometry-minor", "footprint", what, a["prop"], b["prop"],
             f"{what} fabrication property {_f(a['prop'])} -> {_f(b['prop'])}")


_ZONE = {-1: "as the footprint", 0: "none", 1: "thermal relief", 2: "solid", 3: "thermal relief (THT only)"}


def _layers(cu):
    return " + ".join({"F": "front", "B": "back"}.get(x, x) for x in cu) or "none"


def _box(p):
    """A pad's copper bounding box (x1, y1, x2, y2)."""
    import math
    if p.get("w") is None or p.get("h") is None:
        return None
    r = math.radians(p.get("rot") or 0)
    hw = (abs(p["w"] * math.cos(r)) + abs(p["h"] * math.sin(r))) / 2
    hh = (abs(p["w"] * math.sin(r)) + abs(p["h"] * math.cos(r))) / 2
    return ((p["x"] or 0) - hw, (p["y"] or 0) - hh, (p["x"] or 0) + hw, (p["y"] or 0) + hh)


def _pasted(p, windows):
    """A surface pad gets solder paste: its own, or a paste window over it (an exposed pad's split paste)."""
    if p.get("paste"):
        return True
    b = _box(p)
    return bool(b) and any(w == "paste" and box and box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1]
                           for w, box in windows or [])


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
            if a["side"] in ("F", "B") and b["side"] in ("F", "B") and not a.get("paste") and not b.get("paste") and \
                    _pasted(a, fo.get("windows")) and not _pasted(b, fn.get("windows")):
                _chg(sub, "geometry-major", "footprint", f"pad {a['num'] or '(unnumbered)'}", True, False,
                     f"pad {a['num'] or '(unnumbered)'} has no solder paste now (its paste windows were removed)")
    if fo.get("windows") != fn.get("windows"):
        _chg(sub, "geometry-minor", "footprint", "windows", len(fo.get("windows") or []), len(fn.get("windows") or []),
             f"paste / mask windows changed ({len(fo.get('windows') or [])} -> {len(fn.get('windows') or [])})")
    if fo.get("copper") != fn.get("copper"):
        _chg(sub, "geometry-major", "footprint", "copper", len(fo.get("copper") or []), len(fn.get("copper") or []),
             "footprint copper (not pads) changed")
    ca, cb = fo.get("courtyard"), fn.get("courtyard")
    if (ca is None) != (cb is None) or ca and any(abs(p - q) > TOL["noise"] for p, q in zip(ca, cb)):
        _chg(sub, "geometry-minor", "footprint", "courtyard", ca, cb, f"courtyard {_f(ca)} -> {_f(cb)} mm")
    _foot_more(fo, fn, sub)
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
        if (ao.get(k) or []) != (an.get(k) or []):
            _chg(out, "cosmetic", "footprint", k, len(ao.get(k) or []), len(an.get(k) or []),
                 f"{label} changed ({_n_items(ao.get(k), an.get(k))})")
    if (ao.get("edge") or []) != (an.get("edge") or []):     # a cutout or slot the board is milled with
        _chg(out, "geometry-major", "footprint", "edge", len(ao.get("edge") or []), len(an.get("edge") or []),
             f"board edge (Edge.Cuts) in the footprint changed ({_n_items(ao.get('edge'), an.get('edge'))})")


# KiCad footprint attributes: what goes on the BOM and the pick-and-place file is a change to the assembly
_ASSEMBLY = {"exclude_from_bom": "excluded from the BOM", "exclude_from_pos_files": "excluded from the position files",
             "dnp": "do not populate", "board_only": "on the board only (not in the schematic)", "virtual": "virtual"}


def _foot_more(fo, fn, out):
    """KiCad footprint attributes, the settings every pad inherits, and zones (compared when both sides have them)."""
    aa, ab = fo.get("attr"), fn.get("attr")
    if aa is not None and ab is not None and aa != ab:
        for k, label in _ASSEMBLY.items():
            if (k in aa) != (k in ab):
                _chg(out, "geometry-major", "footprint", "attributes", k in aa, k in ab,
                     f"footprint {'now' if k in ab else 'no longer'} {label}")
        ta = [x for x in aa if x not in _ASSEMBLY]
        tb = [x for x in ab if x not in _ASSEMBLY]
        if ta != tb:
            _chg(out, "geometry-minor", "footprint", "attributes", ta, tb,
                 f"footprint attributes {_f(' '.join(ta))} -> {_f(' '.join(tb))}")
    sa, sb = fo.get("settings"), fn.get("settings")
    if sa is not None and sb is not None:
        for k in sorted(set(sa) | set(sb)):
            if sa.get(k) != sb.get(k):
                major = k == "zone_connect"
                va, vb = sa.get(k), sb.get(k)
                if major:
                    va, vb = _ZONE.get(-1 if va is None else int(va), va), _ZONE.get(-1 if vb is None else int(vb), vb)
                _chg(out, "geometry-major" if major else "geometry-minor", "footprint", k, sa.get(k), sb.get(k),
                     f"footprint {k.replace('_', ' ')} {_f(va)} -> {_f(vb)}")
    za, zb = fo.get("zones"), fn.get("zones")
    if za is not None and zb is not None and za != zb:
        ka, kb = [z for z in za if z.get("keepout")], [z for z in zb if z.get("keepout")]
        if ka != kb:
            _chg(out, "geometry-minor", "footprint", "keepouts", len(ka), len(kb), f"keepout zones changed ({_n_items(ka, kb)})")
        ca, cb = [z for z in za if not z.get("keepout")], [z for z in zb if not z.get("keepout")]
        if ca != cb:
            _chg(out, "geometry-major", "footprint", "zones", len(ca), len(cb), f"copper zones changed ({_n_items(ca, cb)})")


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


# ---------------------------------------------------------------- the KiCad footprint shipped besides the PcbLib
def _kicad(o, n, out):
    """The .kicad_mod the KiCad publishers ship (fingerprint["kicad"], else the footprint half): classified like the
    footprint and its 3D model, its pads against the symbol's pins too. A change the .PcbLib already shows is not
    repeated; the rest says "KiCad footprint". Adding or dropping the .kicad_mod compares it with the .PcbLib's land,
    which is then what kicad-export converts and ships."""
    if not o.get("kicad") and not n.get("kicad"):
        return
    bad_o, bad_n = (o.get("kicad") or {}).get("unreadable"), (n.get("kicad") or {}).get("unreadable")
    if bad_n and bad_o != bad_n:               # truncated, garbled, not text: KiCad would not load what ships
        _chg(out, "electrical", "kicad", "kicad_mod", None, bad_n, f"KiCad footprint (.kicad_mod) file unreadable "
             f"({bad_n}): its pads cannot be checked")
        return
    if bad_o and not bad_n:
        _chg(out, "geometry-major", "kicad", "kicad_mod", bad_o, None, "KiCad footprint (.kicad_mod) readable again")
    (fo, mo), (fn, mn) = _shipped(o), _shipped(n)
    sub = []
    _footprint({"footprint": fo}, {"footprint": fn}, sub)
    _pins_pads({"symbol": o.get("symbol"), "footprint": fo}, {"symbol": n.get("symbol"), "footprint": fn}, sub)
    _model({"model": mo}, {"model": mn}, sub)
    told = {(c["class"], c["text"]) for c in out}
    for c in sub:
        if (c["class"], c["text"]) not in told:
            out.append(dict(c, area="kicad", text=f"KiCad footprint (.kicad_mod): {c['text']}"))


def _shipped(fp):
    from .fingerprint import kicad_shipped
    return kicad_shipped(fp)


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
    _kicad(old, new, out)
    _fields(old, new, out)
    seen, uniq = set(), []
    for c in out:
        k = (c["class"], c["text"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return sorted(uniq, key=lambda c: RANK[c["class"]])


def diff_stored(old, new):
    """diff() of a fingerprint stored by an older ecadlib (another schema, no snapshot to read again) and one made now:
    the parts the old schema did not record (a section it had no key for) are left out, so only what both recorded is
    compared."""
    if old.get("schema") != new.get("schema"):
        new = {k: (v if old.get(k) is not None else old.get(k)) for k, v in new.items()}
    return diff(old, new)


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
