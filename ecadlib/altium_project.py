"""Altium project integration: the BOM straight from a project, and where each library part is used.

    ecadlib project BOARD.PrjPcb [-o bom.csv] [--variant V] [--json]
    ecadlib where-used --projects DIR [DIR ...] [--part X] [--plan plan.json] [--lib DIR] [--a365 a365_state.json]
        [--lifecycle lifecycle_state.json] [-o out/where_used]

`ecadlib librarian BOARD.PrjPcb` and `ecadlib triage BOARD.PrjPcb` work too: bom_triage.read_bom hands .PrjPcb,
.SchDoc and .BomDoc inputs to bom_lines() here (variant: environment variable ECADLIB_VARIANT, default the base design).

What is read
  .PrjPcb   UTF-8 INI text. [DocumentN] DocumentPath (Windows-relative; .SchDoc and .BomDoc are used),
            [DeviceSheetN] DocumentPath (device sheets: relative, absolute, or Managed\\Sheets\\<guid>\\X.SchDoc; looked
            for next to the project, as written, and in Altium's device-sheet / data folders and $ECADLIB_DEVICE_SHEETS;
            one that is nowhere is a warning saying its components are not in the BOM),
            [ProjectVariantN] Description + VariationN=Designator=..|UniqueId=\\ABCDEFGH|Kind=0 fitted,1 not fitted,
            2 alternate|AlternatePart=..|AltLibLink_*=.. and ParamVariationN (ParameterName/VariantValue, paired with
            ParamDesignatorN; or ParameterCount + ParameterNameK/VariantValueK), [ParameterN] Name/Value.
  .SchDoc   OLE compound file; the "FileHeader" stream is the SchLib record format: a header block then one
            |KEY=VALUE| block per record, OwnerIndex = the owner's position in that list. Read: RECORD=1 components
            (LibReference, DesignItemId, SourceLibraryName, LibraryPath, DatabaseTableName, UniqueID, CurrentPartId,
            PartCount, ComponentDescription, ComponentKind*, VaultGUID/ItemGUID/RevisionGUID and Symbol*GUID for
            Altium 365 components), RECORD=34 designator, RECORD=41 parameters (Comment '=Name' indirection resolved),
            RECORD=44 -> RECORD=45 implementations (ModelType PCBLIB, IsCurrent=T is the footprint in use).
            '%UTF8%Key' fields override their cp1252 twin. Components of kind Graphical, Net Tie (No BOM) and
            Standard (No BOM) are left out of the BOM. Multi-part components (U1A, U1B on any sheet) count once.
            Sheet symbols (RECORD=15, device sheet symbols 215: RECORD=32 name, 33 file name): a sheet used by
            REPEAT(CH,1,4) or by several symbols is multi-channel, and its components are listed once per channel with
            Altium's channel designators ([Design] ChannelDesignatorFormatString, default $Component_$RoomName: R1_CH1).
            A component with no MPN parameter whose Comment reads as a part number (STM32F101T6U6A, not 10k / 100nF)
            gets the Comment as its MPN (mpn_source Comment, a warning, a triage note).
  .BomDoc   ActiveBOM document. Its layout is not published and no sample was available: the reader is tolerant
            (INI sections or |KEY=VALUE| lines with designator / item id + manufacturer part number / supplier
            fields; the first solution is the chosen one, the rest become alternates) and says when it found nothing.

Where-used: every .PrjPcb under the --projects folders (and loose .SchDoc files in folders without one) is read into an
index part -> projects -> variants / designators / revisions. The part key is the normalised MPN (dedupe.mpn_key), else
the Design Item ID, else LibRef+value+footprint. Against a library (--plan librarian plan.json, --lib folders/DbLibs,
--a365 check-a365 state, all through dedupe.Collector, whose duplicate groups are applied too) it flags parts used in
projects but missing from the library, library parts used nowhere and projects using a duplicate instead of the keeper.
Without a library: projects using an older revision of an Altium 365 item than another project does (same ItemGUID,
lower '-N' revision suffix on the Design Item ID, or simply different RevisionGUIDs), and parts whose lifecycle is
NRND / last time buy / obsolete (lifecycle_state.json from `ecadlib lifecycle`, a plan item's lifecycle, or the
component's own 'Lifecycle Status' / 'Part Status' parameter)."""
import argparse, csv, json, os, re, struct, sys
from pathlib import Path, PureWindowsPath

from . import sheetsafe as SS

try:
    from . import rules as RU
except ImportError:                                    # rules.py lands separately; plain defaults until then
    RU = None

PROJECT_EXT = (".prjpcb",)
SCH_EXT = (".schdoc",)
BOM_EXT = (".bomdoc",)
INPUT_EXT = PROJECT_EXT + SCH_EXT + BOM_EXT
NO_BOM_KINDS = {2, 4, 5}       # ComponentKind: 0 standard, 1 mechanical, 2 graphical, 3 net tie (BOM), 4 net tie (no BOM), 5 standard (no BOM)
KIND_LABEL = {0: "Standard", 1: "Mechanical", 2: "Graphical", 3: "Net Tie", 4: "Net Tie (No BOM)", 5: "Standard (No BOM)", 6: "Jumper"}
MPN_PARAMS = ["Manufacturer Part Number", "Manufacturer Part Number 1", "MPN", "Mfr Part Number", "ManufacturerPartNumber",
              "Manufacturer Part #", "Mfr. Part Number", "Part Number", "PartNumber"]
MFR_PARAMS = ["Manufacturer", "Manufacturer 1", "Manufacturer Name", "Mfr", "MFR"]
LCSC_PARAMS = ["LCSC", "LCSC Part", "LCSC Part Number", "LCSC Part #", "JLCPCB Part", "JLCPCB Part #"]
DATASHEET_PARAMS = ["Datasheet", "Datasheet URL", "DatasheetURL", "Datasheet Link", "HelpURL"]
LIFECYCLE_PARAMS = ["Lifecycle Status", "Lifecycle", "Part Status", "Life Cycle", "LifeCycle"]
VALUE_PARAMS = ["Value"]
REPEAT_RE = re.compile(r"\s*repeat\s*\(\s*([^,()]*?)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)
CHANNEL_FORMAT = "$Component_$RoomName"           # Altium's default channel designator format (Project Options > Multi-Channel)
DEVICE_SHEETS_ENV = "ECADLIB_DEVICE_SHEETS"       # extra folders to look for device sheets in (os.pathsep between them)


# ---------------------------------------------------------------- .SchDoc
def _records(data):
    """FileHeader stream -> [dict] (upper-case keys). Binary records (flag byte != 0) become {'_binary': flag}."""
    out, i = [], 0
    while i + 4 <= len(data):
        n = struct.unpack_from("<I", data, i)[0]
        flag, n = n >> 24, n & 0xFFFFFF
        b = data[i + 4:i + 4 + n]
        i += 4 + n
        if flag:
            out.append({"_binary": flag})
            continue
        kv, utf = {}, {}
        for part in b.rstrip(b"\0").split(b"|"):
            if b"=" not in part:
                continue
            k, v = part.split(b"=", 1)
            key = k.decode("latin-1").upper()
            if key.startswith("%UTF8%"):
                utf[key[6:]] = v.decode("utf-8", "replace")
            else:
                kv[key] = v.decode("cp1252", "replace")
        kv.update(utf)
        out.append(kv)
    return out


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _ci(d, names):
    """First non-empty value of d for any of names, case-insensitively."""
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, "") and str(v).strip() not in ("", "*"):
            return str(v).strip()
    return ""


