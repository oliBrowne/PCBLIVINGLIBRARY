"""Index a local KiCad library directory (*.kicad_sym, *.pretty/*.kicad_mod) for BOM matching."""
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import sexpr

MPN_KEYS = {"mpn", "manufacturer part number", "mfr part number", "mfr_pn", "mpn1", "part number", "manufacturer_part_number"}
LCSC_KEYS = {"lcsc", "lcsc part", "lcsc part #", "lcsc_part", "jlcpcb part #", "jlc"}
SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1.0, "r": 1.0,
      "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
PKG_RE = re.compile(r"(?<![0-9])(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?![0-9])|"
                    r"(SOT-?23(?:-\d)?|SOT-?223|SOIC-?\d+|SOP-?\d+|TSSOP-?\d+|QFN-?\d+|VQFN-?\d+|LQFP-?\d+|SMA|SMB|SOD-?\d+)", re.I)


def norm_mpn(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


_UNIT = r"((?i:ohms?|[fhω]))?(?![A-Za-z0-9])"   # optional unit, then the number must end (74HC595 is not 74)


def parse_value(s, with_unit=False):
    """'10k' / '10kΩ' / '4k7' / '100nF' / '0.1uF' / '10K 1%' / '0R' -> float, else None.
    with_unit=True -> (value, unit) with unit in 'F', 'H', 'R' (ohm) or '' when not stated."""
    s = re.sub(r"(?i)(?<=\d)meg", "M", str(s or "").strip())
    m = re.match(r"^(\d+)([pnuµμmkKMGR])(\d+)" + _UNIT, s)          # 4k7 / 4R7 notation
    if m:
        v, pre, u = float(f"{m.group(1)}.{m.group(3)}"), m.group(2), m.group(4)
    else:
        m = re.match(r"^(\d+(?:\.\d+)?)\s*([pnuµμmkKMGR]?)" + _UNIT, s)
        if not m:
            return (None, "") if with_unit else None
        v, pre, u = float(m.group(1)), m.group(2), m.group(3)
    v *= SI["r" if pre == "R" else pre]
    u = "R" if pre == "R" or (u or "").lower()[:1] in ("o", "ω") else (u or "").upper()
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
                    txt = m.read_text(errors="replace")
                    self.footprints[f"{d.stem}:{m.stem}"] = {"path": str(m), "has_3d": "(model " in txt}

    def _load_sym(self, f):
        try:
            tree = sexpr.parse(f.read_text(errors="replace"))
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

    def match(self, mpn="", lcsc="", value="", package="", footprint=""):
        """Return (symbol, how) - by MPN, LCSC number, or (for passives) value + package."""
        nm = norm_mpn(mpn)
        for s in self.symbols:
            if nm and nm in (norm_mpn(s.name), norm_mpn(s.mpn)):
                return s, "mpn"
        for s in self.symbols:
            if lcsc and s.lcsc.upper() == lcsc.upper():
                return s, "lcsc"
        v, u = parse_value(value, True)
        if v is not None and package:
            for s in self.symbols:
                sv, su = parse_value(s.props.get("Value"), True)
                if (sv is not None and abs(sv - v) <= 1e-6 * v and not (u and su and u != su)     # 0R == 0; 10uF != 10uH
                        and package_of(s.props.get("Footprint"), s.name) == package):
                    return s, "value+package"
        return None, None

    CLASS_PREFIX = {"R": ("R_",), "C": ("C_",), "L": ("L_",), "FB": ("L_", "FB_"), "D": ("D_", "LED_"), "LED": ("LED_",)}

    def find_footprint_for_package(self, package, ref_class=""):
        """Best-effort: an indexed footprint whose name mentions the package (e.g. 0603, SOIC8).
        For 2-terminal passives the footprint family must match the designator class
        (an LED 0603 must not land on R_0603)."""
        if not package:
            return None
        pref = self.CLASS_PREFIX.get(ref_class)
        for k in self.footprints:
            name = k.split(":")[-1]
            if package_of(name) == package and (pref is None or name.startswith(pref)):
                if pref is None and re.match(r"(R|C|L|D|LED)_", name):
                    continue                      # don't hand a passive footprint to an IC/connector
                return k
        return None
