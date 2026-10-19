"""More BOM inputs for bom_triage.read_bom: KiCad schematics and netlists, and .xlsx without openpyxl.

    read_kicad_sch(path)   .kicad_sch (KiCad 6-9), following hierarchical sheets (a sheet used twice counts twice)
    read_kicad_netlist(p)  KiCad netlist / BOM export: XML (`kicad-cli sch export netlist --format kicadxml`,
                           `sch export python-bom`, Eeschema's "Generate BOM" intermediate file) or S-expression (.net)
    xlsx_sheets(path)      every sheet of an .xlsx as rows of text, using only zipfile + ElementTree

Each reader returns (lines, mapping) in the shape read_bom returns for a CSV: one line per group of identical parts
({"designator": "R1, R2", "qty": "2", "value": ..., "footprint": ..., "mpn": ..., "manufacturer": ..., "dnp": bool}),
mapping = {field: the KiCad field it came from}. Custom fields (MPN, "Mfr Part #", LCSC, ...) are matched with
bom_triage.map_headers and the rules' [bom] columns, exactly like CSV headers. Like KiCad's own BOM, power symbols
(#PWR, #FLG) and symbols marked "exclude from BOM" are left out; "Do not populate" becomes dnp."""
import re, sys, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from . import sexpr

STD = {"Reference": "designator", "Value": "value", "Footprint": "footprint", "Datasheet": "datasheet",
       "Description": "description"}
SKIP_FIELD = re.compile(r"(ki_.*|Sim\..*|Spice_.*|Sheetname|Sheetfile|Sheet name|Sheet file|exclude_from_.*)", re.I)
MAX_SHEETS = 2000


def _natkey(ref):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", ref)]


def _field_map(names, columns):
    """{field: [KiCad field names]} for the custom fields, matched like CSV headers. Several names for one field
    (MPN on one sheet, "Mfr Part #" on another) are all kept: a part's value is the first one it has. The rules'
    [bom] columns (a name or a list of names) replace the guess for that field."""
    from .bom_triage import _norm, map_headers
    custom = sorted({n for n in names if n not in STD and not SKIP_FIELD.fullmatch(n)})
    skip = ("qty", "designator", "value", "footprint", "datasheet")
    fmap = {f: [h] for f, h in map_headers(custom).items() if f not in skip}
    taken = {h for hs in fmap.values() for h in hs}
    for n in custom:                                           # other names that on their own mean the same field
        for f, h in map_headers([n]).items():
            if f in fmap and n not in taken:
                fmap[f].append(n)
                taken.add(n)
    for f, want in (columns or {}).items():
        wants = [want] if isinstance(want, str) else list(want)
        hit = [n for w in wants for n in custom + list(STD) if _norm(n) == _norm(w)]
        if hit:
            fmap = {g: [x for x in hs if x not in hit] for g, hs in fmap.items()}
            fmap[f] = list(dict.fromkeys(hit))
    return {f: hs for f, hs in fmap.items() if hs}


def group(parts, columns=None, source="KiCad"):
    """parts: [{"ref", "fields": {name: value}, "dnp": bool}] -> (lines, mapping), identical parts grouped."""
    names = {n for p in parts for n in p["fields"]}
    custom = _field_map(names, columns)
    fmap = {f: [k] for k, f in STD.items() if k in names or f in ("designator", "value")}
    fmap.update(custom)
    groups = {}
    for p in parts:
        ln = {f: next((v for v in (str(p["fields"].get(k, "")).strip() for k in ks) if v), "")
              for f, ks in fmap.items() if f != "designator"}
        if ln.get("datasheet") == "~":
            ln["datasheet"] = ""
        dnp = p["dnp"]
        if "dnp" in ln or "fitted" in ln:                      # a house "DNP" / "Fitted" field as well as KiCad's flag
            from .bom_triage import dnp_cell
            dnp = dnp or dnp_cell(ln.pop("dnp", "")) or dnp_cell(ln.pop("fitted", ""), "fitted")
        key = (tuple(sorted(ln.items())), dnp)
        groups.setdefault(key, (ln, dnp, []))[2].append(p["ref"])
    lines = []
    for ln, dnp, refs in groups.values():
        n = len(refs)                                  # 'R?' twice, or a duplicated R1, is two parts (other units of
                                                       # one part never get here)
        refs = sorted(dict.fromkeys(refs), key=_natkey)
        d = {"designator": ", ".join(refs), "qty": str(n)}
        d.update({k: v for k, v in ln.items() if v})
        d["dnp"] = dnp
        if d.get("lcsc") and not re.fullmatch(r"C\d+", d["lcsc"], re.I):
            d["lcsc_invalid"] = d.pop("lcsc")
        lines.append(d)
    lines.sort(key=lambda d: _natkey(d["designator"].split(",")[0]))
    mapping = {f: " | ".join(ks) for f, ks in fmap.items()}
    if not lines:
        raise ValueError(f"no parts found in the {source} file (only power symbols or parts excluded from the BOM)")
    mapping["qty"] = "(count of references)"
    if "dnp" not in mapping:
        mapping["dnp"] = "Do not populate (KiCad flag)"
    return lines, mapping


