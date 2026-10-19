"""Standard parts without Claude: when the package is fully set by standard data, build the footprint (and, for the
part kinds whose pinout is standard, the symbol) offline and deterministically. The librarian's `standard` source.

    ecadlib stdparts resolve "SOIC-8 3.9x4.9mm" | "0603" --kind R | --mpn GRM188R71H104KA93D --kind C   what would be built, and why
    ecadlib stdparts list [PATTERN]                    the packages the table knows
    ecadlib stdparts bench [--kicad DIR] [--fixture F] every table entry against KiCad's official footprint
    ecadlib stdparts coverage BOM [BOM ...]            how many BOM lines this source resolves (no network)

Footprints come only from package_specs/standard.json.gz: 618 packages, each with its KiCad name and library. Their
dimensions are the KiCad footprint generator's size definitions (JEDEC / IPC-SM-782 / manufacturer package drawings,
built here with footprint_gen's IPC-7351B equations) or, for the packages KiCad draws by hand (SOD, SMA/SMB/SMC, MELF,
SOT-223, SOT-89, SC-82AB, DO-41, DO-201AD, DPAK/D2PAK, TO-220, TO-92), the published recommended land as a pad table. Every entry was built and
compared with kicad-footprints (pads and courtyard within footprint_bench tolerances) before it went into the table;
`bench` repeats that check.

What resolves (first match wins; footprint text = the BOM footprint, the KiCad footprint name, a KiCad symbol's
Footprint property; then the description and value; then the MPN):
  exact KiCad name     "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOT-23-5", "R_0603_1608Metric" (library prefix optional)
  chip passives        R / C / L / LED / D / fuse by the part's kind and a size code: imperial 01005..2512 ("0603",
                       "C0805", "0603 (1608)"), metric with a marker ("1608Metric", "1608M"), IPC names (RESC1608X55N),
                       or the MPN (Yageo RC/CC, Murata GRM/GCM/GJM/LQG/LQM/LQW/BLM, Samsung CL, KEMET C....C, TDK C<metric>,
                       Panasonic ERJ-, Vishay CRCW/TNPW, Stackpole RMCF, Bourns CR, KOA RK73, Uni-Royal). Two different
                       sizes from two places (footprint 0603, MPN GRM21 = 0805) is a conflict: nothing is built.
  named packages       SOT-23/-5/-6, SOT-323/353/363, SOT-523, SOT-89, SOD-123(F)/323(F)/523/923/128, SMA/SMB/SMC/SMF
                       (DO-214), MicroMELF/MiniMELF/MELF, DO-41/DO-201AD (axial), SC-82AB, DPAK/D2PAK (TO-252/263), TO-220 (vertical), TO-92 (inline), and
                       SOIC/SO/TSSOP/MSOP/SSOP/QSOP/QFN/DFN/SON/QFP families from the name (pins, body, pitch, exposed pad,
                       KiCad or LCSC style "QFN-32_L5.0-W5.0-P0.50-EP3.5"). Plain SOIC-8/14/16 means JEDEC MS-012
                       (3.9 mm body), SOIC-nW MS-013 (7.5 mm), TSSOP-8..28 MO-153 (4.4 mm, 0.65 mm pitch), MSOP-8 3x3
                       0.65 mm, QFP without "EP" no exposed pad. Anything else must be in the name.
  ambiguous -> nothing: a name that fits two different footprints (QFN-16 without a body size, LQFP-64 without body and
                       pitch, "SOT-23-3", which KiCad uses for the 1.6 mm MO-178 body and distributors for TO-236,
                       bare "SOT-223" / TSOT-23 / SC-70 / SC-75 / SOT-26 (tab numbering or body differ by vendor),
                       power packages (PowerPAK, LFPAK, SO-8FL ...), TO-220 marked horizontal, a diode in a TO/DPAK
                       package, "SMA" on a part that is not a diode); the next source tries. Uniqueness is judged
                       against every generic name in KiCad's library, not only this table. A description is weaker
                       evidence than a footprint field: there no JEDEC defaults apply, IC families need the body
                       size, and a bare family (SOT-23, TO-220 ...) only counts for transistors and diodes.

Symbols (only when the pinout is standard for the part): R, C, C_Polarized, L, LED and diodes (2-pin packages: pin 1
cathode, as KiCad's footprints), and these MPN families: SOT-23 BJTs (MMBT39xx/2222/2907/4401/4403/5401/5551/A42/A92,
BC807/817/846-860, S8050/S8550: 1=B 2=E 3=C), SOT-23 MOSFETs (2N7002, BSS138/123/84, AO34xx, SI230x, DMG230x,
IRLML: 1=G 2=S 3=D), TO-92 2N39xx/2N2222A/2N440x/2N5401/5551 (E B C), BC327/337/546-558 (C B E), 2N7000 (S G D),
BS170 (D G S), 1117-type LDOs (AMS1117, LM1117, LD1117S ...) in SOT-223-3_TabPin2 (1=GND/ADJ 2=VOUT 3=VIN,
tab = 2), 78xx/79xx and LM317 in TO-220, 78Lxx in TO-92/SOT-89. Other parts get the footprint only and the symbol comes from the next source.
The librarian records `source: standard:<Lib>:<Name>` (footprint) and `standard:<symbol kind>`; it tries this
source after kicad and easyeda and before datasheet, and never replaces a symbol or footprint an earlier source or
the user's own library already filled."""
import argparse, functools, gzip, json, math, re, sys
from pathlib import Path

from . import footprint_gen as F

TABLE = Path(__file__).with_name("package_specs") / "standard.json.gz"
IMPERIAL = ("01005", "0201", "0402", "0603", "0805", "1008", "1206", "1210", "1806", "1812", "1825", "2010", "2220",
            "2225", "2512", "2816", "2920", "3640", "4020")
METRIC = {"0402": "01005", "0603": "0201", "1005": "0402", "1608": "0603", "2012": "0805", "2520": "1008", "3216": "1206",
          "3225": "1210", "4516": "1806", "4532": "1812", "4564": "1825", "5025": "2010", "5750": "2220", "5763": "2225",
          "6332": "2512", "7142": "2816", "7451": "2920", "9116": "3640", "10251": "4020"}