def _mpn_of(d, names):
    """First MPN parameter value that is a part number: Altium vault / Altium 365 item ids (CMP-1035-02800-1, which
    Content Vault parts carry as LibRef, Design Item ID and sometimes 'Part Number') are skipped."""
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        v = str(low.get(n.lower()) or "").strip()
        if v not in ("", "*") and not _is_vault_id(v):
            return v
    return ""


def _param_names(field, defaults, rules):
    names = list(defaults)
    if RU and rules is not None:
        try:
            house = RU.param_name(field, rules)
            if house and house not in names:
                names.insert(0, house)
        except Exception:
            pass
    elif rules:
        house = ((rules.get("parameters") or {}).get("names") or {}).get(field)
        if house and house not in names:
            names.insert(0, house)
    return names


def _resolve(text, params):
    """Altium '=Name' indirection (Comment '=Value', '=Part Number')."""
    if text and text.startswith("=") and len(text) > 1:
        return _ci(params, [text[1:]]) or text
    return text


def read_schdoc(path, rules=None):
    """-> {path, header, components: [...], warnings: [...]}. One entry per RECORD=1 (multi-part parts repeat)."""
    import olefile
    path = Path(path)
    warnings = []
    with olefile.OleFileIO(str(path)) as o:
        if not o.exists("FileHeader"):
            raise ValueError(f"{path.name}: no FileHeader stream; not an Altium schematic")
        recs = _records(o.openstream("FileHeader").read())
    if not recs or "Schematic" not in recs[0].get("HEADER", "") or "Library" in recs[0].get("HEADER", ""):
        raise ValueError(f"{path.name}: FileHeader is not an Altium schematic header ({recs[0].get('HEADER', '')[:60]!r})"
                         if recs else f"{path.name}: empty FileHeader")
    header, recs = recs[0], recs[1:]
    kids = {}
    for j, r in enumerate(recs):
        oi = _int(r.get("OWNERINDEX"), -1)
        if oi >= 0:
            kids.setdefault(oi, []).append(j)
    comps, symbols = [], []
    for idx, r in enumerate(recs):
        rt = r.get("RECORD")
        if rt in ("15", "215"):                       # sheet symbol / device sheet symbol: RECORD=32 name, 33 file name
            name = next((recs[j].get("TEXT", "") for j in kids.get(idx, []) if recs[j].get("RECORD") == "32"), "").strip()
            fname = next((recs[j].get("TEXT", "") for j in kids.get(idx, []) if recs[j].get("RECORD") == "33"), "").strip()
            if fname:
                m = REPEAT_RE.match(name)
                symbols.append({"name": name, "file": fname, "repeat": (m.group(1), int(m.group(2)), int(m.group(3))) if m else None})
            continue
        if rt != "1":
            continue
        params, des, impls = {}, "", []
        for j in kids.get(idx, []):
            c = recs[j]
            if c.get("RECORD") == "34":
                des = c.get("TEXT", "").strip()
            elif c.get("RECORD") == "41" and c.get("NAME"):
                params[c["NAME"]] = c.get("TEXT", "")
            elif c.get("RECORD") == "44":
                for k in kids.get(j, []):
                    m = recs[k]
                    if m.get("RECORD") == "45" and m.get("MODELTYPE", "").upper() == "PCBLIB":
                        impls.append(m)
        cur = next((m for m in impls if m.get("ISCURRENT", "").upper() == "T"), impls[0] if impls else None)
        kind = next((_int(r[k], 0) for k in ("COMPONENTKINDVERSION3", "COMPONENTKINDVERSION2", "COMPONENTKIND") if k in r), 0)
        comment = _resolve(params.get("Comment", ""), params)
        mpn = _mpn_of(params, _param_names("Manufacturer Part Number", MPN_PARAMS, rules))
        did = r.get("DESIGNITEMID", "").strip()
        comps.append({
            "sheet": path.name, "designator": des, "libref": r.get("LIBREFERENCE", ""), "design_item_id": did,
            "source_library": r.get("SOURCELIBRARYNAME", "").strip("*"), "library_path": r.get("LIBRARYPATH", "").strip("*"),
            "database_table": r.get("DATABASETABLENAME", ""), "unique_id": r.get("UNIQUEID", ""),
            "current_part": _int(r.get("CURRENTPARTID"), 1), "part_count": max(_int(r.get("PARTCOUNT"), 2) - 1, 1),
            "description": r.get("COMPONENTDESCRIPTION", ""), "kind": kind, "in_bom": kind not in NO_BOM_KINDS,
            "comment": comment, "value": _ci(params, VALUE_PARAMS) or comment,
            "footprint": (cur or {}).get("MODELNAME", ""), "footprints": [m.get("MODELNAME", "") for m in impls],
            "mpn": mpn, "manufacturer": _ci(params, _param_names("Manufacturer", MFR_PARAMS, rules)),
            "lcsc": _ci(params, LCSC_PARAMS), "datasheet": _datasheet(params),
            "supplier": _ci(params, ["Supplier 1", "Supplier"]), "supplier_pn": _ci(params, ["Supplier Part Number 1", "Supplier Part Number"]),
            "lifecycle": _ci(params, LIFECYCLE_PARAMS),
            "vault_guid": r.get("VAULTGUID", ""), "item_guid": r.get("ITEMGUID", ""), "revision_guid": r.get("REVISIONGUID", ""),
            "symbol_item_guid": r.get("SYMBOLITEMGUID", ""), "symbol_revision_guid": r.get("SYMBOLREVISIONGUID", ""),
            "params": params,
        })
        if not des:
            warnings.append(f"{path.name}: component {r.get('LIBREFERENCE', '?')} ({r.get('UNIQUEID', '')}) has no designator")
        elif des.endswith("?"):
            warnings.append(f"{path.name}: {des} ({r.get('LIBREFERENCE', '')}) is not annotated")
    return {"path": str(path), "header": header, "components": comps, "warnings": warnings, "sheet_symbols": symbols}


def _datasheet(params):
    d = _ci(params, DATASHEET_PARAMS)
    if d:
        return d
    for k, v in params.items():                       # ComponentLink<N>Description=Datasheet + ComponentLink<N>URL
        m = re.fullmatch(r"ComponentLink(\d+)Description", k, re.I)
        if m and "datasheet" in v.lower():
            return _ci(params, [f"ComponentLink{m.group(1)}URL"])
    return ""


# ---------------------------------------------------------------- .PrjPcb
def _read_text(p):
    data = Path(p).read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def parse_ini(text):
    """-> [(section, [(key, value)])], order and duplicate keys kept (configparser would refuse those)."""
    out, cur = [], None
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            continue
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = (s[1:-1], [])
            out.append(cur)
            continue
        if cur is None:
            cur = ("", [])
            out.append(cur)
        k, _, v = line.partition("=")
        cur[1].append((k.strip(), v))
    return out


def _fields(raw):
    """'Designator=R1|UniqueId=\\X|Kind=1' -> dict (split on the first '=': values may start with '=')."""
    out = {}
    for seg in raw.split("|"):
        k, _, v = seg.partition("=")
        if k:
            out[k.strip()] = v
    return out


def _find_file(base, rel):
    p = Path(base) / Path(*PureWindowsPath(rel).parts) if rel else None
    if p is None:
        return None
    if p.exists():
        return p
    cur = Path(base)                                  # Windows paths are case-insensitive: match part by part
    for part in PureWindowsPath(rel).parts:
        if part == "..":
            cur = cur.parent
            continue
        if not cur.is_dir():
            return None
        hit = next((c for c in cur.iterdir() if c.name.lower() == part.lower()), None)
        if hit is None:
            return None
        cur = hit
    return cur


