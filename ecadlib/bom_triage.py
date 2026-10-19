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
csv.field_size_limit(max(csv.field_size_limit(), 64 * 1024 * 1024))   # a long notes cell (> 128 KiB) is still a BOM


def _norm(h):
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


def _hnorm(h):
    """A header normalised for matching: '#' and 'P/N' mean part number ('Mfr. #' is the MPN, not the maker)."""
    return _norm(re.sub(r"(?i)\bp\s*/\s*n\b", " pn ", str(h)).replace("#", " no "))


def map_headers(headers, threshold=0.72):
    """Fuzzy, case-insensitive header -> field mapping. Returns {field: header}."""
    scores = []
    for h in headers:
        nh = _hnorm(h)
        if len(nh) > 80 or "<" in str(h):          # a sentence or a line of markup is not a column header
            continue
        for f, syns in FIELDS.items():
            best = 0.0
            for s in syns:
                if nh == s:
                    sc = 0.97 if f == "mpn" and s in ("part number", "pn") else 1.0   # DigiKey: 'Part Number' is
                                                                     # its own code, 'Manufacturer Part Number' the MPN
                elif re.search(rf"\b{re.escape(s)}\b", nh):
                    sc = 0.75 + 0.2 * len(s) / max(len(nh), 1)          # containment, longer = better
                elif len(nh) <= 5 and not (nh.startswith(s) or s.startswith(nh)):
                    sc = 0.0                                             # a short word is not a typo of another:
                                                                         # 'Mount' (SMD/THT) is not 'Amount'
                else:
                    sc = difflib.SequenceMatcher(None, nh, s).ratio() * 0.95
                best = max(best, sc)
            if f == "qty" and re.search(r"\b(total|build|order|extended|ext|package|pkg|reel|moq|spq|min|minimum|"
                                        r"stock|available|on hand)\b", nh):
                best *= 0.6                                              # 'Total Qty' (all boards), 'Package Qty'
            if f == "mpn" and re.search(r"\b(internal|company|our|house|stock|item|erp|sap|customer|cust|in house)\b", nh):
                best *= 0.5                                              # 'Internal PN' is a house number
            if f == "footprint" and re.search(r"\b(qty|quantity|count|pcs|size|weight)\b", nh):
                best *= 0.5                                              # 'Package Qty' is a reel size
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
    s = format(x, ".15g")                                  # Excel keeps 15 significant digits: 123456.789, not
    if "e" in s:                                           # 123456.789000000004
        s = f"{float(s):.15f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


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


def _decode_line(b):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1")


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
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:                                            # Excel 'CSV' on Windows is cp1252; decided
        text = "".join(_decode_line(ln) for ln in data.splitlines(keepends=True))   # per line: one cp1252 byte in a
                                                                          # UTF-8 file must not turn 10µF into 10ÂµF
    head = text.lstrip()[:4096].lower()
    if re.match(r"(<\?xml[^>]*>\s*)?<(!doctype\s+html|html\b|table\b)", head) or \
            "urn:schemas-microsoft-com:office:spreadsheet" in head:      # '.xls' saved by web tools: HTML / XML 2003
        raise ValueError(f"{p}: this file is an HTML page or Excel 2003 XML, not a CSV or workbook; open it in Excel "
                         "and save it as .xlsx or CSV")
    # keepends: a quoted cell may span lines. Records end only at \r\n, \n or \r (as for csv / Excel): not at the
    # other Unicode line breaks str.splitlines knows (U+2028, form feed, U+0085 pasted from Word / a PDF)
    lines = [ln for ln in re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text) if ln.strip()]
    if not lines:
        raise ValueError(f"{p}: the BOM file is empty")

    def score(d):                                                         # rows sharing the commonest width > 1
        widths = [len(r) for r in csv.reader(lines[:30], delimiter=d)]
        common = max(set(widths), key=widths.count)
        return (widths.count(common) if common > 1 else 0, common)

    try:
        delim = max(DELIMS, key=score)
        rows = list(csv.reader(lines, delimiter=delim))
    except csv.Error as e:                                                # not a ValueError: would be an 'internal error'
        raise ValueError(f"{p}: not a readable CSV ({e})") from e
    width = max(len(r) for r in rows)
    rows = [[re.sub(r"\s*[\r\n\u2028\u2029\x85\x0b\x0c\x1c-\x1e]+\s*", " ", c) for c in r] for r in rows]   # 'RES 10k
    df = pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=str)                         # <newline>0603'
    df.attrs["text"] = True                                               # cells past the header are an unquoted comma
    return df


