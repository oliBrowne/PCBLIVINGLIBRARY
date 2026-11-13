"""[datasheet] archive: the Internet Archive's saved copy of a datasheet link that gave no PDF (ecadlib.ds_archive).
Plain functions (tests.run_all, no pytest). The Archive and every site are faked; nothing is sent anywhere."""
import functools
import json
import os
import tempfile
from pathlib import Path

from ecadlib import datasheet as D, ds_archive as AR
from tests.test_datasheet_batch import KEY, PDF, PdfHTTP, pdf_with
from tests.test_librarian import Response

DEAD = "https://maker.example/old/ds.pdf"
SNAP = "http://web.archive.org/web/20220413111507/https://maker.example/old/ds.pdf"
COPY = "https://web.archive.org/web/20220413111507id_/https://maker.example/old/ds.pdf"
OLD_SNAP = "http://web.archive.org/web/20210410000000/https://maker.example/old/ds.pdf"
OLD_COPY = "https://web.archive.org/web/20210410000000id_/https://maker.example/old/ds.pdf"
PAGE = b"<!doctype html><html><body>Access Denied</body></html>"
SLEPT = []


def _api(snap=SNAP, status="200", available=True):
    closest = {"status": status, "available": available, "url": snap, "timestamp": "20220413111507"}
    return json.dumps({"url": DEAD, "archived_snapshots": {"closest": closest} if snap else {}})


class ArcHTTP(PdfHTTP):
    """PdfHTTP plus the Archive's availability API: answers = {query: the API's JSON text}; down: an error for every
    ask, or a list of (status, error) answers given first, one per ask."""

    def __init__(self, pdfs=None, answers=None, down=None):
        super().__init__(pdfs)
        self.answers, self.down, self.asked = answers or {}, down, []

    def get(self, url, headers=None, **kw):
        if url.startswith(AR.API):
            self.asked.append(url)
            if isinstance(self.down, list):
                if self.down:
                    status, err = self.down.pop(0)
                    return Response(status, "", error=err)
            elif self.down:
                return Response(0, "", error=self.down)
            return Response(200, self.answers.get(url, _api(snap=None)))
        return super().get(url, headers=headers, **kw)


def setup():
    """The suite turns the Archive off (tests/__init__.py); these tests turn it on, against the fake above."""
    os.environ.pop("ECADLIB_NO_ARCHIVE", None)


def teardown():
    os.environ["ECADLIB_NO_ARCHIVE"] = "1"


def archive_on(fn):
    @functools.wraps(fn)
    def run():
        setup()
        try:
            return fn()
        finally:
            teardown()
    return run


def _reader(d, http, client=None, **ds):
    return D.Reader(Path(d) / "out", Path(d) / "cache", key=KEY, log=None, client=client, http=http,
                    sleep=SLEPT.append,
                    rules={"datasheet": {"cross_check": "off", **ds}})


def _fails(rd, urls=((DEAD, "BOM"),)):
    try:
        rd.job("TESTPART", list(urls), "SOIC-8", mpn="TESTPART")
    except D.DatasheetError as e:
        return str(e)
    raise AssertionError("a PDF was found")


# ---------------------------------------------------------------- the API
def test_the_query_keeps_the_links_own_query_and_spaces():
    q = AR.query("https://www.maker.example/dgdl/a%20b.pdf?fileId=5546&x=1")
    assert q == AR.API + "https%3A%2F%2Fwww.maker.example%2Fdgdl%2Fa%2520b.pdf%3FfileId%3D5546%26x%3D1"


def test_a_capture_is_read_as_its_raw_bytes_link_and_day():
    assert AR.parse(_api()) == (COPY, "2022-04-13")
    q = "http://web.archive.org/web/20240717152746/https://www.maker.example/dgdl/x.pdf?fileId=55"
    assert AR.parse(_api(q))[0] == "https://web.archive.org/web/20240717152746id_/https://www.maker.example/dgdl/x.pdf?fileId=55"


