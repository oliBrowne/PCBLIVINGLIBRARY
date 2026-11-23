"""Shared store of confirmed parts: one part's record, schema 1 (docs/shared-store-plan.md). Pure and offline.

    shareable(item, base)          -> why this released plan item may NOT be shared ([] = it may)
    from_item(item, base, by=...)  -> the record (raises NotShareable)
    validate(record)               -> schema problems ([] = a good record); what the store repo's checks run
    build(record, out_dir)         -> the part's files rebuilt from the record alone + a plan item for release_check
    level(record) / agree(a, b) / pins_hash(...) / add_evidence(record, entry) / merge(existing, incoming)

What may be shared. Only a part the release check passed (status ready, the check run again now finds nothing,
no --allow override, no open verify note or hold) whose symbol AND footprint came from an allowed source:
`datasheet` (ecadlib's own read), `standard` (ecadlib's tables) or `kicad:` (the KiCad libraries, CC-BY-SA 4.0
with their credit). `easyeda:` is shared as a pointer only: the LCSC number, the pad and pin hashes and the
evidence, never the drawing (footprint.kicad_mod / symbol.kicad_sym are null). In this version a pointer record is
only recorded: no client builds from it, compares with it or lifts a hold because of it. Anything else (a --lib library, `ecadlib resolve` picks, package specs,
SnapMagic / SamacSys / Ultra Librarian, no source) is never shared: not ours, or we cannot know whose. Maker and
part number must be filled. A datasheet link is kept only when it is public (ds_archive.asked, the test the
Internet Archive fallback uses: no intranet name, private address, user name or signed query); a private one is
left out, unless the part was read from that datasheet, which then has no provenance anyone else can check: refused.

What a record holds, by a whitelist (never the BOM, internal or supplier part numbers, stock or price fields, file
paths, plan `by` names): maker and MPN as written plus the normalised key (store_keys), the description only when a
source gave it (the plan's field_sources names a part search, a library or the datasheet; never the BOM line's own
Description / Notes text: empty otherwise), the public datasheet link and the PDF's hash (library.lock.json), the
KiCad symbol text (read back from the released .SchLib, kicad_export.symbol_from_schlib, with only its Reference /
Value / Footprint / Datasheet / Description fields, set to the record's own values) and its pins, the KiCad footprint text (the released .kicad_mod, renamed to its package; 3D model references and
embedded files taken out: the body is a height and a pointer to where its model came from, never model bytes), the
geometry hash (fp_share.digest, HASH_V), the pin-to-pad map (pin N on pad N: what build() draws), and one `build` evidence entry (who shares it, when,
ecadlib version, the datasheet reader's provider and model when it was read, the hashes, the check rules run clean).

Trust (level): `confirmed` when the evidence that agrees with the record's hashes holds a `signoff`, a `kicad-exact`
entry, or `build` entries from two contributors; else `checked`. One contributor counts once, whatever reader models
their builds used. A disputed record is `checked` (second opinion only). Two records agree when the pads (geometry
hash, same digest version) and the pins (pins_hash: pin number, normalised name with its active-low bar, pad, and the
footprint's mounting-pad count) are the same.

What validate() refuses, beyond the schema: a geometry hash of an older digest version, a pin map that is not pin N
on pad N, control characters anywhere, and KiCad text that carries more than the record's own checked fields: symbol
properties other than Reference / Value / Footprint / Datasheet / Description (with exactly the record's values),
footprint nodes and layers outside a whitelist, 3D models, embedded files, images, hyperlinks, text variables other
than ${REFERENCE}, and more text items than a footprint has (so no file can be split over many of them). It reads
the record only: no name lookup, no network (private(url, resolve=False))."""
import copy, datetime, json, re
from pathlib import Path

from . import __version__, fp_share as FS, sexpr, store_keys as SK
from .sexpr import Q

TOP = {"schema", "key", "also", "rev", "status", "part", "symbol", "footprint", "body", "pin_map", "evidence"}
FIELDS = {"key": {"maker", "mpn", "key"}, "part": {"description", "datasheet", "package"},
          "symbol": {"kicad_sym", "pointer", "pins", "source", "license", "credit"},
          "footprint": {"kicad_mod", "pointer", "hash", "hash_v", "spec", "source", "license", "credit", "mount_pads"},
          "body": {"height_mm", "height_src", "model"}}
DATASHEET = {"url", "sha256", "revision"}
EVIDENCE = {"kind", "by", "at", "ecadlib", "reader", "footprint_hash", "pins_hash", "checks"}
EVIDENCE_MORE = {"revision"}                    # a sign-off: the datasheet revision it was checked against
KINDS = ("build", "signoff", "kicad-exact")
STATUSES = ("active", "disputed")
MAX_BYTES = 400_000
LICENSE = {"kicad": "CC-BY-SA-4.0", "standard": "MIT", "datasheet": "MIT", "easyeda": "pointer only"}
SYM_FIELDS = ("Reference", "Value", "Footprint", "Datasheet", "Description")
FP_DROP = {"model", "embedded_files", "embedded_fonts"}
_STEP = re.compile(r"ISO-10303-21|END-ISO-10303")
_BLOB = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_DATA_URI = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_CHOSEN = re.compile(r"resolve: (symbol|footprint) .* chosen by user")
_HEX = re.compile(r"[0-9a-f]{64}")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")    # C0 / C1 controls (terminal escapes); \t and \n allowed
_REF_PREFIX = re.compile(r"[A-Z]{1,4}")
_LAYER = re.compile(r"(?:F|B|\*|F&B|In\d{1,2})\.Cu|(?:F|B|\*)\.(?:Paste|Mask|SilkS|Silkscreen|Fab|CrtYd|Courtyard|Adhes|"
                    r"Adhesive)|Edge\.Cuts|Dwgs\.User|Cmts\.User|Eco[12]\.User|Margin|User\.\d{1,2}|"
                    r"User\.(?:Drawings|Comments|Eco[12])")
FP_NODES = {"version", "generator", "generator_version", "layer", "descr", "tags", "property", "fp_text", "attr", "pad",
            "fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve", "zone", "uuid", "tedit", "tstamp",
            "locked", "placed", "at", "solder_mask_margin", "solder_paste_margin", "solder_paste_ratio",
            "solder_paste_margin_ratio", "clearance", "zone_connect", "thermal_width", "thermal_gap",
            "net_tie_pad_groups", "private_layers", "duplicate_pad_numbers_are_jumpers", "jumper_pad_groups",
            "autoplace_cost90", "autoplace_cost180", "sheetname", "sheetfile", "path"}
