"""Datasheet downloads (offline; every site answer is a fake): retries with backoff and a capped Retry-After, every
candidate link tried in order, the makers' URL patterns as later candidates, and the held message when a maker's site
refuses the download. Evidence from the owner's run is cited per case.
    python3 -m tests.test_datasheet_downloads"""
import tempfile

from ecadlib import datasheet as D, httpcache as H, librarian as L, messages as M, release_check as RC
from tests import test_datasheet_batch as TB

PDF = TB.PDF
HTML = b"<!DOCTYPE html><html><body>Access Denied</body></html>"
TIMEOUT = "ReadTimeout: HTTPSConnectionPool(host='www.analog.com', port=443): Read timed out. (read timeout=60)"
RESET = ("ConnectionError: ('Connection aborted.', ConnectionResetError(10054, 'An existing connection was forcibly "
         "closed by the remote host', None, 10054, None))")


class Scripted(TB.PdfHTTP):
    """PdfHTTP whose answers per URL can be a list, one per try: a status, PDF bytes, an error string, or
    (status, body, headers)."""

    def get_bytes(self, url):
        v = self.pdfs.get(url)
        if isinstance(v, list):
            self.fetched.append(url)
            a = v.pop(0) if len(v) > 1 else v[0]
            if isinstance(a, str):
                return 0, None, "", url, a
            if isinstance(a, tuple):
                return a[0], a[1], "text/html", url, None, a[2]
            if isinstance(a, int):
                return a, HTML, "text/html", url, None
            return 200, a, "application/pdf", url, None
        return super().get_bytes(url)


def store(d, pdfs, waits):
    return D.PdfStore(d, Scripted(pdfs), sleep=waits.append)


# ---------------------------------------------------------------- retries
def test_timeouts_resets_429_and_5xx_are_tried_again_with_backoff():
    waits = []
    with tempfile.TemporaryDirectory() as d:
        url = "https://www.analog.com/lt8610.pdf"            # LT8610: ReadTimeout on the owner's PC
        st = store(d, {url: [TIMEOUT, RESET, PDF]}, waits)
        data, prov = st.fetch(url)
        assert data == PDF and st.http.fetched.count(url) == 3 and waits == [2.0, 4.0], waits
        for first in (429, 500, 503):
            waits.clear()
            u = f"https://cdn.example/{first}.pdf"
            st.http.pdfs[u] = [first, PDF]
            assert st.fetch(u)[0] == PDF and waits == [2.0], (first, waits)


def test_retry_after_is_honoured_and_capped():
    waits = []
    with tempfile.TemporaryDirectory() as d:
        st = store(d, {"https://a.example/x.pdf": [(503, b"", {"retry-after": "7"}), (429, b"", {"retry-after": "3600"}), PDF]},
                   waits)
        assert st.fetch("https://a.example/x.pdf")[0] == PDF
        assert waits == [7.0, H.RETRY_AFTER_MAX] and H.RETRY_AFTER_MAX == 30.0, waits
    assert H.retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0          # a date in the past: no wait
    assert H.retry_after("Fri, 01 Jan 2100 00:00:00 GMT") == H.RETRY_AFTER_MAX
    assert H.retry_after("soon") is None and H.retry_after(None) is None


def test_lasting_failures_are_not_retried_and_a_dead_host_gets_one_try():
    waits = []
    tls = "SSLError: HTTPSConnectionPool(host='optoelectronics.liteon.com'): certificate verify failed"   # liteon: not bot protection
    with tempfile.TemporaryDirectory() as d:
        st = store(d, {"https://optoelectronics.liteon.com/x.pdf": [tls], "https://maker.example/gone.pdf": [404],
                       "https://www.st.com/a.pdf": [TIMEOUT], "https://www.st.com/b.pdf": [TIMEOUT]}, waits)
        for url, want in (("https://optoelectronics.liteon.com/x.pdf", "certificate verify failed"),
                          ("https://maker.example/gone.pdf", "HTTP 404"), ("https://www.st.com/a.pdf", "(3 tries)")):
            try:
                st.fetch(url)
                raise AssertionError(url)
            except D.DatasheetError as e:
                assert want in str(e), e
        assert st.http.fetched.count("https://optoelectronics.liteon.com/x.pdf") == 1
        assert st.http.fetched.count("https://maker.example/gone.pdf") == 1
        assert st.http.fetched.count("https://www.st.com/a.pdf") == 3 and len(waits) == 2   # STULPI01A: timed out every try
        try:
            st.fetch("https://www.st.com/b.pdf")                # same host, already failed every try: once only
            raise AssertionError("b")
        except D.DatasheetError as e:
            assert "tries" not in str(e) and st.http.fetched.count("https://www.st.com/b.pdf") == 1, e


