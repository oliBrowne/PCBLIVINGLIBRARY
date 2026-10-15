"""Library rules: one file (ecadlib.toml) with your naming conventions, required parameters, layer mapping and
footprint density, which every ecadlib tool follows so new parts match your existing library.

    ecadlib init [--from DIR ...] [--yes]       scan what you already have and write ecadlib.toml (ecadlib/rules_infer.py)
    ecadlib rules check [PATH]                  validate a rules file and print the effective rules
    ecadlib rules show                          print the defaults (what ecadlib does with no file)

Without a file nothing changes: DEFAULTS is today's behaviour exactly. The file is searched in: an explicit
--rules PATH, then the folder the tool works in (plan / output folder) and its parents, then the current folder
and its parents. A workspace (UI / MCP) uses <workspace>/ecadlib.toml. Python 3.10 has no tomllib: there the
same content as JSON in ecadlib_rules.json works.

Plans (plan.json) keep ecadlib's own parameter names ("Manufacturer Part Number", "Supplier 1", ...); your house
names from [parameters.names] are applied where data leaves ecadlib: Altium 365 push, the DbLib export and sync,
the KiCad export and the Altium 365 drift check. Part names, folders, footprint names, required fields, default
parameters, layers and density are applied when the plan and the models are made."""
import argparse, copy, difflib, json, re, sys
from pathlib import Path

FILES = ("ecadlib.toml", "ecadlib_rules.json")
CATEGORIES = ("Resistors", "Capacitors", "Inductors", "Diodes", "Transistors", "Crystals", "Connectors", "ICs", "Misc")
REF_CLASS = {"R": "Resistors", "C": "Capacitors", "L": "Inductors", "D": "Diodes", "LED": "Diodes", "Q": "Transistors",
             "Y": "Crystals", "X": "Crystals", "J": "Connectors", "P": "Connectors", "CN": "Connectors", "U": "ICs", "IC": "ICs"}
CAT_REF = {"Resistors": "R", "Capacitors": "C", "Inductors": "L", "Diodes": "D", "Transistors": "Q", "Crystals": "Y",
           "Connectors": "J", "ICs": "U", "Misc": "U"}
PASSIVES = ("Resistors", "Capacitors", "Inductors")          # the categories with a generic symbol and a synthetic name
SOURCES = ("kicad", "easyeda", "datasheet")
FIELDS = ("mpn", "manufacturer", "value", "package", "metric", "category", "cat3", "ref", "tolerance", "voltage",
          "dielectric", "power", "description")
FP_FIELDS = ("kicad", "package", "metric", "ref", "cat3", "density", "pins")
VALUE_STYLES = ("keep", "rkm", "rkm_lower", "si", "si_upper", "si_unit")
DENSITY = {"L": "C", "N": "B", "M": "A"}                     # IPC-7351 least/nominal/most -> footprint_gen A/B/C
CANONICAL_PARAMS = ("Manufacturer", "Manufacturer Part Number", "Value", "Supplier 1", "Supplier Part Number 1",
                    "Supplier 2", "Supplier Part Number 2", "ComponentLink1Description", "ComponentLink1URL",
                    "ComponentLink2Description", "ComponentLink2URL")
KEY_COLUMNS = {"Part Number", "Library Ref", "Library Path", "Footprint Ref", "Footprint Path", "Description", "Comment"}

