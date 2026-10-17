"""Review 1018: BOM intake edge cases from real exports (hand-made Excel footers, European number formats, DNP markers,
mixed encodings, LCSC-style ranges). Offline. Run:  python3 -m tests.test_review_1018_bom_intake"""
import contextlib, io, json, tempfile
from pathlib import Path

import openpyxl

from ecadlib import bom_triage as T, cli, sourcing as S
from ecadlib.kicad_index import parse_value


def _write(name, data, d=None):
    f = Path(d or tempfile.mkdtemp()) / name
    f.write_bytes(data if isinstance(data, bytes) else data.encode())
    return f


def _cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


def test_quantity_only_footer_is_not_a_part():
    """A hand-made workbook's 'Total parts | | | 3' footer (label in an unmapped Item column) became a BOM line with
    only a quantity, and the librarian planned a part with an empty name for it."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item", "Ref Des", "Value", "Qty", "MPN"])
    ws.append([1, "R1, R2", "10k", 2, "RC0603FR-0710KL"])
    ws.append([2, "U1", None, 1, "LM358DR"])
    ws.append(["Total parts", None, None, 3, None])
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "bom.xlsx"
        wb.save(f)
        lines, _ = T.read_bom(f)
        assert [l["designator"] for l in lines] == ["R1, R2", "U1"], lines
        _cli("librarian", str(f), "-o", f"{d}/lib", "--offline", "--cache", f"{d}/c", "--sources", "standard")
        names = [it["name"] for it in json.loads((Path(d) / "lib/plan.json").read_text())]
        assert "" not in names and len(names) == 2, names
    text = "Designator,Value,Qty,MPN\nR1,10k,1,RC\nTotal parts,,1,\nTotal: 2,,,\nTotal Qty,,2,\n"
    lines, _ = T.read_bom(_write("bom.csv", text))                    # the label in a mapped column
    assert [l["designator"] for l in lines] == ["R1"], lines


def test_dnp_written_in_the_value_or_mpn_cell():
    """No DNP column, but Value / MPN says 'DNP' or '10k DNF' (the KiCad / Altium habit): the line was triaged and
    bought as a fitted part."""
    text = ("Designator,Value,MPN,Qty\nR1,10k,RC0603FR-0710KL,1\nR9,DNP,,1\nR10,10k (DNF),RC0603FR-0710KL,1\n"
            "R11,10k,DNP,1\nC1,100nF NP0,CL05C101JB5NNNC,1\n")
    lines, _ = T.read_bom(_write("bom.csv", text))
    assert [bool(l.get("dnp")) for l in lines] == [False, True, True, True, False], lines
    assert lines[2]["value"] == "10k (DNF)", lines                      # the cell itself is kept as written
    lines, _ = T.read_bom(_write("bom.csv", "Designator,Value,DNP\nR9,DNP,\n"))   # a DNP column decides
    assert lines[0]["dnp"] is False, lines
    rows = T.triage(T.read_bom(_write("bom.csv", text))[0], T.KiCadIndex([]), [], None)
    assert rows[1]["status"].startswith("dnp"), rows[1]


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:
                fails += 1
                print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if fails else 0)