IMP2MET = {v: k for k, v in METRIC.items()}
CHIP_PREFIX = {"R": "R", "C": "C", "L": "L", "LED": "LED", "D": "D", "F": "Fuse"}
# named packages: normalised text (upper case, no spaces / dashes / underscores) -> table name; diode-only ones need a diode
ALIASES = {
    "SOT23": "SOT-23", "TO236": "SOT-23", "TO236AB": "SOT-23",
    "SOT235": "SOT-23-5", "SOT25": "SOT-23-5", "SOT753": "SOT-23-5",
    "SOT236": "SOT-23-6", "SOT26": None,          # SOT-26 is also sold as SC-74 (1.5 mm body)
    "TSOT235": "TSOT-23-5", "TSOT236": "TSOT-23-6", "TSOT23": None,
    "SOT323": "SOT-323_SC-70", "SC70": None, "SC703": "SOT-323_SC-70",
    "SOT353": "SOT-353_SC-70-5", "SC705": "SOT-353_SC-70-5", "SC88A": "SOT-353_SC-70-5",
    "SOT363": "SOT-363_SC-70-6", "SC706": "SOT-363_SC-70-6", "SC88": "SOT-363_SC-70-6",
    "SOT523": "SOT-523", "SC75": None, "SOT883": "SOT-883",                # SC-75 is SOT-416 or SOT-523
    "SOT223": None, "SOT2233": None, "TO261": None, "TO261AA": None,      # tab = pin 2 or pin 4: the part decides
    "SOT89": "SOT-89-3", "SOT893": "SOT-89-3", "TO243": "SOT-89-3", "TO243AA": "SOT-89-3",
    "DPAK": "TO-252-2", "TO252": "TO-252-2", "TO252AA": "TO-252-2", "TO2522": "TO-252-2", "TO2525": None,
    "D2PAK": "TO-263-2", "TO263": "TO-263-2", "TO263AB": "TO-263-2", "TO2632": "TO-263-2", "TO2635": None,     # 5-lead: KiCad counts 4 or 5
    "TO220": "TO-220-3_Vertical", "TO2203": "TO-220-3_Vertical", "TO220AB": "TO-220-3_Vertical",
    "TO92": "TO-92_Inline", "TO923": "TO-92_Inline", "TO92A": None,
    "SC82AB": "SC-82AB",                          # 4 leads, 2 x 2.1 mm body (SOT-343 is a different land)
}
DIODE_ALIASES = {
    "SOD123": "D_SOD-123", "SOD123F": "D_SOD-123F", "SOD323": "D_SOD-323", "SC76": "D_SOD-323", "SC90": "D_SOD-323",
    "SOD323F": "D_SOD-323F", "SOD523": "D_SOD-523", "SC79": "D_SOD-523", "SOD923": "D_SOD-923", "SOD128": "D_SOD-128",
    "SMA": "D_SMA", "DO214AC": "D_SMA", "SMB": "D_SMB", "DO214AA": "D_SMB", "SMC": "D_SMC", "DO214AB": "D_SMC",
    "SMF": "D_SMF", "DO219AB": "D_SMF", "MINIMELF": "D_MiniMELF", "SOD80": "D_MiniMELF", "LL34": "D_MiniMELF",
    "MELF": "D_MELF", "DO213AB": "D_MELF", "LL41": "D_MELF",
    "DO41": "D_DO-41_SOD81_P10.16mm_Horizontal", "DO204AL": "D_DO-41_SOD81_P10.16mm_Horizontal",
    "SOD81": "D_DO-41_SOD81_P10.16mm_Horizontal",       # axial, laid flat with KiCad's 10.16 mm lead bend
    "DO201AD": "D_DO-201AD_P15.24mm_Horizontal", "DO201": "D_DO-201AD_P15.24mm_Horizontal",   # 15.24 mm bend
    "MICROMELF": "D_MicroMELF",
}
AMBIGUOUS = {"SOT233": "KiCad's SOT-23-3 is the 1.6 mm MO-178 body; distributors use SOT-23-3 for TO-236 SOT-23",
             "SOT223": "SOT-223: tab numbered 2 (SOT-223-3_TabPin2, regulators) or 4 (KiCad's SOT-223)",
             "SOT2233": "SOT-223: tab numbered 2 or 4", "TO261": "SOT-223: tab numbered 2 or 4", "TO261AA": "SOT-223: tab numbered 2 or 4",
             "TSOT23": "TSOT-23 without a pin count", "TO92A": "TO-92 variant", "SOT23W": "wide SOT-23",
             **{k: "KiCad has TO-252-2 and TO-252-2_TabPin1 (tab numbered 2 or 1): the part decides"
                for k in ("TO2522", "TO252", "TO252AA", "DPAK")},
             **{k: "KiCad has TO-263-2 and TO-263-2_TabPin1 (tab numbered 2 or 1): the part decides"
                for k in ("TO2632", "TO263", "TO263AB", "D2PAK")}}
# IC families from the name: generic table entries are '<FAM>-<n>[W][-1EP]_<a>x<b>mm_P<p>mm[_EP<x>x<y>mm]'
NAME_RE = re.compile(r"^(?P<fam>[A-Z]+)-(?P<pins>\d+)(?P<w>W)?(?:-(?P<ep>\d)EP)?_(?P<a>[\d.]+)x(?P<b>[\d.]+)mm"
                     r"_P(?P<p>[\d.]+)mm(?:_EP(?P<ea>[\d.]*\d)x(?P<eb>[\d.]*\d)mm)?(?P<rest>_.+)?$")
FAMILIES = ("SOIC", "SO", "SOP", "TSSOP", "HTSSOP", "MSOP", "VSSOP", "SSOP", "QSOP", "TSOP", "QFN", "VQFN", "WQFN", "UQFN",
            "TQFN", "HVQFN", "LQFN", "DFN", "TDFN", "WDFN", "UDFN", "SON", "VSON", "WSON", "USON", "LQFP", "TQFP", "QFP",
            "PQFP", "LFCSP")
NO_LEAD = {"QFN", "VQFN", "WQFN", "UQFN", "TQFN", "HVQFN", "LQFN", "DFN", "TDFN", "WDFN", "UDFN", "SON", "VSON", "WSON", "USON", "LFCSP"}
QUAD_LEADED = {"LQFP", "TQFP", "QFP", "PQFP"}
FREE_RE = re.compile(r"(?<![A-Z0-9])(?P<fam>" + "|".join(sorted(FAMILIES, key=len, reverse=True)) +
                     r")[-_ ]?(?P<pins>\d{1,3})(?P<w>W)?(?:-(?P<ep>\d)EP)?(?![0-9A-Z])", re.I)
POWER_PKG = re.compile(r"Power ?PAK|PowerDI|PowerSO|PowerFLAT|LFPAK|DirectFET|PQFN|\bSO-?8FL|\bWDFN\d|TSDSON|SuperSO|TOLL|\bPDFN", re.I)
MIL_BODY = {"150": 3.9, "208": 5.3, "209": 5.3, "300": 7.5}
NO_GO = re.compile(r"\b(horizontal|lying|right[- ]angle|bent)\b", re.I)
EP_WORD = re.compile(r"exposed[ _-]?pad|thermal[ _-]?pad|(?<![A-Z0-9])\d?EP(?![A-Z])|PowerPAD", re.I)
# KiCad land variants (bigger hand-solder pads, vias in the exposed pad, other mask / pull-back): never the plain land
LAND_VARIANT = re.compile(r"(?<![A-Za-z])(Hand[_ -]?Solder(?:ing)?|ThermalVias|PullBack|TopTented|Mask\d[\d.]*x[\d.]+mm)(?![A-Za-z])", re.I)


# ---------------------------------------------------------------- the table
@functools.lru_cache(maxsize=1)
def table():
    with gzip.open(TABLE, "rt", encoding="utf-8") as f:
        return json.load(f)


def packages():
    return table()["packages"]


def spec(name):
    """Table entry -> footprint_gen spec (a fresh copy with its name)."""
    e = packages()[name]
    s = {k: v for k, v in json.loads(json.dumps(e)).items() if k != "lib"}
    s["name"] = name
    return s


def kicad_ref(name):
    return f"{packages()[name]['lib']}:{name}"


@functools.lru_cache(maxsize=4096)
def build(name, density=None):
    """(kicad_mod text, report) for a table entry."""
    return F.build(spec(name), density)


@functools.lru_cache(maxsize=1)
def _generic():
    """Every generic IC footprint name in KiCad's library (the table's kicad_generic_names, built and checked or
    not), parsed: [(attrs, name)]. Ambiguity is judged against all of them, so a name that fits two KiCad packages
    is ambiguous even when only one of them is in the checked table."""
    out = []
    for n in sorted(set(table().get("kicad_generic_names") or []) | set(packages())):
        m = NAME_RE.match(n)
        if m and m.group("fam") in FAMILIES:
            g = m.groupdict()
            out.append(({"fam": g["fam"], "pins": int(g["pins"]), "w": bool(g["w"]), "ep": bool(g["ep"]),
                         "body": tuple(sorted((float(g["a"]), float(g["b"])))), "pitch": float(g["p"]),
                         "epsize": tuple(sorted((float(g["ea"]), float(g["eb"])))) if g["ea"] else None,
                         "variant": bool(g["rest"])}, n))
    return out


# ---------------------------------------------------------------- name parsing
def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _strip_lib(s):
    s = str(s or "").strip()
    return s.split(":", 1)[1] if re.match(r"^[A-Za-z0-9_.\-]+:[^:]", s) and not re.match(r"^[A-Z]:[\\/]", s) else s


