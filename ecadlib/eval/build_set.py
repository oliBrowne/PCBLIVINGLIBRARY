"""Build the accuracy test set from a checkout of KiCad's official libraries (ground truth).

    ecadlib eval build-set --symbols kicad-symbols --footprints kicad-footprints [-o ecadlib/eval/sets/kicad-v1.json]

Cases come from three places, each recorded in the case's `from`:
  kicad-symbol     a symbol in kicad-symbols whose name is a real MPN (no KiCad `x` wildcards, no `_` variants), with a
                   fixed Footprint property and a Datasheet link: MPN = symbol name, footprint = that property, pins =
                   the symbol's pin numbers and names, manufacturer = the datasheet's domain (a fixed table; distributor
                   and archive links give none).
  kicad-footprint  a connector footprint whose name carries the maker's part number (JST B4B-XH-A, Molex 53398-0471,
                   HRO TYPE-C-31-M-12 ...): footprint = that file, datasheet = the link in its descr, pins = its
                   numbered pads (a generic connector symbol: numbers only, no names).
  curated          chip passives by the makers' ordering codes (Yageo RC0603FR-..., Murata GRM188..., Samsung CL10...,
                   KEMET C0603C..., Panasonic ERJ-3E...): the size code fixes the KiCad chip footprint. Series datasheet
                   links are given only where the maker publishes one series PDF (they were not fetched from here).
Selection is deterministic (sorted, then evenly spread over each family with at most PER_FOOTPRINT cases per
footprint) so the same checkout gives the same set. The set keeps only numbers from KiCad's files (pad centres,
sizes, drills, courtyard box; pin numbers and names): facts, not the drawings. KiCad libraries: CC-BY-SA 4.0 with
the KiCad libraries exception, https://gitlab.com/kicad/libraries."""
import argparse, datetime, json, re, subprocess, sys
from pathlib import Path

from .. import footprint_bench as FB, kicad_import as K, sexpr

# family -> how many cases to keep; classify() maps a KiCad footprint to one of these
QUOTA = {"chip": 36, "sot": 40, "soic": 30, "tssop": 30, "qfn": 40, "qfp": 25, "bga": 25, "diode": 25, "power": 15,
         "tht": 30, "connector": 35}
PER_FOOTPRINT = 2
MAKERS = {"st.com": "STMicroelectronics", "ti.com": "Texas Instruments", "microchip.com": "Microchip", "analog.com": "Analog Devices",
          "nxp.com": "NXP", "onsemi.com": "onsemi", "vishay.com": "Vishay", "infineon.com": "Infineon", "diodes.com": "Diodes Inc.",
          "nexperia.com": "Nexperia", "maximintegrated.com": "Maxim Integrated", "allegromicro.com": "Allegro MicroSystems",
          "littelfuse.com": "Littelfuse", "power.com": "Power Integrations", "powerint.com": "Power Integrations",
          "silabs.com": "Silicon Labs", "murata.com": "Murata", "wolfspeed.com": "Wolfspeed", "skyworksinc.com": "Skyworks",
          "semicon-storage.com": "Toshiba", "toshiba.com": "Toshiba", "rohm.com": "ROHM", "broadcom.com": "Broadcom",
          "kemet.com": "KEMET", "diotec.com": "Diotec", "cypress.com": "Cypress", "renesas.com": "Renesas", "akm.com": "AKM",
          "bourns.com": "Bourns", "issi.com": "ISSI", "wch-ic.com": "WCH", "microsemi.com": "Microsemi", "osram-os.com": "ams OSRAM",
          "espressif.com": "Espressif", "irf.com": "Infineon (IR)", "latticesemi.com": "Lattice", "ftdichip.com": "FTDI",
          "ams.com": "ams OSRAM", "sitime.com": "SiTime", "xilinx.com": "AMD Xilinx", "altera.com": "Intel (Altera)",
          "intersil.com": "Renesas (Intersil)", "nordicsemi.com": "Nordic Semiconductor", "linear.com": "Analog Devices",
          "fairchildsemi.com": "onsemi (Fairchild)", "ixys.com": "IXYS", "mps.com": "Monolithic Power Systems",
          "monolithicpower.com": "Monolithic Power Systems", "richtek.com": "Richtek", "semtech.com": "Semtech",
          "torexsemi.com": "Torex", "ablic.com": "ABLIC", "sii-ic.com": "ABLIC", "jst-mfg.com": "JST", "molex.com": "Molex",
          "hirose.com": "Hirose", "we-online.com": "Wurth Elektronik", "te.com": "TE Connectivity", "kingbright.com": "Kingbright",
          "kingbrightusa.com": "Kingbright", "liteon.com": "Lite-On", "panasonic.com": "Panasonic", "lcsc.com": None,
          "krhro.com": "HRO", "samtec.com": "Samtec", "amphenol.com": "Amphenol", "cui.com": "CUI", "sameskydevices.com": "Same Sky",
          "melexis.com": "Melexis", "zilog.com": "Zilog", "atmel.com": "Microchip (Atmel)",
          "national.com": "Texas Instruments (National)", "awinic.com": "Awinic", "gigadevice.com": "GigaDevice",
          "winbond.com": "Winbond", "neutrik.com": "Neutrik", "cuidevices.com": "CUI Devices",
          "switchcraft.com": "Switchcraft", "lumberg.com": "Lumberg", "amphenolrf.com": "Amphenol RF", "kycon.com": "Kycon", "macronix.com": "Macronix", "cree.com": "Wolfspeed (Cree)", "ween-semi.com": "WeEn"}