def apply_columns(mapping, headers, columns):
    """Rules' [bom] columns (field -> your header, or a list of headers: the first one in this BOM is used) win over
    the fuzzy guess; headers not in this BOM are ignored."""
    for f, want in (columns or {}).items():
        wants = [want] if isinstance(want, str) else list(want)
        h = next((h for w in wants for h in headers if _norm(h) == _norm(w)), None)
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
    if p.suffix.lower() == ".kicad_sch" or (p.suffix.lower() in (".xml", ".net") and _BI().is_kicad_netlist(p)):
        bi = _BI()                                                # KiCad schematic / netlist: no header guessing
        return bi.read_kicad_sch(p, columns) if p.suffix.lower() == ".kicad_sch" else bi.read_kicad_netlist(p, columns)
    if p.suffix.lower() in (".kicad_pro", ".kicad_pcb", ".kicad_prl"):   # a KiCad project: its root schematic
        sch = p.with_suffix(".kicad_sch")
        if sch.is_file():
            return _BI().read_kicad_sch(sch, columns)
        raise ValueError(f"{p}: a KiCad {p.suffix} file is not a BOM and {sch.name} is not next to it; "
                         "give the project's .kicad_sch (or a BOM export)")
    raw = _read_excel(p, columns) if p.suffix.lower() in (".xlsx", ".xlsm", ".xls") else _read_csv(p)
    text_src = bool(raw.attrs.get("text"))
    raw = raw.fillna("")
    if raw.empty or not raw.astype(str).apply(lambda c: c.str.strip()).ne("").any().any():
        raise ValueError(f"{p}: the BOM file is empty")
    hdr_row = max(range(min(10, len(raw))), key=lambda i: len(apply_columns(map_headers(list(raw.iloc[i])),   # header
                                                                      [str(h).strip() for h in raw.iloc[i]], columns)))  # may not be row 0
    headers = [str(h).strip() for h in raw.iloc[hdr_row]]
    two = _two_row_header(headers, list(raw.iloc[hdr_row + 1]) if hdr_row + 1 < len(raw) else [], columns)
    if two:                                                       # 'Manufacturer' merged over 'Name | Part Number'
        headers = two
    df = raw.iloc[hdr_row + (2 if two else 1):].copy()
    df.columns = headers
    mapping = apply_columns(map_headers(headers), headers, columns)
    if not {"designator", "mpn", "value"} & mapping.keys():
        raise ValueError(f"{p}: no BOM header found (need a Designator, MPN or Value/Comment column)")
    _warn_unmatched_columns(p, headers, columns)
    col = {f: headers.index(h) for f, h in mapping.items()}       # by position: a BOM may repeat a header (a 2nd 'MPN')
    hlen = max((j + 1 for j, h in enumerate(headers) if h), default=0)
    suppliers = _supplier_pairs(headers)
    lines = []
    for idx, row in df.iterrows():
        cells = [str(x).strip() for x in row]
        note, shifted = [], False
        n = max((j + 1 for j, c in enumerate(cells) if c), default=0)
        if text_src and n > hlen:                                 # more cells than the header: an unquoted '4,7k'
            vj = col.get("value")
            if (n == hlen + 1 and vj is not None and vj + 1 < len(cells) and re.fullmatch(r"\d+", cells[vj])
                    and re.fullmatch(r"\d+\s*[pnuµμmkKMGR]?[A-Za-zΩ%]*", cells[vj + 1])):
                cells = cells[:vj] + [f"{cells[vj]},{cells[vj + 1]}"] + cells[vj + 2:] + [""]
                note.append(f"value {cells[vj]} was split by an unquoted decimal comma; read as one cell")
            else:
                shifted = True
                note.append(f"row {idx + 1} has {n} cells but the header has {hlen} (an unquoted comma?): its MPN and "
                            "footprint were not used; quote the cell or fix the row")
        d = {f: SS.unquote(cells[j]) for f, j in col.items()}     # "'=X" from our own reports
        if not any(d.values()):
            continue
        if shifted:
            for f in ("mpn", "footprint", "lcsc", "manufacturer", "libref", "datasheet"):
                d.pop(f, None)
        if all(_norm(cells[j]) == _norm(headers[j]) for j in col.values()):
            continue                                              # the header again (BOMs pasted together, page breaks)
        if suppliers and not d.get("lcsc"):                       # Altium: Supplier 1 = LCSC, Supplier Part Number 1
            for nj, pj in suppliers:
                if re.fullmatch(r"(lcsc|jlc|jlcpcb)(\s*(com|electronics|assembly))?", _norm(cells[nj])) and \
                        re.fullmatch(r"C\d+", cells[pj], re.I):
                    d["lcsc"] = cells[pj]
                    mapping.setdefault("lcsc", f"{headers[pj]} (where {headers[nj]} is LCSC)")
                    break
        if d.get("mpn"):
            d["mpn"], extra = _split_mpn(d["mpn"])
            note += extra
            if not d["mpn"]:
                del d["mpn"]
        if note:
            d["bom_note"] = "; ".join(note)
        if lines and d.get("designator") and not any(v for f, v in d.items() if f != "designator"):
            prev, q = lines[-1], re.match(r"\s*(\d+)", lines[-1].get("qty", ""))
            if q and count_refs(prev["designator"]) < int(q.group(1)):
                prev["designator"] += ", " + d["designator"]            # designator list wrapped onto the next row
                continue
        if _EXP.fullmatch(d.get("value", "")):                         # a CSV that kept Excel's 1e-07
            d["value"] = _plain_number(d["value"])
        q = re.fullmatch(r"(\d+)(?:,0{1,2}|\.0+)", d.get("qty", ""))
        if q:                                                            # '1,0' / '2,00' (European Excel), '1.0':
            d["qty"] = q.group(1)                                        # not 10 / 200 pieces ('1,000' stays 1000)
        if _is_total(d) or not any(v for f, v in d.items() if f not in ("qty", "dnp", "bom_note")):
            continue                                                     # 'Total;;;12' footer / a bare sum, not a part
        if "dnp" in d:
            d["dnp"] = _yes(d["dnp"])
        elif DNP_MARK.search(d.get("value", "")) or DNP_MARK.fullmatch(d.get("mpn", "")):
            d["dnp"] = True                                              # no DNP column: Value 'DNP' / '10k (DNF)'
        elif re.fullmatch(r"0+([.,]0+)?", d.get("qty", "")):
            d["dnp"] = True                                              # no DNP column, Quantity 0: an Altium variant
                                                                         # BOM's 'not fitted'
        if d.get("lcsc") and not re.fullmatch(r"C\d+", d["lcsc"], re.I):
            d["lcsc_invalid"] = d.pop("lcsc")
        lines.append(d)
    return lines, mapping