def chip_size(text):
    """Imperial size code from a footprint / description text, or None. Bare 4-digit codes are imperial (the
    industry default); metric needs a marker (1608Metric, 1608M, '0603 (1608)' gives both and they must agree)."""
    t = str(text or "")
    found = set()
    m = re.search(r"(?:RES|CAP|IND|LED|DIO|FUS)C(\d{4})X\d+", t, re.I)       # IPC-7351 names: RESC1608X55N
    if m and m.group(1) in METRIC:
        found.add(METRIC[m.group(1)])
    for m in re.finditer(r"(?<![0-9])(\d{4,5})\s*(?:_?Metric|M)(?![A-Za-z0-9])", t, re.I):
        if m.group(1) in METRIC:
            found.add(METRIC[m.group(1)])
    for m in re.finditer(r"(?<![0-9.])(\d{4,5})(?![0-9.])(?!\s*(?:_?Metric|M\b|mm|mil|V|W|K|R|nF|pF|uF|µF|Ω|ohm|%))", t, re.I):
        code = m.group(1)
        if code in IMPERIAL and not re.search(r"(?:RES|CAP|IND|LED|DIO|FUS)C$", t[:m.start()], re.I):
            found.add(code)
    # a bare metric-only code next to an imperial one ('0603 (1005)', '0402 / 1608') must agree with it; alone it
    # is not read (bare codes are imperial), so it can only turn a size into a conflict, never pick one
    bare_metric = {METRIC[m.group(1)] for m in re.finditer(
        r"(?<![0-9A-Za-z.])(\d{4,5})(?![0-9A-Za-z.])(?!\s*(?:mm|mil|V|W|K|R|nF|pF|uF|µF|Ω|ohm|%))", t, re.I)
        if m.group(1) in METRIC and m.group(1) not in IMPERIAL}
    if found and bare_metric - found:
        found |= bare_metric
    return found.pop() if len(found) == 1 else ("conflict:" + "/".join(sorted(found)) if found else None)


# MPN -> (kind, imperial size); kind "" = any passive kind (decided by the part)
def _mpn_rules():
    m3 = {"03": "0201", "15": "0402", "18": "0603", "21": "0805", "31": "1206", "32": "1210", "43": "1812", "55": "2220"}
    cl = {"03": "0201", "05": "0402", "10": "0603", "21": "0805", "31": "1206", "32": "1210", "43": "1812", "55": "2220"}
    erj = {"XG": "01005", "1G": "0201", "2": "0402", "3": "0603", "6": "0805", "8": "1206", "14": "1210", "12Z": "2010",
           "12": "1812", "1T": "2512", "P03": "0603", "P06": "0805", "P08": "1206", "PA2": "0402", "PA3": "0603", "PA6": "0805",
           "PA8": "1206"}
    koa = {"1H": "0201", "1E": "0402", "1J": "0603", "2A": "0805", "2B": "1206", "2E": "1210", "2H": "2010", "3A": "2512"}
    imp = "(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)"
    return [
        (re.compile(r"^(?:RC|RT|RL|AC|AF|AT|PE|PT|RV)" + imp + r"[A-Z]"), "R", None),           # Yageo resistors
        (re.compile(r"^CC" + imp + r"[A-Z]{2}"), "C", None),                                     # Yageo MLCC
        (re.compile(r"^(?:GRM|GCM|GJM|GCJ|GRT|GCQ|GQM|GMA)(\d\d)"), "C", m3),                   # Murata MLCC
        (re.compile(r"^(?:LQG|LQM|LQW|LQP)(\d\d)"), "L", m3), (re.compile(r"^BLM(\d\d)"), "L", m3),   # Murata L, beads
        (re.compile(r"^CL(\d\d)[A-Z]\d"), "C", cl),                                              # Samsung MLCC
        (re.compile(r"^C(0402|0603|1005|1608|2012|3216|3225|4532|5750)(?:C0G|NP0|X[5-8][A-Z]|JB|CH)"), "C", "metric"),   # TDK
        # KEMET MLCC: C<imperial><C standard | X flexible termination | F ...><capacitance>; C0603X563K1RACTU is an
        # imperial 0603, not TDK's metric 0603 (X followed by a digit is KEMET's capacitance code, not a dielectric)
        (re.compile(r"^C" + imp + r"[CXF]\d{3}[A-Z]"), "C", None),
        (re.compile(r"^ERJ-?(XG|1G|1T|12Z|12|14|PA[2368]|P0[368]|[2368])"), "R", erj),           # Panasonic
        (re.compile(r"^(?:CRCW|TNPW|RCG|RCS)" + imp), "R", None),                                # Vishay
        (re.compile(r"^RMCF" + imp), "R", None), (re.compile(r"^CR" + imp + r"-"), "R", None),   # Stackpole, Bourns
        (re.compile(r"^RK73[HB](1H|1E|1J|2A|2B|2E|2H|3A)"), "R", koa),                           # KOA
        (re.compile(r"^" + imp + r"W[A-Z]{2}[A-Z0-9]"), "R", None),                              # Uni-Royal 0603WAF1002T5E
    ]


MPN_RULES = _mpn_rules()


def mpn_size(mpn):
    """-> (kind, imperial size) or (None, None)."""
    s = re.sub(r"\s+", "", str(mpn or "").upper())
    for rx, kind, conv in MPN_RULES:
        m = rx.match(s)
        if m:
            code = m.group(1)
            size = METRIC.get(code) if conv == "metric" else conv.get(code) if isinstance(conv, dict) else code
            if size:
                return kind, size
    return None, None


def _free_attrs(text):
    """IC family attributes from free text (BOM footprint, LCSC package, description)."""
    t = str(text or "")
    m = FREE_RE.search(t)
    if not m:
        return None
    a = {"fam": m.group("fam").upper(), "pins": int(m.group("pins")), "w": bool(m.group("w")), "ep": True if m.group("ep") else None,
         "body": None, "pitch": None, "epsize": None}
    rest = t[m.end():] + " " + t[:m.start()]
    v = re.match(r"-(N?\d+)(?![\d.]|\s*(?:[xX×*]|mm|mil|EP))", t[m.end():], re.I)   # SOIC-8-N7, SOIC-14-16: pins removed
    if v:
        a["variant"] = v.group(0)
    b = (re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)\s*(?:x\s*[\d.]+\s*)?(?:mm)?", rest)
         or re.search(r"(?<![A-Z])L(\d+(?:\.\d+)?)-W(\d+(?:\.\d+)?)", rest))                    # LCSC L5.0-W5.0
    if b:
        a["body"] = tuple(sorted((float(b.group(1)), float(b.group(2)))))
    p = re.search(r"(?<![A-Z])P\s?(\d+(?:\.\d+)?)\s*(?:mm)?(?![\d.])", rest) or re.search(r"(\d\.\d+)\s*mm\s*pitch|pitch\s*(\d\.\d+)", rest, re.I)
    if p:
        a["pitch"] = float(next(g for g in p.groups() if g))
    e = re.search(r"(?<![A-Z])EP\s?(\d+(?:\.\d+)?)(?:\s*[xX×]\s*(\d+(?:\.\d+)?))?", rest)
    if e:
        a["ep"] = True
        a["epsize"] = tuple(sorted((float(e.group(1)), float(e.group(2) or e.group(1)))))
    elif re.search(r"exposed pad|thermal pad|\bEP\b|-EP\b|_EP\b|PowerPAD", rest, re.I):
        a["ep"] = True
    mil = re.search(r"(150|208|209|300)\s*mil", t, re.I)
    if mil and not a["body"]:
        a["body_w"] = MIL_BODY[mil.group(1)]
    return a


def _defaults(a):
    """JEDEC defaults where the bare family name means one package."""
    fam, n = a["fam"], a["pins"]
    if fam == "SO" or (fam == "SOP" and n in (8, 14, 16) and (a.get("body_w") == 3.9 or (a["body"] or (0,))[0] == 3.9)):
        a["fam"] = fam = "SOIC"                           # SO-8, and SOP-8 150 mil (LCSC), are JEDEC SOIC
    if fam == "SOIC" and a["pitch"] is None:
        a["pitch"] = 1.27
    if fam == "SOIC" and not a["body"] and not a.get("body_w"):
        a["body_w"] = 7.5 if a["w"] else 3.9 if n in (8, 14, 16) else None
    if fam == "TSSOP" and not a["body"] and not a.get("body_w") and n in (8, 14, 16, 20, 24, 28) and a["pitch"] in (None, 0.65):
        a["body_w"], a["pitch"] = 4.4, 0.65
    if fam == "MSOP" and not a["body"] and n == 8 and a["pitch"] in (None, 0.65):
        a["body"], a["pitch"] = (3.0, 3.0), 0.65
    if fam not in NO_LEAD and a["ep"] is None:            # leaded packages with a pad name it (HTSSOP, -1EP, PowerPAD)
        a["ep"] = False
    return a