MPN_RE = re.compile(r"[A-Z0-9][A-Z0-9.+/-]{3,}")
WILDCARD = re.compile(r"(?<![0-9])x|x(?![0-9])")
CHIP_RE = re.compile(r"(R|C|L|LED)_(\d{4})_(\d{4})Metric")
CONNECTOR_MPN = [   # footprint-name pattern -> (manufacturer, regex group with the MPN)
    (re.compile(r"JST_[A-Z]+_([A-Z0-9]+-[A-Z0-9-]+?)_\d+x\d+"), "JST"),
    (re.compile(r"Molex_[A-Za-z0-9.-]+?_(\d{5,6}-\d{3,4})_\d+x\d+"), "Molex"),
    (re.compile(r"Hirose_[A-Za-z0-9.-]+?_(DF\d+[A-Z0-9-]+?)_\d+x\d+"), "Hirose"),
    (re.compile(r"USB_C_Receptacle_HRO_(TYPE-C-\d+-[A-Z0-9-]+)$"), "HRO"),
    (re.compile(r"USB_C_Receptacle_GCT_(USB\d{4}[A-Z0-9-]*)"), "GCT"),
    (re.compile(r"Wuerth_[A-Za-z0-9-]+?_(\d{9,12})_"), "Wurth Elektronik"),
    (re.compile(r"Samtec_([A-Z]{3,4}-1\d\d-\d\d-[A-Z0-9-]+?)_\d+x\d+"), "Samtec"),
]
CONNECTOR_LIBS = ("Connector_JST", "Connector_Molex", "Connector_Hirose", "Connector_USB", "Connector_Wuerth", "Connector_Samtec")

