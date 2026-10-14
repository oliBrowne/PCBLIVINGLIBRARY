"""Simulation models: find SPICE and IBIS models for a librarian plan's parts, check them, attach them.

    ecadlib sim-models out/librarian/plan.json [--models DIR ...] [--attach] [--offline] [--cache DIR] [--no-net]

Find (per plan item, first hit wins):
  passives   R, C, L get a built-in model from their Value; no download. A capacitor with an ESR (item parameter
             `ESR`, or rules [sim] capacitor_esr) and/or `ESL`, or an inductor with a `DCR`, gets a small generated
             .subckt (models/Sim/<part>.lib) instead of the ideal element.
  --models   your own folders of vendor models (.lib .cir .sub .mod .spi .spice .mdl .ckt .ibs .txt, also inside .zip),
             indexed by .subckt / .model / IBIS [Component] name and file name, matched against the MPN
  nexar      Octopart documents (supSearchMpn ... documentCollections) whose name says SPICE / IBIS / simulation,
             when NEXAR_SUPPLY_TOKEN or NEXAR_CLIENT_ID/SECRET are set
  vendor     direct-download URL patterns per manufacturer (VENDORS; each is marked `assumed`), fetched only when the
             host's robots.txt allows it; a wrong guess just fails validation
  pages      manufacturer model pages and the IBIS Open Forum's model list (ibis.org/models/) are reported as links
             for a person: vendor download pages are mostly behind JavaScript, click-through licences or logins, so
             they are never scraped
Digital ICs (MCUs, USB/UART bridges, logic, interfaces) want IBIS; diodes, transistors, regulators and op-amps SPICE.
Everything goes through httpcache (disk cache, per-host rate limit; --offline = cache only).

Check: every model file is parsed with the small parsers here (parse_spice: .subckt name + ports, .model name + type,
.include, encrypted blocks; parse_ibis: [IBIS Ver], [File Name], [Component], [Manufacturer], [Package], [Pin] list,
[Model] names, pin models that do not exist). Model pins are mapped to the symbol's pins (SchLib read back):
  verified   every model pin matched a symbol pin by name (aliases: A/ANODE, K/CATHODE, G/GATE, +IN/IN+/INP ...),
             or a 2-pin passive
  verify     mapped by pin number or by position: probably right, a person has to look
  mismatch   pin counts disagree / IBIS pins are not the package's pins: reported, never attached
--attach (writes): Altium -- a SIM implementation (RECORD=45 ModelType=SIM, RECORD=46/47 pin map, RECORD=48) is added
to the part's .SchLib and the model file is copied beside it as <ModelName>.ckt (.subckt) / .mdl (.model) / .ibs (IBIS,
linked as ModelType=SI); KiCad -- the item gets Sim.Device / Sim.Type / Sim.Library / Sim.Name / Sim.Pins / Sim.Params,
which `ecadlib kicad-export` writes into the .kicad_sym (model files go to <lib>.sim/). An attached model whose pin map
is `verify` is added to the item's `verify` list, so the release check holds the part until a person allows it.
Altium 365: DesReleaseComponentInput (tests/nexar/schema.graphql) takes symbol and footprint files only; the
SIMULATION_MODEL content kind exists but there is no release input for it, so model files are not pushed (the
.SchLib's SIM link travels with the symbol; release the model in Altium Designer and relink). Reported per part.

Outputs next to the plan: plan.json (each item's `sim_model`), sim_models.json, sim_models.md, models/Sim/."""
import argparse, hashlib, io, json, os, re, shutil, struct, sys, time, zipfile
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from . import altium_lib as AL
from .httpcache import UA, CachedHTTP
from .kicad_index import norm_mpn, parse_value

try:                                            # rules.py is optional (RULES_CONTRACT.md); only [sim] keys are read
    from . import rules as RU
except ImportError:                             # pragma: no cover
    RU = None

MODEL_EXT = {".lib", ".cir", ".sub", ".subckt", ".mod", ".model", ".spi", ".spice", ".sp", ".mdl", ".ckt", ".txt", ".ibs", ".ibis"}
MAX_FILE = 20 * 1024 * 1024
IBIS_VERSIONS = {"1.1", "2.0", "2.1", "3.0", "3.1", "3.2", "4.0", "4.1", "4.2", "5.0", "5.1", "6.0", "6.1", "7.0", "7.1", "7.2"}
IBIS_LIST = "https://ibis.org/models/"
NEXAR_QUERY = """query ($q: String!, $limit: Int) {
  supSearchMpn(q: $q, limit: $limit) {
    results { part { mpn manufacturer { name } documentCollections { name documents { name url mimeType } }
      cad { hasAltium hasKicad } } }
  }
}"""

# Manufacturer model pages (for a person) and direct-download patterns (fetched, then validated). The page URLs are
# the vendors' public product pages; every `direct` pattern is ASSUMED (not confirmed from this sandbox: the hosts are
# blocked here) and only ever tried when robots.txt allows it. {mpn} = full MPN, {base} = MPN minus ordering suffix.
VENDORS = [
    {"match": r"texas|\bti\b", "pages": ["https://www.ti.com/product/{base}#design-development"], "direct": []},
    {"match": r"onsemi|on semi|fairchild", "pages": ["https://www.onsemi.com/design/resources/design-resources/models?rpn={mpn}"], "direct": []},
    {"match": r"nexperia|nxp", "pages": ["https://www.nexperia.com/search?q={mpn}"],
     "direct": ["https://assets.nexperia.com/documents/spice-model/{base}.txt"]},
    {"match": r"diodes", "pages": ["https://www.diodes.com/part/view/{base}"], "direct": []},
    {"match": r"stmicro|\bst\b", "pages": ["https://www.st.com/en/search.html#q={base}"], "direct": []},
    {"match": r"infineon|international rectifier", "pages": ["https://www.infineon.com/search?q={mpn}"], "direct": []},
    {"match": r"analog devices|linear tech|maxim", "pages": ["https://www.analog.com/en/search.html?q={base}"], "direct": []},
    {"match": r"microchip|atmel|microsemi", "pages": ["https://www.microchip.com/en-us/search?searchQuery={base}"], "direct": []},
    {"match": r"vishay", "pages": ["https://www.vishay.com/en/search/?query={mpn}"], "direct": []},
    {"match": r"rohm", "pages": ["https://www.rohm.com/search?q={mpn}"], "direct": []},
    {"match": r"toshiba", "pages": ["https://toshiba.semicon-storage.com/search.html?q={mpn}"], "direct": []},
    {"match": r"wch|nanjing qinheng", "pages": ["https://www.wch-ic.com/search?q={base}"], "direct": []},
    {"match": r"ftdi|future technology", "pages": ["https://ftdichip.com/?s={base}"], "direct": []},
    {"match": r"silicon lab|silabs", "pages": ["https://www.silabs.com/search#q={base}"], "direct": []},
]

DIGITAL_RE = re.compile(r"\b(mcu|micro-?controller|microprocessor|processor|usb|uart|bridge|fpga|cpld|ethernet|phy|"
                        r"transceiver|logic|buffer|flip-?flop|latch|shift register|gate|interface|serial|memory|flash|eeprom|"
                        r"sram|dram|i2c|spi|rs-?485|level shift)", re.I)
DIGITAL_MPN = re.compile(r"^(STM32|STM8|ATMEGA|ATTINY|ATSAM|PIC\d|DSPIC|ESP32|ESP8266|RP2040|RP2350|NRF5|CH340|CH341|CH343|"
                         r"CP210|FT232|FT2232|FT231|74(HC|HCT|LVC|AHC|ACT|LV|AC)|SN74|MSP430|LPC\d|MIMXRT|GD32|W25Q|IS25)", re.I)