def _close(x, y, tol=0.051):
    return abs(x - y) <= tol


def _candidates(a):
    out = []
    for g, n in _generic():
        if g["fam"] != a["fam"] or g["pins"] != a["pins"]:
            continue
        if a["fam"] == "SOIC" and g["w"] != a["w"] and not a["body"]:
            continue
        if a["w"] and not g["w"] and g["body"][0] < 4.0:   # 'SOIC-8W 3.9x4.9': W and a narrow body contradict
            continue
        if a["body"] and not all(_close(x, y) for x, y in zip(g["body"], a["body"])):
            continue
        if a.get("body_w") and not _close(g["body"][0], a["body_w"]):
            continue
        if a["pitch"] is not None and not _close(g["pitch"], a["pitch"], 0.011):
            continue
        if a["ep"] is not None and g["ep"] != a["ep"]:
            continue
        if a["epsize"] and not (g["epsize"] and all(_close(x, y) for x, y in zip(g["epsize"], a["epsize"]))):
            continue
        if g["variant"] and n not in packages():      # _Mask / _PullBack ... variants: count only when we can build them
            continue
        out.append(n)
    return out


def _same_land(names):
    """True when every candidate gives the same pads (then the name is not really ambiguous)."""
    from . import footprint_bench as B
    ref = None
    for n in names:
        pads = sorted((p[0], p[1], p[2], p[3], p[4]) for p in B.numbers(build(n)[0])["pads"])
        if ref is None:
            ref = pads
        elif len(pads) != len(ref) or any(p[0] != q[0] or max(abs(x - y) for x, y in zip(p[1:], q[1:])) > 0.01 for p, q in zip(pads, ref)):
            return False
    return True


def ic_package(text, defaults=True):
    """IC package name from free text -> (table name, how) / (None, why) / (None, None) when no family is named.
    defaults=False (descriptions): no JEDEC default body / pitch; the text alone must pick one package."""
    if POWER_PKG.search(str(text or "")):
        return None, f"power package ({POWER_PKG.search(str(text)).group(0)}): not a standard JEDEC outline"
    a = _free_attrs(text)
    if not a:
        return None, None
    if a.get("variant"):
        return None, f"{a['fam']}-{a['pins']}{'W' if a['w'] else ''}{a['variant']}: a variant with pins removed, not the standard package"
    if not defaults and not a["body"]:
        return None, f"{a['fam']}-{a['pins']} in the description without a body size"
    label = f"{a['fam']}-{a['pins']}" + ("W" if a["w"] else "")
    # Never a guess: a wrong footprint skips the datasheet reader. The name must give the body (SO-14 is 3.9 or 5.3 mm,
    # SOIC-16W 10.3 or 12.8 mm long, TDFN-12 3x3 at 0.4 or 0.5 mm) and, with an exposed pad, its size. MSOP-8 is the
    # one family name JEDEC (MO-187-AA) gives a single body.
    if not a["body"] and not a.get("body_w") and not (a["fam"] == "MSOP" and a["pins"] == 8 and not a["ep"]):
        return None, f"{label}: no body size in the name (several bodies share it)"
    if (a["ep"] or a["fam"] in NO_LEAD) and not a["epsize"]:
        return None, f"{label}: no exposed-pad size in the name; give e.g. _EP2.1x2.1mm"
    a = _defaults(a) if defaults else dict(a, fam="SOIC" if a["fam"] == "SO" else a["fam"])
    c = _candidates(a)
    if not c and a["fam"] in NO_LEAD:                     # VQFN/WQFN/...: same land as QFN, height differs
        for fam in ("QFN", "DFN") if a["fam"] not in ("QFN", "DFN") else ():
            c = _candidates(dict(a, fam=fam))
            if c:
                break
    if not c:
        return None, f"{label}: no standard package with those dimensions in the table"
    if len(c) > 1 and not (all(n in packages() for n in c) and _same_land(c)):
        miss = [k for k in ("body", "pitch", "epsize") if not a.get(k) and not (k == "body" and a.get("body_w"))]
        return None, f"{label}: ambiguous ({len(c)} KiCad packages fit, e.g. {', '.join(sorted(c)[:3])}; give the {'/'.join(miss) or 'full name'})"
    best = sorted(c, key=lambda n: (n not in packages(), len(n)))[0]
    if best not in packages():
        return None, f"{label} -> {best}: not in the checked table (ecadlib's IPC build differs from KiCad's)"
    return best, f"{label} -> {best}"


READ_FAMILIES = {"gullwing": set(FAMILIES) - NO_LEAD - QUAD_LEADED, "qfp": QUAD_LEADED, "qfn": NO_LEAD}


def identify(read):
    """A datasheet read's package (a footprint_gen spec) -> (table name, how) when its own numbers name exactly one
    checked package, else (None, why). Pin count, pitch, body and exposed pad pick the KiCad generic names that fit, in
    the family the drawing is titled with when it names one; a fit that is not in the checked table makes it ambiguous
    (as in ic_package). Each table entry left must agree with the whole read, lead span included (crosscheck.compare
    against the table), and so must no other entry with other pads: a maker's own outline the read names (a slightly
    other exposed pad) makes it ambiguous. The read's terminal and land numbers never pick: a misread there cannot
    pick wrong."""
    from . import crosscheck as CC
    fams = READ_FAMILIES.get(read.get("package_type"))
    if not fams:
        return None, f"package type {read.get('package_type')}: no JEDEC family in the table"
    c = CC.critical(read)
    miss = [k for k in ("pin_count", "pitch", "body_length", "body_width") if c.get(k) is None]
    if c["has_ep"] and (c["ep_length"] is None or c["ep_width"] is None):
        miss.append("exposed pad size")
    if miss:
        return None, f"the read has no {', '.join(miss)}"
    fits = lambda k, a, x: CC._close(k, a, (x, x, x), True)
    body = sorted((c["body_length"], c["body_width"]), key=lambda v: v[0])
    ep = sorted((c["ep_length"], c["ep_width"]), key=lambda v: v[0]) if c["has_ep"] else None

    def fitting(fs):
        out = []
        for g, n in _generic():
            if g["fam"] not in fs or g["pins"] != c["pin_count"] or g["ep"] != c["has_ep"]:
                continue
            if g["variant"] and n not in packages():
                continue
            if not fits("pitch", c["pitch"], g["pitch"]) or not all(fits("body_width", a, x) for a, x in zip(body, g["body"])):
                continue
            if ep and not (g["epsize"] and all(fits("ep_width", a, x) for a, x in zip(ep, g["epsize"]))):
                continue
            out.append(n)
        return out
    # the family the drawing is titled with (SOIC, TSSOP, VQFN ...) narrows the rivals, as a BOM name does in
    # ic_package; its size never comes from the name. SO and 150 mil SOP may be JEDEC SOIC or KiCad's own SO / SOP
    # land (both stay rivals); a no-lead family the table lacks (VQFN, WSON) has the QFN / DFN land.
    fam = next((a["fam"] for a in map(_free_attrs, (read.get("name"), read.get("description"))) if a), None)
    first = {fam, "SOIC"} if fam == "SO" or (fam == "SOP" and c["pin_count"] in (8, 14, 16) and _close(body[0][0], 3.9, 0.1)) \
        else {fam}
    tries = ([first] + ([{"QFN"}, {"DFN"}] if fam in NO_LEAD else [])) if fam in fams else [fams]
    names = next((r for r in map(fitting, tries) if r), [])
    label = f"{c['pin_count']} pins, pitch {c['pitch'][0]:g}, body {body[0][0]:g} x {body[1][0]:g}" + \
        (f", exposed pad {ep[0][0]:g} x {ep[1][0]:g}" if ep else "")
    if not names:
        return None, f"{label}: no standard package fits"
    if any(n not in packages() for n in names):
        return None, f"{label}: fits a KiCad package that is not in the checked table ({', '.join(sorted(names)[:3])})"
    agree = [n for n in names if CC.compare(c, CC.critical(spec(n)), "table")["status"] == "agreed"]
    if not agree:
        return None, f"{label}: fits {', '.join(sorted(names)[:3])}, but the read's other numbers disagree"
    said = " ".join(str(read.get(k) or "") for k in ("name", "description", "drawing")).upper()
    generic = {n for _, n in _generic()}
    for n, e in packages().items():                   # a maker's or JEITA's own outline (Texas_RHA0040B_VQFN-40,
        tags = [t for t in n.split("_")[:2] if not FREE_RE.fullmatch(t)]                    # JEITA_SOIC-8) is a rival
        if n in names or n in generic or not any(re.search(rf"(?<![A-Z0-9]){re.escape(t.upper())}(?![A-Z0-9])", said)
                                                 for t in tags):                         # when the read names it
            continue
        if e.get("package_type") == read.get("package_type") and e.get("pin_count") == c["pin_count"] \
                and CC.compare(c, CC.critical(spec(n)), "table")["status"] == "agreed":
            agree.append(n)
    if len(agree) > 1 and not _same_land(agree):
        return None, f"{label}: ambiguous ({', '.join(sorted(agree)[:3])})"
    best = sorted(agree, key=len)[0]
    return best, f"{label} -> {best}"


