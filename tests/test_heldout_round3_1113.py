"""Held-out test round 3 (2026-11-13): tied image-only drawings, a pin table longer than the page budget, and the
housing outline asked for in a connector's task text."""
from ecadlib import datasheet as D, ds_route as DR

FILL = "Electrical characteristics. Supply current 10 uA typical over the full temperature range of the device."


def _pdf(pages, n):
    out = [FILL] * n
    for i, t in pages.items():
        out[i - 1] = t
    return out


def test_tied_image_only_drawings_are_all_sent():
    # AM26LS31CD: N, D, W and J drawings at the back with no text layer, text drawings of other packages between
    pages = {3: "AM26LS31 Pin Configuration (TOP VIEW) D, N or NS package", 17: "PACKAGE OPTION ADDENDUM AM26LS31CD "
             "ACTIVE SOIC D 16", 24: "", 25: "PACKAGE OUTLINE TSSOP-16 Min Max mm A1 D1 E1", 28: "",
             29: "PACKAGE OUTLINE SSOP-16 Min Max mm A1 D1 E1", 32: "", 34: "",   # text drawings: other packages
             4: "Pin Functions PIN NO. NAME TYPE", 5: "Device comparison ordering information",
             6: "Pin descriptions pin name", 18: "PACKAGE OPTION ADDENDUM orderable"}   # pages that fill the budget
    texts = _pdf(pages, 35)
    rows = DR.page_scores(texts, "AM26LS31CD", "SOIC-16_3.9x9.9mm_P1.27mm")[0]
    tied = [i + 1 for i in (23, 27, 31, 33) if rows[i]["drawing"]]
    got, why = DR.pick_pages(texts, 6, "AM26LS31CD", "SOIC-16_3.9x9.9mm_P1.27mm")
    assert set(tied) <= set(got), (tied, got, why)
    assert 28 in got


def test_one_image_drawing_is_not_padded():
    texts = _pdf({3: "Pin Configuration (TOP VIEW)", 20: "Package outline Min Max mm A1 D1 E1 SOIC-8", 28: ""}, 30)
    got, why = DR.pick_pages(texts, 6, "XYZ123", "SOIC-8_3.9x4.9mm_P1.27mm")
    assert not any("tied" in " ".join(why.get(p, [])) for p in got)


SIG = ("7.2 Signal Descriptions Table 7-1 describes the signals. Table 7-1. Signal Descriptions TERMINAL I/O(1) "
       "DESCRIPTION NAME NO.(4) PZ ZCA, ZQW P6.4/CB4/A4 1 A1 I/O General-purpose digital I/O")
SIG_C = "Table 7-1. Signal Descriptions (continued) TERMINAL I/O(1) DESCRIPTION NAME NO.(4) PZ ZCA, ZQW P1.0 21 J2 I/O"
DWG = "PACKAGE OUTLINE ZCA0113A NFBGA - 1 mm max height BALL GRID ARRAY Min Max mm A1 D1 E1 BGA-113"


def test_a_continued_pin_table_goes_in_whole_past_the_budget():
    pages = {11: SIG, **{p: SIG_C for p in range(12, 18)}, 115: DWG, 116: DWG}
    texts = _pdf(pages, 121)
    got, why = DR.pick_pages(texts, 6, "MSP430F5333IZQW", "BGA-113")
    assert set(range(11, 18)) <= set(got), (got, why)
    assert 115 in got
    assert len(got) <= 6 + DR.PIN_TABLE_EXTRA


def test_task_text_asks_a_connector_for_its_housing_outline():
    # every part's task: the hint is often only the MPN (B20B-PUDSS's case has no package), so a hint test never fired
    assert "pad_table.body" in D.task_text("B20B-PUDSS", "B20B-PUDSS")
    assert "pad_table.body" in D.task_text("LM358")


def test_multi_function_pin_names_agree_in_any_order():
    from ecadlib.eval import score as S
    assert S.norm_name("TA0.2/P1.3") == S.norm_name("P1.3/TA0.2")
    assert S.norm_name("P1.3/TA0.2") != S.norm_name("P1.3/TA0.1")
    assert S.norm_name("/RESET") == S.norm_name("RESET")                # a leading bar mark is still not a function


# ---------------------------------------------------------------- a bar over one function of a multi-function name
def _seg_pdf(bar):
    import io
    content = b"BT /F1 12 Tf 100 700 Td (RST/NMI/SBWTDIO) Tj ET"
    if bar:
        content += b" 0.5 w %d 710 m %d 710 l S" % bar
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


def test_a_bar_over_one_function_of_a_name_marks_that_function():
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return
    from ecadlib import pdf_overline as PO
    c = PO.overlined(_seg_pdf((100, 124)))                             # the bar spans RST only
    assert c["RST/NMI/SBWTDIO"] == [0, 1] and c["RST/NMI/SBWTDIO|0"] == [1, 0] and c["RST/NMI/SBWTDIO|1"] == [0, 1]
    pins, done = PO.mark([{"num": "A3", "name": "RST/NMI/SBWTDIO"}], c)
    assert pins[0]["name"] == "~{RST}/NMI/SBWTDIO" and done == ["RST/NMI/SBWTDIO"]
    plain = PO.overlined(_seg_pdf(None))
    assert PO.mark([{"num": "A3", "name": "RST/NMI/SBWTDIO"}], plain)[1] == []


def test_one_function_printed_plain_elsewhere_is_not_marked():
    from ecadlib import pdf_overline as PO
    c = {"RST/NMI/SBWTDIO|0": [1, 1], "RST/NMI/SBWTDIO|1": [0, 2], "RST/NMI/SBWTDIO|2": [0, 2]}
    assert PO.mark([{"num": "A3", "name": "RST/NMI/SBWTDIO"}], c)[1] == []
    assert PO.mark([{"num": "1", "name": "EN/SD"}], {"EN/SD|0": [1, 0], "EN/SD|1": [1, 0]})[1] == []   # all barred:
    # a bar over every function is the whole name's, which needs the usual two printings


def test_a_replayed_read_finds_its_pdf_by_hash(tmp_path):
    import hashlib
    from ecadlib.eval import runner as R
    data = _seg_pdf(None)
    (tmp_path / "X.pdf").write_bytes(data)
    r = R.EvalReader.__new__(R.EvalReader)
    r.cfg = {"folder": str(tmp_path)}
    r.store = type("S", (), {"dir": tmp_path / "none"})()
    prov = {"sha256": hashlib.sha256(data).hexdigest(), "local": "X.pdf", "url": "https://a/x.pdf"}
    assert r._replay_pdf(prov) == data
    assert r._replay_pdf(dict(prov, sha256="0" * 64)) is None            # another file under that name: not used