def test_no_capture_or_an_answer_that_is_not_the_apis_is_none():
    for text in (_api(snap=None), _api(status="404"), _api(available=False), _api("https://evil.example/web/20220413111507/x"),
                 _api("http://web.archive.org/web/2022/x"), "<html>blocked</html>", "", "[]", "null",
                 json.dumps({"archived_snapshots": {"closest": "x"}}), json.dumps({"archived_snapshots": []})):
        assert AR.parse(text) is None, text


def test_only_a_public_link_is_ever_sent_to_the_archive():
    for url in ("http://192.168.1.20/ds/x.pdf", "http://10.0.0.5/x.pdf", "http://127.0.0.1/x.pdf", "http://fileserver/ds/x.pdf",
                "https://docs.acme.local/x.pdf", "https://wiki.corp/x.pdf", "https://nas.lan/x.pdf",
                "https://user:pw@maker.example/x.pdf", "https://bucket.s3.example/x.pdf?X-Amz-Signature=abc&X-Amz-Expires=60",
                "https://maker.example/x.pdf?token=abc", "https://maker.example/x.pdf?api_key=abc", "ftp://maker.example/x.pdf",
                "file:///c:/x.pdf", "http://[::1]/x.pdf", "http://169.254.169.254/latest", "not a link", ""):
        assert AR.asked(url), url
    for url in (DEAD, "http://www.maker.example/dgdl/x.pdf?fileId=5546d462533600a4015355a7b8d5131e",
                "https://maker.example/docs/83725/4n25.pdf"):
        assert AR.asked(url) is None, url


def test_a_copy_is_worth_asking_for_only_after_the_links_own_failure():
    for e in (f"{DEAD}: HTTP 404", f"{DEAD}: HTTP 403 (a web page: the site refused the download)",
              f"{DEAD}: ReadTimeout: (ReadTimeoutError) x (3 tries) (also tried a browser's request headers: ProxyError: y)",
              f"{DEAD}: ConnectionError: (NameResolutionError) x", f"{DEAD}: the link is gone: it now goes to https://maker.example/",
              f"{DEAD}: not a PDF (an HTML page, 5120 bytes)", f"{DEAD}: SSLError: x"):
        assert AR.worth(e), e
    for e in ("", None, f"{DEAD}: ProxyError: proxy CONNECT refused (403 Forbidden)", f"{DEAD}: offline and not downloaded before",
              f"the PDF at {DEAD} doesn't mention TESTPART", f"{DEAD}: not downloaded (x is this machine or a cloud metadata service)",
              f"{DEAD}: larger than 80 MB; download it and pass --pages",
              f"{DEAD}: redirected to http://127.0.0.1/x, not used (127.0.0.1 is this machine)"):
        assert not AR.worth(e), e


# ---------------------------------------------------------------- the reader
@archive_on
def test_a_dead_link_is_read_from_its_archived_copy_and_the_review_says_so():
    http = ArcHTTP({DEAD: 404, COPY: PDF}, {AR.query(DEAD): _api()})
    with tempfile.TemporaryDirectory() as d:
        job = _reader(d, http).job("TESTPART", [(DEAD, "BOM")], "SOIC-8", mpn="TESTPART")
        prov = job["base_prov"]
        assert job["url"] == COPY and prov["archived"] == {"of": DEAD, "saved": "2022-04-13"}, prov
        assert prov["where"] == "Internet Archive copy of the BOM's link, saved 2022-04-13", prov["where"]
        assert prov["attempts"][0]["error"].endswith("HTTP 404")
        line = AR.review_line(prov)
        assert DEAD in line and "2022-04-13" in line and "newer revision" in line
        assert AR.review_line({"url": DEAD}) is None
        assert len(http.asked) == 1