BARE_FAMILY = {"SOT23", "TO220", "TO220AB", "TO263", "TO252", "DPAK", "D2PAK", "TO92"}   # in a description: often the family


def named_package(text, diode=False, weak=False, kind=None):
    """SOT/SOD/DO-214/TO names -> (table name, how) / (None, why) / (None, None). weak (a description): a bare family
    name (SOT-23, TO-220 ...) counts only for a transistor or diode, where it means the 3-lead package."""
    if POWER_PKG.search(str(text or "")):
        return None, f"power package ({POWER_PKG.search(str(text)).group(0)}): not a standard JEDEC outline"
    t = str(text or "").upper()
    for m in re.finditer(r"(?<![A-Z0-9])(SOT|TSOT|SOD|SC|DO|TO|SM[ABCF]|D2PAK|DPAK|MICROMELF|MINIMELF|MELF|LL)[-_ ]?(\d{0,3}[A-Z]{0,2})(?:[-_ ]?(\d)L?)?(?![A-Z0-9]|[-_ ]?\d)", t):
        key = _norm(m.group(0))
        key = re.sub(r"L$", "", key) if re.fullmatch(r"(SOT|TSOT)\d+\dL", key) else key
        if key in AMBIGUOUS:
            return None, f"{m.group(0).strip()}: ambiguous ({AMBIGUOUS[key]})"
        if diode and re.match(r"(TO2[256]|D2?PAK)", key):
            return None, f"{m.group(0).strip()}: a diode in a TO / DPAK package has 2 or 3 leads"
        if weak and key in BARE_FAMILY and kind not in ("Q", "D", "LED"):
            return None, f"{m.group(0).strip()} in the description: the package family, pin count not given"
        name = ALIASES.get(key, "") if key in ALIASES else DIODE_ALIASES.get(key) if diode else None
        if key in ALIASES and name is None:
            return None, f"{m.group(0).strip()}: ambiguous"
        if key in DIODE_ALIASES and not diode and key not in ALIASES:
            return None, f"{m.group(0).strip()}: a diode package name on a part that is not a diode"
        if name and name.startswith(("TO-220", "TO-92")) and NO_GO.search(str(text)):
            return None, f"{m.group(0).strip()}: horizontal mounting is not the standard footprint"
        if name and EP_WORD.search(str(text)):         # 'SOT-23-5-EP': the table's package has no exposed pad
            return None, f"{m.group(0).strip()} with an exposed pad: not the standard package (no pad under the body)"
        if name and name in packages():
            return name, f"{m.group(0).strip()} -> {name}"
    return None, None


def footprint_for(texts, kind=None, mpn="", weak=()):
    """texts: footprint-like strings in priority order (BOM footprint, KiCad footprint names); weak: descriptions and
    values, trusted less (no JEDEC default sizes for IC families). kind: R C L LED D F or None.
    -> (table name, how) or (None, why)."""
    pk = packages()
    texts = [str(t) for t in texts if str(t or "").strip()]
    weak = [str(t) for t in weak if str(t or "").strip()]
    low = {n.lower(): n for n in pk}
    for t in texts:                                       # exact KiCad name, library prefix optional
        n = low.get(_strip_lib(t).strip().lower())
        if n and _norm(n) in AMBIGUOUS and _strip_lib(t) == t.strip():
            return None, f"{n}: ambiguous without a library prefix ({AMBIGUOUS[_norm(n)]})"
        if n:
            if NAME_RE.match(n) or not n.startswith(("R_", "C_", "L_", "LED_", "D_", "Fuse_")) or _chip_kind_ok(n, kind):
                return n, f"KiCad name {n}"
    for t in texts:                                       # 'R_0603_1608Metric_Pad0.98x0.95mm_HandSolder', '..._ThermalVias':
        v = LAND_VARIANT.search(t)                        # a land the table does not have; the plain one has other pads
        if v:
            return None, f"{_strip_lib(t).strip()}: the {v.group(1)} land variant is not in the standard table"
    if kind in CHIP_PREFIX:                              # chip passives: size from the texts, then the MPN
        sizes = {}
        for t in texts + weak:
            s = chip_size(t)
            if s:
                sizes.setdefault(s, t)
        mk, ms = mpn_size(mpn)
        if ms and (mk == kind or (mk == "C" and kind == "C") or (mk == "L" and kind == "L")):
            sizes.setdefault(ms, f"MPN {mpn}")
        elif ms and mk != kind:
            return None, f"MPN {mpn} looks like a {mk} ({ms}) but the part is a {kind}"
        real = {s for s in sizes if not s.startswith("conflict")}
        if any(s.startswith("conflict") for s in sizes) or len(real) > 1:
            return None, "size codes disagree: " + ", ".join(f"{s} ({w})" for s, w in sizes.items())
        if real:
            s = real.pop()
            n = f"{CHIP_PREFIX[kind]}_{s}_{IMP2MET[s]}Metric"
            if n in pk:
                return n, f"{kind} chip {s} from {sizes[s]}"
            return None, f"{kind} chip {s}: not a standard KiCad size"
    why = None
    for t in texts + weak:
        n, how = named_package(t, diode=kind in ("D", "LED"), weak=t not in texts, kind=kind)
        if n or how:
            return n, how
        n, how = ic_package(t, defaults=t in texts)
        if n or (how and t in texts):
            return n, how + ("" if t in texts else " (from the description)") if how else how
        why = why or how
    return None, why or "no standard package named"


def _chip_kind_ok(name, kind):
    pre = name.split("_")[0]
    return kind is None or CHIP_PREFIX.get(kind) == pre or (kind == "D" and pre == "D")


# ---------------------------------------------------------------- symbols (KiCad .kicad_sym text)
def _pin(num, name, typ, x, y, ang, ln=2.54, hide_name=True):
    return (f'(pin {typ} line (at {x:g} {y:g} {ang}) (length {ln:g}) (name "{name}" (effects (font (size 1.27 1.27))))'
            f' (number "{num}" (effects (font (size 1.27 1.27)))))')


def _poly(pts, fill="none", w=0.254):
    return (f"(polyline (pts {' '.join(f'(xy {x:g} {y:g})' for x, y in pts)}) (stroke (width {w:g}) (type default))"
            f" (fill (type {fill})))")


def _rect(x1, y1, x2, y2, fill="none", w=0.254):
    return f"(rectangle (start {x1:g} {y1:g}) (end {x2:g} {y2:g}) (stroke (width {w:g}) (type default)) (fill (type {fill})))"


def _circle(x, y, r, w=0.254):
    return f"(circle (center {x:g} {y:g}) (radius {r:g}) (stroke (width {w:g}) (type default)) (fill (type none)))"


def _arc(s, m, e, w=0.254):
    return f"(arc (start {s[0]:g} {s[1]:g}) (mid {m[0]:g} {m[1]:g}) (end {e[0]:g} {e[1]:g}) (stroke (width {w:g}) (type default)) (fill (type none)))"


