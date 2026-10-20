"""Library rules: one file (ecadlib.toml) with your naming conventions, required parameters, layer mapping and
footprint density, which every ecadlib tool follows so new parts match your existing library.

    ecadlib init [--from DIR ...] [--yes]       scan what you already have and write ecadlib.toml (ecadlib/rules_infer.py)
    ecadlib rules check [PATH]                  validate a rules file and print the effective rules
    ecadlib rules show                          print the defaults (what ecadlib does with no file)

Without a file nothing changes: DEFAULTS is today's behaviour exactly. The file is searched in: an explicit
--rules PATH, then the folder the tool works in (plan / output folder) and its parents, then the current folder
and its parents. A workspace (UI / MCP) uses <workspace>/ecadlib.toml. Python 3.10 has no tomllib: there the
tomli package (installed with ecadlib) reads it, or the same content as JSON in ecadlib_rules.json works.

Plans (plan.json) keep ecadlib's own parameter names ("Manufacturer Part Number", "Supplier 1", ...); your house
names from [parameters.names] are applied where data leaves ecadlib: Altium 365 push, the DbLib export and sync,
the KiCad export and the Altium 365 drift check. Part names, folders, footprint names, required fields, default
parameters, layers and density are applied when the plan and the models are made."""
import argparse, copy, difflib, json, math, os, re, sys
from pathlib import Path

FILES = ("ecadlib.toml", "ecadlib_rules.json")
CATEGORIES = ("Resistors", "Capacitors", "Inductors", "Diodes", "Transistors", "Crystals", "Connectors", "ICs", "Misc")
REF_CLASS = {"R": "Resistors", "C": "Capacitors", "L": "Inductors", "D": "Diodes", "LED": "Diodes", "Q": "Transistors",
             "Y": "Crystals", "X": "Crystals", "J": "Connectors", "P": "Connectors", "CN": "Connectors", "U": "ICs", "IC": "ICs"}
CAT_REF = {"Resistors": "R", "Capacitors": "C", "Inductors": "L", "Diodes": "D", "Transistors": "Q", "Crystals": "Y",
           "Connectors": "J", "ICs": "U", "Misc": "U"}
PASSIVES = ("Resistors", "Capacitors", "Inductors")          # the categories with a generic symbol and a synthetic name
SOURCES = ("kicad", "easyeda", "standard", "datasheet")   # standard: ecadlib.stdparts (offline, before Claude)
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
    "release": {"approvals": {},               # same keys as approvals.json; used when there is no approvals.json
                "parity": "hold"},             # hold | warn | off: KiCad vs Altium files of a part differ (ecadlib.parity)
    "altium365": {"workspace": ""},
    "upkeep": {"schedule": "weekly", "checks": ["lifecycle", "upstream", "a365", "changes"]},   # ecadlib.upkeep
    "datasheet": {"model": "claude-opus-5-5", "effort": "high",              # ecadlib.datasheet (Claude datasheet reader)
                  "api_key_env": "ANTHROPIC_API_KEY", "max_cost_usd": 10.0, "auto_pages": 40,   # per run; 0 = no cap
                  "ask_above_usd": 1.0,                 # ecadlib.ai_cost: the dialogs ask twice above this (a max_cost_usd you set wins)
                  # ecadlib.ds_route: pages and model per part (route = "single": `model` for every part, as before)
                  "page_budget": 6, "route": "single", "ladder": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"],
                  "escalate": True, "max_attempts": 4,
                  "zoom_drawings": False,               # also send sharper close-ups of the drawing pages (A/B test first)
                  # ecadlib.crosscheck: a second source (standard table / a second read) before a footprint is accepted
                  "cross_check": "auto", "cross_check_model": ""},
    "symbols": {                               # ecadlib.symbol_layout: IC symbol pins grouped by function
        "layout_pins": 1,                      # parts with at least this many pins get grouped (0 = never)
        "power_side": "top",                   # top: power top, ground bottom | left: both on the left side, top and bottom empty
        "resistor": "us",                      # us: zigzag (ANSI/IEEE 315) | iec: box (IEC 60617)
        "split_pins": 0,                       # parts with more pins than this become multi-part (0 = never)
        "unit_pins": 48,                       # most pins in one signal part when split
        "stack_power": "none",                 # none (every pin its own end) | stack | hide (same-name power pins)
        "pin_length": 300,                     # mil
        "grid": 100,                           # mil (pin pitch, body edges)
        "order": ["power", "analog", "reset", "clock", "debug", "usb", "adc", "other_in", "other", "power_out", "uart", "i2c",
                  "spi", "can", "eth", "ports", "other_out", "nc", "ground", "ep"],
        "sides": {},                           # group -> left | right | top | bottom (overrides)
    },
    "agent": {                                 # the library agent is parked on a branch; what stays here is used by
        "github_repo": "",                     # `upkeep --github` (owner/name; "" = from the origin remote)
        "github_token_env": "GITHUB_TOKEN", "github_api": "https://api.github.com",
    },
}
# [agent.proof]: ecadlib.visual_check (`ecadlib visual-check`). Empty model / effort / api_key_env = use [datasheet].
DEFAULTS.setdefault("agent", {})["proof"] = {"model": "", "effort": "", "api_key_env": "", "max_cost_usd": 0.0,
                                             "fail_on": ["error"], "max_pages": 6, "dpi": 200}
