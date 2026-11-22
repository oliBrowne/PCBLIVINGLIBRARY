"""The value, chip size and kind a resistor's or capacitor's maker part number states by its maker's published ordering
code, read offline: RC0603FR-0710KL is a 10 kOhm 0603 resistor, GRM188R71H104KA93D a 100 nF 0603 capacitor,
T491A106K016AT a 10 uF tantalum in KEMET case A (EIA 3216-18).

Only whole part numbers that fit a series' code exactly are read (each rule is anchored at both ends); anything else
reads as nothing, never as a guess. Used by the librarian to fill an empty Value on a part-number-only BOM line and to
hold a line whose Value names another value than its part number (fill_lines).

    python3 -m ecadlib.mpn_decode RC0603FR-0710KL GRM188R71H104KA93D
"""
import re
import sys

IMP = r"(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)"
METRIC_SIZE = {"0402": "01005", "0603": "0201", "1005": "0402", "1608": "0603", "2012": "0805", "3216": "1206",
               "3225": "1210", "4532": "1812", "5025": "2010", "5750": "2220", "6432": "2512"}
M2 = {"03": "0201", "15": "0402", "18": "0603", "21": "0805", "31": "1206", "32": "1210", "43": "1812", "55": "2220"}   # Murata
CL = {"03": "0201", "05": "0402", "10": "0603", "21": "0805", "31": "1206", "32": "1210", "43": "1812", "55": "2220"}   # Samsung
ERJ = {"XG": "01005", "1G": "0201", "2": "0402", "3": "0603", "6": "0805", "8": "1206", "14": "1210", "12Z": "2010",
       "12": "1812", "1T": "2512", "P03": "0603", "P06": "0805", "P08": "1206", "PA2": "0402", "PA3": "0603",
       "PA6": "0805", "PA8": "1206"}
KOA = {"1H": "0201", "1E": "0402", "1J": "0603", "2A": "0805", "2B": "1206", "2E": "1210", "2H": "2010", "3A": "2512"}
# tantalum case letter -> EIA metric case (KiCad CP_EIA-<case>_Kemet-<letter> / _AVX-<letter>): the cases both makers
# give the same size and height for; others (KEMET E, R, S, T, U, V, W, X; AVX E, H, K ...) read as no case
TANT_CASE = {"A": "3216-18", "B": "3528-21", "C": "6032-28", "D": "7343-31"}

RKM = r"(\d+[RKM]\d*|[RKM]\d+)"                      # 10K, 4K7, 100R, 0R1, R047, 1M
EIA_R = r"(\d{3,4}|\d+R\d+|R\d+)"                    # 103, 1002, 4R7, 49R9, R10 (ohms)
EIA_C = r"(\d{3}|\dR\d|R\d\d)"                       # 104, 1R0, R50 (picofarads)