DEFAULTS = {
    "naming": {
        "component": "{mpn}",                  # parts that are not generic passives
        "passive": "{cat3}-{package}-{value}",  # resistors / capacitors / inductors with a value (RES-0603-10k)
        "by_category": {},                     # category or designator class -> template (overrides the two above)
        "symbol": "{mpn}",                     # generic passives keep the generic Resistor / Capacitor / Inductor symbol
        "footprint": "kicad",                  # kicad | ipc7351 | template ({package}, {metric}, {ref}, {cat3}, {kicad})
        "case": "keep",                        # keep | upper | lower (part names)
        "value_style": "keep",                 # keep | rkm (4K7, 100N) | rkm_lower (4k7, 100n) | si (4.7k) | si_upper | si_unit (100nF)
    },
    "parameters": {
        "required": ["Manufacturer", "Manufacturer Part Number", "Datasheet", "Description"],
        "names": {},                           # ecadlib name -> your name ("Manufacturer Part Number" = "MPN")
        "defaults": {},                        # parameter -> value added to every new part when missing
        "supplier_slots": 2,
    },
    "folders": {"root": "Components", "map": {}},   # map: category or designator class -> folder
    "layers": {"courtyard": "Mechanical15", "assembly": "Mechanical13", "body3d": "Mechanical13",
               "silkscreen": "TopOverlay", "designator": "TopOverlay"},
    "footprint": {"density": "N", "pin1": ["1", "A1"]},
    "sources": {"order": list(SOURCES)},
    "bom": {"columns": {}},                    # ecadlib field (mpn, manufacturer, value, ...) -> your BOM header
    "release": {"approvals": {}},              # same keys as approvals.json; used when there is no approvals.json
    "altium365": {"workspace": ""},
    "upkeep": {"schedule": "weekly", "checks": ["lifecycle", "upstream", "sourcing", "a365"],   # ecadlib.upkeep
               "price_jump_pct": 20.0, "build_qty": 1, "currency": "USD"},
    "sim": {"capacitor_esr": "", "capacitor_esl": "", "inductor_dcr": ""},   # ecadlib.sim_models ("" = none)
    "datasheet": {"model": "claude-opus-5-5", "effort": "high",              # ecadlib.datasheet (Claude datasheet reader)
                  "api_key_env": "ANTHROPIC_API_KEY", "max_cost_usd": 0.0, "auto_pages": 40},
    "symbols": {                               # ecadlib.symbol_layout: grouped pins for big IC symbols
        "layout_pins": 40,                     # parts with at least this many pins get grouped (0 = never)
        "split_pins": 0,                       # parts with more pins than this become multi-part (0 = never)
        "unit_pins": 48,                       # most pins in one signal part when split
        "stack_power": "stack",                # stack | hide | none (same-name power pins)
        "pin_length": 300,                     # mil
        "grid": 100,                           # mil (pin pitch, body edges)
        "order": ["power", "analog", "power_out", "reset", "clock", "debug", "usb", "adc", "other_in", "uart", "i2c", "spi",
                  "can", "eth", "ports", "other_out", "nc", "ground", "ep"],
        "sides": {},                           # group -> left | right | top | bottom (overrides)
    },
    "agent": {                                 # ecadlib.agent: the library agent (off unless enabled)
        "enabled": False, "interval_minutes": 60, "libs": [],
        "library_repo": "",                    # git repo for the batch PRs ("" = the workspace, if it is one)
        "library_path": "library",             # folder in that repo for the library files when it is not the workspace
        "base_branch": "",                     # "" = the repo's current branch
        "pictures_path": "agent-pictures",
        "github_repo": "",                     # owner/name ("" = from the origin remote)
        "github_token_env": "GITHUB_TOKEN", "github_api": "https://api.github.com",
        "batch_size": 20, "auto_push": False, "upkeep_schedule": "weekly", "datasheet_batch": True,
    },
}
MAP_KEYS = {("naming", "by_category"), ("parameters", "names"), ("parameters", "defaults"), ("folders", "map"),
            ("bom", "columns"), ("release", "approvals"), ("symbols", "sides")}
INT_RANGE = {("parameters", "supplier_slots"): (0, 9), ("upkeep", "build_qty"): (1, 1_000_000),
             ("symbols", "layout_pins"): (0, 100_000), ("symbols", "split_pins"): (0, 100_000), ("symbols", "unit_pins"): (8, 10_000),
             ("symbols", "pin_length"): (50, 1000), ("symbols", "grid"): (50, 100), ("datasheet", "auto_pages"): (0, 600),
             ("agent", "interval_minutes"): (1, 10_080), ("agent", "batch_size"): (1, 500)}
BOM_FIELDS = ("mpn", "manufacturer", "lcsc", "qty", "designator", "value", "footprint", "description", "libref", "dnp", "datasheet")


class RulesError(ValueError):
    pass


# ------------------------------------------------------------------ load / merge / validate
def find(start="."):
    """The rules file in `start` or one of its parents, else in the current folder or its parents; None if none."""
    seen = set()
    for base in (Path(start), Path.cwd()):
        try:
            base = base.resolve()
        except OSError:
            continue
        for d in [base, *base.parents]:
            if d in seen:
                break
            seen.add(d)
            for n in FILES:
                if (d / n).is_file():
                    return d / n
    return None


def parse_text(text, name="ecadlib.toml"):
    if str(name).endswith(".json"):
        try:
            return json.loads(text or "{}")
        except json.JSONDecodeError as e:
            raise RulesError(f"{name}: not valid JSON ({e})") from None
    try:
        import tomllib
    except ImportError:                       # Python 3.10
        raise RulesError(f"{name}: reading TOML needs Python 3.11+; on 3.10 put the same content as JSON in ecadlib_rules.json") from None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise RulesError(f"{name}: not valid TOML ({e})") from None


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def load(path=None, start="."):
    """Rules merged over DEFAULTS. path: an explicit file (must exist), a dict (used as the file's content), or None to
    search `start` and its parents, then the current folder. No file -> a copy of DEFAULTS. Bad content raises RulesError
    naming the key. The result carries `_file` (where it came from, '' for none)."""
    if isinstance(path, dict):
        if "_file" in path and all(k in path for k in DEFAULTS):
            return path                       # already loaded
        raw, where = path, "rules"
    else:
        f = Path(path) if path else find(start)
        if path and not f.is_file():
            raise RulesError(f"rules file {path} not found")
        if not f:
            return dict(copy.deepcopy(DEFAULTS), _file="")
        raw, where = parse_text(f.read_text(encoding="utf-8"), f.name), str(f)
    validate(raw, where)
    out = merge(DEFAULTS, {k: v for k, v in raw.items() if not k.startswith("_")})
    out["_file"] = "" if isinstance(path, dict) else where
    _normalise(out)
    return out


