"""Offline tests for ecadlib.pdf_fetch and PdfStore's [datasheet] download chain: a datasheet link the maker's site
refuses to ecadlib's own request is tried again with a browser's headers, a Chrome TLS fingerprint (curl_cffi), a
headless browser (Playwright), and last in the person's own browser (Downloads folder).

Nothing leaves this machine: the chain's logic runs on fakes, and the real ways run against a small web server on
127.0.0.1 that refuses scripts the way maker sites do (a 403 for a script's User-Agent, a cookie only a page script
sets, a bot-check page that reloads itself). The real-way tests return early when curl_cffi / Playwright / a browser
is missing (ECADLIB_BROWSER= a Chromium binary picks one).   python3 -m tests.test_pdf_fetch"""
import http.server, io, os, re, sys, threading, time
from pathlib import Path

from ecadlib import datasheet as D, pdf_fetch as PF, rules as RU
from tests import test_crosscheck as TC, test_datasheet_batch as TB
from tests.fixtures_lite import lite

PDF = TC.pdf3()                                       # SYNTHETIC 3-page text PDF naming TESTPART


# ---------------------------------------------------------------- what counts as refused
def test_refused_moves_on_for_a_refusal_never_for_a_missing_file():
    page = b"<!doctype html><html><body>Access Denied. Reference #18.2f</body></html>"
    product = b"<html><body><a href='/files/testpart.pdf'>Datasheet</a></body></html>"
    link = lambda h: D.pdf_link(h, "https://www.maker.com/p/testpart")
    for status, data, err in ((403, page, None), (401, b"", None), (429, b"", None), (503, page, None),
                              (200, page, None), (0, None, "ConnectionError: ('Connection aborted.', RemoteDisconnected())"),
                              (0, None, "ReadTimeout: read timed out"), (0, None, "download too slow (12 KB in 120 s); stopped")):
        assert PF.refused(status, data, err, link), (status, err)
    for status, data, err in ((404, page, None), (410, b"", None), (200, PDF, None), (200, product, None),
                              (0, None, "ProxyError: proxy CONNECT refused (403 Forbidden)"),
                              (0, None, "ConnectionError: NameResolutionError: Failed to resolve 'nowhere.example'"),
                              (0, None, "SSLError: CERTIFICATE_VERIFY_FAILED")):
        assert not PF.refused(status, data, err, link), (status, err)


# ---------------------------------------------------------------- the chain, on fakes
URL = "https://www.analog.com/media/en/technical-documentation/data-sheets/ltm4671.pdf"


def store(d, plain, ways=("headers", "impersonate", "browser"), handoff=None, **answers):
    """A PdfStore whose own request answers from `plain` (TB.PdfHTTP) and each way from `answers`."""
    http = TB.PdfHTTP(plain)
    st = D.PdfStore(d, http, download=list(ways), handoff=handoff, sleep=lambda s: None)
    calls = []

    def way(name):
        def get(url, part):
            calls.append((name, url, part))
            a = answers.get(name, (403, b"<html>Access Denied</html>", "text/html", url, None))
            return a(url) if callable(a) else a
        return get
    st.fns = {w: way(w) for w in ("headers", "impersonate", "browser", "handoff")}
    st.missing = {}
    return st, http, calls


def test_a_refused_link_moves_on_until_a_way_gets_the_pdf():
    with __import__("tempfile").TemporaryDirectory() as d:
        st, http, calls = store(d, {URL: 403}, impersonate=(200, PDF, "application/pdf", URL, None))
        data, meta = st.fetch(URL, "BOM", part="LTM4671")
        assert data == PDF and meta["via"] == "impersonate" and [c[0] for c in calls] == ["headers", "impersonate"]
        assert calls[0][2] == "LTM4671"                               # the MPN goes along (the hand-off checks it)
        url2 = URL.replace("ltm4671", "lt8610")                       # the same site again: the way that worked first
        st.fns["impersonate"] = lambda url, part: (calls.append(("impersonate", url, part)) or
                                                   (200, PDF, "application/pdf", url, None))
        st.fetch(url2, "BOM")
        assert http.fetched == [URL] and calls[-1][:2] == ("impersonate", url2), (http.fetched, calls)
        assert st.fetch(URL, "BOM")[1]["from_cache"] is True          # cached like any download: no way runs again


def test_a_missing_file_never_moves_on_and_stays_a_broken_link():
    with __import__("tempfile").TemporaryDirectory() as d:
        st, _, calls = store(d, {URL: 404})
        try:
            st.fetch(URL)
            raise AssertionError("a 404 gave a PDF")
        except D.DatasheetError as e:
            assert calls == [] and re.search(D.BROKEN_LINK, str(e)) and "also tried" not in str(e), e