# (series, kind, regex, size group or None, size map | None (imperial as written) | 'metric', value group, value code)
RULES = [
    ("Yageo RC/AC/AF/RT/RL/PT/PE", "R",
     re.compile(r"^(?:RC|AC|AF|RT|RL|PT|PE)" + IMP + r"[BCDFGJK][RKSP](?:-|[A-Z])(?:\d\d|\d[A-Z])" + RKM + r"L$"), 1, None, 2, "rkm"),
    ("Samsung RC", "R", re.compile(r"^RC(0402|0603|1005|1608|2012|3216|3225|5025|6432)[BDFGJ]" + EIA_R + r"CS$"), 1, "metric", 2, "eia"),
    ("Panasonic ERJ", "R", re.compile(r"^ERJ-?(XG|1G|1T|12Z|12|14|PA[2368]|P0[368]|[2368])[A-Z]{0,3}?"
                                      r"(?:[BDF](\d{4}|\d{1,2}R\d{1,2}|R\d{2,3})|[GJ](\d{3}|\dR\d|R\d\d))[A-Z]$"), 1, ERJ, (2, 3), "eia"),
    ("Vishay CRCW/TNPW/RCG/RCS", "R",
     re.compile(r"^(?:CRCW|TNPW|RCG|RCS)" + IMP + r"(?=[0-9RKM]{4}[BCDFGJ])(\d{1,3}[RKM]\d{0,3})[BCDFGJ][A-Z0-9]{1,4}$"), 1, None, 2, "rkm"),
    ("Stackpole RMCF/RMCP", "R", re.compile(r"^RMC[FP]" + IMP + r"[BDFGJ][TG]" + RKM + r"$"), 1, None, 2, "rkm"),
    ("Bourns CR", "R", re.compile(r"^CR" + IMP + r"-[BDFJ][XW]-" + EIA_R + r"E?LF$"), 1, None, 2, "eia"),
    ("KOA RK73", "R", re.compile(r"^RK73[HB](1H|1E|1J|2A|2B|2E|2H|3A)TT[DP]" + EIA_R + r"[BDFGJ]$"), 1, KOA, 2, "eia"),
    ("UNI-ROYAL", "R", re.compile(r"^" + IMP + r"W[A-Z][BDFGJ](\d{4})T5E$"), 1, None, 2, "eia"),
    ("Murata GRM/GCM/GJM/GRT/GCJ/GQM", "C",
     re.compile(r"^(?:GRM|GCM|GJM|GRT|GCJ|GCQ|GQM)(03|15|18|21|31|32|43|55)[0-9A-Z][0-9A-Z]{2}[0-9A-Z]{2}" + EIA_C +
                r"[BCDFGJKMWZ][0-9A-Z]{2,4}$"), 1, M2, 2, "eia"),
    ("Samsung CL", "C", re.compile(r"^CL(03|05|10|21|31|32|43|55)[A-Z]" + EIA_C + r"[ABCDFGJKMZ][A-Z][0-9A-Z]{5}$"), 1, CL, 2, "eia"),
    ("TDK C", "C", re.compile(r"^C(0402|0603|1005|1608|2012|3216|3225|4532|5750)(?:C0G|NP0|X[5-8][RSTPV]|JB|CH)\d[A-Z]" + EIA_C +
                              r"[BCDFGJKMZ]\d{3}[A-Z]{2}$"), 1, "metric", 2, "eia"),
    ("KEMET C", "C", re.compile(r"^C" + IMP + r"[CX](\d{3}|\dR\d)[BCDFGJKMZ]\d[A-Z]A[CL](?:TU|AUTO|\d{4}|7\d{3})?$"), 1, None, 2, "eia9"),
    ("Yageo CC", "C", re.compile(r"^CC" + IMP + r"[BCDFGJKMZ][RKP](?:NPO|COG|C0G|X7R|X5R|X7S|X6S|X8R|Y5V)\dB[BN]" + EIA_C + r"$"),
     1, None, 2, "eia"),
    ("KYOCERA AVX", "C", re.compile(r"^" + IMP + r"[0-9YZ][ACDGUZ](\d{3}|\dR\d)[BCDFGJKMZ]A[T7J][0-9A-Z]{2}$"), 1, None, 2, "eia9"),
    ("KEMET T491", "CP", re.compile(r"^T491([ABCD])(\d{3})[JKM](\d{3})A[TS]\d{0,4}$"), 1, TANT_CASE, 2, "eia_uf"),
    ("KYOCERA AVX TAJ", "CP", re.compile(r"^TAJ([ABCD])(\d{3})[JKM](\d{3})R[NJ]J?$"), 1, TANT_CASE, 2, "eia_uf"),
]


def _rkm(code):
    m = re.fullmatch(r"(\d*)([RKM])(\d*)", code)
    if not m or not (m.group(1) or m.group(3)):
        return None
    mult = {"R": 1, "K": 1e3, "M": 1e6}[m.group(2)]
    return float(f"{m.group(1) or '0'}.{m.group(3) or '0'}") * mult


def _eia(code, low_mult=False):
    """'103' -> 10e3, '1002' -> 100e2, '4R7' -> 4.7 (the code's own unit); low_mult: a last digit 8 / 9 is x0.01 /
    x0.1 (KEMET, AVX: 109 = 1.0 pF). None for a code no maker writes ('107' as 7 is a 10^7 multiplier on a resistor:
    left to the caller's range check)."""
    if "R" in code:
        a, _, b = code.partition("R")
        return float(f"{a or '0'}.{b or '0'}")
    if not code.isdigit():
        return None
    digits, power = code[:-1], int(code[-1])
    if low_mult and power in (8, 9):
        return int(digits) * (0.01 if power == 8 else 0.1)
    if power > 7:
        return None
    return int(digits) * 10 ** power


def decode(mpn):
    """-> {"kind": "R" | "C" | "CP", "value": base units (ohm / farad), "size": imperial chip size or None, "case": EIA
    tantalum case ('3216-18') or None, "series": maker series} for a part number one rule reads whole, else None."""
    s = re.sub(r"\s+", "", str(mpn or "")).upper()
    if not s:
        return None
    for series, kind, rx, sg, smap, vg, how in RULES:
        m = rx.match(s)
        if not m:
            continue
        code = next((m.group(g) for g in (vg if isinstance(vg, tuple) else (vg,)) if m.group(g)), None)
        if code is None:
            return None
        if how == "rkm":
            v = _rkm(code)
        elif how == "eia_uf":
            v = _eia(code)
            v = v * 1e-12 if v is not None else None             # tantalum codes are pF too: 106 = 10 uF
        else:
            v = _eia(code, low_mult=how == "eia9")
            if v is not None and kind == "C":
                v *= 1e-12
        if v is None:
            return None
        raw = m.group(sg)
        size = case = None
        if kind == "CP":
            case = smap.get(raw)
            if not case:
                return None
        else:
            size = METRIC_SIZE.get(raw) if smap == "metric" else smap.get(raw) if isinstance(smap, dict) else raw
            if not size:
                return None
        if kind == "R" and not (v == 0 or 1e-3 <= v <= 1e9):
            return None
        if kind in ("C", "CP") and not (1e-13 <= v <= 1e-2):
            return None
        return {"kind": kind, "value": v, "size": size, "case": case, "series": series}
    return None


