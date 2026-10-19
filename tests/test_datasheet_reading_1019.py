"""Datasheet getting and reading, 2026-10-19 (research: /mnt/project-files/research/2026-10-19_datasheet_pdf_research.md):
every official datasheet link is kept and tried;
the PDF's bookmarks steer the page picker; TI's Package Option Addendum gives the pin count by MPN."""
import io, json
from pathlib import Path

from ecadlib import bom_triage as T, datasheet as D, ds_route as R, part_lookup as PL, providers as P
from tests.test_ds_route import pdf_lines

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- every official link
NEXAR_PART = {"mpn": "BAT160A", "manufacturer": {"name": "Nexperia"}, "octopartUrl": "https://octopart.com/x",
              "shortDescription": "Schottky", "bestDatasheet": {"url": "https://assets.nexperia.com/documents/data-sheet/BAT160_SER.pdf"},
              "documentCollections": [
                  {"name": "Datasheets", "documents": [
                      {"name": "Datasheet", "url": "https://assets.nexperia.com/documents/data-sheet/BAT160_SER.pdf", "mimeType": "application/pdf"},
                      {"name": "Datasheet", "url": "https://datasheets.octopart.com/BAT160A-Nexperia.pdf", "mimeType": "application/pdf"},
                      {"name": "Datasheet", "url": "//mm.digikey.com/Volume0/opasdata/d220001/medias/docus/BAT160.pdf", "mimeType": "application/pdf"}]},
                  {"name": "Application Notes", "documents": [{"name": "AN", "url": "https://x/an.pdf", "mimeType": "application/pdf"}]}],
              "specs": [], "sellers": []}


def test_nexar_keeps_every_datasheet_copy_best_first():
    info = PL.nexar_info(NEXAR_PART, "nexar", "USD")
    assert info.datasheet.endswith("BAT160_SER.pdf")
    assert info.datasheets == ["https://assets.nexperia.com/documents/data-sheet/BAT160_SER.pdf",
                               "https://datasheets.octopart.com/BAT160A-Nexperia.pdf",
                               "https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/BAT160.pdf"], info.datasheets
    res = P.from_part_info(info, P.SourceResult("nexar"))
    assert res.extra["datasheet"] == info.datasheets[0] and res.extra["datasheets"] == info.datasheets


def test_digikey_media_links_and_protocol_relative_urls():
    prod = {"ManufacturerProductNumber": "LT8610", "Manufacturer": {"Name": "ADI"}, "Description": {"ProductDescription": "Buck"},
            "DatasheetUrl": "//mm.digikey.com/Volume0/opasdata/d220001/medias/docus/lt8610.pdf",
            "MediaLinks": [{"MediaType": "Datasheets", "Url": "https://www.analog.com/media/en/lt8610.pdf"},
                           {"MediaType": "Product Photos", "Url": "https://x/photo.jpg"}]}
    info = PL.digikey_info(prod, "digikey", "USD")
    assert info.datasheets == ["https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/lt8610.pdf",
                               "https://www.analog.com/media/en/lt8610.pdf"], info.datasheets


def test_the_nexar_query_asks_for_document_collections_and_matches_the_schema():
    assert "documentCollections { name documents { name url mimeType } }" in PL.NexarSourcing.FIELDS
    try:
        from graphql import build_schema, parse, validate
    except ImportError:
        print("skip schema check (pip install graphql-core)")
        return
    schema = build_schema((ROOT / "tests" / "nexar" / "schema.graphql").read_text(), assume_valid_sdl=True)
    for q in (PL.NexarSourcing.Q_PART, PL.NexarSourcing.Q_PART_ALT, PL.NexarSourcing.Q_SEARCH):
        errs = validate(schema, parse(q))
        assert not errs, errs