def _BI():
    from . import bom_inputs
    return bom_inputs


def _two_row_header(top, sub, columns=None):
    """Header cells for a two-row header ('Manufacturer' merged over 'Name | Part Number' -> 'Manufacturer Name',
    'Manufacturer Part Number'), or None when the row under the header is not a second header row (data rows carry
    digits: designators, quantities, values)."""
    sub = [str(c).strip() for c in sub] + [""] * max(0, len(top) - len(sub))
    if sum(1 for c in sub if c) < 2 or any(re.search(r"\d", c) for c in sub):
        return None
    out, group = [], ""
    for t, s in zip(top, sub):
        group = t or group                                        # a merged cell holds its text in the first column
        out.append(f"{group} {s}".strip() if s else t)
    score = lambda hs: len(apply_columns(map_headers(hs), hs, columns))
    return out if score(out) > score(list(top)) else None


def _warn_unmatched_columns(p, headers, columns):
    """The rules' [bom] columns name a header this BOM does not have: say so (else the field is silently missing)."""
    for f, want in (columns or {}).items():
        wants = [want] if isinstance(want, str) else list(want)
        if not any(_norm(h) == _norm(w) for w in wants for h in headers):
            have = ", ".join(h for h in headers if h)[:200]
            print(f"  warning: ecadlib.toml [bom] columns: {f} = {want!r} is not a column of {Path(p).name} "
                  f"(columns: {have})", file=sys.stderr)


def _supplier_pairs(headers):
    """[(name column, part number column)] for Altium's 'Supplier 1' / 'Supplier Part Number 1' pairs."""
    names, pns = {}, {}
    for j, h in enumerate(headers):
        nh = _hnorm(h)
        m = re.fullmatch(r"(?:supplier|vendor|distributor)(?: name)?\s*(\d*)", nh)
        if m:
            names.setdefault(m.group(1), j)
        m = re.fullmatch(r"(?:supplier|vendor|distributor)\s*(?:part number|part no|pn|order code|order no|sku|no)\s*(\d*)", nh)
        if m:
            pns.setdefault(m.group(1), j)
    return [(names[k], pns[k]) for k in sorted(names) if k in pns]


_OR_EQ = re.compile(r"\s*[(\[]?\s*(?:or\s+(?:equivalent|equiv\.?|eq\.?|similar|equal)|o\.?\s*e\.?|or\s+eqv\.?)\s*[)\]]?\s*$", re.I)
_PN = re.compile(r"(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-./#()+_]{3,}")


def _split_mpn(mpn):
    """'A / B', 'A, B', 'A or B' -> ('A', [note]); 'A or equivalent' -> ('A', [note]); else (mpn, [])."""
    core = _OR_EQ.sub("", mpn).strip()
    notes = [f"'{mpn}': 'or equivalent' dropped from the MPN"] if core != mpn.strip() and core else []
    core = core or mpn.strip()
    parts = [x.strip() for x in re.split(r"\s+/\s+|\s*;\s*|,\s+|\s+or\s+", core) if x.strip()]
    if len(parts) > 1 and all(_PN.fullmatch(x) for x in parts):
        return parts[0], [f"MPN cell lists several part numbers ({core}): {parts[0]} used, alternates "
                          f"{', '.join(parts[1:])}"]
    return core, notes