def test_every_way_refused_says_what_each_got_and_stays_a_refusal():
    with __import__("tempfile").TemporaryDirectory() as d:
        st, _, calls = store(d, {URL: 403}, headers=(429, b"", "text/html", URL, None),
                             impersonate=(0, None, "", URL, "ConnectionError: Connection reset by peer"))
        st.missing = {"browser": f"not installed ({PF.INSTALL})"}
        try:
            st.fetch(URL)
            raise AssertionError
        except D.DatasheetError as e:
            msg = str(e)
        assert [c[0] for c in calls] == ["headers", "impersonate"]  # the browser is missing: not called
        assert msg.startswith(f"{URL}: HTTP 403") and "also tried a browser's request headers: status 429" in msg, msg
        assert "a Chrome TLS fingerprint (curl_cffi): ConnectionError" in msg and 'pip install "ecadlib[browser]"' in msg
        assert not re.search(D.BROKEN_LINK, msg), msg                 # refused, not broken: the link may still go to the API


def test_plain_never_tries_another_way_and_the_hand_off_comes_last():
    with __import__("tempfile").TemporaryDirectory() as d:
        st, _, calls = store(d, {URL: 403}, ways=())
        try:
            st.fetch(URL)
            raise AssertionError
        except D.DatasheetError as e:
            assert calls == [] and "also tried" not in str(e)

    class Hand:
        def get(self, url, part):
            return 200, PDF, "application/pdf", url, None, {"x-ecadlib-file": "ltm4671.pdf"}
    with __import__("tempfile").TemporaryDirectory() as d:
        st, _, calls = store(d, {URL: 403}, handoff=Hand())
        st.fns["handoff"] = lambda url, part: Hand().get(url, part)
        data, meta = st.fetch(URL, part="LTM4671")
        assert [c[0] for c in calls] == ["headers", "impersonate", "browser"] and meta["via"] == "handoff"
        assert meta["file"] == "ltm4671.pdf" and os.sep not in meta["file"]


def test_a_page_a_way_gets_is_followed_to_its_pdf():
    page = b"<html><body><a href='https://www.analog.com/files/ltm4671-ds.pdf'>Data sheet</a></body></html>"
    pdf_url = "https://www.analog.com/files/ltm4671-ds.pdf"
    with __import__("tempfile").TemporaryDirectory() as d:
        st, http, calls = store(d, {URL: 403, pdf_url: 403},
                                browser=lambda u: (200, page, "text/html", u, None) if u == URL else (200, PDF, "application/pdf", u, None))
        data, meta = st.fetch(URL, part="LTM4671")
        assert data == PDF and meta["page_url"] == URL and meta["page_via"] == "browser" and meta["via"] == "browser"
        assert http.fetched == [URL], http.fetched                  # the PDF link went straight to the browser


def test_a_later_way_that_finds_the_file_gone_ends_the_chain():
    with __import__("tempfile").TemporaryDirectory() as d:
        st, _, calls = store(d, {URL: 403}, handoff=object(), headers=(404, b"", "text/html", URL, None))
        try:
            st.fetch(URL, part="LTM4671")
            raise AssertionError("a 404 gave a PDF")
        except D.DatasheetError as e:
            assert [c[0] for c in calls] == ["headers"], calls              # no other way, never the person
            assert re.search(D.BROKEN_LINK, str(e)) and st.refusals == [], e


def test_a_later_ways_own_network_error_is_a_note_and_the_site_still_refused():
    """A headless browser that does not trust a TLS-inspecting proxy, a proxy only one way uses: the site's 403 is
    what the link got, so the refusal (and the person's browser after it) still count."""
    for err in ("ProxyError: proxy CONNECT refused (403 Forbidden)",
                "browser: Page.goto: net::ERR_CERT_AUTHORITY_INVALID at " + URL,
                "browser: Page.goto: net::ERR_NAME_NOT_RESOLVED at " + URL):
        with __import__("tempfile").TemporaryDirectory() as d:
            st, _, calls = store(d, {URL: 403}, handoff=object(), browser=(0, None, "", URL, err))
            try:
                st.fetch(URL, part="LTM4671", handoff=False)
                raise AssertionError(err)
            except D.DatasheetError as e:
                msg = str(e)
            assert [c[0] for c in calls] == ["headers", "impersonate", "browser"], calls
            assert msg.startswith(f"{URL}: HTTP 403") and err[:20] in msg and st.refusals == [(URL, "", None)], msg


def test_the_hand_off_is_never_remembered_and_a_404_never_reaches_it():
    url2, url3 = URL.replace("ltm4671", "ltm4671ey"), URL.replace("ltm4671", "lt8610")
    with __import__("tempfile").TemporaryDirectory() as d:
        st, http, calls = store(d, {URL: 403, url2: 404, url3: 403}, ways=("headers",), handoff=object())
        st.fns["handoff"] = lambda url, part: (calls.append(("handoff", url, part)) or
                                               (200, PDF, "application/pdf", url, None, {"x-ecadlib-file": "x.pdf"}))
        assert st.fetch(URL, part="LTM4671")[1]["via"] == "handoff" and st.prefer == {}
        try:
            st.fetch(url2, part="LTM4671EY")
            raise AssertionError("a 404 gave a PDF")
        except D.DatasheetError as e:
            assert re.search(D.BROKEN_LINK, str(e)) and calls[-1][1] != url2, (e, calls)
        st.fetch(url3, part="LT8610")
        assert http.fetched == [URL, url2, url3]                    # ecadlib's own request first, every time
        assert [c[0] for c in calls] == ["headers", "handoff", "headers", "handoff"], calls