def test_triage_keeps_every_sources_links_and_the_reader_tries_them_in_order():
    class Src:
        def __init__(self, name, links):
            self.name, self.links = name, links

        def lookup(self, work):
            r = P.SourceResult(self.name, found=True, mpn="LT8610")
            r.extra = {"datasheet": self.links[0], "datasheets": self.links}
            return r
    from ecadlib.kicad_index import KiCadIndex
    provs = [Src("nexar", ["https://www.analog.com/lt8610.pdf", "https://datasheets.octopart.com/lt8610.pdf"]),
             Src("mouser", ["https://www.mouser.com/datasheet/2/609/lt8610.pdf", "https://www.analog.com/lt8610.pdf"])]
    row = T.triage([{"mpn": "LT8610", "designator": "U1"}], KiCadIndex([]), provs, None)[0]
    assert row["src_datasheet"] == "https://www.analog.com/lt8610.pdf"
    assert row["src_datasheet_more"].split() == ["https://datasheets.octopart.com/lt8610.pdf",
                                                 "https://www.mouser.com/datasheet/2/609/lt8610.pdf"], row
    urls = [u for u, _ in D.datasheet_urls({"parameters": []}, {"datasheet": ""}, row, {})]
    assert urls[:3] == ["https://www.analog.com/lt8610.pdf", "https://datasheets.octopart.com/lt8610.pdf",
                        "https://www.mouser.com/datasheet/2/609/lt8610.pdf"], urls       # then the maker URL patterns


def test_ti_redirect_pages_are_unwrapped():
    u = "https://www.ti.com/general/docs/suppproductinfo.tsp?distId=10&gotoUrl=https%3A%2F%2Fwww.ti.com%2Flit%2Fgpn%2Fne555"
    assert D.clean_url(u) == "https://www.ti.com/lit/gpn/ne555"
    assert D.clean_url("//mm.digikey.com/a.pdf") == "https://mm.digikey.com/a.pdf"
    assert D.clean_url("https://www.ti.com/lit/ds/symlink/ne555.pdf") == "https://www.ti.com/lit/ds/symlink/ne555.pdf"
    urls = D.datasheet_urls({"parameters": []}, {"datasheet": u}, {}, {})
    assert urls[0][0] == "https://www.ti.com/lit/gpn/ne555", urls


# ---------------------------------------------------------------- bookmarks
def bookmarked(pages, marks):
    """pdf_lines(pages) with bookmarks: marks = [(title, 0-based page, parent index or None)]."""
    import pypdf
    w = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(pdf_lines(pages))))
    made = []
    for title, pg, parent in marks:
        made.append(w.add_outline_item(title, pg, parent=made[parent] if parent is not None else None))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def mcu_pages(n=80):
    """SYNTHETIC family sheet whose package drawings are pictures (no text layer on those pages), as many big MCU
    datasheets are: only the bookmarks say which page is which package."""
    pages = [["MCU family electrical characteristics", f"page {i + 1}"] for i in range(n)]
    pages[0] = ["TESTMCU family", "Packages: LQFP144, LQFP100, LQFP64, TFBGA100"]
    for i in range(70, 78):
        pages[i] = []                                # drawing pages: vector art, no text
    return pages


MCU_MARKS = [("1. Description", 0, None), ("5. Pin Configuration", 10, None), ("40. Electrical Characteristics", 30, None),
             ("62. Packaging Information", 70, None), ("62.1 LQFP144", 70, 3), ("62.2 TFBGA100", 72, 3),
             ("62.3 LQFP100", 74, 3), ("62.4 LQFP64", 76, 3), ("63. Revision History", 78, None)]


def test_page_marks_give_each_page_its_chapter():
    marks = R.page_marks(bookmarked(mcu_pages(), MCU_MARKS))
    assert marks[74] == "62. Packaging Information > 62.3 LQFP100" and marks[75] == marks[74], marks[74:76]
    assert marks[70] == "62. Packaging Information > 62.1 LQFP144" and marks[5] == "1. Description"
    assert marks[79] == "63. Revision History"
    assert R.page_marks(pdf_lines(mcu_pages())) is None             # no bookmarks: None, text only


def test_bookmarks_find_the_drawing_of_this_package_when_the_page_has_no_text():
    data = bookmarked(mcu_pages(), MCU_MARKS)
    texts, marks = R.page_texts(data), R.page_marks(data)
    before, _ = R.pick_pages(texts, 6, "TESTMCU100", "LQFP-100")
    pages, why = R.pick_pages(texts, 6, "TESTMCU100", "LQFP-100", marks)
    assert 75 in pages and 76 in pages, (pages, why)
    assert not {71, 72, 73, 77, 78} & set(pages), (pages, why)       # the other packages' drawings stay out
    assert any(w.startswith("bookmark: 62.3 LQFP100") for w in why[75]), why
    assert before is None or 75 not in before, before                # text alone could not find it


def test_the_reader_uses_the_bookmarks():
    from unittest import mock
    from tests.test_ds_route import reader
    import tempfile
    data = bookmarked(mcu_pages(), MCU_MARKS)
    with tempfile.TemporaryDirectory() as d:
        rd = reader(d, {"https://a/mcu.pdf": data})
        j = rd.job("TESTMCU100", [("https://a/mcu.pdf", "BOM")], "LQFP-100")
        assert isinstance(j["offset"], list) and 75 in j["offset"], j["offset"]