def _header_score(raw, columns=None):
    def n(i):
        hs = [str(h).strip() for h in raw.iloc[i]]
        return len(apply_columns(map_headers(hs), hs, columns))   # the rules' [bom] columns count (house headers)
    return max((n(i) for i in range(min(10, len(raw)))), default=0)


MAX_UNZIPPED = 256 * 1024 * 1024        # a BOM workbook unpacks to a few MB; more is a zip bomb, not a BOM
MAX_XL_CELLS = 2_000_000


def _compact(cells, where):
    """{(row, col): text} -> rows of text with the empty rows and columns left out (a sheet with one cell at
    XFD1048576 is two cells, not a 17-billion-cell grid)."""
    rows, cols = sorted({r for r, _ in cells}), sorted({c for _, c in cells})
    if len(rows) * len(cols) > MAX_XL_CELLS:
        raise ValueError(f"{where}: the sheet is {len(rows)} rows x {len(cols)} columns; is this really a BOM?")
    ci = {c: k for k, c in enumerate(cols)}
    out = [[""] * len(cols) for _ in rows]
    ri = {r: k for k, r in enumerate(rows)}
    for (r, c), v in cells.items():
        out[ri[r]][ci[c]] = v
    return out


def _uncached_message(p, sheet, refs):
    what = "holds a formula" if len(refs) == 1 else "hold formulas"
    return (f"{p}: sheet {sheet!r}: {', '.join(refs[:5])}{' ...' if len(refs) > 5 else ''} {what} with no "
            "saved result (the workbook was written by a program, not saved by Excel), so their values are unknown; "
            "open it in Excel or LibreOffice, save it, and try again")


def _xlsx_frames(p):
    """[(sheet name, DataFrame of text, [formula cells with no saved value])] of an .xlsx / .xlsm. A sheet whose
    used range is huge (one cell at XFD1048576 in a 5 KB file) is read sparsely instead of as a dense grid."""
    import zipfile
    try:
        with zipfile.ZipFile(p) as z:
            total = sum(i.file_size for i in z.infolist())
            if total > MAX_UNZIPPED:
                raise ValueError(f"{p}: the workbook unpacks to {total // 2**20} MB; a BOM is far smaller "
                                 "(is this the right file?)")
            has_f, grid = False, 0
            for i in z.infolist():
                if i.filename.startswith("xl/worksheets/") and i.filename.endswith(".xml"):
                    x = z.read(i)
                    has_f = has_f or bool(re.search(rb"<(?:\w+:)?f[ >/]", x))
                    rows = [int(r) for r in re.findall(rb"<(?:\w+:)?row\b[^>]*?\br=\"(\d+)\"", x)] or [0]
                    cols = [_col_no(c) for c in set(re.findall(rb"<(?:\w+:)?c\b[^>]*?\br=\"([A-Z]+)\d+\"", x))] or [0]
                    grid = max(grid, max(rows) * max(cols))
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError) as e:
        raise ValueError(f"{p}: not a readable Excel workbook ({type(e).__name__}: {e})") from e
    try:
        if grid > MAX_XL_CELLS:
            frames = _sparse_frames(p)
        else:
            frames = [(n, df.map(_xl_text) if hasattr(df, "map") else df.applymap(_xl_text))   # numbers as typed:
                      for n, df in pd.read_excel(p, header=None, dtype=object, sheet_name=None).items()]  # 1e-07 F -> '100n'
    except ImportError:                                                  # .xlsx without openpyxl: read it ourselves
        unc = {}
        sheets = _BI().xlsx_sheets(p, uncached=unc)
        return [(n, pd.DataFrame(rows, dtype=object), unc.get(n, [])) for n, rows in sheets.items()]
    except Exception as e:                                               # truncated / corrupt zip, missing parts
        if isinstance(e, ValueError) and str(e).startswith(f"{p}:"):    # our own message (_compact)
            raise
        raise ValueError(f"{p}: not a readable Excel workbook ({type(e).__name__}: {e})") from e
    unc = _uncached_formulas(p) if has_f else {}
    return [(n, df, unc.get(n, [])) for n, df in frames]


def _col_no(letters):
    n = 0
    for ch in letters.decode() if isinstance(letters, bytes) else letters:
        n = n * 26 + ord(ch) - 64
    return n


