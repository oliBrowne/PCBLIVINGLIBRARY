"""Datasheet getting, 2026-10-22 (targeted testing of the download pipeline against a local site that behaves as maker
sites do): a PDF cut off mid-download (or a text answer that only starts "%PDF-") is never kept as the datasheet; a
product page's PDF link is read as a browser reads it (&amp; in its query, a #page= fragment, spaces inside the quotes,
a quoted meta refresh); a page with several PDFs gives the one named for the part; a page whose HTML starts after a
long prolog is still read as a page.
    python3 -m tests.test_datasheet_fetch_1022"""
import re, tempfile

from ecadlib import datasheet as D, pdf_fetch as PF
from tests import test_datasheet_batch as TB
from tests.test_datasheet_downloads import Scripted
from tests.fixtures_lite import lite

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


# ---------------------------------------------------------------- 3. a site that stalls scripts (the PC survey's slow links)
SLOW = "https://www.analog.com/media/en/technical-documentation/data-sheets/ad5160.pdf"


def test_a_stalled_or_reset_request_moves_straight_on_when_another_way_follows():
    """analog.com / molex.com answer ecadlib's own request with a reset or a 60 s stall; three tries of it cost 65-186 s
    per link before curl_cffi got the PDF in under 2 s. A refusal is tried once when another way follows."""
    from tests.test_pdf_fetch import store
    for err in ("ConnectionError: ('Connection aborted.', ConnectionResetError(10054, 'forcibly closed'))",
                "ReadTimeout: HTTPSConnectionPool(host='www.analog.com', port=443): Read timed out. (read timeout=60)"):
        with tempfile.TemporaryDirectory() as d:
            st, http, calls = store(d, {}, impersonate=(200, GOOD, "application/pdf", SLOW, None))
            http.pdfs[SLOW] = [err]
            st.http = Scripted(http.pdfs)
            waits = []
            st.sleep = waits.append
            data, meta = st.fetch(SLOW, "BOM", part="AD5160")
            assert data == GOOD and meta["via"] == "impersonate" and st.http.fetched == [SLOW] and waits == [], \
                (st.http.fetched, waits)
            assert "www.analog.com" not in st.gave_up                     # (one try is no proof the host is down)


def test_a_stalled_request_with_no_other_way_is_still_tried_again():
    with tempfile.TemporaryDirectory() as d:
        waits = []
        st = D.PdfStore(d, Scripted({SLOW: ["ReadTimeout: read timed out", GOOD]}), sleep=waits.append)
        assert st.fetch(SLOW, "BOM")[0] == GOOD and len(waits) == 1


@lite
def test_the_browser_headers_way_waits_less_when_a_stronger_way_follows(monkeypatch):
    got = []
    monkeypatch.setattr(PF, "headers_get", lambda url, session=None, timeout=60: got.append(timeout) or
                        (403, b"<html>Access Denied</html>", "text/html", url, None))
    with tempfile.TemporaryDirectory() as d:
        st = D.PdfStore(d, Scripted({SLOW: [403]}), download="headers,impersonate", sleep=lambda s: None)
        st.missing = {}
        st.fns["impersonate"] = lambda url, part: (200, GOOD, "application/pdf", url, None)
        assert st.fetch(SLOW, "BOM")[0] == GOOD and got == [PF.HEADERS_S], got
        st2 = D.PdfStore(d, Scripted({SLOW + "?2": [403]}), download="headers", sleep=lambda s: None)
        try:
            st2.fetch(SLOW + "?2", "BOM")
        except D.DatasheetError:
            pass
        assert got[-1] == 60, got                                        # the last way: the full wait


# ---------------------------------------------------------------- 4. a link that now goes to a home page or a search
def test_a_link_redirected_to_a_home_page_search_or_not_found_page_is_gone():
    for url, final in (("https://www.osram-os.com/Graphics/XPic1/00029253_0.pdf/SFH", "https://ams-osram.com/"),
                       ("https://ams.com/ens210", "https://ams-osram.com/"),
                       ("https://www.skyworksinc.com/uploads/documents/AS179_92LF_200176H.pdf",
                        "https://www.skyworksinc.com:443/search?q=as179_92lf_200176h#documents"),
                       ("https://www.onsemi.com/pub/Collateral/NCP1207B.PDF",
                        "https://www.onsemi.com/design/technical-documentation?notFound=NCP1207B.PDF"),
                       ("https://www.maker.com/ds/x.pdf", "https://www.maker.com/errors/page-not-found")):
        assert PF.soft_404(url, final), (url, final)
    for url, final in (("https://www.maker.com/ds/x.pdf", "https://www.maker.com/ds/x.pdf"),
                       ("https://www.maker.com/", "https://www.maker.com/"),
                       ("https://www.maker.com/p/tps2660", "https://www.maker.com/product/TPS2660"),
                       ("https://www.ti.com/lit/gpn/tps2660", "https://www.ti.com/lit/ds/symlink/tps2660.pdf"),
                       ("https://www.maker.com/search?q=tps2660", "https://www.maker.com/search?q=tps2660&page=1")):
        assert not PF.soft_404(url, final), (url, final)


