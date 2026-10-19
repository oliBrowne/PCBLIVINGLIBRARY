"""A librarian plan's library as KiCad files: one symbol library, one footprint library, 3D models, lib tables.

    python3 -m ecadlib.kicad_export out/librarian/plan.json -o out/kicad --name mylib \
        [--lib examples/kicad_lib] [--include-held] [--cache out/http_cache] [--offline]

Writes into DIR:
  <lib>.kicad_sym    every part's symbol, named after the plan item (CAP-0402-100nF, STM32F103C8T6, ...), with
                     Reference, Value, Footprint "<lib>:<footprint>", Datasheet, Description, then Manufacturer, MPN,
                     the supplier part numbers (LCSC = C1525) and the plan's other parameters
  <lib>.pretty/      one .kicad_mod per footprint, renamed to the plan's footprint name
  <lib>.3dshapes/    the STEP models the footprints point at (${KIPRJMOD}/<lib>.3dshapes/X.step)
  <lib>.sim/         SPICE model files of parts with an attached simulation model (`ecadlib sim-models --attach`);
                     their symbols carry Sim.Device / Sim.Library "<lib>.sim/<file>" / Sim.Name / Sim.Pins / Sim.Params
  sym-lib-table, fp-lib-table   the library's row (uri ${KIPRJMOD}/...) added or replaced; other rows are kept, so
                     -o can be the KiCad project folder itself (or copy DIR's contents / the `lib` lines there)
  kicad_export.json  what came from where, and the checks below
Symbols, by the plan item's `source.symbol`:
  kicad:Lib:Name     the official KiCad symbol again (HTTP cache, then gitlab.com), `extends` chain flattened
  local:Lib:Name     the same from a --lib folder (Lib.kicad_sym)
  easyeda:Cxxx       the EasyEDA symbol converted again with easyeda_convert (the pin data the SchLib came from)
  anything else      (datasheet reader, generated, or a KiCad source that cannot be reached): the item's .SchLib
                     read back with altium_lib.read_schlib and drawn as a KiCad symbol (pins, rectangles, lines,
                     polygons, circles, arcs, text; parts -> units, display modes -> De Morgan body styles)
  A symbol taken again from its source must be the released revision: one whose pins, types or pin -> pad map
  differ from the item's .SchLib (an upstream change since the release) is not used; the .SchLib is read back and
  the report lists a warning (change control, #105).
Footprints: the item's .kicad_mod (models/KiCad/...) when present, else its .PcbLib records converted back.
Format: KiCad 8 (symbol library version 20231120, footprints up to 20240108), which KiCad 8, 9 and 10 open.
Newer upstream syntax is written the KiCad 8 way: `(hide yes)` -> `hide`, KiCad 9/10-only flags (in_pos_files,
show_name, do_not_autoplace, embedded_fonts, jumper settings, rule-area placement, rounded-rect radius, hatched
fill) are dropped. Held items are skipped unless --include-held. Output is sorted and repeatable: a re-export
rewrites only files whose content changes; a file edited by hand since the last export (kicad_export.json keeps the
sha256 of what it wrote) and one it did not write that is no longer wanted are copied to ecadlib-hand-edits/ and
listed under "warnings" before being replaced or removed.
Checks: every written file parses with ecadlib.sexpr; each footprint goes through release_check's footprint rule
(footprint_check) again and must not fail anything the plan's release check did not already record; symbol
pins and footprint pads are compared. Exit code 1 when a check fails.

KiCad's libraries are CC-BY-SA 4.0 (with a design-use exception): exports holding KiCad content get a NOTICE.txt
and must not be committed here (out/kicad/ is in .gitignore)."""
import argparse, copy, hashlib, json, math, re, struct, sys
from pathlib import Path

from . import altium_lib as AL, easyeda_convert as EC, footprint_check as FC, kicad_import as K, providers as P, \
    release_check as RC, sexpr
from .httpcache import CachedHTTP
from .sexpr import Q

SYM_VERSION, FP_VERSION = "20231120", 20240108          # KiCad 8 file formats
SYM_DROP = {"in_pos_files", "duplicate_pin_numbers_are_jumpers", "jumper_pin_groups", "embedded_fonts", "embedded_files",
            "show_name", "do_not_autoplace", "private", "id", "body_styles"}
FP_DROP = {"embedded_fonts", "embedded_files", "duplicate_pad_numbers_are_jumpers", "jumper_pad_groups", "component_classes"}
FP_NEWER = {"padstack", "tenting", "covering", "plugging", "filling", "capping", "units", "variant"}   # KiCad 9+: reported
HATCH = {"hatch", "reverse_hatch", "cross_hatch"}
KICAD_NOTICE = ("Parts of this library come from the KiCad libraries (https://gitlab.com/kicad/libraries), "
                "CC-BY-SA 4.0 with the KiCad libraries' design-use exception (see their LICENSE.md).\n")


def kname(s):
    """A symbol or footprint name KiCad accepts in a lib id and as a file name."""
    return re.sub(r'[\s:/\\<>"|?*\x00-\x1f]', "_", str(s)).strip("._") or "_"


def _num(v):
    return f"{round(float(v), 4) + 0:g}"


def _effects(hide=False, size=1.27):
    return ["effects", ["font", ["size", _num(size), _num(size)]]] + (["hide"] if hide else [])


def _prop(name, value, hide=True, at=(0, 0, 0)):
    return ["property", Q(name), Q(value), ["at", *map(_num, at)], _effects(hide)]


# ---------------------------------------------------------------- KiCad 9/10 syntax -> KiCad 8
def _is_yes(node):
    return len(node) < 2 or node[1] == "yes"


def sym_kicad8(node):
    """One symbol (or any node in it) written the KiCad 8 way; returns a new tree."""
    if not isinstance(node, list):
        return node
    out, hide = [node[0]], False
    for c in node[1:]:
        if not isinstance(c, list) or not c:
            out.append(c)
            continue
        if c[0] in SYM_DROP or (node[0] == "rectangle" and c[0] == "radius"):
            continue
        if c[0] == "hide":
            hide = hide or _is_yes(c)
            continue
        if node[0] == "fill" and c[0] == "type" and len(c) > 1 and c[1] in HATCH:
            c = ["type", "none"]
        if c[0] == "power":                   # KiCad 10 (power global|local) -> KiCad 8 (power)
            c = ["power"]
        out.append(sym_kicad8(c))
    if hide:
        if node[0] == "property":             # KiCad 9 (property ... (hide yes)) -> KiCad 8 (effects ... hide)
            eff = sexpr.first(out, "effects")
            if eff is None:
                out.append(_effects(True))
            elif "hide" not in eff:
                eff.append("hide")
        elif node[0] == "pin":                # bare hide after (length)
            i = next((k + 1 for k, c in enumerate(out) if isinstance(c, list) and c[0] == "length"), len(out))
            out.insert(i, "hide")
        else:
            out.append("hide")
    return out


def fp_kicad8(t, notes):
    """A footprint tree newer than KiCad 8 -> KiCad 8 (version 20240108): drop the KiCad 9/10-only nodes."""
    def walk(node, parent):
        if not isinstance(node, list):
            return node
        out = [node[0]]
        for c in node[1:]:
            if isinstance(c, list) and c:
                if c[0] in FP_DROP or (node[0] == "zone" and c[0] == "placement") or (node[0] == "footprint" and c[0] == "generator_version"):
                    continue
                if node[0] == "footprint" and c[0] == "point":   # KiCad 10 snap point: KiCad 7-9 refuse the whole file
                    K._note(notes, "KiCad 10 (point ...) markers dropped")
                    continue
                if c[0] in FP_NEWER:
                    K._note(notes, f"KiCad 9+ node ({c[0]} ...) kept: may not load in KiCad 8")
            out.append(walk(c, node[0]))
        return out
    t = walk(t, None)
    v = sexpr.first(t, "version")
    if v:
        v[1] = str(FP_VERSION)
    return t