def test_the_requests_path_retries_a_reset_and_sends_an_honest_accept():
    """The real download code (session.get, stream, raw.read) with a fake session: a reset connection is a requests
    ConnectionError, a body cut off mid-read is urllib3's; both are tried again. The User-Agent is the session's own."""
    import requests, urllib3

    class Raw:
        def __init__(self, data, cut=False):
            self.data, self.cut = data, cut

        def read(self, n, decode_content=True):
            if self.cut:
                raise urllib3.exceptions.ProtocolError("Connection broken: ConnectionResetError(104, 'Connection reset by peer')")
            return self.data

    class Resp:
        def __init__(self, raw, status=200):
            self.raw, self.status_code, self.url = raw, status, "https://www.analog.com/ltm4671.pdf"
            self.headers = {"content-type": "application/pdf"}

        def close(self):
            pass

    class Sess:
        def __init__(self):
            self.headers, self.calls, self.script = {"User-Agent": H.UA}, [], [
                requests.ConnectionError(RESET), Resp(Raw(b"", cut=True)), Resp(Raw(PDF))]

        def get(self, url, **kw):
            self.calls.append(kw["headers"])
            a = self.script.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

    class HTTP:
        offline, min_interval, log = False, 0, []

        def __init__(self):
            self.s, self._last = Sess(), {}
    waits = []
    with tempfile.TemporaryDirectory() as d:
        h = HTTP()
        data, prov = D.PdfStore(d, h, sleep=waits.append).fetch("https://www.analog.com/ltm4671.pdf")
        assert data == PDF and len(h.s.calls) == 3 and waits == [2.0, 4.0], (h.s.calls, waits)
        assert h.s.calls[0] == {"Accept": "application/pdf,*/*;q=0.8"} and "Mozilla" not in h.s.headers["User-Agent"]


# ---------------------------------------------------------------- candidates
def test_every_link_is_tried_in_order_then_the_maker_pattern():
    item = {"parameters": [{"name": "ComponentLink2URL", "value": "https://www.lcsc.com/product-detail/C1.html"}]}
    ln = {"datasheet": "https://www.analog.com/LT8610.pdf", "mpn": "LT8610EMSE#PBF", "manufacturer": "Analog Devices",
          "lcsc": "C2836293"}
    row = {"src": "mouser", "src_datasheet": "https://www.mouser.com/datasheet/2/609/LT8610.pdf"}
    urls = D.datasheet_urls(item, ln, row, {"datasheet": ("https://kicad.example/lt8610.pdf", "KiCad symbol LT8610")})
    ad = "https://www.analog.com/media/en/technical-documentation/data-sheets/"
    assert urls == [("https://www.analog.com/LT8610.pdf", "BOM"),
                    ("https://www.mouser.com/datasheet/2/609/LT8610.pdf", "mouser datasheet link"),
                    ("https://kicad.example/lt8610.pdf", "KiCad symbol LT8610"),
                    ("https://www.lcsc.com/product-detail/C1.html", "LCSC datasheet link"),
                    ("https://www.lcsc.com/datasheet/C2836293.pdf", "LCSC datasheet URL pattern (C2836293)"),
                    (ad + "lt8610emse.pdf", "Analog Devices datasheet URL pattern (lt8610emse)"),     # after every other link
                    (ad + "lt8610.pdf", "Analog Devices datasheet URL pattern (lt8610)")], urls
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({urls[0][0]: [TIMEOUT], urls[1][0]: [RESET], urls[2][0]: [403], urls[3][0]: [404],
                         urls[4][0]: [404], urls[5][0]: [404], urls[6][0]: [PDF]})
        rd = D.Reader(d, d, key=TB.KEY, http=http, log=None, sleep=lambda s: None)
        j = rd.job("LT8610", urls, "MSOP-16")
        assert j["where"] == "Analog Devices datasheet URL pattern (lt8610)", j["where"]
        assert j["body"]["messages"][0]["content"][0]["source"]["type"] == "base64"
        assert [a["url"] for a in j["prov"]["attempts"]] == [u for u, _ in urls[:6]], j["prov"]["attempts"]
        assert list(dict.fromkeys(http.fetched)) == [u for u, _ in urls], http.fetched
        # a link that gives the PDF ends the search: the patterns are not asked
        http = Scripted({urls[0][0]: [TIMEOUT], urls[1][0]: [PDF]})
        j = D.Reader(d, d, key=TB.KEY, http=http, log=None, sleep=lambda s: None).job("LT8610", urls, "MSOP-16")
        assert j["where"] == "mouser datasheet link" and not [u for u in http.fetched if "data-sheets/" in u], http.fetched


