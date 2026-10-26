"""Index a local KiCad library directory (*.kicad_sym, *.pretty/*.kicad_mod) for BOM matching."""
import re, unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import sexpr

MPN_KEYS = {"mpn", "manufacturer part number", "mfr part number", "mfr_pn", "mpn1", "part number", "manufacturer_part_number"}
LCSC_KEYS = {"lcsc", "lcsc part", "lcsc part #", "lcsc_part", "jlcpcb part #", "jlc"}
SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1.0, "r": 1.0,
      "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
PKG_RE = re.compile(r"(?<![0-9])(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?![0-9])|"
                    r"(?<![A-Z0-9])(SOT-?23(?:-\d)?|SOT-?223|SOIC-?\d+|SOP-?\d+|TSSOP-?\d+|QFN-?\d+|VQFN-?\d+|LQFP-?\d+|SMA|SMB|SOD-?\d+)(?![A-Z0-9])", re.I)
EP_NAME = re.compile(r"-\d*EP(?=[_-]|$)|_EP\d", re.I)              # KiCad's exposed-pad lands: QFN-16-1EP_..._EP1.7x1.7mm
EP_HINT = re.compile(r"\bEPAD\b|-\d*EP\b|\bEP\b|exposed|thermal ?pad|power ?pad|heat ?sink ?pad", re.I)   # not 'pad'
                                                                    # alone ('touch pad controller')
# KiCad's copies of one land: a hand-solder land (longer pads), thermal vias in the exposed pad, castellated pads
LAND_VARIANT = re.compile(r"(?:_Pad\d+(?:\.\d+)?x\d+(?:\.\d+)?mm)?_Hand[_-]?Solder(?:ing)?$|_ThermalVias$|_Castellated$", re.I)
NO_LEAD = re.compile(r"QFN|DFN|SON|MLF|MLP|LGA", re.I)            # leadless: an exposed pad is common and BOM text rarely
                                                                  # says so


def plain_land(name):
    """A footprint name without KiCad's variant suffix: R_0603_1608Metric_Pad0.98x0.95mm_HandSolder -> R_0603_1608Metric."""
    while LAND_VARIANT.search(name):
        name = LAND_VARIANT.sub("", name)
    return name
BODY_RE = re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:x\d+(?:\.\d+)?)?mm", re.I)


DECIMAL_POINT = re.compile(r"(?<=\d)\.(?=\d)")


def norm_mpn(s, dots=True):
    """An MPN without case and punctuation, for comparing: 'LM358-DR' == 'lm358dr'. The micro sign is U (µA741CN ==
    UA741CN), fullwidth / accented letters fold to ASCII; a letter that has no ASCII form is kept, never dropped
    (µA741CN must not become A741CN, another part). A decimal point between two digits is kept: SMAJ5.0A (a 5 V TVS)
    is not SMAJ50A (a 50 V one), 1.5KE6.8A is not 1.5KE68A. dots=False drops it too, for reading a file name or a
    text one character at a time (datasheet file names), never for deciding two MPNs are one part."""
    s = unicodedata.normalize("NFKD", str(s or "")).replace("µ", "U").replace("μ", "U")\
        .replace("Μ", "U")                                      # micro sign / mu ('µ'.upper() is a capital mu)
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    if dots and "." in s:
        return ".".join(norm_mpn(t, False) for t in DECIMAL_POINT.split(s))
    if not s.isascii():
        return re.sub(r"[\W_]", "", s)
    return re.sub(r"[^A-Z0-9]", "", s)


_UNIT = r"((?i:ohms?|hz|[fhωvwa]))?(?![A-Za-z0-9])"   # optional unit, then the number must end (74HC595 is not 74)
UNITS = {"o": "R", "ω": "R", "f": "F", "h": "H", "v": "V", "w": "W", "a": "A", "hz": "Hz"}