def test_a_way_that_broke_or_answered_no_pdf_is_a_note_and_the_next_way_runs():
    def boom(url):
        raise RuntimeError("the browser died")
    with __import__("tempfile").TemporaryDirectory() as d:
        st, _, calls = store(d, {URL: 403}, headers=(200, b'{"error":"forbidden"}', "application/json", URL, None),
                             impersonate=(200, b"", "application/pdf", URL, None), browser=boom)
        try:
            st.fetch(URL)
            raise AssertionError
        except D.DatasheetError as e:
            msg = str(e)
        assert [c[0] for c in calls] == ["headers", "impersonate", "browser"] and st.prefer == {}, calls
        assert "a browser's request headers: no PDF in its answer (application/json, 21 bytes)" in msg, msg
        assert "a headless browser: RuntimeError: the browser died" in msg, msg
        assert msg.startswith(f"{URL}: HTTP 403") and not re.search(D.BROKEN_LINK, msg), msg   # still a refusal
        assert st.refusals == [(URL, "", None)]


def test_a_page_that_only_refreshes_to_itself_is_refused():
    wait = f'<html><head><meta http-equiv="refresh" content="0;url={URL}"></head><body>Please wait</body></html>'.encode()
    with __import__("tempfile").TemporaryDirectory() as d:
        st, _, calls = store(d, {URL: wait}, headers=(200, PDF, "application/pdf", URL, None))
        assert st.fetch(URL)[1]["via"] == "headers" and len(calls) == 1


def test_a_refusal_keeps_its_review_line_with_the_other_ways_noted():
    e = (f"{URL}: ReadTimeout: HTTPSConnectionPool: read timed out (3 tries) (also tried a browser's request headers: "
         "status 403; a headless browser: not installed (pip install \"ecadlib[browser]\"))")
    assert D.refusal([{"url": URL, "error": e}]) == (URL, "www.analog.com", "timed out on every try")


