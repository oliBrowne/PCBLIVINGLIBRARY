"""Altium Database Library (DbLib) export prototype.

Altium .SchLib/.PcbLib are binary OLE compound files, so instead of writing them we emit
the parts *database* Altium reads through a .DbLib file (OLE DB / ODBC):

    out/altium/parts.sqlite      one table per category (SQLite; needs the SQLite3 ODBC driver on Windows)
    out/altium/parts.xlsx        same tables as sheets (Excel via the ACE OLE DB provider; tables are 'Sheet$')
    out/altium/ecadlib_sqlite.DbLib, ecadlib_excel.DbLib   INI files pointing Altium at the above

    python -m ecadlib.altium_export examples/sample_bom.csv -o out/altium --draft [--triage out/triage_sample_bom.csv]
    python -m ecadlib.altium_export out/lib/plan.json -o out/altium   # release-checked parts only (dblib-sync)

Column names use Altium's reserved DbLib names ([Library Ref], [Library Path], [Footprint Ref],
[Footprint Path], [Description]) plus parameters (Part Number = key, Value, Comment, Manufacturer,
Manufacturer Part Number, Supplier 1/Supplier Part Number 1, ComponentLink1URL/Description).
See docs/altium.md for what is verified vs assumed about the .DbLib INI keys."""
import argparse, re, sqlite3, sys
from contextlib import closing
from pathlib import Path, PureWindowsPath

import pandas as pd

from .altium_lib import file_stem, lib_name, not_cp1252, storable
from .bom_triage import placeholder_mpn, read_bom
from .kicad_index import norm_mpn, package_of, parse_value, two_readings

PLAIN_VALUE = re.compile(r"(\d+(?:[.,]\d+)?\s*[pnuµμmkKMGR]?|\d+[pnuµμmkKMGR]\d+)\s*(?:F|H|Ω|R|ohms?)?", re.I)   # '4k7',
                                                                  # '100nF': only a value (not '100nF 50V')

COLUMNS = ["Part Number", "Library Ref", "Library Path", "Footprint Ref", "Footprint Path",
           "Description", "Value", "Comment", "Manufacturer", "Manufacturer Part Number",
           "Supplier 1", "Supplier Part Number 1", "Supplier 2", "Supplier Part Number 2",
           "ComponentLink1Description", "ComponentLink1URL", "ComponentLink2Description", "ComponentLink2URL"]
RESERVED = {"Library Ref", "Library Path", "Footprint Ref", "Footprint Path", "Description"}
CATEGORY = {"R": "Resistors", "C": "Capacitors", "L": "Inductors", "D": "Diodes", "Q": "Transistors",   # whole prefix:
            "Y": "Crystals", "J": "Connectors", "P": "Connectors", "U": "ICs",                         # LED1 is not an L,
            "LED": "Diodes", "IC": "ICs", "CN": "Connectors", "JP": "Connectors"}                      # CN1 not a C
GENERIC_SYMBOL = {"Resistors": "Resistor", "Capacitors": "Capacitor", "Inductors": "Inductor"}
# what a source's category says (EasyEDA / LCSC "Resistors, R0603", "Chip Resistor - Surface Mount") or a chip
# footprint / package (R_0603_1608Metric, R0603), for a line whose designator says nothing (the menu's "Add part from
# MPN" writes U1, U2 ...). The category noun ends the phrase: "MOSFET driver", "Resistor Networks" are not one.
KIND_WORDS = (("Resistors", r"resistors?"), ("Capacitors", r"capacitors?"), ("Inductors", r"inductors?|ferrite beads?"),
              ("Crystals", r"crystals?"), ("Connectors", r"connectors?"))
KIND_FP = {"R": "Resistors", "C": "Capacitors", "L": "Inductors"}
IC_PACKAGE = (r"(?i)(?:SOT|SOIC|H?T?SSOP|V?SSOP|MSOP|SOP|[VWU]?QFN|[VWU]?DFN|[VWU]?SON|[LT]?QFP|F?BGA|LGA|W?LCSP|"
              r"P?DIP|TO-|SC-?70|SOD)")
CHIP_SIZES = "01005|0201|0402|0603|0805|1008|1206|1210|1812|2010|2220|2512"