def test_a_gone_link_is_said_once_every_way_failed_and_is_a_broken_link():
    from tests.test_pdf_fetch import store
    url, home = "https://www.osram-os.com/Graphics/XPic1/00029253_0.pdf/SFH", "https://ams-osram.com/"
    page = b"<!doctype html><html><body>ams OSRAM home</body></html>"

    class Moved(TB.PdfHTTP):
        def get_bytes(self, u):
            self.fetched.append(u)
            return 200, page, "text/html", home, None
    with tempfile.TemporaryDirectory() as d:
        st, _, calls = store(d, {}, headers=(200, page, "text/html", home, None),
                             impersonate=(200, page, "text/html", home, None), browser=(200, page, "text/html", home, None))
        st.http = Moved()
        try:
            st.fetch(url, "BOM", part="SFH216")
            raise AssertionError("taken")
        except D.DatasheetError as e:
            msg = str(e)
        assert [c[0] for c in calls] == ["headers", "impersonate", "browser"], calls
        assert "gone" in msg and home in msg and re.search(D.BROKEN_LINK, msg), msg


def test_a_link_sent_to_a_site_search_that_a_browser_resolves_is_kept():
    """Skyworks (survey re-run 2026-10-22, AAT1217ICA-3.3): ecadlib's own request lands on the site search, the
    browser's on the PDF."""
    from tests.test_pdf_fetch import store
    url = "http://www.skyworksinc.com/uploads/documents/AAT1217_202050B.pdf"
    search = "https://www.skyworksinc.com:443/search?q=aat1217_202050b#documents"
    page = b"<!doctype html><html><body>Skyworks | Search Results</body></html>"

    class Search(TB.PdfHTTP):
        def get_bytes(self, u):
            self.fetched.append(u)
            return 200, page, "text/html", search, None
    with tempfile.TemporaryDirectory() as d:
        st, _, calls = store(d, {}, headers=(200, page, "text/html", search, None),
                             impersonate=(200, page, "text/html", search, None),
                             browser=(200, GOOD, "application/pdf", url, None))
        st.http = Search()
        data, meta = st.fetch(url, "BOM", part="AAT1217ICA-3.3")
        assert data == GOOD and meta["via"] == "browser", meta


@lite
def test_ecadlib_own_request_waits_less_when_another_way_follows(monkeypatch):
    got = []

    class Sess:
        def get(self, url, timeout=None, **kw):
            got.append(timeout[1] if isinstance(timeout, tuple) else timeout)    # (connect, read)
            raise D.requests.exceptions.ReadTimeout(f"Read timed out. (read timeout={got[-1]})")

    class Http:
        s, min_interval = Sess(), 0
    with tempfile.TemporaryDirectory() as d:
        st = D.PdfStore(d, Http(), download="impersonate", sleep=lambda s: None)
        st.missing = {}
        st.fns["impersonate"] = lambda url, part: (200, GOOD, "application/pdf", url, None)
        assert st.fetch(SLOW, "BOM")[0] == GOOD and got == [PF.HEADERS_S], got
        got.clear()
        st2 = D.PdfStore(d, Http(), sleep=lambda s: None)                  # plain only: the full minute, tried again
        try:
            st2.fetch(SLOW + "?x", "BOM")
        except D.DatasheetError:
            pass
        assert got == [60, 60, 60], got


# ---------------------------------------------------------------- 5. the part-name check on the right datasheet
def _jst(text):
    """JST's connector PDFs (eXH.pdf ...): a font whose text layer comes out 31 code points low (space as \\x01,
    digits as \\x11-\\x1a), only the heading in clear (survey 2026-10-22: 12 parts rejected)."""
    return "".join(chr(ord(c) - 31) if ord(c) > 32 else "\x01" if c == " " else c for c in text)


def test_an_unreadable_text_layer_can_not_tell_and_never_rejects():
    body = ("This is a 2.5 mm pitch wire-to-board connector that meets the needs of high density PCB requirements and "
            "provides design flexibility in all sorts of electronic devices. S13B-XH-A B2B-XH-A ") * 6
    garbled = ["XH CONNECTOR\n" + _jst(body)] * 3
    assert D.mentions_part(garbled, "S13B-XH-A") is None
    assert D.mentions_part(["XH CONNECTOR\n" + body] * 3, "S13B-XH-A") is True          # (read in clear: named)
    assert D.mentions_part(["LTC4054 Standalone Linear Li-Ion Battery Charger" + FILLER], "LTC4060EDHC") is False


