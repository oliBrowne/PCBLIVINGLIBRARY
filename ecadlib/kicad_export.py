"""A librarian plan's library as KiCad files: one symbol library, one footprint library, 3D models, lib tables.

    python3 -m ecadlib.kicad_export out/librarian/plan.json -o out/kicad --name mylib \
        [--lib examples/kicad_lib] [--include-held] [--cache out/http_cache] [--offline]

Writes into DIR:
  <lib>.kicad_sym    every part's symbol, named after the plan item (CAP-0402-100nF, STM32F103C8T6, ...), with
                     Reference, Value, Footprint "<lib>:<footprint>", Datasheet, Description, then Manufacturer, MPN,
                     the supplier part numbers (LCSC = C1525) and the plan's other parameters
  <lib>.pretty/      one .kicad_mod per footprint, renamed to the plan's footprint name
  <lib>.3dshapes/    the STEP models the footprints point at (${KIPRJMOD}/<lib>.3dshapes/X.step; with --project, or
                     a project given before for DIR, the path from that KiCad project's folder: the KiCad window's
                     <project>/ecadlib/ -> ${KIPRJMOD}/ecadlib/<lib>.3dshapes/X.step)
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
fill) are dropped. Held items are skipped unless --include-held, and so is a `ready` item whose files fail the
release check run again here (release_check.publish_problems). Output is sorted and repeatable: a re-export
rewrites only files whose content changes; a file edited by hand since the last export (kicad_export.json keeps the
sha256 of what it wrote) and one it did not write that is no longer wanted are copied to ecadlib-hand-edits/ and
listed under "warnings" before being replaced or removed.
Checks: every written file parses with ecadlib.sexpr; each footprint goes through release_check's footprint rule
(footprint_check) again and must not fail anything the plan's release check did not already record; symbol
pins and footprint pads are compared. Exit code 1 when a check fails.

KiCad's libraries are CC-BY-SA 4.0 (with a design-use exception): exports holding KiCad content get a NOTICE.txt
and must not be committed here (out/kicad/ is in .gitignore)."""
import argparse, copy, hashlib, json, math, os, re, struct, sys, time
from pathlib import Path

from . import altium_lib as AL, easyeda_convert as EC, footprint_check as FC, fsutil, kicad_import as K, providers as P, \
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
    """A footprint or library name KiCad accepts in a lib id and as a file name: NFC (macOS file systems take NFC and
    NFD spellings for one file name) and no Windows device name (AUX -> AUX_: fsutil.portable_stem)."""
    from .fsutil import portable_stem
    return portable_stem(ksym(s))


def ksym(s):
    """A symbol name KiCad accepts in a lib id: kname without the device-name rule. A symbol lives inside the
    .kicad_sym, never as a file of its own, so a part named CON or AUX keeps the lib id it was released with
    (footprint and library names are file names: kname)."""
    import unicodedata
    return re.sub(r'[\s:/\\<>"|?*\x00-\x1f]', "_", unicodedata.normalize("NFC", str(s))).strip("._") or "_"


def libname(s):
    """A library name (--name): kname, and also no ';', '{', '}', '=' (it goes into kicad-dblib's ODBC connection
    string, where they split the value)."""
    return kname(re.sub(r"[;{}=]", "_", str(s)))


class PlanError(ValueError):
    """plan.json is not a librarian plan, or an entry in it is malformed (exit 2, nothing written)."""


def check_plan(items, plan):
    """A librarian plan: a list of part entries, each with a name and symbol / footprint / parameters of the right
    shape. Raises PlanError naming the entry and field at fault (a hand edit gone wrong), before anything is written."""
    if not isinstance(items, list):
        raise PlanError(f"{plan}: not a librarian plan (expected a list of parts, got a JSON {type(items).__name__})")
    for k, it in enumerate(items, 1):
        if not isinstance(it, dict) or not isinstance(it.get("name"), (str, int, float)) or not str(it["name"]).strip():
            raise PlanError(f"{plan}: entry #{k} is not a part (a part needs a \"name\")")
        for f in ("symbol", "footprint", "source", "change"):
            if (f in ("symbol", "footprint") or it.get(f) is not None) and not isinstance(it.get(f), dict):
                raise PlanError(f"{plan}: {it['name']}: \"{f}\" must be an object")
        par = it.get("parameters")
        if par is not None and not isinstance(par, list):
            raise PlanError(f"{plan}: {it['name']}: \"parameters\" must be a list")
        for j, p in enumerate(par or [], 1):
            if not isinstance(p, dict) or not str(p.get("name") or "").strip():
                raise PlanError(f"{plan}: {it['name']}: parameter #{j} has no name")


def _num(v):
    return f"{round(float(v), 4) + 0:g}"


def _effects(hide=False, size=1.27, justify=()):
    return ["effects", ["font", ["size", _num(size), _num(size)]]] + ([["justify", *justify]] if justify else []) + \
        (["hide"] if hide else [])


def _prop(name, value, hide=True, at=(0, 0, 0), justify=()):
    return ["property", Q(name), Q(value), ["at", *map(_num, at)], _effects(hide, justify=justify)]


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
    par = {str(p["name"]): str(p["value"]) for p in item.get("parameters") or []
           if isinstance(p, dict) and p.get("name") and str(p.get("value", "")).strip()}
    used = {"Value", "Manufacturer", "Manufacturer Part Number", "ComponentLink2URL", "ComponentLink2Description", "Datasheet"}
    ds = par.get("ComponentLink2URL") or par.get("Datasheet")
    if not ds:                                # a datasheet in another link slot (Description "Datasheet"), as Altium has it
        n = next((m.group(1) for k in sorted(par) for m in [re.fullmatch(r"ComponentLink(\d+)Description", k)]
                  if m and "datasheet" in par[k].lower() and par.get(f"ComponentLink{m.group(1)}URL")), None)
        if n:
            ds = par[f"ComponentLink{n}URL"]
            used |= {f"ComponentLink{n}URL", f"ComponentLink{n}Description"}
    out = [("Reference", props.get("Reference", "U")), ("Value", par.get("Value") or item["name"]),
           ("Footprint", f"{lib}:{fp_name}" if fp_name else ""),
           ("Datasheet", ds or (props.get("Datasheet", "") if props.get("Datasheet", "") != "~" else "")),
           ("Description", str(item.get("description") or "").strip() or props.get("Description") or props.get("ki_description", "")),
           ("Manufacturer", par.get("Manufacturer", "")), ("MPN", par.get("Manufacturer Part Number", ""))]
    sup = {}
    for k in sorted(par):
        m = re.fullmatch(r"Supplier (\d+)", k)
        if m and par.get(f"Supplier Part Number {m.group(1)}"):
            sup[par[k]] = sup.get(par[k], 0) + 1   # the same supplier twice (two Mouser numbers): "Mouser", "Mouser 2"
            out.append((par[k] + (f" {sup[par[k]]}" if sup[par[k]] > 1 else ""), par[f"Supplier Part Number {m.group(1)}"]))
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


def _pick(entries, pick, path, what):
    """The entry named pick (ignoring case) of a multi-entry .SchLib / .PcbLib; the only one when there is one. A pick
    that names none of several is an error: the first entry would be another part's symbol / footprint."""
    hit = next((e for e in entries if pick and str(e["name"]).upper() == str(pick).upper()), None)
    if hit is None and pick and len(entries) > 1:
        raise ValueError(f"{what} {pick!r} is not in {Path(path).name} (it holds "
                         f"{', '.join(repr(str(e['name'])) for e in entries[:8])}{', ...' if len(entries) > 8 else ''})")
    return hit or entries[0]