# ---------------------------------------------------------------- symbols
def flatten(chain, name):
    """[symbol, parent, ...] -> one symbol `name`: the base's graphics, units renamed, properties merged
    (derived ones override)."""
    base = chain[-1]
    props, order = {}, []
    for s in reversed(chain):
        for p in sexpr.find(s, "property"):
            if p[1] not in props:
                order.append(p[1])
            props[p[1]] = copy.deepcopy(p)
    flags, units = [], []
    for c in base[2:]:
        if not isinstance(c, list) or c[0] in ("extends", "property"):
            continue
        if c[0] == "symbol":
            sub = str(c[1])
            suffix = sub[len(base[1]):] if sub.startswith(str(base[1]) + "_") else "_" + "_".join(sub.rsplit("_", 2)[1:])
            units.append([c[0], Q(name + suffix)] + copy.deepcopy(c[2:]))
        else:
            flags.append(copy.deepcopy(c))
    return ["symbol", Q(name)] + flags + [props[k] for k in order] + units


def plan_fields(item, lib, fp_name, props, rules=None):
    """Plan parameters -> ordered [(property, value)]: the five KiCad fields first, then the rest; other fields in the
    rules' house names ([parameters] names; MPN follows the name given for Manufacturer Part Number)."""
    par = {p["name"]: str(p["value"]) for p in item.get("parameters", []) if str(p.get("value", "")).strip()}
    used = {"Value", "Manufacturer", "Manufacturer Part Number", "ComponentLink2URL", "ComponentLink2Description", "Datasheet"}
    out = [("Reference", props.get("Reference", "U")), ("Value", par.get("Value") or item["name"]),
           ("Footprint", f"{lib}:{fp_name}" if fp_name else ""),
           ("Datasheet", par.get("ComponentLink2URL") or par.get("Datasheet") or (props.get("Datasheet", "") if props.get("Datasheet", "") != "~" else "")),
           ("Description", str(item.get("description") or "").strip() or props.get("Description") or props.get("ki_description", "")),
           ("Manufacturer", par.get("Manufacturer", "")), ("MPN", par.get("Manufacturer Part Number", ""))]
    for k in sorted(par):
        m = re.fullmatch(r"Supplier (\d+)", k)
        if m and par.get(f"Supplier Part Number {m.group(1)}"):
            out.append((par[k], par[f"Supplier Part Number {m.group(1)}"]))
            used |= {k, f"Supplier Part Number {m.group(1)}"}
        m = re.fullmatch(r"ComponentLink(\d+)URL", k)
        if m and k not in used:
            out.append((f"{par.get(f'ComponentLink{m.group(1)}Description') or 'Link' + m.group(1)} URL", par[k]))
            used |= {k, f"ComponentLink{m.group(1)}Description"}
    out += [(k, v) for k, v in sorted(par.items()) if k not in used and not re.fullmatch(r"(Supplier( Part Number)? \d+|ComponentLink\d+\w+)", k)]
    names = ((rules or {}).get("parameters") or {}).get("names") or {}
    if names:
        std = ("Reference", "Value", "Footprint", "Datasheet", "Description")
        out = [(k if k in std else names.get("Manufacturer Part Number", k) if k == "MPN" else names.get(k, k), v) for k, v in out]
    seen, res = set(), []
    for k, v in out:
        if k not in seen and (v or k in ("Reference", "Value", "Footprint", "Datasheet", "Description")):
            seen.add(k); res.append((k, v))
    return res


def set_fields(sym, fields):
    """Set each property's value (existing ones keep their position and visibility); new ones go hidden after them."""
    have = {p[1]: p for p in sexpr.find(sym, "property")}
    last = max((i for i, c in enumerate(sym) if isinstance(c, list) and c[0] == "property"), default=1)
    add = []
    for k, v in fields:
        if k in have:
            have[k][2] = Q(v)
        else:
            add.append(_prop(k, v, hide=k not in ("Reference", "Value")))
    sym[last + 1:last + 1] = add
    return sym


ETYPE_K = {0: "input", 1: "bidirectional", 2: "output", 3: "open_collector", 4: "passive", 5: "tri_state", 6: "open_emitter", 7: "power_in"}
SYMBOL_NAME = {v: k for k, v in AL.PIN_SYMBOL.items()}


def _pin_shape(symbols, num, notes):
    d = {slot: SYMBOL_NAME.get(v, v) for slot, v in zip(AL.PIN_SLOTS, symbols) if v}
    shape = next((k for k, v in AL.PIN_SHAPE.items() if v == d), None)
    if shape is None:
        K._note(notes, f"pin {num}: Altium pin symbols {d} have no KiCad pin shape; drawn as a plain line")
    return shape or "line"


def _kicad_pin_name(s):
    """Altium 'R\\E\\S\\E\\T\\' overbar -> KiCad '~{RESET}'."""
    out, i = "", 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == "\\":
            j += 2
        if j > i:
            out += "~{" + s[i:j:2] + "}"
            i = j
        else:
            out += s[i]; i += 1
    return out