def _diode_body(kind):
    """Anode right, cathode (bar) left at x = -1.27: pin 1 K must be the left pin (as KiCad's Device:D)."""
    g = [_poly([(1.27, 1.27), (1.27, -1.27), (-1.27, 0), (1.27, 1.27)], "none"), _poly([(-1.27, 0), (1.27, 0)])]
    bar = {"D": [(-1.27, 1.27), (-1.27, -1.27)],
           "D_Schottky": [(-0.635, 0.762), (-0.635, 1.27), (-1.27, 1.27), (-1.27, -1.27), (-1.905, -1.27), (-1.905, -0.762)],
           "D_Zener": [(-0.762, 1.27), (-1.27, 1.27), (-1.27, -1.27), (-1.778, -1.27)],
           "D_TVS": [(-0.762, 1.27), (-1.27, 1.27), (-1.27, -1.27), (-1.778, -1.27)]}
    g.append(_poly(bar.get(kind, bar["D"])))
    if kind == "LED":
        g += [_poly([(-0.508, -1.524), (0.762, -2.794)]), _poly([(0.762, -2.794), (0.0, -2.794)]), _poly([(0.762, -2.794), (0.762, -2.032)]),
              _poly([(0.762, -1.524), (2.032, -2.794)]), _poly([(2.032, -2.794), (1.27, -2.794)]), _poly([(2.032, -2.794), (2.032, -2.032)])]
    return g


SYMBOLS = {   # kind -> (KiCad-style name, reference, description, graphics, pins [(num, name, type, x, y, angle, length)])
              # pin ends on the 100 mil grid (±5.08 mm), so wires snap to them on Altium's default grid
    "R": ("R", "R", "Resistor", [_rect(-1.016, -2.54, 1.016, 2.54)],
          [("1", "~", "passive", 0, 5.08, 270, 2.54), ("2", "~", "passive", 0, -5.08, 90, 2.54)]),
    "R_US": ("R_US", "R", "Resistor, US symbol", [_poly([(0, 2.54), (0, 2.286), (1.016, 1.905), (-1.016, 1.143), (1.016, 0.381), (-1.016, -0.381),
                                                       (1.016, -1.143), (-1.016, -1.905), (0, -2.286), (0, -2.54)])],
             [("1", "~", "passive", 0, 5.08, 270, 2.54), ("2", "~", "passive", 0, -5.08, 90, 2.54)]),
    "C": ("C", "C", "Unpolarized capacitor", [_poly([(-2.032, 0.762), (2.032, 0.762)], w=0.508), _poly([(-2.032, -0.762), (2.032, -0.762)], w=0.508)],
          [("1", "~", "passive", 0, 5.08, 270, 4.318), ("2", "~", "passive", 0, -5.08, 90, 4.318)]),
    "CP": ("C_Polarized", "C", "Polarized capacitor", [_rect(-2.286, 0.508, 2.286, 1.016), _rect(-2.286, -0.508, 2.286, -1.016, "outline"),
                                                        _poly([(-1.778, 2.286), (-0.762, 2.286)]), _poly([(-1.27, 2.794), (-1.27, 1.778)])],
           [("1", "~", "passive", 0, 5.08, 270, 4.064), ("2", "~", "passive", 0, -5.08, 90, 4.064)]),
    "L": ("L", "L", "Inductor", [_arc((0, 2.54), (0.635, 1.905), (0, 1.27)), _arc((0, 1.27), (0.635, 0.635), (0, 0)),
                                 _arc((0, 0), (0.635, -0.635), (0, -1.27)), _arc((0, -1.27), (0.635, -1.905), (0, -2.54))],
          [("1", "1", "passive", 0, 5.08, 270, 2.54), ("2", "2", "passive", 0, -5.08, 90, 2.54)]),
    "LED": ("LED", "D", "Light emitting diode (pin 1 = cathode)", _diode_body("LED"),
            [("1", "K", "passive", -5.08, 0, 0, 3.81), ("2", "A", "passive", 5.08, 0, 180, 3.81)]),
}
for _k, _d in (("D", "Diode"), ("D_Schottky", "Schottky diode"), ("D_Zener", "Zener diode"), ("D_TVS", "TVS diode (unidirectional)")):
    SYMBOLS[_k] = (_k, "D", f"{_d} (pin 1 = cathode)", _diode_body(_k), [("1", "K", "passive", -5.08, 0, 0, 3.81), ("2", "A", "passive", 5.08, 0, 180, 3.81)])

BJT = lambda pnp: [_circle(1.27, 0, 2.8194), _poly([(0.635, 1.905), (0.635, -1.905)], w=0.508), _poly([(0.635, 0.635), (2.54, 2.54)]),
                   _poly([(0.635, -0.635), (2.54, -2.54)]), _poly([(-2.54, 0), (0.635, 0)])] + (
    [_poly([(2.286, -1.778), (1.778, -2.286), (1.27, -1.27), (2.286, -1.778)], "outline")] if pnp else
    [_poly([(1.524, -2.286), (2.286, -1.524), (2.54, -2.54), (1.524, -2.286)], "outline")])
FET = lambda pch: [_circle(1.27, 0, 2.8194), _poly([(0.254, 1.905), (0.254, -1.905)]), _poly([(0.762, 2.286), (0.762, 1.27)], w=0.508),
                   _poly([(0.762, 0.508), (0.762, -0.508)], w=0.508), _poly([(0.762, -1.27), (0.762, -2.286)], w=0.508),
                   _poly([(0.762, 1.778), (2.54, 1.778), (2.54, 2.54)]), _poly([(0.762, -1.778), (2.54, -1.778), (2.54, -2.54)]),
                   _poly([(0.762, 0), (2.54, 0), (2.54, -1.778)]), _poly([(-2.54, 0), (0.254, 0)])] + (
    [_poly([(2.286, 0), (1.27, 0.381), (1.27, -0.381), (2.286, 0)], "outline")] if pch else
    [_poly([(1.016, 0), (2.032, 0.381), (2.032, -0.381), (1.016, 0)], "outline")])


def _three(kind, order):
    """3-pin discrete: order = pin names for pins 1, 2, 3 (B/E/C or G/S/D)."""
    pos = {"B": (-5.08, 0, 0), "G": (-5.08, 0, 0), "C": (2.54, 5.08, 270), "D": (2.54, 5.08, 270), "E": (2.54, -5.08, 90),
           "S": (2.54, -5.08, 90)}
    pins = [(str(i + 1), nm, "input" if nm in "BG" else "passive", *pos[nm], 2.54) for i, nm in enumerate(order)]
    g = BJT(kind == "PNP") if kind in ("NPN", "PNP") else FET(kind == "PMOS")
    desc = {"NPN": "NPN transistor", "PNP": "PNP transistor", "NMOS": "N-channel MOSFET", "PMOS": "P-channel MOSFET"}[kind]
    ref = "Q"
    return (f"Q_{kind}_{''.join(order)}", ref, f"{desc}, pins {', '.join(f'{i + 1}={n}' for i, n in enumerate(order))}", g, pins)


def _regulator(order, adj_names):
    """3-pin linear regulator box; order = names for pins 1..3 (VI, VO, GND or ADJ); tab = the VO pin's number."""
    side = {"VI": (-7.62, 0, 0), "VO": (7.62, 0, 180), "GND": (0, -7.62, 90), "ADJ": (0, -7.62, 90)}
    typ = {"VI": "power_in", "VO": "power_out", "GND": "power_in", "ADJ": "input"}
    pins = [(str(i + 1), nm, typ[nm], *side[nm], 2.54) for i, nm in enumerate(order)]
    return (f"Regulator_Linear_{'_'.join(order)}", "U", f"Linear regulator, pins {', '.join(f'{i + 1}={n}' for i, n in enumerate(order))}",
            [_rect(-5.08, 2.54, 5.08, -5.08, "background")], pins)