@archive_on
def test_a_link_every_way_was_refused_for_is_read_from_its_archived_copy():
    http = ArcHTTP({DEAD: 403, COPY: PDF}, {AR.query(DEAD): _api()})
    with tempfile.TemporaryDirectory() as d:
        assert _reader(d, http).job("TESTPART", [(DEAD, "BOM")], "SOIC-8", mpn="TESTPART")["url"] == COPY


@archive_on
def test_http_and_https_of_one_link_are_asked_for_once():
    plain = DEAD.replace("https://", "http://")
    http = ArcHTTP({DEAD: 404, plain: 404, COPY: PDF}, {AR.query(DEAD): _api()})
    with tempfile.TemporaryDirectory() as d:
        assert _reader(d, http).job("TESTPART", [(plain, "BOM")], "SOIC-8", mpn="TESTPART")["url"] == COPY
        assert http.asked == [AR.query(DEAD)]


@archive_on
def test_a_link_that_works_never_asks_the_archive():
    http = ArcHTTP({DEAD: PDF}, {AR.query(DEAD): _api()})
    with tempfile.TemporaryDirectory() as d:
        job = _reader(d, http).job("TESTPART", [(DEAD, "BOM")], "SOIC-8", mpn="TESTPART")
        assert job["url"] == DEAD and not http.asked and "archived" not in job["base_prov"]


@archive_on
def test_the_second_links_copy_is_taken_when_the_first_has_none():
    other, snap = "https://maker.example/moved/ds2.pdf", "http://web.archive.org/web/20200102030405/https://maker.example/moved/ds2.pdf"
    copy = "https://web.archive.org/web/20200102030405id_/https://maker.example/moved/ds2.pdf"
    http = ArcHTTP({DEAD: 404, other: 404, copy: PDF}, {AR.query(other): _api(snap)})
    with tempfile.TemporaryDirectory() as d:
        job = _reader(d, http).job("TESTPART", [(DEAD, "BOM"), (other, "part source")], "SOIC-8", mpn="TESTPART")
        assert job["url"] == copy and job["base_prov"]["archived"]["of"] == other
        assert any(a["where"] == AR.WHERE and "no saved copy" in a["error"] for a in job["base_prov"]["attempts"])


@archive_on
def test_at_most_three_links_of_a_part_are_asked_for():
    links = [(f"https://maker.example/v{i}/ds.pdf", "BOM") for i in range(5)]
    http = ArcHTTP({u: 404 for u, _ in links})
    with tempfile.TemporaryDirectory() as d:
        _fails(_reader(d, http), links)
        assert len(http.asked) == AR.MAX_LINKS == 3


@archive_on
def test_off_never_asks_and_the_part_fails_as_before():
    http = ArcHTTP({DEAD: 404, COPY: PDF}, {AR.query(DEAD): _api()})
    with tempfile.TemporaryDirectory() as d:
        assert "HTTP 404" in _fails(_reader(d, http, archive=False)) and not http.asked


@archive_on
def test_no_copy_and_an_archive_that_is_down_are_said_in_the_error():
    with tempfile.TemporaryDirectory() as d:
        why = _fails(_reader(d, ArcHTTP({DEAD: 404})))
        assert "HTTP 404" in why and "the Internet Archive has no saved copy of it" in why, why
    with tempfile.TemporaryDirectory() as d:
        why = _fails(_reader(d, ArcHTTP({DEAD: 404}, down="ConnectionError: reset")))
        assert "the Internet Archive did not answer (ConnectionError: reset) (3 tries)" in why, why


@archive_on
def test_a_copy_that_is_not_the_parts_pdf_is_not_used():
    for copy_body, said in ((PAGE, "not a PDF"), (404, "HTTP 404")):
        http = ArcHTTP({DEAD: 404, COPY: copy_body}, {AR.query(DEAD): _api()})
        with tempfile.TemporaryDirectory() as d:
            why = _fails(_reader(d, http))
            assert said in why.split("; ", 1)[1], why