def test_pages_command_reports_bookmarks():
    import contextlib, tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "mcu.pdf"
        f.write_bytes(bookmarked(mcu_pages(), MCU_MARKS))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert R.pages_main([str(f), "--part", "TESTMCU100", "--package", "LQFP-100", "--budget", "6", "--json"]) == 0
    out = json.loads(buf.getvalue())
    assert out["bookmarks"] is True and 75 in out["steps"][0]["pages"], out


# ---------------------------------------------------------------- TI's Package Option Addendum
def addendum_texts(line):
    t = ["TPS2660x 60V eFuse", "filler"] + ["typical characteristics"] * 40
    t[38] = "PACKAGE OPTION ADDENDUM\nOrderable Device Status Package Type Package Drawing Pins Package Qty Eco Plan\n" + line \
        + "\nTPS26600RGER ACTIVE VQFN RGE 24 3000 RoHS & Green NIPDAU Level-2-260C-1 YEAR -40 to 125 TPS26600"
    return t


def test_ordering_facts_from_old_and_new_ti_addendum_lines():
    old = addendum_texts("TPS26600PWPR ACTIVE HTSSOP PWP 16 2000 RoHS & Green NIPDAU Level-3-260C-168 HR -40 to 125 TPS26600")
    f = R.ordering_facts(old, "TPS26600PWPR")
    assert (f["pins"], f["package"], f["code"], f["page"]) == (16, "HTSSOP", "PWP", 39), f
    assert R.ordering_facts(old, "TPS26600PWP")["pins"] == 16                 # the reel suffix left off
    assert R.ordering_facts(old, "TPS26600RGER")["pins"] == 24                # the other package, not mixed up
    new = addendum_texts("TPS26600PWPR Active Production HTSSOP (PWP) | 16 2000 | LARGE T&R Yes NIPDAU Level-3-260C-168 HR")
    f = R.ordering_facts(new, "TPS26600PWPR")
    assert (f["pins"], f["package"], f["code"]) == (16, "HTSSOP", "PWP"), f
    assert R.ordering_facts(old, "LM317T") is None and R.ordering_facts(["no addendum"] * 5, "TPS26600PWPR") is None


def test_ordering_facts_disagreeing_lines_give_nothing():
    t = addendum_texts("TPS26600PWPR ACTIVE HTSSOP PWP 16 2000 RoHS")
    t[39] = "PACKAGE OPTION ADDENDUM\nTPS26600PWPR ACTIVE HTSSOP PWP 14 2000 RoHS"
    assert R.ordering_facts(t, "TPS26600PWPR") is None


def table_pdf(rows, at=38, n=42):
    """A PDF whose page `at` holds a ruled table (pdfplumber finds tables by their lines), other pages plain text."""
    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = pypdf.PdfWriter()
    font = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                           NameObject("/BaseFont"): NameObject("/Helvetica")}))
    esc = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    for i in range(n):
        page = w.add_blank_page(792, 612)
        if i == at:
            cw, rh, x0, y0 = [110, 50, 90, 60, 40], 20, 40, 560
            ops = [f"BT /F1 9 Tf 40 590 Td (PACKAGE OPTION ADDENDUM) Tj ET", "0.5 w"]
            for r, row in enumerate(rows):
                x = x0
                for c, cell in enumerate(row):
                    y = y0 - r * rh
                    ops.append(f"{x} {y - rh} {cw[c]} {rh} re S")
                    ops.append(f"BT /F1 7 Tf {x + 2} {y - 14} Td ({esc(cell)}) Tj ET")
                    x += cw[c]
            body = "\n".join(ops)
        else:
            body = f"BT /F1 10 Tf 72 540 Td (typical characteristics page {i + 1}) Tj ET"
        cs = DecodedStreamObject()
        cs.set_data(body.encode())
        page[NameObject("/Contents")] = w._add_object(cs)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdfplumber_reads_the_addendum_table_by_column():
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        print("skip (pip install pdfplumber)")
        return
    data = table_pdf([["Orderable Device", "Status", "Package Type", "Package Drawing", "Pins"],
                      ["TPS26600PWPR", "ACTIVE", "HTSSOP", "PWP", "16"],
                      ["TPS26600RGER", "ACTIVE", "VQFN", "RGE", "24"]])
    texts = R.page_texts(data)
    f = R.ordering_facts(texts, "TPS26600PWPR", data)
    assert f and (f["pins"], f["package"], f["code"], f["how"]) == (16, "HTSSOP", "PWP", "pdfplumber table"), f
    assert R.ordering_facts(texts, "TPS26600RGER", data)["pins"] == 24