@lite
def test_a_name_that_does_not_resolve_never_moves_on_however_long_the_link(monkeypatch):
    import socket, requests
    def nowhere(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")
    monkeypatch.setattr(socket, "getaddrinfo", nowhere)
    s = requests.Session()
    s.trust_env = False                                             # no proxy: the name itself is looked up
    long_url = "https://www.no-such-maker.invalid/" + "media/en/technical-documentation/data-sheets/" * 3 + "ltm4671.pdf"
    for url in ("https://nx.invalid/a.pdf", long_url):
        try:
            s.get(url, timeout=5)
            raise AssertionError(url)
        except requests.RequestException as e:
            err = PF._err(e)
        assert "NameResolutionError" in err and not PF.refused(0, None, err), err


def test_a_closed_port_never_moves_on():
    import socket, requests
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()                                                     # nothing listens there: connection refused
    s = requests.Session()
    s.trust_env = False
    try:
        s.get(f"http://127.0.0.1:{port}/" + "data-sheets/" * 12 + "ds.pdf", timeout=5)
        raise AssertionError
    except requests.RequestException as e:
        err = PF._err(e)
    assert not PF.refused(0, None, err), err


@lite
def test_the_hand_off_waits_until_no_link_of_the_part_worked(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_DOWNLOAD", "headers")
    lcsc, url2 = "https://www.lcsc.com/datasheet/C9999.pdf", URL.replace("ltm4671", "lt8610")
    rd = D.Reader(tmp_path / "out", tmp_path / "cache", key=TB.KEY, http=TB.PdfHTTP({URL: 403, url2: 403, lcsc: PDF}),
                  log=None, sleep=lambda s: None, poll=0, handoff=True, rules={"datasheet": {"downloads_folder": str(tmp_path)}})
    asked = []
    rd.store.fns = {"headers": lambda u, p: (403, b"<html>Access Denied</html>", "text/html", u, None),
                    "handoff": lambda u, p: asked.append((u, p)) or (200, PDF, "application/pdf", u, None,
                                                                     {"x-ecadlib-file": "lt8610.pdf"})}
    rd.store.missing = {}
    assert isinstance(rd.store.handoff, PF.Handoff)
    job = rd.job("LTM4671", [(URL, "BOM"), (lcsc, "LCSC")], "QFN")
    assert asked == [] and job["base_prov"]["url"] == lcsc, (asked, job["base_prov"])   # LCSC's copy, nobody asked
    job = rd.job("LT8610", [(url2, "BOM")], "QFN")
    prov = job["base_prov"]
    assert asked == [(url2, "LT8610")] and prov["via"] == "handoff" and prov["file"] == "lt8610.pdf", (asked, prov)
    assert [a["url"] for a in prov["attempts"]] == [url2]           # the automatic try is kept in the record


@lite
def test_a_handed_off_pdf_from_a_product_page_keeps_its_page_and_where(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_DOWNLOAD", "headers")
    page_url, pdf_url = "https://www.maker.com/products/xyz9876", "https://www.maker.com/files/xyz9876-datasheet.pdf"
    lcsc = "https://www.lcsc.com/datasheet/C1234.pdf"
    page = f"<html><body><a href='{pdf_url}'>Datasheet</a></body></html>".encode()
    rd = D.Reader(tmp_path / "out", tmp_path / "cache", key=TB.KEY, http=TB.PdfHTTP({page_url: page, pdf_url: 403, lcsc: 404}),
                  log=None, sleep=lambda s: None, poll=0, handoff=True, rules={"datasheet": {"downloads_folder": str(tmp_path)}})
    rd.store.fns = {"headers": lambda u, p: (403, b"<html>Access Denied</html>", "text/html", u, None),
                    "handoff": lambda u, p: (200, PDF, "application/pdf", u, None, {"x-ecadlib-file": "xyz9876.pdf"})}
    rd.store.missing = {}
    prov = rd.job("XYZ9876", [(page_url, "BOM"), (lcsc, "LCSC")], "QFN")["base_prov"]
    assert (prov["url"], prov["where"], prov["page_url"], prov["via"]) == (pdf_url, "BOM", page_url, "handoff"), prov


def test_the_hand_off_says_what_to_save_whatever_the_log_drops(tmp_path=None):
    import contextlib
    from ecadlib import librarian as L, term as TM
    with __import__("tempfile").TemporaryDirectory() as d:
        dropped = []
        rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=TB.PdfHTTP({}), log=dropped.append,
                      sleep=lambda s: None, poll=0, handoff=True, rules={"datasheet": {"downloads_folder": d}})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rd.store.handoff.say("  Save the PDF to X")
        assert "Save the PDF to X" in err.getvalue() and dropped == []        # its own printer, not the run's log
        screen = io.StringIO()
        prog = TM.Progress(TM.Term(screen, styled=True, width=80), 3, "parts")
        prog.t.set_live("[1/3] LTM4671  working")
        L.handoff_says(rd, prog)
        rd.store.handoff.say("  Save the PDF to X")
        out = screen.getvalue()
        assert "Save the PDF to X" in out and out.rstrip().endswith("working"), out   # above the live line, redrawn


@lite
def test_a_single_way_or_none_from_the_environment_or_the_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_DOWNLOAD", "browser")
    assert TC.reader(tmp_path, None).store.ways == ["browser"]
    monkeypatch.setenv("ECADLIB_DOWNLOAD", " headers , impersonate ")
    assert TC.reader(tmp_path, None).store.ways == ["headers", "impersonate"]
    for bad in ("curl", "headers,handoff", "off"):
        monkeypatch.setenv("ECADLIB_DOWNLOAD", bad)
        try:
            TC.reader(tmp_path, None)
            raise AssertionError(bad)
        except D.DatasheetError as e:
            assert "ECADLIB_DOWNLOAD" in str(e), e
    monkeypatch.setenv("ECADLIB_DOWNLOAD", "")
    assert TC.reader(tmp_path, {"datasheet": {"download": []}}).store.ways == []          # an empty list: no other way
    assert D.PdfStore(tmp_path, download="headers").ways == ["headers"]                  # never split into letters


@lite
def test_the_web_ui_and_servers_never_hand_off(tmp_path, monkeypatch):
    from ecadlib.app import Workspace
    monkeypatch.setattr(PF, "interactive", lambda *a, **k: True)
    ws = Workspace(tmp_path / "ws", offline=True)
    assert ws._datasheet_reader(tmp_path / "out", batch=False).store.handoff is None


def test_interactive_is_false_off_the_main_thread():
    class T:
        def isatty(self):
            return True
    got = []
    t = threading.Thread(target=lambda: got.append(PF.interactive(T(), T(), {})))
    t.start()
    t.join()
    assert got == [False] and PF.interactive(T(), T(), {}) is True


def test_one_browser_and_one_session_per_process():
    assert PF.shared("browser") is PF.shared("browser") and PF.shared("impersonate") is PF.shared("impersonate")
    with __import__("tempfile").TemporaryDirectory() as d:
        assert D.PdfStore(d)._tool("browser") is D.PdfStore(d)._tool("browser") is PF.shared("browser")


def test_a_proxy_with_a_password_is_split_for_the_browser():
    assert PF.pw_proxy("http://user:s%40cret@proxy.corp:8080", ".corp,localhost") == {
        "server": "http://proxy.corp:8080", "username": "user", "password": "s@cret", "bypass": ".corp,localhost"}
    assert PF.pw_proxy("proxy.corp:3128") == {"server": "http://proxy.corp:3128"}


# ---------------------------------------------------------------- settings
def test_settings_and_their_checks():
    assert RU.DEFAULTS["datasheet"]["download"] == D.SETTINGS["download"] == "auto"
    assert RU.DEFAULTS["datasheet"]["handoff"] == D.SETTINGS["handoff"] == "auto"
    RU.validate({"datasheet": {"download": "plain", "handoff": "off", "handoff_wait_s": 60, "downloads_folder": "~/dl"}}, "t")
    RU.validate({"datasheet": {"download": ["headers", "browser"]}}, "t")
    for bad in ({"download": "yes"}, {"download": ["headers", "curl"]}, {"handoff": "always"}, {"handoff_wait_s": 2},
                {"handoff_wait_s": True}, {"downloads_folder": 3}):
        try:
            RU.validate({"datasheet": bad}, "t")
            raise AssertionError(bad)
        except RU.RulesError as e:
            assert "datasheet." in str(e)


@lite
def test_reader_uses_the_chain_and_never_hands_off_unwatched(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_DOWNLOAD", "")                      # tests/__init__ sets "plain" for the suite
    monkeypatch.setattr(PF, "interactive", lambda *a, **k: False)
    rd = TC.reader(tmp_path, None)
    assert rd.store.ways == list(PF.TIERS) and rd.store.handoff is None
    monkeypatch.setattr(PF, "interactive", lambda *a, **k: True)
    rd = TC.reader(tmp_path, {"datasheet": {"downloads_folder": str(tmp_path)}})
    assert isinstance(rd.store.handoff, PF.Handoff) and rd.store.handoff.folder == tmp_path
    assert TC.reader(tmp_path, {"datasheet": {"handoff": "off", "download": "plain"}}).store.handoff is None
    assert TC.reader(tmp_path, {"datasheet": {"handoff": "off", "download": "plain"}}).store.ways == []
    monkeypatch.setenv("ECADLIB_DOWNLOAD", "headers,browser")
    assert TC.reader(tmp_path, None).store.ways == ["headers", "browser"]


def test_interactive_needs_two_terminals_and_no_ci():
    class T:
        def __init__(self, tty):
            self.tty = tty

        def isatty(self):
            return self.tty
    assert PF.interactive(T(True), T(True), {}) is True
    assert PF.interactive(T(True), T(False), {}) is False
    assert PF.interactive(T(True), T(True), {"CI": "true"}) is False
    assert PF.interactive(T(True), T(True), {"ECADLIB_NO_HANDOFF": "1"}) is False


# ---------------------------------------------------------------- the hand-off, on a fake Downloads folder
class Clock:
    def __init__(self):
        self.t, self.then = 0.0, []                                 # then: what happens on the next sleeps, in order

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.then:
            self.then.pop(0)()


@lite
def test_handoff_takes_the_new_pdf_named_for_the_link(tmp_path):
    (tmp_path / "old.pdf").write_bytes(PDF)                         # there before: never taken
    clock, said = Clock(), []

    def opened(url):                                                  # the browser writes a partial file, then the PDF
        part = tmp_path / "ltm4671.pdf.crdownload"
        part.write_bytes(b"%PDF-1.4 partial")
        clock.then += [lambda: None, lambda: part.rename(tmp_path / "ltm4671.pdf"),
                       lambda: (tmp_path / "ltm4671.pdf").write_bytes(PDF)]
    h = PF.Handoff(tmp_path, wait_s=30, say=said.append, open_url=opened, sleep=clock.sleep, clock=clock)
    status, data, ctype, final, err, hdr = h.get(URL, "LTM4671")
    assert (status, data, err, hdr["x-ecadlib-file"]) == (200, PDF, None, "ltm4671.pdf"), (status, err)
    assert any(URL in m for m in said) and any("Got ltm4671.pdf" in m for m in said)
    assert h.get(URL.replace("https://", "http://"), "LTM4671")[4] == "already opened in your browser this run"


@lite
def test_handoff_leaves_an_unrelated_pdf_alone_and_stops_asking_when_nobody_answers(tmp_path):
    clock, said, opened = Clock(), [], []
    other = TC.TR.pdf_lines([["Invoice 2026-114", "total due"]])

    def open_url(url):
        opened.append(url)
        (tmp_path / "invoice.pdf").write_bytes(other)
    h = PF.Handoff(tmp_path, wait_s=20, say=said.append, open_url=open_url, sleep=clock.sleep, clock=clock)
    r = h.get(URL, "LTM4671")
    assert r[1] is None and "no PDF for it appeared" in r[4] and any("invoice.pdf is not named" in m for m in said)
    r = h.get(URL.replace("ltm4671", "lt8610"), "LT8610")
    assert len(opened) == 1 and "nobody watching" in r[4]


@lite
def test_handoff_takes_a_pdf_whose_text_names_the_part(tmp_path):
    clock = Clock()
    h = PF.Handoff(tmp_path, wait_s=20, say=lambda m: None, sleep=clock.sleep, clock=clock,
                   open_url=lambda url: (tmp_path / "download (3).pdf").write_bytes(PDF))
    assert h.get("https://www.maker.com/getfile?id=99", "TESTPART")[1] == PDF


@lite
def test_handoff_takes_a_firefox_download_saved_over_its_empty_placeholder(tmp_path):
    clock = Clock()

    def opened(url):                                                  # Firefox: an empty file first, the bytes in .part
        (tmp_path / "ltm4671.pdf").write_bytes(b"")
        part = tmp_path / "ltm4671.pdf.part"
        part.write_bytes(PDF[:100])
        clock.then += [lambda: None, lambda: None, lambda: part.write_bytes(PDF),
                       lambda: os.replace(part, tmp_path / "ltm4671.pdf")]
        return True
    h = PF.Handoff(tmp_path, wait_s=30, say=lambda m: None, open_url=opened, sleep=clock.sleep, clock=clock)
    r = h.get(URL, "LTM4671")
    assert r[1] == PDF and r[4] is None, r[4]


@lite
def test_handoff_with_no_browser_here_says_so_at_once(tmp_path):
    clock, said = Clock(), []
    h = PF.Handoff(tmp_path, wait_s=60, say=said.append, open_url=lambda url: False, sleep=clock.sleep, clock=clock)
    r = h.get(URL, "LTM4671")
    assert r[4] == "no browser could be opened here" and clock.t == 0 and not h.gone, r[4]
    assert any(URL in m and "ecadlib datasheet FILE.pdf --part LTM4671" in m for m in said), said


@lite
def test_handoff_with_no_desktop_never_starts_a_console_browser(tmp_path, monkeypatch):
    import webbrowser
    gui = PF.gui
    assert gui({}, "linux") is False and gui({"DISPLAY": ":0"}, "linux") and gui({}, "win32")
    assert gui({"WAYLAND_DISPLAY": "wayland-0"}, "linux") and gui({}, "darwin")
    monkeypatch.setattr(PF, "gui", lambda *a, **k: False)
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a browser was run")))
    clock = Clock()
    h = PF.Handoff(tmp_path, wait_s=60, say=lambda m: None, sleep=clock.sleep, clock=clock)
    assert h.get(URL, "LTM4671")[4] == "no browser could be opened here" and clock.t == 0


@lite
def test_handoff_says_when_it_cannot_read_the_downloads_folder(tmp_path, monkeypatch):
    real = os.scandir

    def scandir(path="."):
        if Path(path) == tmp_path:
            raise PermissionError(1, "Operation not permitted")
        return real(path)
    monkeypatch.setattr(os, "scandir", scandir)
    clock, opened = Clock(), []
    h = PF.Handoff(tmp_path, wait_s=60, say=lambda m: None, open_url=opened.append, sleep=clock.sleep, clock=clock)
    r = h.get(URL, "LTM4671")
    assert "cannot read your Downloads folder" in r[4] and opened == [] and clock.t == 0 and not h.gone, r[4]


@lite
def test_handoff_errors_never_name_a_folder_on_this_machine(tmp_path):
    clock = Clock()
    for h in (PF.Handoff(tmp_path, wait_s=5, say=lambda m: None, open_url=lambda u: True, sleep=clock.sleep, clock=clock),
              PF.Handoff(tmp_path / "nowhere", say=lambda m: None)):
        err = h.get(URL, "LTM4671")[4]
        assert err and str(tmp_path) not in err and "Downloads folder" in err, err


@lite
def test_linux_downloads_folder_follows_xdg(tmp_path, monkeypatch):
    if sys.platform in ("win32", "darwin"):
        return
    (tmp_path / "Téléchargements").mkdir()
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Bureau"\nXDG_DOWNLOAD_DIR="$HOME/Téléchargements"\n',
                                                      encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", "")
    assert PF.downloads_dir() == tmp_path / "Téléchargements"
    (tmp_path / "cfg" / "user-dirs.dirs").write_text("", encoding="utf-8")
    assert PF.downloads_dir() == tmp_path / "Downloads"


def test_downloads_folder_setting():
    assert PF.downloads_dir("~/dl") == Path.home() / "dl"
    assert PF.downloads_dir("").name


# ---------------------------------------------------------------- the real ways, against a local site that refuses scripts
BIG = PDF + b"%" + bytes(range(256)) * 20000 + b"\n%%EOF\n"   # about 5 MB: read out of the page in pieces
COOKIE_JS = b"""<!doctype html><html><head><title>Maker</title><script>document.cookie = "human=1; path=/";</script>
</head><body>Maker home</body></html>"""
CHALLENGE = b"""<!doctype html><html><head><title>Just a moment...</title><script>
setTimeout(function(){ document.cookie = "cleared=1; path=/"; location.reload(); }, 600);</script></head>
<body>Checking your browser</body></html>"""
PRODUCT_JS = b"""<!doctype html><html><body><div id=d></div><script>
document.getElementById('d').innerHTML = "<a href='/js/ds.pdf'>Datasheet</a>";</script></body></html>"""


class Site(http.server.BaseHTTPRequestHandler):
    """/ua/ds.pdf: 403 to a script's User-Agent. /js/ds.pdf: 403 without the cookie the home page's script sets.
    /cf/ds.pdf: a bot-check page (503) whose script sets a cookie and reloads. /product: the PDF link is written by
    a script. /inline/ds.pdf: served inline (a PDF viewer would show it)."""
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        ua, cookie, path = self.headers.get("User-Agent", ""), self.headers.get("Cookie", ""), self.path.split("?")[0]
        pdf_hdr = (("Content-Disposition", "attachment; filename=ds.pdf"),)
        if path == "/":
            return self._send(200, COOKIE_JS)
        if "Chrome/" not in ua or "ecad" in ua.lower():
            return self._send(403, b"<!doctype html><html><body>Access Denied</body></html>")
        if path == "/ua/ds.pdf":
            return self._send(200, PDF, "application/pdf", pdf_hdr)
        if path == "/js/ds.pdf":
            return self._send(200, PDF, "application/pdf", pdf_hdr) if "human=1" in cookie else \
                self._send(403, b"<!doctype html><html><body>Access Denied</body></html>")
        if path == "/cf/ds.pdf":
            return self._send(200, PDF, "application/pdf", pdf_hdr) if "cleared=1" in cookie else self._send(503, CHALLENGE)
        if path == "/product":
            return self._send(200, PRODUCT_JS)
        host = self.headers.get("Host", "")
        if path == "/" and host.startswith("localhost"):          # a file host whose home page sends you elsewhere
            port = host.rsplit(":", 1)[-1]
            return self._send(200, f"<script>location.replace('http://127.0.0.1:{port}/en/')</script>".encode())
        if path == "/redir/ds.pdf":                                # a link that redirects to that file host
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_address[1]}/cdn/ds.pdf")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/cdn/ds.pdf":
            return self._send(200, PDF, "application/octet-stream", pdf_hdr)
        if path == "/big/ds.pdf":
            return self._send(200, BIG, "application/pdf", pdf_hdr)
        if path in ("/stall/ds.pdf", "/stallinline/ds.pdf"):     # headers and a little, then nothing: a stuck CDN edge
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(PDF) * 50))
            if path.startswith("/stall/"):
                self.send_header("Content-Disposition", "attachment; filename=ds.pdf")
            self.end_headers()
            self.wfile.write(PDF[:200])
            self.wfile.flush()
            time.sleep(25)
            return
        if path == "/inline/ds.pdf":
            return self._send(200, PDF, "application/pdf") if "human=1" in cookie else self._send(403, b"<html>no</html>")
        return self._send(404, b"<html>Not found</html>")