def resolve(rules, start="."):
    """rules argument of the tools: a loaded dict, a path, or None (search from `start`)."""
    return load(rules, start) if not (isinstance(rules, dict) and "_file" in rules) else rules


def _normalise(r):
    pin1 = r["footprint"]["pin1"]
    r["footprint"]["pin1"] = [pin1] if isinstance(pin1, str) else list(pin1)
    d = str(r["footprint"]["density"]).upper()
    r["footprint"]["density"] = {"A": "M", "B": "N", "C": "L"}.get(d, d)


def _err(where, key, msg):
    raise RulesError(f"{where}: {key} {msg}")


def _template_fields(t):
    return re.findall(r"\{(\w+)\}", t)


def validate(raw, where="ecadlib.toml"):
    """Raise RulesError naming the first bad key; returns raw unchanged when it is fine."""
    if not isinstance(raw, dict):
        raise RulesError(f"{where}: expected a table of sections")
    for sec, val in raw.items():
        if sec.startswith("_"):
            continue
        if sec not in DEFAULTS:
            _err(where, f"[{sec}]", f"is not a known section; known: {', '.join(DEFAULTS)}")
        if not isinstance(val, dict):
            _err(where, f"[{sec}]", "must be a table")
        for k, v in val.items():
            key = f"{sec}.{k}"
            if k not in DEFAULTS[sec]:
                close = difflib.get_close_matches(k, DEFAULTS[sec], 1)
                _err(where, key, f"is not a known key" + (f" (did you mean {sec}.{close[0]}?)" if close else
                                                          f"; known in [{sec}]: {', '.join(DEFAULTS[sec])}"))
            d = DEFAULTS[sec][k]
            if isinstance(d, bool):
                if not isinstance(v, bool):
                    _err(where, key, "must be true or false")
            elif (sec, k) in MAP_KEYS:
                if not isinstance(v, dict):
                    _err(where, key, "must be a table of name = value")
                if (sec, k) != ("release", "approvals") and not all(isinstance(x, (str, int, float)) for x in v.values()):
                    _err(where, key, "values must be text")
            elif isinstance(d, list):
                if isinstance(v, str) and (sec, k) == ("footprint", "pin1"):
                    v = [v]
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    _err(where, key, "must be a list of text values")
            elif isinstance(d, int) and not isinstance(d, bool):
                lo, hi = INT_RANGE.get((sec, k), (0, 9))
                if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                    _err(where, key, f"must be a whole number from {lo} to {hi}")
            elif isinstance(d, float):
                if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                    _err(where, key, "must be a number of 0 or more")
            elif sec == "sim":
                if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                    _err(where, key, "must be a value such as \"10m\" (ohm / henry)")
            elif not isinstance(v, str):
                _err(where, key, "must be text")
    n = raw.get("naming", {})
    for k in ("component", "passive", "symbol"):
        if k in n:
            _check_template(where, f"naming.{k}", n[k], FIELDS)
    for c, t in (n.get("by_category") or {}).items():
        if c not in CATEGORIES and c not in REF_CLASS:
            _err(where, f"naming.by_category.{c}", f"is not a category ({', '.join(CATEGORIES)}) or designator class ({', '.join(REF_CLASS)})")
        _check_template(where, f"naming.by_category.{c}", t, FIELDS)
    if "footprint" in n and n["footprint"] not in ("kicad", "ipc7351"):
        if "{" not in n["footprint"]:
            _err(where, "naming.footprint", f"= {n['footprint']!r}: use kicad, ipc7351 or a template such as \"{{ref}}{{package}}\"")
        _check_template(where, "naming.footprint", n["footprint"], FP_FIELDS)
    for k, ok in (("case", ("keep", "upper", "lower")), ("value_style", VALUE_STYLES)):
        if k in n and n[k] not in ok:
            _err(where, f"naming.{k}", f"= {n[k]!r}: must be one of {', '.join(ok)}")
    fp = raw.get("footprint", {})
    if "density" in fp and str(fp["density"]).upper() not in ("L", "N", "M", "A", "B", "C"):
        _err(where, "footprint.density", f"= {fp['density']!r}: must be L, N or M (IPC-7351 least / nominal / most)")
    for k, v in (raw.get("layers") or {}).items():
        try:
            layer_id(v)
        except ValueError as e:
            _err(where, f"layers.{k}", f"= {v!r}: {e}")
    for s in (raw.get("sources") or {}).get("order", []):
        if s not in SOURCES:
            _err(where, "sources.order", f"has {s!r}: sources are {', '.join(SOURCES)}")
    for f in (raw.get("bom") or {}).get("columns", {}):
        if f not in BOM_FIELDS:
            _err(where, f"bom.columns.{f}", f"is not a BOM field; fields are {', '.join(BOM_FIELDS)}")
    ds = raw.get("datasheet") or {}
    if "effort" in ds and ds["effort"] not in ("low", "medium", "high", "xhigh", "max"):
        _err(where, "datasheet.effort", f"= {ds['effort']!r}: must be low, medium, high, xhigh or max")
    if "api_key_env" in ds and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ds["api_key_env"]):
        _err(where, "datasheet.api_key_env", "must be the NAME of an environment variable (never the key itself)")
    if "model" in ds and not re.fullmatch(r"[a-z0-9][a-z0-9.\-]*", ds["model"]):
        _err(where, "datasheet.model", f"= {ds['model']!r}: must be a model id such as claude-opus-5-5")
    if (raw.get("release") or {}).get("approvals"):
        from .approvals import normalize_policy
        try:
            normalize_policy(dict(raw["release"]["approvals"]), f"{where}: release.approvals")
        except (ValueError, TypeError) as e:
            raise RulesError(str(e)) from None
    _check_symbols(where, raw.get("symbols") or {})
    ag = raw.get("agent") or {}
    if "upkeep_schedule" in ag and ag["upkeep_schedule"] not in ("off", "daily", "weekly", "monthly"):
        _err(where, "agent.upkeep_schedule", f"= {ag['upkeep_schedule']!r}: must be off, daily, weekly or monthly")
    if ag.get("github_token_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ag["github_token_env"]):
        _err(where, "agent.github_token_env", "must be the NAME of an environment variable (never the token itself)")
    if ag.get("github_repo") and not re.fullmatch(r"[\w.-]+/[\w.-]+", ag["github_repo"]):
        _err(where, "agent.github_repo", f"= {ag['github_repo']!r}: must be owner/name")
    if "github_api" in ag and not str(ag["github_api"]).startswith("https://"):
        _err(where, "agent.github_api", "must be an https:// URL")
    return raw


def _check_symbols(where, sy):
    from .symbol_layout import GROUPS, STACKS
    if "stack_power" in sy and sy["stack_power"] not in STACKS:
        _err(where, "symbols.stack_power", f"= {sy['stack_power']!r}: must be one of {', '.join(STACKS)}")
    grid = sy.get("grid", DEFAULTS["symbols"]["grid"])
    if "grid" in sy and grid not in (50, 100):
        _err(where, "symbols.grid", f"= {grid!r}: must be 50 or 100 (mil)")
    if sy.get("pin_length", DEFAULTS["symbols"]["pin_length"]) % grid:
        _err(where, "symbols.pin_length", f"= {sy.get('pin_length')!r}: must be a multiple of the grid ({grid} mil) so pin ends are on it")
    for g in sy.get("order", []):
        if g not in GROUPS:
            _err(where, "symbols.order", f"has {g!r}: groups are {', '.join(GROUPS)}")
    for g, side in (sy.get("sides") or {}).items():
        if g not in GROUPS:
            _err(where, f"symbols.sides.{g}", f"is not a group; groups are {', '.join(GROUPS)}")
        if side not in ("left", "right", "top", "bottom"):
            _err(where, f"symbols.sides.{g}", f"= {side!r}: must be left, right, top or bottom")


def _check_template(where, key, t, fields):
    if not isinstance(t, str) or not t.strip():
        _err(where, key, "must be a non-empty template")
    bad = [f for f in _template_fields(t) if f not in fields]
    if bad:
        _err(where, key, f"= {t!r}: unknown field {{{bad[0]}}}; fields are {', '.join('{' + f + '}' for f in fields)}")


def is_default(rules, section, key=None):
    r = (rules or DEFAULTS)[section]
    return (r == DEFAULTS[section]) if key is None else r.get(key) == DEFAULTS[section][key]


# ------------------------------------------------------------------ layers
NAMED_LAYERS = {"topoverlay": 33, "bottomoverlay": 34, "toppaste": 35, "bottompaste": 36, "topsolder": 37, "bottomsolder": 38,
                "toplayer": 1, "bottomlayer": 32, "keepout": 56}


def layer_id(name):
    """'Mechanical15' / 'Mechanical 15' / 'M15' / 'Top Overlay' / 71 -> Altium layer id (Mechanical n = 56 + n, 1..16)."""
    if isinstance(name, int):
        return name
    s = re.sub(r"[\s_]+", "", str(name)).lower()
    m = re.fullmatch(r"(?:mechanical|mech|m)(\d+)", s)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 16:
            raise ValueError("mechanical layers 1 to 16 are supported")
        return 56 + n
    if s in NAMED_LAYERS:
        return NAMED_LAYERS[s]
    raise ValueError("not an Altium layer name (e.g. Mechanical15, TopOverlay)")


def layer_name(i):
    return f"Mechanical{i - 56}" if 57 <= i <= 72 else {v: k for k, v in NAMED_LAYERS.items()}.get(i, str(i)).replace(
        "top", "Top").replace("bottom", "Bottom").replace("overlay", "Overlay").replace("paste", "Paste").replace(
        "solder", "Solder").replace("layer", "Layer").replace("keepout", "KeepOut")


def layers(rules):
    """{role: layer id} for the PcbLib writer; None when the rules keep ecadlib's layers."""
    r = (rules or DEFAULTS)["layers"]
    return None if r == DEFAULTS["layers"] else {k: layer_id(v) for k, v in r.items()}


def ipc_density(rules):
    """footprint_gen density letter (A/B/C) for the rules' IPC level; None for N (nominal, ecadlib's default)."""
    d = (rules or DEFAULTS)["footprint"]["density"]
    return None if d == "N" else DENSITY[d]


def density_for(spec, rules, explicit=None):
    """Density to build a spec with: an explicit choice, then the rules (when not N), then the spec's own (None = B)."""
    return explicit or ipc_density(rules) or (spec or {}).get("density")


# ------------------------------------------------------------------ parameters
def param_name(canonical, rules):
    return ((rules or DEFAULTS)["parameters"]["names"] or {}).get(canonical, canonical)


def canonical_param(house, rules):
    inv = {v: k for k, v in ((rules or DEFAULTS)["parameters"]["names"] or {}).items()}
    return inv.get(house, house)


def house_params(params, rules):
    """[{name, value}] in ecadlib names -> house names (order kept)."""
    if not (rules or DEFAULTS)["parameters"]["names"]:
        return params
    return [dict(p, name=param_name(p["name"], rules)) for p in params]


def house_item(item, rules):
    """A plan item as it should look in the user's library: parameter and cleared names in house names."""
    if not (rules or DEFAULTS)["parameters"]["names"]:
        return item
    return dict(item, parameters=house_params(item.get("parameters", []), rules),
                cleared=[param_name(c, rules) for c in item.get("cleared", [])])


def house_row(row, rules):
    """A DbLib row (column -> value) with parameter columns renamed; key and reserved columns stay."""
    if not (rules or DEFAULTS)["parameters"]["names"]:
        return row
    return {(k if k in KEY_COLUMNS else param_name(k, rules)): v for k, v in row.items()}


def default_params(rules):
    """[(canonical name, value)] to add when missing."""
    return [(canonical_param(k, rules), str(v)) for k, v in ((rules or DEFAULTS)["parameters"]["defaults"] or {}).items()]


def bom_columns(rules):
    """{BOM field: your header} for bom_triage.read_bom, or None."""
    return dict((rules or DEFAULTS)["bom"]["columns"]) or None


# ------------------------------------------------------------------ naming
SI_PRE = [(1e-12, "p"), (1e-9, "n"), (1e-6, "u"), (1e-3, "m"), (1.0, ""), (1e3, "k"), (1e6, "M"), (1e9, "G")]
UNIT = {"Resistors": "", "Capacitors": "F", "Inductors": "H"}
CAT3 = {"Resistors": "RES", "Capacitors": "CAP", "Inductors": "IND", "Diodes": "DIO", "Transistors": "TRA",
        "Crystals": "CRY", "Connectors": "CON", "ICs": "ICS", "Misc": "MIS"}


PREFIXES = {"Resistors": "kMG", "Capacitors": "pnu", "Inductors": "num"}


def _mantissa(v, cat):
    pres = [(f, p) for f, p in SI_PRE if p in PREFIXES[cat] or (cat == "Resistors" and p == "")]
    if v == 0:
        return 0.0, pres[0][1]
    f, p = next(((f, p) for f, p in reversed(pres) if abs(v) >= f * 0.9999), pres[0])
    return round(v / f, 6), p


def _num(m):
    s = f"{m:.6g}"
    return s


def fmt_value(raw, cat, style):
    """BOM value -> the rules' value style. Unparseable values and style 'keep' come back unchanged."""
    if style == "keep" or cat not in UNIT or not str(raw or "").strip():
        return raw
    from .kicad_index import parse_value
    v = parse_value(raw)
    return raw if v is None else fmt_number(v, cat, style)


def fmt_number(v, cat, style):
    """A value in base units (ohm, F, H) written in a value style."""
    m, p = _mantissa(v, cat)
    unit_letter = {"": "R"} if cat == "Resistors" else {}
    if style in ("rkm", "rkm_lower"):
        L = unit_letter.get(p, p)
        L = L.upper() if style == "rkm" else (L if L in ("M", "R") else L.lower())
        if cat == "Resistors" and style == "rkm_lower" and p == "k":
            L = "k"
        ip, _, fp = _num(m).partition(".")
        return f"{ip}{L}{fp}" if fp else f"{ip}{L}"
    s = _num(m)
    if style == "si":
        return f"{s}{p}"
    if style == "si_upper":
        return f"{s}{p.upper() if p not in ('m',) else p}"
    return f"{s}{p}{UNIT[cat]}"                # si_unit


TOL_RE = re.compile(r"(?<![\d.])±?\s?(\d+(?:\.\d+)?)\s?%")
VOLT_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?V(?:DC)?(?![A-Za-z])")
DIEL_RE = re.compile(r"\b(X[5-8][RSTPV]|C0G|NP0|COG|Y5V|Z5U)\b", re.I)
POWER_RE = re.compile(r"(?<![\w.])(\d+/\d+|\d+(?:\.\d+)?)\s?W(?![A-Za-z])")


def line_fields(ln, cat, pkg, rules=None, ref=""):
    """Template fields for one BOM line (dict from bom_triage.read_bom)."""
    from .kicad_index import METRIC_TO_IMPERIAL
    text = " ".join(str(ln.get(k, "")) for k in ("value", "description"))
    g = lambda rx: (rx.search(text).group(0).replace(" ", "").lstrip("±") if rx.search(text) else "")
    met = {v: k for k, v in METRIC_TO_IMPERIAL.items()}.get(pkg or "", "")
    diel = g(DIEL_RE).upper().replace("COG", "C0G")
    return {"mpn": ln.get("mpn", ""), "manufacturer": ln.get("manufacturer", ""),
            "value": fmt_value(ln.get("value", ""), cat, (rules or DEFAULTS)["naming"]["value_style"]),
            "package": pkg or "", "metric": met, "category": cat, "cat3": CAT3.get(cat, cat[:3].upper()),
            "ref": ref or CAT_REF.get(cat, "U"), "tolerance": g(TOL_RE), "voltage": g(VOLT_RE).replace("VDC", "V"),
            "dielectric": diel, "power": g(POWER_RE), "description": ln.get("description", "")}


def render(template, fields):
    """Fill a template; an empty field takes one neighbouring separator with it (RES_{package}_{value} without a
    package gives RES_10k, not RES__10k). Returns '' when every field in it is empty."""
    names = _template_fields(template)
    if names and not any(str(fields.get(n) or "") for n in names):
        return ""
    out = template
    for n in names:
        v = str(fields.get(n) or "")
        if v:
            out = out.replace("{" + n + "}", v, 1)
        else:
            out = re.sub(r"[_\-. ]?\{" + n + r"\}|\{" + n + r"\}[_\-. ]?", "", out, count=1)
    return out.strip("_-. ")


def template_for(cat, rules, ref=""):
    n = (rules or DEFAULTS)["naming"]
    by = n.get("by_category") or {}
    return by.get(cat) or by.get(ref) or (n["passive"] if cat in PASSIVES else n["component"])


def component_name(item_or_fields, rules, cat=None):
    """Part name per the rules. item_or_fields: a fields dict from line_fields (or a BOM line plus cat)."""
    f = item_or_fields if "cat3" in item_or_fields else line_fields(item_or_fields, cat or "Misc", "", rules)
    cat = f.get("category") or cat or "Misc"
    t = template_for(cat, rules, f.get("ref", ""))
    name = render(t, f)
    if ("{value}" in t and not f.get("value")) or ("{mpn}" in t and not f.get("mpn")):
        name = ""                              # the template's key field is missing: named like any other part
    if not name:
        name = render((rules or DEFAULTS)["naming"]["component"], f) or f.get("mpn") or f.get("value") or ""
    case = (rules or DEFAULTS)["naming"]["case"]
    return name.upper() if case == "upper" else name.lower() if case == "lower" else name


def symbol_name(fields, rules):
    t = (rules or DEFAULTS)["naming"]["symbol"]
    return render(t, fields) or fields.get("mpn", "")


# ------------------------------------------------------------------ footprint names
CHIP_H = {"R": {"0201": 26, "0402": 40, "0603": 55, "0805": 70, "1206": 70, "1210": 70, "2010": 70, "2512": 70},
          "C": {"0201": 33, "0402": 55, "0603": 90, "0805": 125, "1206": 160, "1210": 250, "1812": 250},
          "L": {"0402": 55, "0603": 90, "0805": 125, "1206": 160}, "LED": {"0402": 55, "0603": 80, "0805": 100, "1206": 120}}
CHIP_PREFIX = {"R": "RESC", "C": "CAPC", "L": "INDC", "LED": "LEDC", "D": "DIOC"}
IPC_NAME = re.compile(r"^((RES|CAP|IND|LED|DIO|FUS)(C|M|MP)\d{4}X\d+|(SOIC|SOP|SSOP|TSOP|QFP|QFN|SON|DFN|SOT|SOD|BGA|PQFN|CAPAE)"
                      r"\d+(P\d+)?X\d+(X\d+)?(-\d+)?)([LNM])$", re.I)
GULL = {"SOIC": "SOIC", "SOP": "SOP", "TSSOP": "SOP", "MSOP": "SOP", "SSOP": "SOP", "VSSOP": "SOP"}


def _h(spec, default):
    if spec and spec.get("height_max"):
        from .footprint_gen import dim
        return int(round(dim(spec["height_max"])[2] * 100))
    return default


def ipc7351_name(kicad, spec=None, density="N"):
    """KiCad library name -> IPC-7351 land pattern name (RESC1608X55N, SOIC127P600X175-8N, QFN50P300X300X100-17N).
    Heights come from the package spec when there is one, else typical values; None when the family is unknown."""
    from .kicad_index import METRIC_TO_IMPERIAL
    n = str(kicad).split(":")[-1]
    m = re.match(r"^(R|C|L|LED|D)_(\d{4})_(\d{4})Metric", n)
    if m:
        kind, imp, met = m.groups()
        h = _h(spec, CHIP_H.get(kind, CHIP_H["C"]).get(imp, 60))
        return f"{CHIP_PREFIX[kind]}{met}X{h}{density}"
    m = re.match(r"^(SOIC|SOP|TSSOP|MSOP|SSOP|VSSOP)-(\d+)_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm_P(\d+(?:\.\d+)?)mm", n)
    if m:
        fam, pins, bw, _, p = m.groups()
        span = {"3.9": 600, "4.4": 640, "3": 490, "5.3": 780, "7.5": 1030, "5.6": 780}.get(bw, int(round((float(bw) + 2.1) * 100)))
        h = _h(spec, {"SOIC": 175, "SSOP": 200}.get(fam, 120))
        return f"{GULL[fam]}{int(round(float(p) * 100))}P{span}X{h}-{pins}{density}"
    m = re.match(r"^(V|W|U|X)?(QFN|DFN|SON)-(\d+)(-1EP)?_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm_P(\d+(?:\.\d+)?)mm", n)
    if m:
        _, fam, pins, ep, a, b, p = m.groups()
        h = _h(spec, 100)
        fam = "SON" if fam in ("DFN", "SON") else "QFN"
        return f"{fam}{int(round(float(p) * 100))}P{int(round(float(a) * 100))}X{int(round(float(b) * 100))}X{h}-{int(pins) + (1 if ep else 0)}{density}"
    m = re.match(r"^(L|T)QFP-(\d+)_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm_P(\d+(?:\.\d+)?)mm", n)
    if m:
        _, pins, a, b, p = m.groups()
        h = _h(spec, 160)
        return f"QFP{int(round(float(p) * 100))}P{int(round((float(a) + 2) * 100))}X{int(round((float(b) + 2) * 100))}X{h}-{pins}{density}"
    sot = {"SOT-23": "SOT95P240X{h}-3", "SOT-23-5": "SOT95P280X{h}-5", "SOT-23-6": "SOT95P280X{h}-6", "SOT-223-3_TabPin2": "SOT230P700X{h}-4"}
    if n in sot:
        return sot[n].format(h=_h(spec, 180 if "223" in n else 145 if n != "SOT-23" else 120)) + density
    return None


def footprint_name(name, rules, kind=None, spec=None):
    """Footprint name per [naming] footprint: 'kicad' keeps it; 'ipc7351' converts KiCad names it knows; a template
    is used when its fields can be filled (chip packages), else the name is kept."""
    mode = (rules or DEFAULTS)["naming"]["footprint"]
    if not name or mode == "kicad":
        return name
    density = (rules or DEFAULTS)["footprint"]["density"]
    if mode == "ipc7351":
        return name if IPC_NAME.match(str(name)) else (ipc7351_name(name, spec, density) or name)
    from .kicad_index import METRIC_TO_IMPERIAL, package_of
    pkg = package_of(name) or ""
    if pkg not in METRIC_TO_IMPERIAL.values():
        return name
    m = re.match(r"^(R|C|L|LED|D|CP)_", str(name).split(":")[-1])
    ref = kind or (m.group(1) if m else "")
    cat = REF_CLASS.get(ref, "")
    f = {"kicad": str(name).split(":")[-1], "package": pkg, "metric": {v: k for k, v in METRIC_TO_IMPERIAL.items()}[pkg],
         "ref": ref, "cat3": CAT3.get(cat, ""), "density": density, "pins": "2"}
    if any(not f.get(x) for x in _template_fields(mode)):
        return name
    return render(mode, f) or name


# ------------------------------------------------------------------ folders
def folder_for(cat, rules, root=None):
    """Workspace folder for a category (or designator class): [folders] map, else <root>/<category>."""
    r = (rules or DEFAULTS)["folders"]
    m = r.get("map") or {}
    hit = m.get(cat) or next((v for k, v in m.items() if REF_CLASS.get(k) == cat), None)
    if hit:
        return hit
    cat = REF_CLASS.get(cat, cat)
    return f"{root if root is not None else r['root']}/{cat}"


# ------------------------------------------------------------------ writing ecadlib.toml
def _q(s):
    return json.dumps(str(s), ensure_ascii=False).replace("\x7f", "\\u007f")   # TOML refuses a raw DEL


def _c(s):
    """One comment line: notes carry text from scanned libraries (part names, BOM headers), and a newline in one
    would end the comment and put the rest into the file as TOML."""
    return re.sub(r"[\x00-\x1f\x7f\u2028\u2029]+", " ", str(s))


def _key(k):
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _q(k)


def _val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_val(x)}" for k, x in v.items()) + " }"
    return _q(v)