# ------------------------------------------------------------------ .kicad_sch
def _val(node, head, default=None):
    n = sexpr.first(node, head)
    return n[1] if n and len(n) > 1 and isinstance(n[1], str) else default    # '(ref (x))' is not a reference


def _props(node):
    return {p[1]: p[2] for p in sexpr.find(node, "property")
            if len(p) > 2 and isinstance(p[1], str) and isinstance(p[2], str)}


def _readable(what):
    """A decorator: a malformed file (a list where a string belongs, a missing node) is a clear ValueError naming the
    file, not an internal TypeError / AttributeError."""
    def wrap(fn):
        def run(path, *a, **k):
            try:
                return fn(path, *a, **k)
            except (TypeError, AttributeError, IndexError, KeyError) as e:
                raise ValueError(f"{Path(path).name}: not a readable {what} ({type(e).__name__}: {e})") from None
        run.__name__, run.__doc__ = fn.__name__, fn.__doc__
        return run
    return wrap


def _load_sch(path):
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")     # -sig: a file saved with a BOM mark
    if text.lstrip().startswith("EESchema Schematic File"):
        raise ValueError(f"{Path(path).name}: a KiCad 5 schematic (.sch); open it in KiCad 6 or later and save it, "
                         "or export a BOM / netlist from KiCad")
    try:
        tree = sexpr.parse(text)
    except ValueError as e:
        raise ValueError(f"{Path(path).name}: not a readable KiCad schematic ({e})") from e
    if not isinstance(tree, list) or not tree or tree[0] != "kicad_sch":
        raise ValueError(f"{Path(path).name}: not a KiCad schematic (no kicad_sch header)")
    return tree