def read_prjpcb(path):
    """-> {path, name, documents, schdocs, bomdocs, missing, variants, parameters, design}."""
    path = Path(path)
    secs = parse_ini(_read_text(path))
    docs, devs, variants, params, design = [], [], [], {}, {}
    for name, kv in secs:
        d = dict(kv)
        if name.lower() == "design":
            design = d
        elif re.fullmatch(r"Document\d+", name, re.I) and d.get("DocumentPath"):
            docs.append(d["DocumentPath"])
        elif re.fullmatch(r"DeviceSheet\d+", name, re.I) and d.get("DocumentPath"):
            devs.append(d["DocumentPath"])
        elif re.fullmatch(r"Parameter\d+", name, re.I) and d.get("Name"):
            params[d["Name"]] = d.get("Value", "")
        elif re.fullmatch(r"ProjectVariant\d+", name, re.I):
            vs = [_fields(d[f"Variation{i}"]) for i in range(1, _int(d.get("VariationCount")) + 1) if f"Variation{i}" in d]
            pvs = []
            for i in range(1, _int(d.get("ParamVariationCount")) + 1):
                raw = d.get(f"ParamVariation{i}")
                if raw is None:
                    continue
                f = _fields(raw)
                des = d.get(f"ParamDesignator{i}") or f.get("Designator", "")
                n = _int(f.get("ParameterCount"), 0)
                if n:
                    for k in range(1, n + 1):
                        pvs.append({"designator": des, "unique_id": f.get("UniqueId", ""), "name": f.get(f"ParameterName{k}", ""),
                                    "value": f.get(f"VariantValue{k}", "")})
                else:
                    pvs.append({"designator": des, "unique_id": f.get("UniqueId", ""), "name": f.get("ParameterName", ""),
                                "value": f.get("VariantValue", "")})
            variants.append({"name": d.get("Description") or name, "unique_id": d.get("UniqueId", ""),
                             "allow_fabrication": d.get("AllowFabrication", "") == "1", "variations": vs, "param_variations": pvs})
    sch, bom, missing, dev_missing = [], [], [], []
    for rel in docs:
        ext = PureWindowsPath(rel).suffix.lower()
        if ext not in SCH_EXT + BOM_EXT:
            continue
        f = _find_file(path.parent, rel)
        if f is None:
            missing.append(rel)
        else:
            (sch if ext in SCH_EXT else bom).append(f)
    for rel in devs:
        if PureWindowsPath(rel).suffix.lower() not in SCH_EXT:
            continue
        f = find_device_sheet(path.parent, rel)
        if f is None:
            dev_missing.append(rel)
        elif not any(Path(f).resolve() == Path(x).resolve() for x in sch):
            sch.append(f)
    return {"path": str(path), "name": path.stem, "documents": docs + devs, "device_sheets": devs, "schdocs": sch, "bomdocs": bom,
            "missing": missing, "device_missing": dev_missing, "variants": variants, "parameters": params, "design": design}


def device_sheet_dirs(environ=None):
    """Folders device sheets usually live in: $ECADLIB_DEVICE_SHEETS, Altium's 'Device Sheets' library folders under
    %PUBLIC% / your Documents, and Altium's data folders under %APPDATA% / %LOCALAPPDATA% / %PROGRAMDATA% (managed
    device sheets are cached there)."""
    env = os.environ if environ is None else environ
    out = [Path(x) for x in (env.get(DEVICE_SHEETS_ENV) or "").split(os.pathsep) if x.strip()]
    for var, pats in (("PUBLIC", ["Documents/Altium/*/Library/Device Sheets", "Documents/Altium/*/Library"]),
                      ("USERPROFILE", ["Documents/Altium/*/Library/Device Sheets", "Documents/Altium"]),
                      ("APPDATA", ["Altium*"]), ("LOCALAPPDATA", ["Altium*"]), ("PROGRAMDATA", ["Altium*"])):
        if env.get(var):
            for pat in pats:
                out += sorted(Path(env[var]).glob(pat))
    return [d for d in dict.fromkeys(out) if d.is_dir()]


def _find_named(root, name, limit=50000):
    """A file called `name` (any case) under root, depth first, giving up after `limit` entries."""
    n, want = 0, name.lower()
    for dirpath, dirnames, filenames in os.walk(root):
        n += len(dirnames) + len(filenames)
        hit = next((f for f in filenames if f.lower() == want), None)
        if hit:
            return Path(dirpath) / hit
        if n > limit:
            return None
    return None


def find_device_sheet(base, rel, dirs=None):
    """A [DeviceSheetN] DocumentPath on this machine: relative to the project, the absolute path as written, the same
    relative path (Managed\\Sheets\\<guid>\\X.SchDoc ...) under each device-sheet folder, then the file name anywhere
    under those folders and the project's folder. None when it is nowhere."""
    f = _find_file(base, rel)
    if f is not None:
        return f
    w = PureWindowsPath(rel)
    if w.is_absolute() and Path(rel).is_file():
        return Path(rel)
    dirs = device_sheet_dirs() if dirs is None else list(dirs)
    tail = PureWindowsPath(*w.parts[1:]) if w.is_absolute() else w          # the path without its drive
    for d in dirs:
        for k in range(len(tail.parts)):                                  # ...\Library\Device Sheets\X.SchDoc: try shorter tails
            f = _find_file(d, str(PureWindowsPath(*tail.parts[k:])))
            if f is not None and f.is_file():
                return f
    for d in [Path(base)] + dirs:
        f = _find_named(d, w.name)
        if f is not None:
            return f
    return None


# ---------------------------------------------------------------- .BomDoc (ActiveBOM)
def read_bomdoc(path):
    """Tolerant ActiveBOM reader -> {items: [{designators, design_item_id, chosen: {mpn, manufacturer, supplier,
    supplier_pn}, alternates: [...]}], warnings}. Unverified against a real .BomDoc (see module docstring)."""
    path = Path(path)
    text = _read_text(path) if b"\0" not in Path(path).read_bytes()[:64] or Path(path).read_bytes()[:2] in (b"\xff\xfe", b"\xfe\xff") else ""
    warnings, items, recs = [], [], []
    if not text:
        return {"items": [], "warnings": [f"{path.name}: binary BomDoc, not read (only text ActiveBOM documents are supported)"]}
    for name, kv in parse_ini(text):
        if any("|" in v and "=" in v for _, v in kv):
            for k, v in kv:
                f = _fields(v) if "|" in v else {}
                if f:
                    recs.append(f)
        recs.append({k: v for k, v in kv})
    for line in text.splitlines():                    # bare '|KEY=VALUE|...' lines
        if line.startswith("|") and "=" in line:
            recs.append(_fields(line.strip("|")))
    for f in recs:
        low = {k.lower().replace(" ", "").replace("_", ""): v.strip() for k, v in f.items()}
        des = next((low[k] for k in ("designator", "designators", "refdes") if low.get(k)), "")
        did = next((low[k] for k in ("designitemid", "itemid", "libref", "partid") if low.get(k)), "")
        sols = {}
        for k, v in low.items():
            m = re.fullmatch(r"(manufacturerpartnumber|mfrpartnumber|mpn|manufacturer|mfr|suppliername|supplier|"
                             r"supplierpartnumber|supplierordercode|supplierpn)(\d*)", k)
            if m and v:
                fld = {"manufacturerpartnumber": "mpn", "mfrpartnumber": "mpn", "mpn": "mpn", "manufacturer": "manufacturer",
                       "mfr": "manufacturer", "suppliername": "supplier", "supplier": "supplier"}.get(m.group(1), "supplier_pn")
                sols.setdefault(_int(m.group(2), 1), {}).setdefault(fld, v)
        sols = [s for _, s in sorted(sols.items()) if s.get("mpn")]
        if (des or did) and sols:
            items.append({"designators": _split_refs(des), "design_item_id": did, "chosen": sols[0], "alternates": sols[1:]})
    if not items:
        warnings.append(f"{path.name}: no ActiveBOM line items recognised (format unverified); schematic values used")
    return {"items": items, "warnings": warnings}