def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_browser_headers_pass_a_user_agent_check_only():
    srv, base = site()
    try:
        assert PF.headers_get(f"{base}/ua/ds.pdf")[:2] == (200, PDF)
        st = PF.headers_get(f"{base}/js/ds.pdf")
        assert st[0] == 403 and PF.refused(st[0], st[1], st[4])
    finally:
        srv.shutdown()


def test_impersonate_passes_a_user_agent_check():
    if not PF.impersonate_available():
        print("  (curl_cffi not installed: skipped)")
        return
    srv, base = site()
    imp = PF.Impersonate(timeout=20)
    try:
        r = imp.get(f"{base}/ua/ds.pdf")
        assert r[:2] == (200, PDF), r[:5]
        assert imp.get(f"{base}/js/ds.pdf")[0] == 403                # no page script ran: still refused
    finally:
        imp.close()
        srv.shutdown()


def _browser(**kw):
    if not PF.playwright_available():
        print("  (playwright not installed: skipped)")
        return None
    b = PF.Browser(**{"timeout": 20, "settle": 0.3, "challenge_s": 8, **kw})
    r = b._worker.call(lambda: (b._start(b._s), b._s.why)[1], 120)
    if r:
        print(f"  (no browser: {r}: skipped)")
        b.close()
        return None
    return b


