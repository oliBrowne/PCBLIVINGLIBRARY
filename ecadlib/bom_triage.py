"""BOM triage: what's already in the local KiCad library, what LCSC/EasyEDA can supply,
and what needs a footprint generated.

    python -m ecadlib.bom_triage examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints \
        -o out/triage [--offline] [--providers easyeda,jlcpcb_search,digikey,...]
"""
import argparse, csv, difflib, json, re, sys
from pathlib import Path

import pandas as pd

from . import providers as P, sheetsafe as SS
from .httpcache import CachedHTTP
from .kicad_index import KiCadIndex, package_of, parse_value

FIELDS = {   # canonical field -> header synonyms (normalised: lowercase, punctuation -> space)
    "mpn": ["mpn", "manufacturer part number", "mfr part number", "mfr part", "mfg part number", "mfr pn",
            "manufacturer part", "part number", "mfr no", "manufacturer pn", "pn", "mfr part no"],
    "manufacturer": ["manufacturer", "mfr", "mfr name", "mfg", "maker", "brand", "vendor", "manufacturer name"],
    "lcsc": ["lcsc", "lcsc part", "lcsc part number", "lcsc part no", "lcsc pn", "jlcpcb part", "jlc part", "lcsc id"],
    "qty": ["qty", "quantity", "count", "qnty", "amount", "pcs"],
    "designator": ["designator", "designators", "reference", "references", "ref", "refdes", "ref des", "parts"],
    "value": ["value", "val", "comment"],
    "footprint": ["footprint", "package", "case", "package case", "pcb footprint"],
    "description": ["description", "desc", "notes"],
    "libref": ["libref", "lib ref", "library ref", "library reference", "symbol"],
    "dnp": ["dnp", "dnf", "do not populate", "do not place", "do not fit", "no stuff", "not fitted"],
    "datasheet": ["datasheet", "data sheet", "datasheet url", "datasheet link"],
}
DELIMS = (",", ";", "\t", "|")


def _norm(h):
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


def map_headers(headers, threshold=0.72):
    """Fuzzy, case-insensitive header -> field mapping. Returns {field: header}."""
    scores = []
    for h in headers:
        nh = _norm(h)
        for f, syns in FIELDS.items():
            best = 0.0
            for s in syns:
                if nh == s:
                    sc = 0.97 if f == "mpn" and s in ("part number", "pn") else 1.0   # DigiKey: 'Part Number' is
                                                                     # its own code, 'Manufacturer Part Number' the MPN
                elif re.search(rf"\b{re.escape(s)}\b", nh):
                    sc = 0.75 + 0.2 * len(s) / max(len(nh), 1)          # containment, longer = better
                else:
                    sc = difflib.SequenceMatcher(None, nh, s).ratio() * 0.95
                best = max(best, sc)
            if f != "lcsc" and re.search(r"\b(lcsc|jlc|jlcpcb)\b", nh):
                best *= 0.5                                              # 'LCSC Part Number' is not the MPN
            if f == "lcsc" and not re.search(r"\b(lcsc|jlc|jlcpcb)", nh):
                best *= 0.5                                              # nor is a bare 'Part Number' an LCSC code
            if f == "mpn" and re.search(r"\b(supplier|distributor|digi ?key|mouser|farnell|newark|arrow|tme|vendor)\b", nh):
                best *= 0.5                                              # nor is a distributor's order code
            if f == "manufacturer" and re.search(r"\b(part|pn|number|no)\b", nh):
                best *= 0.6                                              # 'Mfr Part #' is not the maker
            scores.append((best, f, h))
    mapping, used = {}, set()
    for sc, f, h in sorted(scores, key=lambda t: -t[0]):
        if sc >= threshold and f not in mapping and h not in used:
            mapping[f] = h
            used.add(h)
    return mapping


def utf16_without_bom(data):
    """'utf-16-le' / 'utf-16-be' for UTF-16 text without a byte-order mark (mostly-ASCII text: every other byte is
    zero), else None."""
    head = data[:4096]
    if len(head) < 4 or head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return None
    n = len(head) // 2 * 2
    even, odd = head[0:n:2], head[1:n:2]
    if odd.count(0) > 0.7 * len(odd) and even.count(0) < 0.1 * len(even):
        return "utf-16-le"
    if even.count(0) > 0.7 * len(even) and odd.count(0) < 0.1 * len(odd):
        return "utf-16-be"
    return None