SUB_RANGE = {("agent", "proof", "max_pages"): (1, 100), ("agent", "proof", "dpi"): (50, 400)}
# GitLab merge requests instead of GitHub PRs for `upkeep` (ecadlib.forge); "auto" picks by the origin remote
DEFAULTS["agent"].update({"git_host": "auto", "gitlab_project": "", "gitlab_token_env": "GITLAB_TOKEN",
                          "gitlab_api": "https://gitlab.com/api/v4"})
# [notify]: webhooks (ecadlib.notify); [providers]: order and choice of part sources incl. plugins (ecadlib.plugins)
DEFAULTS["notify"] = {"targets": [], "retries": 3, "timeout_s": 10, "backoff_s": 2.0}
# [ai]: bring your own model (ecadlib.llm): provider, model, endpoint, key variable, guide rails. Defaults = Claude as before.
from . import llm as _LL
DEFAULTS["ai"] = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _LL.DEFAULTS.items()}
for _k in _LL.SECTION_KEYS:                           # [datasheet] / [agent.proof] may pick their own ("" = [ai])
    DEFAULTS["datasheet"].setdefault(_k, "")
    DEFAULTS["agent"]["proof"].setdefault(_k, "")
DEFAULTS["providers"] = {"order": [], "unofficial": False}   # EasyEDA / JLCPCB search: opt in
# [setup]: written by the `ecadlib init` setup questions (ecadlib.firstrun). libs / workspace are the defaults for
# --lib / --workspace (after the command line and ECADLIB_LIB / ECADLIB_WORKSPACE); empty = as before.
DEFAULTS["setup"] = {"tool": "both", "libs": [], "workspace": ""}
# [style]: your house style learned from your library + your written direction (ecadlib.house_style, `ecadlib style`).
# profile: the learned file, relative to this rules file. apply: auto = new parts are adapted by code wherever a
# profile exists; off = never. The model for `ecadlib style learn/apply --ai` may be set here ("" = [ai]).
DEFAULTS["style"] = {"profile": "house_style.json", "apply": "auto", "model": "", "api_key_env": "",
                     **{k: "" for k in _LL.SECTION_KEYS}}
# [change]: change control (ecadlib.change_gate): what a change to a released part needs, per class of change.
DEFAULTS["change"] = {"identity": "hold", "electrical": "hold", "geometry_major": "hold", "geometry_minor": "warn",
                      "cosmetic": "pass", "approvers": 1, "a365_transition": "", "unlisted": "hold"}
MAP_KEYS = {("naming", "by_category"), ("parameters", "names"), ("parameters", "defaults"), ("folders", "map"),
            ("bom", "columns"), ("release", "approvals"), ("symbols", "sides")}
INT_RANGE = {("parameters", "supplier_slots"): (0, 9),
             ("symbols", "layout_pins"): (0, 100_000), ("symbols", "split_pins"): (0, 100_000), ("symbols", "unit_pins"): (8, 10_000),
             ("symbols", "pin_length"): (50, 1000), ("symbols", "grid"): (50, 100), ("datasheet", "auto_pages"): (0, 600), ("datasheet", "page_budget"): (0, 600),
             ("datasheet", "max_attempts"): (1, 10),
             ("notify", "retries"): (0, 10), ("change", "approvers"): (1, 9), ("notify", "timeout_s"): (1, 120)}