# chip passives by ordering code; (MPN, manufacturer, KiCad footprint, series datasheet or "")
_Y = "https://www.yageo.com/upload/media/product/productsearch/datasheet/rchip/PYu-RC_Group_51_RoHS_L_12.pdf"
_P = "https://industrial.panasonic.com/cdbs/www-data/pdf/RDA0000/AOA0000C304.pdf"
_K = "https://content.kemet.com/datasheets/KEM_C1002_X7R_SMD.pdf"
CURATED = [
    ("RC0201FR-0710KL", "Yageo", "Resistor_SMD:R_0201_0603Metric", _Y), ("RC0402FR-0710KL", "Yageo", "Resistor_SMD:R_0402_1005Metric", _Y),
    ("RC0603FR-0710KL", "Yageo", "Resistor_SMD:R_0603_1608Metric", _Y), ("RC0805FR-0710KL", "Yageo", "Resistor_SMD:R_0805_2012Metric", _Y),
    ("RC1206FR-0710KL", "Yageo", "Resistor_SMD:R_1206_3216Metric", _Y), ("RC1210FR-0710KL", "Yageo", "Resistor_SMD:R_1210_3225Metric", _Y),
    ("RC2010FK-0710KL", "Yageo", "Resistor_SMD:R_2010_5025Metric", _Y), ("RC2512FK-0710RL", "Yageo", "Resistor_SMD:R_2512_6332Metric", _Y),
    ("ERJ-1GNF1002C", "Panasonic", "Resistor_SMD:R_0201_0603Metric", _P), ("ERJ-2RKF1002X", "Panasonic", "Resistor_SMD:R_0402_1005Metric", _P),
    ("ERJ-3EKF1002V", "Panasonic", "Resistor_SMD:R_0603_1608Metric", _P), ("ERJ-6ENF1002V", "Panasonic", "Resistor_SMD:R_0805_2012Metric", _P),
    ("ERJ-8ENF1002V", "Panasonic", "Resistor_SMD:R_1206_3216Metric", _P), ("ERJ-14NF1002U", "Panasonic", "Resistor_SMD:R_1210_3225Metric", _P),
    ("GRM033R61A104KE15D", "Murata", "Capacitor_SMD:C_0201_0603Metric", ""), ("GRM155R71C104KA88D", "Murata", "Capacitor_SMD:C_0402_1005Metric", ""),
    ("GRM188R71H104KA93D", "Murata", "Capacitor_SMD:C_0603_1608Metric", ""), ("GRM21BR71H104KA01L", "Murata", "Capacitor_SMD:C_0805_2012Metric", ""),
    ("GRM31CR71H475KA12L", "Murata", "Capacitor_SMD:C_1206_3216Metric", ""), ("GRM32ER71H106KA12L", "Murata", "Capacitor_SMD:C_1210_3225Metric", ""),
    ("CL03A104KP3NNNC", "Samsung", "Capacitor_SMD:C_0201_0603Metric", ""), ("CL05B104KO5NNNC", "Samsung", "Capacitor_SMD:C_0402_1005Metric", ""),
    ("CL10B104KB8NNNC", "Samsung", "Capacitor_SMD:C_0603_1608Metric", ""), ("CL21B104KBCNNNC", "Samsung", "Capacitor_SMD:C_0805_2012Metric", ""),
    ("CL31A106KBHNNNE", "Samsung", "Capacitor_SMD:C_1206_3216Metric", ""), ("C0402C104K4RACTU", "KEMET", "Capacitor_SMD:C_0402_1005Metric", _K),
    ("C0603C104K5RACTU", "KEMET", "Capacitor_SMD:C_0603_1608Metric", _K), ("C0805C104K5RACTU", "KEMET", "Capacitor_SMD:C_0805_2012Metric", _K),
    ("C1206C104K5RACTU", "KEMET", "Capacitor_SMD:C_1206_3216Metric", _K), ("C1812C104K5RACTU", "KEMET", "Capacitor_SMD:C_1812_4532Metric", _K),
    ("LQG15HS10NJ02D", "Murata", "Inductor_SMD:L_0402_1005Metric", ""), ("LQG18HN10NJ00D", "Murata", "Inductor_SMD:L_0603_1608Metric", ""),
    ("LTST-C191KRKT", "Lite-On", "LED_SMD:LED_0603_1608Metric", ""), ("LTST-C171KRKT", "Lite-On", "LED_SMD:LED_0805_2012Metric", ""),
    ("LTST-C150KRKT", "Lite-On", "LED_SMD:LED_1206_3216Metric", ""), ("ERJ-2GE0R00X", "Panasonic", "Resistor_SMD:R_0402_1005Metric", _P),
]


def classify(fp):
    """'Lib:Name' -> family or None."""
    lib, name = fp.split(":", 1)
    n = name.upper()
    if CHIP_RE.fullmatch(name.split("_Pad")[0].split("_Hand")[0]) or CHIP_RE.match(name):
        return "chip"
    if lib.startswith("Connector") or lib in ("Connector_Audio", "Connector_RJ"):
        return "connector"
    if lib == "Package_BGA" or lib == "Package_CSP" or n.startswith(("BGA", "WLCSP", "LFBGA", "UFBGA", "TFBGA")):
        return "bga"
    if lib in ("Package_DFN_QFN", "Package_SON") or re.match(r"(V|W|U|X|T)?(QFN|DFN|SON)", n):
        return "qfn"
    if lib == "Package_QFP":
        return "qfp"
    if lib == "Package_SO":
        if n.startswith(("SOIC", "SO-", "SOP-")) and "SSOP" not in n:
            return "soic"
        if re.match(r"(H?TSSOP|MSOP|SSOP|QSOP|VSSOP|TSOP|HVSSOP|VSOP|TVSOP)", n):
            return "tssop"
        return None
    if lib == "Package_TO_SOT_SMD":
        if re.match(r"(SOT|SC-|TSOT|SOT-?\d)", n):
            return "sot"
        if re.match(r"(TO-252|TO-263|TO-268|D2?PAK|DPAK|TO-277|POWERDI|PQFN|LFPAK|TDSON|PowerPAK)", n, re.I):
            return "power"
        return None
    if lib == "Diode_SMD" and re.match(r"D_(SOD|SMA|SMB|SMC|MELF|MINIMELF|MICROMELF|SOD-?\d|PowerDI)", n):
        return "diode"
    if lib in ("Package_DIP", "Package_TO_SOT_THT", "Diode_THT"):
        return "tht"
    return None


