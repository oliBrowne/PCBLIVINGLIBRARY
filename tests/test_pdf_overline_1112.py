"""Active-low pin names read from the PDF's own text layer (an overbar font, a line drawn over the letters)."""
import io
from ecadlib import pdf_overline as PO


def test_a_name_printed_with_a_bar_more_often_than_without_is_marked():
    counts = {"SHDN": [18, 0], "FAULT": [16, 2], "EN": [1, 9], "TIMER": [0, 15], "HO": [2, 2], "PG": [1, 0]}
    pins = [{"num": "1", "name": "SHDN"}, {"num": "2", "name": "FAULT"}, {"num": "3", "name": "EN"},
            {"num": "4", "name": "TIMER"}, {"num": "5", "name": "~{RST}"}, {"num": "6", "name": "HO"},
            {"num": "7", "name": "PG"}]
    out, done = PO.mark(pins, counts)
    assert [p["name"] for p in out] == ["~{SHDN}", "~{FAULT}", "EN", "TIMER", "~{RST}", "HO", "PG"]
    assert done == ["SHDN", "FAULT"]


def _pdf(barred):
    """A one-page PDF with 'SHDN' (a line drawn over it when barred) and 'VIN', in a standard font."""
    content = b"BT /F1 12 Tf 100 700 Td (SHDN) Tj ET BT /F1 12 Tf 100 650 Td (VIN) Tj ET"
    if barred:                                        # two bars in one path object, as many PDFs draw them
        content += b" 0.5 w 100 710 m 135 710 l 300 400 m 340 400 l S"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    x = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs))
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x))
    return out.getvalue()


def test_a_line_drawn_over_the_letters_is_a_bar_and_plain_text_is_not():
    try:
        import pypdfium2  # noqa: F401
    except ImportError:                               # the reader's PDF library: without it nothing is read
        return
    assert PO.overlined(_pdf(True)).get("SHDN") == [1, 0]
    assert PO.overlined(_pdf(False)).get("SHDN") == [0, 1]
    assert PO.overlined(_pdf(True)).get("VIN") == [0, 1]


def test_a_bar_drawn_in_pieces_is_one_bar():
    """TI draws AD-EN's bar as three strokes (over AD, the hyphen and EN; bq51051b page 4): no single stroke covers
    the name, so strokes that continue each other on one line are joined. Strokes on other lines stay apart."""
    pieces = [(199.2, 262.0, 209.2, 262.0), (209.2, 262.0, 211.6, 262.0), (211.6, 262.0, 221.6, 262.0),
              (240.0, 262.0, 250.0, 262.0), (199.2, 300.0, 209.2, 300.0)]
    out = PO._joined(pieces)
    assert (199.2, 262.0, 221.6, 262.0) in out
    assert (240.0, 262.0, 250.0, 262.0) in out and (199.2, 300.0, 209.2, 300.0) in out and len(out) == 3
