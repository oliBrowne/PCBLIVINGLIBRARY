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
import argparse, re, sqlite3, sys
from contextlib import closing
from pathlib import Path, PureWindowsPath

import pandas as pd

from .altium_lib import file_stem, lib_name
from .bom_triage import read_bom
from .kicad_index import package_of

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


def kind_of(descriptions=(), footprints=()):
    """Resistors / Capacitors / Inductors / Crystals / Connectors from a source's category words or a chip
    footprint, else None."""
    semi = any(re.match(IC_PACKAGE, str(f or "").rpartition(":")[2].strip()) for f in footprints)
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


def to_rows(lines, triage=None, rules=None):
    """{category: DataFrame of DbLib rows}. rules (ecadlib.rules): part names, footprint names, value style and
    supplier columns; None keeps ecadlib's own. df.attrs["kicad_fp"] maps a part to its footprint's original name
    when the rules renamed it."""
    from . import rules as RU
    custom = rules is not None and not RU.is_default(rules, "naming")
    tri = {}
    if triage is not None:
        for _, t in triage.iterrows():
            if str(t["mpn"]):                  # an empty MPN would hand one row's footprint to every MPN-less line
                tri[str(t["mpn"])] = t
    out, seen, renamed = {}, {}, {}
    for ln in lines:
        prefix = re.match(r"[A-Z]+", ln.get("designator", "").upper() or "U")
        cat = CATEGORY.get(prefix.group(0) if prefix else "U", "Misc")
        mpn, lcsc = ln.get("mpn", ""), ln.get("lcsc", "")
        fp = ln.get("footprint", "")
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
        if custom:
            f = RU.line_fields(ln, cat, pkg, rules, ref)
            pn = (RU.component_name(f, rules) or ln.get("designator", "").split(",")[0]).replace(" ", "")
        else:                                         # an empty token drops out with its dash: IND-10uH, not IND--10uH
            pn = ("-".join(t for t in (cat[:3].upper(), pkg, value) if t)
                  if cat in GENERIC_SYMBOL and value and not (kind and value == mpn)
                  else mpn or value or ln.get("designator", "").split(",")[0]).replace(" ", "")
        if seen.setdefault(pn, mpn) != mpn:      # same synthetic PN, different part (e.g. 100nF 16V vs 50V): keep both
            pn = f"{pn}-{mpn}".replace(" ", "")
        lib_ref = lib_name(GENERIC_SYMBOL.get(cat) or (custom and mpn and RU.symbol_name(f, rules)) or mpn or pn)
        kicad_fp = fp_ref = lib_name(fp_ref)
        if custom:
            fp_ref = lib_name(RU.footprint_name(fp_ref, rules, ref))
            if fp_ref != kicad_fp:
                renamed[pn] = kicad_fp
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
    cols = columns(rules)
    res = {k: pd.DataFrame(v, columns=cols).fillna("").drop_duplicates("Part Number") for k, v in sorted(out.items())}
    for df in res.values():
        df.attrs["kicad_fp"] = renamed
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
    """INI layout copied from real-world .DbLib files (see README 'verified'). tables: table names, or
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
    ap = argparse.ArgumentParser(description="Write an Altium DbLib (SQLite + XLSX) from a BOM")
    ap.add_argument("bom")
    ap.add_argument("-o", "--out", default="out/altium")
    ap.add_argument("--triage", help="triage CSV from bom_triage (to pick up resolved local footprints)")
    ap.add_argument("--win-dir", default=r"C:\ecadlib\altium", help="Windows folder the DB will live in (for the connection string)")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml in the output folder, the current folder or a parent)")
    a = ap.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