@_readable("KiCad schematic")
def read_kicad_sch(path, columns=None, root=None):
    """BOM lines from a KiCad 6+ schematic and all its sub-sheets. root: sub-sheets must be inside this folder (the
    web UI / MCP server pass their workspace, so an uploaded schematic can't read files elsewhere on the server);
    None reads them wherever the schematic says."""
    path = Path(path)
    fence = Path(root).resolve() if root is not None else None
    root = _load_sch(path)
    root_uuid = _val(root, "uuid", "")
    legacy = {}                                                # KiCad 6: (symbol_instances (path "/sheet/sym" (reference ..)))
    for p in sexpr.find(sexpr.first(root, "symbol_instances") or [], "path"):
        legacy[tuple(x for x in str(p[1]).split("/") if x)] = {"reference": _val(p, "reference"), "unit": _val(p, "unit"),
                                                                "value": _val(p, "value"), "footprint": _val(p, "footprint")}
    parts, seen_refs, units, cache, count, dups = [], {}, {}, {}, [0], set()

    def instance_ref(sym, sheet_path):
        props = _props(sym)
        for proj in sexpr.find(sexpr.first(sym, "instances") or [], "project"):    # KiCad 7+: per sheet path
            for p in sexpr.find(proj, "path"):
                parts_ = [x for x in str(p[1]).split("/") if x]
                if parts_ and parts_[0] == root_uuid:
                    parts_ = parts_[1:]
                if tuple(parts_) == sheet_path:
                    return _val(p, "reference") or props.get("Reference", ""), {}
        old = legacy.get(sheet_path + (_val(sym, "uuid", ""),))
        if old:
            return old["reference"] or props.get("Reference", ""), {k: old[k] for k in ("value", "footprint") if old.get(k)}
        return props.get("Reference", ""), {}

    def walk(file, sheet_path, chain):
        count[0] += 1
        if count[0] > MAX_SHEETS:
            raise ValueError(f"{path.name}: more than {MAX_SHEETS} sheet instances; is the hierarchy recursive?")
        rf = file.resolve()
        if rf in chain:
            raise ValueError(f"{path.name}: sheet {file.name} includes itself (recursive hierarchy)")
        if rf not in cache:
            cache[rf] = root if rf == path.resolve() else _load_sch(file)
        tree = cache[rf]
        for sym in sexpr.find(tree, "symbol"):
            if _val(sym, "in_bom", "yes") == "no" or _val(sym, "exclude_from_bom", "no") == "yes":
                continue
            ref, over = instance_ref(sym, sheet_path)
            if not ref or ref.startswith("#"):                 # power symbols and flags
                continue
            if ref.endswith("?"):                              # unannotated: every symbol is its own part (two 'R?'
                if str(_val(sym, "unit", "1")) != "1":         # are two resistors); only units 2.. of a multi-unit
                    continue                                   # part are left out, they belong to its unit 1
                key = (ref, sheet_path, _val(sym, "uuid", "") or id(sym))
            else:
                key = ref
            lib, unit = _val(sym, "lib_id", ""), str(_val(sym, "unit", "1"))
            fields = _props(sym)
            fields.update({"Value" if k == "value" else "Footprint": v for k, v in over.items()})
            fields.pop("Reference", None)
            dnp = _val(sym, "dnp", "no") == "yes"
            if key in seen_refs and lib == units[key][0] and unit not in units[key][1]:
                units[key][1].add(unit)                        # another unit of a multi-unit part: one part, whose
                p = seen_refs[key]                             # fields may be filled on any unit (as KiCad's BOM does)
                for k, v in fields.items():
                    if str(v).strip() and not str(p["fields"].get(k, "")).strip():
                        p["fields"][k] = v
                p["dnp"] = p["dnp"] or dnp
                continue
            if key in seen_refs:                               # another symbol, or the same unit again: a second part
                dups.add(ref)
                key = (key, len(parts))
            units[key] = (lib, {unit})
            seen_refs[key] = part = {"ref": ref, "fields": fields, "dnp": dnp, "sheet": "/".join(sheet_path)}
            parts.append(part)
        for sh in sexpr.find(tree, "sheet"):
            props = _props(sh)
            sub = props.get("Sheetfile") or props.get("Sheet file")
            if not sub:
                continue
            target = (file.parent / sub)
            if not target.is_file():                           # drawn on Windows / macOS: the case may differ
                from .altium_project import _find_file
                target = _find_file(file.parent, sub) or target
            outside = fence is not None and fence != target.resolve() and fence not in target.resolve().parents
            if outside or not target.is_file():                # one message for both: no file-existence oracle
                raise ValueError(f"{path.name}: sub-sheet {sub!r} (sheet {props.get('Sheetname', '?')!r}) not found next to {file.name}")
            walk(target, sheet_path + (_val(sh, "uuid", ""),), chain | {rf})

    walk(path, (), frozenset())
    if dups:
        print(f"  warning: {path.name}: reference(s) {', '.join(sorted(dups, key=_natkey)[:5])} used by more than one "
              "symbol; each is counted as its own part: annotate the schematic", file=sys.stderr)
    unannotated = sorted({p["ref"] for p in parts if p["ref"].endswith("?")})
    if unannotated:
        print(f"  warning: {path.name}: unannotated references ({', '.join(unannotated[:5])}); annotate the schematic "
              "for a reliable BOM", file=sys.stderr)
    return group(parts, columns, "KiCad schematic")


# ------------------------------------------------------------------ KiCad netlist (XML or S-expression)
def _xml_root(data, name):
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise ValueError(f"{name}: XML with a DOCTYPE/ENTITY is not accepted (KiCad never writes one)")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"{name}: not readable XML ({e})") from e