def test_a_big_family_datasheet_is_looked_at_up_to_its_configuration_summary():
    """SAM D21 / SAM E70 (1115 / 1976 pages): the first pages say only "SAM E70/S70/V70/V71 Family"; the order codes
    are in the Configuration Summary on p14."""
    pages = ["SAM E70/S70/V70/V71 Family Arm Cortex-M7 MCUs" + FILLER] * 13 + \
            ["1. Configuration Summary ATSAME70Q21 ATSAME70Q20 ATSAME70Q19 ATSAME70N21 ATSAME70N20 ATSAME70N19" + FILLER] + \
            ["Peripheral chapter" + FILLER] * 60
    big = TB.pdf_with(pages)
    url = "https://ww1.microchip.com/downloads/en/DeviceDoc/SAM-E70-S70-V70-V71-Family-Data-Sheet-DS60001527D.pdf"
    with tempfile.TemporaryDirectory() as d:
        data, meta = D.PdfStore(d, Scripted({url: [big]}), sleep=lambda s: None).fetch(url, "BOM", mpn="ATSAME70N19A-AN")
        assert data == big
    # a short datasheet of another part naming this one only in its last page's related-parts table: still rejected
    small = TB.pdf_with(["LTC4054 Standalone Linear Li-Ion Battery Charger" + FILLER] * 15 +
                        ["Related Parts: LTC4060 Standalone Linear NiMH/NiCd Fast Battery Charger" + FILLER])
    url2 = "https://www.analog.com/media/en/technical-documentation/data-sheets/405442xf.pdf"
    with tempfile.TemporaryDirectory() as d:
        try:
            D.PdfStore(d, Scripted({url2: [small]}), sleep=lambda s: None).fetch(url2, "BOM", mpn="LTC4060EDHC")
            raise AssertionError("taken")
        except D.DatasheetError as e:
            assert "doesn't mention LTC4060EDHC" in str(e), e


# ---------------------------------------------------------------- 6. review of #165 (Greptile)
def test_a_refusal_or_bot_check_at_the_home_page_is_no_gone_link():
    from tests.test_pdf_fetch import store
    url, home = "https://www.maker.com/ds/tps2660.pdf", "https://www.maker.com/"
    for status, body in ((403, b"<!doctype html><html><body>Access Denied. Reference #18.2f</body></html>"),
                         (200, b"<!doctype html><html><body>Just a moment... enable JavaScript</body></html>")):
        class AtHome(TB.PdfHTTP):
            def get_bytes(self, u):
                self.fetched.append(u)
                return status, body, "text/html", home, None
        with tempfile.TemporaryDirectory() as d:
            st, _, calls = store(d, {}, impersonate=(200, GOOD, "application/pdf", url, None))
            st.http = AtHome()
            data, meta = st.fetch(url, "BOM", part="TPS2660")
            assert data == GOOD and meta["via"] == "impersonate", (status, meta)
    assert not PF.gone_page(url, 403, b"<html>Access Denied</html>", home)
    assert PF.gone_page(url, 200, b"<!doctype html><html><body>Welcome to Maker</body></html>", home)


def test_a_cut_off_pdf_moves_on_to_the_next_way():
    from tests.test_pdf_fetch import store
    url = "https://www.ti.com/lit/ds/symlink/tps2660.pdf"
    assert PF.refused(0, None, D.cut_off(GOOD[:700]))
    with tempfile.TemporaryDirectory() as d:
        st, http, calls = store(d, {}, headers=(200, GOOD[:700], "application/pdf", url, None),
                                impersonate=(200, GOOD, "application/pdf", url, None))
        st.http = Scripted({url: [GOOD[:700]]})
        data, meta = st.fetch(url, "BOM", part="TPS2660")
        assert data == GOOD and meta["via"] == "impersonate" and [c[0] for c in calls] == ["headers", "impersonate"]


def test_a_link_named_for_another_part_of_the_range_is_not_the_parts():
    page = b'<a href="/files/STM32L476.pdf">L4</a> <a href="/files/datasheet.pdf">Datasheet</a>'
    assert D.pdf_link(page, BASE, part="STM32F103C8T6") == "https://www.maker.com/files/datasheet.pdf"
    assert D.pdf_link(page + b'<a href="/files/stm32f103c8.pdf">F1</a>', BASE, part="STM32F103C8T6") == \
        "https://www.maker.com/files/stm32f103c8.pdf"


def test_the_headers_way_gets_the_full_wait_when_it_is_the_last_way_left():
    from tests.test_pdf_fetch import URL
    got = []
    with tempfile.TemporaryDirectory() as d:
        st = D.PdfStore(d, Scripted({URL: [403]}), download="headers,impersonate", sleep=lambda s: None)
        st.missing = {}
        st.prefer["www.analog.com"] = "impersonate"                   # impersonate worked for this site before
        st.fns["impersonate"] = lambda url, part: (403, b"<html>Access Denied</html>", "text/html", url, None)
        st.fns["headers"] = lambda url, part: got.append(st._headers_s()) or (200, GOOD, "application/pdf", url, None)
        assert st.fetch(URL, "BOM")[0] == GOOD and got == [60], got


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_datasheet_fetch_1022"]))
