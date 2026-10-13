"""Altium Database Library (DbLib) export prototype.

Altium .SchLib/.PcbLib are binary OLE compound files, so instead of writing them we emit
the parts *database* Altium reads through a .DbLib file (OLE DB / ODBC):

    out/altium/parts.sqlite      one table per category (SQLite; needs the SQLite3 ODBC driver on Windows)
    out/altium/parts.xlsx        same tables as sheets (Excel via the ACE OLE DB provider; tables are 'Sheet$')
    out/altium/ecadlib_sqlite.DbLib, ecadlib_excel.DbLib   INI files pointing Altium at the above

    python -m ecadlib.altium_export examples/sample_bom.csv -o out/altium [--triage out/triage_sample_bom.csv]

Column names use Altium's reserved DbLib names ([Library Ref], [Library Path], [Footprint Ref],
[Footprint Path], [Description]) plus parameters (Part Number = key, Value, Comment, Manufacturer,
Manufacturer Part Number, Supplier 1/Supplier Part Number 1, ComponentLink1URL/Description).
See README for what is verified vs assumed about the .DbLib INI keys."""
import argparse, re, sqlite3
from pathlib import Path, PureWindowsPath

import pandas as pd

from .bom_triage import read_bom
from .kicad_index import package_of

COLUMNS = ["Part Number", "Library Ref", "Library Path", "Footprint Ref", "Footprint Path",
           "Description", "Value", "Comment", "Manufacturer", "Manufacturer Part Number",
           "Supplier 1", "Supplier Part Number 1", "Supplier 2", "Supplier Part Number 2",
           "ComponentLink1Description", "ComponentLink1URL", "ComponentLink2Description", "ComponentLink2URL"]
RESERVED = {"Library Ref", "Library Path", "Footprint Ref", "Footprint Path", "Description"}
CATEGORY = {"R": "Resistors", "C": "Capacitors", "L": "Inductors", "D": "Diodes", "Q": "Transistors",
            "Y": "Crystals", "J": "Connectors", "P": "Connectors", "U": "ICs"}
GENERIC_SYMBOL = {"Resistors": "Resistor", "Capacitors": "Capacitor", "Inductors": "Inductor"}