_SI = ((1e-12, "p"), (1e-9, "n"), (1e-6, "u"), (1e-3, "m"))
_EXP = re.compile(r"[-+]?(\d+(\.\d*)?|\.\d+)[eE][-+]?\d+")


def _plain_number(x):
    """A spreadsheet number as BOM text, never in exponent notation: 1.0 -> '1', 1050170001.0 -> '1050170001',
    0.1 -> '0.1', 1e-07 -> '100n' (a capacitor typed in farads: SI prefix, as engineers write it)."""
    x = float(x)
    if x != x or x in (float("inf"), float("-inf")):
        return ""
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    if 0 < abs(x) < 1e-3:
        for scale, pre in _SI:
            if abs(x) < scale * 1000:
                return f"{x / scale:.6g}{pre}"
    return f"{x:.12f}".rstrip("0").rstrip(".")


def _xl_text(v):
    if v is None or (isinstance(v, float) and v != v):
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return _plain_number(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10] if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 else v.isoformat()
    return str(v)


def _read_csv(p):
    """Tolerant CSV read: Altium ActiveBOM exports put title rows ('Bill of Materials;;;;;', a blank
    row) above a wider header, which pandas' sniffer chokes on. Rows may be ragged; they are padded."""
    data = Path(p).read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):                             # Excel "Unicode Text" is UTF-16 + tabs
        data = data.decode("utf-16").encode("utf-8")
    elif utf16_without_bom(data):                                         # PowerShell / some exporters: no BOM
        data = data.decode(utf16_without_bom(data), errors="replace").encode("utf-8")
    if b"\x00" in data[:4096]:
        raise ValueError(f"{p}: not a text BOM (binary data); save it as CSV or XLSX")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):                       # Excel 'CSV' on Windows is cp1252
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = [ln for ln in text.splitlines(keepends=True) if ln.strip()]  # keepends: a quoted cell may span lines
    if not lines:
        raise ValueError(f"{p}: the BOM file is empty")

    def score(d):                                                         # rows sharing the commonest width > 1
        widths = [len(r) for r in csv.reader(lines[:30], delimiter=d)]
        common = max(set(widths), key=widths.count)
        return (widths.count(common) if common > 1 else 0, common)

    delim = max(DELIMS, key=score)
    rows = list(csv.reader(lines, delimiter=delim))
    width = max(len(r) for r in rows)
    rows = [[re.sub(r"\s*[\r\n]+\s*", " ", c) for c in r] for r in rows]   # 'RES 10k<newline>0603' -> one line
    return pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=str)


def apply_columns(mapping, headers, columns):
    """Rules' [bom] columns (field -> your header) win over the fuzzy guess; headers not in this BOM are ignored."""
    for f, want in (columns or {}).items():
        h = next((h for h in headers if _norm(h) == _norm(want)), None)
        if h is not None:
            mapping = {k: v for k, v in mapping.items() if v != h}
            mapping[f] = h
    return mapping


def read_bom(path, columns=None, rules=None):
    """columns: the rules' [bom] columns ({field: header}), applied over the fuzzy header match. rules: for an Altium
    project, the house parameter names to read the MPN / manufacturer from (default: ecadlib.toml found from the project)."""
    p = Path(path)
    if p.suffix.lower() in (".prjpcb", ".schdoc", ".bomdoc"):     # Altium project: BOM straight from the schematics
        import os
        from . import rules as RU
        from .altium_project import bom_lines
        return bom_lines(p, variant=os.environ.get("ECADLIB_VARIANT") or None, rules=RU.resolve(rules, p.parent))
    raw = (_read_excel(p) if p.suffix.lower() in (".xlsx", ".xlsm", ".xls") else _read_csv(p)).fillna("")
    if raw.empty or not raw.astype(str).apply(lambda c: c.str.strip()).ne("").any().any():
        raise ValueError(f"{p}: the BOM file is empty")
    hdr_row = max(range(min(10, len(raw))), key=lambda i: len(map_headers(list(raw.iloc[i]))))  # header may not be row 0
    headers = [str(h).strip() for h in raw.iloc[hdr_row]]
    df = raw.iloc[hdr_row + 1:].copy()
    df.columns = headers
    mapping = apply_columns(map_headers(headers), headers, columns)
    if not {"designator", "mpn", "value"} & mapping.keys():
        raise ValueError(f"{p}: no BOM header found (need a Designator, MPN or Value/Comment column)")
    lines = []
    for _, row in df.iterrows():
        d = {f: SS.unquote(str(row[h]).strip()) for f, h in mapping.items()}       # "'=X" from our own reports
        if not any(d.values()):
            continue
        if lines and d.get("designator") and not any(v for f, v in d.items() if f != "designator"):
            prev, q = lines[-1], re.match(r"\s*(\d+)", lines[-1].get("qty", ""))
            if q and count_refs(prev["designator"]) < int(q.group(1)):
                prev["designator"] += ", " + d["designator"]            # designator list wrapped onto the next row
                continue
        if _EXP.fullmatch(d.get("value", "")):                         # a CSV that kept Excel's 1e-07
            d["value"] = _plain_number(d["value"])
        if _is_total(d):
            continue                                                     # 'Total;;;12' footer, not a part
        if "dnp" in d:
            d["dnp"] = _yes(d["dnp"])
        if d.get("lcsc") and not re.fullmatch(r"C\d+", d["lcsc"], re.I):
            d["lcsc_invalid"] = d.pop("lcsc")
        lines.append(d)
    return lines, mapping