REG_RE = re.compile(r"\b(regulator|ldo|buck|boost|dc-?dc|converter|step-?down|step-?up|reference|pmic)\b", re.I)
OPAMP_RE = re.compile(r"\b(op-?amp|operational|comparator|amplifier|instrumentation amp)\b", re.I)
MOS_RE = re.compile(r"\b(mosfet|fet|n-?ch(annel)?|p-?ch(annel)?|nmos|pmos)\b", re.I)
BJT_RE = re.compile(r"\b(bjt|npn|pnp|bipolar|darlington)\b", re.I)


# ---------------------------------------------------------------- numbers
SUF = [(1e12, "T"), (1e9, "G"), (1e6, "Meg"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"), (1e-15, "f")]


def spice_num(x):
    """1e4 -> '10k', 4.7e-6 -> '4.7u', 1e6 -> '1Meg' (SPICE: M is milli)."""
    if not x:
        return "0"
    for f, s in SUF:
        if abs(x) >= f * 0.9999999:
            return f"{x / f:.6g}{s}"
    return f"{x:.6g}"


def value_of(text, unit=""):
    """BOM value -> float. Accepts what kicad_index.parse_value does, plus upper-case prefixes ('100NF', '4.7UF')."""
    s = str(text or "").strip()
    v = parse_value(s)
    if v is None:
        s2 = re.sub(r"(?<=\d)([NUP])(?=F|H|$)", lambda m: m.group(1).lower(), s.upper().replace("Ω", ""))
        v = parse_value(s2)
    return v


# ---------------------------------------------------------------- SPICE parser
MODEL_PINS = {"D": ["A", "K"], "NPN": ["C", "B", "E"], "PNP": ["C", "B", "E"], "LPNP": ["C", "B", "E"],
              "NMOS": ["D", "G", "S"], "PMOS": ["D", "G", "S"], "VDMOS": ["D", "G", "S"], "NJF": ["D", "G", "S"], "PJF": ["D", "G", "S"],
              "R": ["1", "2"], "C": ["1", "2"], "L": ["1", "2"]}


def _spice_lines(text):
    out = []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        line = re.split(r"\s;|^;|\s\$\s", line)[0].strip()      # ';' comments, PSpice ' $ ' comments
        if line.startswith("+") and out:
            out[-1] += " " + line[1:].strip()
        elif line:
            out.append(line)
    return out


def parse_spice(text):
    """-> {subckts: [{name, pins, params}], models: [{name, type}], includes, encrypted, errors}."""
    res = {"format": "spice", "subckts": [], "models": [], "includes": [], "encrypted": False, "errors": []}
    if "\x00" in text[:4096]:
        res["errors"].append("binary file, not a SPICE netlist")
        return res
    if re.search(r"\$CDNENCSTART|Begin Encrypted|\.protect|\*\s*encrypted", text, re.I):
        res["encrypted"] = True
    stack = []
    for line in _spice_lines(text):
        tok = line.split()
        kw = tok[0].lower()
        if kw == ".subckt":
            if len(tok) < 2:
                res["errors"].append(f"'.subckt' without a name: {line[:60]}")
                continue
            pins, params = [], {}
            rest = tok[2:]
            for i, t in enumerate(rest):
                if t.lower() in ("params:", "param:") or "=" in t:
                    params = dict(re.findall(r"([\w.]+)\s*=\s*([^\s=]+)", " ".join(rest[i:])))
                    break
                pins.append(t)
            sc = {"name": tok[1], "pins": pins, "params": params}
            stack.append(sc)
            if len(stack) == 1:
                res["subckts"].append(sc)
        elif kw == ".ends":
            if not stack:
                res["errors"].append(f"'.ends' without '.subckt'")
            else:
                stack.pop()
        elif kw == ".model" and not stack:
            m = re.match(r"\.model\s+(\S+)\s+([A-Za-z]+)", line, re.I)
            if m:
                res["models"].append({"name": m.group(1), "type": m.group(2).upper()})
            else:
                res["errors"].append(f"unreadable .model line: {line[:60]}")
        elif kw in (".include", ".inc", ".lib") and len(tok) > 1 and not stack:
            res["includes"].append(tok[-1].strip("\"'"))
    for sc in stack:
        res["errors"].append(f".subckt {sc['name']} has no .ends")
    if not res["subckts"] and not res["models"]:
        res["errors"].append("no .subckt or .model found")
    return res


# ---------------------------------------------------------------- IBIS parser
def _ibis_kw(s):
    return re.sub(r"[\s_]+", " ", s.strip().lower())


def parse_ibis(text, filename=None):
    """-> {version, file_name, components: [{name, manufacturer, package, pins: [{pin, signal, model}]}], models, errors, warnings}."""
    res = {"format": "ibis", "version": None, "file_name": None, "components": [], "models": [], "selectors": [],
           "errors": [], "warnings": []}
    cc, sect, comp, seen_end = "|", None, None, False
    for raw in text.replace("\r", "").split("\n"):
        line = raw.split(cc, 1)[0].rstrip() if cc in raw else raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
        if m:
            kw, arg = _ibis_kw(m.group(1)), m.group(2).strip()
            sect = kw
            if kw == "comment char":
                c = arg[:1]
                if c and arg[1:].startswith("_char"):
                    cc = c
            elif kw == "ibis ver":
                res["version"] = arg
            elif kw == "file name":
                res["file_name"] = arg
            elif kw == "component":
                comp = {"name": arg, "manufacturer": "", "package": {}, "pins": []}
                res["components"].append(comp)
            elif kw == "manufacturer" and comp is not None:
                comp["manufacturer"] = arg
            elif kw == "model":
                res["models"].append(arg.split()[0] if arg else "")
            elif kw == "model selector":
                res["selectors"].append(arg.split()[0] if arg else "")
            elif kw == "end":
                seen_end = True
            continue
        if sect == "pin" and comp is not None:
            t = line.split()
            if len(t) >= 3:
                comp["pins"].append({"pin": t[0], "signal": t[1], "model": t[2]})
            else:
                res["errors"].append(f"[Pin] line with fewer than 3 columns: {line.strip()[:60]}")
        elif sect == "package" and comp is not None:
            t = line.split()
            if len(t) >= 2:
                comp["package"][t[0]] = t[1:]
    if res["version"] is None:
        res["errors"].append("no [IBIS Ver]: not an IBIS file")
    elif res["version"] not in IBIS_VERSIONS:
        res["warnings"].append(f"[IBIS Ver] {res['version']} is not a known IBIS version")
    if not res["components"]:
        res["errors"].append("no [Component]")
    for c in res["components"]:
        if not c["pins"]:
            res["errors"].append(f"[Component] {c['name']} has no [Pin] list")
        if not c["manufacturer"]:
            res["warnings"].append(f"[Component] {c['name']} has no [Manufacturer]")
        known = {m.lower() for m in res["models"] + res["selectors"]} | {"power", "gnd", "nc", "nic"}
        bad = sorted({p["model"] for p in c["pins"] if p["model"].lower() not in known})
        if bad:
            res["errors"].append(f"[Component] {c['name']}: pin models without a [Model]: {', '.join(bad[:8])}")
    if res["file_name"] and filename and res["file_name"].lower() != Path(filename).name.lower():
        res["warnings"].append(f"[File Name] {res['file_name']} differs from the file's name {Path(filename).name}")
    if res["version"] and not seen_end:
        res["warnings"].append("no [End]")
    return res


def parse_model(text, filename=""):
    if re.search(r"^\s*\[IBIS Ver\]", text, re.I | re.M) or str(filename).lower().endswith((".ibs", ".ibis")):
        return parse_ibis(text, filename)
    return parse_spice(text)


def model_names(p):
    if p["format"] == "ibis":
        return [c["name"] for c in p["components"]]
    return [s["name"] for s in p["subckts"]] + [m["name"] for m in p["models"]]


# ---------------------------------------------------------------- part classification
def _par(item):
    return {p["name"]: str(p["value"]) for p in item.get("parameters", [])}


def classify(item, sym_pins=None):
    """-> R C CP L LED D MOS BJT Q REG OPAMP DIGITAL IC or None, and the wanted format ('spice'/'ibis'/'builtin')."""
    cat = item.get("folder", "").split("/")[-1]
    fp = item.get("footprint", {}).get("ref") or ""
    par = _par(item)
    text = " ".join([item.get("description") or "", par.get("Description", ""), item["name"]])
    mpn = par.get("Manufacturer Part Number") or item["name"]
    names = {re.sub(r"[^A-Z]", "", n.upper()) for _, n in (sym_pins or [])}
    if cat == "Resistors":
        return "R", "builtin"
    if cat == "Capacitors":
        return ("CP" if fp.startswith("CP_") else "C"), "builtin"
    if cat == "Inductors":
        return "L", "builtin"
    if cat == "Diodes":
        return ("LED" if fp.startswith("LED_") or re.search(r"\bLED\b", text, re.I) else "D"), "spice"
    if cat == "Transistors":
        if MOS_RE.search(text) or {"G", "D", "S"} <= names or {"GATE", "DRAIN", "SOURCE"} <= names:
            return "MOS", "spice"
        if BJT_RE.search(text) or {"B", "C", "E"} <= names:
            return "BJT", "spice"
        return "Q", "spice"
    if cat == "ICs" or cat not in ("Connectors", "Crystals"):
        if DIGITAL_MPN.match(norm_mpn(mpn)) or DIGITAL_RE.search(text):
            return "DIGITAL", "ibis"
        if REG_RE.search(text):
            return "REG", "spice"
        if OPAMP_RE.search(text):
            return "OPAMP", "spice"
        return ("IC", "spice") if cat == "ICs" else (None, None)
    return None, None


# ---------------------------------------------------------------- pin names and mapping
ALIASES = [("A", "ANODE", "AN"), ("K", "CATHODE", "KA", "CAT"), ("G", "GATE"), ("D", "DRAIN"), ("S", "SOURCE"),
           ("B", "BASE"), ("C", "COLLECTOR", "COL"), ("E", "EMITTER", "EMI"),
           ("IN+", "+IN", "INP", "NONINV", "NI", "+"), ("IN-", "-IN", "INN", "INV", "-"),
           ("V+", "VCC", "VDD", "VS+", "+VS", "VPOS", "V+IN"), ("V-", "VEE", "VSS", "VS-", "-VS", "VNEG"),
           ("OUT", "OUTPUT", "VO", "Y"), ("VIN", "IN", "VI", "PVIN"), ("VOUT", "VO", "OUT"), ("GND", "VSS", "0", "AGND", "PGND", "COM"),
           ("EN", "ENABLE", "ON"), ("FB", "FEEDBACK", "ADJ"), ("SW", "LX", "PH"), ("BST", "BOOT", "CB"), ("PG", "PGOOD", "POK")]


def canon(name):
    s = str(name or "").upper()
    s = re.sub(r"~\{([^}]*)\}", r"\1N", s)                       # KiCad ~{SHDN} -> SHDNN (active low marked)
    if "\\" in s:                                                 # Altium S\H\D\N\ overline
        s = s.replace("\\", "") + "N"
    return re.sub(r"[^A-Z0-9+\-]", "", s)


def _alias_sets(n):
    c = canon(n)
    return {c} | {canon(a) for grp in ALIASES if c in {canon(x) for x in grp} for a in grp}


def map_pins(model_pins, sym_pins, kind=None):
    """model_pins: [name] in model node order; sym_pins: [(number, name)].
    -> {status: verified|verify|mismatch, method, map: {symbol pin number: model pin name}, order, notes}."""
    notes = []
    if sym_pins is None:
        return {"status": "verify", "method": "symbol pins unknown", "map": {}, "notes": ["symbol pins could not be read"]}
    snum = [n for n, _ in sym_pins]
    if kind in ("R", "C", "CP", "L") and len(model_pins) == 2 and len(set(snum)) == 2:
        nums = sorted(dict.fromkeys(snum), key=lambda x: (len(x), x))
        return {"status": "verified", "method": "2-pin passive, pin 1 -> node 1", "map": dict(zip(nums, model_pins)), "notes": notes}
    # 1) by name, exact canonical first, then aliases
    m, used = {}, set()
    for mp in model_pins:
        hits = [n for n, nm in sym_pins if canon(nm) == canon(mp) and n not in used]
        if len(hits) != 1:
            hits = [n for n, nm in sym_pins if _alias_sets(nm) & _alias_sets(mp) and n not in used] if not hits else hits
        if len(hits) >= 1 and len({canon(dict(sym_pins)[h]) for h in hits}) == 1:
            m[hits[0]] = mp
            used.add(hits[0])
            if len(hits) > 1:
                notes.append(f"model pin {mp}: symbol pins {', '.join(hits)} share the name; pin {hits[0]} used")
    if len(m) == len(model_pins):
        extra = [f"{n}:{nm}" for n, nm in sym_pins if n not in m]
        if extra:
            notes.append(f"symbol pins not in the model: {', '.join(extra[:10])}")
        return {"status": "verified", "method": "by pin name", "map": m, "notes": notes}
    # 2) diodes: KiCad/IPC convention pin 1 = cathode
    if kind in ("D", "LED") and len(model_pins) == 2 and len(set(snum)) == 2:
        k_first = [x for x in model_pins if canon(x) in ("K", "CATHODE")] and [x for x in model_pins if canon(x) in ("A", "ANODE")]
        if k_first:
            a = next(x for x in model_pins if canon(x) in ("A", "ANODE"))
            k = next(x for x in model_pins if canon(x) in ("K", "CATHODE"))
            nums = sorted(set(snum), key=lambda x: (len(x), x))
            return {"status": "verify", "method": "pin 1 = cathode convention (symbol pin names say nothing)",
                    "map": {nums[0]: k, nums[1]: a}, "notes": notes}
    # 3) by number: model nodes named 1..n
    if all(re.fullmatch(r"\d+", p) for p in model_pins) and set(model_pins) <= set(snum):
        return {"status": "verify", "method": "model node names are pin numbers", "map": {p: p for p in model_pins},
                "notes": notes + ["vendor node numbers usually follow the datasheet pinout: check"]}
    # 4) by position
    nums = list(dict.fromkeys(snum))
    if len(nums) == len(model_pins):
        order = sorted(nums, key=lambda x: (len(x), x))
        return {"status": "verify", "method": "by position (symbol pin order = model node order)", "map": dict(zip(order, model_pins)),
                "notes": notes + [f"names do not match: model {' '.join(model_pins)} / symbol {' '.join(nm for _, nm in sym_pins)}"]}
    return {"status": "mismatch", "method": "", "map": {}, "notes": notes + [
        f"model has {len(model_pins)} pins ({' '.join(model_pins)}), symbol has {len(nums)}: not attached"]}


def check_ibis_pins(comp, sym_pins, pads=None):
    """IBIS [Pin] list vs symbol pins (by number, then signal names) and the footprint's pad count."""
    notes = []
    ipins = {p["pin"]: p for p in comp["pins"]}
    if sym_pins is None:
        return {"status": "verify", "method": "symbol pins unknown", "map": {}, "notes": ["symbol pins could not be read"]}
    snum = dict(sym_pins)
    found = [n for n in snum if n in ipins]
    if not snum or len(found) < 0.5 * len(snum):
        return {"status": "mismatch", "method": "", "map": {}, "notes": [
            f"only {len(found)} of {len(snum)} symbol pins are in [Component] {comp['name']}'s [Pin] list: not attached"]}
    agree = [n for n in found if _alias_sets(snum[n]) & _alias_sets(ipins[n]["signal"])]
    missing = [n for n in snum if n not in ipins]
    extra = [p for p in ipins if p not in snum]
    if missing:
        notes.append(f"symbol pins not in IBIS: {', '.join(missing[:10])}")
    if extra:
        notes.append(f"IBIS pins not on the symbol: {', '.join(extra[:10])}")
    if pads is not None and pads != len(ipins):
        notes.append(f"footprint has {pads} pads, IBIS lists {len(ipins)} pins")
    ok = not missing and len(agree) >= 0.8 * len(found)
    notes.append(f"{len(agree)} of {len(found)} pin names agree")
    return {"status": "verified" if ok else "verify", "method": "IBIS pin numbers = symbol pin numbers",
            "map": {n: ipins[n]["signal"] for n in found}, "notes": notes}


def symbol_pins(item, plan_dir):
    f = (item.get("symbol") or {}).get("file")
    if not f:
        return None
    try:
        _, syms = AL.read_schlib(Path(plan_dir) / f)
        return [(r["num"], r["name"]) for r in syms[0]["records"] if r.get("RECORD") == "2" and not r.get("mode")]
    except Exception:
        return None


def pad_count(item, plan_dir):
    f = (item.get("footprint") or {}).get("file")
    if not f:
        return None
    try:
        return len({r["designator"] for r in AL.read_pcblib(Path(plan_dir) / f)[0]["records"] if r["type"] == "pad" and r["designator"]})
    except Exception:
        return None


# ---------------------------------------------------------------- built-in passives
def builtin(item, kind, rules=None):
    """-> model dict (kicad fields, altium link, optional generated subckt text) or None with a reason."""
    par = _par(item)
    v = value_of(par.get("Value"))
    if v is None:
        return None, f"Value {par.get('Value')!r} is not a {kind} value"
    sim = (rules or {}).get("sim") or {}
    esr = value_of(par.get("ESR") or sim.get("capacitor_esr")) if kind in ("C", "CP") else None
    esl = value_of(par.get("ESL") or sim.get("capacitor_esl")) if kind in ("C", "CP") else None
    dcr = value_of(par.get("DCR") or sim.get("inductor_dcr")) if kind == "L" else None
    elem = {"R": ("R", "r", "RESISTOR"), "C": ("C", "c", "CAPACITOR"), "CP": ("C", "c", "CAPACITOR"), "L": ("L", "l", "INDUCTOR")}[kind]
    m = {"source": "builtin", "format": "spice", "value": spice_num(v), "altium": {"name": elem[2], "type": "SIM", "file": None,
         "description": f"generic {elem[2].lower()}, value from the Value parameter"}}
    if esr or esl or dcr:
        name = "ecadlib_" + re.sub(r"[^A-Za-z0-9_]", "_", item["name"])
        body = [f"* {item['name']}: {elem[2].lower()} {spice_num(v)}" + (f" ESR {spice_num(esr)}" if esr else "")
                + (f" ESL {spice_num(esl)}" if esl else "") + (f" DCR {spice_num(dcr)}" if dcr else "") + " (generated by ecadlib)",
                f".subckt {name} 1 2"]
        if kind == "L":
            body += [f"L1 1 n1 {spice_num(v)}", f"R1 n1 2 {spice_num(dcr)}"]
        else:
            nodes = ["1"] + [f"n{i}" for i in range(1, (esr is not None) + (esl is not None) + 1)] + ["2"]
            parts = [("C", spice_num(v))] + ([("R", spice_num(esr))] if esr else []) + ([("L", spice_num(esl))] if esl else [])
            body += [f"{e}1 {nodes[i]} {nodes[i + 1]} {val}" for i, (e, val) in enumerate(parts)]
        body += [".ends", ""]
        m.update({"name": name, "model_type": "subckt", "pins": ["1", "2"], "text": "\n".join(body),
                  "kicad": {"Sim.Device": "SUBCKT", "Sim.Name": name}})
        m["altium"] = {"name": name, "type": "SIM", "file": f"{name}.ckt", "per_part": True,
                       "description": f"{elem[2].lower()} with parasitics"}
    else:
        m.update({"name": elem[0], "model_type": "ideal", "pins": ["1", "2"],
                  "kicad": {"Sim.Device": elem[0], "Sim.Params": f"{elem[1]}={spice_num(v)}"}})
    return m, None


# ---------------------------------------------------------------- user model folders
class ModelFolder:
    """Index of model files under the given folders (and inside .zip files there): name -> [(file, parsed)]."""

    def __init__(self, dirs):
        self.entries, self.errors = [], []
        for d in dirs or []:
            for f in sorted(Path(d).rglob("*")):
                if not f.is_file() or f.stat().st_size > MAX_FILE:
                    continue
                try:
                    if f.suffix.lower() == ".zip":
                        with zipfile.ZipFile(f) as z:
                            for zi in z.infolist():
                                if Path(zi.filename).suffix.lower() in MODEL_EXT and zi.file_size <= MAX_FILE:
                                    self._add(f"{f}!{zi.filename}", z.read(zi).decode("latin-1"))
                    elif f.suffix.lower() in MODEL_EXT:
                        self._add(str(f), f.read_bytes().decode("latin-1"))
                except (OSError, zipfile.BadZipFile) as e:
                    self.errors.append(f"{f}: {e}")

    def _add(self, where, text):
        if Path(where.split("!")[-1]).suffix.lower() == ".txt" and not re.search(r"^\s*(\.subckt|\.model|\[IBIS Ver\])", text, re.I | re.M):
            return
        p = parse_model(text, where.split("!")[-1])
        names = model_names(p) + [Path(where.split("!")[-1]).stem]
        self.entries.append({"file": where, "text": text, "parsed": p, "keys": {norm_mpn(n) for n in names if n}})

    def find(self, mpn):
        """Best entries for an MPN: exact name, then a model named after the MPN minus an ordering suffix, or the
        MPN with a suffix. -> [(score, entry, how)]."""
        nm, out = norm_mpn(mpn), []
        if len(nm) < 3:
            return out
        for e in self.entries:
            for k in e["keys"]:
                if k == nm:
                    out.append((0, e, "exact name"))
                elif len(k) >= 4 and nm.startswith(k) and len(nm) - len(k) <= 5:
                    out.append((1 + len(nm) - len(k), e, f"model {k} is the MPN minus an ordering suffix"))
                elif len(nm) >= 4 and k.startswith(nm) and len(k) - len(nm) <= 4:
                    out.append((2 + len(k) - len(nm), e, f"model {k} is the MPN plus a suffix"))
        best = {}
        for s, e, how in sorted(out, key=lambda x: x[0]):
            best.setdefault(e["file"], (s, e, how))
        return sorted(best.values(), key=lambda x: x[0])


# ---------------------------------------------------------------- network (robots-aware, cached)
class Net:
    def __init__(self, http, enabled=True):
        self.http, self.enabled, self.robots, self.hosts = http, enabled, {}, {}

    def allowed(self, url):
        u = urlparse(url)
        host = u.netloc
        if host not in self.robots:
            r = self.http.get(f"{u.scheme}://{host}/robots.txt")
            if r.error and not r.status:
                self.robots[host] = (None, r.error)
            elif r.ok:
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
                self.robots[host] = (rp, "")
            else:
                self.robots[host] = ("all", "")          # no robots.txt (404 ...): allowed
        rp, err = self.robots[host]
        if rp is None:
            self.hosts[host] = f"unreachable ({err})"
            return False, self.hosts[host]
        if rp != "all" and not rp.can_fetch(UA, url):
            self.hosts[host] = "robots.txt disallows"
            return False, "robots.txt disallows"
        return True, ""

    def get_bytes(self, url):
        """Bytes of url, or (None, why). Cached like httpcache (bodies kept as bytes for .zip archives)."""
        if not self.enabled:
            return None, "network off"
        ok, why = self.allowed(url)
        if not ok:
            return None, why
        host = urlparse(url).netloc
        if hasattr(self.http, "get_bytes"):           # test fakes
            data, err = self.http.get_bytes(url)
            self.hosts[host] = "ok" if data is not None else err
            return data, err
        d = Path(self.http.dir) / "sim_models"
        f = d / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".bin")
        if f.exists() and (self.http.offline or time.time() - f.stat().st_mtime < self.http.ttl):
            self.hosts[host] = "ok (cache)"
            return f.read_bytes(), ""
        if self.http.offline:
            return None, "offline: not in cache"
        wait = self.http.min_interval - (time.time() - self.http._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self.http._last[host] = time.time()
        try:
            r = self.http.s.get(url, timeout=self.http.timeout)
        except Exception as e:                      # requests errors: report, never raise
            self.hosts[host] = f"unreachable ({type(e).__name__})"
            return None, self.hosts[host]
        if not r.ok:
            self.hosts[host] = f"HTTP {r.status_code}"
            return None, f"HTTP {r.status_code}"
        if len(r.content) > MAX_FILE:
            return None, "too large"
        d.mkdir(parents=True, exist_ok=True)
        f.write_bytes(r.content)
        self.hosts[host] = "ok"
        return r.content, ""

    def get_text(self, url):
        if not self.enabled:
            return None, "network off"
        ok, why = self.allowed(url)
        if not ok:
            return None, why
        r = self.http.get(url)
        self.hosts[urlparse(url).netloc] = "ok" if r.ok else (r.error or f"HTTP {r.status}")
        return (r.text, "") if r.ok else (None, r.error or f"HTTP {r.status}")


def unpack(data, name):
    """Downloaded bytes -> [(file name, text)] of model files (a .zip is opened in memory, nothing is extracted)."""
    if data[:4] == b"PK\x03\x04":
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for zi in z.infolist():
                if Path(zi.filename).suffix.lower() in MODEL_EXT and zi.file_size <= MAX_FILE:
                    out.append((Path(zi.filename).name, z.read(zi).decode("latin-1")))
        return out
    return [(name, data.decode("latin-1"))]


def ibis_list_links(html, manufacturer):
    """IBIS Open Forum model list page -> vendor model-page links whose text names the manufacturer."""
    words = [w for w in re.findall(r"[a-z]{3,}", (manufacturer or "").lower()) if w not in ("inc", "ltd", "corp", "co", "the", "semiconductor", "semiconductors", "electronics", "technology")]
    out = []
    for href, text in re.findall(r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html or "", re.I | re.S):
        t = re.sub(r"<[^>]+>", "", text).lower()
        if words and any(w in t for w in words):
            out.append(urljoin(IBIS_LIST, href))
    return list(dict.fromkeys(out))


def nexar_docs(data):
    """supSearchMpn response -> [(collection, name, url)] of documents that look like simulation models."""
    out = []
    for r in ((data.get("data") or {}).get("supSearchMpn") or {}).get("results") or []:
        part = (r or {}).get("part") or {}
        for col in part.get("documentCollections") or []:
            for d in col.get("documents") or []:
                label = f"{col.get('name', '')} {d.get('name', '')}"
                if re.search(r"spice|ibis|simulation|pspice|ltspice|\bmodel", label, re.I) and not re.search(r"3d|step|cad", label, re.I):
                    out.append((col.get("name", ""), d.get("name", ""), d.get("url", "")))
    return out


def vendor_for(manufacturer):
    return next((v for v in VENDORS if manufacturer and re.search(v["match"], manufacturer, re.I)), None)


def base_mpn(mpn):
    """MPN minus a typical ordering/packaging suffix: TPS62133RGTR -> TPS62133, BSS138LT1G -> BSS138."""
    m = re.match(r"^([A-Z]{1,6}\d{2,}(?:[A-Z]\d+)?)", str(mpn or "").upper())
    return m.group(1) if m else str(mpn or "").upper()


# ---------------------------------------------------------------- one part
def choose(parsed, want, mpn):
    """Pick the model inside a parsed file: -> (name, model_type, pins) or (None, ...)."""
    nm = norm_mpn(mpn)
    if parsed["format"] == "ibis":
        comps = parsed["components"]
        c = next((c for c in comps if norm_mpn(c["name"]) == nm), None) or next(
            (c for c in comps if nm.startswith(norm_mpn(c["name"])) or norm_mpn(c["name"]).startswith(nm)), None) or (comps[0] if len(comps) == 1 else None)
        return (c["name"], "ibis", [p["pin"] for p in c["pins"]]) if c else (None, None, None)
    cands = [(s["name"], "subckt", s["pins"]) for s in parsed["subckts"]] + [(m["name"], f"model:{m['type']}", MODEL_PINS.get(m["type"])) for m in parsed["models"]]
    cands = [c for c in cands if c[2]]
    hit = next((c for c in cands if norm_mpn(c[0]) == nm), None) or next(
        (c for c in cands if len(norm_mpn(c[0])) >= 4 and (nm.startswith(norm_mpn(c[0])) or norm_mpn(c[0]).startswith(nm))), None)
    return hit or (cands[0] if len(cands) == 1 else (None, None, None))


KICAD_DEVICE = {"D": "D", "NPN": "NPN", "PNP": "PNP", "NMOS": "NMOS", "PMOS": "PMOS", "VDMOS": "NMOS", "NJF": "NJFET", "PJF": "PJFET"}


def kicad_fields(m):
    """Sim.* fields for a found model (map: symbol pin -> model pin)."""
    if m["source"] == "builtin" and m["model_type"] == "ideal":
        return dict(m["kicad"])
    if m["format"] == "ibis":
        return {}                                     # KiCad's IBIS device is one pin + one driver model: set per instance
    pins = " ".join(f"{k}={v}" for k, v in sorted(m["pin_map"]["map"].items(), key=lambda kv: (len(kv[0]), kv[0])))
    if m["model_type"] == "subckt":
        d = {"Sim.Device": "SUBCKT", "Sim.Name": m["name"]}
    else:
        t = m["model_type"].split(":")[1]
        d = {"Sim.Device": KICAD_DEVICE.get(t, "SPICE"), "Sim.Name": m["name"]}
    d["Sim.Library"] = m["file_name"]
    d["Sim.Pins"] = pins
    return d


def find_one(item, plan_dir, folder, net, rules=None, sym_pins=None):
    """-> sim_model dict for the plan item (never raises for a missing model)."""
    par = _par(item)
    mpn = par.get("Manufacturer Part Number") or ""
    mfr = par.get("Manufacturer") or ""
    kind, want = classify(item, sym_pins)
    rec = {"kind": kind, "wanted": want, "found": False, "tried": [], "pages": [], "notes": []}
    if kind is None:
        rec["tried"].append("no simulation model for this kind of part (connector, crystal ...)")
        return rec
    if want == "builtin":
        m, why = builtin(item, kind, rules)
        if not m:
            rec["tried"].append(f"built-in: {why}")
            return rec
        m["pin_map"] = map_pins(m["pins"], sym_pins, kind)
        rec.update(m, found=True)
        rec["tried"].append(f"built-in {kind} model, {m['value']}")
        return rec
    if not mpn:
        rec["tried"].append("no MPN: nothing to look up")
        return rec
    cands = []                                         # (source, file name, text, how)
    for s, e, how in folder.find(mpn) if folder else []:
        cands.append((f"folder:{e['file']}", Path(e["file"].split("!")[-1]).name, e["text"], how))
    if folder:
        rec["tried"].append(f"--models: {len(cands)} candidate file(s)")
    # Nexar documents
    if net.enabled and not cands:
        try:
            from .lifecycle import NexarSupply
            nx = NexarSupply(net.http)
            if nx.configured():
                tok = nx.token()
                r = net.http.post(nx.API_URL, json.dumps({"query": NEXAR_QUERY, "variables": {"q": mpn, "limit": 3}}),
                                  headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}) if tok else None
                docs = nexar_docs(r.json()) if r is not None and r.ok else []
                rec["tried"].append(f"nexar: {len(docs)} model document(s)" if r is not None and r.ok else
                                    f"nexar: {nx._token_err or (r.error if r is not None else 'no token')}")
                for col, name, url in docs:
                    data, err = net.get_bytes(url)
                    if data:
                        cands += [(f"nexar:{url}", fn, t, f"Octopart document '{name}'") for fn, t in unpack(data, Path(urlparse(url).path).name or name)]
                    else:
                        rec["pages"].append(url)
                        rec["tried"].append(f"nexar document {url}: {err}")
            else:
                rec["tried"].append("nexar: not configured (NEXAR_SUPPLY_TOKEN or NEXAR_CLIENT_ID/SECRET)")
        except Exception as e:
            rec["tried"].append(f"nexar: failed ({type(e).__name__}: {e})")
    v = vendor_for(mfr)
    fill = lambda t: t.format(mpn=quote(mpn), base=quote(base_mpn(mpn)))
    if v and net.enabled and not cands:
        for t in v["direct"]:
            url = fill(t)
            data, err = net.get_bytes(url)
            if data:
                cands += [(f"vendor:{url}", fn, txt, "vendor URL pattern (assumed)") for fn, txt in unpack(data, Path(urlparse(url).path).name)]
            rec["tried"].append(f"vendor {url}: {'fetched' if data else err}")
    if v:
        rec["pages"] += [fill(t) for t in v["pages"]]
    if want == "ibis":
        rec["pages"].append(IBIS_LIST)
    # validate candidates; the wanted format (IBIS for digital parts) first
    parsed = [(src, fname, text, how, parse_model(text, fname)) for src, fname, text, how in cands]
    parsed.sort(key=lambda c: (c[4]["format"] == "ibis") != (want == "ibis"))
    for src, fname, text, how, p in parsed:
        if p["errors"] and not model_names(p):
            rec["tried"].append(f"{src}: not a usable model ({'; '.join(p['errors'][:2])})")
            continue
        name, mtype, pins = choose(p, want, mpn)
        if not name:
            rec["tried"].append(f"{src}: no model named like {mpn} in {', '.join(model_names(p)[:5])}")
            continue
        if p["format"] == "ibis":
            comp = next(c for c in p["components"] if c["name"] == name)
            pm = check_ibis_pins(comp, sym_pins, pad_count(item, plan_dir))
        else:
            pm = map_pins(pins, sym_pins, kind)
        rec.update({"found": True, "source": src, "format": p["format"], "name": name, "model_type": mtype, "pins": pins,
                    "file_name": fname, "text": text, "match": how, "pin_map": pm,
                    "checks": {k: p[k] for k in ("errors", "warnings", "includes", "encrypted", "version") if p.get(k)}})
        if p.get("encrypted"):
            rec["notes"].append("encrypted model: only simulators with the vendor's key can run it (PSpice/LTspice)")
        if p.get("includes"):
            rec["notes"].append(f"model .include's other files: {', '.join(p['includes'])} (not followed)")
        if p["format"] == "spice" and want == "ibis":
            rec["notes"].append("SPICE model found for a digital part (IBIS wanted)")
        if pm["status"] == "mismatch" and len(parsed) > 1:
            rec["tried"].append(f"{src}: {name} pin mismatch, trying the next candidate")
            continue
        break
    if not rec["found"]:
        rec["tried"].append("no model found" + (": see `pages` for the manufacturer's model pages" if rec["pages"] else ""))
    return rec


# ---------------------------------------------------------------- Altium: SIM implementation records
def _blocks(d):
    out, j = [], 0
    while j < len(d):
        n = struct.unpack_from("<I", d, j)[0]
        out.append((n >> 24, d[j + 4:j + 4 + (n & 0xFFFFFF)]))
        j += 4 + (n & 0xFFFFFF)
    return out


def _txt(b):
    return b.rstrip(b"\0").decode("cp1252")


def _ole_tree(o, prefix=()):
    tree = {}
    for entry in o.listdir(streams=True, storages=False):
        node = tree
        for part in entry[:-1]:
            node = node.setdefault(part, {})
        node[entry[-1]] = o.openstream(entry).read()
    for entry in o.listdir(streams=False, storages=True):   # empty storages
        node = tree
        for part in entry:
            node = node.setdefault(part, {})
    return tree


def sim_link_records(link, il, start):
    """RECORD=45 (+46, 47 per pin, 48) for one SIM/SI link; il = index of the component's RECORD=44, start = the
    index this RECORD=45 gets (46 and 48 are owned by the 45, each 47 by the 46).
    link: {type 'SIM'|'SI', name, description, file, kind, map: {symbol pin: model pin index}, uid}."""
    kv = [("RECORD", 45), ("OwnerIndex", il), ("IndexInSheet", -1), ("Description", AL.clean(link.get("description", ""))),
          ("ModelName", AL.clean(link["name"])), ("ModelType", link["type"]), ("DatafileCount", 1)]
    if link.get("file"):
        kv.append(("ModelDatafile0", AL.clean(link["file"])))
    kv += [("ModelDatafileEntity0", AL.clean(link["name"])), ("ModelDatafileKind0", link.get("kind") or link["type"]),
           ("IsCurrent", "T"), ("UniqueID", link["uid"])]
    recs = [AL.pblock(AL.params(kv)), AL.pblock(f"|RECORD=46|OwnerIndex={start}")]
    pins = sorted(link.get("map", {}).items(), key=lambda kv: (len(kv[0]), kv[0]))
    trivial = all(str(k) == str(v) for k, v in pins)
    for k, v in pins:
        recs.append(AL.pblock(AL.params([("RECORD", 47), ("OwnerIndex", start + 1), ("DesIntf", AL.clean(str(k))), ("DesImpCount", 1),
                                         ("DesImp0", AL.clean(str(v)))] + ([("IsTrivial", "T")] if trivial else []))))
    recs.append(AL.pblock(f"|RECORD=48|OwnerIndex={start}"))
    return recs


def attach_altium(schlib, links):
    """Replace the SIM/SI implementations of every component in a .SchLib with `links` (list, see sim_link_records).
    The footprint link (ModelType=PCBLIB) and every other record are kept byte for byte, apart from OwnerIndex
    renumbering after the removed records. Returns the number of components changed."""
    import olefile
    with olefile.OleFileIO(str(schlib)) as o:
        tree = _ole_tree(o)
    hdr = AL.parse_params(_txt(_blocks(tree["FileHeader"])[0][1]))
    keymap = {}
    if "SectionKeys" in tree:
        sk = AL.parse_params(_txt(_blocks(tree["SectionKeys"])[0][1]))
        keymap = {sk[f"LIBREF{i}"]: sk[f"SECTIONKEY{i}"] for i in range(int(sk.get("KEYCOUNT", 0)))}
    changed, weight_delta = 0, 0
    for i in range(int(hdr.get("COMPCOUNT", 0))):
        key = keymap.get(hdr[f"LIBREF{i}"], AL.section_key(hdr[f"LIBREF{i}"]))
        blocks = _blocks(tree[key]["Data"])
        kv = lambda b: AL.parse_params(_txt(b[1])) if b[0] == 0 else {}
        il = next((k for k, b in enumerate(blocks) if kv(b).get("RECORD") == "44"), None)
        if il is None:
            continue
        head, tail = blocks[:il + 1], blocks[il + 1:]
        groups, cur = [], None                      # implementation groups after RECORD=44
        for b in tail:
            if kv(b).get("RECORD") == "45":
                cur = [b]
                groups.append(cur)
            elif cur is not None:
                cur.append(b)
            else:
                head.append(b)
        keep = [g for g in groups if kv(g[0]).get("MODELTYPE", "").upper() not in ("SIM", "SI")]
        out = list(head)
        for g in keep:                              # renumber: 45 -> 44's index, 46/48 -> 45's, 47 -> 46's
            i45 = len(out)
            for b in g:
                rec = kv(b).get("RECORD")
                own = {"45": il, "46": i45, "48": i45, "47": i45 + 1}.get(rec)
                t = _txt(b[1])
                if own is not None:
                    t = re.sub(r"\|OwnerIndex=\d+", f"|OwnerIndex={own}", t, count=1)
                out.append((0, AL.enc(t) + b"\0") if b[0] == 0 else b)
        for ln in links:
            out += [(0, r[4:]) for r in sim_link_records(ln, il, len(out))]
        weight_delta += len(out) - len(blocks)
        tree[key]["Data"] = b"".join(struct.pack("<I", len(b) | (flag << 24)) + b for flag, b in out)
        changed += 1
    if weight_delta and "WEIGHT" in hdr:
        t = _txt(_blocks(tree["FileHeader"])[0][1])
        t = re.sub(r"\|Weight=(\d+)", lambda m: f"|Weight={int(m.group(1)) + weight_delta}", t, count=1)
        tree["FileHeader"] = AL.pblock(t)
    from . import cfb
    Path(schlib).write_bytes(cfb.to_bytes(tree))
    return changed


def read_sim_links(schlib):
    """[(RECORD=45 params, [RECORD=47 params])] of the SIM/SI links in a .SchLib (first component)."""
    _, syms = AL.read_schlib(schlib)
    out, cur = [], None
    for r in syms[0]["records"]:
        if r.get("RECORD") == "45":
            cur = (r, []) if r.get("MODELTYPE", "").upper() in ("SIM", "SI") else None
            if cur:
                out.append(cur)
        elif r.get("RECORD") == "47" and cur:
            cur[1].append(r)
    return out


# ---------------------------------------------------------------- driver
def _safe(s):
    return re.sub(r"[^A-Za-z0-9_.+-]", "_", str(s))[:80] or "model"


def run(plan, models=(), attach=False, http=None, offline=False, cache="out/http_cache", net=True, rules=None, log=print):
    """Find, check and (attach=True) attach simulation models for every item of a librarian plan.json.
    Returns the report dict (also sim_models.json / .md next to the plan)."""
    plan = Path(plan)
    plan_dir = plan.parent
    items = json.loads(plan.read_text())
    http = http or CachedHTTP(cache, offline=offline, timeout=60)
    nt = Net(http, enabled=net)
    folder = ModelFolder(models) if models else None
    sim_dir = plan_dir / "models" / "Sim"
    rep = {"plan": str(plan), "attach": attach, "parts": [], "hosts": {}, "folder_errors": folder.errors if folder else [],
           "altium365": "DesReleaseComponentInput has symbolFiles/footprintFiles only (no simulation-model input; the "
                        "SIMULATION_MODEL content kind has no release mutation): model files are not pushed"}
    ibis_html = None
    users = {}                                     # SchLib file -> plan items using it
    for it in items:
        f = (it.get("symbol") or {}).get("file")
        if f:
            users.setdefault(f, []).append(it["name"])
    links = {}                                     # SchLib file -> [link]
    for it in items:
        old = it.get("sim_model") or {}
        it["verify"] = [v for v in it.get("verify", []) if not v.startswith("sim model")]
        if old.get("pinned"):                       # a person chose this model: keep it
            rep["parts"].append({"part": it["name"], "status": "kept (pinned)", **{k: old.get(k) for k in ("name", "source", "file")}})
            continue
        sym_pins = symbol_pins(it, plan_dir)
        try:
            rec = find_one(it, plan_dir, folder, nt, rules, sym_pins)
        except Exception as e:                     # one bad part must not stop the run
            rec = {"found": False, "tried": [f"failed: {type(e).__name__}: {e}"], "pages": [], "notes": []}
        if rec.get("wanted") == "ibis" and not rec["found"] and nt.enabled:
            if ibis_html is None:
                ibis_html, _ = nt.get_text(IBIS_LIST)
                ibis_html = ibis_html or ""
            rec["pages"] = list(dict.fromkeys(ibis_list_links(ibis_html, _par(it).get("Manufacturer")) + rec["pages"]))
        text = rec.pop("text", None)
        if rec.get("found") and text:
            sim_dir.mkdir(parents=True, exist_ok=True)
            fn = _safe(rec.get("file_name") or f"{rec['name']}.lib")
            if rec["source"] == "builtin":
                fn = f"{_safe(rec['name'])}.lib"
            (sim_dir / fn).write_text(text)
            rec["file"] = os.path.relpath(sim_dir / fn, plan_dir)
            rec["file_name"] = fn
        if rec.get("found"):
            rec["kicad"] = kicad_fields(rec) if rec["pin_map"]["status"] != "mismatch" else {}
            if rec["format"] == "ibis":
                rec["notes"].append("KiCad: IBIS devices are per pin (Sim.Device IBIS, Sim.Ibis.Pin/Model); set them on the "
                                    "schematic instance, the file is in the library's .sim folder")
        rec["attached"] = {}
        if attach and rec.get("found") and rec["pin_map"]["status"] != "mismatch":
            sf = (it.get("symbol") or {}).get("file")
            per_part = rec["source"] != "builtin" or (rec.get("altium") or {}).get("per_part")
            if sf and per_part and len(users.get(sf, [])) > 1:
                rec["notes"].append(f"Altium: symbol {sf} is shared with {', '.join(n for n in users[sf] if n != it['name'])}: "
                                    "a part-specific model is not added to it")
            elif sf:
                link = _altium_link(rec, it)
                prev = links.setdefault(sf, [])
                if not any(p["name"] == link["name"] for p in prev):
                    prev.append(link)
                rec["attached"]["altium"] = {"schlib": sf, "model_name": link["name"], "type": link["type"],
                                             "file": link.get("file"), "record_format": "assumed (see sim_models.md)"}
            if rec["kicad"] or rec["format"] == "ibis":
                rec["attached"]["kicad"] = True
            if rec["pin_map"]["status"] == "verify" and rec["attached"]:
                it["verify"].append(f"sim model {rec['name']}: pin map not verified ({rec['pin_map']['method']})")
        it["sim_model"] = rec
        rep["parts"].append({"part": it["name"], "kind": rec.get("kind"), "found": rec.get("found"), "source": rec.get("source"),
                             "format": rec.get("format"), "name": rec.get("name"), "file": rec.get("file"),
                             "pins": (rec.get("pin_map") or {}).get("status"), "pin_method": (rec.get("pin_map") or {}).get("method"),
                             "attached": sorted(rec["attached"]), "notes": rec.get("notes", []) + (rec.get("pin_map") or {}).get("notes", []),
                             "pages": rec.get("pages", []), "tried": rec.get("tried", [])})
    # Altium: copy model files beside each SchLib and write the links
    for sf, lks in links.items():
        path = plan_dir / sf
        for ln in lks:
            if ln.get("src"):
                shutil.copyfile(plan_dir / ln["src"], path.parent / ln["file"])
        try:
            attach_altium(path, lks)
        except Exception as e:
            rep.setdefault("errors", []).append(f"{sf}: {type(e).__name__}: {e}")
    rep["hosts"] = dict(sorted(nt.hosts.items()))
    if attach:
        from . import release_check as RC
        rows = RC.apply(items, plan_dir)
        RC.write_report(rows, plan_dir / "release_check", plan)
    plan.write_text(json.dumps(items, indent=2))
    (plan_dir / "sim_models.json").write_text(json.dumps(rep, indent=2) + "\n")
    write_md(rep, plan_dir / "sim_models.md")
    return rep


def _altium_link(rec, it):
    uid = AL.unique_id(f"{it['name']}/sim/{rec.get('name')}")
    if rec["source"] == "builtin":
        a = rec["altium"]
        link = {"type": "SIM", "name": a["name"], "description": a["description"], "uid": uid, "map": {}}
        if a.get("file"):
            link.update(file=a["file"], src=rec["file"])
        link["map"] = {k: str(i + 1) for i, k in enumerate(sorted(rec["pin_map"]["map"], key=lambda x: (len(x), x)))}
        return link
    if rec["format"] == "ibis":
        return {"type": "SI", "name": rec["name"], "description": f"IBIS {rec['name']} ({rec['file_name']})", "uid": uid,
                "file": rec["file_name"], "src": rec["file"], "map": {}}
    ext = ".ckt" if rec["model_type"] == "subckt" else ".mdl"
    order = {p: str(i + 1) for i, p in enumerate(rec["pins"])}            # model pin -> node index (1-based)
    return {"type": "SIM", "name": rec["name"], "description": f"{rec['model_type']} {rec['name']} from {rec['source'].split(':')[0]}",
            "uid": uid, "file": f"{_safe(rec['name'])}{ext}", "src": rec["file"],
            "map": {k: order[v] for k, v in rec["pin_map"]["map"].items() if v in order}}


def kicad_export_fields(item, plan_dir, sim_out, rel_dir, notes):
    """For kicad_export: [(Sim.* field, value)] of an attached model, copying its file into sim_out."""
    rec = item.get("sim_model") or {}
    if not rec.get("found") or not (rec.get("attached") or {}).get("kicad"):
        return []
    fields = dict(rec.get("kicad") or {})
    if rec.get("file"):
        src = Path(plan_dir) / rec["file"]
        if src.exists():
            Path(sim_out).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, Path(sim_out) / Path(rec["file"]).name)
            if "Sim.Library" in fields:
                fields["Sim.Library"] = f"{rel_dir}/{Path(rec['file']).name}"
        else:
            notes.append(f"sim model file {rec['file']} is missing")
            return []
    if rec.get("pin_map", {}).get("status") == "verify":
        notes.append(f"sim model {rec.get('name')}: pin map to verify ({rec['pin_map']['method']})")
    order = ("Sim.Device", "Sim.Type", "Sim.Library", "Sim.Name", "Sim.Pins", "Sim.Params")
    return [(k, fields[k]) for k in order if fields.get(k)]