def split_value(s):
    """'100nF 50V' -> (1e-07, 'F', '50V'): the leading value in base units, its unit ('F', 'H', 'R' ohm, 'V', 'W', 'A',
    'Hz', or '' when not stated) and the rest of the text; (None, '', text) when it does not start with a value."""
    s = re.sub(r"(?i)(?<=\d)meg", "M", str(s or "").strip())
    m = re.match(r"^(\d+)([pnuµμmkKMGR])(\d+)" + _UNIT, s)          # 4k7 / 4R7 notation
    if m:
        v, pre, u = float(f"{m.group(1)}.{m.group(3)}"), m.group(2), m.group(4)
    elif re.match(r"^(\d+)([VvWwAa])(\d+)(?![A-Za-z0-9])", s):        # 3V3 / 1A5 notation
        m = re.match(r"^(\d+)([VvWwAa])(\d+)", s)
        v, pre, u = float(f"{m.group(1)}.{m.group(3)}"), "", m.group(2)
    else:
        m = re.match(r"^(\d+(?:[.,]\d+)?)(?![.,]?\d)\s*([pnuµμmkKMGR]?)" + _UNIT, s)  # '4,7k' / '2,2µF': European
                                              # decimals; the number never stops short of its decimals (3.3V is not 3)
        if not m:
            return None, "", s
        v, pre, u = float(m.group(1).replace(",", ".")), m.group(2), m.group(3)
    v *= SI["r" if pre == "R" else pre]
    u = "R" if pre == "R" else UNITS.get((u or "").lower(), UNITS.get((u or "").lower()[:1], ""))
    return v, u, s[m.end():].strip()


COMMA_3_RE = re.compile(r"\s*([1-9]\d{0,2}),(\d{3})(?![\d,.])")


def two_readings(s):
    """'4,700pF' -> ('4.7pF', '4700pF'): a leading number with a comma before exactly three digits is a decimal comma
    in Europe and a thousands separator elsewhere (as change_diff._readings), so the value is ambiguous; None for any
    other value ('4,7pF', '0,100uF', '4,700.5pF' read one way). split_value takes the decimal comma: what these
    values store does not change, but nothing should pick, rename or merge a part by such a value."""
    s = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in str(s or ""))   # '４,７００pF': every digit
    m = COMMA_3_RE.match(s)                                                                   # split_value's \d reads
    if not m:
        return None
    rest, dec = s[m.end():].strip(), m.group(2).rstrip("0")
    return m.group(1) + ("." + dec if dec else "") + rest, m.group(1) + m.group(2) + rest


def parse_value(s, with_unit=False):
    """'10k' / '10kΩ' / '4k7' / '100nF' / '0.1uF' / '10K 1%' / '0R' / '3.3V' -> float, else None.
    with_unit=True -> (value, unit) with unit in 'F', 'H', 'R' (ohm), 'V', 'W', 'A', 'Hz' or '' when not stated."""
    v, u, _ = split_value(s)
    if v is None:
        return (None, "") if with_unit else None
    return (v, u) if with_unit else v


IPC_CHIP_RE = re.compile(r"(?:RES|CAP|IND|LED|DIO)C(\d{4})X\d+", re.I)    # IPC-7351 chip names: RESC1608X55N
METRIC_TO_IMPERIAL = {"0402": "01005", "0603": "0201", "1005": "0402", "1608": "0603", "2012": "0805",
                      "3216": "1206", "3225": "1210", "4532": "1812", "5025": "2010", "6332": "2512"}


def package_of(*texts):
    for t in texts:
        m = IPC_CHIP_RE.search(str(t or ""))
        if m and m.group(1) in METRIC_TO_IMPERIAL:
            return METRIC_TO_IMPERIAL[m.group(1)]
        m = PKG_RE.search(str(t or ""))
        if m:
            return (m.group(1) or m.group(2)).upper().replace("-", "")
    return None


def _body(text):
    m = BODY_RE.search(str(text or ""))
    return tuple(sorted((float(m.group(1)), float(m.group(2))))) if m else None


@dataclass
class Symbol:
    lib: str
    name: str
    props: dict = field(default_factory=dict)

    @property
    def mpn(self):
        return next((v for k, v in self.props.items() if k.lower() in MPN_KEYS), "")

    @property
    def lcsc(self):
        return next((v for k, v in self.props.items() if k.lower() in LCSC_KEYS), "")