def test_a_headless_browser_runs_the_site_scripts_and_keeps_the_pdf():
    b = _browser(allow_local=True)
    if b is None:
        return
    srv, base = site()
    try:
        assert PF.headers_get(f"{base}/js/ds.pdf")[0] == 403
        r = b.get(f"{base}/js/ds.pdf")                              # home page first (sets the cookie), then the download
        assert r[0] == 200 and r[1] == PDF and r[4] is None, r[:5] if r[4] else r[0]
        r = b.get(f"{base}/cf/ds.pdf")                              # a bot-check page that reloads itself
        assert r[0] == 200 and r[1] == PDF, (r[0], r[4], (r[1] or b"")[:80])
        r = b.get(f"{base}/product")                                # a script writes the link: the page as rendered
        assert r[0] == 200 and D.pdf_link(r[1], r[3]) == f"{base}/js/ds.pdf", r[:1]
        r = b.get(f"{base}/inline/ds.pdf")                          # no attachment header: the PDF response itself
        assert r[0] == 200 and D.is_pdf(r[1]), (r[0], r[4])
        ua = b._worker.call(lambda: (lambda pg: (pg.evaluate("navigator.userAgent"), pg.close())[0])(b._s.ctx.new_page()), 30)
        assert "HeadlessChrome" not in ua and "Chrome/" in ua, ua    # the site sees Chrome, not a headless one
    finally:
        b.close()
        srv.shutdown()