SYM_LIB_NODES = {"version", "generator", "generator_version", "symbol"}
SYM_NODES = {"property", "pin_names", "pin_numbers", "exclude_from_sim", "in_bom", "on_board", "symbol", "power",
             "in_pos_files", "duplicate_pin_numbers_are_jumpers"}
UNIT_NODES = {"rectangle", "polyline", "circle", "arc", "bezier", "text", "pin", "unit_name"}
FP_TEXT_MAX, FP_TEXT_LEN, DESCR_LEN, SYM_TEXT_MAX, SYM_TEXT_LEN, PIN_TEXT_LEN = 10, 64, 500, 16, 100, 48


class NotShareable(ValueError):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


class Conflict(ValueError):
    """Two builds of one part disagree (other pads or other pins)."""


class PointerOnly(ValueError):
    """A pointer record (EasyEDA) holds no drawing to build from."""


def kind_of(source):
    """'kicad' / 'standard' / 'datasheet' / 'easyeda' for an allowed source (plan or record spelling), else None."""
    s = str(source or "")
    if s == "datasheet" or s.startswith("datasheet:"):
        return "datasheet"
    return next((k for k in ("kicad", "standard", "easyeda") if s.startswith(k + ":") and len(s) > len(k) + 1), None)


def credit_of(kind):
    from . import easyeda_convert as EC, kicad_import as K
    return {"kicad": K.KICAD_CREDIT, "easyeda": EC.CREDIT}.get(kind, "")


def _record_source(source):
    """A plan source as the record names it: a datasheet read without its link (the record's part.datasheet holds the
    public one), the others as they are (kicad:Lib:Name, standard:..., easyeda:C123)."""
    return "datasheet" if kind_of(source) == "datasheet" else str(source)


def private(url, resolve=True):
    """Why a link is not a public web link (ds_archive.asked: the Internet Archive fallback's test), else None.
    resolve=False: from the link's text alone, with no name lookup (validate: a record a stranger wrote must not make
    this machine ask a DNS server they may run)."""
    from .ds_archive import asked
    return asked(url, resolve=resolve) if str(url or "").lower().startswith(("http://", "https://")) else \
        "not an http(s) link"


def _params(item):
    return {p["name"]: p.get("value", "") for p in item.get("parameters") or []
            if isinstance(p, dict) and p.get("name")}


def _field(par, name, rules):
    from . import release_check as RC, rules as RU
    v = par.get(RU.param_name(name, rules), par.get(name, ""))
    return str(v).strip() if RC._filled(v) else ""


