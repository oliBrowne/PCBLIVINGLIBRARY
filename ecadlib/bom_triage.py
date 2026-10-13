"""BOM triage: what's already in the local KiCad library, what LCSC/EasyEDA can supply,
and what needs a footprint generated.

    python -m ecadlib.bom_triage examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints \
        -o out/triage [--offline] [--providers easyeda,jlcpcb_search,digikey,...]
"""
import argparse, csv, difflib, re, sys
from pathlib import Path

import pandas as pd

from . import providers as P
from .httpcache import CachedHTTP
from .kicad_index import KiCadIndex, package_of

FIELDS = {   # canonical field -> header synonyms (normalised: lowercase, punctuation -> space)
    "mpn": ["mpn", "manufacturer part number", "mfr part number", "mfr part", "mfg part number", "mfr pn",
            "manufacturer part", "part number", "mfr no", "mfr", "manufacturer pn", "pn"],
    "manufacturer": ["manufacturer", "mfr name", "mfg", "maker", "brand", "vendor", "manufacturer name"],
    "lcsc": ["lcsc", "lcsc part", "lcsc part number", "lcsc part no", "lcsc pn", "jlcpcb part", "jlc part", "lcsc id"],
    "qty": ["qty", "quantity", "count", "qnty", "amount", "pcs"],
    "designator": ["designator", "designators", "reference", "references", "ref", "refdes", "ref des", "parts"],
    "value": ["value", "val", "comment"],
    "footprint": ["footprint", "package", "case", "package case", "pcb footprint"],
    "description": ["description", "desc", "notes"],
}


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
                    sc = 1.0
                elif re.search(rf"\b{re.escape(s)}\b", nh):
                    sc = 0.75 + 0.2 * len(s) / max(len(nh), 1)          # containment, longer = better
                else:
                    sc = difflib.SequenceMatcher(None, nh, s).ratio() * 0.95
                best = max(best, sc)
            if f != "lcsc" and re.search(r"\b(lcsc|jlc|jlcpcb)\b", nh):
                best *= 0.5                                              # 'LCSC Part Number' is not the MPN
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


def _read_csv(p):
    for enc in ("utf-8-sig", "cp1252"):                                  # Excel 'CSV' on Windows is cp1252
        try:
            return pd.read_csv(p, header=None, dtype=str, sep=None, engine="python", encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(p, header=None, dtype=str, sep=None, engine="python", encoding="latin-1")


def read_bom(path):
    p = Path(path)
    raw = (pd.read_excel(p, header=None, dtype=str) if p.suffix.lower() in (".xlsx", ".xlsm", ".xls")
           else _read_csv(p))
    raw = raw.fillna("")
    hdr_row = max(range(min(10, len(raw))), key=lambda i: len(map_headers(list(raw.iloc[i]))))  # header may not be row 0
    headers = [str(h).strip() for h in raw.iloc[hdr_row]]
    df = raw.iloc[hdr_row + 1:].copy()
    df.columns = headers
    mapping = map_headers(headers)
    lines = []
    for _, row in df.iterrows():
        d = {f: str(row[h]).strip() for f, h in mapping.items()}
        if not any(d.values()):
            continue
        if lines and d.get("designator") and not any(v for f, v in d.items() if f != "designator"):
            prev, q = lines[-1], re.match(r"\s*(\d+)", lines[-1].get("qty", ""))
            if q and len(re.findall(r"[A-Za-z]+\d+", prev["designator"])) < int(q.group(1)):
                prev["designator"] += ", " + d["designator"]            # designator list wrapped onto the next row
                continue
        if d.get("lcsc") and not re.fullmatch(r"C\d+", d["lcsc"], re.I):
            d["lcsc_invalid"] = d.pop("lcsc")
        lines.append(d)
    return lines, mapping


def triage(lines, index, provs, http):
    rows = []
    for i, ln in enumerate(lines, 1):
        pkg = package_of(ln.get("footprint"), ln.get("description"), ln.get("value"), ln.get("mpn"))
        sym, how = index.match(ln.get("mpn", ""), ln.get("lcsc", ""), ln.get("value", ""), pkg)
        fp_key = None
        if sym:
            fp_key, _ = index.footprint(sym.props.get("Footprint", ""))
        if not fp_key:
            fp_key, _ = index.footprint(ln.get("footprint", ""))
        cls = re.match(r"[A-Z]+", ln.get("designator", "").upper())
        gen_fp = None if fp_key else index.find_footprint_for_package(pkg, cls.group(0) if cls else "")
        notes, src, errored, work, unconfigured = [], None, False, dict(ln), []
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
            "mpn": ln.get("mpn", ""), "manufacturer": ln.get("manufacturer", ""), "lcsc": ln.get("lcsc") or (f"{work['lcsc']} (via MPN)" if work.get("lcsc") else ""),
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
    with open(out.with_suffix(".csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["line", "status"])
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
    out.with_suffix(".md").write_text("\n".join(md) + "\n")
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
    a = ap.parse_args(argv)

    http = CachedHTTP(a.cache, min_interval=a.rate, offline=a.offline)
    want = a.providers.split(",")
    provs = [c(http) for c in P.ALL if c.name in want]
    lines, mapping = read_bom(a.bom)
    index = KiCadIndex(a.lib)
    print(f"BOM lines: {len(lines)}  header map: {mapping}")
    print(f"local index: {len(index.symbols)} symbols, {len(index.footprints)} footprints")
    rows = triage(lines, index, provs, http)
    counts = write_reports(rows, mapping, provs, http, a.out, a.bom)
    for r in rows:
        print(f"{r['line']:>2} {r['mpn'][:24]:<24} {r['lcsc']:<9} {r['status']:<34} {r['notes'][:90]}")
    print("\n" + counts.to_string())
    print(f"\nwrote {Path(a.out).with_suffix('.md')} and .csv")


if __name__ == "__main__":
    sys.exit(main())