def test_a_site_that_stops_sending_is_given_up_and_the_browser_carries_on():
    b = _browser(allow_local=True, timeout=4)
    if b is None:
        return
    srv, base = site()
    try:
        for path in ("/stall/ds.pdf", "/stallinline/ds.pdf"):        # a download, then a PDF shown inline
            t0 = time.monotonic()
            r = b.get(base + path)
            assert r[1] is None and r[4] and time.monotonic() - t0 < 25, (path, r[0], r[4], time.monotonic() - t0)
        assert b.get(base + "/ua/ds.pdf")[:2] == (200, PDF)
    finally:
        b.close()
        srv.shutdown()


def test_a_browser_that_died_is_started_again():
    b = _browser(allow_local=True)
    if b is None:
        return
    srv, base = site()
    try:
        assert b.get(base + "/ua/ds.pdf")[:2] == (200, PDF)
        b._worker.call(lambda: b._s.browser.close(), 30)            # as if it crashed
        r = b.get(base + "/ua/ds.pdf")
        assert r[:2] == (200, PDF), r[4]
    finally:
        b.close()
        srv.shutdown()


def test_a_download_from_another_host_and_a_big_one():
    b = _browser(allow_local=True)
    if b is None:
        return
    srv, base = site()
    try:
        for _ in range(2):                                          # the second time on a fresh page of a warm site
            r = b.get(f"{base}/redir/ds.pdf")
            assert r[:2] == (200, PDF) and r[3].startswith("http://localhost:"), (r[0], r[4], r[3])
        r = b.get(f"{base}/big/ds.pdf")
        assert r[0] == 200 and r[1] == BIG, (r[0], r[4], len(r[1] or b""))
    finally:
        b.close()
        srv.shutdown()