def short_package(fp):
    """What a BOM's Package column would say: the footprint name up to its first '_', maker prefixes skipped
    (D_SOD-123 -> SOD-123, R_0603_1608Metric -> 0603, Maxim_TDFN-12-1EP_... -> TDFN-12-1EP). Connectors: none (a
    BOM names them by MPN only)."""
    lib, name = fp.split(":", 1)
    m = CHIP_RE.match(name)
    if m:
        return m.group(2)
    if lib.startswith("Connector"):
        return ""
    toks = re.sub(r"^(D|LED)_", "", name).split("_")
    return next((t for t in toks if re.search(r"\d", t)), toks[-1])


def maker(url):
    m = re.match(r"https?://([^/:]+)", url or "")
    if not m:
        return None
    parts = m.group(1).lower().split(".")
    for k in range(len(parts) - 1):
        d = ".".join(parts[k:])
        if d in MAKERS:
            return MAKERS[d]
    return None


def pins_of(chain):
    """[(num, name)] of a KiCad symbol (extends chain), one per pin number, names as drawn ('~' = none)."""
    out = {}
    for s in reversed(chain):
        for sub in sexpr.find(s, "symbol"):
            for p in sexpr.find(sub, "pin"):
                num = (sexpr.first(p, "number") or ["", ""])[1]
                name = (sexpr.first(p, "name") or ["", ""])[1]
                out.setdefault(str(num), str(name))
    return sorted(out.items(), key=lambda kv: _natural(kv[0]))


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def _git_head(d):
    try:
        return subprocess.run(["git", "-C", str(d), "log", "-1", "--format=%H %cs"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def symbol_candidates(sym_dir):
    for d in sorted(Path(sym_dir).glob("*.kicad_symdir")):
        for f in sorted(d.glob("*.kicad_sym")):
            for s in sexpr.find(sexpr.parse(f.read_text(encoding="utf-8")), "symbol"):
                name = s[1]
                if not MPN_RE.fullmatch(name) or not re.search(r"\d", name) or WILDCARD.search(name):
                    continue
                try:
                    chain, _ = K.load_symbol(f"{f}:{name}", None)
                except (FileNotFoundError, ValueError):
                    continue
                props = K._props(chain)
                fp, ds = props.get("Footprint", ""), props.get("Datasheet", "")
                if ":" not in fp or not ds.startswith("http"):
                    continue
                yield {"mpn": name, "symbol": f"{d.name[:-len('.kicad_symdir')]}:{name}", "footprint": fp, "datasheet": ds,
                       "description": props.get("Description", ""), "pins": pins_of(chain)}


def connector_candidates(fp_dir):
    for lib in CONNECTOR_LIBS:
        for f in sorted((Path(fp_dir) / f"{lib}.pretty").glob("*.kicad_mod")):
            name = f.stem
            for rx, mk in CONNECTOR_MPN:
                m = rx.search(name)
                if m:
                    text = f.read_text(encoding="utf-8")
                    descr = (sexpr.first(sexpr.parse(text), "descr") or ["", ""])[1]
                    url = re.search(r"https?://[^\s,)\"]+", descr)
                    nums = sorted({p[0] for p in FB.numbers(text)["pads"] if re.fullmatch(r"\d+", p[0] or "")}, key=int)
                    yield {"mpn": m.group(1), "manufacturer": mk, "footprint": f"{lib}:{name}",
                           "datasheet": url.group(0) if url else "", "description": descr[:200],
                           "pins": [(n, "") for n in nums]}
                    break


def spread(items, n):
    """n items evenly spread over a sorted list (deterministic)."""
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def build(sym_dir, fp_dir, quota=None, per_footprint=PER_FOOTPRINT, log=print):
    quota = dict(QUOTA, **(quota or {}))
    refs, cases, fam_pool = {}, [], {}

    def ref(fp):
        if fp not in refs:
            lib, name = fp.split(":", 1)
            f = Path(fp_dir) / f"{lib}.pretty" / f"{name}.kicad_mod"
            refs[fp] = FB.numbers(f.read_text(encoding="utf-8")) if f.exists() else None
        return refs[fp]

    for c in symbol_candidates(sym_dir):
        fam = classify(c["footprint"])
        if fam and fam != "chip":
            c.update(manufacturer=maker(c["datasheet"]) or "", **{"from": {"kind": "kicad-symbol", "symbol": c["symbol"]}})
            fam_pool.setdefault(fam, []).append(c)
    for c in connector_candidates(fp_dir):
        c["from"] = {"kind": "kicad-footprint", "footprint": c["footprint"]}
        fam_pool.setdefault("connector", []).append(c)
    for mpn, mk, fp, ds in CURATED:
        kind = CHIP_RE.match(fp.split(":")[1]).group(1)
        fam_pool.setdefault("chip", []).append({"mpn": mpn, "manufacturer": mk, "footprint": fp, "datasheet": ds,
                                                "description": {"R": "chip resistor", "C": "chip capacitor", "L": "chip inductor",
                                                                "LED": "chip LED"}[kind],
                                                "pins": [("1", "~"), ("2", "~")] if kind != "LED" else [("1", "K"), ("2", "A")],
                                                "from": {"kind": "curated", "note": "size code in the ordering code fixes the chip footprint"
                                                         + ("; series datasheet link not fetched here" if ds else "")}})
    for fam, n in quota.items():
        pool, seen_mpn = [], set()
        per = {}
        for c in sorted(fam_pool.get(fam, []), key=lambda c: (c["footprint"], c["mpn"])):
            if c["mpn"] in seen_mpn or (fam != "chip" and per.get(c["footprint"], 0) >= per_footprint) or not ref(c["footprint"]) \
                    or not ref(c["footprint"])["pads"]:
                continue
            if fam != "connector" and not c["manufacturer"] and fam != "chip":
                continue                                    # a distributor / archive link: no maker to put in the BOM
            per[c["footprint"]] = per.get(c["footprint"], 0) + 1
            seen_mpn.add(c["mpn"])
            pool.append(c)
        chosen = spread(pool, n)
        log(f"  {fam:<10} {len(chosen):>3} of {len(pool)} candidates")
        for c in chosen:
            cases.append({"id": f"{fam}-{re.sub(r'[^A-Za-z0-9.-]', '_', c['mpn'])}", "family": fam, "mpn": c["mpn"],
                          "manufacturer": c["manufacturer"], "package": short_package(c["footprint"]),
                          "footprint": c["footprint"], "datasheet": c["datasheet"], "description": c["description"][:200],
                          "pins": [list(p) for p in c["pins"]], "from": c["from"]})
    used = {c["footprint"] for c in cases}
    return cases, {k: v for k, v in refs.items() if k in used}


def write_set(path, cases, refs, sym_dir, fp_dir, name):
    meta = {"name": name, "version": 1, "built": datetime.date.today().isoformat(),
            "kicad_symbols": _git_head(sym_dir), "kicad_footprints": _git_head(fp_dir),
            "license": "Numbers only (pad centres, sizes, drills, courtyard box; pin numbers and names) from KiCad's official "
                       "libraries, https://gitlab.com/kicad/libraries, CC-BY-SA 4.0 with the KiCad libraries exception.",
            "built_with": "ecadlib eval build-set (ecadlib/eval/build_set.py)", "cases": len(cases)}
    lines = ["{", f'"meta": {json.dumps(meta)},', '"cases": [']
    lines += [json.dumps(c, separators=(",", ":")) + ("," if i < len(cases) - 1 else "") for i, c in enumerate(cases)]
    lines += ["],", '"references": {']
    items = sorted(refs.items())
    lines += [f"{json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}" + ("," if i < len(items) - 1 else "")
              for i, (k, v) in enumerate(items)]
    lines += ["}", "}"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib eval build-set", description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", required=True, help="kicad-symbols checkout (git clone --depth 1 https://gitlab.com/kicad/libraries/kicad-symbols)")
    ap.add_argument("--footprints", required=True, help="kicad-footprints checkout")
    ap.add_argument("-o", "--out", help="set file (default: ecadlib/eval/sets/<name>.json)")
    ap.add_argument("--name", default="kicad-v1")
    a = ap.parse_args(argv)
    cases, refs = build(a.symbols, a.footprints)
    out = a.out or Path(__file__).with_name("sets") / f"{a.name}.json"
    write_set(out, cases, refs, a.symbols, a.footprints, a.name)
    print(f"{len(cases)} cases, {len(refs)} reference footprints -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