def _sparse_frames(p):
    """Every sheet as a DataFrame of the cells that exist, empty rows and columns left out."""
    import openpyxl
    wb = openpyxl.load_workbook(p, data_only=True)        # not read_only: only the cells in the file are made
    out = []
    for ws in wb.worksheets:
        cells = {k: _xl_text(c.value).strip() for k, c in getattr(ws, "_cells", {}).items() if c.value is not None}
        cells = {k: v for k, v in cells.items() if v}
        out.append((ws.title, pd.DataFrame(_compact(cells, f"{p}: sheet {ws.title!r}") if cells else [], dtype=object)))
    return out


def _uncached_formulas(p):
    """{sheet name: [cells holding a formula with no saved result]}: pandas / openpyxl read those as empty (and
    cannot tell them from a saved empty text, so the sheet XML is read)."""
    out = {}
    try:
        _BI().xlsx_sheets(p, uncached=out)
    except ValueError:                                    # unreadable for the plain reader: nothing more to say
        pass
    return out


def _read_excel(p, columns=None):
    """The sheet that holds the BOM: workbooks often put a cover / notes sheet first. columns: the rules' [bom]
    columns, so a sheet with house headers is found behind a cover sheet."""
    head = Path(p).read_bytes()[:8]
    if not head.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):     # not a workbook: CSV / tab text named .xls(x)
        return _read_csv(p)
    if head.startswith(b"PK"):
        sheets = _xlsx_frames(p)
    else:
        try:
            sheets = [(n, df.map(_xl_text) if hasattr(df, "map") else df.applymap(_xl_text), [])   # numbers as typed:
                      for n, df in pd.read_excel(p, header=None, dtype=object, sheet_name=None).items()]  # 1e-07 F -> '100n'
        except ImportError as e:                                          # old binary .xls needs xlrd (not a dependency)
            raise ValueError(f"{p}: can't read this old .xls workbook ({e}); save it as .xlsx or CSV") from e
        except Exception as e:                                            # truncated / corrupt file
            raise ValueError(f"{p}: not a readable Excel workbook ({type(e).__name__}: {e})") from e
    if not sheets:
        return pd.DataFrame()
    name, df, unc = max(sheets, key=lambda s: _header_score(s[1], columns))
    if unc:
        raise ValueError(_uncached_message(p, name, unc))
    return df


TOTAL = re.compile(r"(grand\s*|sub\s*)?totals?(\s+(parts?|components?|qty|quantity|count|items?|lines?|pcs))?\s*:?\s*\d*",
                   re.I)                                                  # 'Total', 'Total parts:', 'Total Qty', 'Total: 42'


def _is_total(d):
    """A totals footer: some cell says 'Total' and the row names no part."""
    return (not any(d.get(f) for f in ("mpn", "lcsc", "value", "libref", "footprint", "manufacturer"))
            and any(TOTAL.fullmatch(str(v).strip()) for v in d.values()))


DNP_MARK = re.compile(r"(?<![A-Za-z0-9])(dnp|dnf|dni|do\s*not\s*(populate|place|fit|install|mount)|not\s*(fitted|populated|placed))"
                      r"(?![A-Za-z0-9])", re.I)


def _yes(v):
    """A DNP cell is set when it says anything other than blank / no / 0 / false / fitted."""
    return _norm(v) not in ("", "0", "n", "no", "false", "fitted", "populate", "fit")


GENERIC_LIBREF = re.compile(r"(res(istor)?|cap(acitor)?|ind(uctor)?|led|diode|zener|schottky|fuse|bead|ferrite|"
                            r"xtal|crystal|header|conn(ector)?|sw(itch)?|[npm]mos|npn|pnp|[rcldq])"
                            r"([\s_-]*(pol|semi|var|adj|smd|tvs))*[\s_-]*\d*", re.I)   # Altium generic symbols: Res1, Cap Pol1


KICAD_GENERIC = re.compile(r"(Conn|Screw_Terminal|Pin_?Header|Socket)_\d+x\d+\w*|\w+_Small|TestPoint\w*|SW_\w+|"
                           r"MountingHole\w*|(Solder)?Jumper\w*|Fiducial\w*|Crystal_\w+|LED_\w+|D_\w+|Q_\w+", re.I)
                                                                  # KiCad's generic symbol names are not part numbers


PLACEHOLDER_MPN = re.compile(r"\s*(n/?a|none|tbd|tba|tbc|dnp|dnf|n\.?c\.?|generic|any|various|not applicable|-+|\?+|x+)\s*", re.I)


def placeholder_mpn(s):
    """True for an MPN cell that names no part ('N/A', 'TBD', 'Generic', '-'): two such cells are not the same part."""
    return bool(PLACEHOLDER_MPN.fullmatch(str(s or "")))