@_readable("KiCad netlist")
def read_kicad_netlist(path, columns=None):
    path = Path(path)
    data = path.read_bytes()
    if data[:3] == b"\xef\xbb\xbf":                       # UTF-8 byte-order mark
        data = data[3:]
    if data.lstrip()[:1] == b"(":
        return _read_net_sexpr(path, data.decode("utf-8", errors="replace"), columns)
    root = _xml_root(data, path.name)
    if root.tag != "export" or root.find("components") is None:
        raise ValueError(f"{path.name}: not a KiCad netlist / BOM XML (expected <export><components>)")
    parts = []
    for c in root.find("components").findall("comp"):
        props = {p.get("name"): p.get("value", "") for p in c.findall("property")}
        if "exclude_from_bom" in props:
            continue
        ref = c.get("ref", "")
        if not ref or ref.startswith("#"):
            continue
        fields = {"Value": c.findtext("value", ""), "Footprint": c.findtext("footprint", ""),
                  "Datasheet": c.findtext("datasheet", "")}
        lib = c.find("libsource")
        desc = c.findtext("description") or props.get("ki_description") or (lib.get("description", "") if lib is not None else "")
        if desc:
            fields["Description"] = desc
        for f in c.findall("fields/field"):
            fields.setdefault(f.get("name", ""), (f.text or "").strip())
            if f.get("name") in ("Value", "Footprint", "Datasheet", "Description") and (f.text or "").strip():
                fields[f.get("name")] = f.text.strip()
        parts.append({"ref": ref, "fields": fields, "dnp": "dnp" in props})
    return group(parts, columns, "KiCad netlist")


def _read_net_sexpr(path, text, columns):
    try:
        tree = sexpr.parse(text)
    except ValueError as e:
        raise ValueError(f"{path.name}: not a readable KiCad netlist ({e})") from e
    if not tree or tree[0] != "export":
        raise ValueError(f"{path.name}: not a KiCad netlist (expected (export ...))")
    parts = []
    for c in sexpr.find(sexpr.first(tree, "components") or [], "comp"):
        props = {_val(p, "name"): _val(p, "value", "") for p in sexpr.find(c, "property")}
        ref = _val(c, "ref", "")
        if "exclude_from_bom" in props or not ref or ref.startswith("#"):
            continue
        fields = {"Value": _val(c, "value", ""), "Footprint": _val(c, "footprint", ""), "Datasheet": _val(c, "datasheet", "")}
        desc = _val(c, "description") or props.get("ki_description") or _val(sexpr.first(c, "libsource") or [], "description")
        if desc:
            fields["Description"] = desc
        for f in sexpr.find(sexpr.first(c, "fields") or [], "field"):
            name = _val(f, "name", "")
            v = next((x for x in f[1:] if not isinstance(x, list)), "")
            fields.setdefault(name, str(v).strip())
        parts.append({"ref": ref, "fields": fields, "dnp": "dnp" in props})
    return group(parts, columns, "KiCad netlist")


def is_kicad_netlist(path):
    """True for a KiCad XML / S-expression netlist or BOM intermediate file (by content, not just the name)."""
    try:
        head = Path(path).read_bytes()[:2048]
    except OSError:
        return False
    if head[:3] == b"\xef\xbb\xbf":
        head = head[3:]
    return bool(re.search(rb"^\s*(<\?xml[^>]*>\s*)?<export\b", head) or re.match(rb"\s*\(export\s", head))


# ------------------------------------------------------------------ .xlsx without openpyxl
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
MAX_CELLS = 2_000_000


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]*", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_hidden(path):
    """{sheet name: None for a hidden sheet, else the set of hidden column indexes (0 = A)} of an .xlsx."""
    from .bom_triage import MAX_UNZIPPED
    out = {}
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
            return out                                         # xlsx_sheets / pandas refuse it with the message
        names = set(z.namelist())
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in _xml_root(z.read("xl/_rels/workbook.xml.rels"), "rels").findall("rel:Relationship", NS):
                t = r.get("Target", "")
                rels[r.get("Id")] = t.lstrip("/") if t.startswith("/") else "xl/" + t
        for i, s in enumerate(_xml_root(z.read("xl/workbook.xml"), "workbook").findall("m:sheets/m:sheet", NS)):
            name = s.get("name", f"Sheet{i + 1}")
            if s.get("state") in ("hidden", "veryHidden"):
                out[name] = None
                continue
            target = rels.get(s.get(f"{{{NS['r']}}}id"), f"xl/worksheets/sheet{i + 1}.xml")
            cols = set()
            if target in names:
                for c in _xml_root(z.read(target), target).findall("m:cols/m:col", NS):
                    if c.get("hidden") in ("1", "true"):
                        cols.update(range(int(c.get("min", 1)) - 1, min(int(c.get("max", 1)), 16384)))
            out[name] = cols
    return out


