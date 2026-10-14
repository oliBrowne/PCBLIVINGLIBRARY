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
  sym-lib-table, fp-lib-table   tables with the one library each (uri ${KIPRJMOD}/...): copy DIR's contents
                     into the project folder, or paste the `lib` lines into the project's tables
  kicad_export.json  what came from where, and the checks below
Symbols, by the plan item's `source.symbol`:
  kicad:Lib:Name     the official KiCad symbol again (HTTP cache, then gitlab.com), `extends` chain flattened
  local:Lib:Name     the same from a --lib folder (Lib.kicad_sym)
  easyeda:Cxxx       the EasyEDA symbol converted again with easyeda_convert (the pin data the SchLib came from)
  anything else      (datasheet reader, generated, or a KiCad source that cannot be reached): the item's .SchLib
                     read back with altium_lib.read_schlib and drawn as a KiCad symbol (pins, rectangles, lines,
                     polygons, circles, arcs, text; parts -> units, display modes -> De Morgan body styles)
Footprints: the item's .kicad_mod (models/KiCad/...) when present, else its .PcbLib records converted back.
Format: KiCad 8 (symbol library version 20231120, footprints up to 20240108), which KiCad 8, 9 and 10 open.
Newer upstream syntax is written the KiCad 8 way: `(hide yes)` -> `hide`, KiCad 9/10-only flags (in_pos_files,
show_name, do_not_autoplace, embedded_fonts, jumper settings, rule-area placement, rounded-rect radius, hatched
fill) are dropped. Held items are skipped unless --include-held. Output is sorted and repeatable.
Checks: every written file parses with ecadlib.sexpr; each footprint goes through release_check's footprint rule
(footprint_check) again and must not fail anything the plan's release check did not already record; symbol
pins and footprint pads are compared. Exit code 1 when a check fails.