@archive_on
def test_a_copy_that_names_another_part_is_not_used():
    wrong = pdf_with(["LTC4054 Standalone Linear Li-Ion Battery Charger"] + ["page"] * 3)
    http = ArcHTTP({DEAD: 404, COPY: wrong}, {AR.query(DEAD): _api()})
    with tempfile.TemporaryDirectory() as d:
        try:
            _reader(d, http).job("LTC4060EDHC", [(DEAD, "BOM")], "DFN-16", mpn="LTC4060EDHC")
        except D.DatasheetError as e:
            assert f"the PDF at {COPY} doesn't mention LTC4060EDHC" in str(e), e
        else:
            raise AssertionError("another part's PDF was taken")


@archive_on
def test_a_private_link_and_this_machines_network_never_reach_the_archive():
    for url in ("http://192.168.1.20/ds/x.pdf", "https://docs.acme.local/x.pdf"):
        http = ArcHTTP({url: 404, url.replace("http://", "https://"): 404})
        with tempfile.TemporaryDirectory() as d:
            _fails(_reader(d, http), [(url, "BOM")])
            assert not http.asked, url
    http = ArcHTTP({})                               # every link: "ProxyError: proxy CONNECT refused"
    with tempfile.TemporaryDirectory() as d:
        job = _reader(d, http).job("TESTPART", [(DEAD, "BOM")], "SOIC-8", mpn="TESTPART")
        assert job["base_prov"]["sent_as"] == "url" and not http.asked   # (left to the API, as before)


@archive_on
def test_offline_never_asks():
    http = ArcHTTP({DEAD: 404, COPY: PDF}, {AR.query(DEAD): _api()})
    http.offline = True
    with tempfile.TemporaryDirectory() as d:
        try:
            _reader(d, http).job("TESTPART", [(DEAD, "BOM")], "SOIC-8", mpn="TESTPART")
        except D.DatasheetError:
            pass
        assert not http.asked


@archive_on
def test_the_archive_comes_before_a_paid_web_search():
    class NoSearch:
        def messages(self, body, headers=None):
            raise AssertionError("a web search was paid for")
    http = ArcHTTP({DEAD: 404, COPY: PDF}, {AR.query(DEAD): _api()})
    with tempfile.TemporaryDirectory() as d:
        job = _reader(d, http, client=NoSearch(), web_search=True).job("TESTPART", [(DEAD, "BOM")], "SOIC-8", mpn="TESTPART")
        assert job["url"] == COPY


@archive_on
def test_a_busy_archive_is_asked_again_and_a_proxy_refusal_is_not():
    for first in ([(429, "HTTP 429")], [(503, "HTTP 503"), (0, "ReadTimeout: slow")]):
        http, n = ArcHTTP({DEAD: 404, COPY: PDF}, {AR.query(DEAD): _api()}, down=list(first)), len(first)
        del SLEPT[:]
        with tempfile.TemporaryDirectory() as d:
            assert _reader(d, http).job("TESTPART", [(DEAD, "BOM")], "SOIC-8", mpn="TESTPART")["url"] == COPY
            assert len(http.asked) == n + 1 and SLEPT == [2, 4][:n], (http.asked, SLEPT)
    for err in ("ProxyError: proxy CONNECT refused (403 Forbidden)", "offline: not in cache"):
        http = ArcHTTP({DEAD: 404, COPY: PDF}, {AR.query(DEAD): _api()}, down=err)
        del SLEPT[:]
        with tempfile.TemporaryDirectory() as d:
            why = _fails(_reader(d, http))
            assert len(http.asked) == 1 and not SLEPT and "tries)" not in why.split("Internet Archive")[1], why
    assert AR.lookup(DEAD, ArcHTTP(down=[(404, "HTTP 404")]), sleep=SLEPT.append)[1].endswith("(HTTP 404)") and not SLEPT