# ---------------------------------------------------------------- project model + variants
def _split_refs(s):
    out = []
    for tok in re.split(r"[,;\s]+", s or ""):
        m = re.fullmatch(r"([A-Za-z_]+)(\d+)-(?:\1)?(\d+)", tok)
        if m and int(m.group(3)) >= int(m.group(2)) and int(m.group(3)) - int(m.group(2)) < 1000:
            out += [f"{m.group(1)}{i}" for i in range(int(m.group(2)), int(m.group(3)) + 1)]
        elif tok:
            out.append(tok)
    return out


def _natkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def load_project(path, rules=None):
    """.PrjPcb / .SchDoc / .BomDoc -> {name, path, components (one per physical part), variants, warnings, bomdoc}."""
    p = Path(path)
    ext = p.suffix.lower()
    warnings, bomdocs = [], []
    if ext in BOM_EXT:                                # a BomDoc names no project file: use the one next to it
        prj = sorted(p.parent.glob("*.[Pp][Rr][Jj][Pp][Cc][Bb]"))
        if not prj:
            raise ValueError(f"{p.name}: no .PrjPcb next to the BomDoc; pass the project instead")
        proj = read_prjpcb(prj[0])
        bomdocs = [p]
    elif ext in PROJECT_EXT:
        proj = read_prjpcb(p)
        bomdocs = proj["bomdocs"]
    elif ext in SCH_EXT:
        proj = {"path": str(p), "name": p.stem, "schdocs": [p], "bomdocs": [], "missing": [], "variants": [], "parameters": {},
                "documents": [p.name], "design": {}}
    else:
        raise ValueError(f"{p.name}: not an Altium project, schematic or BomDoc")
    warnings += [f"{Path(proj['path']).name}: document {m} not found" for m in proj["missing"]]
    warnings += [f"{Path(proj['path']).name}: device sheet {m} not found (looked next to the project and in Altium's device "
                 f"sheet folders; its components are NOT in this BOM). Fix: put the sheet there or add its folder to "
                 f"{DEVICE_SHEETS_ENV}" for m in proj.get("device_missing", [])]
    if not proj["schdocs"]:
        warnings.append(f"{Path(proj['path']).name}: no schematic documents found")
    raw, read = [], []
    for f in proj["schdocs"]:
        try:
            s = read_schdoc(f, rules)
        except Exception as e:                        # one unreadable sheet should not hide the rest
            warnings.append(f"{Path(f).name}: {type(e).__name__}: {e}")
            continue
        read.append(s)
        warnings += s["warnings"]
    fmt = (proj.get("design") or {}).get("ChannelDesignatorFormatString") or CHANNEL_FORMAT
    for s in read:
        raw += expand_channels(s, read, fmt, warnings)
    comps, by_des = [], {}
    for c in raw:                                     # multi-part: U1 part A on one sheet, part B on another -> one part
        k = c["designator"] if c["designator"] and not c["designator"].endswith("?") else f"?{c['sheet']}:{c['unique_id']}"
        prev = by_des.get(k)
        if prev:
            same = (prev["design_item_id"], prev["libref"]) == (c["design_item_id"], c["libref"])
            if same and (prev["part_count"] > 1 or c["part_count"] > 1):
                prev["parts_placed"] += 1
                prev["sheets"] = sorted(set(prev["sheets"]) | {c["sheet"]})
                continue
            warnings.append(f"duplicate designator {c['designator']} ({prev['sheet']}: {prev['libref']}, {c['sheet']}: {c['libref']})")
            k = f"{k}@{c['sheet']}:{c['unique_id']}"
        c = dict(c, parts_placed=1, sheets=[c["sheet"]])
        by_des[k] = c
        comps.append(c)
    bom_items = []
    for b in bomdocs:
        r = read_bomdoc(b)
        bom_items += r["items"]
        warnings += r["warnings"]
    if bom_items:                                     # ActiveBOM's chosen solution wins over the schematic parameter
        for c in comps:
            hit = next((it for it in bom_items if c["designator"] in it["designators"]), None) or \
                  next((it for it in bom_items if not it["designators"] and it["design_item_id"] and it["design_item_id"] == c["design_item_id"]), None)
            if hit:
                ch = hit["chosen"]
                c.update(mpn=ch["mpn"] if ch.get("mpn") and not _is_vault_id(ch["mpn"]) else c["mpn"], manufacturer=ch.get("manufacturer") or c["manufacturer"],
                         supplier=ch.get("supplier") or c["supplier"], supplier_pn=ch.get("supplier_pn") or c["supplier_pn"],
                         alternates=hit["alternates"], mpn_source="BomDoc")
    for c in comps:
        c.setdefault("alternates", [])
        if not c.get("mpn_source"):
            c["mpn_source"] = "parameter" if c["mpn"] else ""
        if not c["mpn"] and c["design_item_id"] and not _is_vault_id(c["design_item_id"]) and c["source_library"].lower().endswith((".dblib", ".svndblib")):
            c["mpn"], c["mpn_source"] = c["design_item_id"], "DesignItemId"   # a DbLib's key is usually the part number
        if not c["mpn"] and c["in_bom"] and looks_like_mpn(c["comment"]):
            c["mpn"], c["mpn_source"] = c["comment"].strip(), "Comment"          # Content Vault parts: no MPN parameter
    by_comment = sorted({c["designator"] for c in comps if c.get("mpn_source") == "Comment"}, key=_natkey)
    if by_comment:
        warnings.append(f"no Manufacturer Part Number parameter on {len(by_comment)} component(s) "
                        f"({', '.join(by_comment[:8])}{' ...' if len(by_comment) > 8 else ''}): their Comment is used as the MPN")
    return {"name": proj["name"], "path": proj["path"], "components": comps, "variants": proj["variants"],
            "parameters": proj["parameters"], "warnings": list(dict.fromkeys(warnings)),
            "bomdocs": [str(b) for b in bomdocs], "sheets": [str(s) for s in proj["schdocs"]]}


VALUE_RE = re.compile(r"(?i)^[0-9]+([.,][0-9]+)?\s*([pnuµμmkKMGRL]|meg)?[0-9]*\s*(f|h|r|Ω|ohms?|v|a|w|hz|%|ppm)?"
                      r"([/_\-±+][0-9a-zµμΩ.%±/_\-+]*)?$")