# a distributor's own category name (LCSC / Mouser / JLCPCB write it inside the text: "... 0402 Multilayer Ceramic
# Capacitors MLCC - SMD/SMT ROHS", "... N-Channel SOT-23 MOSFETs ROHS"), matched as written (title case, whole words)
SOURCE_CATEGORY = (("Capacitors", r"Multilayer Ceramic Capacitors|Ceramic Capacitors|Aluminum Electrolytic Capacitors|"
                                  r"Tantalum Capacitors|Film Capacitors|Polymer Capacitors"),
                   ("Inductors", r"Power Inductors|Ferrite Beads|Inductors \(SMD\)"),
                   ("Transistors", r"MOSFETs|Bipolar Transistors|Digital Transistors|JFETs|IGBTs"),
                   ("Diodes", r"Light Emitting Diodes|LEDs|LED Indication - Discrete|Schottky (?:Barrier )?Diodes|Zener Diodes|Switching Diodes|"
                              r"Rectifiers|TVS Diodes|ESD and Surge Protection \(TVS/ESD\)"),
                   ("Crystals", r"Crystals"),
                   ("Connectors", r"USB Connectors|Pin Headers|Female Headers|Wire To Board Connector|"
                                  r"Board to Board Connectors|IDC Connectors|Screw Terminal Blocks"))
# DigiKey's all-caps description codes: "CAP CER 0.1UF 16V X7R 0402", "MOSFET N-CH 30V 5.7A SOT23-3"
DIGIKEY_CODE = (("Capacitors", r"CAP (?:CER|TANT|ALUM|FILM|POLY)"), ("Resistors", r"RES(?: SMD| THICK| THIN)? \d"),
                ("Inductors", r"(?:FIXED )?IND \d|FERRITE BEAD"), ("Transistors", r"MOSFET [2NP]|TRANS (?:NPN|PNP|2NPN|2PNP)"),
                ("Diodes", r"LED [A-Z]|DIODE |TVS DIODE"), ("Crystals", r"CRYSTAL \d"), ("Connectors", r"CONN "))
PASSIVE_KINDS = ("Resistors", "Capacitors", "Inductors")


def source_kind(descriptions=()):
    """The table a source's own category name or DigiKey code says, else None."""
    for d in descriptions:
        t = re.sub(r"\s+", " ", str(d or "")).strip()
        for cat, rx in DIGIKEY_CODE:
            if t == t.upper() and re.match(rf"(?:{rx})", t):
                return cat
        for cat, rx in SOURCE_CATEGORY:
            if re.search(rf"(?<![\w-])(?<!(?i:for) )(?<!(?i:with) )(?<!(?i:of) )(?<!(?i:integrated) )(?<!(?i:internal) )(?<!(?i:built-in) )(?:{rx})(?![\w-])", t):   # not "driver for MOSFETs"
                return cat
    return None


def kind_of(descriptions=(), footprints=()):
    """Resistors / Capacitors / Inductors / Crystals / Connectors / Transistors / Diodes from a source's category
    words or a chip footprint, else None."""
    semi = any(re.match(IC_PACKAGE, str(f or "").rpartition(":")[2].strip()) for f in footprints)
    got = source_kind(descriptions)
    if got and not (semi and got in PASSIVE_KINDS):     # a MOSFET is in a SOT-23; a "capacitor" in one is not a capacitor
        return got
    for d in descriptions if not semi else ():  # an SOT-23 / MSOP part is not filed by its words ("Precision
        parts = [p.strip() for p in re.split(r",|;| - ", re.sub(r"\([^)]*\)", "", str(d or "")))]
        for cat, rx in KIND_WORDS:
            if re.fullmatch(rf"(?:[\w/&-]+\s+){{0,3}}(?i:{rx})(?:\s+[A-Z]{{2,5}})?", parts[0]):
                return cat                  # Matched Resistors", "Programmable Resistor" are ICs)
    if any(re.fullmatch(CHIP_SIZES, str(f or "").strip()) for f in footprints):
        for cat, rx in KIND_WORDS[:3]:      # a bare chip size (LCSC's package "0603") and the noun anywhere in the
            if any(re.search(rf"\b(?i:{rx})\b", str(d or "")) for d in descriptions):     # text: "... Chip Resistor"
                return cat
    for f in footprints:                    # not "easyeda:C7593": that is an LCSC part number (an NE555), not C 7593
        lib, _, name = str(f or "").rpartition(":")
        if lib.lower() not in ("easyeda", "lcsc") and re.match(
                rf"^([RCL])(?:{CHIP_SIZES}|_(?:{CHIP_SIZES})_\d{{4,5}}Metric)(?:_|$)", name.split(" ")[0]):
            return KIND_FP[name[0]]
    return None