def part_numbers(ln):
    """MPN candidates, best first: the MPN column, then an Altium 'Comment'/value that is not a
    passive value (ICs carry their part number there, e.g. STM32F103C8T6), then the LibRef. Altium vault item ids
    (CMP-1035-02800-1: the LibRef / Comment of Content Vault and Altium 365 parts) are never part numbers."""
    from .altium_project import is_vault_id
    out = [ln.get("mpn", "")]
    v = ln.get("value", "")
    if v and parse_value(v) is None and re.search(r"[A-Za-z]", v) and re.search(r"\d", v) and " " not in v.strip() \
            and not KICAD_GENERIC.fullmatch(v.strip()):
        out.append(v)
    lib = ln.get("libref", "").strip()
    if not GENERIC_LIBREF.fullmatch(lib):
        out.append(lib)
    return [c for c in dict.fromkeys(c.strip() for c in out) if c and not is_vault_id(c)]


def count_refs(s):
    """'R1, R2, R5-R8' -> 6 (ranges like R5-R8, R5-8 or 'R5 - R8' count every designator). Units of one multi-unit
    part ('U1A, U1B', Altium's suffixed designators) count once, as does a designator listed twice."""
    s = re.sub(r"(?<=[0-9A-Za-z])\s*[-\u2013\u2014~\uff5e]\s*(?=[A-Za-z]*\d)", "-", s.strip())   # 'R1 – R4', 'C1~C4' -> 'R1-R4'
    seen, extra = set(), 0
    for tok in re.split(r"[,;\s]+", s):
        m = re.fullmatch(r"([A-Za-z]+)(\d+)[A-Za-z]?(?:-(?:\1)?(\d+)[A-Za-z]?)?", tok)
        if not m:
            if re.fullmatch(r"[A-Za-z]+\d+[A-Za-z0-9_.]*", tok):   # Altium channel designators (R1_CH1, R1_A): one each
                seen.add(("", tok.upper()))
            continue
        pre, lo = m.group(1).upper(), int(m.group(2))
        hi = int(m.group(3)) if m.group(3) and int(m.group(3)) >= lo else lo
        if hi - lo > 100000:                     # absurd range: count it, don't enumerate it
            extra += hi - lo + 1
        else:
            seen.update((pre, k) for k in range(lo, hi + 1))
    return len(seen) + extra


def ref_keys(s, limit=10000):
    """'R1, R2, R5-R8' -> {'R1', 'R2', 'R5', ..., 'R8'} (units U1A / U1B are U1); an absurd range is left out."""
    s = re.sub(r"(?<=[0-9A-Za-z])\s*[-–—~～]\s*(?=[A-Za-z]*\d)", "-", str(s or "").strip())
    out = set()
    for tok in re.split(r"[,;\s]+", s):
        m = re.fullmatch(r"([A-Za-z]+)(\d+)[A-Za-z]?(?:-(?:\1)?(\d+)[A-Za-z]?)?", tok)
        if not m:
            if re.fullmatch(r"[A-Za-z]+\d+[A-Za-z0-9_.]*", tok):
                out.add(tok.upper())
            continue
        lo = int(m.group(2))
        hi = int(m.group(3)) if m.group(3) and int(m.group(3)) >= lo else lo
        if hi - lo <= limit:
            out.update(f"{m.group(1).upper()}{k}" for k in range(lo, hi + 1))
    return out


def shared_designators(lines):
    """{line number (1-based): note} for designators listed on more than one fitted line (R2 = 10k and R2 = 1k)."""
    where = {}
    for i, ln in enumerate(lines, 1):
        if not ln.get("dnp"):
            for r in ref_keys(ln.get("designator", "")):
                where.setdefault(r, []).append(i)
    out = {}
    for r, ns in sorted(where.items()):
        for i in ns if len(set(ns)) > 1 else ():
            others = ", ".join(str(n) for n in ns if n != i)
            out.setdefault(i, []).append(f"{r} is also on line {others}")
    return {i: "designator on two lines: " + "; ".join(v[:5]) + (" ..." if len(v) > 5 else "") for i, v in out.items()}