def looks_like_mpn(s):
    """A Comment that reads as a manufacturer part number: one word of letters and digits (- _ . / # + allowed), at least
    5 characters with 2 letters and 2 digits, not '=Param', not a plain value (10k, 100nF, 4k7, 1uF/16V, 0R) and not
    an Altium vault item id (CMP-1035-02800-1: a Content Vault part's LibRef, shown as its Comment)."""
    s = (s or "").strip()
    if not s or s.startswith("=") or _is_vault_id(s) or len(s) < 5 or len(s) > 60 or not re.fullmatch(r"[A-Za-z0-9][-A-Za-z0-9_./#+]*", s):
        return False
    if sum(ch.isalpha() for ch in s) < 2 or sum(ch.isdigit() for ch in s) < 2:
        return False
    return not VALUE_RE.match(s)


def _channel_paths(sheet, read, seen=()):
    """Room-name paths of every instance of a sheet: () for a top sheet; one entry per channel otherwise."""
    name = Path(sheet["path"]).name.lower()
    parents = [(p, sym) for p in read for sym in p.get("sheet_symbols", [])
               if PureWindowsPath(sym["file"]).name.lower() == name and p is not sheet]
    if not parents or name in seen:
        return [()]
    out = []
    for p, sym in parents:
        rooms = ([f"{sym['repeat'][0]}{i}" for i in range(sym["repeat"][1], sym["repeat"][2] + 1)] if sym["repeat"]
                 else [sym["name"] or PureWindowsPath(sym["file"]).stem])
        out += [pp + (r,) for pp in _channel_paths(p, read, seen + (name,)) for r in rooms]
    return out


def channel_designator(fmt, des, room, index):
    """Altium's channel designator: fmt tokens $Component, $ComponentPrefix, $ComponentIndex, $RoomName, $ChannelIndex,
    $ChannelAlpha (R1 in channel CH2 with '$Component_$RoomName' -> R1_CH2)."""
    m = re.match(r"([^\d]*)(\d*)(.*)", des)
    alpha = ""
    k = index
    while k:
        k, r = divmod(k - 1, 26)
        alpha = chr(65 + r) + alpha
    vals = {"$ComponentPrefix": m.group(1), "$ComponentIndex": m.group(2) + m.group(3), "$Component": des, "$RoomName": room,
            "$ChannelIndex": str(index), "$ChannelAlpha": alpha}
    return re.sub("|".join(re.escape(k) for k in sorted(vals, key=len, reverse=True)), lambda t: vals[t.group(0)], fmt)


def unique_rooms(rooms):
    """Room names made unique the way Altium numbers channels: sheet symbols that all carry the same name (six
    symbols 'Red_LED' on one sheet) become Red_LED1 ... Red_LED6, so every channel's designators differ. Names that are
    already unique are kept."""
    count = {}
    for r in rooms:
        count[r] = count.get(r, 0) + 1
    taken, seen, out = set(rooms), {}, []
    for r in rooms:
        if count[r] == 1:
            out.append(r)
            continue
        k = seen.get(r, 0)
        while True:
            k += 1
            cand = f"{r}{k}"
            if cand not in taken:
                break
        seen[r] = k
        taken.add(cand)
        out.append(cand)
    return out


def expand_channels(sheet, read, fmt=CHANNEL_FORMAT, warnings=None):
    """The sheet's components, once per channel when sheet symbols use it more than once (REPEAT(CH,1,4), or several
    symbols naming the same file): designators per Altium's channel format, e.g. R1_CH1 ... R1_CH4."""
    paths = _channel_paths(sheet, read)
    comps = sheet["components"]
    if len(paths) <= 1:
        return list(comps)
    while all(len(p) > 1 for p in paths) and len({p[0] for p in paths}) == 1:
        paths = [p[1:] for p in paths]                 # a level every instance shares (one sheet symbol) names nothing
    rooms = unique_rooms(["_".join(p) for p in paths])
    out = []
    for c in comps:
        for i, room in enumerate(rooms, 1):
            des = channel_designator(fmt, c["designator"], room, i) if c["designator"] else c["designator"]
            out.append(dict(c, designator=des, unique_id=f"{c['unique_id']}@{room}", channel=room))
    if warnings is not None:
        name = Path(sheet["path"]).name
        ex = channel_designator(fmt, comps[0]["designator"], rooms[0], 1) if comps and comps[0]["designator"] else ""
        warnings.append(f"{name}: multi-channel sheet, {len(rooms)} channels ({', '.join(rooms[:6])}{' ...' if len(rooms) > 6 else ''}): "
                        f"{len(comps)} components counted once per channel" + (f", designators like {ex} ({fmt})" if ex else "")
                        + ("; nested channels joined with _" if any(len(p) > 1 for p in paths) else "")
                        + ("; sheet symbols with the same name numbered 1, 2, ..." if len(set("_".join(p) for p in paths)) < len(paths) else ""))
    return out


VAULT_ID_RE = re.compile(r"(?i)[A-Z]{2,5}-\d{3,}(-\d+)+(\.\d+)?")


def is_vault_id(s):
    """Altium vault / Altium 365 item(-revision) ids: CMP-1035-02800-1, CMP-003-000000-1, PCC-011-0000-2, SYM-001-0001.
    Never a manufacturer part number."""
    return bool(VAULT_ID_RE.fullmatch(str(s or "").strip()))


_is_vault_id = is_vault_id


def variant_of(project, name):
    if not name:
        return None
    v = next((v for v in project["variants"] if v["name"].lower() == name.lower()), None)
    if v is None:
        raise ValueError(f"no variant {name!r} in {Path(project['path']).name}; variants: "
                         f"{', '.join(v['name'] for v in project['variants']) or 'none'}")
    return v


def apply_variant(comps, variant):
    """-> copies of comps with 'fitted' and variant overrides (alternate part, parameter values)."""
    out = []
    for c in comps:
        c = dict(c, fitted=c["in_bom"], variant_note="")
        if variant:
            uid = c["unique_id"].split("@")[0]               # a channel copy: <UniqueID>@<room>
            by_uid = [v for v in variant["variations"] if v.get("UniqueId") and v["UniqueId"].rsplit("\\", 1)[-1] == uid]
            hit = next((v for v in by_uid if v.get("Designator") == c["designator"]), None) or \
                  next(iter(by_uid), None) or \
                  next((v for v in variant["variations"] if not v.get("UniqueId") and v.get("Designator") == c["designator"]), None)
            if hit:
                kind = _int(hit.get("Kind"), 0)
                if kind == 1:
                    c["fitted"], c["variant_note"] = False, "not fitted"
                elif kind == 2:
                    alt = next((v for k, v in hit.items() if re.search(r"designitemid|libreference|libref", k, re.I) and v), "") \
                        or hit.get("AlternatePart", "")
                    c["variant_note"] = f"alternate part {alt}".strip()
                    if alt and not alt.startswith("="):
                        c.update(design_item_id=alt, mpn=alt if c["mpn_source"] in ("DesignItemId", "") else c["mpn"],
                                 libref=next((v for k, v in hit.items() if re.search(r"libreference", k, re.I) and v), c["libref"]))
            params = dict(c["params"])
            for pv in variant["param_variations"]:
                if (pv["unique_id"] and pv["unique_id"].rsplit("\\", 1)[-1] == uid and (not c.get("channel") or pv["designator"] in ("", c["designator"]))) or (not pv["unique_id"] and pv["designator"] == c["designator"]):
                    params[pv["name"]] = pv["value"]
            if params != c["params"]:
                changed = {k for k in params if params[k] != c["params"].get(k)}
                c["params"] = params
                mpn = _mpn_of({k: params[k] for k in changed}, MPN_PARAMS)
                c["comment"] = _resolve(params.get("Comment", c["comment"]), params)
                c["value"] = _ci(params, VALUE_PARAMS) or c["comment"]
                if mpn:
                    c["mpn"] = mpn
                c["variant_note"] = (c["variant_note"] + "; parameters changed").strip("; ")
        out.append(c)
    return out