def test_the_ordering_table_fills_a_blank_pin_count_and_flags_a_different_one():
    facts = {"pins": 16, "package": "HTSSOP", "code": "PWP", "page": 39, "part": "TPS26600PWPR", "how": "text line"}
    pkg, rev = {"pin_count": None}, []
    D.ordering_check(pkg, facts, rev)
    assert pkg["pin_count"] == 16 and "from the ordering table (p39" in rev[0], rev
    assert not R.INCOMPLETE.search(rev[0]) and not R.FAILED.search(rev[0])   # not a reason to read again
    pkg, rev = {"pin_count": 14}, []
    D.ordering_check(pkg, facts, rev)
    assert pkg["pin_count"] == 14 and "differs" in rev[0], rev
    pkg, rev = {"pin_count": 16}, []
    D.ordering_check(pkg, facts, rev)
    assert rev == []


def test_the_reader_reads_the_ordering_table_when_it_makes_the_job():
    import tempfile
    from tests.test_ds_route import reader
    pages = [["TPS2660x eFuse", "page"]] * 40
    pages[38] = ["PACKAGE OPTION ADDENDUM", "TPS26600PWPR ACTIVE HTSSOP PWP 16 2000 RoHS & Green NIPDAU"]
    with tempfile.TemporaryDirectory() as d:
        rd = reader(d, {"https://a/tps.pdf": pdf_lines(pages)})
        j = rd.job("TPS26600PWPR", [("https://a/tps.pdf", "BOM")], "HTSSOP-16")
        assert j["facts"] and j["facts"]["pins"] == 16, j.get("facts")


def test_pdfplumber_reads_ti_s_newer_package_pins_column():
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        print("skip (pip install pdfplumber)")
        return
    data = table_pdf([["Orderable part number", "Status", "Material type", "Package | Pins", "Package qty"],
                      ["BQ25750RRVR", "Active", "Production", "VQFN (RRV) | 36", "3000 | LARGE"],
                      ["BQ25750RRVR.A", "Active", "Production", "VQFN (RRV) | 36", "3000 | LARGE"]])
    f = R.ordering_facts(R.page_texts(data), "BQ25750RRVR", data)
    assert f and (f["pins"], f["package"], f["code"], f["how"]) == (36, "VQFN", "RRV", "pdfplumber table"), f


def test_pdf_render_pypdfium2_and_fallback():
    """Page pictures come from pypdfium2 (in process); pdftoppm is the fallback when it is missing."""
    import io, shutil, sys
    from ecadlib import pdf_render as R
    from tests.test_visual_check import make_pdf as _pdf
    pdf = _pdf(["PAGE ONE", "PAGE TWO"])
    png = R.render_png(pdf, 2, dpi=72)
    assert png.startswith(b"\x89PNG") and R.renderer() == "pypdfium2"
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    assert im.mode == "RGB" and im.size[0] > 100 and min(im.convert("L").get_flattened_data() if hasattr(im, "get_flattened_data") else im.convert("L").getdata()) < 128   # text drawn in black
    try:
        R.render_png(pdf, 3)
        raise AssertionError("page 3 should not exist")
    except R.RenderError:
        pass
    saved = sys.modules.get("pypdfium2")
    sys.modules["pypdfium2"] = None                     # import now raises ImportError
    try:
        if shutil.which("pdftoppm"):
            assert R.renderer() == "pdftoppm" and R.render_png(pdf, 1, dpi=72).startswith(b"\x89PNG")
    finally:
        sys.modules["pypdfium2"] = saved
    from ecadlib.llm import rails
    blocks = rails.page_images(pdf, [1, 2])
    assert [b["type"] for b in blocks] == ["text", "image", "text", "image"]


# ---------------------------------------------------------------- dimension tables read in code
def _placed_pdf(words, lines=()):
    """One-page PDF: words [(x, y, text)] in Helvetica 8 pt, and ruled lines [(x0, y0, x1, y1)]."""
    ops = "".join(f"BT /F1 8 Tf {x} {y} Td ({t}) Tj ET\n" for x, y, t in words)
    ops += "".join(f"{a} {b} m {c} {d} l S\n" for a, b, c, d in lines)
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [5 0 R] /Count 1 >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(ops)} >>\nstream\n{ops}endstream",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()