def xlsx_sheets(path, uncached=None):
    """{sheet name: rows (lists of text)} read with the standard library only. Numbers come back as BOM text the
    way bom_triage writes them (1e-07 -> '100n', 3.0 -> '3'); dates stay as Excel serial numbers. A sheet whose
    grid would exceed MAX_CELLS is refused. uncached: a dict to fill with {sheet name: [cells holding a formula with no saved value]}."""
    from .bom_triage import _plain_number, MAX_UNZIPPED
    path = Path(path)
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ValueError(f"{path.name}: not a readable .xlsx workbook ({e})") from e
    with z:
        names = set(z.namelist())
        total = sum(i.file_size for i in z.infolist())
        if total > MAX_UNZIPPED:
            raise ValueError(f"{path.name}: the workbook unpacks to {total // 2**20} MB; a BOM is far smaller "
                             "(is this the right file?)")

        def xml(name):
            return _xml_root(z.read(name), f"{path.name}:{name}")

        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in xml("xl/sharedStrings.xml").findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in xml("xl/_rels/workbook.xml.rels").findall("rel:Relationship", NS):
                t = r.get("Target", "")
                rels[r.get("Id")] = t.lstrip("/") if t.startswith("/") else "xl/" + t
        out, cells = {}, 0
        for i, s in enumerate(xml("xl/workbook.xml").findall("m:sheets/m:sheet", NS)):
            target = rels.get(s.get(f"{{{NS['r']}}}id"), f"xl/worksheets/sheet{i + 1}.xml")
            if target not in names:
                continue
            rows, sheet = {}, s.get("name", f"Sheet{i + 1}")
            for row in xml(target).findall("m:sheetData/m:row", NS):
                r = int(row.get("r", len(rows) + 1)) - 1
                vals = {}
                for j, c in enumerate(row.findall("m:c", NS)):
                    if uncached is not None and c.find("m:f", NS) is not None and (c.find("m:v", NS) is None or (
                            not c.findtext("m:v", "", NS) and c.get("t") not in ("str", "inlineStr"))):   # '' is a
                                                                   # saved text result; a number is never empty
                        uncached.setdefault(sheet, []).append(c.get("r", "?"))
                    cells += 1
                    if cells > MAX_CELLS:
                        raise ValueError(f"{path.name}: more than {MAX_CELLS} cells; is this really a BOM?")
                    col = _col(c.get("r", "")) if c.get("r") else j
                    t, v = c.get("t", "n"), c.findtext("m:v", None, NS)
                    if t == "s" and v is not None:
                        text = shared[int(v)] if int(v) < len(shared) else ""
                    elif t == "inlineStr":
                        text = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
                    elif t == "b":
                        text = "TRUE" if v == "1" else "FALSE"
                    elif t in ("str", "e"):
                        text = v or ""
                    elif v is not None:
                        try:
                            text = _plain_number(v)
                        except ValueError:
                            text = v
                    else:
                        text = ""
                    vals[col] = text
                if vals:
                    rows[r] = vals
            if not rows:
                out[sheet] = []
                continue
            width = max(max(v) for v in rows.values()) + 1
            if (max(rows) + 1) * width > MAX_CELLS:            # one cell at XFD1048576 is a 17-billion-cell grid
                raise ValueError(f"{path.name}: sheet {sheet!r} spans {max(rows) + 1} rows x {width} columns; "
                                 "is this really a BOM?")
            out[sheet] = [[rows.get(r, {}).get(c, "") for c in range(width)] for r in range(max(rows) + 1)]
        return out