BOM_FIELDS = ("mpn", "manufacturer", "lcsc", "qty", "designator", "value", "footprint", "description", "libref", "dnp", "datasheet")


class RulesError(ValueError):
    pass


# ------------------------------------------------------------------ load / merge / validate
def find(start="."):
    """The rules file in `start` or one of its parents, else in the current folder or its parents; None if none.
    $ECADLIB_CONFIG (a file path), when set, is used instead of the search (an explicit --rules still wins), except
    that a workspace's own file still comes first: one found from `start` (a plan, a workspace) in a folder that is not
    the current folder or one of its parents, as app.Workspace does."""
    env = os.environ.get("ECADLIB_CONFIG", "").strip()
    if env and Path(start) != Path("."):
        try:
            here = Path.cwd().resolve()
            mine = {here, *here.parents}
            base = Path(start).resolve()
            for d in [base, *base.parents]:
                if d in mine:
                    break
                for n in FILES:
                    if (d / n).is_file():
                        return d / n
        except OSError:
            pass
    if env:                                    # run inside a workspace (its file beside librarian/ or uploads/)
        here = Path.cwd()
        if (here / "librarian").is_dir() or (here / "uploads").is_dir():
            for n in FILES:
                if (here / n).is_file():
                    return (here / n).resolve()
        if not Path(env).expanduser().is_file():
            raise RulesError(f"ECADLIB_CONFIG={env}: no such file")
        return Path(env).expanduser().resolve()
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