def _safe_name(s, notes):
    """lib_name(s), or for a name Altium can't store (CJK text) the same name with those characters as '_' and a note
    saying so: one such part must not stop the whole library. The part is then held (df.attrs["unstorable"],
    altium365.plan -> release check). Look-alikes cp1252 holds (Greek mu, fullwidth, zero-width) are mapped by
    altium_lib.storable first and need no note."""
    try:
        return lib_name(s)
    except ValueError as e:
        t = re.sub(r"[\x00-\x1f\x7f]", "_", storable(s))
        safe = "".join("_" if not_cp1252(ch) else ch for ch in t)
        notes.append(f"{e}: named {safe!r} instead")
        return lib_name(safe)


def _land_canon(name):
    """A footprint field as the package it names: a known alias as its land ('DO-214AC' -> 'D_SMA', 'TO-236' ->
    'SOT-23'), a distributor's pins-first name the family's way ('48-LQFP' -> 'LQFP-48', '8-SOIC'), ST's 'SO8N'."""
    from . import stdparts as SP
    key = SP._norm(name)
    hit = SP.ALIASES.get(key) or SP.DIODE_ALIASES.get(key)
    if hit and key not in SP.AMBIGUOUS:
        return hit
    fams = "|".join(sorted(SP.FAMILIES, key=len, reverse=True))
    name = re.sub(rf"^(\d{{1,3}})[-_ ]?({fams})(?![A-Za-z0-9])", r"\2-\1", name, flags=re.I)
    return re.sub(r"^(SO-?\d{1,2})N(?![A-Za-z0-9])", r"\1", name, flags=re.I)


def _ic_land(name):
    """IC family attributes of a land name (stdparts' free-text reading), the family written one way: SO / SOP-8/14/16
    are JEDEC SOIC unless a body says otherwise, a bare 'QFP' is any QFP of the count. -> dict or None."""
    from . import stdparts as SP
    a = SP._free_attrs(name)
    if not a:
        return None
    if a["fam"] == "SO" or a["fam"] == "SOP" and a["pins"] in (8, 14, 16):
        a["fam"] = "SOIC"
    a["leadless"] = a["fam"] in SP.NO_LEAD
    return a


def lands_differ(a, b):
    """Two BOM footprint fields that name two lands (R_0603_1608Metric vs R_0402_1005Metric, SOIC-8 vs SOIC-8-1EP_..),
    not one land written two ways ('0603' vs 'Resistor_SMD:R_0603_1608Metric'; R-FX10-2: 'LQFP-48_7x7mm' vs '48-LQFP'
    or 'QFP-48', 'SOIC-8_3.9x4.9mm' vs 'SO-8' / '8-SOIC' / EasyEDA's 'SOIC-8_L5.0-W4.0-P1.27..', 'D_SMA' vs 'DO-214AC').
    IC packages compare by family, pin count, wide body, exposed pad, and body and pitch where both give them. An empty
    field says nothing."""
    from .kicad_index import EP_FAMILY, EP_HINT, EP_NAME, LAND_VARIANT
    from . import stdparts as SP
    a, b = (str(x or "").split(":")[-1].strip() for x in (a, b))
    if not a or not b or a.casefold() == b.casefold():
        return False
    if bool(LAND_VARIANT.search(a) or SP.LAND_VARIANT.search(a)) != bool(LAND_VARIANT.search(b) or SP.LAND_VARIANT.search(b)):
        return True                             # the hand-solder / thermal-via copy of a land is another land
    ca, cb = _land_canon(a), _land_canon(b)
    if ca.casefold() == cb.casefold():
        return False

    def ep(n, attrs):
        return bool(EP_NAME.search(n) or EP_HINT.search(n) or EP_FAMILY.search(n) or (attrs or {}).get("ep"))

    ia, ib = _ic_land(ca), _ic_land(cb)
    if ia and ib:
        if SP.NAME_RE.match(ca) and SP.NAME_RE.match(cb):
            return True                         # two KiCad names of one family: two lands
        if (ia["fam"], ia["pins"]) != (ib["fam"], ib["pins"]) and not (
                ia["pins"] == ib["pins"] and {ia["fam"], ib["fam"]} & {"QFP"} and {ia["fam"], ib["fam"]} <= SP.QUAD_LEADED):
            return True
        if ia["w"] != ib["w"]:
            return True
        ea, eb = ep(ca, ia), ep(cb, ib)
        if ea != eb and not (ib if ea else ia)["leadless"]:
            return True                         # a bare leadless name ('QFN-20') does not say there is no pad
        if ia["body"] and ib["body"] and any(abs(x - y) > 0.15 for x, y in zip(ia["body"], ib["body"])):
            return True
        if ia.get("body_w") and ib["body"] and abs(ia["body_w"] - ib["body"][0]) > 0.15 or \
                ib.get("body_w") and ia["body"] and abs(ib["body_w"] - ia["body"][0]) > 0.15:
            return True
        if ia["pitch"] and ib["pitch"] and abs(ia["pitch"] - ib["pitch"]) > 0.01:
            return True
        if ia["epsize"] and ib["epsize"] and any(abs(x - y) > 0.05 for x, y in zip(ia["epsize"], ib["epsize"])):
            return True
        return False
    if package_of(ca) != package_of(cb):
        return True
    if bool(EP_NAME.search(ca) or EP_HINT.search(ca)) != bool(EP_NAME.search(cb) or EP_HINT.search(cb)):
        return True
    return "_" in ca and "_" in cb              # two full land names of one package: two lands ('_HandSolder')


