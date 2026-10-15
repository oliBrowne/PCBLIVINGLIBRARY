"""Spreadsheet-safe report cells (CSV formula injection, OWASP): a cell that starts with = + - @ tab or CR is run as a
formula by Excel / LibreOffice / Google Sheets, so a BOM cell like =HYPERLINK(...) or =cmd|' /C calc'!A0 would run
when someone opens a report. Such cells get a leading apostrophe (Excel's "this is text" mark). Plain numbers
(-5, +3.3, -1.5e-3, 12%) and a lone '-' stay as they are. bom_triage.read_bom strips the apostrophe again, so a BOM
ecadlib writes reads back unchanged.

The DbLib's data files keep Altium's '=Value' / '=Part Number' Comment literal: the .xlsx stores every cell as text
(no spreadsheet evaluates it), and the .csv mirror uses param_refs=True (bare names only; '=HYPERLINK(..)' is still
neutralised)."""
import csv, re

_RISKY = re.compile(r"^[=+\-@\t\r]")
_NUMBER = re.compile(r"^[-+]?(\d+([.,]\d*)?|[.,]\d+)([eE][-+]?\d+)?%?$")
_PARAM_REF = re.compile(r"^=[A-Za-z][A-Za-z0-9 _]*$")     # Altium's '=Value' / '=Part Number': names only, no calls


def cell(v, param_refs=False):
    """The value to write into a report cell. param_refs: keep Altium '=Name' parameter references literal (the DbLib
    data files); they hold no function call or operator, so nothing can run."""
    if (isinstance(v, str) and _RISKY.match(v) and v not in ("-", "+") and not _NUMBER.match(v.strip())
            and not (param_refs and _PARAM_REF.match(v))):
        return "'" + v
    return v


def unquote(v):
    """Undo cell() when reading a BOM back: "'=X" -> "=X" (only for the cells cell() would have changed)."""
    if isinstance(v, str) and v.startswith("'") and _RISKY.match(v[1:]) and cell(v[1:]) == v:
        return v[1:]
    return v


class DictWriter(csv.DictWriter):
    def __init__(self, *a, param_refs=False, **k):
        super().__init__(*a, **k)
        self.param_refs = param_refs

    def writerow(self, rowdict):
        return super().writerow({k: cell(v, self.param_refs) for k, v in rowdict.items()})

    def writerows(self, rowdicts):
        for r in rowdicts:
            self.writerow(r)


class _Writer:
    def __init__(self, w):
        self._w = w

    def writerow(self, row):
        return self._w.writerow([cell(v) for v in row])

    def writerows(self, rows):
        for r in rows:
            self.writerow(r)


def writer(f, *a, **k):
    """csv.writer whose cells go through cell()."""
    return _Writer(csv.writer(f, *a, **k))


def frame(df):
    """A pandas DataFrame with every cell through cell() (for DataFrame.to_csv)."""
    return df.map(cell) if hasattr(df, "map") else df.applymap(cell)