def triage(lines, index, provs, http):
    rows = []
    dup = shared_designators(lines)
    for i, ln in enumerate(lines, 1):
        if ln.get("dnp"):
            rows.append({"line": i, "designator": ln.get("designator", ""), "qty": ln.get("qty", ""),
                         "mpn": ln.get("mpn", ""), "manufacturer": ln.get("manufacturer", ""), "lcsc": ln.get("lcsc", ""),
                         "value": ln.get("value", ""), "package": "", "status": "dnp (not fitted)",
                         "local_symbol": "", "local_footprint": "", "src": "", "src_symbol": "", "src_footprint": "",
                         "src_3d": "", "src_mpn": "", "src_description": "", "src_description_from": "", "src_datasheet": "", "notes": "marked do-not-populate; not triaged"})
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
        notes, src, errored, work, unconfigured, desc, sheet, desc_from = [], None, False, dict(ln), [], "", "", ""
        if pns and not ln.get("mpn"):
            work["mpn"] = pns[0]                               # let providers search by the Comment/LibRef part number
            notes.append(f"part number taken from {'Comment/value' if pns[0] == ln.get('value', '').strip() else 'LibRef'}: {pns[0]}")
        if ln.get("altium_mpn_source") == "Comment":
            notes.append("MPN taken from the schematic Comment (the component has no Manufacturer Part Number parameter)")
        if re.fullmatch(r"\d+(\.\d+)?E\+\d+", ln.get("mpn", ""), re.I):     # Excel turned 530470610 into 5.30E+08
            notes.append(f"MPN '{ln['mpn']}' is in Excel scientific notation: its digits were lost; "
                         "re-export the BOM with the MPN column formatted as text")
        if ln.get("lcsc_invalid"):
            notes.append(f"ignored malformed LCSC '{ln['lcsc_invalid']}'")
        if ln.get("bom_note"):
            notes.append(ln["bom_note"])
        if dup.get(i):
            notes.append(dup[i])
        if not (sym and fp_key):
            for prov in provs:
                if isinstance(prov, P.JLCPCBSearch) and work.get("lcsc"):
                    continue                                   # already have an LCSC number
                if isinstance(prov, P.EasyEDA) and not work.get("lcsc"):
                    notes.append("easyeda: skipped (needs an LCSC number)")
                    continue                                   # EasyEDA is keyed by LCSC number only
                try:
                    r = prov.lookup(work)
                except P.NotConfigured:
                    unconfigured.append(prov.name)
                    continue
                except NotImplementedError as e:
                    notes.append(str(e)[:80])
                    continue
                except Exception as e:                         # a third-party plugin (ecadlib.plugins) never stops triage
                    if not getattr(prov, "_plugin", False):
                        raise
                    r = P.SourceResult(prov.name, error=f"plugin error: {type(e).__name__}: {str(e)[:120]}")
                if not isinstance(r, P.SourceResult):
                    r = P.SourceResult(getattr(prov, "name", "?"), error="plugin returned no SourceResult")
                if r.error:
                    errored = True
                    notes.append(f"{prov.name}: {r.error}")
                elif isinstance(prov, P.JLCPCBSearch):
                    if r.found:
                        work["lcsc"] = r.lcsc
                        if not desc and (r.extra or {}).get("description"):
                            desc, desc_from = r.extra["description"], prov.name
                    notes.append(f"jlcpcb: MPN -> {r.lcsc}" if r.found else "jlcpcb: no exact MPN match")
                else:
                    if not (src and src.found and (not r.found or not (r.has_footprint or r.has_symbol))):
                        src = r                                # a found part is kept over a later miss or a later
                                                               # answer without CAD data (official APIs give none)
                    if r.found:
                        if not desc and (r.extra or {}).get("description"):
                            desc, desc_from = r.extra["description"], prov.name
                        sheet = sheet or (r.extra or {}).get("datasheet") or ""
                        if r.lcsc and not work.get("lcsc"):   # an official source (Nexar: LCSC's offer) found the LCSC
                            work["lcsc"] = r.lcsc              # number, so the unofficial JLCPCB search is not needed
                            notes.append(f"{prov.name}: MPN -> {r.lcsc}")
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
            "src_mpn": src.mpn if src else "", "src_description": desc, "src_description_from": desc_from, "src_datasheet": sheet, "notes": "; ".join(dict.fromkeys(notes)),
        })
    return rows


