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


def parse_value(s):
    """'10k' / '10kΩ' / '4k7' / '100nF' / '0.1uF' / '10K 1%' -> float, else None."""
    s = str(s or "").strip().replace("Ω", "").replace("ohm", "")
    m = re.match(r"^(\d+)([pnuµμmkKMGR])(\d+)", s)                 # 4k7 / 4R7 notation
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * SI[m.group(2) if m.group(2) != "R" else "r"]
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([pnuµμmkKMGR]?)", s)
    if not m:
        return None
    pre = m.group(2)
    return float(m.group(1)) * SI.get(pre if pre not in ("R",) else "r", 1.0)


def package_of(*texts):
    for t in texts:
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
        v = parse_value(value)
        if v is not None and package:
            for s in self.symbols:
                sv = parse_value(s.props.get("Value"))
                if sv and abs(sv - v) <= 1e-6 * v and package_of(s.props.get("Footprint"), s.name) == package:
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