# MPN family -> (allowed packages (table names), symbol builder args)
FAMILY = [
    (r"^(MMBT(3904|2222A?|4401|5551|A42|A06|A05|2369)|BC8(17|18|46|47|48|49|50)|BCW3[123]|S8050|MMBT8050)", ("SOT-23",), ("NPN", "BEC")),
    (r"^(MMBT(3906|2907A?|4403|5401|A92|A56|A55)|BC8(07|08|56|57|58|59|60)|S8550|MMBT8550)", ("SOT-23",), ("PNP", "BEC")),
    (r"^(2N7002|BSS138|BSS123|AO340[0246]|AO341[68]|AO3422|SI230[24]|SI2312|DMG230[2]|IRLML(2502|6344|6346|0030|0040|0060|2030|2060|6244))", ("SOT-23",), ("NMOS", "GSD")),
    (r"^(BSS84|AO340[17]|AO341[359]|AO3423|SI230[15]|SI2307|DMG230[57]|IRLML(6402|6401|2244|2246|5103|5203|9301|9303))", ("SOT-23",), ("PMOS", "GSD")),
    (r"^(2N3904|2N3906|2N2222A?|2N2907A?|2N440[13]|2N5401|2N5551)", ("TO-92_Inline",), ("NPN|PNP", "EBC")),
    (r"^(BC3[23]7|BC54[6-9]|BC55[6-9]|BC550|BC560)", ("TO-92_Inline",), ("NPN|PNP", "CBE")),
    (r"^2N7000", ("TO-92_Inline",), ("NMOS", "SGD")),
    (r"^BS170", ("TO-92_Inline",), ("NMOS", "DGS")),
    (r"^(AMS1117|LM1117|LD1117S|NCP1117ST|TLV1117|SPX1117M3|HT1117|CJT1117)", ("SOT-223-3_TabPin2",), ("REG", "1117")),
    (r"^(L|LM|MC|UA|KA)?78\d\d", ("TO-220-3_Vertical",), ("REG", "78")),
    (r"^(L|LM|MC|UA|KA)?79\d\d", ("TO-220-3_Vertical",), ("REG", "79")),
    (r"^LM317T", ("TO-220-3_Vertical",), ("REG", "317")),
    (r"^(L|LM|MC|UA|KA)?78L\d\d", ("TO-92_Inline", "SOT-89-3"), ("REG", "78L")),
]
FAMILY_PKG = {"SOT-23": r"\bSOT-?23(?![-\d])", "TO-92_Inline": r"\bTO-?92(?![-\dLS])", "SOT-223-3_TabPin2": r"\bSOT-?223\b|\bTO-?261",
              "TO-220-3_Vertical": r"\bTO-?220(?:AB|-3)?(?![-\dF])", "SOT-89-3": r"\bSOT-?89(?:-3)?(?![-\d])"}
PNP_RE = re.compile(r"^(2N3906|2N2907|2N4403|2N5401|BC3[2]7|BC55[6-9]|BC560)")


def symbol_kind(kind, mpn, fp_name, description=""):
    """-> (symbol key, why) for a standard symbol, or (None, why)."""
    if kind in ("R", "C", "CP", "L", "LED"):
        return kind, "generic symbol"
    two_pin = fp_name and (fp_name.startswith(("D_", "LED_")) or (spec_pins(fp_name) == 2))
    if kind == "D":
        if not two_pin:
            return None, "diode in a package with more than 2 pins: pinout not standard"
        d = str(description or "") + " " + str(mpn or "")
        if re.match(r"^D_\d{4}", fp_name) and not re.search(r"diode|rectifier|zener|schottky|\bTVS\b|\bESD\b", d, re.I):
            return None, "chip-size D part: LED or diode is not stated (footprint is the same)"
        sub = "D_Zener" if re.search(r"zener|BZX|BZT|MMSZ|\bZD", d, re.I) else "D_TVS" if re.search(r"\bTVS\b|transient|SMAJ|SMBJ|SMCJ|P6KE|ESD", d, re.I) \
            else "D_Schottky" if re.search(r"schottky|\bBAT\d|\bSS\d|\bB5\d|\bMBR|\bSK\d|\bSB\d|1N58[1-9]", d, re.I) else "D"
        if sub == "D_TVS":     # KiCad's D_TVS is bidirectional (A1/A2); the MPN does not reliably say which kind it is
            return None, "TVS diode: uni- or bidirectional is not certain from the MPN; symbol left to the other sources"
        return sub, "2-pin diode, pin 1 = cathode"
    m = re.sub(r"[^A-Z0-9]", "", str(mpn or "").upper())
    for rx, pk, (what, pins) in FAMILY:
        if re.match(rx, m):
            if fp_name not in pk:
                return None, f"{mpn}: standard pinout known for {'/'.join(pk)}, not {fp_name}"
            return f"{what}:{pins}", f"{mpn}: {what} family pinout {pins}"
    return None, "no standard pinout for this part"


def spec_pins(name):
    try:
        return spec(name).get("pin_count")
    except KeyError:
        return None


def symbol_text(key, mpn=""):
    """KiCad .kicad_sym text for a symbol key from symbol_kind; returns (text, symbol name)."""
    if ":" in key:
        what, order = key.split(":")
        if what == "REG":
            fixed = order != "1117" or bool(re.search(r"-?(\d\.\d|\d{2}|\d-\d)(?![\d.])", re.sub(r"^.*?1117", "", str(mpn).upper())))
            gnd = "GND" if fixed else "ADJ"
            names = {"1117": [gnd, "VO", "VI"], "78": ["VI", "GND", "VO"], "79": ["GND", "VI", "VO"], "317": ["ADJ", "VO", "VI"],
                     "78L": ["VO", "GND", "VI"]}[order]
            name, ref, desc, g, pins = _regulator(names, ())
        else:
            kind = what if "|" not in what else ("PNP" if PNP_RE.match(re.sub(r"[^A-Z0-9]", "", str(mpn).upper())) else "NPN")
            name, ref, desc, g, pins = _three(kind, list(order))
    else:
        name, ref, desc, g, pins = SYMBOLS[key]
    body = " ".join(g)
    pin_txt = " ".join(_pin(*p) for p in pins)
    hide_names = "(pin_names (offset 0) hide)" if all(p[1] in ("~", "1", "2", "K", "A") for p in pins) else "(pin_names (offset 0.254))"
    if key in SYMBOLS:                         # 2-pin passives and diodes: no "1"/"2" or K/A text on the body (as KiCad's Device:R)
        hide_names += " (pin_numbers hide)"
    flat = all(p[5] in (0, 180) for p in pins)  # horizontal 2-pin parts (diodes): labels above and below, not on the body
    at_ref, at_val = ("0 2.794", "0 -4.064") if flat else ("2.54 0", "-2.54 0")
    text = (f'(kicad_symbol_lib (version 20220914) (generator "ecadlib_stdparts")\n'
            f'  (symbol "{name}" {hide_names} (in_bom yes) (on_board yes)\n'
            f'    (property "Reference" "{ref}" (at {at_ref} 0) (effects (font (size 1.27 1.27))))\n'
            f'    (property "Value" "{name}" (at {at_val} 0) (effects (font (size 1.27 1.27))))\n'
            f'    (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))\n'
            f'    (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))\n'
            f'    (property "Description" "{desc}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))\n'
            f'    (symbol "{name}_0_1" {body})\n'
            f'    (symbol "{name}_1_1" {pin_txt})))\n')
    return text, name


# ---------------------------------------------------------------- one part
def part_kind(category, footprint="", description="", designator=""):
    """R C CP L LED D F Q U or None from the plan folder's last segment, the footprint and the description."""
    cat = str(category or "").split("/")[-1]
    fp, d = str(footprint or ""), str(description or "")
    if cat == "Resistors":
        return "R"
    if cat == "Capacitors":
        return "CP" if fp.startswith("CP_") or re.search(r"tantal|electrolytic|polarized|polymer", d, re.I) else "C"
    if cat == "Inductors":
        return "L"
    if cat == "Diodes":
        return "LED" if fp.startswith("LED_") or re.search(r"\bLED\b", d, re.I) else "D"
    if re.search(r"\bfuse\b|polyfuse|PTC", d, re.I) and re.match(r"^F\d", str(designator or "")):
        return "F"
    return {"Transistors": "Q", "ICs": "U"}.get(cat)


# polarized capacitors (tantalum, polymer, aluminium) by MPN: their case is not a chip-capacitor land and pin 1 is +
POLAR_CAP = re.compile(r"^(T49[1-9]|T5[2-9]\d|TAJ|TPS[A-Z]|TPM|TRJ|TCJ|TCM|TCN|TCQ|TLJ|TPC|NOJ|NOS|F9[1-8]|293D|593D|298D|"
                       r"T8[36]|TR3|TP3|EEE|EEV|EEH|EEF|EEU|ECA|\d+TP[A-Z]|\d+SVP|\d+SEP|UWT|UWX|UUD|UUX|UCD|PCJ|PCF|PLV)")