KiCad's libraries are CC-BY-SA 4.0 (with a design-use exception): exports holding KiCad content get a NOTICE.txt
and must not be committed here (out/kicad/ is in .gitignore)."""
import argparse, copy, json, math, re, sys
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
           ("Description", str(item.get("description") or "").strip() or props.get("Description", "")),
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


def symbol_from_schlib(path, name, notes):
    """First symbol of a .SchLib -> KiCad symbol tree (KiCad 8 syntax), via altium_lib.read_schlib."""
    _, syms = AL.read_schlib(path)
    if not syms:
        raise ValueError(f"{path}: no symbol")
    recs = syms[0]["records"]
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
    for r in recs[1:]:
        kind = r.get("RECORD")
        if kind == "2":
            d = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}[r["orient"]]
            x, y, L = r["x"] * 0.254, r["y"] * 0.254, r["length"] * 0.254
            nm = _kicad_pin_name(r["name"]) or "~"
            pin = ["pin", ETYPE_K.get(r["etype"], "passive"), _pin_shape(r["symbols"], r["num"], notes),
                   ["at", _num(x + L * d[0]), _num(y + L * d[1]), str((r["orient"] * 90 + 180) % 360)], ["length", _num(L)]]
            if r["conglomerate"] & 0x04:
                pin.append("hide")
            pin += [["name", Q(nm), _effects()], ["number", Q(r["num"]), _effects()]]
            if r["etype"] == 7:
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


def load_symbol(item, plan_dir, http, libs, name, notes):
    """-> (KiCad symbol tree, how). Reuses KiCad / EasyEDA sources; else reads the SchLib back."""
    src = str((item.get("source") or {}).get("symbol") or "")
    why = []
    try:
        if src.startswith("kicad:"):
            chain, _ = K.load_symbol(src[6:], http, keep_quotes=True)
            return flatten(chain, name), f"KiCad {src[6:]} (reused" + (", flattened from " + " -> ".join(str(s[1]) for s in chain[1:]) if len(chain) > 1 else "") + ")"
        if src.startswith("local:"):
            lib, sname = src[6:].split(":", 1)
            f = next((p for r in libs for p in sorted(Path(r).rglob(f"{lib}.kicad_sym"))), None)
            if f:
                chain, _ = K.load_symbol(f"{f}:{sname}", None, keep_quotes=True)
                return flatten(chain, name), f"local KiCad {lib}:{sname} (reused from {f.name})"
            why.append(f"{lib}.kicad_sym not under --lib")
        if src.startswith("easyeda:"):
            lcsc = src[8:]
            r = http.get(P.EasyEDA.URL.format(lcsc), headers=P.EasyEDA.HEADERS)
            res = (r.json().get("result") or {}) if r.ok else {}
            ds = res.get("dataStr") or {}
            if ds.get("shape"):
                t = sexpr.parse(EC.symbol_to_kicad(ds, name, "", {}, notes), keep_quotes=True)
                return K._find_symbol(t, name), f"EasyEDA {lcsc} (converted again with easyeda_convert)"
            why.append(f"EasyEDA {lcsc}: " + ("no single-unit symbol" if r.ok else r.error or f"HTTP {r.status}"))
    except (IOError, ValueError, KeyError) as e:
        why.append(f"{src}: {type(e).__name__}: {e}")
    f = item["symbol"].get("file")
    if not f or not (plan_dir / f).exists():
        raise FileNotFoundError("no symbol: " + "; ".join(why or ["no source and no .SchLib"]))
    return symbol_from_schlib(plan_dir / f, name, notes), f"{f} read back ({src or 'no source'})" + (f"; {'; '.join(why)}" if why else "")


# ---------------------------------------------------------------- footprints
LAYER_K = {AL.TOP_OVERLAY: "F.SilkS", AL.BOTTOM_OVERLAY: "B.SilkS", AL.MECH13: "F.Fab", AL.MECH15: "F.CrtYd",
           AL.TOP: "F.Cu", AL.BOTTOM: "B.Cu", AL.TOP_PASTE: "F.Paste", AL.TOP_SOLDER: "F.Mask"}


def footprint_from_pcblib(path, name, notes):
    """First footprint of a .PcbLib -> .kicad_mod text (KiCad 8), via altium_lib.read_pcblib."""
    fp = AL.read_pcblib(path)[0]
    m = lambda u: _num(AL.to_mm(u))
    pt = lambda x, y: f"{m(x)} {m(-y)}"
    stroke = lambda w: f'(stroke (width {m(w) if w else "0.1"}) (type solid))'
    out, pads, smd = [], [], True
    for r in fp["records"]:
        t, layer = r["type"], LAYER_K.get(r.get("layer"))
        if t == "pad":
            w, h, rot = r["w"], r["h"], r["rot"] % 360
            shape = {1: "circle" if w == h else "oval", 2: "rect", 9: "roundrect"}.get(r["shape"], "rect")
            if r["shape"] == 3:
                K._note(notes, f"pad {r['designator']}: octagonal pad written as rect")
            at = f"(at {pt(r['x'], r['y'])}{' ' + _num(rot) if rot else ''})"
            extra = f" (roundrect_rratio {_num((r['corner'] or 0) / 200)})" if shape == "roundrect" else ""
            if r["hole"]:
                smd = False
                kind = "thru_hole" if r["plated"] else "np_thru_hole"
                drill = f"(drill {m(r['hole'])})"
                if r["hole_shape"] == 2 and r["slot"]:
                    a, b = (r["slot"], r["hole"]) if round(r["hole_rot"]) % 180 == 0 else (r["hole"], r["slot"])
                    drill = f"(drill oval {m(a)} {m(b)})"
                pads.append(f'\t(pad {sexpr.quote(r["designator"])} {kind} {shape} {at} (size {m(w)} {m(h)}){extra} {drill} (layers "*.Cu" "*.Mask"))')
            else:
                side = "B" if r["layer"] == AL.BOTTOM else "F"
                manual = r["paste_mode"] == 2 and r["paste"] is not None
                paste = not (manual and r["paste"] <= -max(w, h) // 2)
                margin = f" (solder_paste_margin {m(r['paste'])})" if manual and paste and r["paste"] else ""
                layers = " ".join(f'"{side}.{l}"' for l in (("Cu", "Paste", "Mask") if paste else ("Cu", "Mask")))
                pads.append(f'\t(pad {sexpr.quote(r["designator"])} smd {shape} {at} (size {m(w)} {m(h)}){extra}{margin} (layers {layers}))')
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
            if layer == "F.Paste":            # paste window without copper: a paste-only pad, as KiCad draws them
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                pads.append(f'\t(pad "" smd rect (at {pt((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)}) '
                            f'(size {m(max(xs) - min(xs))} {m(max(ys) - min(ys))}) (layers "F.Paste"))')
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


# ---------------------------------------------------------------- checks
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
    items = sorted(json.loads(plan.read_text()), key=lambda i: kname(i["name"]))
    pretty, shapes = out / f"{lib}.pretty", out / f"{lib}.3dshapes"
    out.mkdir(parents=True, exist_ok=True)
    pretty.mkdir(exist_ok=True)
    rep = {"plan": str(plan), "library": lib, "parts": [], "skipped": [], "footprints": {}, "models": {}, "errors": [], "kicad_content": False}
    symbols, fps, written = {}, {}, set()
    for it in items:
        status = it.get("status", "ready")
        if status == "held" and not include_held:
            rep["skipped"].append({"part": it["name"], "why": "held: " + "; ".join(it.get("hold_reasons", []))})
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
        if fpn and fpn not in fps:
            kmod = RC._kicad_mod(it, plan_dir, RC._path(plan_dir, it["footprint"].get("file")))
            fnotes = []
            try:
                if kmod:
                    text, how = kmod.read_text(), f"{kmod.relative_to(plan_dir) if kmod.is_relative_to(plan_dir) else kmod}"
                else:
                    text, how = footprint_from_pcblib(plan_dir / it["footprint"]["file"], fpn, fnotes), f"{it['footprint']['file']} converted back"
                tree, models = footprint_tree(text, fpn, lib, fnotes)
                body = sexpr.dumps(tree) + "\n"
                sexpr.parse(body)
                (pretty / f"{fpn}.kicad_mod").write_text(body)
                for src3d, fn in models:
                    if fn in rep["models"]:
                        continue
                    data = None
                    try:
                        data = K.fetch(http, "kicad-packages3D", src3d) if src3d and http is not None else None
                    except IOError as e:
                        fnotes.append(f"3D {src3d}: {e}")
                    if data:
                        shapes.mkdir(exist_ok=True)
                        (shapes / fn).write_text(data)
                        rep["models"][fn] = f"kicad-packages3D/{src3d}"
                        rep["kicad_content"] = True
                    elif (shapes / fn).is_file():     # put there by hand or by an earlier export: keep it
                        rep["models"][fn] = f"{lib}.3dshapes/{fn} (existing file kept)"
                    else:
                        rep["models"][fn] = None
                        fnotes.append(f"STEP {fn} not available offline: put it in {lib}.3dshapes/")
                if not models:
                    fnotes.append("no 3D model (the Altium library has an extruded body only)")
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
            sym, how = load_symbol(it, plan_dir, http, libs, sname, notes)
            sym = sym_kicad8(sym)
            props = {str(p[1]): str(p[2]) for p in sexpr.find(sym, "property")}
            set_fields(sym, plan_fields(it, lib, row["footprint"], props, rules))
            if it.get("sim_model"):               # attached by `ecadlib sim-models --attach`: Sim.* fields + model file
                from .sim_models import kicad_export_fields
                sim = kicad_export_fields(it, plan_dir, out / f"{lib}.sim", f"{lib}.sim", notes)
                set_fields(sym, sim)
                row["sim"] = dict(sim).get("Sim.Name") or dict(sim).get("Sim.Device")
            symbols[sname] = sym
            row["symbol"] = how
            if row["footprint"]:
                pads = {p[1] for p in sexpr.find(sexpr.parse((pretty / f"{row['footprint']}.kicad_mod").read_text()), "pad") if p[1]}
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
    (out / f"{lib}.kicad_sym").write_text(text)
    for old in pretty.glob("*.kicad_mod"):    # a re-export drops files no longer in the plan
        if old.stem not in fps:
            old.unlink()
    for old in shapes.glob("*") if shapes.exists() else []:
        if not rep["models"].get(old.name):
            old.unlink()
    desc = f"ecadlib export of {plan.name}"
    for kind, table, uri in (("sym", "sym_lib_table", f"{lib}.kicad_sym"), ("fp", "fp_lib_table", f"{lib}.pretty")):
        (out / f"{kind}-lib-table").write_text(
            f'({table}\n\t(version 7)\n\t(lib (name {sexpr.quote(lib)})(type "KiCad")(uri "${{KIPRJMOD}}/{uri}")(options "")(descr {sexpr.quote(desc)}))\n)\n')
    if rep["kicad_content"]:
        (out / "NOTICE.txt").write_text(KICAD_NOTICE)
    rep["footprints"] = {k: fps[k] for k in sorted(fps)}
    rep["symbols"] = len(symbols)
    (out / "kicad_export.json").write_text(json.dumps(rep, indent=2) + "\n")
    return rep


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
    a = ap.parse_args(argv)
    rep = export(a.plan, a.out, a.name, a.include_held, a.lib, CachedHTTP(a.cache, offline=a.offline, timeout=60), rules=a.rules)
    for r in rep["parts"]:
        print(f"  {r['symbol_name']:<28} fp={r['footprint'] or '-':<40} sym={r['symbol'] or 'FAILED'}")
        for n in r["notes"]:
            print(f"      {n}")
    for fpn, f in rep["footprints"].items():
        for n in f["notes"]:
            print(f"  {fpn}: {n}")
    for s in rep["skipped"]:
        print(f"  skipped {s['part']}: {s['why'][:120]}")
    for e in rep["errors"]:
        print(f"  ERROR {e}")
    print(f"{rep['symbols']} symbols, {len(rep['footprints'])} footprints, {sum(1 for v in rep['models'].values() if v)}/{len(rep['models'])} STEP models "
          f"-> {Path(a.out) / (rep['library'] + '.kicad_sym')}; {len(rep['skipped'])} held parts skipped")
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