def _group_key(c):
    from .dedupe import mpn_key
    if c["mpn"]:
        return "MPN:" + mpn_key(c["mpn"])
    if c["design_item_id"]:
        return "ID:" + c["design_item_id"].upper()
    return "LIB:" + "|".join(x.upper() for x in (c["libref"], c["value"], c["footprint"]))


def bom_groups(project, variant=None):
    """Grouped BOM lines (fitted ones, then one DNP line per not-fitted group) with per-variant quantities."""
    comps = [c for c in project["components"] if c["in_bom"]]
    base = apply_variant(comps, variant_of(project, variant))
    per_var = {v["name"]: {c["designator"]: c["fitted"] for c in apply_variant(comps, v)} for v in project["variants"]}
    groups = {}
    for c in base:
        g = groups.setdefault((_group_key(c), c["fitted"]), {"comps": [], "fitted": c["fitted"]})
        g["comps"].append(c)
    out = []
    for (key, fitted), g in sorted(groups.items(), key=lambda kv: (not kv[0][1], _natkey(kv[1]["comps"][0]["designator"]))):
        cs = sorted(g["comps"], key=lambda c: _natkey(c["designator"]))
        first = cs[0]
        refs = [c["designator"] for c in cs]
        pick = lambda f: next((c[f] for c in cs if c.get(f)), "")
        out.append({
            "key": key, "designator": ", ".join(refs), "qty": str(len(refs)) if fitted else "0", "count": len(refs),
            "mpn": pick("mpn"), "manufacturer": pick("manufacturer"), "value": first["value"] or first["comment"],
            "footprint": pick("footprint"), "description": pick("description"), "libref": first["libref"],
            "design_item_id": pick("design_item_id"), "source_library": pick("source_library"), "lcsc": pick("lcsc"),
            "datasheet": pick("datasheet"), "supplier": pick("supplier"), "supplier_pn": pick("supplier_pn"),
            "lifecycle": pick("lifecycle"), "item_guid": pick("item_guid"), "revision_guid": pick("revision_guid"),
            "vault_guid": pick("vault_guid"), "mpn_source": pick("mpn_source"), "dnp": not fitted,
            "alternates": "; ".join(dict.fromkeys(a.get("mpn", "") for c in cs for a in c.get("alternates", []) if a.get("mpn"))),
            "variant_notes": "; ".join(dict.fromkeys(f"{c['designator']}: {c['variant_note']}" for c in cs if c["variant_note"])),
            "sheets": ", ".join(sorted({s for c in cs for s in c["sheets"]})),
            "variants": {vn: sum(1 for r in refs if fit.get(r)) for vn, fit in per_var.items()},
        })
    return out


LINE_FIELDS = ("designator", "qty", "mpn", "manufacturer", "value", "footprint", "description", "libref", "lcsc", "datasheet")
MAPPING = {"designator": "Designator (RECORD=34)", "qty": "count of designators", "mpn": "MPN parameter / ActiveBOM / DbLib DesignItemId",
           "manufacturer": "Manufacturer parameter", "value": "Comment / Value parameter", "footprint": "current PCB model (RECORD=45)",
           "description": "ComponentDescription", "libref": "LibReference", "lcsc": "LCSC parameter", "datasheet": "Datasheet / ComponentLink",
           "dnp": "variant (Kind=1) / component kind"}


def bom_lines(path, variant=None, rules=None):
    """bom_triage.read_bom hook: (lines, mapping) in the same shape read_bom returns for a CSV."""
    prj = load_project(path, rules)
    for w in prj["warnings"]:
        print(f"  warning: {w}", file=sys.stderr)
    lines = []
    for g in bom_groups(prj, variant):
        ln = {f: g[f] for f in LINE_FIELDS if g.get(f)}
        ln["designator"], ln["qty"] = g["designator"], g["qty"] if not g["dnp"] else str(g["count"])
        ln["dnp"] = g["dnp"]
        for k in ("design_item_id", "item_guid", "revision_guid", "supplier", "supplier_pn", "lifecycle"):
            if g.get(k):
                ln[f"altium_{k}"] = g[k]
        if g.get("mpn_source") == "Comment":
            ln["altium_mpn_source"] = "Comment"
        lines.append(ln)
    if not lines:
        raise ValueError(f"{Path(path).name}: no components found in the project's schematics")
    return lines, dict(MAPPING)


CSV_COLS = [("Designator", "designator"), ("Quantity", "qty"), ("Manufacturer Part Number", "mpn"), ("Manufacturer", "manufacturer"),
            ("Comment", "value"), ("Footprint", "footprint"), ("Description", "description"), ("LibRef", "libref"),
            ("Design Item ID", "design_item_id"), ("Source Library", "source_library"), ("Supplier 1", "supplier"),
            ("Supplier Part Number 1", "supplier_pn"), ("LCSC", "lcsc"), ("Datasheet", "datasheet"), ("Lifecycle Status", "lifecycle"),
            ("Alternates", "alternates"), ("Item GUID", "item_guid"), ("Revision GUID", "revision_guid"), ("Sheets", "sheets"),
            ("Variant Notes", "variant_notes"), ("DNP", "dnp")]


def write_bom_csv(groups, out, variants=()):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = SS.writer(f)
        w.writerow([h for h, _ in CSV_COLS] + [f"Qty [{v}]" for v in variants])
        for g in groups:
            w.writerow([("DNP" if g[k] else "") if k == "dnp" else g.get(k, "") for _, k in CSV_COLS] +
                       [g["variants"].get(v, 0) for v in variants])
    return out


# ---------------------------------------------------------------- where-used
def find_projects(dirs):
    """Every .PrjPcb under dirs; loose .SchDoc files (not listed by a project and with no .PrjPcb in their folder or
    the folders above it, up to the scanned one) count as one-sheet projects."""
    found, loose, claimed = [], [], set()
    for d in dirs:
        d = Path(d)
        if d.is_file():
            found.append(d)
            continue
        if not d.is_dir():
            raise FileNotFoundError(f"{d}: not found")
        files = sorted(p for p in d.rglob("*") if p.is_file())
        prj_dirs = set()
        for p in files:
            if p.suffix.lower() in PROJECT_EXT:
                found.append(p)
                prj_dirs.add(p.parent.resolve())
                try:
                    claimed |= {Path(s).resolve() for s in read_prjpcb(p)["schdocs"]}
                except (OSError, ValueError):
                    pass
        root = d.resolve()
        for p in files:
            if p.suffix.lower() in SCH_EXT:
                rp = p.resolve()
                above = [rp.parent] + [a for a in rp.parent.parents if a == root or root in a.parents]
                if rp not in claimed and not any(a in prj_dirs for a in above):
                    loose.append(p)
    return list(dict.fromkeys(found + loose))


def _rev_suffix(did):
    m = re.search(r"-(\d+)$", did or "")
    return int(m.group(1)) if m and _is_vault_id(did) else None