def _fields_differ(key, a, b):
    """Two lines' Value / MPN / supplier number that name two things: 10k vs 1k, SMAJ5.0A vs SMAJ50A. One thing written
    two ways is not ('100nF' / '0.1uF', 'TPS62133RGTR' / 'tps62133-rgtr', 'C1525' / ' C1525'). An empty one says nothing."""
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a or not b:
        return False
    if key == "Value":
        va, vb = parse_value(a, True), parse_value(b, True)
        if va[0] is not None and vb[0] is not None and not two_readings(a) and not two_readings(b):
            return abs(va[0] - vb[0]) > 1e-9 * max(abs(va[0]), abs(vb[0])) or bool(va[1] and vb[1] and va[1] != vb[1])
    return re.sub(r"[\s-]", "", a).casefold() != re.sub(r"[\s-]", "", b).casefold()


def prior_names(items):
    """{plan item name: its MPN} of an existing plan, for to_rows(prior=...)."""
    return {i["name"]: next((str(p.get("value") or "") for p in i.get("parameters") or []
                             if p.get("name") == "Manufacturer Part Number"), "") for i in items or [] if i.get("name")}


def to_rows(lines, triage=None, rules=None, prior=None):
    """{category: DataFrame of DbLib rows}. rules (ecadlib.rules): part names, footprint names, value style and
    supplier columns; None keeps ecadlib's own. df.attrs["kicad_fp"] maps a part to its footprint's original name
    when the rules renamed it; df.attrs["unstorable"] maps a part whose names Altium can't store to the reason
    (held by the release check). prior: {part name: MPN} already in the plan (prior_names), so names stay with parts."""
    from . import rules as RU
    custom = rules is not None and not RU.is_default(rules, "naming")
    tri, by_line = {}, {}
    if triage is not None:
        for _, t in triage.iterrows():
            if str(t["mpn"]):                  # an empty MPN would hand one row's footprint to every MPN-less line
                tri[str(t["mpn"])] = t
            try:                               # each BOM line's own row (R-BF-2): one MPN on an 0603 and an 0402
                by_line[int(str(t.get("line", "")).strip())] = t    # line must not take the last row's land
            except ValueError:
                pass
    holds = {}
    out, seen, renamed, same_value, canon, first, notes, unstorable = {}, {}, {}, {}, {}, {}, {}, {}
    # a name already in the plan stays with its part (SYS-1): when the part released under it is still in the BOM, a
    # line added above it with another MPN takes the suffixed name instead
    present = {norm_mpn("" if placeholder_mpn(ln.get("mpn", "")) else ln.get("mpn", "")) for ln in lines}
    reserved = {n: norm_mpn(m) for n, m in (prior or {}).items() if norm_mpn(m) in present}
    for i, ln in enumerate(lines, 1):
        bad = []
        prefix = re.match(r"[A-Z]+", ln.get("designator", "").upper() or "U")
        cat = CATEGORY.get(prefix.group(0) if prefix else "U", "Misc")
        mpn, lcsc = ln.get("mpn", ""), ln.get("lcsc", "")
        if placeholder_mpn(mpn):               # 'N/A' / '-' names no part: two such lines are two parts, not one
            mpn = ""
        fp = ln.get("footprint", "")
        t = by_line.get(i)
        if t is None or not mpn or str(t["mpn"]) != mpn:
            t = tri.get(mpn)
        ref, kind = (prefix.group(0) if prefix else "U"), None
        if cat in ("ICs", "Misc"):              # U? / no designator: what the part is, if the source or footprint says
            got = t if t is not None else {}
            kind = kind_of((ln.get("description"), got.get("src_description")),
                           (fp, got.get("local_footprint"), got.get("package")))
            if kind:
                cat, ref = kind, RU.CAT_REF[kind]
        if t is not None and isinstance(t.get("local_footprint"), str) and t["local_footprint"]:
            fp = t["local_footprint"].split(" ")[0]
        fp_ref = fp.split(":")[-1]
        pkg = package_of(fp, ln.get("description")) or ""
        value = ln.get("value", "")
        if cat in GENERIC_SYMBOL and not mpn and PLAIN_VALUE.fullmatch(value.strip()):
            pv = parse_value(value)                   # 1uF / 1u / 1µF, 100nF / 0.1uF, 4k7 / 4.7k: one part, named
            if pv is not None and not two_readings(value):   # with the first spelling the BOM uses ('4,700pF' reads
                                                             # two ways: its own part)
                value = same_value.setdefault((cat, pkg, fp_ref, f"{pv:.6g}"), value)
        name_mpn = re.sub(r"\s+", "_", mpn.strip())   # a space in an MPN is a separator ('FC-135 32.768KA-AG0'
        if custom:                                    # -> FC-135_32.768KA-AG0, as kicad_export.kname), not dropped
            f = RU.line_fields(ln, cat, pkg, rules, ref)
            pn = (RU.component_name({**f, "mpn": name_mpn}, rules)
                  or ln.get("designator", "").split(",")[0]).replace(" ", "")
        else:                                         # an empty token drops out with its dash: IND-10uH, not IND--10uH
            pn = ("-".join(t for t in (cat[:3].upper(), pkg, value) if t)
                  if cat in GENERIC_SYMBOL and value and not (kind and value == mpn)
                  else name_mpn or value or ln.get("designator", "").split(",")[0]).replace(" ", "")
        key = norm_mpn(mpn)                     # one part however the MPN is typed: case, dashes (TPS62133-RGTR)
        if key and key in canon:
            pn = canon[key]
        else:                                   # no MPN: the footprint tells two parts apart unless the name has the
            ident = key or ("" if pkg and pkg in pn else fp_ref)              # package (RES-0603-10k) already
            # Altium part numbers ignore case: names are compared folded, and a name spelt another way is another
            # part (RES-2512-10m is 10 mOhm, RES-2512-10M 10 MOhm), never one name in two cases (R-FX10-7, R-BF-9)
            ident, fold = (ident, "" if key else pn), pn.casefold()
            if pn in reserved and fold not in seen and key != reserved[pn]:
                seen[fold] = None               # held for the part released under this name, further down the BOM
            if pn in reserved and seen.get(fold) is None and key == reserved[pn]:
                seen[fold] = ident
            if seen.setdefault(fold, ident) != ident:   # same synthetic PN, different part (100nF 16V vs 50V; a 10k
                pn = f"{pn}-{name_mpn or fp_ref}".replace(" ", "")              # SMD and a 10k axial): keep both
                seen.setdefault(pn.casefold(), ident)
            if key:
                canon[key] = pn
        pn = _safe_name(pn, bad)
        lib_ref = _safe_name(GENERIC_SYMBOL.get(cat) or (custom and mpn and RU.symbol_name(f, rules)) or mpn or pn, bad)
        kicad_fp = fp_ref = _safe_name(fp_ref, bad)
        if custom:
            fp_ref = _safe_name(RU.footprint_name(fp_ref, rules, ref), bad)
            if fp_ref != kicad_fp:
                renamed[pn] = kicad_fp
        if bad:                                 # a name Altium can't store: the part is held (release check)
            unstorable[pn] = "; ".join(dict.fromkeys(bad))
        out.setdefault(cat, []).append({
            "Part Number": pn,
            "Library Ref": lib_ref,
            "Library Path": f"SchLib\\{file_stem(lib_ref)}.SchLib",      # altium_lib layout: one entry per file
            "Footprint Ref": fp_ref,
            "Footprint Path": f"PcbLib\\{file_stem(fp_ref)}.PcbLib" if fp_ref else "",
            "Description": ln.get("description", ""),
            "Value": value, "Comment": "=Value" if cat in GENERIC_SYMBOL else mpn,
            "Manufacturer": ln.get("manufacturer", ""), "Manufacturer Part Number": mpn,
            "Supplier 1": "LCSC" if lcsc else "", "Supplier Part Number 1": lcsc,
            "Supplier 2": "", "Supplier Part Number 2": "",
            "ComponentLink1Description": "LCSC" if lcsc else "",
            "ComponentLink1URL": f"https://www.lcsc.com/product-detail/{lcsc}.html" if lcsc else "",
            "ComponentLink2Description": "Datasheet" if lcsc else "",
            "ComponentLink2URL": f"https://www.lcsc.com/datasheet/{lcsc}.pdf" if lcsc else "",
        })
        row, des = out[cat][-1], ln.get("designator", "") or "?"
        notes.setdefault(pn, []).extend(bad)
        if ln.get("bom_hold"):                  # a row read_bom could not read as the header says (shifted cells)
            holds.setdefault(pn, []).append(f"BOM line {des}: {ln['bom_hold']}")
        if pn not in first:
            first[pn] = (row, des, ln.get("footprint", ""))
        else:                                   # a second BOM line for this part: the first one wins, say what differs
            keep, kdes, kfp = first[pn]
            diff = [f"{k} {keep[k]!r} vs {row[k]!r}" for k in ("Footprint Ref", "Manufacturer", "Value",
                                                                 "Manufacturer Part Number", "Supplier Part Number 1")
                    if str(keep[k]).strip() and str(row[k]).strip() and keep[k] != row[k]]
            land = lands_differ(kfp, ln.get("footprint", "")) or lands_differ(keep["Footprint Ref"], row["Footprint Ref"])
            if land and not any(d.startswith("Footprint Ref") for d in diff):
                diff.insert(0, f"Footprint {kfp!r} vs {ln.get('footprint', '')!r}")
            if diff:
                notes[pn].append(f"BOM lines {kdes} and {des} are one part {pn} but differ ({'; '.join(diff)}): "
                                 f"using {kdes}; check the BOM")
            if land:                            # one part built on one land for lines that name two: held (R-BF-2)
                holds.setdefault(pn, []).append(
                    f"BOM lines {kdes} and {des} are one part {pn} on two footprints "
                    f"({kfp or keep['Footprint Ref']!r} vs {ln.get('footprint', '') or row['Footprint Ref']!r}): "
                    "give the lines their own MPNs or one footprint")
            other = [d for d, k in ((f"Value {keep['Value']!r} vs {row['Value']!r}", "Value"),
                                    (f"Manufacturer Part Number {keep['Manufacturer Part Number']!r} vs "
                                     f"{row['Manufacturer Part Number']!r}", "Manufacturer Part Number"),
                                    (f"Supplier Part Number 1 {keep['Supplier Part Number 1']!r} vs "
                                     f"{row['Supplier Part Number 1']!r}", "Supplier Part Number 1"))
                     if _fields_differ(k, keep[k], row[k])]
            if other:                           # one part released with one line's value / MPN / order number for
                holds.setdefault(pn, []).append(                  # lines that name two (R-BF-3): held, not a note
                    f"BOM lines {kdes} and {des} are one part {pn} but differ ({'; '.join(other)}): "
                    "fix the BOM so the lines agree, or give them their own MPNs")
    notes = {k: list(dict.fromkeys(v)) for k, v in notes.items() if v}
    for v in notes.values():
        for n in v:
            print(f"  warning: {n}", file=sys.stderr)
    cols = columns(rules)
    res = {k: pd.DataFrame(v, columns=cols).fillna("").drop_duplicates("Part Number") for k, v in sorted(out.items())}
    for df in res.values():
        df.attrs["kicad_fp"] = renamed
        df.attrs["unstorable"] = unstorable
        df.attrs["notes"] = notes               # {Part Number: [BOM conflicts, renamed characters]}
        df.attrs["holds"] = {k: list(dict.fromkeys(v)) for k, v in holds.items()}   # {Part Number: [BOM conflicts
        # that hold the part]}: altium365.plan -> item bom_holds -> release check
    return res