def _header_score(raw):
    return max((len(map_headers(list(raw.iloc[i]))) for i in range(min(10, len(raw)))), default=0)


def _read_excel(p):
    """The sheet that holds the BOM: workbooks often put a cover / notes sheet first."""
    head = Path(p).read_bytes()[:8]
    if not head.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):     # not a workbook: CSV / tab text named .xls(x)
        return _read_csv(p)
    try:
        sheets = [df.map(_xl_text) if hasattr(df, "map") else df.applymap(_xl_text)    # numbers as typed: 1e-07 F
                  for df in pd.read_excel(p, header=None, dtype=object, sheet_name=None).values()]  # -> '100n', not '1e-07'
    except ImportError as e:                                              # old binary .xls needs xlrd (not a dependency)
        raise ValueError(f"{p}: can't read this old .xls workbook ({e}); save it as .xlsx or CSV") from e
    except Exception as e:                                                # truncated / corrupt zip, missing parts
        raise ValueError(f"{p}: not a readable Excel workbook ({type(e).__name__}: {e})") from e
    return max(sheets, key=_header_score) if sheets else pd.DataFrame()


TOTAL = re.compile(r"(grand\s*|sub\s*)?totals?\s*:?", re.I)


def _is_total(d):
    """A totals footer: some cell says 'Total' and the row names no part."""
    return (not any(d.get(f) for f in ("mpn", "lcsc", "value", "libref", "footprint", "manufacturer"))
            and any(TOTAL.fullmatch(str(v).strip()) for v in d.values()))


def _yes(v):
    """A DNP cell is set when it says anything other than blank / no / 0 / false / fitted."""
    return _norm(v) not in ("", "0", "n", "no", "false", "fitted", "populate", "fit")


GENERIC_LIBREF = re.compile(r"(res(istor)?|cap(acitor)?|ind(uctor)?|led|diode|zener|schottky|fuse|bead|ferrite|"
                            r"xtal|crystal|header|conn(ector)?|sw(itch)?|[npm]mos|npn|pnp|[rcldq])"
                            r"([\s_-]*(pol|semi|var|adj|smd|tvs))*[\s_-]*\d*", re.I)   # Altium generic symbols: Res1, Cap Pol1


def part_numbers(ln):
    """MPN candidates, best first: the MPN column, then an Altium 'Comment'/value that is not a
    passive value (ICs carry their part number there, e.g. STM32F103C8T6), then the LibRef."""
    out = [ln.get("mpn", "")]
    v = ln.get("value", "")
    if v and parse_value(v) is None and re.search(r"[A-Za-z]", v) and re.search(r"\d", v) and " " not in v.strip():
        out.append(v)
    lib = ln.get("libref", "").strip()
    if not GENERIC_LIBREF.fullmatch(lib):
        out.append(lib)
    return [c for c in dict.fromkeys(c.strip() for c in out) if c]


def count_refs(s):
    """'R1, R2, R5-R8' -> 6 (ranges like R5-R8 or R5-8 count every designator)."""
    n = 0
    for tok in re.split(r"[,;\s]+", re.sub(r"\s*[\u2013\u2014]\s*", "-", s.strip())):   # 'R1–R4' (en dash)
        m = re.fullmatch(r"([A-Za-z]+)(\d+)(?:-(?:\1)?(\d+))?", tok)
        if m:
            n += int(m.group(3)) - int(m.group(2)) + 1 if m.group(3) and int(m.group(3)) >= int(m.group(2)) else 1
    return n