def load_library(plan=None, libs=(), a365=None, log=None):
    """dedupe.Collector over a librarian plan / library folders / Altium 365 state -> (parts, groups, errors)."""
    from . import dedupe as DD
    col = DD.Collector(DD.load_config(None, Path(plan).parent if plan else None), log or (lambda *a, **k: None))
    if plan:
        col.add_plan(json.loads(Path(plan).read_text()), Path(plan).parent)
        a365 = a365 or (Path(plan).parent / "a365_state.json" if (Path(plan).parent / "a365_state.json").exists() else None)
    for lib in libs:
        col.add_lib(lib)
    guids = {}
    if a365:
        col.add_a365(a365)
        data = json.loads(Path(a365).read_text())     # any GUIDs the state carries map back to the component name
        for name, snap in (data.items() if isinstance(data, dict) else ((n.get("name"), n) for n in data)):
            for g in re.findall(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", json.dumps(snap)):
                guids[g.upper()] = name
    return col.parts, DD.find_groups(col), col.errors, guids


def load_lifecycle(path):
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text())
    return {k: v for k, v in (data.get("parts") or {}).items()}


def where_used(dirs, part=None, plan=None, libs=(), a365=None, lifecycle=None, rules=None, log=None):
    """-> {parts: {key: entry}, flags: [...], projects: [...], library: bool, warnings: [...]}"""
    from .dedupe import mpn_key
    from .kicad_index import norm_mpn
    from .lifecycle import SEVERITY, normalise
    projects, warnings, parts = [], [], {}
    for f in find_projects(dirs):
        try:
            prj = load_project(f, rules)
        except Exception as e:
            warnings.append(f"{f}: {type(e).__name__}: {e}")
            continue
        projects.append({"name": prj["name"], "path": prj["path"], "variants": [v["name"] for v in prj["variants"]],
                         "components": len(prj["components"])})
        warnings += [f"{prj['name']}: {w}" for w in prj["warnings"]]
        comps = [c for c in prj["components"] if c["in_bom"]]
        fits = {v["name"]: {c["designator"]: c["fitted"] for c in apply_variant(comps, v)} for v in prj["variants"]}
        for c in comps:
            key = _group_key(c)
            e = parts.setdefault(key, {"key": key, "mpn": c["mpn"], "manufacturer": c["manufacturer"], "design_item_ids": set(),
                                       "librefs": set(), "item_guids": set(), "footprints": set(), "lifecycle_param": set(), "uses": {}})
            e["mpn"] = e["mpn"] or c["mpn"]
            e["manufacturer"] = e["manufacturer"] or c["manufacturer"]
            for fld, s in (("design_item_id", "design_item_ids"), ("libref", "librefs"), ("item_guid", "item_guids"),
                           ("footprint", "footprints"), ("lifecycle", "lifecycle_param")):
                if c[fld]:
                    e[s].add(c[fld])
            u = e["uses"].setdefault((prj["path"], c["design_item_id"], c["revision_guid"]), {
                "project": prj["name"], "path": prj["path"], "design_item_id": c["design_item_id"], "revision_guid": c["revision_guid"],
                "item_guid": c["item_guid"], "designators": [], "not_fitted_in": {}})
            u["designators"].append(c["designator"])
            for vn, fit in fits.items():
                if not fit.get(c["designator"], True):
                    u["not_fitted_in"].setdefault(vn, []).append(c["designator"])
    for e in parts.values():
        e["uses"] = list(e["uses"].values())
        for u in e["uses"]:
            u["designators"].sort(key=_natkey)
            u["qty"] = len(u["designators"])
    flags = []

    # library match
    lib_parts, groups, errs, guids = load_library(plan, libs, a365, log) if (plan or libs or a365) else ([], [], [], {})
    warnings += errs
    have_lib = bool(plan or libs or a365)
    by_key, by_name = {}, {}
    for lp in lib_parts:
        if lp.get("key"):
            by_key.setdefault(lp["key"], []).append(lp)
        by_name.setdefault(str(lp["name"]).upper(), []).append(lp)
    keeper = {}
    for g in groups:
        if g["kind"] == "mpn":
            for m in g["members"]:
                if m["name"] != g["keeper"]:
                    keeper[m["name"]] = g["keeper"]
    matched = set()
    for e in parts.values():
        hits = []
        if e["mpn"]:
            hits += by_key.get(mpn_key(e["mpn"]), [])
        for did in e["design_item_ids"] | e["librefs"]:
            hits += by_name.get(did.upper(), [])
        names = list(dict.fromkeys(h["name"] for h in hits))
        for g in e["item_guids"]:
            if g.upper() in guids:
                names.append(guids[g.upper()])
        e["library"] = list(dict.fromkeys(names))
        e["library_origin"] = sorted({h["origin"] for h in hits} | ({"a365"} if any(g.upper() in guids for g in e["item_guids"]) else set()))
        matched |= set(e["library"])
        if have_lib and not e["library"]:
            flags.append(_flag("missing_from_library", e, "used in projects but not in the library"))
        for n in e["library"]:
            if n in keeper:
                flags.append(_flag("duplicate_in_use", e, f"library part {n} is a duplicate; keeper is {keeper[n]}"))
    if have_lib:
        seen = set()
        for lp in lib_parts:
            if lp["name"] not in matched and lp["name"] not in seen and lp.get("origin") in ("plan", "dblib", "a365"):
                seen.add(lp["name"])
                flags.append({"flag": "unused_library_part", "key": "", "part": lp["name"], "mpn": lp.get("mpn", ""), "projects": "",
                              "detail": f"{lp.get('origin')} part used by none of the {len(projects)} projects scanned"})

    # revisions: same Altium 365 item placed at different revisions
    by_item = {}
    for e in parts.values():
        for u in e["uses"]:
            if u["item_guid"] and u["revision_guid"]:
                by_item.setdefault(u["item_guid"].upper(), []).append((e, u))
    for ig, uses in by_item.items():
        revs = {u["revision_guid"].upper() for _, u in uses}
        if len(revs) < 2:
            continue
        nums = {u["revision_guid"].upper(): _rev_suffix(u["design_item_id"]) for _, u in uses}
        top = max((n for n in nums.values() if n is not None), default=None)
        for e, u in uses:
            n = nums[u["revision_guid"].upper()]
            if top is not None and n is not None:
                if n < top:
                    newest = next(x["design_item_id"] for _, x in uses if nums[x["revision_guid"].upper()] == top)
                    flags.append(_flag("old_revision", e, f"{u['design_item_id']} placed; {newest} is used elsewhere", u))
            else:
                flags.append(_flag("mixed_revisions", e, f"item {ig} placed at {len(revs)} revisions; this project has {u['revision_guid']}", u))

    # lifecycle
    lc = load_lifecycle(lifecycle or (Path(plan).parent / "lifecycle_state.json" if plan else None))
    plan_lc = {}
    if plan:
        for it in json.loads(Path(plan).read_text()):
            st = it.get("lifecycle")
            st = st.get("status") if isinstance(st, dict) else st
            if st:
                plan_lc[it["name"]] = st
    for e in parts.values():
        st, src = "", ""
        if e["mpn"] and norm_mpn(e["mpn"]) in lc:
            st, src = lc[norm_mpn(e["mpn"])].get("status", ""), "lifecycle state"
        if not st:
            st, src = next(((plan_lc[n], "plan") for n in e["library"] if n in plan_lc), ("", ""))
        if not st and e["lifecycle_param"]:
            st, src = normalise(sorted(e["lifecycle_param"])[0]), "schematic parameter"
        e["lifecycle"] = st if st and st != "unknown" else ""
        if SEVERITY.get(e["lifecycle"], 0) > 0:
            flags.append(_flag("lifecycle", e, f"{e['lifecycle']} ({src})"))

    if part:
        q = part.upper()
        keep = {k for k, e in parts.items() if q in k.upper() or q in (e["mpn"] or "").upper() or
                any(q in x.upper() for x in e["design_item_ids"] | e["librefs"] | e["item_guids"] | set(e["library"])) or
                (e["mpn"] and mpn_key(part) == mpn_key(e["mpn"]))}
        parts = {k: v for k, v in parts.items() if k in keep}
        flags = [f for f in flags if f.get("key") in keep or (f["flag"] == "unused_library_part" and q in f["part"].upper())]
    for e in parts.values():
        for s in ("design_item_ids", "librefs", "item_guids", "footprints", "lifecycle_param"):
            e[s] = sorted(e[s])
    return {"parts": parts, "flags": flags, "projects": projects, "library": have_lib, "warnings": list(dict.fromkeys(warnings))}