def test_maker_pattern_table():
    ad = "https://www.analog.com/media/en/technical-documentation/data-sheets/"
    ti = "https://www.ti.com/lit/ds/symlink/"
    want = {("LT8610", "Analog Devices"): [ad + "lt8610.pdf"],
            ("LTM4671", "Analog Devices Inc."): [ad + "ltm4671.pdf"],
            ("LTM4671EY#PBF", "Linear Technology"): [ad + "ltm4671ey.pdf", ad + "ltm4671.pdf"],
            ("AD7606BSTZ-RL", "ADI"): [ad + "ad7606bstz-rl.pdf", ad + "ad7606.pdf"],
            ("LT1763CS8-3.3", "Linear Technology"): [ad + "lt1763cs8-3.3.pdf", ad + "lt1763.pdf"],
            ("TPS2663", "Texas Instruments"): [ti + "tps2663.pdf"],
            ("TPS26630PWPR", "Texas Instruments"): [ti + "tps26630pwpr.pdf", ti + "tps26630.pdf"],
            ("TPS26600PWPR", "TI"): [ti + "tps26600pwpr.pdf", ti + "tps26600.pdf"],
            ("LM1117MPX-3.3/NOPB", "Texas Instruments"): [ti + "lm1117mpx-3.3.pdf", ti + "lm1117.pdf"],
            ("BAT160A", "Nexperia"): [], ("TPS2663", ""): [], ("NOPARTNUMBER", "Texas Instruments"): []}
    for (mpn, maker), urls in want.items():
        assert [u for u, _ in D.maker_urls(mpn, maker)] == urls, (mpn, D.maker_urls(mpn, maker))
    assert [u for u, _ in D.maker_urls("NE555DR", "", ["https://www.ti.com/lit/ds/symlink/ne555.pdf"])] == \
        [ti + "ne555dr.pdf", ti + "ne555.pdf"]                                 # the maker known from a link's host
    # never instead of a BOM or provider link: after them, and only once each
    urls = D.datasheet_urls({"parameters": []}, {"datasheet": ti + "tps2663.pdf", "mpn": "TPS2663", "manufacturer": "TI"},
                            {"lcsc": "C2836293"}, {})
    assert [u for u, _ in urls] == [ti + "tps2663.pdf", "https://www.lcsc.com/datasheet/C2836293.pdf"], urls


# ---------------------------------------------------------------- refusal message
def test_a_refused_download_says_why_and_what_to_do():
    nexperia = "https://assets.nexperia.com/documents/data-sheet/BAT160_SERIES.pdf"   # 403 text/html on the owner's PC
    adi = "https://www.analog.com/media/en/technical-documentation/data-sheets/ltm4671.pdf"   # ConnectionResetError 10054
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({nexperia: [403], adi: [RESET]})
        rd = D.Reader(d, d, key=TB.KEY, http=http, log=None, sleep=lambda s: None)
        j = rd.job("BAT160A", [(nexperia, "BOM")])
        assert j["prov"]["sent_as"] == "url" and "HTTP 403 (a web page" in j["prov"]["attempts"][0]["error"]
        fetch_err = "batch request errored: invalid_request_error: Unable to download the file. Please verify the URL"
        line = D.refused_line(j, fetch_err)
        assert line.startswith("datasheet download refused by assets.nexperia.com (answered HTTP 403 with a web page)"), line
        assert "`ecadlib datasheet FILE.pdf --part BAT160A`" in line and nexperia in line
        assert D.refused_line(j, "max_cost_usd reached") is None            # not a download failure: no such advice
        j = rd.job("LTM4671", [(adi, "nexar datasheet link")])
        assert j["prov"]["attempts"][0]["error"].endswith("(3 tries)")
        line = D.refused_line(j, "The request timed out while trying to download the file.")
        assert "(reset the connection)" in line, line
        st = {"review": []}
        L.Run.refused(st, j, D.APIError("API request failed: The request timed out while trying to download the file."))
        assert st["review"] == [line], st                                   # the librarian keeps it for the release check
        item = {"name": "LTM4671", "symbol": {}, "footprint": {}, "parameters": [],
                "librarian": {"tried": [], "review": [line]}}
        fails, _, _ = RC.check_item(item, d)
        reasons = [f"{r}: {m}" for r, m in fails if r == "files"]
        said = M.explain_all(reasons)
        assert [e["code"] for e in said] == ["files.datasheet_refused"], said
        assert said[0]["text"] == ("www.analog.com would not let ecadlib download the datasheet (it reset the connection): the "
                                   "maker's site most likely blocks automated downloads (bot protection)."), said[0]["text"]
        assert said[0]["fix"] == (f"Download the PDF in a browser ({adi}) and read it with ecadlib datasheet FILE.pdf "
                                  "--part LTM4671, or pick a footprint by hand."), said[0]["fix"]
    for e, why in ((TIMEOUT + " (3 tries)", "timed out on every try"), (TIMEOUT, None),
                   ("SSLError: certificate verify failed (3 tries)", None), ("HTTP 404", None)):
        r = D.refusal([{"url": "https://www.st.com/stulpi01a.pdf", "error": "https://www.st.com/stulpi01a.pdf: " + e}])
        assert (r and r[2]) == why, (e, r)


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_datasheet_downloads"]))