def triage(lines, index, provs, http):
    rows = []
    for i, ln in enumerate(lines, 1):
        if ln.get("dnp"):
            rows.append({"line": i, "designator": ln.get("designator", ""), "qty": ln.get("qty", ""),
                         "mpn": ln.get("mpn", ""), "manufacturer": ln.get("manufacturer", ""), "lcsc": ln.get("lcsc", ""),
                         "value": ln.get("value", ""), "package": "", "status": "dnp (not fitted)",
                         "local_symbol": "", "local_footprint": "", "src": "", "src_symbol": "", "src_footprint": "",
                         "src_3d": "", "src_mpn": "", "notes": "marked do-not-populate; not triaged"})
            continue
        pns = part_numbers(ln)
        pkg = package_of(ln.get("footprint"), ln.get("description"), ln.get("value"), *pns)
        cls = re.match(r"[A-Z]+", ln.get("designator", "").upper())
        sym = how = None
        for pn in pns or [""]:
            sym, how = index.match(pn, ln.get("lcsc", ""), ln.get("value", ""), pkg, ref_class=cls.group(0) if cls else "")
            if sym:
                break
        fp_key = None
        if sym:
            fp_key, _ = index.footprint(sym.props.get("Footprint", ""))
        if not fp_key:
            fp_key, _ = index.footprint(ln.get("footprint", ""))
        gen_fp = None if fp_key else index.find_footprint_for_package(
            pkg, cls.group(0) if cls else "", hint=f"{ln.get('footprint', '')} {ln.get('description', '')}")
        notes, src, errored, work, unconfigured = [], None, False, dict(ln), []
        if pns and not ln.get("mpn"):
            work["mpn"] = pns[0]                               # let providers search by the Comment/LibRef part number
            notes.append(f"part number taken from {'Comment/value' if pns[0] == ln.get('value', '').strip() else 'LibRef'}: {pns[0]}")
        if ln.get("lcsc_invalid"):
            notes.append(f"ignored malformed LCSC '{ln['lcsc_invalid']}'")
        if not (sym and fp_key):
            for prov in provs:
                if isinstance(prov, P.JLCPCBSearch) and work.get("lcsc"):
                    continue                                   # already have an LCSC number
                if isinstance(prov, P.EasyEDA) and not work.get("lcsc"):
                    continue                                   # EasyEDA is keyed by LCSC number only
                try:
                    r = prov.lookup(work)
                except P.NotConfigured:
                    unconfigured.append(prov.name)
                    continue
                except NotImplementedError as e:
                    notes.append(str(e)[:80])
                    continue
                if r.error:
                    errored = True
                    notes.append(f"{prov.name}: {r.error}")
                elif isinstance(prov, P.JLCPCBSearch):
                    if r.found:
                        work["lcsc"] = r.lcsc
                    notes.append(f"jlcpcb: MPN -> {r.lcsc}" if r.found else "jlcpcb: no exact MPN match")
                else:
                    src = r
                    if r.found and ln.get("mpn") and r.mpn and _n(r.mpn) != _n(ln["mpn"]):
                        notes.append(f"MPN mismatch: BOM {ln['mpn']} vs {prov.name} {r.mpn}")
                    if r.found and r.has_footprint:
                        break

        if sym and fp_key:
            status = "in_library"
        elif sym and gen_fp:
            status = "in_library (generated footprint)"
        elif src and src.found and src.has_footprint:
            status = "available_easyeda" if src.has_symbol else "easyeda_footprint_only"
        elif errored:
            status = "unverified (source unreachable)"
        else:
            status = "needs_footprint_generation"
        if unconfigured:
            notes.append("not configured: " + ",".join(unconfigured))
        if sym and not fp_key:
            notes.append(f"local symbol OK but footprint '{sym.props.get('Footprint', '')}' not in indexed libs")
        if gen_fp and not sym:
            notes.append(f"generated footprint exists: {gen_fp}")
        rows.append({
            "line": i, "designator": ln.get("designator", ""), "qty": ln.get("qty", ""),
            "mpn": ln.get("mpn") or work.get("mpn", ""), "manufacturer": ln.get("manufacturer", ""), "lcsc": ln.get("lcsc") or (f"{work['lcsc']} (via MPN)" if work.get("lcsc") else ""),
            "value": ln.get("value", ""), "package": pkg or "",
            "status": status,
            "local_symbol": f"{sym.lib}:{sym.name} ({how})" if sym else "",
            "local_footprint": fp_key or (f"{gen_fp} (generated, by package)" if gen_fp else ""),
            "src": src.provider if src else "", "src_symbol": _yn(src and src.has_symbol),
            "src_footprint": _yn(src and src.has_footprint), "src_3d": _yn(src and src.has_3d),
            "src_mpn": src.mpn if src else "", "notes": "; ".join(dict.fromkeys(notes)),
        })
    return rows