def _flag(kind, e, detail, use=None):
    uses = [use] if use else e["uses"]
    return {"flag": kind, "key": e["key"], "part": e["mpn"] or next(iter(e["design_item_ids"]), "") or next(iter(e["librefs"]), ""),
            "mpn": e["mpn"], "projects": ", ".join(dict.fromkeys(u["project"] for u in uses)), "detail": detail}


WU_COLS = ["key", "mpn", "manufacturer", "design_item_id", "libref", "library", "lifecycle", "project", "qty", "designators",
           "not_fitted", "item_guid", "revision_guid", "path"]


def where_used_rows(res):
    rows = []
    for k, e in sorted(res["parts"].items()):
        for u in e["uses"]:
            rows.append({"key": k, "mpn": e["mpn"], "manufacturer": e["manufacturer"], "design_item_id": u["design_item_id"],
                         "libref": ", ".join(e["librefs"]), "library": ", ".join(e.get("library", [])), "lifecycle": e.get("lifecycle", ""),
                         "project": u["project"], "qty": u["qty"], "designators": ", ".join(u["designators"]),
                         "not_fitted": "; ".join(f"{v}: {', '.join(d)}" for v, d in u["not_fitted_in"].items()),
                         "item_guid": u["item_guid"], "revision_guid": u["revision_guid"], "path": u["path"]})
    return rows


def write_where_used(res, stem):
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    rows = where_used_rows(res)
    with open(stem.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = SS.DictWriter(f, fieldnames=WU_COLS)
        w.writeheader()
        w.writerows(rows)
    with open(stem.with_name(stem.name + "_flags.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = SS.DictWriter(f, fieldnames=["flag", "part", "mpn", "projects", "detail"], extrasaction="ignore")
        w.writeheader()
        w.writerows(res["flags"])
    counts = {}
    for fl in res["flags"]:
        counts[fl["flag"]] = counts.get(fl["flag"], 0) + 1
    md = ["# Where used", "", f"- projects scanned: {len(res['projects'])}", f"- parts: {len(res['parts'])}",
          f"- library compared: {'yes' if res['library'] else 'no (pass --plan / --lib / --a365)'}"]
    md += [f"- **{k}**: {v}" for k, v in sorted(counts.items())]
    md += ["", "## Projects", "", "| project | components | variants | path |", "|---|---|---|---|"]
    md += [f"| {p['name']} | {p['components']} | {', '.join(p['variants']) or '-'} | `{p['path']}` |" for p in res["projects"]]
    if res["flags"]:
        md += ["", "## Flags", "", "| flag | part | projects | detail |", "|---|---|---|---|"]
        md += [f"| {f['flag']} | {f['part']} | {f['projects']} | {f['detail']} |" for f in res["flags"]]
    md += ["", "## Parts", "", "| part | MPN | library | lifecycle | project | qty | designators | not fitted |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['design_item_id'] or r['libref']} | {r['mpn']} | {r['library'] or '-'} | {r['lifecycle'] or '-'} | {r['project']} | "
                  f"{r['qty']} | {r['designators'][:60]} | {r['not_fitted'] or '-'} |")
    if res["warnings"]:
        md += ["", "## Warnings", ""] + [f"- {w}" for w in res["warnings"]]
    stem.with_suffix(".md").write_text("\n".join(md) + "\n")
    js = {"projects": res["projects"], "flags": res["flags"], "warnings": res["warnings"], "parts": res["parts"]}
    stem.with_suffix(".json").write_text(json.dumps(js, indent=1, default=list))
    return {"md": stem.with_suffix(".md"), "csv": stem.with_suffix(".csv"), "flags": stem.with_name(stem.name + "_flags.csv"),
            "json": stem.with_suffix(".json")}


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib project", description="BOM straight from an Altium project (.PrjPcb / .SchDoc / .BomDoc)")
    ap.add_argument("project")
    ap.add_argument("-o", "--out", help="BOM CSV to write (default: <project>_bom.csv next to the project)")
    ap.add_argument("--variant", help="apply this project variant (default: the base design)")
    ap.add_argument("--json", action="store_true", help="print the grouped BOM as JSON")
    a = ap.parse_args(argv)
    try:
        prj = load_project(a.project)
        groups = bom_groups(prj, a.variant)
    except (ValueError, FileNotFoundError) as e:
        print(f"ecadlib project: {e}", file=sys.stderr)
        return 2
    variants = [v["name"] for v in prj["variants"]]
    out = write_bom_csv(groups, a.out or Path(a.project).with_name(f"{Path(a.project).stem}_bom.csv"), variants)
    if a.json:
        print(json.dumps(groups, indent=1))
    else:
        fitted = [g for g in groups if not g["dnp"]]
        print(f"{prj['name']}: {len(prj['sheets'])} sheets, {len(prj['components'])} components, {len(fitted)} BOM lines"
              f"{' (variant ' + a.variant + ')' if a.variant else ''}; variants: {', '.join(variants) or 'none'}")
        for g in groups:
            print(f"  {g['qty']:>3}  {(g['mpn'] or g['design_item_id'] or g['libref'])[:28]:<28} {g['footprint'][:24]:<24} "
                  f"{g['designator'][:40]}{'  DNP' if g['dnp'] else ''}")
    for w in prj["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    print(f"wrote {out}")
    return 0


def where_used_main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib where-used", description="Which projects use which library part")
    ap.add_argument("--projects", nargs="+", required=True, help="folders (searched recursively) or project files")
    ap.add_argument("--part", help="only this part (MPN, Design Item ID, LibRef or GUID; substring match)")
    ap.add_argument("--plan", help="librarian plan.json to compare against")
    ap.add_argument("--lib", action="append", default=[], help="library folder / .DbLib / .SchLib (repeatable)")
    ap.add_argument("--a365", help="check-a365 state JSON (Altium 365 components)")
    ap.add_argument("--lifecycle", help="lifecycle_state.json from `ecadlib lifecycle`")
    ap.add_argument("-o", "--out", default="out/where_used", help="report stem (.md, .csv, _flags.csv, .json)")
    a = ap.parse_args(argv)
    try:
        res = where_used(a.projects, a.part, a.plan, a.lib, a.a365, a.lifecycle)
    except (ValueError, FileNotFoundError) as e:
        print(f"ecadlib where-used: {e}", file=sys.stderr)
        return 2
    files = write_where_used(res, a.out)
    print(f"{len(res['projects'])} projects, {len(res['parts'])} parts, {len(res['flags'])} flags")
    for fl in res["flags"][:50]:
        print(f"  {fl['flag']:<22} {fl['part'][:28]:<28} {fl['detail'][:80]}")
    print(f"wrote {files['md']} and .csv")
    return 1 if res["flags"] else 0


if __name__ == "__main__":
    sys.exit(main())