def value_text(d):
    """'10kΩ', '4.7kΩ', '0Ω', '100nF', '10uF' for a decode() result (a resistor with its Ω: a bare '100' is no value)."""
    from . import rules as RU
    if d["kind"] == "R":
        return (RU.fmt_number(d["value"], "Resistors", "si_unit") if d["value"] else "0") + "Ω"
    return RU.fmt_number(d["value"], "Capacitors", "si_unit")


KIND_PREFIX = {"R": ("R",), "C": ("C",), "CP": ("C",)}    # the designator letters a decoded kind may sit on


# a number with a unit in a description: the value it names, read loosely (a hold, never a fill, follows from it)
NAMES_A_VALUE = {
    "Resistors": re.compile(r"(?i)(?<![A-Za-z\d.,])\d+(?:[.,]\d+)?\s*(?:[kmg]?\s*(?:Ω|ohms?\b)|[kmgr](?![a-z])|[kmr]\d)"),
    "Capacitors": re.compile(r"(?i)(?<![A-Za-z\d.,])\d+(?:[.,]\d+)?\s*(?:[pnuµμm]\s*f?|f)(?![a-z])"),
}


def fill_lines(lines):
    """For each BOM line whose part number decode() reads, on a designator of that kind (R1 for a resistor code, C1 for
    a capacitor's): an empty Value is filled with the value the part number states (ln['value_from_mpn'] records the
    series), and a Value that names another value is held (ln['bom_hold']), since one of the two is a slip. A Value
    that reads as no value of the kind ('104', a rating) is left to the release check. -> the number of lines filled."""
    from . import release_check as RC
    from .librarian import passive_value
    filled = 0
    for ln in lines:
        mpn = str(ln.get("mpn") or "").strip()
        d = decode(mpn) if mpn else None
        if not d:
            continue
        refs = re.findall(r"[A-Za-z]+(?=\d)", str(ln.get("designator") or ""))
        if not refs or any(r.upper() not in KIND_PREFIX[d["kind"]] for r in refs):
            continue                                  # no designator, or one of another kind (an R code on C1): not ours
        text, value = value_text(d), str(ln.get("value") or "").strip()
        cat = "Resistors" if d["kind"] == "R" else "Capacitors"
        if not RC._filled(value):
            from .altium_export import description_values
            said = {float(v) for (c, v) in description_values(ln.get("description"), cat) if c == cat}
            other = [v for v in said if abs(v - d["value"]) > 1e-6 * max(abs(v), abs(d["value"]), 1e-15)]
            if other:                             # 'RES 4.7K or 10K' on a 4.7 kOhm code: the BOM says another value
                hold = (f"the description {str(ln.get('description'))[:60]!r} names another value than the part "
                        f"number {mpn} ({text} by its {d['series']} code): fix the BOM line")
                ln["bom_hold"] = f"{ln['bom_hold']}; {hold}" if ln.get("bom_hold") else hold
                continue
            if said:                              # the description says it: the plan takes it in the BOM's spelling
                continue
            if NAMES_A_VALUE[cat].search(str(ln.get("description") or "")):   # 'RES 4.7 kOhm', '4,7 kΩ': a value the
                hold = (f"the description {str(ln.get('description'))[:60]!r} names a value the tool cannot read "  # reader
                        f"exactly; the part number {mpn} is {text} by its {d['series']} code: fill the Value")  # misses
                ln["bom_hold"] = f"{ln['bom_hold']}; {hold}" if ln.get("bom_hold") else hold
                continue
            ln["value"], ln["value_from_mpn"] = text, f"part number {mpn} ({d['series']} code)"
            filled += 1
            continue
        v = passive_value(value, cat)
        if v is None and cat == "Capacitors":
            v = passive_value(value.lower(), cat)     # '100N', '10U': BOM capitals
        if v is None and cat == "Resistors" and re.fullmatch(r"\d+(?:\.\d+)?", value):
            v = float(value)                          # a resistor's bare '430' is 430 ohm (JLCPCB's matched BOM:
                                                      # Value 430 on a 43 kOhm 0603WAF4302T5E was released)
        if v is not None and abs(v - d["value"]) > 1e-6 * max(abs(v), abs(d["value"]), 1e-15):
            hold = (f"Value {value} but the part number {mpn} is {text} by its {d['series']} code: one of the two is "
                    "wrong; fix the BOM line")
            ln["bom_hold"] = f"{ln['bom_hold']}; {hold}" if ln.get("bom_hold") else hold
    return filled


def main(argv=None):
    for a in (sys.argv[1:] if argv is None else argv):
        d = decode(a)
        print(a, "->", f"{d['kind']} {value_text(d)} {d['size'] or d['case']} ({d['series']})" if d else "not read")


if __name__ == "__main__":
    main()