def symbol_from_schlib(path, name, notes, pick=None):
    """First symbol of a .SchLib (or the one named pick) -> KiCad symbol tree (KiCad 8 syntax), via altium_lib.read_schlib."""
    _, syms = AL.read_schlib(path)
    if not syms:
        raise ValueError(f"{path}: no symbol")
    recs = next((s for s in syms if pick and s["name"].upper() == pick.upper()), syms[0])["records"]
    comp = recs[0]
    v = lambda r, k: (int(r.get(k.upper(), 0) or 0) + int(r.get(k.upper() + "_FRAC", 0) or 0) / 100000) * 0.254
    xy = lambda r, a, b: (_num(v(r, a)), _num(v(r, b)))
    lw = lambda r: ["stroke", ["width", {"0": "0", "1": "0.254", "2": "0.508", "3": "0.762"}.get(r.get("LINEWIDTH", "0"), "0")], ["type", "default"]]
    def fill(r):
        if r.get("ISSOLID") != "T":
            return ["fill", ["type", "none"]]
        return ["fill", ["type", "outline" if r.get("AREACOLOR") == str(AL.BLUE) else "background"]]
    units, pins, ref, value_at, ref_at, params = {}, [], "U", (0, 0), (0, 0), []
    owner = lambda r: (max(1, int(r.get("OWNERPARTID", r.get("part", 1)) or 1)), int(r.get("OWNERPARTDISPLAYMODE", r.get("mode", 0)) or 0) + 1)
    spots = {}                                # (part, mode, x, y, orient) -> first visible pin there
    hidden_net = {int(r["OWNERINDEX"]): r.get("TEXT", "") for r in recs if r.get("RECORD") == "41"
                  and r.get("NAME") == "HiddenNetName" and str(r.get("OWNERINDEX", "")).lstrip("-").isdigit()}
    for ri, r in enumerate(recs[1:], 1):
        kind = r.get("RECORD")
        if kind == "2":
            d = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}[r["orient"]]
            x, y, L = r["x"] * 0.254, r["y"] * 0.254, r["length"] * 0.254
            nm = _kicad_pin_name(r["name"]) or "~"
            if r["conglomerate"] & 0x04 and hidden_net.get(ri):   # Altium ties a hidden pin to its HiddenNetName net;
                nm = hidden_net[ri]                                # KiCad ties a hidden power pin to the net of its name
                K._note(notes, "hidden pins named after their Altium HiddenNetName (KiCad connects them by name)")
            spot = (r["part"], r["mode"], r["x"], r["y"], r["orient"])
            lead = spots.get(spot) if not r["conglomerate"] & 0x04 else None
            etype = ETYPE_K.get(r["etype"], "passive")
            if r["conglomerate"] & 0x04 and hidden_net.get(ri):
                etype = "power_in"                # only hidden power inputs join a net by name in KiCad
            if lead is not None:              # stacked on a visible pin (Altium joins pins at one point): KiCad shows one,
                etype = "passive" if lead["etype"] == 7 else etype      # the rest hidden, passive under a power pin (KLC S4.3)
                K._note(notes, "stacked pins written as one visible pin and hidden ones at the same spot")
            elif not r["conglomerate"] & 0x04:
                spots[spot] = r
            pin = ["pin", etype, _pin_shape(r["symbols"], r["num"], notes),
                   ["at", _num(x + L * d[0]), _num(y + L * d[1]), str((r["orient"] * 90 + 180) % 360)], ["length", _num(L)]]
            if r["conglomerate"] & 0x04 or lead is not None:
                pin.append("hide")
            pin += [["name", Q(nm), _effects()], ["number", Q(r["num"]), _effects()]]
            if r["etype"] == 7 and lead is None:
                K._note(notes, "Altium power pins written as KiCad power_in")
            units.setdefault((max(1, r["part"]), r["mode"] + 1), []).append(pin)
            pins.append(r)
            continue
        g = None
        if kind == "14":
            g = ["rectangle", ["start", *xy(r, "Location.X", "Location.Y")], ["end", *xy(r, "Corner.X", "Corner.Y")], lw(r), fill(r)]
        elif kind in ("6", "7"):
            pts = [xy(r, f"X{k}", f"Y{k}") for k in range(1, int(r.get("LOCATIONCOUNT", 0)) + 1)]
            if kind == "7" and pts:
                pts.append(pts[0])
            g = ["polyline", ["pts", *[["xy", *p] for p in pts]], lw(r), fill(r) if kind == "7" else ["fill", ["type", "none"]]]
        elif kind == "13":
            g = ["polyline", ["pts", ["xy", *xy(r, "Location.X", "Location.Y")], ["xy", *xy(r, "Corner.X", "Corner.Y")]], lw(r), ["fill", ["type", "none"]]]
        elif kind == "8":
            if r.get("SECONDARYRADIUS", r.get("RADIUS")) != r.get("RADIUS"):
                K._note(notes, "ellipse drawn as a circle")
            g = ["circle", ["center", *xy(r, "Location.X", "Location.Y")], ["radius", _num(v(r, "Radius"))], lw(r), fill(r)]
        elif kind == "12":
            cx, cy, rad = v(r, "Location.X"), v(r, "Location.Y"), v(r, "Radius")
            a0, a1 = float(r.get("STARTANGLE", 0) or 0), float(r.get("ENDANGLE", 360) or 0)
            span = (a1 - a0) % 360 or 360
            pt = lambda a: ["%s" % _num(cx + rad * math.cos(math.radians(a))), _num(cy + rad * math.sin(math.radians(a)))]
            if span >= 359.999:
                g = ["circle", ["center", _num(cx), _num(cy)], ["radius", _num(rad)], lw(r), ["fill", ["type", "none"]]]
            else:
                g = ["arc", ["start", *pt(a0)], ["mid", *pt(a0 + span / 2)], ["end", *pt(a1)], lw(r), ["fill", ["type", "none"]]]
        elif kind == "4":
            g = ["text", Q(r.get("TEXT", "")), ["at", *xy(r, "Location.X", "Location.Y"), "900" if r.get("ORIENTATION") in ("1", "3") else "0"], _effects()]
        elif kind == "34":
            ref, ref_at = r.get("TEXT", "U?").rstrip("?") or "U", (v(r, "Location.X"), v(r, "Location.Y"))
        elif kind == "41" and r.get("NAME") == "Comment":
            value_at = (v(r, "Location.X"), v(r, "Location.Y"))
        elif kind == "41" and r.get("NAME") not in ("HiddenNetName", None):
            params.append((r["NAME"], r.get("TEXT", "")))
        if g:
            units.setdefault(owner(r), []).append(g)
    if not pins:
        raise ValueError(f"{path}: symbol has no pins")
    show = [p for p in pins if not p["conglomerate"] & 0x04]
    sym = ["symbol", Q(name)]
    if show and not any(p["conglomerate"] & 0x10 for p in show):
        sym.append(["pin_numbers", "hide"])
    sym.append(["pin_names", ["offset", "1.016"]] + (["hide"] if show and not any(p["conglomerate"] & 0x08 for p in show) else []))
    sym += [["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"]]
    sym += [_prop("Reference", ref, False, (*ref_at, 0)), _prop("Value", name, False, (*value_at, 0)), _prop("Footprint", ""),
            _prop("Datasheet", ""), _prop("Description", comp.get("COMPONENTDESCRIPTION", ""))]
    sym += [_prop(k, val) for k, val in params if k not in ("Value", "Footprint", "Datasheet", "Description", "Reference", "KiCad Footprint")]
    for (u, st), items in sorted(units.items()):
        sym.append(["symbol", Q(f"{name}_{u}_{st}")] + items)
    if int(comp.get("PARTCOUNT", 2)) > 2:
        K._note(notes, f"{int(comp['PARTCOUNT']) - 1} Altium parts written as KiCad units")
    return sym


def released_differs(item, plan_dir, sym):
    """A symbol fetched again from the part's source (KiCad, --lib, EasyEDA) against the released revision, the item's
    .SchLib (publish_guard has put a held change back) -> the changes that are more than cosmetic (pin names and types,
    hidden pins, the pin -> pad map); [] when it is the released symbol; None when there is no .SchLib to compare with.
    The fetched symbol is compared as the librarian releases it (converted to Altium and read back), so what the
    conversion always does (stacked pins hidden, unit 0 pins in every part) is no difference."""
    import tempfile
    from . import change_diff as CD, fingerprint as FP
    f = (item.get("symbol") or {}).get("file")
    if not f or not (plan_dir / f).is_file():
        return None
    rel = FP.of_item(item, plan_dir)
    if rel.get("symbol") is None:
        return None
    try:
        conv = K.convert_symbol([sym], str(sym[1]), None, [], None, [])
        with tempfile.TemporaryDirectory(prefix="ecadlib-kx-") as tmp:
            _, entries = AL.read_schlib(AL.write_schlib(Path(tmp) / "fetched.SchLib", [conv]))
        fetched = FP.altium_symbol(entries[0])[0]
    except (ValueError, KeyError, IndexError, OSError) as e:
        return [{"class": "electrical", "text": f"the fetched symbol cannot be compared ({type(e).__name__}: {e})"}]
    got = copy.deepcopy(rel)
    got["symbol"] = dict(fetched, name=rel["symbol"].get("name"))
    got["hashes"] = FP.hashes(got)
    return [c for c in CD.diff(rel, got) if c["class"] != "cosmetic"]