def columns(rules=None):
    """COLUMNS with the rules' number of supplier slots (2 = COLUMNS as is)."""
    n = (rules or {}).get("parameters", {}).get("supplier_slots", 2)
    sup = [c for i in range(1, n + 1) for c in (f"Supplier {i}", f"Supplier Part Number {i}")]
    return COLUMNS[:10] + sup + COLUMNS[14:] if n != 2 else list(COLUMNS)


def sqlite_connection(db_path):
    return ('Provider=MSDASQL.1;Persist Security Info=False;'
            f'Extended Properties="DRIVER=SQLite3 ODBC Driver;Database={db_path};LongNames=0;Timeout=1000;NoTXN=0;SyncPragma=NORMAL;StepAPI=0;"')


def excel_connection(xlsx_path):
    return (f'Provider=Microsoft.ACE.OLEDB.12.0;Data Source={xlsx_path};'
            'Extended Properties="Excel 12.0 Xml;HDR=YES;IMEX=1";Persist Security Info=False')


def dblib_links(conn_str, last_table, left_quote="[", right_quote="]", db_type="", db_path="", relative=False,
                search_path="", extra=()):
    """[OutputDatabaseLinkFile] + [DatabaseLinks] lines. db_type/db_path/relative are what Altium's DbLib editor
    writes for its Access/Excel fast connection (LibraryDatabaseType=Microsoft Excel, LibraryDatabasePath=x.xls,
    DatabasePathRelative=1); extra = (key, value) pairs appended to [DatabaseLinks] (SVNRepositoryURL ...)."""
    return (["[OutputDatabaseLinkFile]", "Version=1.1", "[DatabaseLinks]", f"ConnectionString={conn_str}",
             "AddMode=3", "RemoveMode=1", "UpdateMode=2", "ViewMode=0",
             f"LeftQuote={left_quote}", f"RightQuote={right_quote}", "QuoteTableNames=1", "UseTableSchemaName=0",
             "DefaultColumnType=VARCHAR(255)", f"LibraryDatabaseType={db_type}", f"LibraryDatabasePath={db_path}",
             f"DatabasePathRelative={int(relative)}", "TopPanelCollapsed=0", f"LibrarySearchPath={search_path}",
             "OrcadMultiValueDelimiter=,", "SearchSubDirectories=0", "SchemaName=", f"LastFocusedTable={last_table}"]
            + [f"{k}={v}" for k, v in extra])