HELP = {
    "naming": "How new parts, symbols and footprints are named. Template fields: " + ", ".join("{" + f + "}" for f in FIELDS),
    "naming.component": "parts that are not generic passives",
    "naming.passive": "resistors, capacitors and inductors with a value",
    "naming.footprint": "kicad (KiCad/IPC names as today) | ipc7351 (RESC1608X55N) | a template such as \"{ref}{package}\"",
    "naming.value_style": "keep | rkm (4K7, 100N) | rkm_lower (4k7, 100n) | si (4.7k, 100n) | si_upper (4.7K) | si_unit (100nF)",
    "parameters": "Parameters every part must have, your names for ecadlib's, and values to add when missing",
    "parameters.names": "ecadlib name = your name",
    "folders": "Altium 365 / DbLib folder per category or designator class",
    "layers": "Altium layers for footprint content (Mechanical1..16, TopOverlay)",
    "footprint": "IPC-7351 density L (least) | N (nominal) | M (most); pin-1 pad names accepted",
    "sources": "Where missing symbols and footprints are looked for, in order",
    "bom": "Your BOM column headers, when ecadlib's guess is wrong (field = header)",
    "release": "Sign-off policy (same keys as approvals.json); an approvals.json next to a plan wins",
    "sim": "Parasitics for the built-in passive simulation models (sim-models), e.g. capacitor_esr = \"10m\"",
    "datasheet": "Claude datasheet reader (ecadlib datasheet / librarian): model, effort, which environment variable holds the "
                 "API key, a cost cap per run (0 = none) and how many pages to send before picking pages by text (0 = all)",
    "datasheet.api_key_env": "name of the environment variable with the API key; the key itself never goes in this file",
    "datasheet.max_cost_usd": "refuse a run whose estimated worst-case cost is higher (0 = no cap; --yes overrides)",
    "symbols": "Big IC symbols: pins grouped by function (power top, ground bottom, inputs left, ports right); see `ecadlib symbol -h`",
    "symbols.layout_pins": "parts with at least this many pins get the grouped layout; smaller parts keep the plain one (0 = never)",
    "symbols.split_pins": "more pins than this: a multi-part symbol (power part + signal parts); 0 = one part always",
    "symbols.stack_power": "stack (one pin shown, the rest on top of it) | hide (the rest hidden, tied by name) | none",
    "agent": "Library agent (`ecadlib agent run|serve`): watches projects/BOMs, builds and proves new parts, one GitHub PR per "
             "batch for review, pushes approved parts to Altium 365, turns upkeep findings into work. Off unless enabled",
    "agent.interval_minutes": "minutes between runs of `ecadlib agent serve`",
    "agent.library_repo": "git repo the batch PRs are made in (\"\" = the workspace when it is a git repo; none: PR text in agent/batches/)",
    "agent.github_token_env": "name of the environment variable with the GitHub token; the token itself never goes in this file",
    "agent.auto_push": "push approved parts to Altium 365 on each run (false: `ecadlib agent push`)",
    "agent.upkeep_schedule": "off | daily | weekly | monthly: how often upkeep findings are turned into queue items",
    "agent.batch_size": "most parts in one review batch (one pull request each)",
    "symbols.order": "group order on each side; groups: power analog power_out ground ep reset clock debug usb adc other_in uart i2c spi can eth ports other_out nc",
}