ST_ROWS = [["Symbol", "millimeters", "", "", "inches", "", ""], ["", "Min", "Typ", "Max", "Min", "Typ", "Max"],
           ["A", "-", "-", "1.60", "-", "-", "0.0630"], ["b(9)(11)", "0.30", "0.37", "0.45", "0.0118", "0.0146", "0.0177"],
           ["D(4)", "9.00 BSC", "", "", "0.3543 BSC", "", ""], ["e", "0.80 BSC", "", "", "0.0315 BSC", "", ""],
           ["L", "0.45", "0.60", "0.75", "0.0177", "0.0236", "0.0295"]]


def _st_pdf():
    """ST's ruled table: a grid, "9.00 BSC" in a cell merged across min/typ/max, inches beside mm."""
    xs, top, h = [60, 120, 170, 220, 270, 330, 380, 430], 700, 16
    words, lines = [], []
    for r, row in enumerate(ST_ROWS):
        y = top - r * h
        for c, t in enumerate(row):
            if t:
                words.append((xs[c] + 3, y - 11, t))
    for r in range(len(ST_ROWS) + 1):
        lines.append((xs[0], top - r * h, xs[-1], top - r * h))
    for c, x in enumerate(xs):
        lines.append((x, top, x, top - len(ST_ROWS) * h))
    return _placed_pdf(words, lines)


def _nxp_pdf():
    """NXP / Nexperia: no ruling, symbols across, max / nom / min down (the SOT23 table of PESD2CANFD24V)."""
    syms = ["A", "A1", "bp", "c", "D", "E", "e", "e1", "HE", "Lp", "Q"]
    xs = [110 + 20 * k for k in range(len(syms))]
    rows = {"max": ["1.1", "0.1", "0.48", "0.15", "3.0", "1.4", "", "", "2.5", "0.45", "0.55"],
            "nom": ["", "", "", "", "", "", "1.9", "0.95", "", "", ""],
            "min": ["0.9", "", "0.38", "0.09", "2.8", "1.2", "", "", "2.1", "0.15", "0.45"]}
    words = [(60, 700, "Dimensions (mm are the original dimensions)"), (75, 680, "Unit")] + [(x, 680, s) for x, s in zip(xs, syms)]
    for k, (lab, vals) in enumerate(rows.items()):
        y = 668 - 9 * k
        words.append((88, y, lab))
        if lab == "nom":
            words.append((66, y, "mm"))
        words += [(x, y, v) for x, v in zip(xs, vals) if v]
    return _placed_pdf(words)


def test_dim_tables_st_ruled_and_nxp_unruled():
    from ecadlib import ds_route as DR
    st = DR.dim_tables(_st_pdf(), [1])
    assert len(st) == 1 and st[0]["unit"] == "mm" and st[0]["page"] == 1
    rows = st[0]["rows"]
    assert rows["b"] == {"min": 0.30, "nom": 0.37, "max": 0.45, "span": False}       # footnote marks dropped
    assert rows["A"]["max"] == 1.6 and rows["A"]["min"] is None and not rows["A"]["span"]
    assert rows["D"]["min"] == 9.0 and rows["D"]["span"] and rows["e"]["min"] == 0.8   # "9.00 BSC" across the columns
    nx = DR.dim_tables(_nxp_pdf(), [1])
    r = nx[0]["rows"]
    assert (r["D"]["min"], r["D"]["max"]) == (2.8, 3.0) and r["e"]["nom"] == 1.9 and r["e1"]["nom"] == 0.95
    assert (r["HE"]["min"], r["HE"]["max"]) == (2.1, 2.5) and r["A1"] == {"min": None, "nom": None, "max": 0.1, "span": False}
    from tests.test_visual_check import make_pdf
    assert DR.dim_tables(make_pdf(["Symbol Parameter Conditions Min Typ Max Unit"]), [1]) == []   # electrical: not this


