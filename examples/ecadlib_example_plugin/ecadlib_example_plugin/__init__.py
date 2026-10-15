"""Example ecadlib plugin (see ecadlib/plugins.py for the interface).

StockroomCSV: a part source that answers from a CSV of parts you already stock and have CAD data for
    (columns: mpn, manufacturer, package, footprint, symbol, url). Path in $ECADLIB_STOCKROOM_CSV.
    Use it first:  [providers] order = ["stockroom", "jlcpcb_search", "easyeda"]

markdown_catalog: an exporter that writes catalog.md, one table row per plan part.
    ecadlib plugins export markdown-catalog out/lib/plan.json -o out/catalog"""
import csv, os
from pathlib import Path

from ecadlib.providers import NotConfigured, Provider, SourceResult


class StockroomCSV(Provider):
    name = "stockroom"
    auth = "none: a local CSV named by $ECADLIB_STOCKROOM_CSV"
    env = ("ECADLIB_STOCKROOM_CSV",)

    def __init__(self, http):
        super().__init__(http)                          # self.http: ecadlib's cached HTTP client (unused here)
        self._rows = None

    def _table(self):
        if self._rows is None:
            path = os.environ.get("ECADLIB_STOCKROOM_CSV")
            if not path:
                raise NotConfigured("stockroom: set ECADLIB_STOCKROOM_CSV to your stock CSV")
            with open(path, newline="", encoding="utf-8-sig") as f:
                self._rows = {r.get("mpn", "").strip().upper(): r for r in csv.DictReader(f) if r.get("mpn")}
        return self._rows

    def lookup(self, line):
        mpn = (line.get("mpn") or "").strip()
        if not mpn:
            return SourceResult(self.name, found=None, error="no MPN")
        r = self._table().get(mpn.upper())
        if not r:
            return SourceResult(self.name, found=False, mpn=mpn)
        yes = lambda k: (r.get(k) or "").strip().lower() in ("1", "yes", "y", "true")
        return SourceResult(self.name, found=True, mpn=r["mpn"], manufacturer=r.get("manufacturer", ""),
                            package=r.get("package", ""), has_symbol=yes("symbol"), has_footprint=yes("footprint"),
                            has_3d=False, url=r.get("url", ""))


def markdown_catalog(items, out_dir, rules):
    """Markdown catalog of the plan (name, MPN, manufacturer, footprint, status)."""
    def par(it, n):
        return next((p.get("value", "") for p in it.get("parameters", []) if p.get("name") == n), "")
    cell = lambda s: str(s or "").replace("|", "\\|").replace("\n", " ")
    rows = ["| Part | MPN | Manufacturer | Footprint | Status |", "|---|---|---|---|---|"]
    rows += [f"| {cell(it.get('name'))} | {cell(par(it, 'Manufacturer Part Number'))} | {cell(par(it, 'Manufacturer'))} | "
             f"{cell((it.get('footprint') or {}).get('ref'))} | {cell(it.get('status'))} |" for it in items]
    out = Path(out_dir) / "catalog.md"
    out.write_text("# Library catalog\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return [out]