def load_symbol(item, plan_dir, http, libs, name, notes, rules=None, warnings=None):
    """-> (KiCad symbol tree, how). Reuses KiCad / EasyEDA sources; else reads the SchLib back. An EasyEDA IC
    symbol (a plain box, [symbols] layout_pins) is read back from the SchLib, which the librarian wrote with its pins
    grouped (ecadlib.symbol_layout), so both libraries show the same symbol. A reused symbol must be the released
    revision (change control, #105): when the source changed since the release (an upstream pin swap), the released
    .SchLib is read back instead and the difference goes to `warnings`; upstream check-kicad --update or a librarian
    re-run brings the source's change through change control."""
    src = str((item.get("source") or {}).get("symbol") or "")
    why = []

    def released(sym, how):
        bad = released_differs(item, plan_dir, sym)
        if not bad:
            return sym, how
        from . import change_diff as CD
        why.append(f"{how} is not the released revision ({CD.summary(bad)})")
        if warnings is not None:
            warnings.append(f"{item['name']}: the symbol at its source, {how}, is not the released revision "
                            f"({CD.summary(bad)}); the released symbol ({item['symbol']['file']}) is exported. Bring "
                            f"the source's change through change control (ecadlib upstream check-kicad --update, or a "
                            f"librarian re-run) to release it")
        return None
    try:
        if src.startswith("kicad:"):
            chain, _ = K.load_symbol(src[6:], http, keep_quotes=True)
            got = released(flatten(chain, name), f"KiCad {src[6:]} (reused" + (", flattened from " + " -> ".join(str(s[1]) for s in chain[1:]) if len(chain) > 1 else "") + ")")
            if got:
                return got
        if src.startswith("local:"):
            lib, sname = src[6:].split(":", 1)
            f = next((p for r in libs for p in sorted(Path(r).rglob(f"{lib}.kicad_sym"))), None)
            if f:
                chain, _ = K.load_symbol(f"{f}:{sname}", None, keep_quotes=True)
                got = released(flatten(chain, name), f"local KiCad {lib}:{sname} (reused from {f.name})")
                if got:
                    return got
            else:
                why.append(f"{lib}.kicad_sym not under --lib")
        if src.startswith("easyeda:"):
            lcsc = src[8:]
            r = http.get(P.EasyEDA.URL.format(lcsc), headers=P.EasyEDA.HEADERS)
            res = (r.json().get("result") or {}) if r.ok else {}
            ds = res.get("dataStr") or {}
            if ds.get("shape"):
                sub = []
                t = sexpr.parse(EC.symbol_to_kicad(ds, name, "", {}, sub), keep_quotes=True)
                ks = K._find_symbol(t, name)
                f = item["symbol"].get("file")
                npins = len({str(sexpr.first(p, "number")[1]) for u in sexpr.find(ks, "symbol") for p in sexpr.find(u, "pin")})
                from . import symbol_layout as SL
                try:
                    box = SL.is_box_ic(K.convert_symbol([ks], name, None, [], None, []))
                except ValueError:
                    box = False
                if not (SL.wants_layout(npins, rules) and box and f and (plan_dir / f).exists()):
                    got = released(ks, f"EasyEDA {lcsc} (converted again with easyeda_convert)")
                    if got:
                        notes.extend(sub)
                        return got
                else:
                    why.append(f"EasyEDA {lcsc}: {npins} pins, grouped by function in the SchLib")
            else:
                why.append(f"EasyEDA {lcsc}: " + ("no single-unit symbol" if r.ok else r.error or f"HTTP {r.status}"))
    except (IOError, ValueError, KeyError) as e:
        why.append(f"{src}: {type(e).__name__}: {e}")
    f = item["symbol"].get("file")
    if not f or not (plan_dir / f).exists():
        raise FileNotFoundError("no symbol: " + "; ".join(why or ["no source and no .SchLib"]))
    return symbol_from_schlib(plan_dir / f, name, notes), f"{f} read back ({src or 'no source'})" + (f"; {'; '.join(why)}" if why else "")


# ---------------------------------------------------------------- footprints
LAYER_K = {AL.TOP_OVERLAY: "F.SilkS", AL.BOTTOM_OVERLAY: "B.SilkS", AL.MECH13: "F.Fab", AL.MECH15: "F.CrtYd",
           AL.TOP: "F.Cu", AL.BOTTOM: "B.Cu", AL.TOP_PASTE: "F.Paste", AL.TOP_SOLDER: "F.Mask",
           AL.BOTTOM_PASTE: "B.Paste", AL.BOTTOM_SOLDER: "B.Mask"}


def _tail(shape):
    return " " + shape.split(" ", 1)[1] if " " in shape else ""