@archive_on
def test_an_older_capture_is_tried_when_the_newest_is_not_the_pdf():
    older = AR.query(DEAD, before="20210413")           # a year before the capture of 2022-04-13
    assert AR.earlier("2022-04-13") == "20210413" and older.endswith("&timestamp=20210413")
    for bad in (PAGE, 404, PDF[:len(PDF) // 2]):
        http = ArcHTTP({DEAD: 404, COPY: bad, OLD_COPY: PDF}, {AR.query(DEAD): _api(), older: _api(OLD_SNAP)})
        with tempfile.TemporaryDirectory() as d:
            job = _reader(d, http).job("TESTPART", [(DEAD, "BOM")], "SOIC-8", mpn="TESTPART")
            assert job["url"] == OLD_COPY and job["base_prov"]["archived"] == {"of": DEAD, "saved": "2021-04-10"}, bad
            assert http.asked == [AR.query(DEAD), older]


@archive_on
def test_older_captures_stop_at_two_at_the_same_capture_and_at_another_parts_pdf():
    o1, o2, o3 = (AR.query(DEAD, before=b) for b in ("20210413", "20200410", "20190411"))
    snap2 = "http://web.archive.org/web/20200411000000/https://maker.example/old/ds.pdf"
    snap3 = "http://web.archive.org/web/20190412000000/https://maker.example/old/ds.pdf"
    pdfs = {DEAD: 404, COPY: PAGE, OLD_COPY: PAGE, AR.parse(_api(snap2))[0]: PAGE, AR.parse(_api(snap3))[0]: PDF}
    http = ArcHTTP(pdfs, {AR.query(DEAD): _api(), o1: _api(OLD_SNAP), o2: _api(snap2), o3: _api(snap3)})
    with tempfile.TemporaryDirectory() as d:
        _fails(_reader(d, http))
        assert http.asked == [AR.query(DEAD), o1, o2] and AR.OLDER == 2, http.asked
    http = ArcHTTP({DEAD: 404, COPY: PAGE}, {AR.query(DEAD): _api(), o1: _api()})   # the same capture again
    with tempfile.TemporaryDirectory() as d:
        _fails(_reader(d, http))
        assert http.asked == [AR.query(DEAD), o1] and http.fetched.count(COPY) == 1
    wrong = pdf_with(["LTC4054 Standalone Linear Li-Ion Battery Charger"] + ["page"] * 3)
    http = ArcHTTP({DEAD: 404, COPY: wrong, OLD_COPY: PDF}, {AR.query(DEAD): _api(), o1: _api(OLD_SNAP)})
    with tempfile.TemporaryDirectory() as d:
        try:
            _reader(d, http).job("LTC4060EDHC", [(DEAD, "BOM")], "DFN-16", mpn="LTC4060EDHC")
        except D.DatasheetError:
            pass
        assert http.asked == [AR.query(DEAD)]            # the same document, older: not asked for


def test_the_setting_is_a_known_rules_key_on_by_default_and_can_be_turned_off():
    from ecadlib import rules as RU
    assert D.SETTINGS["archive"] is True and RU.DEFAULTS["datasheet"]["archive"] is True
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ecadlib-rules.toml"
        p.write_text("[datasheet]\narchive = false\n", encoding="utf-8")
        assert RU.load(p)["datasheet"]["archive"] is False
        p.write_text("[datasheet]\narchive = \"no\"\n", encoding="utf-8")
        try:
            RU.load(p)
        except RU.RulesError:
            pass
        else:
            raise AssertionError("a text value was taken for archive")


def test_the_suites_switch_keeps_every_other_test_from_the_archive():
    assert os.environ.get("ECADLIB_NO_ARCHIVE") == "1"
    http = ArcHTTP({DEAD: 404, COPY: PDF}, {AR.query(DEAD): _api()})
    with tempfile.TemporaryDirectory() as d:
        assert "HTTP 404" in _fails(_reader(d, http)) and not http.asked