def dumps(rules, notes=None, only=None, header=None):
    """Commented TOML. notes: {"section.key": [comment lines]} written above each key. only: keys to write
    (others are left out, i.e. stay at their defaults); None writes every section that differs from the defaults
    plus every key that has a note."""
    notes = notes or {}
    lines = [_c(h) for h in header or []]
    for sec, vals in rules.items():
        if sec.startswith("_") or sec not in DEFAULTS:
            continue
        keys = [k for k in vals if (only is None and (vals[k] != DEFAULTS[sec].get(k) or f"{sec}.{k}" in notes))
                or (only is not None and f"{sec}.{k}" in only)]
        if not keys:
            continue
        lines += ["", *(f"# {_c(h)}" for h in [HELP.get(sec)] if h), f"[{sec}]"]
        tables = []
        for k in keys:
            v = vals[k]
            if (sec, k) in MAP_KEYS and isinstance(v, dict) and v and all(not isinstance(x, dict) for x in v.values()):
                tables.append(k)
                continue
            for n in notes.get(f"{sec}.{k}", []) + ([f"({HELP[f'{sec}.{k}']})"] if f"{sec}.{k}" in HELP else []):
                lines.append(f"# {_c(n)}")
            lines.append(f"{_key(k)} = {_val(v)}")
        for k in tables:
            lines += ["", f"[{sec}.{_key(k)}]"]
            for n in notes.get(f"{sec}.{k}", []) + ([f"({HELP[f'{sec}.{k}']})"] if f"{sec}.{k}" in HELP else []):
                lines.append(f"# {_c(n)}")
            for kk, vv in vals[k].items():
                for n in notes.get(f"{sec}.{k}.{kk}", []):
                    lines.append(f"# {_c(n)}")
                lines.append(f"{_key(kk)} = {_val(vv)}")
    return "\n".join(lines).strip("\n") + "\n"


def diff(old, new, name="ecadlib.toml"):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"{name} (current)", f"{name} (proposed)"))


def effective(rules):
    return {k: v for k, v in rules.items() if not k.startswith("_")}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib rules", description="check or show library rules (ecadlib.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate a rules file and print the effective rules")
    c.add_argument("path", nargs="?")
    sub.add_parser("show", help="print the defaults (what ecadlib does without a file)")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        print(dumps(DEFAULTS, only={f"{s}.{k}" for s in DEFAULTS for k in DEFAULTS[s]}))
        return 0
    try:
        r = load(a.path)
    except RulesError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"# {r['_file'] or 'no rules file found: defaults'}")
    print(json.dumps(effective(r), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
