"""Datasheet getting, 2026-10-22 (targeted testing of the download pipeline against a local site that behaves as maker
sites do): a PDF cut off mid-download (or a text answer that only starts "%PDF-") is never kept as the datasheet; a
product page's PDF link is read as a browser reads it (&amp; in its query, a #page= fragment, spaces inside the quotes,
a quoted meta refresh); a page with several PDFs gives the one named for the part; a page whose HTML starts after a
long prolog is still read as a page.
    python3 -m tests.test_datasheet_fetch_1022"""
import tempfile

from ecadlib import datasheet as D, pdf_fetch as PF
from tests import test_datasheet_batch as TB
from tests.test_datasheet_downloads import Scripted

FILLER = " Features: low quiescent current, wide input range, thermal shutdown, small package, RoHS compliant."
GOOD = TB.pdf_with(["TPS2660 60-V, 2-A industrial eFuse" + FILLER, "Pin Functions", "Package outline"])
OTHER = TB.pdf_with(["Application note: layout guidelines for power stages" + FILLER])


# ---------------------------------------------------------------- 1. a PDF cut off mid-download
def test_a_complete_pdf_ends_with_its_end_of_file_marker():
    assert D.pdf_complete(GOOD)
    assert D.pdf_complete(GOOD + b"\r\n\x00\x00  \n")                  # padding after %%EOF (old generators)
    assert not D.pdf_complete(GOOD[: len(GOOD) // 2])                  # the connection closed half way
    assert not D.pdf_complete(b"%PDF-1.4 not really a pdf, just text")


def test_a_cut_off_download_is_never_kept_and_is_downloaded_again_next_time():
    url = "https://www.ti.com/lit/ds/symlink/tps2660.pdf"
    cut = GOOD[: len(GOOD) // 2]
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({url: [cut]})                                  # cut off on every try
        st = D.PdfStore(d, http, sleep=lambda s: None)
        try:
            st.fetch(url, "BOM", mpn="TPS2660PWPR")
            raise AssertionError("a cut-off PDF was kept")
        except D.DatasheetError as e:
            assert "cut off" in str(e) and url in str(e), e
        assert http.fetched == [url] * 3, http.fetched
        http.pdfs[url] = [GOOD]
        data, meta = st.fetch(url, "BOM", mpn="TPS2660PWPR")            # not cached: the next fetch downloads it again
        assert data == GOOD and meta["from_cache"] is False and len(http.fetched) == 4, http.fetched


def test_a_cut_off_pdf_is_a_transient_failure_tried_again_in_the_same_fetch():
    url = "https://www.ti.com/lit/ds/symlink/tps2660.pdf"
    with tempfile.TemporaryDirectory() as d:
        waits = []
        st = D.PdfStore(d, Scripted({url: [GOOD[:700], GOOD]}), sleep=waits.append)
        data, meta = st.fetch(url, "BOM", mpn="TPS2660PWPR")
        assert data == GOOD and len(waits) == 1, waits


# ---------------------------------------------------------------- 2. a product page's PDF link, as a browser reads it
BASE = "https://www.maker.com/product/tps2660"


def test_an_html_entity_in_a_pdf_link_is_decoded():
    page = b'<html><body><a href="/files/tps2660.pdf?v=3&amp;lang=en">Datasheet</a></body></html>'
    assert D.pdf_link(page, BASE) == "https://www.maker.com/files/tps2660.pdf?v=3&lang=en"


def test_a_pdf_link_with_a_page_fragment_or_spaces_inside_its_quotes_is_found():
    assert D.pdf_link(b'<a href="/ds/tps2660.pdf#page=3">ds</a>', BASE) == "https://www.maker.com/ds/tps2660.pdf"
    assert D.pdf_link(b'<iframe src="x"></iframe><a href=" /ds/tps2660.pdf ">ds</a>', BASE) == \
        "https://www.maker.com/ds/tps2660.pdf"
    assert D.pdf_link(b'<a href="/ds/tps2660.pdf?x=1#view=FitH">ds</a>', BASE) == "https://www.maker.com/ds/tps2660.pdf?x=1"


def test_a_meta_refresh_is_read_however_its_url_is_written():
    for meta in (b"""<meta http-equiv="refresh" content="0; URL='/ds/tps2660.pdf'">""",
                 b"""<meta http-equiv="Refresh" content='0;url="/ds/tps2660.pdf"'>""",
                 b"""<meta http-equiv="refresh" content="0; url = /ds/tps2660.pdf">""",
                 b"""<meta content="0;URL=/ds/tps2660.pdf" http-equiv="refresh">""",
                 b"""<meta http-equiv=refresh content="5;url=/ds/tps2660.pdf?a=1&amp;b=2">"""):
        got = D.pdf_link(b"<html><head>" + meta + b"</head></html>", BASE)
        assert got and got.startswith("https://www.maker.com/ds/tps2660.pdf"), (meta, got)
    assert D.pdf_link(b"""<meta http-equiv="refresh" content="5;url=/ds/x.pdf?a=1&amp;b=2">""", BASE) == \
        "https://www.maker.com/ds/x.pdf?a=1&b=2"
    assert D.pdf_link(b"""<meta http-equiv="refresh" content="300">""", BASE) is None    # a reload, no link


def test_a_page_with_several_pdfs_gives_the_one_named_for_the_part():
    page = (b'<html><body><a href="/files/slva123-layout.pdf">App note</a> <a href="/files/brochure.pdf">Brochure</a>'
            b'<a href="/files/tps2660.pdf">TPS2660</a></body></html>')
    assert D.pdf_link(page, BASE, part="TPS2660PWPR") == "https://www.maker.com/files/tps2660.pdf"
    assert D.pdf_link(page, BASE) == "https://www.maker.com/files/slva123-layout.pdf"   # no part: as before
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({BASE: [(200, page, {})], "https://www.maker.com/files/tps2660.pdf": [GOOD],
                         "https://www.maker.com/files/slva123-layout.pdf": [OTHER]})
        data, meta = D.PdfStore(d, http, sleep=lambda s: None).fetch(BASE, "BOM", part="TPS2660PWPR", mpn="TPS2660PWPR")
        assert data == GOOD and meta["page_url"] == BASE, meta


def test_a_page_whose_html_starts_after_a_long_prolog_is_still_a_page():
    page = b"\n<!-- generated -->\n" + b" " * 5000 + b'<!DOCTYPE html><html><body><a href="/files/tps2660.pdf">ds</a></body></html>'
    assert D.looks_html(page) and not D.looks_html(GOOD)
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({BASE: [(200, page, {})], "https://www.maker.com/files/tps2660.pdf": [GOOD]})
        data, meta = D.PdfStore(d, http, sleep=lambda s: None).fetch(BASE, "BOM", mpn="TPS2660PWPR")
        assert data == GOOD and meta["page_url"] == BASE, meta
    assert PF.refused(200, b" " * 5000 + b"<html><body>Please enable JavaScript</body></html>", None, lambda h: None)


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_datasheet_fetch_1022"]))