def _n(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _yn(v):
    return "" if v is None or v == "" else ("yes" if v else "no")


def write_reports(rows, mapping, provs, http, out, bom):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        w = SS.DictWriter(f, fieldnames=list(rows[0]) if rows else ["line", "status"])   # no formulas from BOM cells
        w.writeheader()
        w.writerows(rows)
    counts = pd.Series([r["status"] for r in rows]).value_counts()
    md = [f"# BOM triage - `{bom}`", "", "## Summary", ""]
    md += [f"- **{k}**: {v}" for k, v in counts.items()]
    md += ["", "## Header mapping", "", "| field | BOM column |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in mapping.items()]
    md += ["", "## Lines", "", "| # | Ref | MPN | LCSC | Pkg | Status | Local symbol | Local footprint | Src sym/fp/3D | Notes |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s3 = "/".join(x or "-" for x in (r["src_symbol"], r["src_footprint"], r["src_3d"]))
        md.append(f"| {r['line']} | {r['designator'][:18]} | {r['mpn']} | {r['lcsc']} | {r['package']} | {r['status']} | "
                  f"{r['local_symbol']} | {r['local_footprint']} | {s3} | {r['notes']} |")
    md += ["", "## Providers", "", "| provider | configured | auth |", "|---|---|---|"]
    md += [f"| {p.name} | {'yes' if p.configured() else 'no'} | {p.auth} |" for p in provs]
    hosts = {}
    for m, u, st, cached in http.log:
        h = re.sub(r"^https?://([^/]+).*", r"\1", u)
        hosts.setdefault(h, []).append(("cache " if cached else "") + str(st))
    md += ["", "## HTTP (this run)", ""] + [f"- `{h}`: {len(v)} requests -> {sorted(set(v))}" for h, v in hosts.items()]
    out.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Triage a BOM against a local KiCad library and part sources")
    ap.add_argument("bom")
    ap.add_argument("--lib", action="append", default=[], help="KiCad library dir (repeatable)")
    ap.add_argument("-o", "--out", default="out/triage", help="report path stem (.md and .csv written)")
    ap.add_argument("--providers", default="jlcpcb_search,easyeda,digikey,mouser,nexar,snapmagic,samacsys")
    ap.add_argument("--offline", action="store_true", help="use HTTP cache only")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--rate", type=float, default=1.0, help="min seconds between requests per host")
    ap.add_argument("--rules", help="library rules file; its [bom] columns name your BOM headers (default: ecadlib.toml found from here)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON (bom, lines, mapping, counts, rows, reports)")
    a = ap.parse_args(argv)

    from . import rules as RU
    http = CachedHTTP(a.cache, min_interval=a.rate, offline=a.offline)
    want = a.providers.split(",")
    provs = [c(http) for c in P.ALL if c.name in want]
    rules = RU.load(a.rules, start=Path(a.out).parent)
    try:
        lines, mapping = read_bom(a.bom, RU.bom_columns(rules), rules)
    except (ValueError, OSError) as e:                   # unreadable / headerless BOM: a message, not a traceback
        print(f"cannot read BOM: {e}", file=sys.stderr)
        return 2
    index = KiCadIndex(a.lib)
    say = (lambda *x: None) if a.json else print
    say(f"BOM lines: {len(lines)}  header map: {mapping}")
    say(f"local index: {len(index.symbols)} symbols, {len(index.footprints)} footprints")
    rows = triage(lines, index, provs, http)
    counts = write_reports(rows, mapping, provs, http, a.out, a.bom)
    if a.json:                                           # stable shape: README "Machine-readable output (--json)"
        print(json.dumps({"bom": str(a.bom), "lines": len(rows), "mapping": mapping,
                          "counts": {str(k): int(v) for k, v in counts.items()}, "rows": rows,
                          "reports": [str(Path(a.out).with_suffix(x)) for x in (".md", ".csv")]}, indent=2, default=str))
        return 0
    for r in rows:
        print(f"{r['line']:>2} {r['mpn'][:24]:<24} {r['lcsc']:<9} {r['status']:<34} {r['notes'][:90]}")
    print("\n" + counts.to_string())
    print(f"\nwrote {Path(a.out).with_suffix('.md')} and .csv")


if __name__ == "__main__":
    sys.exit(main())