def _in_poly(x, y, pts):
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _touches(p, pts, eps=400):
    """A polygon overlaps or touches pad p's anchor rectangle (eps internal units, about 1 um)?"""
    a = math.radians(p["rot"])
    loc = [((x - p["x"]) * math.cos(a) + (y - p["y"]) * math.sin(a), -(x - p["x"]) * math.sin(a) + (y - p["y"]) * math.cos(a))
           for x, y in pts]                   # the polygon in the pad frame
    hw, hh = p["w"] / 2 + eps, p["h"] / 2 + eps
    if any(abs(u) <= hw and abs(v) <= hh for u, v in loc) or any(_in_poly(u, v, loc) for u in (-hw, hw) for v in (-hh, hh)):
        return True

    def cross(a1, a2, b1, b2):
        d = lambda o, p, q: (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
        return d(a1, a2, b1) * d(a1, a2, b2) <= 0 and d(b1, b2, a1) * d(b1, b2, a2) <= 0
    box = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    return any(cross(e1, e2, f1, f2) for e1, e2 in zip(loc, loc[1:] + loc[:1]) for f1, f2 in zip(box, box[1:] + box[:1]))


def _custom_pads(recs):
    """Polygon pads as altium_lib / kicad_import write them: an anchor pad plus regions on its copper (and solder mask /
    paste) layers around or touching the anchor. -> ({id(pad): [copper outline, ...]}, {id(region) folded into a pad})."""
    side = {AL.TOP: ((AL.TOP,), (AL.TOP_SOLDER, AL.TOP_PASTE)), AL.BOTTOM: ((AL.BOTTOM,), (AL.BOTTOM_SOLDER, AL.BOTTOM_PASTE)),
            AL.MULTI: ((AL.TOP, AL.BOTTOM), (AL.TOP_SOLDER, AL.TOP_PASTE, AL.BOTTOM_SOLDER, AL.BOTTOM_PASTE))}
    regs = [r for r in recs if r["type"] == "region" and len(r.get("pts") or ()) >= 3]
    custom, used = {}, set()
    pads = sorted([r for r in recs if r["type"] == "pad" and r["layer"] in side], key=lambda r: r["layer"] == AL.MULTI)
    cen = lambda g: (sum(x for x, _ in g["pts"]) / len(g["pts"]), sum(y for _, y in g["pts"]) / len(g["pts"]))
    box = lambda g: (min(x for x, _ in g["pts"]), min(y for _, y in g["pts"]), max(x for x, _ in g["pts"]), max(y for _, y in g["pts"]))
    covers = lambda h, g: all(a <= b + 400 for a, b in zip(box(h)[:2] + tuple(-v for v in box(h)[2:]),   # h's box holds g's
                                                           box(g)[:2] + tuple(-v for v in box(g)[2:])))
    opened = lambda p, g: any(h["layer"] in side[p["layer"]][1] and _in_poly(*cen(h), g["pts"]) and covers(h, g)
                              for h in regs)              # its own mask / paste outline, over all of it
    owner = {}
    tests = (lambda p, g: _in_poly(p["x"], p["y"], g["pts"]),                 # a region around the pad's centre, or one
             lambda p, g: _touches(p, g["pts"]) and opened(p, g))             # touching it with its own mask / paste
    for test in tests:                        # SMD anchors first
        for p in pads:                        # regions around a pad's centre first, then regions touching its anchor
            if p["layer"] == AL.MULTI:
                continue
            for g in regs:
                if id(g) not in owner and g["layer"] in side[p["layer"]][0] and test(p, g):
                    owner[id(g)] = p
    for test in tests:                        # then through-hole pads: vias inside an SMD pad's shape don't own it
        for p in pads:
            if p["layer"] != AL.MULTI:
                continue
            for g in regs:
                if id(g) not in owner and g["layer"] in side[p["layer"]][0] and test(p, g):
                    owner[id(g)] = p
    strokes = [t for t in recs if t["type"] == "track" and t["layer"] in (AL.TOP, AL.BOTTOM)]
    for p in pads:
        cu, other = side[p["layer"]]
        hit = [g for g in regs if owner.get(id(g)) is p]
        if not hit:
            continue
        first = [g for g in hit if g["layer"] == cu[0]] or hit      # a through-hole pad: the top outline (= the bottom one)
        keep = [g for g in first if g["layer"] == first[0]["layer"]]
        custom[id(p)] = [g["pts"] for g in keep]
        used |= {id(g) for g in hit}
        mid = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        near = lambda u, v: abs(u[0] - v[0]) <= 1000 and abs(u[1] - v[1]) <= 1000
        shut = lambda mode, v: mode == 2 and v is not None and v <= -max(p["w"], p["h"]) // 2    # closed by expansion
        has = {AL.TOP_PASTE: not shut(p.get("paste_mode"), p.get("paste")), AL.BOTTOM_PASTE: not shut(p.get("paste_mode"), p.get("paste")),
               AL.TOP_SOLDER: not shut(p.get("mask_mode"), p.get("mask")), AL.BOTTOM_SOLDER: not shut(p.get("mask_mode"), p.get("mask"))}
        for layer in [l for l in other if has[l]]:   # its mask / paste outlines: one per copper polygon or stroke, centred on it
            for c, poly in [(mid(box(g)), g) for g in keep] + [(((t["x1"] + t["x2"]) / 2, (t["y1"] + t["y2"]) / 2), None)
                                                                 for t in strokes]:
                cand = [g for g in regs if g["layer"] == layer and id(g) not in used and near(mid(box(g)), c)]
                g = next((g for g in cand if poly is None or covers(g, poly) or layer in (AL.TOP_PASTE, AL.BOTTOM_PASTE)
                          and covers(poly, g)), cand[0] if cand else None)      # mask grows, paste may shrink
                if g is not None:
                    used.add(id(g))
    return custom, used


def _not_copper(r):
    """Why an Altium primitive is no drawing at all, or None: a keep-out (flags bit 9, on any layer) or a region that
    is not copper (polygon / board cutout, cavity, ...). Written as fp_poly / fp_line on F.Cu they would be copper."""
    if r["type"] in ("track", "arc", "fill", "region") and r.get("raw") and len(r["raw"][0]) >= 3 \
            and struct.unpack_from("<H", r["raw"][0], 1)[0] & 0x200:
        return "keep-out"
    if r["type"] == "region":
        p = AL.parse_params(r.get("params") or "")
        if p.get("KIND", "0") not in ("0", "") or p.get("ISBOARDCUTOUT", "").upper() == "TRUE":
            return f"region (kind {p.get('KIND')}{', board cutout' if p.get('ISBOARDCUTOUT', '').upper() == 'TRUE' else ''})"
    return None


def footprint_from_pcblib(path, name, notes, pick=None):
    """First footprint of a .PcbLib (or the one named pick) -> .kicad_mod text (KiCad 8), via altium_lib.read_pcblib."""
    fps = AL.read_pcblib(path)
    fp = next((f for f in fps if pick and f["name"].upper() == pick.upper()), fps[0])
    m = lambda u: _num(AL.to_mm(u))
    pt = lambda x, y: f"{m(x)} {m(-y)}"
    stroke = lambda w: f'(stroke (width {m(w) if w else "0.1"}) (type solid))'
    out, pads, smd = [], [], True
    recs = []
    for r in fp["records"]:
        why = _not_copper(r)
        if why:
            K._note(notes, f"Altium {why} {r['type']} on layer {r.get('layer')} not converted (add a KiCad rule area by hand)")
        else:
            recs.append(r)
    custom, used = _custom_pads(recs)
    for r in recs:
        t, layer = r["type"], LAYER_K.get(r.get("layer"))
        if id(r) in used:
            continue                          # copper / mask / paste outline of a polygon pad: part of that pad
        if t == "pad":
            w, h, rot = r["w"], r["h"], r["rot"] % 360
            shape = {1: "circle" if w == h else "oval", 2: "rect", 9: "roundrect"}.get(r["shape"], "rect")
            if id(r) in custom:               # anchor pad + its regions -> a KiCad custom pad (KiCad library layout)
                a = math.radians(rot)
                rel = lambda x, y: (((x - r["x"]) * math.cos(a) + (y - r["y"]) * math.sin(a)),
                                    -(-(x - r["x"]) * math.sin(a) + (y - r["y"]) * math.cos(a)))
                prims = " ".join("(gr_poly (pts " + " ".join(f"(xy {m(u)} {m(v)})" for u, v in (rel(x, y) for x, y in poly))
                                 + ") (width 0) (fill yes))" for poly in custom[id(r)])
                anchor = "circle" if r["shape"] == 1 else "rect"
                shape = f'custom (options (clearance outline) (anchor {anchor})) (primitives {prims})'
                K._note(notes, f"pad {r['designator']}: anchor pad and its copper regions written as a KiCad custom pad")
            if r["shape"] == 3:
                K._note(notes, f"pad {r['designator']}: octagonal pad written as rect")
            at = f"(at {pt(r['x'], r['y'])}{' ' + _num(rot) if rot else ''})"
            extra = f" (roundrect_rratio {_num((r['corner'] or 0) / 200)})" if shape == "roundrect" else ""
            mmanual = r.get("mask_mode") == 2 and r.get("mask") is not None
            tented = mmanual and r["mask"] <= -max(w, h) // 2        # manual expansion that closes the opening
            if mmanual and not tented and r["mask"]:                 # e.g. NSMD BGA lands: opening = land + margin
                extra += f" (solder_mask_margin {m(r['mask'])})"
            if r["hole"]:
                smd = False
                kind = "thru_hole" if r["plated"] else "np_thru_hole"
                ox, oy = r.get("offset") or (0, 0)          # copper offset from the hole (pad frame, Altium y up)
                off = f" (offset {m(ox)} {m(-oy)})" if ox or oy else ""
                drill = f"(drill {m(r['hole'])}{off})"
                if r["hole_shape"] == 2 and r["slot"]:
                    a, b = (r["slot"], r["hole"]) if round(r["hole_rot"]) % 180 == 0 else (r["hole"], r["slot"])
                    drill = f"(drill oval {m(a)} {m(b)}{off})"
                mask = "" if tented else ' "*.Mask"'
                pads.append(f'\t(pad {sexpr.quote(r["designator"])} {kind} {shape.split()[0]} {at} (size {m(w)} {m(h)}){extra} {drill} '
                            f'(layers "*.Cu"{mask}){_tail(shape)})')
            else:
                side = "B" if r["layer"] == AL.BOTTOM else "F"
                manual = r["paste_mode"] == 2 and r["paste"] is not None
                paste = not (manual and r["paste"] <= -max(w, h) // 2)
                margin = f" (solder_paste_margin {m(r['paste'])})" if manual and paste and r["paste"] else ""
                layers = " ".join(f'"{side}.{l}"' for l in ("Cu",) + (("Paste",) if paste else ()) + (() if tented else ("Mask",)))
                pads.append(f'\t(pad {sexpr.quote(r["designator"])} smd {shape.split()[0]} {at} (size {m(w)} {m(h)}){extra}{margin} (layers {layers}){_tail(shape)})')
        elif t == "track" and layer:
            out.append(f'\t(fp_line (start {pt(r["x1"], r["y1"])}) (end {pt(r["x2"], r["y2"])}) {stroke(r["w"])} (layer "{layer}"))')
        elif t == "arc" and layer:
            span = (r["a1"] - r["a0"]) % 360 or 360
            p = lambda a: pt(r["cx"] + r["r"] * math.cos(math.radians(a)), r["cy"] + r["r"] * math.sin(math.radians(a)))
            if span >= 359.999:
                out.append(f'\t(fp_circle (center {pt(r["cx"], r["cy"])}) (end {p(0)}) {stroke(r["w"])} (fill none) (layer "{layer}"))')
            else:
                out.append(f'\t(fp_arc (start {p(r["a0"])}) (mid {p(r["a0"] + span / 2)}) (end {p(r["a0"] + span)}) {stroke(r["w"])} (layer "{layer}"))')
        elif t in ("fill", "region") and layer:
            pts = [(r["x1"], r["y1"]), (r["x2"], r["y1"]), (r["x2"], r["y2"]), (r["x1"], r["y2"])] if t == "fill" else r["pts"]
            frot = (struct.unpack_from("<d", r["raw"][0], 29)[0] % 360 if t == "fill" and r.get("raw") and len(r["raw"][0]) >= 37
                    else 0.0)                 # an Altium fill turns about its centre
            if frot:
                cx, cy, a = (r["x1"] + r["x2"]) / 2, (r["y1"] + r["y2"]) / 2, math.radians(frot)
                pts = [(cx + (x - cx) * math.cos(a) - (y - cy) * math.sin(a), cy + (x - cx) * math.sin(a) + (y - cy) * math.cos(a))
                       for x, y in pts]
            if layer in ("F.Paste", "B.Paste") and frot:
                pads.append(f'\t(pad "" smd rect (at {pt(cx, cy)} {_num(frot)}) '
                            f'(size {m(abs(r["x2"] - r["x1"]))} {m(abs(r["y2"] - r["y1"]))}) (layers "{layer}"))')
            elif layer in ("F.Paste", "B.Paste"):   # paste window without copper: a paste-only pad, as KiCad draws them
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                pads.append(f'\t(pad "" smd rect (at {pt((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)}) '
                            f'(size {m(max(xs) - min(xs))} {m(max(ys) - min(ys))}) (layers "{layer}"))')
                if t == "region" and len(pts) != 4:
                    K._note(notes, "paste region written as its bounding rectangle")
            else:
                xy = " ".join(f"(xy {pt(x, y)})" for x, y in pts)
                out.append(f'\t(fp_poly (pts {xy}) (stroke (width 0) (type solid)) (fill solid) (layer "{layer}"))')
        elif t == "text" and r["text"] in (".Designator", ".Comment"):
            k, v, lyr = ("Reference", "REF**", "F.SilkS") if r["text"] == ".Designator" else ("Value", name, "F.Fab")
            out.insert(0, f'\t(property "{k}" {sexpr.quote(v)} (at {pt(r["x"], r["y"])} 0) (layer "{lyr}") '
                          f'(effects (font (size {m(r["h"])} {m(r["h"])}) (thickness {m(r["w"])}))))')
        elif t == "body":
            K._note(notes, f"3D body (extruded, height {AL.parse_params(r['params']).get('OVERALLHEIGHT', '?')}) not converted: KiCad needs a STEP model")
        elif t not in ("text",):
            K._note(notes, f"{t} on layer {r.get('layer')} not converted")
    if not any(o.lstrip().startswith('(property "Reference"') for o in out):
        out.insert(0, '\t(property "Reference" "REF**" (at 0 0 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))')
    if not any(o.lstrip().startswith('(property "Value"') for o in out):
        out.insert(1, f'\t(property "Value" {sexpr.quote(name)} (at 0 0 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))')
    descr = fp["params"].get("DESCRIPTION", "")
    head = [f"(footprint {sexpr.quote(name)}", f"\t(version {FP_VERSION})", '\t(generator "ecadlib")', '\t(layer "F.Cu")']
    head += [f"\t(descr {sexpr.quote(descr)})"] if descr else []
    head += [f"\t(attr {'smd' if smd else 'through_hole'})"]
    return "\n".join(head + out + pads) + "\n)\n"


def footprint_tree(text, name, lib, notes):
    """.kicad_mod text -> tree renamed to `name`, KiCad 8 syntax, model paths -> ${KIPRJMOD}/<lib>.3dshapes.
    Returns (tree, [(model url path in kicad-packages3D, file name)])."""
    t = sexpr.parse(text, keep_quotes=True)
    old = str(t[1])
    t[1] = Q(name)
    for p in list(sexpr.find(t, "property")) + list(sexpr.find(t, "fp_text")):
        if p[1] in ("Value", "value") and str(p[2]) == old:
            p[2] = Q(name)
    v = sexpr.first(t, "version")
    if v and int(v[1]) > FP_VERSION:
        t = fp_kicad8(t, notes)
    models = []
    for mdl in sexpr.find(t, "model"):
        src = K.model_url_path(str(mdl[1]))
        fn = Path(src).name if src else Path(str(mdl[1]).replace("\\", "/")).with_suffix(".step").name
        mdl[1] = Q(f"${{KIPRJMOD}}/{lib}.3dshapes/{fn}")
        models.append((src, fn))
    return t, models


def _local_model(src3d, fn, libs):
    """A footprint's STEP in a local library folder (--lib): <lib>/**/<X>.3dshapes/<fn>, else None."""
    if not src3d:
        return None
    d = Path(src3d).parent.name
    return next((p for r in libs for p in sorted(Path(r).rglob(fn)) if p.parent.name == d and p.is_file()), None)


def altium_model(pcblib, fpn, lib, shapes, taken, notes):
    """The 3D model of a footprint that has no KiCad (model ...): the STEP embedded in its .PcbLib, else a STEP extruded
    from its 3D body (outline, standoff and height), written to <lib>.3dshapes/. -> ((model ...) node, file name, how)
    or None. Altium and KiCad then both have the part's 3D model (ecadlib.parity checks it)."""
    from . import step_gen as SG
    fp = AL.read_pcblib(pcblib)[0]
    bodies = [r for r in fp["records"] if r["type"] == "body"]
    if not bodies:
        return None
    bp = AL.parse_params(bodies[0]["params"])
    mil = lambda k: RC._mil(bp.get(k)) or 0.0
    fn = f"{fpn}.step" if f"{fpn}.step" not in taken else f"{fpn}_ecadlib.step"
    if fp.get("models"):                      # embedded STEP (kicad_import / step_gen): the same file, unchanged
        mp, data = fp["models"][0]
        rot = [float(mp.get(k, 0) or 0) for k in ("ROTX", "ROTY", "ROTZ")]
        if any(abs(v) > 1e-9 for v in rot):
            K._note(notes, f"3D: the embedded STEP {mp.get('NAME')} is rotated in Altium; not written for KiCad")
            return None
        dz = AL.to_mm(int(float(mp.get("DZ", 0) or 0)))
        how = f"embedded STEP {mp.get('NAME', '')} from the .PcbLib"
    else:                                     # extruded body: the same prism as a STEP solid (y up, mm, as KiCad models)
        pts = [(AL.to_mm(x), AL.to_mm(y)) for x, y in bodies[0]["pts"]]
        lo, hi = mil("STANDOFFHEIGHT"), mil("OVERALLHEIGHT")
        if len(pts) < 3 or hi - lo <= 0:
            return None
        data = SG.write_step([SG.prism(pts, hi - lo, (0, 0, lo), name="body")], fpn, "ecadlib: the Altium 3D body as a STEP").encode("ascii")
        dz, how = 0.0, f"extruded body ({hi:.2f} mm) from the .PcbLib as a STEP"
    shapes.mkdir(parents=True, exist_ok=True)
    if not (shapes / fn).is_file() or (shapes / fn).read_bytes() != data:
        (shapes / fn).write_bytes(data)
    node = ["model", Q(f"${{KIPRJMOD}}/{lib}.3dshapes/{fn}"), ["offset", ["xyz", "0", "0", _num(dz)]],
            ["scale", ["xyz", "1", "1", "1"]], ["rotate", ["xyz", "0", "0", "0"]]]
    return node, fn, how


# ---------------------------------------------------------------- checks
def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def footprint_failures(item, plan_dir, kmod):
    """release_check's footprint rule (footprint_check plus the KiCad-library leniency) on `kmod`."""
    probe = dict(item, footprint=dict(item["footprint"], kicad_mod=str(Path(kmod).resolve())))
    fails, _, _ = RC.check_item(probe, plan_dir)
    return [f"{r}: {m}" for r, m in fails if r == "footprint"]


def _pins(sym):
    return {str(sexpr.first(p, "number")[1]) for u in sexpr.find(sym, "symbol") for p in sexpr.find(u, "pin") if sexpr.first(p, "number")}


# ---------------------------------------------------------------- driver
def export(plan, out, name, include_held=False, libs=(), http=None, log=print, rules=None):
    """Write the KiCad library for plan.json into `out`; returns the report dict (also kicad_export.json).
    rules: library rules (dict or path); None = the ecadlib.toml found from the plan's folder, if any."""
    plan, out = Path(plan), Path(out)
    plan_dir, lib = plan.parent, kname(name)
    from .rules import resolve
    rules = resolve(rules, plan_dir)
    items = json.loads(plan.read_text(encoding="utf-8"))
    from . import change_gate as CG          # change control (#105): a hand edit since the last writer is settled
    CG.publish_guard(plan, items, "kicad", rules=rules, log=log)   # first; a held change exports the released revision
    items = sorted(items, key=lambda i: kname(i["name"]))
    pretty, shapes = out / f"{lib}.pretty", out / f"{lib}.3dshapes"
    out.mkdir(parents=True, exist_ok=True)
    pretty.mkdir(exist_ok=True)
    rep = {"plan": str(plan), "library": lib, "parts": [], "skipped": [], "footprints": {}, "models": {}, "errors": [], "kicad_content": False}
    rep.update(warnings=[], written={})
    report = out / "kicad_export.json"
    try:                                      # what the last export wrote (and its sha256): edited by hand since?
        prev = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
    except ValueError:
        prev = {}
    prev_hash = prev.get("written") or {}
    owned = set(prev_hash) if "written" in prev else (
        {f"{lib}.kicad_sym"} | {f"{lib}.pretty/{k}.kicad_mod" for k in prev.get("footprints") or {}}
        | {f"{lib}.3dshapes/{k}" for k, v in (prev.get("models") or {}).items() if v}) if prev else set()

    def _hand_edited(path, rel):
        """A file this export replaces or removes that changed since the last export: copied aside and reported."""
        if rel in prev_hash and _sha(path) != prev_hash[rel]:
            keep = out / "ecadlib-hand-edits" / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.write_bytes(path.read_bytes())
            rep["warnings"].append(f"{rel}: edited since the last export; replaced from the plan "
                                   f"(the edited file is in {keep.relative_to(out).as_posix()})")

    def put(path, text):
        """Write `path` only when its content changes (a re-export leaves files untouched)."""
        rel, data = path.relative_to(out).as_posix(), text.encode("utf-8")
        cur = path.read_bytes() if path.is_file() else None
        if cur is not None and cur != data and rel not in rep["written"]:
            _hand_edited(path, rel)
        if cur != data:
            path.write_bytes(data)
        rep["written"][rel] = hashlib.sha256(data).hexdigest()
    symbols, fps, written, fp_refs = {}, {}, set(), {}
    for it in items:
        status = it.get("status", "ready")
        if status == "held" and not include_held:
            from . import messages as M        # "says": the same reasons in plain words, for dialogs (KiCad plugin)
            rep["skipped"].append({"part": it["name"], "why": "held: " + "; ".join(it.get("hold_reasons", [])),
                                   "says": [{k: e[k] for k in ("code", "text", "fix")} for e in M.explain_all(it.get("hold_reasons"))]})
            continue
        sname, notes = kname(it["name"]), []
        row = {"part": it["name"], "symbol_name": sname, "status": status, "symbol": None, "footprint": None, "notes": notes}
        if sname in symbols:
            rep["errors"].append(f"{it['name']}: symbol name {sname} already used by another part"); continue
        src = it.get("source") or {}
        rep["kicad_content"] |= any(str(src.get(k) or "").startswith("kicad:") for k in ("symbol", "footprint"))
        # footprint first: the symbol's Footprint field names it
        fref = it["footprint"].get("ref")
        fpn = kname(fref) if fref and (it["footprint"].get("file") or it["footprint"].get("kicad_mod")) else None
        if fpn and fp_refs.setdefault(fpn.casefold(), (fref, fpn, it["name"]))[0] != fref:
            o_ref, o_fpn, o_part = fp_refs[fpn.casefold()]      # another footprint already has this file name (KiCad
            rep["errors"].append(f"{it['name']}: footprint {fref!r} would be {lib}:{fpn}, the name of footprint "  # name;
                                 f"{o_ref!r} ({o_part}); rename one of them")         # ignoring case: Windows / macOS)
            notes.append(f"footprint left out: {fpn} is footprint {o_ref!r} of {o_part}")
            fpn = ""
        if fpn and fpn not in fps:
            kmod = RC._kicad_mod(it, plan_dir, RC._path(plan_dir, it["footprint"].get("file")))
            fnotes = []
            try:
                if kmod:
                    text, how = kmod.read_text(encoding="utf-8"), f"{kmod.relative_to(plan_dir) if kmod.is_relative_to(plan_dir) else kmod}"
                else:
                    text, how = footprint_from_pcblib(plan_dir / it["footprint"]["file"], fpn, fnotes), f"{it['footprint']['file']} converted back"
                tree, models = footprint_tree(text, fpn, lib, fnotes)
                sexpr.parse(sexpr.dumps(tree) + "\n")
                for src3d, fn in models:
                    if fn in rep["models"]:
                        continue
                    data = None
                    local = _local_model(src3d, fn, libs)
                    try:
                        data = K.fetch(http, "kicad-packages3D", src3d) if not local and src3d and http is not None else None
                    except IOError as e:
                        fnotes.append(f"3D {src3d}: {e}")
                    if local:                         # the model next to the footprint's own library (${KIPRJMOD}/...)
                        shapes.mkdir(exist_ok=True)
                        if not (shapes / fn).is_file() or (shapes / fn).read_bytes() != local.read_bytes():
                            (shapes / fn).write_bytes(local.read_bytes())
                        rep["models"][fn] = str(local)
                    elif data:
                        shapes.mkdir(exist_ok=True)
                        if not (shapes / fn).is_file() or (shapes / fn).read_bytes() != data.encode("utf-8"):
                            (shapes / fn).write_text(data, encoding="utf-8", newline="")
                        rep["models"][fn] = f"kicad-packages3D/{src3d}"
                        rep["kicad_content"] = True
                    elif (shapes / fn).is_file():     # put there by hand or by an earlier export: keep it
                        rep["models"][fn] = f"{lib}.3dshapes/{fn} (existing file kept)"
                    else:
                        rep["models"][fn] = None
                        fnotes.append(f"STEP {fn} not available offline: put it in {lib}.3dshapes/")
                pcb = RC._path(plan_dir, it["footprint"].get("file"))
                got = None
                if models and not any(rep["models"].get(fn) for _, fn in models) and pcb and pcb.is_file():
                    # no KiCad STEP to be had (e.g. a ${KIPRJMOD} path of a library not under --lib): the .PcbLib's
                    # 3D model instead of a model path that points at nothing
                    for _, fn in models:              # the KiCad model's own file name stays free for the real
                        rep["models"].pop(fn, None)   # model (put there by hand, or fetched by a later online export)
                    got = altium_model(pcb, fpn, lib, shapes, {**rep["models"], **{fn: True for _, fn in models}}, fnotes)
                    if got:
                        tree[:] = [x for x in tree if not (isinstance(x, list) and x and str(x[0]) == "model")]
                        fnotes[:] = [n for n in fnotes if "not available offline" not in n]
                        fnotes.append(f"3D: the KiCad model {', '.join(fn for _, fn in models)} is not available; {got[2]} instead")
                    else:
                        for _, fn in models:
                            rep["models"][fn] = None
                elif not models and pcb and pcb.is_file():
                    got = altium_model(pcb, fpn, lib, shapes, rep["models"], fnotes)
                if got:                       # no KiCad model: the Altium one, so both libraries have the part in 3D
                    tree.append(got[0])
                    rep["models"][got[1]] = got[2]
                elif not models:
                    fnotes.append("no 3D model (no KiCad model and no 3D body in the .PcbLib)")
                put(pretty / f"{fpn}.kicad_mod", sexpr.dumps(tree) + "\n")
                new = footprint_failures(it, plan_dir, pretty / f"{fpn}.kicad_mod")
                had = [f for f in (it.get("release_check") or {}).get("failures", []) if f.startswith("footprint:")]
                worse = [f for f in new if f not in had]
                fps[fpn] = {"from": how, "by": it["name"], "notes": fnotes, "check": "same as release check" if not worse else "new failures",
                            "failures": new}
                if worse:
                    rep["errors"].append(f"footprint {fpn}: fails more than in the release check: {'; '.join(worse)}")
            except Exception as e:             # one bad part must not stop the export
                rep["errors"].append(f"footprint {fpn} ({it['name']}): {type(e).__name__}: {e}")
                fpn = None
        elif fpn is None:
            notes.append("no footprint in the plan")
        row["footprint"] = fpn if fpn in fps else None
        if not (it.get("source") or {}).get("symbol") and not it["symbol"].get("file"):
            notes.append("no symbol in the plan: not in the .kicad_sym")
            rep["parts"].append(row)
            continue
        try:
            sym, how = load_symbol(it, plan_dir, http, libs, sname, notes, rules, warnings=rep["warnings"])
            sym = sym_kicad8(sym)
            props = {str(p[1]): str(p[2]) for p in sexpr.find(sym, "property")}
            set_fields(sym, plan_fields(it, lib, row["footprint"], props, rules))
            sym[:] = [c for c in sym if not (isinstance(c, list) and c[:2] == ["property", "ki_description"])]   # KiCad 7 -> Description
            if it.get("sim_model"):               # attached by `ecadlib sim-models --attach`: Sim.* fields + model file
                from .sim_models import kicad_export_fields
                sim = kicad_export_fields(it, plan_dir, out / f"{lib}.sim", f"{lib}.sim", notes)
                set_fields(sym, sim)
                row["sim"] = dict(sim).get("Sim.Name") or dict(sim).get("Sim.Device")
            symbols[sname] = sym
            row["symbol"] = how
            if row["footprint"]:
                pads = {p[1] for p in sexpr.find(sexpr.parse((pretty / f"{row['footprint']}.kicad_mod").read_text(encoding="utf-8")), "pad") if p[1]}
                missing = sorted(_pins(sym) - pads)
                if missing:
                    notes.append(f"symbol pins without a pad in {row['footprint']}: {', '.join(missing)}")
        except Exception as e:
            rep["errors"].append(f"symbol {it['name']}: {type(e).__name__}: {e}")
        rep["parts"].append(row)
    tree = ["kicad_symbol_lib", ["version", SYM_VERSION], ["generator", Q("ecadlib")], ["generator_version", Q("8.0")]]
    tree += [symbols[k] for k in sorted(symbols)]
    text = sexpr.dumps(tree) + "\n"
    parsed = sexpr.parse(text)
    assert [s[1] for s in sexpr.find(parsed, "symbol")] == sorted(symbols)
    put(out / f"{lib}.kicad_sym", text)
    stale = [p for p in pretty.glob("*.kicad_mod") if p.stem not in fps]
    stale += [p for p in (shapes.glob("*") if shapes.exists() else []) if p.is_file() and not rep["models"].get(p.name)]
    for old in stale:                         # a re-export drops the files no longer in the plan; one it did not write
        rel = old.relative_to(out).as_posix()  # (added by hand) is moved aside and reported, not lost
        if rel not in owned:
            keep = out / "ecadlib-hand-edits" / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.write_bytes(old.read_bytes())
            rep["warnings"].append(f"{rel}: not written by ecadlib and not in the plan: moved to "
                                   f"{keep.relative_to(out).as_posix()}")
        else:
            _hand_edited(old, rel)
        old.unlink()
    for fn, how in rep["models"].items():              # a model put there by hand stays the user's file
        if how and (shapes / fn).is_file() and (not str(how).endswith("(existing file kept)")
                                                or f"{lib}.3dshapes/{fn}" in owned):
            rep["written"][f"{lib}.3dshapes/{fn}"] = _sha(shapes / fn)
    desc = f"ecadlib export of {plan.name}"
    from .kicad_dblib import add_lib_row          # the row for this library; other rows (a project's own, another
    for kind, table, uri in (("sym", "sym_lib_table", f"{lib}.kicad_sym"), ("fp", "fp_lib_table", f"{lib}.pretty")):  # export) stay
        add_lib_row(out / f"{kind}-lib-table", lib, f"${{KIPRJMOD}}/{uri}", desc, "KiCad", table)
    if rep["kicad_content"] and not ((out / "NOTICE.txt").is_file()
                                     and (out / "NOTICE.txt").read_text(encoding="utf-8") == KICAD_NOTICE):
        (out / "NOTICE.txt").write_text(KICAD_NOTICE, encoding="utf-8")
    rep["footprints"] = {k: fps[k] for k in sorted(fps)}
    rep["symbols"] = len(symbols)
    rep["written"] = dict(sorted(rep["written"].items()))
    for it in items:
        ch = it.get("change") or {}
        if ch.get("state") == "held":
            rep["warnings"].append(f"{it['name']}: a change is held for approval ({ch.get('summary')}); the released "
                                   f"revision r{ch.get('base')} is exported (ecadlib part show {plan} {it['name']})")
    exported = {r["part"] for r in rep["parts"] if r.get("symbol") or r.get("footprint")}
    CG.record_publish(plan_dir, items, f"kicad:{CG.project_key(plan_dir, out)}", ok=lambda it: it["name"] in exported)
    body = json.dumps(rep, indent=2) + "\n"
    if not report.is_file() or report.read_text(encoding="utf-8") != body:
        report.write_text(body, encoding="utf-8")
    return rep


def print_result(rep, out, plan, t=None):
    """Terminal view of an export: a row per part (-v: with the conversion notes), skipped parts with the reason in
    plain words, errors, then the summary (what was written, how to add it in KiCad)."""
    from . import messages as M, term as TM
    t = t or TM.Term()
    rows = []
    for r in rep["parts"]:
        rows.append([t.status("ready" if r["symbol"] else "failed", "written" if r["symbol"] else "failed"),
                     r["symbol_name"], r["footprint"] or "-"])
        if t.verbose:
            rows += [["", "", t.dim(n)] for n in r["notes"]]
    for s in rep["skipped"]:
        e = M.explain(str(s["why"]).removeprefix("held: "))
        rows.append([t.status("held", "skipped"), s["part"], e["text"]])
    t.table(rows)
    if t.verbose:
        for fpn, f in rep["footprints"].items():
            for n in f["notes"]:
                t.write(t.dim(f"  {fpn}: {n}"))
    for w in rep.get("warnings", []):
        t.write(f"  note: {w}")
    for e in rep["errors"]:
        t.err.error("kicad-export", M.clean(str(e)), "run again with -v to see which part; --debug for details")
    steps = sum(1 for v in rep["models"].values() if v)
    lib = rep["library"]
    head = (f"{M.plural(rep['symbols'], 'symbol')}, {M.plural(len(rep['footprints']), 'footprint')}, "
            f"{steps}/{len(rep['models'])} 3D models written"
            + (f"; {M.plural(len(rep['skipped']), 'held part')} left out (--include-held adds them)" if rep["skipped"] else ""))
    t.summary(head, written=[(f"{out}/{lib}.kicad_sym", "symbols"), (f"{out}/{lib}.pretty/", "footprints"),
                             (f"{out}/sym-lib-table, fp-lib-table", "library tables (paths use ${KIPRJMOD})")],
              next=[(f"cp -r {out}/* <your KiCad project folder>/", "then reopen the project; or add the library in "
                     "Preferences > Manage Symbol / Footprint Libraries"),
                    (f"ecadlib parity {plan} --kicad {out}", "check KiCad and Altium agree, part by part")]
              + ([(f"ecadlib review {plan}", "the held parts")] if rep["skipped"] else []))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write a librarian plan's library as KiCad files (.kicad_sym, .pretty, .3dshapes, lib tables)")
    ap.add_argument("plan", help="librarian plan.json (model paths are relative to its folder)")
    ap.add_argument("-o", "--out", default="out/kicad")
    ap.add_argument("--name", default="ecadlib", help="library name (default %(default)s)")
    ap.add_argument("--lib", action="append", default=[], help="local KiCad library dir for local: symbols (repeatable)")
    ap.add_argument("--include-held", action="store_true", help="also export parts the release check held")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--offline", action="store_true", help="use the HTTP cache only")
    ap.add_argument("--rules", help="library rules file: house parameter names (default: ecadlib.toml found from the plan)")
    ap.add_argument("--json", action="store_true", help="print the export report as JSON instead of the log")
    a = ap.parse_args(argv)
    rep = export(a.plan, a.out, a.name, a.include_held, a.lib, CachedHTTP(a.cache, offline=a.offline, timeout=60), rules=a.rules,
                 log=(lambda *x, **k: None) if a.json else print)
    if a.json:
        print(json.dumps(rep, default=str))
        return 1 if rep["errors"] else 0
    print_result(rep, Path(a.out), a.plan)
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