def to_rows(lines, triage=None):
    tri = {}
    if triage is not None:
        for _, t in triage.iterrows():
            tri[str(t["mpn"])] = t
    out = {}
    for ln in lines:
        prefix = re.match(r"[A-Z]+", ln.get("designator", "").upper() or "U")
        cat = CATEGORY.get(prefix.group(0)[0] if prefix else "U", "Misc")
        mpn, lcsc = ln.get("mpn", ""), ln.get("lcsc", "")
        fp = ln.get("footprint", "")
        t = tri.get(mpn)
        if t is not None and isinstance(t.get("local_footprint"), str) and t["local_footprint"]:
            fp = t["local_footprint"].split(" ")[0]
        fp_ref = fp.split(":")[-1]
        pkg = package_of(fp, ln.get("description")) or ""
        value = ln.get("value", "")
        pn = (f"{cat[:3].upper()}-{pkg}-{value}" if cat in GENERIC_SYMBOL else mpn).replace(" ", "")
        out.setdefault(cat, []).append({
            "Part Number": pn,
            "Library Ref": GENERIC_SYMBOL.get(cat, mpn),
            "Library Path": f"SchLib\\{cat}.SchLib",
            "Footprint Ref": fp_ref,
            "Footprint Path": "PcbLib\\ecadlib.PcbLib",
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
    return {k: pd.DataFrame(v, columns=COLUMNS).drop_duplicates("Part Number") for k, v in sorted(out.items())}


def dblib_text(conn_str, tables, left_quote="[", right_quote="]", excel=False):
    """INI layout copied from real-world .DbLib files (see README 'verified')."""
    L = ["[OutputDatabaseLinkFile]", "Version=1.1", "[DatabaseLinks]", f"ConnectionString={conn_str}",
         "AddMode=3", "RemoveMode=1", "UpdateMode=2", "ViewMode=0",
         f"LeftQuote={left_quote}", f"RightQuote={right_quote}", "QuoteTableNames=1", "UseTableSchemaName=0",
         "DefaultColumnType=VARCHAR(255)", "LibraryDatabaseType=", "LibraryDatabasePath=", "DatabasePathRelative=0",
         "TopPanelCollapsed=0", "LibrarySearchPath=", "OrcadMultiValueDelimiter=,", "SearchSubDirectories=0",
         "SchemaName=", f"LastFocusedTable={tables[0] + ('$' if excel else '')}"]
    fm = 0
    for i, t in enumerate(tables, 1):
        tn = t + ("$" if excel else "")
        L += [f"[Table{i}]", "SchemaName=", f"TableName={tn}", "Enabled=True", "UserWhere=0", "UserWhereText="]
    for t in tables:
        tn = t + ("$" if excel else "")
        for c in COLUMNS:
            fm += 1
            ftype = 0 if c == "Part Number" else 1                      # observed: key column FieldType=0
            pname = f"[{c}]" if c in RESERVED else c
            L += [f"[FieldMap{fm}]",
                  f"Options=FieldName={tn}.{c}|TableNameOnly={tn}|FieldNameOnly={c}|FieldType={ftype}|"
                  f"ParameterName={pname}|VisibleOnAdd=False|AddMode=0|RemoveMode=0|UpdateMode=0"]
    return "\r\n".join(L) + "\r\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write an Altium DbLib (SQLite + XLSX) from a BOM")
    ap.add_argument("bom")
    ap.add_argument("-o", "--out", default="out/altium")
    ap.add_argument("--triage", help="triage CSV from bom_triage (to pick up resolved local footprints)")
    ap.add_argument("--win-dir", default=r"C:\ecadlib\altium", help="Windows folder the DB will live in (for the connection string)")
    a = ap.parse_args(argv)

    lines, _ = read_bom(a.bom)
    tri = pd.read_csv(a.triage, dtype=str).fillna("") if a.triage else None
    tables = to_rows(lines, tri)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    db = out / "parts.sqlite"
    db.unlink(missing_ok=True)
    with sqlite3.connect(db) as con:
        for name, df in tables.items():
            cols = ", ".join(f'"{c}" TEXT' + (" PRIMARY KEY" if c == "Part Number" else "") for c in COLUMNS)
            con.execute(f'CREATE TABLE "{name}" ({cols})')
            con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(COLUMNS))})', df.values.tolist())
    with pd.ExcelWriter(out / "parts.xlsx") as w:
        for name, df in tables.items():
            df.to_excel(w, sheet_name=name, index=False)

    win = PureWindowsPath(a.win_dir)
    sqlite_cs = ('Provider=MSDASQL.1;Persist Security Info=False;'
                 f'Extended Properties="DRIVER=SQLite3 ODBC Driver;Database={win / "parts.sqlite"};LongNames=0;Timeout=1000;NoTXN=0;SyncPragma=NORMAL;StepAPI=0;"')
    excel_cs = (f'Provider=Microsoft.ACE.OLEDB.12.0;Data Source={win / "parts.xlsx"};'
                'Extended Properties="Excel 12.0 Xml;HDR=YES;IMEX=1";Persist Security Info=False')
    names = list(tables)
    (out / "ecadlib_sqlite.DbLib").write_text(dblib_text(sqlite_cs, names, '"', '"'), newline="")
    (out / "ecadlib_excel.DbLib").write_text(dblib_text(excel_cs, names, "[", "]", excel=True), newline="")
    for n_, df in tables.items():
        print(f"{n_:<12} {len(df):>2} parts  e.g. {df.iloc[0]['Part Number']} -> {df.iloc[0]['Footprint Ref']}")
    print(f"wrote {db}, {out/'parts.xlsx'}, {out/'ecadlib_sqlite.DbLib'}, {out/'ecadlib_excel.DbLib'}")


if __name__ == "__main__":
    main()