def symbol_from_schlib(path, name, notes, pick=None):
    """First symbol of a .SchLib (or the one named pick) -> KiCad symbol tree (KiCad 8 syntax), via altium_lib.read_schlib."""
    _, syms = AL.read_schlib(path)
    if not syms:
        raise ValueError(f"{path}: no symbol")
    recs = _pick(syms, pick, path, "symbol")["records"]
    comp = recs[0]
    v = lambda r, k: (int(r.get(k.upper(), 0) or 0) + int(r.get(k.upper() + "_FRAC", 0) or 0) / 100000) * 0.254
    xy = lambda r, a, b: (_num(v(r, a)), _num(v(r, b)))
    lw = lambda r: ["stroke", ["width", {"0": "0", "1": "0.254", "2": "0.508", "3": "0.762"}.get(r.get("LINEWIDTH", "0"), "0")], ["type", "default"]]
    def fill(r):
        if r.get("ISSOLID") != "T":
            return ["fill", ["type", "none"]]
        return ["fill", ["type", "outline" if r.get("AREACOLOR") == str(AL.BLUE) else "background"]]
    units, pins, ref, value_at, ref_at, params = {}, [], "U", (0, 0), (0, 0), []
    multi = int(comp.get("PARTCOUNT", 2) or 2) > 2   # Altium part 0 (pins / graphics shared by every part) -> KiCad unit 0
    unit = lambda p: 0 if multi and p == 0 else max(1, p)
    owner = lambda r: (unit(int(r.get("OWNERPARTID", r.get("part", 1)) or 1)), int(r.get("OWNERPARTDISPLAYMODE", r.get("mode", 0)) or 0) + 1)
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
                nm = _kicad_pin_name(hidden_net[ri])               # KiCad ties a hidden power pin to the net of its name
                #                                                  (an Altium overbar is KiCad's ~{...}, as on a pin name)
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
            units.setdefault((unit(r["part"]), r["mode"] + 1), []).append(pin)
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
    # an Altium parameter's location is its bottom-left corner (no JUSTIFICATION record), so the KiCad text is too
    bl = ("left", "bottom")
    sym += [_prop("Reference", ref, False, (*ref_at, 0), bl), _prop("Value", name, False, (*value_at, 0), bl), _prop("Footprint", ""),
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
    How the symbol is drawn is no difference: the house rules may redraw the .SchLib (pins grouped, one end per power
    pin) while KiCad keeps the source's drawing, so pin ends that moved, and power pins of one name stacked or not,
    are left out; a stack that joins other pins is a difference.
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
    return [c for c in CD.diff(rel, got) if c["class"] != "cosmetic" and not c.get("drawing")]


def kicad_pin_details(sym):
    """The KiCad-only details of a symbol's pins, which do not survive the conversion to Altium (passive / free /
    no_connect / unspecified are one Altium type, power_in / power_out another; alternate functions are dropped):
    {number: {name, ktype, alt}}. Recorded on a KiCad publish line, so a later export of the same revision that reuses
    the source's symbol again can tell when the source changed one of them."""
    from . import fingerprint as FP
    fp, _ = FP.kicad_symbol(sym)
    return {n: {"name": p["name"], "ktype": p["ktype"], "alt": p["alt"]} for n, p in sorted(fp["pins"].items())}


def kicad_baseline(item, plan_dir):
    """The KiCad-only pin details ({number: {name, ktype, alt}}) the first KiCad publish of the part's released
    revision shipped (the ledger's publish line), or None."""
    from . import ledger as LG
    lg = LG.Ledger(plan_dir)
    head = lg.head(item["name"]) if lg.exists() else None
    if not head:
        return None
    return next((e["detail"]["ksym"] for e in reversed(lg.history(item["name"]))
                 if e.get("event") == "publish" and str(e.get("target") or "").startswith("kicad")
                 and e.get("revision") == head["revision"] and isinstance(e.get("detail"), dict)
                 and e["detail"].get("ksym")), None)


def kicad_baseline_differs(item, plan_dir, details):
    """KiCad-only pin details of a fetched symbol against those an earlier KiCad publish of the same released
    revision shipped -> [{class, text}] (electrical); [] when they match or no earlier publish recorded them."""
    base = kicad_baseline(item, plan_dir)
    if base is None:
        return []
    out = []
    for n in sorted(set(base) | set(details)):
        a, b = base.get(n), details.get(n)
        if a is None or b is None:
            out.append({"class": "electrical", "text": f"pin {n} {'added' if a is None else 'removed'}"})
            continue
        if a["ktype"] != b["ktype"]:
            out.append({"class": "electrical", "text": f"pin {n} ({b['name']}) KiCad type {a['ktype']} -> {b['ktype']}"})
        if a["alt"] != b["alt"]:
            out.append({"class": "electrical", "text": f"pin {n} ({b['name']}) alternate functions changed"})
    return out


POWER_OUT_NAME = re.compile(r"^(?:V?OUT\d*|VO\d*|VREG[A-Z0-9_]*|VDDCORE_?OUT|\d+V\d*_?OUT|V\d+V?\d*_?OUT|LDO_?OUT\d*)$")
SWITCH_CONTACT = {"NO", "COM", "NO1", "NO2", "COM1", "COM2", "NC1", "NC2"}   # a relay / switch: its NC is a contact


def restore_types(sym, own=None, notes=None, guess=True):
    """Put back on a symbol read back from a .SchLib the KiCad pin types Altium cannot hold: Altium has one Power type
    (a regulator's output comes back power_in, and KiCad ERC finds its rail undriven) and no NC type (an NC pin comes
    back passive). own: {pin number: 'power_out' | 'no_connect'} the source gave (the datasheet read, recorded with the
    plan's symbol: librarian.kicad_only_types). Without it, by name: a visible power pin named as a regulator output
    (symbol_layout's power_out names, less VCAP / VCORE / DECOUPLE, which are decoupled inputs) is power_out, and a
    visible passive pin named NC / DNC (symbol_layout's nc names) is no_connect, unless the part is a relay or switch,
    whose NC is the normally closed contact. Only a power_in pin becomes power_out and only a passive one no_connect,
    so a pin the source gave another type keeps it. Hidden pins are left alone. guess=False: own only, no names."""
    from . import symbol_layout as SL
    pins = [p for u in sexpr.find(sym, "symbol") for p in sexpr.find(u, "pin")]
    num = lambda p: str(sexpr.first(p, "number")[1]).strip('"')
    nm = lambda p: str(sexpr.first(p, "name")[1]).strip('"')
    if own or not guess:
        want = {str(k): v for k, v in (own or {}).items() if v in ("power_out", "no_connect")}
    else:
        names = {SL._key(nm(p)) for p in pins}
        ref = next((str(q[2]).strip('"') for q in sexpr.find(sym, "property") if str(q[1]).strip('"') == "Reference"), "")
        switch = bool(names & SWITCH_CONTACT) or ref.upper().rstrip("?0123456789") in ("K", "S", "SW", "RLY")
        want = {}
        for p in pins:
            k = SL._key(nm(p))
            if p[1] == "power_in" and POWER_OUT_NAME.match(k):
                want[num(p)] = "power_out"
            elif p[1] == "passive" and not switch and SL.classify({"name": nm(p), "num": num(p)}) == "nc":
                want[num(p)] = "no_connect"
    done = []
    for p in pins:
        t = want.get(num(p))
        if "hide" not in p and (t == "power_out" and p[1] == "power_in" or t == "no_connect" and p[1] == "passive"):
            p[1] = t
            done.append(num(p))
    if done and notes is not None:
        K._note(notes, "power_out / no_connect pin types put back (Altium has one Power type and no NC type)"
                + ("" if own else ", by pin name"))
    return done


def load_symbol(item, plan_dir, http, libs, name, notes, rules=None, warnings=None, kpins=None, schlib=None):
    """-> (KiCad symbol tree, how). Reuses KiCad / EasyEDA sources; else reads the SchLib back. An EasyEDA IC
    symbol (a plain box, [symbols] layout_pins) is read back from the SchLib, which the librarian wrote with its pins
    grouped (ecadlib.symbol_layout), so both libraries show the same symbol. A reused symbol must be the released
    revision (change control, #105): when the source changed since the release (an upstream pin swap), the released
    .SchLib is read back instead and the difference goes to `warnings`; upstream check-kicad --update or a librarian
    re-run brings the source's change through change control. The KiCad-only pin details (types the Altium
    conversion merges, alternate functions) are compared with those the first KiCad publish of the revision shipped;
    kpins: {part: those details} of a reused symbol, for the publish line."""
    src = str((item.get("source") or {}).get("symbol") or "")
    why = []

    def released(sym, how):
        bad = released_differs(item, plan_dir, sym)
        details = None
        if not bad:
            try:
                details = kicad_pin_details(sym)
                bad = kicad_baseline_differs(item, plan_dir, details)
            except (ValueError, KeyError, IndexError, TypeError) as e:
                bad = [{"class": "electrical", "text": f"the fetched symbol cannot be compared ({type(e).__name__}: {e})"}]
        if not bad:
            if kpins is not None and details is not None:
                kpins[item["name"]] = details
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
    sym = symbol_from_schlib(schlib or plan_dir / f, name,   # schlib: the released copy staged by the export
                              notes, pick=item["symbol"].get("ref"))
    own = item["symbol"].get("kicad_types")
    if not src.startswith(("kicad:", "local:")):
        restore_types(sym, own, notes)
    else:            # a curated KiCad symbol read back (offline, no cache, or its source changed since the release):
        if not own:  # the power_out / no_connect types its source gave, as the librarian recorded them (else as the
            try:     # KiCad publish of this revision shipped them), never guessed by pin name
                own = {n: d.get("ktype") for n, d in (kicad_baseline(item, plan_dir) or {}).items() if isinstance(d, dict)}
            except (OSError, ValueError, KeyError, TypeError):
                own = None
        restore_types(sym, own, notes, guess=False)
    if src.startswith("standard:Regulator_Linear_"):   # Altium has one Power type: a standard regulator's VO is power_out
        from . import stdparts as SP                     # in ecadlib's own symbol and in KiCad's (ERC needs a rail driver)
        try:
            want = {p[0]: p[2] for p in SP._regulator(src.split("_")[2:], ())[4]}
        except KeyError:
            want = {}
        for u in sexpr.find(sym, "symbol"):
            for p in sexpr.find(u, "pin"):
                if p[1] == "power_in" and want.get(str(sexpr.first(p, "number")[1]).strip('"')) == "power_out" and "hide" not in p:
                    p[1] = "power_out"
    return sym, f"{f} read back ({src or 'no source'})" + (f"; {'; '.join(why)}" if why else "")


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


def _round_rect_ratio(pts, tol=400):
    """A region outline that is an axis-aligned rounded rectangle (as altium_lib.round_rect writes a rounded paste
    window) -> KiCad's roundrect_rratio (corner radius / shorter side), else None. pts in Altium units; tol ~0.01 mm."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    top = [x for x, y in pts if abs(y - y1) <= 2]           # the flat top edge (arc points are lower)
    r = (w - (max(top) - min(top))) / 2 if top else 0
    if not (w > 0 and h > 0 and tol < r <= min(w, h) / 2 + tol):
        return None
    for x, y in pts:                          # every vertex on the rounded outline: r from the inner rectangle
        cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
        if abs(math.hypot(x - cx, y - cy) - r) > tol:
            return None
    return min(r / min(w, h), 0.5)


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


def footprint_from_pcblib(path, name, notes, pick=None, rules=None):
    """First footprint of a .PcbLib (or the one named pick) -> .kicad_mod text (KiCad 8), via altium_lib.read_pcblib.
    rules: library rules whose [layers] put the courtyard / assembly drawing on other mechanical layers."""
    fps = AL.read_pcblib(path)
    if not fps:
        raise ValueError(f"{path}: no footprint")
    fp = _pick(fps, pick, path, "footprint")
    from . import rules as RU
    lk, own = dict(LAYER_K), RU.layers(rules) if isinstance(rules, dict) and rules.get("layers") else None
    for role, klayer in (("assembly", "F.Fab"), ("courtyard", "F.CrtYd")) if own else ():
        if own.get(role) is not None:
            lk[own[role]] = klayer
    m = lambda u: _num(AL.to_mm(u))
    pt = lambda x, y: f"{m(x)} {m(-y)}"
    stroke = lambda w: f'(stroke (width {m(w) if w else "0.1"}) (type solid))'
    out, pads, texts = [], [], []
    tht = npth = cu_smd = False       # a plated pin makes it THT; a locating peg (NPTH) beside SMD pads does not
    recs = []
    for r in fp["records"]:
        why = _not_copper(r)
        if why:
            K._note(notes, f"Altium {why} {r['type']} on layer {r.get('layer')} not converted (add a KiCad rule area by hand)")
        else:
            recs.append(r)
    custom, used = _custom_pads(recs)
    for r in recs:
        t, layer = r["type"], lk.get(r.get("layer"))
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
                tht, npth = tht or bool(r["plated"]), npth or not r["plated"]
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
                cu_smd = True
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
                rr = _round_rect_ratio(pts) if t == "region" and len(pts) > 4 else None
                pads.append(f'\t(pad "" smd {"roundrect" if rr else "rect"} '
                            f'(at {pt((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)}) '
                            f'(size {m(max(xs) - min(xs))} {m(max(ys) - min(ys))}) (layers "{layer}")'
                            + (f' (roundrect_rratio {_num(round(rr, 4))})' if rr else "") + ")")
                if t == "region" and len(pts) != 4 and not rr:
                    K._note(notes, "paste region written as its bounding rectangle")
            elif layer in ("F.Mask", "B.Mask") and len(pts) == 4 and len({round(x) for x, _ in pts}) == 2 \
                    and len({round(y) for _, y in pts}) == 2:   # a mask window (a PowerPAD land's opening): a mask-only
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]      # pad, as KiCad draws it, so the pad checks see it
                pads.append(f'\t(pad "" smd rect (at {pt((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)}) '
                            f'(size {m(max(xs) - min(xs))} {m(max(ys) - min(ys))}) (layers "{layer}"))')
            else:
                xy = " ".join(f"(xy {pt(x, y)})" for x, y in pts)
                out.append(f'\t(fp_poly (pts {xy}) (stroke (width 0) (type solid)) (fill solid) (layer "{layer}"))')
        elif t == "text" and r["text"] in (".Designator", ".Comment"):
            texts.append(r)
        elif t == "body":
            bp = AL.parse_params(r['params'])
            kind = (f"embedded STEP {bp.get('MODEL.NAME', '')}" if str(bp.get("MODEL.EMBED", "")).upper() == "TRUE"
                    else f"extruded, height {bp.get('OVERALLHEIGHT', '?')}")
            K._note(notes, f"3D body ({kind}) not converted: KiCad needs a STEP model")
        elif t not in ("text",):
            K._note(notes, f"{t} on layer {r.get('layer')} not converted")
    # Altium stroke text sits at its bottom-left anchor, KiCad's at its centre (altium_lib.text_centre undoes the shift
    # texts_from_kicad made). The silk .Designator is the Reference; one on the assembly layer is the fab ${REFERENCE}.
    silk = {AL.TOP_OVERLAY: "F.SilkS", AL.BOTTOM_OVERLAY: "B.SilkS"}
    def text(r):
        cx, cy = AL.text_centre(r["text"], AL.to_mm(r["x"]), AL.to_mm(r["y"]), AL.to_mm(r["h"]), r.get("rot") or 0)
        return (f'(at {_num(cx)} {_num(-cy)} {_num((r.get("rot") or 0) % 360)}) (layer "{silk.get(r["layer"], "F.Fab")}") '
                f'(effects (font (size {m(r["h"])} {m(r["h"])}) (thickness {m(r["w"])})))')
    des = [r for r in texts if r["text"] == ".Designator"]
    ref = next((r for r in des if r["layer"] in silk), des[0] if des else None)
    com = next((r for r in texts if r["text"] == ".Comment"), None)
    fab = next((r for r in des if r is not ref and r["layer"] not in silk), None)
    out[:0] = ([f'\t(property "Reference" "REF**" {text(ref)})'] if ref else []) + \
              ([f'\t(property "Value" {sexpr.quote(name)} {text(com)})'] if com else []) + \
              ([f'\t(fp_text user "${{REFERENCE}}" {text(fab)})'] if fab else [])
    if not any(o.lstrip().startswith('(property "Reference"') for o in out):
        out.insert(0, '\t(property "Reference" "REF**" (at 0 0 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))')
    if not any(o.lstrip().startswith('(property "Value"') for o in out):
        out.insert(1, f'\t(property "Value" {sexpr.quote(name)} (at 0 0 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))')
    descr = fp["params"].get("DESCRIPTION", "")
    head = [f"(footprint {sexpr.quote(name)}", f"\t(version {FP_VERSION})", '\t(generator "ecadlib")', '\t(layer "F.Cu")']
    head += [f"\t(descr {sexpr.quote(descr)})"] if descr else []
    head += [f"\t(attr {'through_hole' if tht or (npth and not cu_smd) else 'smd'})"]
    return "\n".join(head + out + pads) + "\n)\n"


def models_at(lib, out=None, project=None):
    """The folder a footprint's (model ...) path names for the library's <lib>.3dshapes/, as KiCad resolves it from
    the project that uses the library (KIPRJMOD = that project's folder): ${KIPRJMOD}/<lib>.3dshapes when the library
    is written into the project folder itself (or no project is known), ${KIPRJMOD}/<path>/<lib>.3dshapes when it is
    written elsewhere (the KiCad window's <project>/ecadlib/), the absolute folder when no relative path reaches it
    (another drive)."""
    if project is None:
        return f"${{KIPRJMOD}}/{lib}.3dshapes"
    try:
        rel = Path(os.path.relpath(Path(out).resolve(), Path(project).resolve())).as_posix()
    except ValueError:
        return (Path(out).resolve() / f"{lib}.3dshapes").as_posix()
    return f"${{KIPRJMOD}}/{lib}.3dshapes" if rel == "." else f"${{KIPRJMOD}}/{rel}/{lib}.3dshapes"


def footprint_tree(text, name, lib, notes, at=None):
    """.kicad_mod text -> tree renamed to `name`, KiCad 8 syntax, model paths -> ${KIPRJMOD}/<lib>.3dshapes (at: the
    folder models_at gives instead). Returns (tree, [(model url path in kicad-packages3D, file name)])."""
    at = at or models_at(lib)
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
        mdl[1] = Q(f"{at}/{fn}")
        models.append((src, fn))
    return t, models


def _local_model(src3d, fn, libs):
    """A footprint's STEP in a local library folder (--lib): <lib>/**/<X>.3dshapes/<fn>, else the same name as .stp /
    .STEP (any case: the (model ...) path's own spelling, which fn has as .step), else None."""
    if not src3d:
        return None
    d, stem = Path(src3d).parent.name, Path(fn).stem.casefold()
    exact = next((p for r in libs for p in sorted(Path(r).rglob(fn)) if p.parent.name == d and p.is_file()), None)
    if exact is not None:
        return exact
    return next((p for r in libs for q in sorted(Path(r).rglob(d)) if q.is_dir() for p in sorted(q.iterdir())
                 if p.is_file() and p.stem.casefold() == stem and p.suffix.lower() in (".step", ".stp")), None)


def altium_model(pcblib, fpn, lib, shapes, taken, notes, pick=None, write=None, at=None):
    """The 3D models of a footprint that has no KiCad (model ...): for each 3D body of the footprint (pick: its name in
    a multi-footprint .PcbLib), the STEP embedded in the .PcbLib that the body names (MODELID), else a STEP extruded
    from the body (outline, standoff and height), written to <lib>.3dshapes/ (write(path, bytes); default a plain
    write). -> ([(model ...) node, ...], [(file name, how), ...]) or None. Altium and KiCad then both have the part's
    3D model (ecadlib.parity checks it). An embedded STEP is placed as the body places it in Altium (MODEL.2D.X / Y,
    MODEL.3D.ROTZ, MODEL.3D.DZ; the library's model record when the body has none), as KiCad's own Altium importer
    does: offset (X, +Y, DZ) in the model's y-up frame, rotation z = -ROTZ (KiCad turns a model by the negated angle).
    One tilted (ROTX / ROTY) or turned in 2D is not written, nor a body projected to the bottom side
    (BODYPROJECTION = 1, which a top-side KiCad model would show on the wrong side), each with a note.
    at: the model folder as the footprint names it (models_at)."""
    from . import step_gen as SG
    at = at or models_at(lib)
    fps = AL.read_pcblib(pcblib)
    if not fps:
        return None
    fp = _pick(fps, pick, pcblib, "footprint")
    lib_models = fps[0].get("models") or []          # read_pcblib puts the library's embedded models on entry 0
    by_id = {str(mp.get("ID", "")): (mp, data) for mp, data in lib_models}
    bodies = [r for r in fp["records"] if r["type"] == "body"]
    nodes, files, done = [], [], set()
    for k, body in enumerate(bodies):
        bp = AL.parse_params(body["params"])
        mil = lambda key: RC._mil(bp.get(key)) or 0.0
        base = fpn if k == 0 else f"{fpn}_{k + 1}"
        fn = f"{base}.step" if f"{base}.step" not in taken else f"{base}_ecadlib.step"
        if str(bp.get("BODYPROJECTION") or "0").strip() not in ("0", ""):
            K._note(notes, f"3D: body {k + 1} is on the bottom side in Altium (BODYPROJECTION {bp.get('BODYPROJECTION')}); "
                           f"not written for KiCad")
            continue
        emb = by_id.get(str(bp.get("MODELID", "")))
        if emb is None and len(lib_models) == 1 and len(fps) == 1:
            emb = lib_models[0]                       # one footprint, one model (no matching id): it is this one's
        if emb is not None and str(bp.get("MODELID", "")) not in done:   # embedded STEP: the same file, unchanged
            done.add(str(bp.get("MODELID", "")))
            mp, data = emb
            # the body's own placement (what Altium shows), else the library model record's
            num = lambda v: float(re.sub(r"[^0-9eE+.\-]", "", str(v)) or 0)
            rot = [num(bp[f"MODEL.3D.{key}"]) if str(bp.get(f"MODEL.3D.{key}") or "").strip() else num(mp.get(key, 0) or 0)
                   for key in ("ROTX", "ROTY", "ROTZ")]
            if abs(rot[0]) > 1e-9 or abs(rot[1]) > 1e-9 or abs(num(bp.get("MODEL.2D.ROTATION") or 0)) > 1e-9:
                K._note(notes, f"3D: the embedded STEP {mp.get('NAME')} is rotated in Altium; not written for KiCad")
                continue
            dz = RC._mil(bp["MODEL.3D.DZ"]) if RC._mil(bp.get("MODEL.3D.DZ")) is not None else \
                AL.to_mm(int(float(mp.get("DZ", 0) or 0)))
            off, rz = (mil("MODEL.2D.X"), mil("MODEL.2D.Y"), dz), -rot[2]   # KiCad's importer: y = +MODEL.2D.Y
            how = f"embedded STEP {mp.get('NAME', '')} from the .PcbLib"
        elif emb is not None:
            continue
        else:                                     # extruded body: the same prism as a STEP solid (y up, mm, as KiCad models)
            pts = [(AL.to_mm(x), AL.to_mm(y)) for x, y in body.get("pts") or []]
            lo, hi = mil("STANDOFFHEIGHT"), mil("OVERALLHEIGHT")
            if len(pts) < 3 or hi - lo <= 0:
                continue
            data = SG.write_step([SG.prism(pts, hi - lo, (0, 0, lo), name="body")], base, "ecadlib: the Altium 3D body as a STEP").encode("ascii")
            off, rz, how = (0.0, 0.0, 0.0), 0.0, f"extruded body ({hi:.2f} mm) from the .PcbLib as a STEP"
        shapes.mkdir(parents=True, exist_ok=True)
        if write is not None:
            write(shapes / fn, data)
        elif not (shapes / fn).is_file() or (shapes / fn).read_bytes() != data:
            (shapes / fn).write_bytes(data)
        nodes.append(["model", Q(f"{at}/{fn}"), ["offset", ["xyz"] + [_num(v) for v in off]],
                      ["scale", ["xyz", "1", "1", "1"]], ["rotate", ["xyz", "0", "0", _num(rz)]]])
        files.append((fn, how))
    return (nodes, files) if nodes else None


# ---------------------------------------------------------------- checks
def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _same(data, sha):
    """data is what was written (sha256 sha), byte for byte or but for CRLF line ends (a git checkout with
    core.autocrlf on Windows; KiCad reads both)?"""
    return sha in (hashlib.sha256(data).hexdigest(), hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest())


def _keep_aside(out, path, rel):
    """Copy path to <out>/ecadlib-hand-edits/<rel>; when an earlier, different copy is there, to <stem>.2<suffix>,
    .3, ... so every kept version stays. -> the copy's path."""
    data = Path(path).read_bytes()
    keep = Path(out) / "ecadlib-hand-edits" / rel
    n = 1
    while keep.is_file() and keep.read_bytes() != data:
        n += 1
        keep = keep.with_name(f"{Path(rel).stem}.{n}{Path(rel).suffix}")
    if not keep.is_file():
        keep.parent.mkdir(parents=True, exist_ok=True)
        fsutil.write_bytes_atomic(keep, data)
    return keep


def _lib_record(rep):
    """What a later export (and kicad-dblib) needs of one library's export: the files written with their sha256, the
    parts with their symbol / footprint, the footprints, the plan it came from, whether it holds KiCad library content
    (kicad-pcm's licence note)."""
    return {"plan": rep.get("plan"), "written": dict(rep.get("written") or {}), "kicad_content": bool(rep.get("kicad_content")),
            "parts": [{"part": p.get("part"), "symbol_name": p.get("symbol_name"), "symbol": bool(p.get("symbol")),
                       "footprint": p.get("footprint"), "held": bool(p.get("held"))}
                      for p in rep.get("parts") or [] if isinstance(p, dict)],
            "footprints": sorted(rep.get("footprints") or {}),
            "kept": [dict(p) for p in rep.get("kept") or [] if isinstance(p, dict)],
            "models_at": rep.get("models_at") or models_at(rep.get("library") or "")}


def footprint_failures(item, plan_dir, kmod):
    """release_check's footprint rule (footprint_check plus the KiCad-library leniency) on `kmod`."""
    probe = dict(item, footprint=dict(item["footprint"], kicad_mod=str(Path(kmod).resolve())))
    fails, _, _ = RC.check_item(probe, plan_dir)
    return [f"{r}: {m}" for r, m in fails if r == "footprint"]


def _pins(sym):
    return {str(sexpr.first(p, "number")[1]) for u in sexpr.find(sym, "symbol") for p in sexpr.find(u, "pin") if sexpr.first(p, "number")}


# ---------------------------------------------------------------- driver
def export(plan, out, name, include_held=False, libs=(), http=None, log=print, rules=None, project=None):
    """Write the KiCad library for plan.json into `out`; returns the report dict (also kicad_export.json).
    rules: library rules (dict or path); None = the ecadlib.toml found from the plan's folder, if any.
    project: the KiCad project folder that uses the library (KIPRJMOD), when `out` is not that folder itself (the
    KiCad window's <project>/ecadlib/): the 3D model paths are written from there (models_at). None: the folder an
    earlier export into `out` was given, else `out` is taken as the project folder.
    The library lock is held for the whole export (change control: the files read are the settled ones)."""
    import tempfile
    from . import change_gate as CG
    with CG.publishing(Path(plan).parent), tempfile.TemporaryDirectory(prefix="ecadlib-kx-") as stage:
        return _export(plan, out, name, include_held, libs, http, log, rules, project, stage=stage)


def _export(plan, out, name, include_held=False, libs=(), http=None, log=print, rules=None, project=None, stage=None):
    plan, out = Path(plan), Path(out)
    plan_dir, lib = plan.parent, libname(name)
    from .rules import resolve
    rules = resolve(rules, plan_dir)
    try:
        items = json.loads(plan.read_text(encoding="utf-8"))
    except ValueError as e:
        raise PlanError(f"{plan}: not a librarian plan ({e})") from e
    check_plan(items, plan)                   # a wrong file ({}) or a broken entry: refused before anything is touched
    if not items and ((out / f"{lib}.kicad_sym").is_file() or any((out / f"{lib}.pretty").glob("*.kicad_mod"))):
        raise PlanError(f"{plan} has no parts: exporting it would empty the library {lib} in {out}; nothing written "
                        f"(delete {lib}.kicad_sym and {lib}.pretty/ by hand if that is what you want)")
    from . import change_gate as CG          # change control (#105): a hand edit since the last writer is settled
    CG.publish_guard(plan, items, "kicad", rules=rules, log=log)   # first; a held change exports the released revision
    items = sorted(items, key=lambda i: ksym(i["name"]))
    pretty, shapes = out / f"{lib}.pretty", out / f"{lib}.3dshapes"
    out.mkdir(parents=True, exist_ok=True)
    pretty.mkdir(exist_ok=True)
    rep = {"plan": str(plan), "library": lib, "parts": [], "skipped": [], "footprints": {}, "models": {}, "errors": [], "kicad_content": False}
    rep.update(warnings=[], written={}, change_control=True, kept=[])   # made with a ledger (a lost one fails closed)
    kpins = {}                                 # part -> KiCad-only pin details of a reused symbol (publish line)
    report = out / "kicad_export.json"
    try:                                      # what the last export wrote (and its sha256): edited by hand since?
        prev = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
    except ValueError:
        prev = {}
    prev = prev if isinstance(prev, dict) else {}
    # one kicad_export.json per folder, several libraries (--name a, then --name b): each library's record is kept
    # under "libraries", so exporting b does not forget what a wrote (its hand-edit and stale-file protection)
    prev_libs = {k: v for k, v in (prev.get("libraries") or {}).items() if isinstance(v, dict)} \
        if isinstance(prev.get("libraries"), dict) else {}
    if prev.get("library") and prev["library"] not in prev_libs and "written" in prev:
        prev_libs[prev["library"]] = _lib_record(prev)
    mine = prev_libs.get(lib) or (prev if prev and prev.get("library", lib) == lib else {})
    prev_hash = mine.get("written") or {}
    owned = set(prev_hash) if "written" in mine else (
        {f"{lib}.kicad_sym"} | {f"{lib}.pretty/{k}.kicad_mod" for k in mine.get("footprints") or {}}
        | {f"{lib}.3dshapes/{k}" for k, v in (mine.get("models") or {}).items() if v}) if mine else set()
    was_at = mine.get("models_at")              # where an earlier export (given the project) put the model paths
    at = models_at(lib, out, project) if project is not None else \
        was_at if isinstance(was_at, str) and was_at.endswith(f"/{lib}.3dshapes") else models_at(lib)
    rep["models_at"] = at

    def _hand_edited(path, rel):
        """A file this export replaces or removes that changed since the last export: copied aside and reported."""
        if rel in prev_hash and not _same(path.read_bytes(), prev_hash[rel]):
            keep = _keep_aside(out, path, rel)
            rep["warnings"].append(f"{rel}: edited since the last export; replaced from the plan "
                                   f"(the edited file is in {keep.relative_to(out).as_posix()})")

    def put(path, text):
        """Write `path` only when its content changes (a re-export leaves files untouched), whole or not at all.
        A file there that ecadlib did not write (a project's own library of the same name) is copied aside first."""
        rel = path.relative_to(out).as_posix()
        data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        cur = path.read_bytes() if path.is_file() else None
        if cur is not None and cur != data and rel not in rep["written"]:
            if rel in prev_hash:
                _hand_edited(path, rel)
            elif rel not in owned and cur.replace(b"\r\n", b"\n") != data.replace(b"\r\n", b"\n"):
                keep = _keep_aside(out, path, rel)
                rep["warnings"].append(f"{rel}: was there before and not written by ecadlib; replaced from the plan "
                                       f"(the old file is in {keep.relative_to(out).as_posix()})")
        if cur != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            fsutil.write_bytes_atomic(path, data)
        rep["written"][rel] = hashlib.sha256(data).hexdigest()
    symbols, fps, written, fp_refs, fp_src, model_src = {}, {}, set(), {}, {}, {}
    # a held part exported here before keeps what was exported (dblib-sync leaves a held part's row and files alone and
    # push sends nothing for it): only a part that passes the check is written anew, none is taken out for a hold
    before = {r["part"]: r for r in (mine.get("parts") or []) + (mine.get("kept") or [])
              if isinstance(r, dict) and r.get("part") and r.get("symbol") and r.get("symbol_name")}
    sym_file = out / f"{lib}.kicad_sym"
    try:                                       # only as the last export wrote it: a hand edit is not taken in as ours
        old_syms = {str(x[1]): x for x in sexpr.find(sexpr.parse(sym_file.read_text(encoding="utf-8"), keep_quotes=True),
                                                     "symbol")} \
            if before and sym_file.is_file() and _same(sym_file.read_bytes(), prev_hash.get(f"{lib}.kicad_sym")) else {}
    except (ValueError, OSError, IndexError):
        old_syms = {}

    held = []                                  # (item, why): kept after the other parts are written
    from . import ledger as LG
    lg = LG.Ledger(plan_dir)

    def keep(it, why):
        """A held part released and exported before: its symbol, footprint and 3D files stay as they were (a footprint
        another part writes now, under the same name, is that part's). Not kept: one --include-held exported while held
        or never released, or one whose files were edited by hand since (the edit is not taken in as ours). -> kept?"""
        r = before.get(it["name"])
        sym = old_syms.get(str(r["symbol_name"])) if r and not r.get("held") else None
        if sym is None or r["symbol_name"] in symbols or not (lg.exists() and lg.head(it["name"])):
            return False
        symbols[r["symbol_name"]] = sym
        fpn, note = r.get("footprint"), ""
        f = pretty / f"{fpn}.kicad_mod" if fpn else None
        rel = f.relative_to(out).as_posix() if f is not None else ""
        if f is not None and f.is_file() and fpn not in fps:
            fps[fpn] = {"from": "kept from the last export", "by": it["name"], "notes": [], "check": "held", "failures": []}
            rep["written"][rel] = prev_hash.get(rel) or _sha(f)
            for fn in re.findall(r'\(model "[^"]*?' + re.escape(f"{lib}.3dshapes/") + r'([^"/]+)"', f.read_text(encoding="utf-8")):
                if (shapes / fn).is_file():            # a STEP put there by hand stays the user's file
                    rep["models"].setdefault(fn, "kept from the last export" if f"{lib}.3dshapes/{fn}" in owned
                                             else f"{lib}.3dshapes/{fn} (existing file kept)")
        elif fpn in fps and fps[fpn].get("by") != it["name"] and rep["written"].get(rel) != prev_hash.get(rel):
            note = f"; its footprint {lib}:{fpn} is now {fps[fpn].get('by')}'s, written anew"
        rep["kept"].append({"part": it["name"], "symbol_name": r["symbol_name"], "symbol": True,
                            "footprint": fpn if fpn in fps else None})
        rep["warnings"].append(f"{it['name']}: held ({'; '.join(why)}); the version exported before stays in {lib} "
                               f"until it passes the check again{note}")
        return True

    def fp_source(it, fpn, fnotes):
        """The footprint's .kicad_mod text (its own, or its .PcbLib entry converted back) and where it came from."""
        kmod = RC._kicad_mod(it, plan_dir, RC._path(plan_dir, it["footprint"].get("file")))
        if kmod:
            data = released_bytes(it, kmod)
            return data.decode("utf-8"), f"{kmod.relative_to(plan_dir) if kmod.is_relative_to(plan_dir) else kmod}"
        return (footprint_from_pcblib(released_path(it, plan_dir / it["footprint"]["file"]), fpn, fnotes,
                                      pick=it["footprint"].get("ref"), rules=rules),
                f"{it['footprint']['file']} converted back")

    from . import ledger as LG
    ledger = LG.Ledger(plan_dir)

    def released_bytes(it, path):
        """The file's bytes as the released revision has them (change_gate.released_bytes): an edit saved while this
        export ran is not exported; the head's own copy from the blob store goes out, with a warning."""
        data = path.read_bytes()
        try:
            data, head = CG.released_bytes(ledger, it, path, plan_dir, data=data)
        except FileNotFoundError:              # no copy to send: the edit is not shipped either
            raise ValueError(f"{path.name} changed while the export ran and the released copy is missing")
        if head:
            msg = (f"{it['name']}: {path.name} changed while the export ran (an edit saved meanwhile); the released "
                   f"revision r{head['revision']} was exported, the edit is settled by the next ecadlib run")
            if msg not in rep["warnings"]:
                rep["warnings"].append(msg)
        return data

    staged = {}

    def released_path(it, path):
        """A .SchLib / .PcbLib to read for a released part: its released_bytes, staged in a temporary folder under its
        own name, so what is read is what was checked (an edit saved later reaches neither)."""
        key = (str(Path(path).resolve()), it["name"])
        if key not in staged:
            import hashlib
            d = Path(stage) / hashlib.sha256("\0".join(key).encode()).hexdigest()[:16]
            d.mkdir(parents=True, exist_ok=True)
            (d / Path(path).name).write_bytes(released_bytes(it, Path(path)))
            staged[key] = d / Path(path).name
        return staged[key]
    for it in items:
        status = it.get("status", "ready")
        if status == "held" and not include_held:
            from . import messages as M        # "says": the same reasons in plain words, for dialogs (KiCad plugin)
            rep["skipped"].append({"part": it["name"], "why": "held: " + "; ".join(it.get("hold_reasons", [])),
                                   "says": [{k: e[k] for k in ("code", "text", "fix")} for e in M.explain_all(it.get("hold_reasons"))]})
            held.append((it, it.get("hold_reasons") or ["release check"]))
            continue
        if status == "awaiting_approval":      # never released (no ledger revision): nothing to publish yet (#105)
            rep["skipped"].append({"part": it["name"], "why": "awaiting_approval: waiting for sign-off, not released yet",
                                   "says": [{"code": "awaiting_approval", "text": "Waiting for sign-off: not released yet.",
                                             "fix": f"ecadlib approve {plan} status"}]})
            held.append((it, ["waiting for sign-off"]))
            continue
        why = [] if status == "held" else RC.publish_problems(it, plan_dir, rules, writes_files=True)   # checked again: `ready` is not
        if why and not include_held:                                                  # taken on trust
            from . import messages as M
            rep["skipped"].append({"part": it["name"], "why": "held: " + "; ".join(why), "recheck": why,
                                   "says": [{k: e[k] for k in ("code", "text", "fix")} for e in M.explain_all(why)]})
            held.append((it, why))
            continue
        sname, notes = ksym(it["name"]), []
        if str((it.get("source") or {}).get("symbol") or "").startswith("easyeda:") and not P.unofficial_allowed(rules):
            notes.append("EasyEDA not fetched again ([providers] unofficial = false): the symbol is read back from the SchLib")
            it = dict(it, source=dict(it["source"], symbol=""))
        row = {"part": it["name"], "symbol_name": sname, "status": status, "symbol": None, "footprint": None, "notes": notes,
               "held": status == "held" or bool(why)}    # --include-held wrote it while held: never kept as released
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
        if fpn and fpn in fps and fpn in fp_src:   # the same footprint ref again: it must be the same footprint
            try:
                again = fp_source(it, fpn, [])[0]
            except Exception as e:
                again = f"{type(e).__name__}: {e}"
            if again != fp_src[fpn]:
                rep["errors"].append(f"{it['name']}: footprint {fref!r} differs from the footprint {fref!r} of "
                                     f"{fps[fpn]['by']} (written as {lib}:{fpn}); give one of them another footprint name")
                notes.append(f"footprint left out: {lib}:{fpn} is {fps[fpn]['by']}'s, with different content")
                fpn = ""
        if fpn and fpn not in fps:
            fnotes = []
            try:
                text, how = fp_source(it, fpn, fnotes)
                fp_src[fpn] = text
                tree, models = footprint_tree(text, fpn, lib, fnotes, at)
                sexpr.parse(sexpr.dumps(tree) + "\n")
                mnodes = list(sexpr.find(tree, "model"))
                for k, (src3d, fn0) in enumerate(models):
                    local = _local_model(src3d, fn0, libs)
                    fn = local.name if local is not None else fn0     # SOIC8_TI.stp: copied under its own name
                    ident = str(local) if local is not None else f"kicad-packages3D/{src3d}" if src3d else fn
                    n, base = 1, fn
                    while fn in model_src and model_src[fn] != ident:   # another footprint's model has this name
                        n += 1
                        fn = f"{Path(base).stem}_{n}{Path(base).suffix}"
                    if fn != base:
                        fnotes.append(f"3D: another footprint's model is also named {base}; this one is written as {fn}")
                    if fn != fn0:
                        models[k] = (src3d, fn)
                        if k < len(mnodes):
                            mnodes[k][1] = Q(f"{at}/{fn}")
                    if fn in rep["models"]:
                        continue
                    model_src[fn] = ident
                    data = None
                    try:
                        data = K.fetch(http, "kicad-packages3D", src3d) if not local and src3d and http is not None else None
                    except IOError as e:
                        fnotes.append(f"3D {src3d}: {e}")
                    if local:                         # the model next to the footprint's own library (${KIPRJMOD}/...)
                        put(shapes / fn, local.read_bytes())
                        rep["models"][fn] = str(local)
                    elif data:
                        put(shapes / fn, data)
                        rep["models"][fn] = f"kicad-packages3D/{src3d}"
                        rep["kicad_content"] = True
                    elif (shapes / fn).is_file():     # put there by hand or by an earlier export: keep it
                        rep["models"][fn] = f"{lib}.3dshapes/{fn} (existing file kept)"
                    else:
                        rep["models"][fn] = None
                        fnotes.append(f"STEP {fn} not available offline: put it in {lib}.3dshapes/")
                pcb = RC._path(plan_dir, it["footprint"].get("file"))
                if pcb and pcb.is_file():          # its 3D models: from the released .PcbLib too
                    pcb = released_path(it, pcb)
                got = None
                if models and not any(rep["models"].get(fn) for _, fn in models) and pcb and pcb.is_file():
                    # no KiCad STEP to be had (e.g. a ${KIPRJMOD} path of a library not under --lib): the .PcbLib's
                    # 3D model instead of a model path that points at nothing
                    for _, fn in models:              # the KiCad model's own file name stays free for the real
                        rep["models"].pop(fn, None)   # model (put there by hand, or fetched by a later online export)
                    got = altium_model(pcb, fpn, lib, shapes, {**rep["models"], **model_src, **{fn: True for _, fn in models}},
                                       fnotes, pick=fref, write=put, at=at)
                    if got:
                        tree[:] = [x for x in tree if not (isinstance(x, list) and x and str(x[0]) == "model")]
                        fnotes[:] = [n for n in fnotes if "not available offline" not in n]
                        fnotes.append(f"3D: the KiCad model {', '.join(fn for _, fn in models)} is not available; "
                                      f"{'; '.join(h for _, h in got[1])} instead")
                    else:
                        for _, fn in models:
                            rep["models"][fn] = None
                elif not models and pcb and pcb.is_file():
                    got = altium_model(pcb, fpn, lib, shapes, {**rep["models"], **model_src}, fnotes, pick=fref, write=put, at=at)
                if got:                       # no KiCad model: the Altium one, so both libraries have the part in 3D
                    tree.extend(got[0])
                    for fn, h in got[1]:
                        rep["models"][fn] = h
                        model_src[fn] = f"altium:{fpn}:{fn}"
                    fnotes[:] = [n for n in fnotes if not n.startswith("3D body")]   # it is converted, after all
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
            sf = it["symbol"].get("file")
            sch = (released_path(it, plan_dir / sf) if sf and str(sf).lower().endswith(".schlib")
                   and (plan_dir / sf).is_file() else None)
            sym, how = load_symbol(it, plan_dir, http, libs, sname, notes, rules, warnings=rep["warnings"], kpins=kpins,
                                   schlib=sch)
            sym = sym_kicad8(sym)
            props = {str(p[1]): str(p[2]) for p in sexpr.find(sym, "property")}
            fields = plan_fields(it, lib, row["footprint"], props, rules)
            names = ((rules or {}).get("parameters") or {}).get("names") or {}
            gone = ({k for k, v in names.items() if v != k} | ({"MPN"} if names.get("Manufacturer Part Number", "MPN") != "MPN"
                    else set())) - {k for k, _ in fields}      # a house name replaces the field the .SchLib carried
            sym[:] = [c for c in sym if not (isinstance(c, list) and c[0] == "property" and str(c[1]) in gone)]
            set_fields(sym, fields)
            sym[:] = [c for c in sym if not (isinstance(c, list) and c[:2] == ["property", "ki_description"])]   # KiCad 7 -> Description
            symbols[sname] = sym
            row["symbol"] = how
            if row["footprint"]:
                pads = {p[1] for p in sexpr.find(sexpr.parse((pretty / f"{row['footprint']}.kicad_mod").read_text(encoding="utf-8")), "pad") if p[1]}
                missing = sorted(_pins(sym) - pads)
                if missing:                   # a pin with no pad: the symbol / footprint pair is wrong (exit 1)
                    notes.append(f"symbol pins without a pad in {row['footprint']}: {', '.join(missing)}")
                    rep["errors"].append(f"{it['name']}: symbol pins without a pad in {lib}:{row['footprint']}: "
                                         f"{', '.join(missing)}")
        except Exception as e:
            rep["errors"].append(f"symbol {it['name']}: {type(e).__name__}: {e}")
        rep["parts"].append(row)
    taken = set()                              # held parts exported before and not kept: not "left the plan"
    for it, why in held:
        if not keep(it, why) and it["name"] in before:
            taken.add(before[it["name"]]["symbol_name"])
            rep["warnings"].append(f"{it['name']}: held ({'; '.join(why)}); taken out of {lib} until it is released "
                                   f"(a KiCad project using it loses the link)")
    tree = ["kicad_symbol_lib", ["version", SYM_VERSION], ["generator", Q("ecadlib")], ["generator_version", Q("8.0")]]
    tree += [symbols[k] for k in sorted(symbols)]
    text = sexpr.dumps(tree) + "\n"
    parsed = sexpr.parse(text)
    assert [s[1] for s in sexpr.find(parsed, "symbol")] == sorted(symbols)
    put(out / f"{lib}.kicad_sym", text)
    gone = sorted({r.get("symbol_name") for r in (mine.get("parts") or []) + (mine.get("kept") or [])
                   if r.get("symbol") and r.get("symbol_name")} - set(symbols) - taken)   # this library's own record:
                                                                                         # the top level is the last one
    if gone:                                  # a part that left the plan, or another BOM's plan into the same folder
        was = mine.get("plan") or (prev.get("plan") if prev.get("library") == lib else None)   # None: a record
        same = not was or Path(str(was)).resolve() == plan.resolve()                         # from before "plan"
        from .messages import plural
        rep["warnings"].append(f"{plural(len(gone), 'part')} exported last time "
                               + ("" if same else f"from {was} ") + f"and not in this plan removed from "
                               f"{lib}: {', '.join(gone[:8])}{' ...' if len(gone) > 8 else ''}; a KiCad project using "
                               f"them loses those links" + ("" if same else " (give each plan its own -o folder)"))
    stale =[p for p in pretty.glob("*.kicad_mod") if p.stem not in fps]
    for p in (shapes.glob("*") if shapes.exists() else []):
        if p.is_file() and fsutil.atomic_tmp(p) and time.time() - p.stat().st_mtime > 600:
            p.unlink()                        # ecadlib's own temp file a killed export left: no one's work
        elif p.is_file() and not fsutil.atomic_tmp(p) and not rep["models"].get(p.name):
            stale.append(p)
    for old in stale:                         # a re-export drops the files no longer in the plan; one it did not write
        rel = old.relative_to(out).as_posix()  # (added by hand) is moved aside and reported, not lost
        if rel not in owned:
            keep = _keep_aside(out, old, rel)
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
        try:                                  # a table it cannot edit: the library is written; say so, keep the report
            add_lib_row(out / f"{kind}-lib-table", lib, f"${{KIPRJMOD}}/{uri}", desc, "KiCad", table, warnings=rep["warnings"])
        except (ValueError, OSError) as e:
            rep["warnings"].append(f"{kind}-lib-table not updated: {e}")
    notice = out / "NOTICE.txt"               # a project's own NOTICE.txt is kept: the KiCad notice is added to it
    cur = notice.read_text(encoding="utf-8", errors="replace") if notice.is_file() else None
    if rep["kicad_content"] and (cur is None or KICAD_NOTICE.strip() not in cur):
        fsutil.write_text_atomic(notice, KICAD_NOTICE if cur is None or not cur.strip() else cur.rstrip("\n") + "\n\n" + KICAD_NOTICE)
        if cur is not None and cur.strip():
            rep["warnings"].append("NOTICE.txt: the KiCad libraries' licence notice added at the end (the existing text is kept)")
    rep["footprints"] = {k: fps[k] for k in sorted(fps)}
    rep["symbols"] = len(symbols)
    rep["written"] = dict(sorted(rep["written"].items()))
    rep["held_changes"] = []
    shipped = {r["part"] for r in rep["parts"] if r.get("symbol") or r.get("footprint")}
    for it in items:
        ch = it.get("change") or {}
        if ch.get("state") == "held":
            rep["held_changes"].append(it["name"])
            kept = (f"the released revision r{ch['base']} is exported" if ch.get("base") is not None else
                    "it has no released revision; --include-held exported the held version anyway"
                    if it["name"] in shipped else "it has no released revision, so it is not exported")
            rep["warnings"].append(f"{it['name']}: a change is held for approval ({ch.get('summary')}); {kept} "
                                   f"(ecadlib part show {plan} {it['name']})")
    exported = {r["part"] for r in rep["parts"] if r.get("symbol") or r.get("footprint")}
    CG.record_publish(plan_dir, items, f"kicad:{CG.project_key(plan_dir, out)}", ok=lambda it: it["name"] in exported,
                      detail=lambda it: {"ksym": kpins[it["name"]]} if it["name"] in kpins else {})
    rep["libraries"] = dict(sorted({**prev_libs, lib: _lib_record(rep)}.items()))
    body = json.dumps(rep, indent=2) + "\n"
    if not report.is_file() or report.read_text(encoding="utf-8") != body:
        fsutil.write_text_atomic(report, body)
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
        says = s.get("says") or [M.explain(str(s["why"]).removeprefix("held: "))]   # one sentence per reason
        rows.append([t.status("held", "skipped"), s["part"], says[0]["text"] + (f" (+{len(says) - 1} more)" if len(says) > 1 else "")])
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
    waiting = [s for s in rep["skipped"] if str(s["why"]).startswith("awaiting_approval")]
    kept = {k.get("part") for k in rep.get("kept") or [] if isinstance(k, dict)}
    held = [s for s in rep["skipped"] if s not in waiting and s["part"] not in kept]
    waiting = [s for s in waiting if s["part"] not in kept]
    q = lambda p: _shell_quote(str(p))
    head = (f"{M.plural(rep['symbols'], 'symbol')}, {M.plural(len(rep['footprints']), 'footprint')}, "
            f"{steps}/{len(rep['models'])} 3D models written"
            + (f"; {M.plural(len(held), 'held part')} left out (--include-held adds them)" if held else "")
            + (f"; {M.plural(len(waiting), 'part')} waiting for sign-off left out" if waiting else "")
            + (f"; {M.plural(len(kept), 'held part')} kept as exported before" if kept else ""))
    t.summary(head, written=[(f"{out}/{lib}.kicad_sym", "symbols"), (f"{out}/{lib}.pretty/", "footprints"),
                             (f"{out}/sym-lib-table, fp-lib-table", "library tables (paths use ${KIPRJMOD})")],
              next=[(f"ecadlib kicad-export {q(plan)} -o <your KiCad project folder> --name {q(lib)}",
                     "into the project: its own lib-table rows are kept (copying these tables over would drop them); "
                     "or Preferences > Manage Symbol / Footprint Libraries"),
                    (f"ecadlib parity {q(plan)} --kicad {q(out)}", "check KiCad and Altium agree, part by part")]
              + ([(f"ecadlib review {q(plan)}", "the held parts")] if rep["skipped"] else [])
              + ([(f"ecadlib part status {q(plan)}", f"{M.plural(len(rep['held_changes']), 'held change')} waiting for "
                   "a person (exit code 1 until then)")] if rep.get("held_changes") else []))


def _shell_quote(s):
    from .cliutil import shell_word
    return shell_word(s)


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
    ap.add_argument("--project", help="the KiCad project folder that uses the library, when -o is not that folder: the "
                                      "3D model paths are written from it (${KIPRJMOD}/<path to -o>/<lib>.3dshapes); "
                                      "remembered for later exports into -o")
    a = ap.parse_args(argv)
    try:
        rep = export(a.plan, a.out, a.name, a.include_held, a.lib, CachedHTTP(a.cache, offline=a.offline, timeout=60),
                     rules=a.rules, log=(lambda *x, **k: None) if a.json else print, project=a.project)
    except PlanError as e:
        print(f"ecadlib kicad-export: {e}", file=sys.stderr)
        return 2
    # exit 1 also while a change is held: the library on disk is not what was exported, a person has to look
    if a.json:
        print(json.dumps(rep, default=str))
        return 1 if rep["errors"] or rep.get("held_changes") else 0
    print_result(rep, Path(a.out), a.plan)
    return 1 if rep["errors"] or rep.get("held_changes") else 0


if __name__ == "__main__":
    sys.exit(main())