def _lock_entry(item, base, lock):
    """The part's library.lock.json entry: lock is the whole lock, the part's entry, or None (read beside the plan)."""
    if lock is None:
        try:
            lock = json.loads((Path(base) / "library.lock.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lock = {}
    if isinstance(lock, dict) and isinstance(lock.get("parts"), dict):
        lock = lock["parts"].get(item.get("name")) or {}
    return lock if isinstance(lock, dict) else {}


def _read(item, entry):
    src = item.get("source") or {}
    return any(kind_of(src.get(h)) == "datasheet" for h in ("symbol", "footprint")) or bool(entry.get("datasheet"))


def _links(item, rules, entry):
    """(links the part was read from, the plan's datasheet links): every candidate, in order."""
    from . import rules as RU
    src, par = item.get("source") or {}, _params(item)
    read = [str(src.get(h))[len("datasheet:"):] for h in ("symbol", "footprint") if kind_of(src.get(h)) == "datasheet"]
    read += [(entry.get("datasheet") or {}).get("url") or ""]
    plan = [par.get(k) for k in ("ComponentLink2URL", RU.param_name("Datasheet", rules), "Datasheet")]
    return [u for u in dict.fromkeys(str(x).strip() for x in read) if u], \
        [u for u in dict.fromkeys(str(x or "").strip() for x in plan) if u.lower().startswith(("http://", "https://"))]


def _datasheet(item, rules, entry):
    """-> (the public link to record or None, why the part may not be shared or None, links left out)."""
    read, plan = _links(item, rules, entry)
    if _read(item, entry):
        bad = [(u, private(u)) for u in read if private(u)]
        if bad:
            return None, (f"read from a datasheet at a link that is not public ({bad[0][1]}): no provenance anyone "
                          "else can check"), read + plan
        good = read or [u for u in plan if not private(u)]
        if not good:
            return None, "read from a datasheet with no public link: no provenance anyone else can check", plan
        return good[0], None, [u for u in read + plan if u != good[0]]
    good = next((u for u in plan if not private(u)), None)
    return good, None, [u for u in plan if u != good]


def shared_description(item):
    """The plan item's description, when a source gave it (field_sources names where the librarian took it from: a
    part search, the KiCad library, the datasheet read, a store record); "" when it is the BOM line's own text (no
    field_sources entry, or one that names the BOM), when a house style rewrote it, or when the item does not say.
    The BOM's Description / Notes columns are the user's own words (project names, internal codes): never shared."""
    src = str((item.get("field_sources") or {}).get("Description") or "")
    if not src.strip() or re.search(r"\bBOM\b", src, re.I):
        return ""
    hs = item.get("house_style")
    if isinstance(hs, dict) and any(isinstance(c, dict) and c.get("field") == "description"
                                    for c in hs.get("changes") or []):
        return ""
    return str(item.get("description") or "").strip()


def _norm_item(item):
    return {**item, "symbol": item.get("symbol") or {}, "footprint": item.get("footprint") or {}}


def _kicad_mod_path(item, base):
    from . import release_check as RC
    it = _norm_item(item)
    return RC._kicad_mod(it, base, RC._path(base, it["footprint"].get("file")))


# ---------------------------------------------------------------- what may be shared
def shareable(item, base=".", rules=None, lock=None):
    """Reasons this plan item may NOT be shared; [] = it may (see the module doc). rules: loaded rules or None."""
    from . import release_check as RC, rules as RU
    rules = RU.resolve(rules, base)
    why, src = [], item.get("source") or {}
    st = item.get("status")
    if st != "ready":
        why.append(f"status {st!r} is not ready: only a part the release check released is shared")
    if item.get("allow") or (item.get("release_check") or {}).get("result") == "allowed":
        why.append("released with --allow (an override is in force): only a part that passes every check is shared")
    why += [f"verify: {v}" for v in RC.doubts(item)]
    why += [f"held: {h}" for h in item.get("hold_reasons") or []]
    if item.get("no_bom_part"):
        why.append("no maker part (a mounting hole / test pad): nothing to share")
    for half in ("symbol", "footprint"):
        s = src.get(half)
        if kind_of(s) is None:
            why.append(f"{half} source {s or 'none'!r} is not shareable: only datasheet, standard and kicad: parts "
                       "(easyeda: as a pointer); a --lib library, a package spec or a paid library is not ours to "
                       "share")
        elif _person_chose(item, half):
            why.append(f"{half} source {s}: chosen by a person (ecadlib resolve or a pin map): not shared")
    par = _params(item)
    k = SK.key(_field(par, "Manufacturer", rules), _field(par, "Manufacturer Part Number", rules))
    if not k or not SK.maker_of(k):
        why.append("Manufacturer and Manufacturer Part Number must both be filled: the store names a part by them")
    _, bad, _ = _datasheet(item, rules, _lock_entry(item, base, lock))
    if bad:
        why.append(f"datasheet: {bad}")
    if not why:
        if not _kicad_mod_path(item, base):
            why.append("files: no KiCad footprint (.kicad_mod) beside the part's .PcbLib")
        why += [f"release check: {p}" for p in RC.publish_problems(item, base, rules)]
    return why


def _person_chose(item, half):
    tried = (item.get("librarian") or {}).get("tried") or []
    return bool((item.get(half) or {}).get("pin_map") or (item.get(half) or {}).get("chosen")) or any(
        (m := _CHOSEN.match(str(t))) and m.group(1) == half for t in tried)


# ---------------------------------------------------------------- the record
def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_footprint(text, name):
    """A .kicad_mod for the record: named `name` (and its Value), its 3D model references and embedded files taken out.
    -> (text, the first model path it named or None)."""
    t = sexpr.parse(text, keep_quotes=True)
    if not (isinstance(t, list) and len(t) > 1 and t[0] in ("footprint", "module")):
        raise ValueError("not a KiCad footprint")
    model = next((str(c[1]) for c in sexpr.find(t, "model") if len(c) > 1), None)
    t = [c for c in t if not (isinstance(c, list) and c and c[0] in FP_DROP)]
    t[1] = Q(name)
    for c in t:
        if isinstance(c, list) and len(c) > 2 and ((c[0] == "fp_text" and c[1] == "value") or
                                                   (c[0] == "property" and str(c[1]) == "Value")):
            c[2] = Q(name)
    return sexpr.dumps(t) + "\n", model


def _nat(s):
    return [(0, int(x), "") if x.isdecimal() else (1, 0, x) for x in re.split(r"(\d+)", str(s)) if x]


def sym_pins(tree):
    """[{num, name, type}] of a KiCad symbol tree (its units' pins), sorted, each once."""
    rows = {(str(sexpr.first(p, "number")[1]), str(sexpr.first(p, "name")[1]), str(p[1]))
            for u in sexpr.find(tree, "symbol") for p in sexpr.find(u, "pin") if sexpr.first(p, "number")}
    return [{"num": n, "name": m, "type": t} for n, m, t in sorted(rows, key=lambda r: (_nat(r[0]), r[1], r[2]))]


def _clean_symbol(tree, name, url, desc):
    """Only the KiCad fields every symbol has, each once, with the record's values (no supplier, MPN or company
    fields; the Reference a plain designator prefix, the Footprint empty)."""
    props = {str(c[1]): c for c in tree if isinstance(c, list) and c and c[0] == "property" and len(c) > 2}
    ref = re.sub(r"[^A-Z]", "", str(props["Reference"][2]).upper())[:4] if "Reference" in props else ""
    vals = {"Reference": ref if _REF_PREFIX.fullmatch(ref) else "U", "Value": name, "Footprint": "",
            "Datasheet": url or "", "Description": desc or ""}
    out, seen = [], set()
    for c in tree:
        if isinstance(c, list) and c and c[0] == "property":
            k = str(c[1]) if len(c) > 2 else ""
            if k not in SYM_FIELDS or k in seen:
                continue
            seen.add(k)
            c = list(c)
            c[2] = Q(vals[k])
        elif isinstance(c, list) and c and c[0] in ("embedded_files", "embedded_fonts"):
            continue
        out.append(c)
    at = next((i for i, c in enumerate(out) if isinstance(c, list) and c and c[0] in ("symbol", "pin_names",
                                                                                         "pin_numbers")), len(out))
    for k in SYM_FIELDS:
        if k not in seen:
            out.insert(at, ["property", Q(k), Q(vals[k]), ["at", 0, 0, 0],
                            ["effects", ["font", ["size", 1.27, 1.27]], ["hide", "yes"]]])
            at += 1
    return out


def _pads(text):
    return {str(p[1]) for p in sexpr.find(sexpr.parse(text), "pad") if len(p) > 1 and str(p[1])}


def _height_src(item):
    for n in (item.get("librarian") or {}).get("notes") or []:
        m = re.match(r"3D: .*\bheight [\d.]+ mm \((.*)\)$", str(n))
        if m:
            return m.group(1)[:120]
    return ""


def _model_pointer(item, kind, model_path):
    from . import kicad_import as K
    m = (item.get("footprint") or {}).get("model") or {}
    if m.get("url") and not private(m["url"]):
        return {"source": str(m.get("source") or ""), "url": str(m["url"])}
    if kind == "kicad" and model_path and K.model_url_path(model_path):
        return {"source": "kicad", "url": K.GITLAB.format(repo="kicad-packages3D", path=K.model_url_path(model_path))}
    return None


def from_item(item, base=".", rules=None, by="", lock=None, at=None):
    """The record of a released plan item (see the module doc). by: the contributor's store name (their GitHub
    name), never a name from the plan; lock: library.lock.json (whole, or the part's entry; None: read beside the
    plan); at: the evidence time (UTC ISO), default now. Raises NotShareable(reasons)."""
    from . import kicad_export as KX, release_check as RC, rules as RU
    rules = RU.resolve(rules, base)
    why = shareable(item, base, rules, lock)
    if why:
        raise NotShareable(why)
    entry = _lock_entry(item, base, lock)
    par, src = _params(item), item["source"]
    maker, mpn = _field(par, "Manufacturer", rules), _field(par, "Manufacturer Part Number", rules)
    url, _, dropped = _datasheet(item, rules, entry)
    scrub = lambda s: _scrub(s, dropped)
    fkind, skind = kind_of(src["footprint"]), kind_of(src["symbol"])
    package = str(src["footprint"]).rsplit(":", 1)[-1] if fkind in ("kicad", "standard") else KX.kname(mpn)
    text, model_path = clean_footprint(_kicad_mod_path(item, base).read_text(encoding="utf-8"), package)
    text = scrub(text)
    desc = scrub(shared_description(item))
    name, notes = KX.ksym(mpn), []
    tree = KX.symbol_from_schlib(RC._path(base, item["symbol"]["file"]), name, notes, pick=item["symbol"].get("ref"))
    KX.restore_types(tree, item["symbol"].get("kicad_types"), notes, guess=skind != "kicad")
    tree = _clean_symbol(tree, name, url, desc)
    lib = ["kicad_symbol_lib", ["version", KX.SYM_VERSION], ["generator", Q("ecadlib")], tree]
    sym_text = scrub(sexpr.dumps(lib) + "\n")
    pins = sym_pins(next(sexpr.find(sexpr.parse(sym_text), "symbol")))
    pads = _pads(text)
    pin_map = {p["num"]: p["num"] for p in pins if p["num"] in pads}     # pin N on pad N (what build() draws)
    h = FS.digest(text)
    ds = entry.get("datasheet") or {}
    read = _read(item, entry)
    sha = ds.get("pdf_sha256") if (ds.get("url") == url or read) and _HEX.fullmatch(str(ds.get("pdf_sha256") or "")) \
        else None
    height = FS.height_of({"file": item["footprint"].get("file")}, base, {})
    lcsc = lambda s: {"lcsc": str(s).split(":", 1)[1]}
    try:
        mount = int(item["footprint"].get("mount_pads") or 0)
    except (TypeError, ValueError):
        mount = 0
    rec = {"schema": SK.SCHEMA, "key": {"maker": maker, "mpn": mpn, "key": SK.key(maker, mpn)}, "also": [], "rev": 1,
           "status": "active",
           "part": {"description": desc, "package": package,
                    "datasheet": {"url": url, "sha256": sha, "revision": None} if url else None},
           "symbol": {"kicad_sym": None if skind == "easyeda" else sym_text,
                      "pointer": lcsc(src["symbol"]) if skind == "easyeda" else None, "pins": pins,
                      "source": _record_source(src["symbol"]), "license": LICENSE[skind], "credit": credit_of(skind)},
           "footprint": {"kicad_mod": None if fkind == "easyeda" else text,
                         "pointer": lcsc(src["footprint"]) if fkind == "easyeda" else None, "hash": h,
                         "hash_v": FS.HASH_V, "spec": copy.deepcopy(item["footprint"].get("spec")) if isinstance(
                             item["footprint"].get("spec"), dict) else None, "source": _record_source(src["footprint"]),
                         "license": LICENSE[fkind], "credit": credit_of(fkind), "mount_pads": mount},
           "body": {"height_mm": round(float(height), 4) if height else None, "height_src": _height_src(item),
                    "model": _model_pointer(item, fkind, model_path)},
           "pin_map": pin_map, "evidence": []}
    mode = (rules.get("release") or {}).get("parity", "hold")
    rec["evidence"].append({
        "kind": "build", "by": str(by or ""), "at": at or _now(), "ecadlib": __version__,
        "reader": {"provider": str(ds.get("provider") or ""), "model": str(ds.get("model") or "")}
        if read and ds else None,
        "footprint_hash": h, "pins_hash": pins_hash(rec),
        "checks": [r for r in RC.RULES if not (r == "parity" and mode == "off")]})
    return rec


def _scrub(text, links):
    for u in sorted(links, key=len, reverse=True):
        if u and private(u):
            text = text.replace(u, "")
    return text


# ---------------------------------------------------------------- hashes and trust
def _pin_name(s):
    """A pin name compared across KiCad and Altium spellings: the active-low bar kept (KiCad '~{CS}' and KiCad 6
    '~CS~' are Altium's 'C\\S\\', never plain 'CS'), white space dropped, upper case."""
    from .altium_lib import overline
    s = str(s or "")
    if "~{" not in s and s != "~":
        s = re.sub(r"~([^~]*)~?", lambda m: "~{" + m.group(1) + "}" if m.group(1) else "~", s)
    return re.sub(r"\s", "", overline(s)).upper()


def _mount(v):
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def pins_hash(record_or_pins, pin_map=None, mount_pads=None):
    """Hash of the sorted (pin number, normalised pin name, pad) rows and the footprint's mounting-pad count (a pad
    table's role-mount pads on no net): a record (its pins, pin_map and footprint.mount_pads), or pins with a pin map
    and mount_pads. Two builds whose hashes agree attest the same pins, pads and mounting pads."""
    if isinstance(record_or_pins, dict):
        pins = (record_or_pins.get("symbol") or {}).get("pins") or []
        pin_map = record_or_pins.get("pin_map") if pin_map is None else pin_map
        if mount_pads is None:
            mount_pads = (record_or_pins.get("footprint") or {}).get("mount_pads")
    else:
        pins = record_or_pins or []
    pm = {str(k): str(v) for k, v in (pin_map or {}).items()} if isinstance(pin_map, dict) else {}
    rows = sorted({(str(p.get("num")), _pin_name(p.get("name")), pm.get(str(p.get("num")), str(p.get("num"))))
                   for p in pins if isinstance(p, dict)}, key=lambda r: (_nat(r[0]), r[1], r[2]))
    mount = _mount(mount_pads)
    return SK.digest([list(r) for r in rows] + ([["#mount_pads", str(mount)]] if mount else []))[:32]


def _same_pads(a, b):
    fa, fb = a.get("footprint") or {}, b.get("footprint") or {}
    if fa.get("hash_v") == fb.get("hash_v"):
        return bool(fa.get("hash")) and fa.get("hash") == fb.get("hash")
    if fa.get("kicad_mod") and fb.get("kicad_mod"):        # hashed by two digest versions: compare at this one
        return FS.digest(fa["kicad_mod"]) == FS.digest(fb["kicad_mod"])
    return False


def _differs(a, b):
    return [w for w, same in (("pads", _same_pads(a, b)), ("pins", pins_hash(a) == pins_hash(b))) if not same]


def agree(a, b):
    """Same pads (geometry hash, same digest version) and the same pins (pins_hash)."""
    return not _differs(a, b)


def _agrees(record, e):
    return e.get("footprint_hash") == (record.get("footprint") or {}).get("hash") and \
        e.get("pins_hash") == pins_hash(record)


def level(record, trusted=None, maintainers=()):
    """'confirmed' or 'checked' (module doc). trusted: contributor names whose evidence counts (None: everyone's).
    maintainers: names (the store's own, store.json) whose two builds with two different AI models count as two:
    a contributor's own word for which model read the datasheet cannot be checked, a maintainer's seed run can."""
    if record.get("status") != "active":
        return "checked"
    ev = [e for e in record.get("evidence") or [] if isinstance(e, dict) and _agrees(record, e)
          and (trusted is None or e.get("by") in trusted)]
    if any(e.get("kind") in ("signoff", "kicad-exact") for e in ev):
        return "confirmed"
    builds = [e for e in ev if e.get("kind") == "build" and e.get("by")]
    if len({e["by"] for e in builds}) >= 2:
        return "confirmed"
    models = {(e["reader"].get("provider"), e["reader"].get("model")) for e in builds
              if e["by"] in set(maintainers) and isinstance(e.get("reader"), dict) and e["reader"].get("model")}
    return "confirmed" if len(models) >= 2 else "checked"


def add_evidence(record, entry):
    """A copy of record with entry added (rev unchanged; an identical entry is not added again). Raises Conflict when
    the entry's hashes are not the record's."""
    what = [w for w, k, v in (("pads", "footprint_hash", (record.get("footprint") or {}).get("hash")),
                              ("pins", "pins_hash", pins_hash(record))) if entry.get(k) != v]
    if what:
        raise Conflict(f"{' and '.join(what)} differ from the record of {record['key']['key']}: evidence for another "
                       "build is not added")
    out = copy.deepcopy(record)
    if SK.digest(entry) not in {SK.digest(e) for e in out["evidence"]}:
        out["evidence"].append(copy.deepcopy(entry))
    return out


def merge(existing, incoming):
    """The store's record with an incoming build of the same part: the same pads and pins -> the existing record with
    the incoming evidence added (each entry once; its `also` codes stay the existing record's: a contributor never
    adds codes by a merge); otherwise Conflict naming what differs."""
    if existing["key"]["key"] != incoming["key"]["key"]:
        raise Conflict(f"another part: {incoming['key']['key']} is not {existing['key']['key']}")
    what = _differs(existing, incoming)
    if what:
        raise Conflict(f"{' and '.join(what)} differ from the store's record of {existing['key']['key']}")
    out = copy.deepcopy(existing)
    for e in incoming.get("evidence") or []:
        out = add_evidence(out, dict(e, footprint_hash=out["footprint"]["hash"], pins_hash=pins_hash(out))
                           if _agrees(incoming, e) else e)
    return out


# ---------------------------------------------------------------- validate
def _section(probs, where, d, fields, required=None):
    if not isinstance(d, dict):
        probs.append(f"{where}: not an object")
        return False
    probs += [f"{where}: unknown field {k!r}" for k in sorted(set(d) - fields, key=str)]
    probs += [f"{where}: missing {k!r}" for k in sorted((fields if required is None else required) - set(d))]
    return True


def _iso(s):
    try:
        return "T" in str(s) and datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")) is not None
    except ValueError:
        return False


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def validate(record, resolve=False):
    """Schema 1 problems of a record ([] = good): fields and types, allowed sources and their licences, the key, the
    hashes recomputed from the drawing and pins at this ecadlib's digest version, size, forbidden content (3D models,
    PDFs, base64 blobs, private links, e-mail addresses, control characters), KiCad text that carries more than the
    record's fields (_kicad_text), evidence. resolve: look host names up for the private-link test (default no: a
    syntax test only, nothing goes to the network)."""
    p = []
    if not _section(p, "record", record, TOP):
        return p
    try:
        text = SK.dumps(record)
    except (TypeError, ValueError) as e:
        return p + [f"record: not JSON ({e})"]
    if any(_CTRL.search(x) for x in _strings(record)):
        p.append("record holds control characters (only line breaks and tabs are text)")
    if len(text.encode("utf-8")) > MAX_BYTES:
        p.append(f"record too large ({len(text.encode('utf-8'))} bytes, at most {MAX_BYTES})")
    if _STEP.search(text):
        p.append("record holds a STEP model (ISO-10303-21): 3D models are never stored, only a pointer")
    if "%PDF-" in text:
        p.append("record holds a PDF: datasheets are never stored, only their public link and hash")
    if _DATA_URI.search(text):
        p.append("record holds a data: URI (base64): no embedded files")
    elif _BLOB.search(text):
        p.append("record holds a base64 blob: no embedded files")
    if record.get("schema") != SK.SCHEMA:
        p.append(f"schema {record.get('schema')!r} is not {SK.SCHEMA}")
    rev = record.get("rev")
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
        p.append(f"rev {rev!r}: a whole number, 1 or more")
    if record.get("status") not in STATUSES:
        p.append(f"status {record.get('status')!r} is not one of {', '.join(STATUSES)}")
    also = record.get("also")
    if not isinstance(also, list) or not all(isinstance(a, str) and a.strip() for a in also):
        p.append("also: a list of part numbers")
    k = record.get("key")
    if _section(p, "key", k, FIELDS["key"]):
        if not all(isinstance(k.get(x), str) for x in FIELDS["key"]):
            p.append("key: maker, mpn and key are text")
        elif not SK.maker_of(k["key"]) or SK.key(k["maker"], k["mpn"]) != k["key"]:
            p.append(f"key {k['key']!r} is not the key of maker {k['maker']!r} and MPN {k['mpn']!r} "
                     f"({SK.key(k['maker'], k['mpn'])!r})")
    part = record.get("part")
    if _section(p, "part", part, FIELDS["part"]):
        if not isinstance(part.get("description"), str) or not isinstance(part.get("package"), str):
            p.append("part: description and package are text")
        elif _EMAIL.search(part["description"]):
            p.append("part: the description holds an e-mail address")
        ds = part.get("datasheet")
        if ds is not None and _section(p, "part.datasheet", ds, DATASHEET):
            why = private(ds.get("url"), resolve)
            if why:
                p.append(f"part.datasheet: url is not a public link ({why})")
            if ds.get("sha256") is not None and not _HEX.fullmatch(str(ds["sha256"])):
                p.append("part.datasheet: sha256 is not a SHA-256 hex digest")
    pins = None
    sym = record.get("symbol")
    if _section(p, "symbol", sym, FIELDS["symbol"]):
        _source(p, "symbol", sym, "kicad_sym")
        pins = sym.get("pins")
        if not isinstance(pins, list) or not pins or not all(
                isinstance(x, dict) and set(x) == {"num", "name", "type"} and
                all(isinstance(v, str) for v in x.values())
                for x in pins):
            p.append("symbol: pins must be a list of {num, name, type}")
            pins = None
        elif isinstance(sym.get("kicad_sym"), str):
            try:
                tree = next(sexpr.find(sexpr.parse(sym["kicad_sym"]), "symbol"))
                if sym_pins(tree) != pins:
                    p.append("symbol: pins are not the pins of its kicad_sym")
                if any(sexpr.first(tree, x) for x in ("embedded_files", "embedded_fonts")):
                    p.append("symbol: kicad_sym holds embedded files")
            except (ValueError, StopIteration, IndexError, TypeError) as e:
                p.append(f"symbol: kicad_sym is not a KiCad symbol library ({type(e).__name__})")
    fp, pads = record.get("footprint"), None
    if _section(p, "footprint", fp, FIELDS["footprint"]):
        _source(p, "footprint", fp, "kicad_mod")
        hv = fp.get("hash_v")
        if not isinstance(hv, int) or isinstance(hv, bool) or not 1 <= hv <= FS.HASH_V:
            p.append(f"footprint: hash_v {hv!r} is not a digest version (1..{FS.HASH_V})")
        elif isinstance(fp.get("kicad_mod"), str) and hv != FS.HASH_V:
            p.append(f"footprint: hash_v {hv} is an older digest version: a drawing is hashed at {FS.HASH_V} (an older "
                     "one cannot tell a pad moved to the bottom side, paste or custom copper apart)")
        if not isinstance(fp.get("hash"), str) or not fp.get("hash"):
            p.append("footprint: hash missing")
        elif isinstance(fp.get("kicad_mod"), str) and isinstance(hv, int):
            try:
                t = sexpr.parse(fp["kicad_mod"])
                if not (isinstance(t, list) and t and t[0] in ("footprint", "module")):
                    raise ValueError("not a footprint")
                if any(sexpr.first(t, x) for x in FP_DROP):
                    p.append("footprint: kicad_mod holds a 3D model reference or embedded files (body.model points to "
                             "the model; models are never stored)")
                pads = _pads(fp["kicad_mod"])
                if FS.digest(fp["kicad_mod"], hv) != fp["hash"]:
                    p.append("footprint: hash does not match its kicad_mod (the pads changed after it was hashed)")
            except ValueError as e:
                p.append(f"footprint: kicad_mod is not a KiCad footprint ({e})")
        if fp.get("spec") is not None and not isinstance(fp.get("spec"), dict):
            p.append("footprint: spec is an object or null")
        mp = fp.get("mount_pads")
        if not isinstance(mp, int) or isinstance(mp, bool) or mp < 0:
            p.append("footprint: mount_pads is a whole number")
    body = record.get("body")
    if _section(p, "body", body, FIELDS["body"]):
        h = body.get("height_mm")
        if h is not None and (not isinstance(h, (int, float)) or isinstance(h, bool) or not 0 < h <= 100):
            p.append(f"body: height_mm {h!r} is not a height in mm")
        if not isinstance(body.get("height_src"), str):
            p.append("body: height_src is text")
        m = body.get("model")
        if m is not None and _section(p, "body.model", m, {"source", "url"}):
            if private(m.get("url"), resolve):
                p.append(f"body.model: url is not a public link ({private(m.get('url'), resolve)})")
    pm = record.get("pin_map")
    if not isinstance(pm, dict) or not all(isinstance(a, str) and isinstance(b, str) for a, b in pm.items()):
        p.append("pin_map: an object of pin number -> pad number")
    elif any(a != b for a, b in pm.items()):
        p.append("pin_map: only pin N on pad N (the map a record's part is built with); this one maps "
                 + ", ".join(f"{a}->{b}" for a, b in sorted(pm.items()) if a != b)[:120])
    elif pins is not None:
        nums = {x["num"] for x in pins}
        if set(pm) - nums:
            p.append(f"pin_map: pins not on the symbol: {', '.join(sorted(set(pm) - nums))}")
        if pads is not None and set(pm.values()) - pads:
            p.append(f"pin_map: pads not on the footprint: {', '.join(sorted(set(pm.values()) - pads))}")
    _evidence(p, record, pins is not None and isinstance(pm, dict) and isinstance(fp, dict))
    if isinstance(sym, dict) and isinstance(fp, dict) and isinstance(part, dict):
        p += _kicad_text(record)
    return p


def _walk(t):
    yield t
    for c in t[1:] if isinstance(t, list) else ():
        if isinstance(c, list):
            yield from _walk(c)


def _atoms(t):
    for c in t if isinstance(t, list) else ():
        if isinstance(c, list):
            yield from _atoms(c)
        else:
            yield str(c)


def _vars(text, allowed=()):
    """Text variables (${...}) in text other than the allowed ones."""
    return [v for v in re.findall(r"\$\{[^}]*\}?", text) if v not in allowed]


def _kicad_text(record):
    """Problems of a record's KiCad text beyond its pads and pins: it must carry the record's checked fields and
    nothing else (validate)."""
    p = []
    part, sym, fp = record["part"], record["symbol"], record["footprint"]
    ks, km = sym.get("kicad_sym"), fp.get("kicad_mod")
    if isinstance(km, str):
        try:
            t = sexpr.parse(km)
            p += [f"footprint: {x}" for x in _fp_text_problems(t, str(part.get("package") or ""))]
        except (ValueError, IndexError, TypeError) as e:
            p.append(f"footprint: kicad_mod is not a KiCad footprint ({type(e).__name__})")
    if isinstance(ks, str):
        try:
            t = sexpr.parse(ks)
            p += [f"symbol: {x}" for x in _sym_text_problems(t, part)]
        except (ValueError, IndexError, TypeError) as e:
            p.append(f"symbol: kicad_sym is not a KiCad symbol library ({type(e).__name__})")
    return list(dict.fromkeys(p))


def _fp_text_problems(t, package):
    p = []
    if not (isinstance(t, list) and len(t) > 1 and t[0] in ("footprint", "module")):
        return ["kicad_mod is not a KiCad footprint"]
    odd = sorted({str(c[0]) for c in t[2:] if isinstance(c, list) and c and str(c[0]) not in FP_NODES})
    if odd:
        p.append(f"kicad_mod holds {', '.join(odd[:5])}: a record's footprint is pads, outlines and its own name only")
    nodes = list(_walk(t))
    if any(isinstance(n, list) and n and n[0] in ("href", "image", "model", "embedded_files", "embedded_fonts",
                                                    "fp_text_box", "dimension", "group", "net")
           for n in nodes[1:]):
        p.append("kicad_mod holds a hyperlink, image, 3D model, embedded file, text box, dimension or net")
    layers = set()
    for n in nodes:
        if isinstance(n, list) and n and n[0] in ("layer", "layers"):
            layers.update(str(x) for x in n[1:] if not isinstance(x, list) and str(x) != "knockout")
    bad = sorted(x for x in layers if not _LAYER.fullmatch(x))
    if bad:
        p.append(f"kicad_mod uses layers {', '.join(bad[:5])}: not a footprint layer")
    texts = [c for c in t[2:] if isinstance(c, list) and c and c[0] in ("fp_text", "property")]
    if len(texts) > FP_TEXT_MAX:
        p.append(f"kicad_mod has {len(texts)} text items (at most {FP_TEXT_MAX}): no text beyond the reference, value "
                 "and a few notes")
    for c in texts:
        kind, val = (str(c[1]), str(c[2])) if len(c) > 2 else ("", "")
        if c[0] == "fp_text" and kind not in ("reference", "value", "user"):
            p.append(f"kicad_mod: fp_text {kind[:20]!r} is not reference / value / user")
        elif c[0] == "property" and kind not in SYM_FIELDS:
            p.append(f"kicad_mod: property {kind[:30]!r} is not one of {', '.join(SYM_FIELDS)}")
        elif (c[0] == "fp_text" and kind == "value") or (c[0] == "property" and kind == "Value"):
            if val != package:
                p.append(f"kicad_mod: its value text {val[:40]!r} is not the record's package {package!r}")
        elif len(val) > (DESCR_LEN if kind in ("Description", "Datasheet") else FP_TEXT_LEN):
            p.append(f"kicad_mod: text {kind[:20]!r} is {len(val)} characters long")
        if _vars(val, ("${REFERENCE}",)):
            p.append(f"kicad_mod: text variables {', '.join(_vars(val, ('${REFERENCE}',))[:3])} (only ${{REFERENCE}})")
    for k in ("descr", "tags"):
        for c in (c for c in t[2:] if isinstance(c, list) and c and c[0] == k):
            v = " ".join(_atoms(c[1:]))
            if len(v) > DESCR_LEN:
                p.append(f"kicad_mod: {k} is {len(v)} characters long (at most {DESCR_LEN})")
            if _vars(v):
                p.append(f"kicad_mod: {k} holds text variables")
    if sum(1 for c in t[2:] if isinstance(c, list) and c and c[0] in ("descr", "tags")) > 2:
        p.append("kicad_mod: more than one descr / tags")
    return p


def _sym_text_problems(t, part):
    p = []
    if not (isinstance(t, list) and t and t[0] == "kicad_symbol_lib"):
        return ["kicad_sym is not a KiCad symbol library"]
    odd = sorted({str(c[0]) for c in t[1:] if isinstance(c, list) and c and str(c[0]) not in SYM_LIB_NODES})
    if odd:
        p.append(f"kicad_sym holds {', '.join(odd[:5])}: one symbol only")
    syms = [c for c in t[1:] if isinstance(c, list) and c and c[0] == "symbol"]
    if len(syms) != 1:
        return p + [f"kicad_sym holds {len(syms)} symbols: one only"]
    sy = syms[0]
    name = str(sy[1]) if len(sy) > 1 else ""
    odd = sorted({str(c[0]) for c in sy[2:] if isinstance(c, list) and c and str(c[0]) not in SYM_NODES})
    if odd:
        p.append(f"kicad_sym holds {', '.join(odd[:5])}: a record's symbol is its pins, drawing and five fields")
    for u in (c for c in sy[2:] if isinstance(c, list) and c and c[0] == "symbol"):
        odd = sorted({str(c[0]) for c in u[2:] if isinstance(c, list) and c and str(c[0]) not in UNIT_NODES})
        if odd:
            p.append(f"kicad_sym: unit {str(u[1])[:40]!r} holds {', '.join(odd[:5])}")
        texts = [c for c in u[2:] if isinstance(c, list) and c and c[0] == "text"]
        if len(texts) > SYM_TEXT_MAX or any(len(str(c[1])) > SYM_TEXT_LEN for c in texts if len(c) > 1):
            p.append(f"kicad_sym: unit {str(u[1])[:40]!r} has more or longer text than a symbol drawing has")
        for pin in (c for c in u[2:] if isinstance(c, list) and c and c[0] == "pin"):
            for k in ("name", "number"):
                v = sexpr.first(pin, k)
                if v and len(v) > 1 and len(str(v[1])) > PIN_TEXT_LEN:
                    p.append(f"kicad_sym: a pin {k} is {len(str(v[1]))} characters long (at most {PIN_TEXT_LEN})")
    nodes = list(_walk(t))
    if any(isinstance(n, list) and n and n[0] in ("href", "image", "embedded_files", "embedded_fonts", "text_box",
                                                    "extends") for n in nodes[1:]):
        p.append("kicad_sym holds a hyperlink, image, embedded file, text box or a link to another symbol")
    if any(_vars(a) for a in _atoms(t)):
        p.append("kicad_sym holds text variables (${...})")
    props = [c for c in sy[2:] if isinstance(c, list) and c and c[0] == "property"]
    got = [str(c[1]) if len(c) > 1 else "" for c in props]
    if sorted(got) != sorted(SYM_FIELDS):
        p.append(f"kicad_sym properties are {', '.join(got)[:120]}: exactly {', '.join(SYM_FIELDS)}, once each")
    want = {"Value": {name}, "Datasheet": {str(((part.get("datasheet") or {}).get("url")) or "")},
            "Description": {str(part.get("description") or "")}, "Footprint": {"", str(part.get("package") or "")}}
    for c in props:
        k, v = (str(c[1]), str(c[2])) if len(c) > 2 else ("", "")
        if k == "Reference" and not _REF_PREFIX.fullmatch(v):
            p.append(f"kicad_sym: Reference {v[:20]!r} is not a designator prefix (1 to 4 capital letters)")
        elif k in want and v not in want[k]:
            p.append(f"kicad_sym: {k} {v[:60]!r} is not the record's ({sorted(want[k])[-1][:60]!r})")
    return p


def _source(p, where, d, text_key):
    s, kind = d.get("source"), kind_of(d.get("source"))
    if kind is None or (kind == "datasheet" and s != "datasheet"):
        p.append(f"{where}: source {s!r} is not shareable (datasheet, standard:, kicad:, easyeda: pointer)")
        return
    if d.get("license") != LICENSE[kind] or d.get("credit") != credit_of(kind):
        p.append(f"{where}: license {d.get('license')!r} / credit do not go with source {s} ({LICENSE[kind]})")
    if kind == "easyeda":
        if d.get(text_key) is not None or d.get("pointer") != {"lcsc": s.split(":", 1)[1]}:
            p.append(f"{where}: an EasyEDA part is a pointer only ({text_key} null, pointer {{lcsc}} of its source)")
    elif not isinstance(d.get(text_key), str) or d.get("pointer") is not None:
        p.append(f"{where}: {text_key} is the KiCad text and pointer null for a {kind} source")


def _evidence(p, record, hashes):
    from .release_check import RULES
    ev = record.get("evidence")
    if not isinstance(ev, list) or not ev:
        p.append("evidence: at least one entry")
        return
    ph = pins_hash(record) if hashes else None
    for i, e in enumerate(ev):
        w = f"evidence[{i}]"
        if not _section(p, w, e, EVIDENCE | EVIDENCE_MORE, EVIDENCE):
            continue
        if e.get("kind") not in KINDS:
            p.append(f"{w}: kind {e.get('kind')!r} is not one of {', '.join(KINDS)}")
        if not isinstance(e.get("by"), str) or _EMAIL.search(str(e.get("by"))):
            p.append(f"{w}: by is a contributor name (no e-mail address)")
        if not _iso(e.get("at")):
            p.append(f"{w}: at {e.get('at')!r} is not an ISO date and time")
        if not isinstance(e.get("ecadlib"), str):
            p.append(f"{w}: ecadlib is the version text")
        r = e.get("reader")
        if r is not None and not (isinstance(r, dict) and set(r) == {"provider", "model"}
                                  and all(isinstance(v, str) for v in r.values())):
            p.append(f"{w}: reader is null or {{provider, model}}")
        c = e.get("checks")
        if not isinstance(c, list) or not all(isinstance(x, str) for x in c) or set(c) - set(RULES):
            p.append(f"{w}: checks are release-check rule names ({', '.join(RULES)})")
        if hashes and e.get("footprint_hash") != (record.get("footprint") or {}).get("hash"):
            p.append(f"{w}: footprint_hash is not the record's footprint hash")
        if hashes and e.get("pins_hash") != ph:
            p.append(f"{w}: pins_hash does not match the record's pins and pin_map")


# ---------------------------------------------------------------- the part from the record alone
def footprint_text(record):
    return (record.get("footprint") or {}).get("kicad_mod")


def symbol_chain(record):
    """[KiCad symbol tree], what kicad_import.convert_symbol takes; None for a pointer record."""
    text = (record.get("symbol") or {}).get("kicad_sym")
    return [next(sexpr.find(sexpr.parse(text), "symbol"))] if text else None


def spec(record):
    return (record.get("footprint") or {}).get("spec")


def height(record):
    return (record.get("body") or {}).get("height_mm")


def build(record, out_dir, rules=None):
    """Rebuild the part from the record alone into out_dir (models/SchLib, models/PcbLib, models/KiCad) with the
    librarian's converters, and return its plan item (status 'draft': not checked yet; source store:<key_id>@r<rev>)
    for release_check.check_item(item, out_dir, rules). Raises PointerOnly for an EasyEDA pointer."""
    from . import altium_lib as AL, kicad_import as K, librarian as L, rules as RU
    fp_text, chain = footprint_text(record), symbol_chain(record)
    if fp_text is None or chain is None:
        raise PointerOnly(f"{record['key']['key']}: a pointer record ({record['footprint']['source']}): fetched from "
                          "its source, not built from the record")
    rules = rules or RU.DEFAULTS
    out = Path(out_dir)
    models = out / "models"
    for d in ("PcbLib", "SchLib", "KiCad"):
        (models / d).mkdir(parents=True, exist_ok=True)
    kid, rev = SK.key_id(record["key"]["key"]), record["rev"]
    store = f"store:{kid}@r{rev}"
    fsrc, ssrc, mpn = record["footprint"]["source"], record["symbol"]["source"], record["key"]["mpn"]
    fp_name = FS.real_name(fp_text) or record["part"]["package"]
    stem, notes = AL.file_stem(fp_name), []
    fp, t = K.convert_footprint(fp_text, fp_name, fsrc.split(":", 1)[-1], notes,
                                K.KICAD_LICENSE if fsrc.startswith("kicad:") else "")
    h, hs = height(record), (record.get("body") or {}).get("height_src") or ""
    try:
        K.add_body(fp, t, None, h, False, notes, gen_step=True, spec=spec(record),
                   height_src=f"store record: {hs}" if hs else "store record" if h else None)
    except ValueError as e:
        notes.append(f"no 3D body: {e}")
    kmod, pcb = models / "KiCad" / f"{stem}.kicad_mod", models / "PcbLib" / f"{stem}.PcbLib"
    if stem != fp_name:
        fp_text, _ = clean_footprint(fp_text, stem)
    kmod.write_text(fp_text, encoding="utf-8", newline="\n")
    AL.write_pcblib(pcb, [fp], RU.layers(rules))
    snotes = []
    name = str(chain[0][1])
    sym = K.convert_symbol(chain, name, fp["name"], L.kicad_params(mpn, False, ssrc.startswith("kicad:")), None, snotes)
    sym = L.house_symbol(sym, {"passive": False, "folder": ""})
    spath = models / "SchLib" / f"{AL.file_stem(sym['name'])}.SchLib"
    AL.write_schlib(spath, [sym])
    rel = lambda p: p.relative_to(out).as_posix()
    pname = lambda k: RU.param_name(k, rules)
    par = [{"name": pname("Manufacturer"), "value": record["key"]["maker"]},
           {"name": pname("Manufacturer Part Number"), "value": mpn}]
    url = ((record.get("part") or {}).get("datasheet") or {}).get("url")
    if url:
        par += [{"name": "ComponentLink2Description", "value": "Datasheet"},
                {"name": "ComponentLink2URL", "value": url}]
    if "CC-BY-SA-4.0" in (record["symbol"]["license"], record["footprint"]["license"]):
        par.append({"name": "Library Credit", "value": K.KICAD_CREDIT})
    types = L.kicad_chain_types(chain)
    mount = record["footprint"].get("mount_pads") or 0
    # (a record with no source's description, v1's usual case, is checked with its maker, MPN and package as the text:
    # the release check's fields rule is about the plan's fields, not the record's drawing this rebuild checks)
    desc = str(record["part"].get("description") or "").strip() or \
        f"{record['key']['maker']} {mpn} ({record['part'].get('package') or 'package'})"
    return {"name": mpn, "folder": "", "description": desc, "comment": mpn, "parameters": par,
            "symbol": {"ref": sym["name"], "file": rel(spath), **({"kicad_types": types} if types else {})},
            "footprint": {"ref": fp["name"], "file": rel(pcb), "kicad_mod": rel(kmod),
                          **({"mount_pads": mount} if mount else {})},
            "status": "draft", "source": {"symbol": store, "footprint": store},
            "store": {"key": record["key"]["key"], "key_id": kid, "rev": rev, "symbol": ssrc, "footprint": fsrc},
            "field_sources": {"Description": f"shared store record {kid} r{rev}"}, "verify": [],
            "librarian": {"tried": [f"store: {record['key']['key']} r{rev}"], "passive": False, "review": [],
                          "notes": notes + snotes}}