def resolve(texts, kind=None, mpn="", description=""):
    """What the standard source builds for one part:
    {"footprint": table name | None, "fp_how": str, "kicad_ref": "Lib:Name", "symbol": key | None, "sym_how": str}.
    A polarized capacitor (kind CP, or a C whose MPN is a tantalum / polymer / electrolytic series) gets nothing: the
    table has no polarized case, and a chip-capacitor land would lose the + mark."""
    if kind == "C" and POLAR_CAP.match(_norm(mpn)):
        kind = "CP"
    if kind == "CP":
        return {"footprint": None, "fp_how": "polarized capacitor: its case (tantalum / polymer / electrolytic) is not in "
                "the standard table", "kicad_ref": None, "symbol": None, "sym_how": "polarized capacitor: pin 1 = + only "
                "with a footprint that marks it"}
    fk = {"Q": None, "U": None}.get(kind, kind)
    name, how = footprint_for(list(texts), fk, mpn, weak=[description])
    if not name:                                          # a known MPN family settles a bare family name (SOT-223, TO-220)
        fam = next((pk for rx, pk, _ in FAMILY if re.match(rx, _norm(mpn))), ())
        said = " ".join(map(str, texts)) + " " + str(description)
        hit = [p for p in fam if re.search(FAMILY_PKG[p], said, re.I)]
        if len(hit) == 1 and not POWER_PKG.search(said) and not NO_GO.search(said) and not LAND_VARIANT.search(said) \
                and not EP_WORD.search(said):
            name, how = hit[0], f"{mpn}: its family comes in {hit[0]} (named as {re.search(FAMILY_PKG[hit[0]], said, re.I).group(0)})"
    sym, sym_how = symbol_kind(kind, mpn, name, description) if (name or kind in ("R", "C", "CP", "L", "LED")) else (None, "no standard package")
    return {"footprint": name, "fp_how": how, "kicad_ref": kicad_ref(name) if name else None, "symbol": sym, "sym_how": sym_how}


# ---------------------------------------------------------------- checks and CLI
def bench(reference, names=None):
    """Compare table entries with KiCad. reference(name, lib) -> numbers dict or None.
    -> (rows, summary) like footprint_bench.run."""
    from . import footprint_bench as B
    rows = []
    for n in names or sorted(packages()):
        ref = reference(n, packages()[n]["lib"])
        if ref is None:
            rows.append({"name": n, "error": "no reference"})
            continue
        r = B.compare(B.numbers(build(n)[0]), ref)
        rows.append({"name": n, **{k: r[k] for k in ("pads", "pads_ok", "all_pads_ok", "courtyard_ok", "courtyard_diff", "worst_pos", "worst_size")}})
    done = [r for r in rows if "error" not in r]
    return rows, {"footprints": len(done), "unavailable": len(rows) - len(done), "all_pads_ok": sum(r["all_pads_ok"] for r in done),
                  "courtyards_ok": sum(r["courtyard_ok"] for r in done),
                  "both_ok": sum(r["all_pads_ok"] and r["courtyard_ok"] for r in done)}


def coverage(boms, rules=None):
    """How many BOM lines the standard source resolves offline (footprint; symbol too). Reads the BOMs like triage."""
    from . import bom_triage as T, rules as RU
    out = []
    for b in boms:
        lines, _ = T.read_bom(b, RU.bom_columns(RU.resolve(rules, Path(b).parent)))
        for ln in lines:
            if str(ln.get("dnp") or "").strip() or not any(str(ln.get(k) or "").strip() for k in ("mpn", "value", "footprint")):
                continue
            cat = RU.REF_CLASS.get(re.match(r"[A-Za-z]*", str(ln.get("designator") or "")).group(0).upper())
            kind = part_kind(cat, ln.get("footprint"), ln.get("description"), ln.get("designator"))
            r = resolve([ln.get("footprint"), ln.get("value")], kind, ln.get("mpn") or "", ln.get("description") or "")
            out.append({"bom": str(b), "designator": str(ln.get("designator") or "")[:40], "mpn": ln.get("mpn") or "",
                        "footprint_text": ln.get("footprint") or "", "kind": kind or "", **r})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib stdparts", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="what the standard source would build for one part")
    r.add_argument("text", nargs="*", help="footprint / package text(s), in priority order")
    r.add_argument("--kind", choices=["R", "C", "CP", "L", "LED", "D", "F", "Q", "U"])
    r.add_argument("--mpn", default="")
    r.add_argument("--description", default="")
    r.add_argument("--kicad-mod", help="write the footprint here")
    l_ = sub.add_parser("list", help="the packages in the table")
    l_.add_argument("pattern", nargs="?", default="")
    b = sub.add_parser("bench", help="every table entry against KiCad's official footprint")
    b.add_argument("--kicad", help="a kicad-footprints checkout (default: fetch from GitLab through the HTTP cache)")
    b.add_argument("--fixture", help="numbers-only reference file (.json or .json.gz)")
    b.add_argument("--cache", default="out/http_cache")
    b.add_argument("--offline", action="store_true")
    c = sub.add_parser("coverage", help="BOM lines the standard source resolves")
    c.add_argument("bom", nargs="+")
    c.add_argument("--rules")
    for p in (r, l_, b, c):
        p.add_argument("--json", action="store_true", help="print JSON")
    a = ap.parse_args(argv)
    if a.cmd == "resolve":
        res = resolve(a.text, a.kind, a.mpn, a.description)
        if a.kicad_mod and res["footprint"]:
            Path(a.kicad_mod).write_text(build(res["footprint"])[0], encoding="utf-8", newline="\n")
        print(json.dumps(res, indent=2) if a.json else
              f"footprint: {res['kicad_ref'] or '-'} ({res['fp_how']})\nsymbol:    {res['symbol'] or '-'} ({res['sym_how']})")
        return 0 if res["footprint"] or res["symbol"] else 1
    if a.cmd == "list":
        names = [n for n in sorted(packages()) if a.pattern.lower() in n.lower()]
        print(json.dumps({n: packages()[n]["lib"] for n in names}, indent=2) if a.json else "\n".join(kicad_ref(n) for n in names))
        return 0
    if a.cmd == "bench":
        from . import footprint_bench as B
        if a.fixture:
            op = gzip.open if a.fixture.endswith(".gz") else open
            with op(a.fixture, "rt", encoding="utf-8") as f:
                fx = json.load(f)
            fx = fx.get("footprints", fx)
            ref = lambda n, lib: fx.get(n)
        elif a.kicad:
            ref = lambda n, lib: (B.numbers((Path(a.kicad) / f"{lib}.pretty" / f"{n}.kicad_mod").read_text(encoding="utf-8"))
                                  if (Path(a.kicad) / f"{lib}.pretty" / f"{n}.kicad_mod").exists() else None)
        else:
            from .httpcache import CachedHTTP
            http = CachedHTTP(a.cache, min_interval=0.3, offline=a.offline, ttl_s=30 * 86400)
            ref = lambda n, lib: B.reference(n, lib, http)
        rows, s = bench(ref)
        if a.json:
            print(json.dumps({"summary": s, "rows": rows}, indent=2))
        else:
            for row in rows:
                if "error" in row or not (row["all_pads_ok"] and row["courtyard_ok"]):
                    print(f"  {row['name']}: " + (row.get("error") or f"pads {row['pads_ok']}/{row['pads']}, courtyard diff {row['courtyard_diff']}"))
            print(f"{s['both_ok']}/{s['footprints']} match KiCad (pads and courtyard); {s['unavailable']} without a reference")
        return 0 if s["both_ok"] == s["footprints"] and s["footprints"] else 1
    rows = coverage(a.bom, a.rules)
    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"  {row['designator']:<16} {row['mpn'][:24]:<24} {row['footprint_text'][:28]:<28} -> {row['kicad_ref'] or '-'}"
                  f"  sym {row['symbol'] or '-'}  ({row['fp_how']})")
        fp = sum(bool(r_["footprint"]) for r_ in rows)
        both = sum(bool(r_["footprint"] and r_["symbol"]) for r_ in rows)
        print(f"{len(rows)} lines: footprint for {fp}, footprint and symbol for {both}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