def test_table_check_flags_fills_and_spans():
    from ecadlib import datasheet as D
    tables = [{"page": 9, "unit": "mm", "rows": {
        "D": {"min": 2.8, "nom": None, "max": 3.0, "span": False}, "E": {"min": 1.2, "nom": None, "max": 1.4, "span": False},
        "e": {"min": 0.95, "nom": None, "max": None, "span": True}, "b": {"min": 0.38, "nom": None, "max": 0.48, "span": False}}}]
    dim = lambda lab, mn, nm, mx, unit="mm", page=3: {"label": lab, "min": mn, "nom": nm, "max": mx, "unit": unit,
                                                      "page": page, "figure": ""}
    pkg = {"body_length": dim("D", 2.8, None, 3.1), "body_width": dim("E", None, None, 1.4),
           "pitch": dim("e", None, 0.95, None), "lead_width": dim("b(2)", 0.38, None, 0.48, page=4)}
    review = []
    D.table_check(pkg, tables, [2, 5, 9, 10], review)        # sent page 3 = page 9 of the PDF; page 4 = 10 (next: 9)
    assert any("body_length (D) differs" in r and "max 3.1, table 3" in r for r in review)
    assert pkg["body_length"]["max"] == 3.1                  # a differing value is flagged, never changed
    assert pkg["body_width"]["min"] == 1.2 and any("body_width (E) min 1.2 mm filled" in r for r in review)
    assert not any("pitch" in r or "lead_width" in r for r in review)   # a span that agrees, a footnoted label on p+1
    pkg = {"pitch": dim("e", None, 1.0, None)}
    review = []
    D.table_check(pkg, tables, [2, 5, 9], review)
    assert review and "pitch (e) differs" in review[0] and "nom 1 mm, the table says 0.95 mm" in review[0]
    review = []                                      # AL8860MP: the read is the MSOP-8's, the table on its page the SOT23's
    D.table_check({"body_length": dim("D", 2.9, None, 3.1), "body_width": dim("E", 2.9, None, 3.1),
                   "pitch": dim("e", None, 0.65, None), "lead_width": dim("b", 0.38, None, 0.48)},
                  tables, [2, 5, 9], review)
    assert review == [], review                      # 3 of the 4 matched rows disagree: another package's table
    review = []
    D.table_check({"body_length": dim("D", 0.1102, None, 0.1181, unit="in")}, tables, [2, 5, 9], review)
    assert review == []                                      # inches, within the inch column's rounding
    from ecadlib import ds_route as DR
    assert not any(DR.INCOMPLETE.search(r) or DR.FAILED.search(r) for r in
                   ["body_length (D) differs from the dimension table on p9 (read in code): max 3.1, table 3 mm: check the read"])


def test_zoom_blocks_close_ups_of_drawing_pages_only():
    import base64, io
    from PIL import Image
    from ecadlib import datasheet as D
    from tests.test_visual_check import make_pdf
    pdf = make_pdf(["PIN TABLE", "PACKAGE OUTLINE", "LAND PATTERN"])
    why = {12: ["pinout"], 40: ["drawing"], 41: ["land", "drawing (continued)"]}
    z = D.zoom_blocks(pdf, [12, 40, 41], why)
    texts = [b["text"] for b in z if b["type"] == "text"]
    assert texts[0].startswith("Close-up of attached PDF page 2 (original page 40), top and bottom half")
    assert "Cite page 3" in texts[1] and len(z) == 6
    for b in z[1:3]:
        im = Image.open(io.BytesIO(base64.b64decode(b["source"]["data"])))
        assert im.width == D.ZOOM_PX and -(-im.width // 28) * -(-im.height // 28) <= 4784   # sent as rendered
    assert D.zoom_blocks(pdf, [12, 40, 41], {12: ["pinout"]}) == []
    assert D.SETTINGS["zoom_drawings"] is False                                       # off until an A/B says so


def test_the_reader_sends_close_ups_only_when_zoom_drawings_is_on():
    from tests.test_ds_route import reader
    import tempfile
    data = bookmarked(mcu_pages(), MCU_MARKS)
    with tempfile.TemporaryDirectory() as d:
        rd = reader(d, {"https://a/mcu.pdf": data})
        off = rd.job("TESTMCU100", [("https://a/mcu.pdf", "BOM")], "LQFP-100")
        assert not any(b["type"] == "image" for b in off["body"]["messages"][0]["content"])
        rd.cfg = dict(rd.cfg, zoom_drawings=True)
        on = rd.job("TESTMCU100", [("https://a/mcu.pdf", "BOM")], "LQFP-100")
    content = on["body"]["messages"][0]["content"]
    assert content[0]["type"] == "document" and content[-1]["type"] == "text" and content[-1]["text"].startswith("Part:")
    assert any(b["type"] == "image" for b in content) and on["prov"]["zoom_pages"]
    assert on["key"] != off["key"]                    # a different request: never answered from the plain one's cache