def table_section(i, table):
    return [f"[Table{i}]", "SchemaName=", f"TableName={table}", "Enabled=True", "UserWhere=0", "UserWhereText="]


def fieldmap_section(i, table, column, key="Part Number"):
    ftype = 0 if column == key else 1                                   # observed: key column FieldType=0
    pname = f"[{column}]" if column in RESERVED else column
    return [f"[FieldMap{i}]",
            f"Options=FieldName={table}.{column}|TableNameOnly={table}|FieldNameOnly={column}|FieldType={ftype}|"
            f"ParameterName={pname}|VisibleOnAdd=False|AddMode=0|RemoveMode=0|UpdateMode=0"]


def dblib_text(conn_str, tables, left_quote="[", right_quote="]", excel=False, columns=COLUMNS, **links):
    """INI layout copied from real-world .DbLib files (see docs/altium.md, 'verified'). tables: table names, or
    {table: columns}; excel adds the '$' Altium's Excel provider puts after sheet names."""
    cols = tables if isinstance(tables, dict) else {t: columns for t in tables}
    names = [t + ("$" if excel else "") for t in cols]
    L = dblib_links(conn_str, names[0], left_quote, right_quote, **links)
    for i, tn in enumerate(names, 1):
        L += table_section(i, tn)
    fm = 0
    for tn, cs in zip(names, cols.values()):
        for c in cs:
            fm += 1
            L += fieldmap_section(fm, tn, c)
    return "\r\n".join(L) + "\r\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write an Altium DbLib from a librarian plan.json: only the parts that pass "
                                             "the release check go in (dblib-sync), the others are listed with the reason. "
                                             "From a BOM only with --draft: an unchecked table skeleton.")
    ap.add_argument("bom", help="plan.json from `ecadlib librarian` (a BOM needs --draft)")
    ap.add_argument("-o", "--out", default="out/altium")
    ap.add_argument("--draft", action="store_true",
                    help="from a BOM: write a row for every BOM line, release-checked or not (Library Path / Footprint "
                         "Path name files this command does not write)")
    ap.add_argument("--name", default="ecadlib", help="from a plan: base name of the .DbLib, .xlsx and .csv")
    ap.add_argument("--triage", help="triage CSV from bom_triage (to pick up resolved local footprints)")
    ap.add_argument("--win-dir", default=WIN_DIR, help="Windows folder the DB will live in (for the connection string)")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml in the output folder, the current folder or a parent)")
    a = ap.parse_args(argv)

    if _is_plan(a.bom):                       # the release gate, as every other export route: dblib-sync ships only
        from . import altium_dblib_sync as DS  # the parts that pass the check now and lists the others with the reason
        return DS.main([a.bom, "--to", a.out, "--name", a.name] + (["--win-dir", a.win_dir] if a.win_dir != WIN_DIR else [])
                       + (["--rules", a.rules] if a.rules else []))
    if not a.draft:
        print(f"ecadlib dblib: {a.bom} is a BOM, and no BOM line has been through the release check, so nothing was "
              "written (the rows would name .SchLib / .PcbLib files that are not built).\n"
              f"  hint: `ecadlib librarian {a.bom} -o out/lib` builds and checks the parts, then "
              "`ecadlib dblib out/lib/plan.json -o OUT` writes the DbLib with the parts that pass.\n"
              "  --draft writes the old unchecked table skeleton (every BOM line) anyway.", file=sys.stderr)
        return 2
    from . import rules as RU
    rules = RU.load(a.rules, start=a.out)
    try:
        lines, _ = read_bom(a.bom, RU.bom_columns(rules), rules)
    except (ValueError, OSError) as e:                   # unreadable / headerless BOM: a message, not a traceback
        print(f"cannot read BOM: {e}", file=sys.stderr)
        return 2
    if not lines:
        print(f"cannot read BOM: {a.bom} has a header but no part lines", file=sys.stderr)
        return 2
    tri = pd.read_csv(a.triage, dtype=str).fillna("") if a.triage else None
    tables = to_rows(lines, tri, rules)
    tables = {k: df.rename(columns=lambda c: RU.house_row({c: 0}, rules).popitem()[0]) for k, df in tables.items()}
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    db = out / "parts.sqlite"
    db.unlink(missing_ok=True)
    with closing(sqlite3.connect(db)) as con, con:      # `with con` alone commits but never closes
        for name, df in tables.items():
            cols = ", ".join(f'"{c}" TEXT' + (" PRIMARY KEY" if c == "Part Number" else "") for c in df.columns)
            con.execute(f'CREATE TABLE "{name}" ({cols})')
            con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(df.columns))})', df.values.tolist())
    with pd.ExcelWriter(out / "parts.xlsx", engine="openpyxl") as w:   # a declared dependency
        for name, df in tables.items():
            df.to_excel(w, sheet_name=name, index=False)
            for row in w.sheets[name].iter_rows(min_row=2):    # all text: Comment "=Value" is Altium's parameter
                for c in row:                                     # reference, not an Excel formula (#NAME?)
                    if isinstance(c.value, str):
                        c.data_type = "s"

    win = PureWindowsPath(a.win_dir)
    sqlite_cs, excel_cs = sqlite_connection(win / "parts.sqlite"), excel_connection(win / "parts.xlsx")
    names = {k: list(df.columns) for k, df in tables.items()}
    (out / "ecadlib_sqlite.DbLib").write_text(dblib_text(sqlite_cs, names, '"', '"'), newline="")
    # the Excel one names its workbook relative to itself (DatabasePathRelative=1, as Altium's DbLib editor writes it),
    # so the folder can move; the SQLite ODBC string has no relative form and keeps --win-dir
    (out / "ecadlib_excel.DbLib").write_text(dblib_text(excel_cs, names, "[", "]", excel=True, db_type="Microsoft Excel",
                                                        db_path="parts.xlsx", relative=True), newline="")
    for n_, df in tables.items():
        print(f"{n_:<12} {len(df):>2} parts  e.g. {df.iloc[0]['Part Number']} -> {df.iloc[0]['Footprint Ref']}")
    print(f"wrote {db}, {out/'parts.xlsx'}, {out/'ecadlib_sqlite.DbLib'}, {out/'ecadlib_excel.DbLib'}")
    print("draft: no part in it was release-checked, and its Library Path / Footprint Path name files this command "
          "does not write")


WIN_DIR = r"C:\ecadlib\altium"


def _is_plan(path):
    """A librarian / altium365 plan.json (a JSON list of parts), not a BOM."""
    import json
    p = Path(path)
    try:
        with open(p, "rb") as f:
            head = f.read(64).lstrip(b"\xef\xbb\xbf \t\r\n")
        if not head.startswith(b"["):
            return False
        return isinstance(json.loads(p.read_text(encoding="utf-8-sig")), list)
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    sys.exit(main())