def test_a_dead_driver_is_started_again_and_an_abandoned_call_stops_its_browser():
    b = _browser(allow_local=True, timeout=20)
    if b is None:
        return
    srv, base = site()
    try:
        assert b.get(f"{base}/ua/ds.pdf")[:2] == (200, PDF)
        import signal
        os.kill(b._s.pid, getattr(signal, "SIGKILL", signal.SIGTERM))   # the driver (and its browser) gone between calls
        time.sleep(1)
        r = b.get(f"{base}/ua/ds.pdf")
        assert r[:2] == (200, PDF), r[4]
        b.limit, old = 3, b._s
        t0 = time.monotonic()
        r = b.get(f"{base}/stall/ds.pdf")                           # never finishes: given up, its browser stopped
        assert "did not finish in 3 s" in (r[4] or "") and time.monotonic() - t0 < 10, r[4]
        assert b._s is not old and old.pid
        b.limit = 90
        assert b.get(f"{base}/ua/ds.pdf")[:2] == (200, PDF)
    finally:
        b.close()
        srv.shutdown()


@lite
def test_a_missing_driver_is_one_note(monkeypatch):
    if not PF.playwright_available():
        return
    from playwright._impl import _driver
    monkeypatch.setattr(_driver, "compute_driver_executable", lambda: ("/nonexistent/node", "/nonexistent/cli.js"))
    b = PF.Browser(timeout=5)
    try:
        r = b.get(URL)
        assert r[1] is None and "Playwright's driver is missing" in r[4], r[4]
    finally:
        b.close()


def test_a_call_that_never_returns_is_left_behind_on_its_own_thread():
    b = PF.Browser(timeout=1)
    b.limit = 0.5
    b._get = lambda url, s: time.sleep(3) or (200, PDF, "application/pdf", url, None)
    t0, stuck = time.monotonic(), b._worker
    r = b.get(URL)
    assert r[4] == "the browser did not finish in 0.5 s" and time.monotonic() - t0 < 2, r
    b._get = lambda url, s: (200, PDF, "application/pdf", url, None)
    assert b.get(URL)[:2] == (200, PDF) and b._worker is not stuck   # a new worker, not queued behind the stuck one
    b._get = lambda url, s: (_ for _ in ()).throw(RuntimeError("Target page, context or browser has been closed"))
    assert b.get(URL)[4] == "browser: RuntimeError: Target page, context or browser has been closed"
    b.close()


def test_the_program_exits_at_once_even_with_a_call_left_behind():
    import subprocess
    code = ("import time; from ecadlib import pdf_fetch as PF; b = PF.Browser(timeout=1); b.limit = 0.2; "
            "b._get = lambda url, s: time.sleep(120); print(b.get('https://www.maker.com/ds.pdf')[4]); "
            "PF.shared('browser').get")
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=90,
                       cwd=str(Path(__file__).resolve().parent.parent))
    assert r.returncode == 0 and "did not finish" in r.stdout and time.monotonic() - t0 < 20, (r.stdout, r.stderr[-500:])


def test_the_browser_never_opens_this_machine_from_a_link():
    b = _browser()                                                  # the shipped setting: local addresses blocked
    if b is None:
        return
    srv, base = site()
    try:
        r = b.get(f"{base}/ua/ds.pdf")
        assert r[1] in (None, b"") or not D.is_pdf(r[1]), r[:2]
    finally:
        b.close()
        srv.shutdown()


@lite
def test_the_whole_chain_against_the_local_site(tmp_path, monkeypatch):
    """PdfStore with the shipped "auto" chain, its local-address guard lifted for the test site only."""
    b = _browser(allow_local=True)
    if b is None:
        return
    srv, base = site()
    monkeypatch.setattr(D, "unsafe_download", lambda url: None)
    try:
        st = D.PdfStore(tmp_path, None, download="auto", sleep=lambda s: None)
        st.fns["browser"] = lambda url, part: b.get(url)            # this test's browser, not the shared one
        data, meta = st.fetch(f"{base}/js/ds.pdf", "BOM", part="TESTPART")
        assert data == PDF and meta["via"] == "browser", meta
        data, meta = st.fetch(f"{base}/ua/ds.pdf", "BOM")         # the same site: the browser first now
        assert meta["via"] == "browser"
        st2 = D.PdfStore(tmp_path / "2", None, download=["headers"], sleep=lambda s: None)
        assert st2.fetch(f"{base}/ua/ds.pdf")[1]["via"] == "headers"
    finally:
        b.close()
        srv.shutdown()


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            t = time.time()
            v()
            print(f"ok {k} ({time.time() - t:.1f} s)")