class KiCadIndex:
    def __init__(self, roots):
        self.symbols, self.footprints = [], {}       # footprints: "lib:name" -> {"path", "has_3d"}
        for root in roots:
            root = Path(root)
            for f in root.rglob("*.kicad_sym"):
                self._load_sym(f)
            for d in root.rglob("*.pretty"):
                for m in d.glob("*.kicad_mod"):
                    txt = m.read_text(encoding="utf-8", errors="replace")
                    self.footprints[f"{d.stem}:{m.stem}"] = {"path": str(m), "has_3d": "(model " in txt}

    def _load_sym(self, f):
        try:
            tree = sexpr.parse(f.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            return
        for s in sexpr.find(tree, "symbol"):
            props = {p[1]: p[2] for p in sexpr.find(s, "property") if len(p) > 2}
            self.symbols.append(Symbol(f.stem, s[1], props))

    def footprint(self, ref):
        """Resolve 'Lib:Name' (or bare name) to an indexed footprint."""
        if not ref:
            return None, None
        if ref in self.footprints:
            return ref, self.footprints[ref]
        name = ref.split(":")[-1]
        for k, v in self.footprints.items():
            if k.split(":")[-1] == name:
                return k, v
        return None, None

    def match(self, mpn="", lcsc="", value="", package="", footprint="", ref_class=""):
        """Return (symbol, how) - by MPN, LCSC number, or (for passives) value + package.
        ref_class (R, C, L...) keeps a C 10u 0805 line off an L 10u 0805 symbol."""
        nm = norm_mpn(mpn)
        for s in self.symbols:
            if nm and nm in (norm_mpn(s.name), norm_mpn(s.mpn)):
                return s, "mpn"
        for s in self.symbols:
            if lcsc and s.lcsc.upper() == lcsc.upper():
                return s, "lcsc"
        v, u = parse_value(value, True)
        if v is not None and package and not two_readings(value):    # '4,700pF': 4.7 pF or 4.7 nF, so neither
            for s in self.symbols:
                sv, su = parse_value(s.props.get("Value"), True)
                if (sv is not None and abs(sv - v) <= 1e-6 * v and not (u and su and u != su)     # 0R == 0; 10uF != 10uH
                        and not two_readings(s.props.get("Value"))
                        and package_of(s.props.get("Footprint"), s.name) == package and self._same_class(s, ref_class)):
                    return s, "value+package"
        return None, None

    CLASS_PREFIX = {"R": ("R_",), "C": ("C_",), "L": ("L_",), "FB": ("L_", "FB_"), "D": ("D_", "LED_"), "LED": ("LED_",)}

    def _same_class(self, s, ref_class):
        if not ref_class:
            return True
        sref = re.match(r"[A-Z]+", str(s.props.get("Reference", "")).upper())
        if sref:
            return sref.group(0) == ref_class or (ref_class, sref.group(0)) in (("FB", "L"), ("LED", "D"), ("D", "LED"))
        pref = self.CLASS_PREFIX.get(ref_class)
        fp = str(s.props.get("Footprint", "")).split(":")[-1]
        return pref is None or not fp or fp.startswith(pref)

    def find_footprint_for_package(self, package, ref_class="", hint=""):
        """Best-effort: an indexed footprint whose name mentions the package (e.g. 0603, SOIC8).
        For 2-terminal passives the footprint family must match the designator class
        (an LED 0603 must not land on R_0603)."""
        if not package:
            return None
        pref, hits = self.CLASS_PREFIX.get(ref_class), []
        array = re.search(r"array|network|\b\d+ ?x ?\d{4}\b", str(hint or ""), re.I)
        for k in self.footprints:
            name = k.split(":")[-1]
            if package_of(name) == package and (pref is None or name.startswith(pref)):
                if pref is None and re.match(r"(R|C|L|D|LED)_", name):
                    continue                      # don't hand a passive footprint to an IC/connector
                if "_Array_" in name and not array:
                    continue                      # R_Array_Convex_4x0603 is four resistors, asked for by name only
                hits.append(k)
        names = {k.split(":")[-1] for k in hits}
        hits = [k for k in hits if plain_land(k.split(":")[-1]) == k.split(":")[-1]
                or plain_land(k.split(":")[-1]) not in names]   # the hand-solder / thermal-vias copy of a land that
                                                                # is there too is no second choice: the plain one
        bodies = {k: _body(plain_land(k)) for k in hits}
        want = _body(hint)
        if want:                                  # body size known: it must agree (QFN-32 4x4 is not 5x5)
            hits = [k for k in hits if bodies[k] in (want, None)]
        elif len({b for b in bodies.values() if b}) > 1:
            return None                           # several body sizes and nothing to choose by: ambiguous
        if len(hits) > 1:                         # one body, several lands (with / without an exposed pad, EP sizes):
            ep = bool(EP_HINT.search(str(hint or "")))       # the one the line's words name, never the first indexed
            has = {bool(EP_NAME.search(k.split(":")[-1])) for k in hits}
            if not ep and has == {True, False} and any(NO_LEAD.search(k.split(":")[-1]) for k in hits):
                return None                       # 'QFN-16 3x3mm' saying nothing of a pad: most have one, some not
            hits = [k for k in hits if bool(EP_NAME.search(k.split(":")[-1])) == ep]
            if len(hits) > 1:
                return None
        return hits[0] if hits else None