def toml_module():
    """tomllib (Python 3.11+), else tomli (the same API; a dependency on 3.10), else None."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return None
    return tomllib


def parse_text(text, name="ecadlib.toml"):
    if isinstance(text, str):
        text = text.lstrip("﻿")          # "UTF-8 with BOM" (Notepad, PowerShell 5, Visual Studio): fine
    if str(name).endswith(".json"):
        try:
            return json.loads(text or "{}")
        except json.JSONDecodeError as e:
            raise RulesError(f"{name}: not valid JSON ({e})") from None
    tomllib = toml_module()
    if tomllib is None:                       # Python 3.10 without the tomli package
        raise RulesError(f"{name}: reading TOML on Python 3.10 needs the tomli package (pip install tomli), "
                         "or put the same content as JSON in ecadlib_rules.json") from None
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
    if isinstance(raw, dict) and "schema_version" in raw:      # ecadlib/migrate.py: a file from a newer ecadlib is refused
        from . import migrate as MG
        try:
            raw = MG.check_key("rules", raw, where)
        except MG.SchemaTooNew as e:
            raise RulesError(str(e)) from None
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


# Keys an older ecadlib knew, accepted and ignored so an old ecadlib.toml still loads (the Fusion / EAGLE and EasyEDA
# Pro exports were removed after 0.3.1).
REMOVED_KEYS = {("release", "eagle"), ("release", "easyeda"),
                # cut on 2026-10-19 (price and stock; the library agent, parked on a branch)
                ("upkeep", "price_jump_pct"), ("upkeep", "build_qty"), ("upkeep", "currency"),
                *(("agent", k) for k in ("watch", "enabled", "interval_minutes", "libs", "library_repo", "library_path",
                                         "base_branch", "pictures_path", "batch_size", "auto_push", "upkeep_schedule",
                                         "datasheet_batch", "manufacturer_aliases"))}
REMOVED_SECTIONS = {"sim", "hub", "assembly"}          # cut on 2026-10-19: an old file with them loads, they are ignored


def validate(raw, where="ecadlib.toml"):
    """Raise RulesError naming the first bad key; returns raw unchanged when it is fine."""
    if not isinstance(raw, dict):
        raise RulesError(f"{where}: expected a table of sections")
    for sec, val in raw.items():
        if sec.startswith("_") or sec == "schema_version" or sec in REMOVED_SECTIONS:   # schema_version: ecadlib/migrate.py
            continue
        if sec not in DEFAULTS:
            _err(where, f"[{sec}]", f"is not a known section; known: {', '.join(DEFAULTS)}")
        if not isinstance(val, dict):
            _err(where, f"[{sec}]", "must be a table")
        for k, v in val.items():
            key = f"{sec}.{k}"
            if sec == "ai" and k in DEFAULTS["ai"]:      # checked by ecadlib.llm.validate below
                continue
            if (sec, k) in REMOVED_KEYS:                # left from an older ecadlib; ignored
                continue
            if (sec, k) == ("notify", "targets"):
                from .notify import check_targets
                check_targets(where, v, _err)
                continue
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
                if (sec, k) == ("bom", "columns") and all(isinstance(x, str) or (isinstance(x, list) and x and all(
                        isinstance(y, str) for y in x)) for x in v.values()):
                    pass                                   # a header, or a list of headers (house aliases)
                elif (sec, k) != ("release", "approvals") and not all(isinstance(x, (str, int, float)) for x in v.values()):
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
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0:
                    _err(where, key, "must be a number of 0 or more (not nan or inf)")
            elif isinstance(d, dict):
                _check_table(where, sec, k, v, d)
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
    if "model" in ds and not _LL.MODEL_RE.fullmatch(str(ds["model"])):
        _err(where, "datasheet.model", f"= {ds['model']!r}: must be a model id such as claude-opus-5-5 or qwen2.5:7b")
    if "route" in ds and ds["route"] not in ("single", "ladder"):
        _err(where, "datasheet.route", f"= {ds['route']!r}: must be single (one model) or ladder (cheapest model that passes)")
    for m in ds.get("ladder") or [] if isinstance(ds.get("ladder"), list) else []:
        if not _LL.MODEL_RE.fullmatch(str(m)):
            _err(where, "datasheet.ladder", f"has {m!r}: must be model ids such as claude-haiku-5-5 (or endpoint:model "
                                            "for an [ai.endpoints] entry), cheapest first")
    if "cross_check" in ds and ds["cross_check"] not in ("auto", "always", "off"):
        _err(where, "datasheet.cross_check", f"= {ds['cross_check']!r}: must be auto (standard table, else a second read), "
                                             "always (a second read for every part) or off")
    if ds.get("cross_check_model") and not _LL.MODEL_RE.fullmatch(str(ds["cross_check_model"])):
        _err(where, "datasheet.cross_check_model", f"= {ds['cross_check_model']!r}: must be a model id (or endpoint:model); "
                                                   "\"\" = the model whose answer is checked")
    if "ladder" in ds and isinstance(ds["ladder"], list) and not ds["ladder"]:
        _err(where, "datasheet.ladder", "must name at least one model")
    st = raw.get("setup") or {}
    if "tool" in st and st["tool"] not in ("altium", "kicad", "both"):
        _err(where, "setup.tool", f"= {st['tool']!r}: must be altium, kicad or both")
    _LL.validate(where, raw.get("ai"), [("datasheet", ds), ("agent.proof", (raw.get("agent") or {}).get("proof")),
                                        ("style", raw.get("style"))], _err)
    sty = raw.get("style") or {}
    if "apply" in sty and sty["apply"] not in ("auto", "off"):
        _err(where, "style.apply", f"= {sty['apply']!r}: must be auto (adapt new parts to house_style.json) or off")
    if sty.get("profile") and not str(sty["profile"]).endswith(".json"):
        _err(where, "style.profile", f"= {sty['profile']!r}: must be a .json file (written by `ecadlib style learn`)")
    if "parity" in (raw.get("release") or {}) and raw["release"]["parity"] not in ("hold", "warn", "off"):
        _err(where, "release.parity", f"= {raw['release']['parity']!r}: must be hold, warn or off")
    if (raw.get("release") or {}).get("approvals"):
        from .approvals import normalize_policy
        try:
            normalize_policy(dict(raw["release"]["approvals"]), f"{where}: release.approvals")
        except (ValueError, TypeError) as e:
            raise RulesError(str(e)) from None
    if raw.get("change"):
        from .change_gate import normalize_policy as change_policy
        try:
            change_policy(dict(raw["change"]), f"{where}: [change]")
        except ValueError as e:
            raise RulesError(str(e)) from None
    _check_symbols(where, raw.get("symbols") or {})
    ag = raw.get("agent") or {}
    if ag.get("github_token_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ag["github_token_env"]):
        _err(where, "agent.github_token_env", "must be the NAME of an environment variable (never the token itself)")
    if ag.get("github_repo") and not re.fullmatch(r"[\w.-]+/[\w.-]+", ag["github_repo"]):
        _err(where, "agent.github_repo", f"= {ag['github_repo']!r}: must be owner/name")
    if "github_api" in ag and not str(ag["github_api"]).startswith("https://"):
        _err(where, "agent.github_api", "must be an https:// URL")
    if "git_host" in ag and ag["git_host"] not in ("auto", "github", "gitlab"):
        _err(where, "agent.git_host", f"= {ag['git_host']!r}: must be auto, github or gitlab")
    if ag.get("gitlab_token_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ag["gitlab_token_env"]):
        _err(where, "agent.gitlab_token_env", "must be the NAME of an environment variable (never the token itself)")
    if ag.get("gitlab_project") and not re.fullmatch(r"\d+|[\w.-]+(/[\w.-]+)+", ag["gitlab_project"]):
        _err(where, "agent.gitlab_project", f"= {ag['gitlab_project']!r}: must be group/name (subgroups allowed) or a numeric id")
    if "gitlab_api" in ag and not re.fullmatch(r"https://[^\s/]+(/[^\s]*)?/api/v4/?", str(ag["gitlab_api"])):
        _err(where, "agent.gitlab_api", "must be an https:// URL ending in /api/v4 (e.g. https://gitlab.example.com/api/v4)")
    nf = raw.get("notify") or {}
    if "backoff_s" in nf and nf["backoff_s"] > 60:
        _err(where, "notify.backoff_s", "must be 60 seconds or less")
    for x in (raw.get("providers") or {}).get("order", []):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", x):
            _err(where, "providers.order", f"has {x!r}: provider names are letters, digits, _ . -")
    return raw


def _check_table(where, sec, k, v, d):
    """A nested table such as [agent.proof]: known keys, each of its default's type."""
    if not isinstance(v, dict):
        _err(where, f"{sec}.{k}", "must be a table")
    for kk, vv in v.items():
        key = f"{sec}.{k}.{kk}"
        if kk not in d:
            _err(where, key, f"is not a known key; known in [{sec}.{k}]: {', '.join(d)}")
        dd = d[kk]
        if isinstance(dd, list):
            vv = [vv] if isinstance(vv, str) else vv
            if not isinstance(vv, list) or not all(isinstance(x, str) for x in vv):
                _err(where, key, "must be a list of text values")
        elif isinstance(dd, int) and not isinstance(dd, bool):
            lo, hi = SUB_RANGE.get((sec, k, kk), (0, 1_000_000))
            if not isinstance(vv, int) or isinstance(vv, bool) or not lo <= vv <= hi:
                _err(where, key, f"must be a whole number from {lo} to {hi}")
        elif isinstance(dd, float):
            if not isinstance(vv, (int, float)) or isinstance(vv, bool) or not math.isfinite(vv) or vv < 0:
                _err(where, key, "must be a number of 0 or more (not nan or inf)")
        elif not isinstance(vv, str):
            _err(where, key, "must be text")
    if (sec, k) == ("agent", "proof"):
        if v.get("effort") not in (None, "", "low", "medium", "high", "xhigh", "max"):
            _err(where, "agent.proof.effort", f"= {v['effort']!r}: must be low, medium, high, xhigh or max (empty = [datasheet])")
        if v.get("model") and not _LL.MODEL_RE.fullmatch(str(v["model"])):
            _err(where, "agent.proof.model", f"= {v['model']!r}: must be a model id such as claude-opus-5-5")
        if v.get("api_key_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v["api_key_env"]):
            _err(where, "agent.proof.api_key_env", "must be the NAME of an environment variable (never the key itself)")
        fo = [v["fail_on"]] if isinstance(v.get("fail_on"), str) else v.get("fail_on") or []
        if any(x not in ("error", "warning") for x in fo):
            _err(where, "agent.proof.fail_on", f"= {v['fail_on']!r}: severities are error, warning")


RESISTOR_STYLES = ("us", "iec")


def resistor_style(rules):
    """[symbols] resistor: "us" (zigzag, the default) or "iec" (box)."""
    return ((rules or {}).get("symbols") or {}).get("resistor", DEFAULTS["symbols"]["resistor"])


def _check_symbols(where, sy):
    from .symbol_layout import GROUPS, STACKS, POWER_SIDES
    if "power_side" in sy and sy["power_side"] not in POWER_SIDES:
        _err(where, "symbols.power_side", f"= {sy['power_side']!r}: must be one of {', '.join(POWER_SIDES)}")
    if "resistor" in sy and sy["resistor"] not in RESISTOR_STYLES:
        _err(where, "symbols.resistor", f"= {sy['resistor']!r}: must be one of {', '.join(RESISTOR_STYLES)}")
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
    out, renamed = {}, set()
    for k, v in row.items():
        h = k if k in KEY_COLUMNS else param_name(k, rules)
        if h != k:                          # the canonical field wins over a parameter already spelled like the house
            if v or h not in out:           # name (a stale "Mfr PN" BOM column), unless the canonical one is empty
                out[h] = v
            renamed.add(h)
        elif h not in renamed or not out.get(h):
            out[h] = v
    return out


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
    unit_letter = {"": {"Resistors": "R", "Capacitors": "F", "Inductors": "H"}[cat]}   # 1.5 F -> 1F5 (IEC 60062), never 15
    if style in ("rkm", "rkm_lower"):
        L = unit_letter.get(p, p)
        L = (L if L == "m" else L.upper()) if style == "rkm" else (L if L in ("M", "R", "F", "H") else L.lower())   # milli
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
    "bom": "Your BOM column headers, when ecadlib's guess is wrong (field = header, or field = [\"header\", \"alias\"])",
    "release": "Sign-off policy (same keys as approvals.json); an approvals.json next to a plan wins",
    "release.parity": "hold | warn | off: a part whose KiCad and Altium files differ (pads, drills, courtyard, 3D, pins) is held",
    "change": "Change control: what a change to a released part needs, per class (hold = a person approves it, warn = "
              "released and listed, pass = released); approvers = people per held change (2 = two-person rule)",
    "change.identity": "hold | warn: the MPN, manufacturer or value changed",
    "change.electrical": "hold | warn: pins renamed / swapped / retyped, pins or pads added or removed, pin -> pad changed",
    "change.geometry_major": "hold | warn | pass: pads moved, rotated, resized beyond tolerance, drills, 3D origin",
    "change.geometry_minor": "hold | warn | pass: pad size within tolerance, corner rounding, margins, courtyard, 3D height",
    "change.cosmetic": "hold | warn | pass: silkscreen, fab, texts, symbol graphics, pin lengths, other fields",
    "change.a365_transition": "optional: the Altium 365 life-cycle transition (or state) a new component revision is "
                              "moved to after an approved change, e.g. \"Promote to Prototype\"; empty = leave it",
    "change.unlisted": "hold | warn | pass: Altium project check: a component the workspace has a newer revision of, "
                       "whose changes the library's ledger cannot list (hold = the check fails)",
    "datasheet": "Claude datasheet reader (ecadlib datasheet / librarian): model, effort, which environment variable holds the "
                 "API key, a cost cap per run (0 = none) and how many pages to send before picking pages by text (0 = all)",
    "datasheet.api_key_env": "name of the environment variable with the API key; the key itself never goes in this file",
    "datasheet.max_cost_usd": "refuse a run whose estimated worst-case cost is higher (default 10.00 per run; 0 = no cap; --yes does not lift it, "
                             "--max-cost-usd does)",
    "datasheet.ask_above_usd": "the Altium dialog, KiCad window and web UI always show the model and cost before an AI step; "
                               "above this (or above max_cost_usd when set) they ask a second time (default 1.00; 0 = "
                               "ask twice for every paid read)",
    "datasheet.page_budget": "pages sent on the first try, picked by text (drawing for this package, land pattern, pin table, "
                             "ordering info); widened to 3x, then the whole PDF, when the answer is incomplete (0 = whole PDF)",
    "datasheet.route": "single = `model` for every part | ladder = start on the cheapest model of `ladder` that suits the "
                       "package (pins, family, pages) and move up when datasheet's checks fail",
    "datasheet.ladder": "models for route = \"ladder\", cheapest first",
    "datasheet.escalate": "retry with more pages / the next model when the answer is incomplete or fails the checks",
    "datasheet.max_attempts": "most answers paid for per part (first try included)",
    "datasheet.zoom_drawings": "also send each picked drawing / land-pattern page as two close-up pictures (top and bottom "
                               "half, about 260 dpi), so small dimension text is sharper than in the PDF's own page "
                               "picture; about 10,000 more input tokens per drawing page (off by default)",
    "datasheet.cross_check": "auto: compare every datasheet footprint with the standard package table, or (not in the table) "
                             "with a second read of only the critical numbers; disagreement holds the part | always: the "
                             "second read for every datasheet part | off",
    "datasheet.cross_check_model": "model for the second read (\"\" = the model whose answer is checked; endpoint:model works)",
    "ai.temperature": "auto: temperature 0 (+ seed) where the model takes one; Claude 4.7+/5.x reject it and get nothing | "
                      "off | a number 0-2",
    "agent.proof": "Claude visual check (ecadlib visual-check): model, effort, api_key_env (empty = [datasheet]), max_cost_usd per "
                   "check (0 = no cap), fail_on (mismatch severities that fail a part), max_pages, dpi of the datasheet pages",
    "ai": "Bring your own model (ecadlib.llm): provider = anthropic (default) | openai-compatible | openai | openrouter | "
          "gemini | ollama | lmstudio | vllm | llamacpp; model; base_url; api_key_env (the NAME of the variable, never the "
          "key); assist = auto | off | full (guide rails by the model's tier); feedback_retry; detect; [ai.profiles.\"model\"] "
          "tier/pdf/vision/json_schema/context overrides; [ai.endpoints.name] for a mixed ladder (name:model)",
    "ai.assist": "auto: rails by the model's tier (frontier: none beyond support) | off: support only | full: every rail",
    "ai.feedback_retry": "one more turn with datasheet's exact check errors before a failing answer is kept (every model)",
    "datasheet.provider": "\"\" = [ai] provider; set it to read datasheets with another provider than the rest",
    "symbols": "IC symbols: pins grouped by function (inputs left, outputs right, power top, ground bottom); see `ecadlib symbol -h`",
    "symbols.layout_pins": "parts with at least this many pins get the grouped layout; smaller parts keep the plain one (0 = never)",
    "symbols.power_side": "top: power pins on top, ground on the bottom | left: top and bottom empty, power at the top of the left side, ground at its bottom",
    "symbols.resistor": "us: zigzag resistor symbol (ANSI, default) | iec: box resistor symbol (IEC 60617)",
    "symbols.split_pins": "more pins than this: a multi-part symbol (power part + signal parts); 0 = one part always",
    "symbols.stack_power": "none (default: every pin its own end) | stack (one pin shown, the rest on top of it) | hide (the rest hidden, tied by name)",
    "agent": "GitHub / GitLab settings for `ecadlib upkeep --github | --gitlab` (the library agent that also used them is "
             "parked on a branch); [agent.proof] is the visual check's model",
    "agent.github_token_env": "name of the environment variable with the GitHub token; the token itself never goes in this file",
    "agent.git_host": "auto (GitLab when origin is on a gitlab* host or the gitlab_api host, else GitHub) | github | gitlab",
    "agent.gitlab_api": "GitLab REST v4 base URL; self-hosted: https://gitlab.example.com/api/v4",
    "agent.gitlab_project": "group/name or numeric id (\"\" = from the origin remote)",
    "agent.gitlab_token_env": "name of the environment variable with the GitLab token (api scope); never the token itself",
    "notify": "Webhooks when upkeep has news: [[notify.targets]] name, type = slack|teams|json, url_env = NAME "
              "of the env var holding the URL, events = upkeep_changes|lifecycle (default all), "
              "token_env (json only); retries with backoff, never stops the run",
    "providers": "Part sources for triage / librarian / UI, in order, including plugins (ecadlib.providers entry points); "
                 "[] = each command's built-in default (the official Nexar / DigiKey / Mouser APIs first, then EasyEDA); "
                 "unofficial = true lets in the undocumented EasyEDA and JLCPCB search endpoints (default false; `ecadlib init` asks); "
                 "`ecadlib plugins list` shows what is installed",
    "setup": "Written by `ecadlib init`: the tool you use, your library folders (the default --lib) and the workspace "
             "(the default --workspace); relative paths start at this file's folder",
    "setup.tool": "altium | kicad | both: which outputs `ecadlib init` and `ecadlib demo` point you at",
    "symbols.order": "group order on each side; groups: power analog power_out ground ep reset clock debug usb adc other_in other uart i2c spi can eth ports other_out nc",
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