def _n(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _yn(v):
    return "" if v is None or v == "" else ("yes" if v else "no")


def write_reports(rows, mapping, provs, http, out, bom):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = SS.DictWriter(f, fieldnames=list(rows[0]) if rows else ["line", "status"])   # no formulas from BOM cells
        w.writeheader()
        w.writerows(rows)
    counts = pd.Series([r["status"] for r in rows]).value_counts()
    md = [f"# BOM triage - `{bom}`", "", "## Summary", ""]
    md += [f"- **{k}**: {v}" for k, v in counts.items()]
    md += ["", "## Header mapping", "", "| field | BOM column |", "|---|---|"]
    c = lambda s: re.sub(r"\s*[\r\n]+\s*", " ", str(s)).replace("|", "\\|")    # a BOM cell 'A|B' or a newline must
    md += [f"| {c(k)} | {c(v)} |" for k, v in mapping.items()]               # not shift or end the table row
    md += ["", "## Lines", "", "| # | Ref | MPN | LCSC | Pkg | Status | Local symbol | Local footprint | Src sym/fp/3D | Notes |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s3 = "/".join(x or "-" for x in (r["src_symbol"], r["src_footprint"], r["src_3d"]))
        md.append(f"| {r['line']} | {c(r['designator'][:18])} | {c(r['mpn'])} | {c(r['lcsc'])} | {c(r['package'])} | "
                  f"{r['status']} | {c(r['local_symbol'])} | {c(r['local_footprint'])} | {s3} | {c(r['notes'])} |")
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
    ap.add_argument("--providers", help="comma list in order (default: [providers] order in the rules, else "
                    "nexar,digikey,mouser,jlcpcb_search,easyeda: the official APIs first); plugins: `ecadlib plugins list`")
    ap.add_argument("--offline", action="store_true", help="use HTTP cache only")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--rate", type=float, default=1.0, help="min seconds between requests per host")
    ap.add_argument("--rules", help="library rules file; its [bom] columns name your BOM headers (default: ecadlib.toml found from here)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON (bom, lines, mapping, counts, rows, reports)")
    a = ap.parse_args(argv)

    from . import rules as RU
    from . import plugins as PL
    http = CachedHTTP(a.cache, min_interval=a.rate, offline=a.offline)
    rules = RU.load(a.rules, start=Path(a.out).parent)
    try:
        provs = [] if a.providers is not None and not a.providers.strip(",; ") else \
            [c(http) for c in PL.select(a.providers, rules, default=P.DEFAULT_ORDER)]   # --providers "": none (local only)
    except ValueError as e:
        print(f"ecadlib triage: {e}", file=sys.stderr)
        return 2
    from .firstrun import bom_hint
    try:
        lines, mapping = read_bom(a.bom, RU.bom_columns(rules), rules)
    except (ValueError, OSError) as e:                   # unreadable / headerless BOM: a message, not a traceback
        print(f"cannot read BOM: {e}" + bom_hint(e), file=sys.stderr)
        return 2
    if not lines:
        print(f"cannot read BOM: {a.bom}: the BOM has a header but no part rows" + bom_hint("no part rows"), file=sys.stderr)
        return 2
    index = KiCadIndex(a.lib)
    say = (lambda *x: None) if a.json else print
    from . import term as TM
    say(f"BOM: {len(lines)} lines; columns: " + ", ".join(f"{k} = {v!r}" for k, v in mapping.items()))
    if a.lib or TM.VERBOSITY > 0:
        say(f"your KiCad libraries: {len(index.symbols)} symbols, {len(index.footprints)} footprints")
    rows = triage(lines, index, provs, http)
    counts = write_reports(rows, mapping, provs, http, a.out, a.bom)
    if a.json:                                           # stable shape: docs/command-line.md "Machine-readable output (--json)"
        print(json.dumps({"bom": str(a.bom), "lines": len(rows), "mapping": mapping,
                          "counts": {str(k): int(v) for k, v in counts.items()}, "rows": rows,
                          "reports": [Path(a.out).with_suffix(x).as_posix() for x in (".md", ".csv")]}, indent=2, default=str))
        return 0
    print_result(rows, counts, a)


TRIAGE_WORDS = {   # triage status -> (term status, plain words)
    "in_library": ("ready", "in your library"),
    "in_library (generated footprint)": ("ready", "in your library (footprint generated)"),
    "available_easyeda": ("signoff", "found online (EasyEDA)"),
    "easyeda_footprint_only": ("held", "footprint online, no symbol"),
    "unverified (source unreachable)": ("skipped", "not checked: source unreachable"),
    "needs_footprint_generation": ("held", "needs a new footprint"),
}


def print_result(rows, counts, a, t=None):
    """Terminal view: a row per BOM line in plain words (-v: the source notes), the counts, the next step."""
    from . import messages as M, term as TM
    t = t or TM.Term()
    table = []
    for r in rows:
        st, word = TRIAGE_WORDS.get(r["status"], ("info", r["status"].replace("_", " ")))
        table.append([str(r["line"]), t.status(st, word), r["mpn"][:32], r["lcsc"],
                      M.clean(r["notes"])[:200] if t.verbose else ""])
    t.table(table, right=(0,))
    by = {TRIAGE_WORDS.get(str(k), ("", str(k)))[1]: int(v) for k, v in counts.items()}
    unreach = sum(int(v) for k, v in counts.items() if str(k).startswith("unverified"))
    notes = []
    if unreach:
        notes.append((f"{M.plural(unreach, 'line')} could not be checked: the part sources were not reachable"
                      + (" (--offline: only the cache)" if a.offline else ""),
                      "run again online, or add API keys (`ecadlib doctor` lists them)"))
    stem = Path(a.out)
    t.summary(f"{M.plural(len(rows), 'BOM line')}: " + ", ".join(f"{v} {k}" for k, v in by.items()),
              written=[(str(stem.with_suffix(".md")), "triage report"), (str(stem.with_suffix(".csv")), "the same as a table")],
              next=[(f"ecadlib librarian {a.bom} -o ws/librarian/board", "fill the missing parts and build the library")],
              notes=notes)

if __name__ == "__main__":
    sys.exit(main())