def write_md(rep, path):
    found = [p for p in rep["parts"] if p.get("found")]
    md = [f"# Simulation models - `{rep['plan']}`", "",
          f"{len(rep['parts'])} parts: {len(found)} with a model "
          f"({sum(p.get('pins') == 'verified' for p in found)} pin maps verified, {sum(p.get('pins') == 'verify' for p in found)} to verify, "
          f"{sum(p.get('pins') == 'mismatch' for p in found)} mismatched).", "",
          "| Part | Kind | Model | Source | Pins | Attached |", "|---|---|---|---|---|---|"]
    for p in rep["parts"]:
        md.append(f"| {p['part']} | {p.get('kind') or '-'} | {p.get('name') or '-'} | {(p.get('source') or '-').split('!')[0]} | "
                  f"{p.get('pins') or '-'} | {', '.join(p.get('attached') or []) or '-'} |")
    md += ["", "## Notes and where to look", ""]
    for p in rep["parts"]:
        lines = (p.get("notes") or []) + ([f"pin map: {p['pin_method']}"] if p.get("pin_method") else []) + \
                ([f"model pages: {' '.join(p['pages'])}"] if p.get("pages") and not p.get("found") else []) + \
                ([f"tried: {'; '.join(p['tried'])}"] if not p.get("found") else [])
        if lines:
            md.append(f"- **{p['part']}**: " + "; ".join(lines))
    md += ["", "## Hosts", ""] + [f"- {h}: {s}" for h, s in rep["hosts"].items()] + ["", f"Altium 365: {rep['altium365']}.",
           "", "Altium SIM link format: RECORD=45 ModelType=SIM/SI with ModelName, ModelDatafile0 (the model file beside "
           "the .SchLib), ModelDatafileEntity0/Kind0, RECORD=47 DesIntf (symbol pin) -> DesImp0 (model node index). "
           "Record types from AltiumSharp; no Altium-saved SIM link was available to compare, so open one library in "
           "Altium Designer and check the model before relying on it."]
    Path(path).write_text("\n".join(md) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find, check and attach SPICE / IBIS simulation models for a librarian plan")
    ap.add_argument("plan", help="librarian plan.json")
    ap.add_argument("--models", action="append", default=[], metavar="DIR", help="your folder of vendor model files (repeatable)")
    ap.add_argument("--attach", action="store_true", help="write the models into the .SchLib files and the plan's KiCad fields")
    ap.add_argument("--offline", action="store_true", help="use the HTTP cache only")
    ap.add_argument("--no-net", action="store_true", help="no network at all (built-in passives and --models only)")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--rules", help="ecadlib.toml with a [sim] section (capacitor_esr, capacitor_esl, inductor_dcr)")
    a = ap.parse_args(argv)
    rules = {}
    if RU is not None:
        try:
            rules = RU.load(a.rules) if a.rules else RU.load(start=str(Path(a.plan).parent))
        except Exception as e:
            print(f"rules: {e}", file=sys.stderr)
    elif a.rules:
        rules = json.loads(Path(a.rules).read_text()) if a.rules.endswith(".json") else {}
    rep = run(a.plan, a.models, a.attach, offline=a.offline, cache=a.cache, net=not a.no_net, rules=rules)
    for p in rep["parts"]:
        print(f"  {p['part']:<28} {p.get('kind') or '-':<8} {(p.get('name') or '-'):<24} {p.get('pins') or '-':<9} "
              f"{','.join(p.get('attached') or []) or '-'}")
    for h, s in rep["hosts"].items():
        print(f"  host {h}: {s}")
    for e in rep.get("errors", []):
        print(f"  ERROR {e}")
    print(f"{sum(bool(p.get('found')) for p in rep['parts'])}/{len(rep['parts'])} parts with a model -> "
          f"{Path(a.plan).parent / 'sim_models.md'}")
    return 1 if rep.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
