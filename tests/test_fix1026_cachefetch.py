"""2026-10-26 fresh hunt round 5 on PR #196 (reviews/2026-10-26_review_196/cachefetch.md, R-CF-1..3).
Plain functions: python -m tests.run_all test_fix1026_cachefetch"""
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-CF-1: the package code inside an ordering tail
_OTHER = "Package Diagrams 256-ball caBGA (BGA256) 14 x 14 mm, 0.8 mm pitch. Dimensions A A1 b D E e"


def _own(mpn):
    return {"BGA256"} if "256" in mpn else {"BGA121"} if "121" in mpn else {"BGA153"} if "153" in mpn else {"BGA132"}


def test_the_package_code_is_found_before_an_ordering_tail():
    from ecadlib import outline_docs as OD
    want = {"EPM1270M256": "M256", "EPM1270M256C5N": "M256", "EPM1270M256I5N": "M256", "EPM570M256C4N": "M256",
            "ICE40HX8K-BG121": "BG121", "ICE40HX8K-BG121TR": "BG121", "10M08SCM153C8G": "M153",
            "LCMXO2-1200HC-4MG132C": "MG132", "M2GL090T-FG484": "FG484"}
    for mpn, code in want.items():
        own = {"BGA484"} if "484" in mpn else _own(mpn)
        assert OD.mpn_package_code(mpn, own) == code, (mpn, OD.mpn_package_code(mpn, own))
    # a number that is the part number itself, or no count of this part: no code
    for mpn, own in (("74HC14", {"SOIC14"}), ("AT24C08", {"SOIC8"}), ("XYZ-T23", {"SOT23"}), ("GBU4A", {"DIP275"}),
                     ("LM358DR", {"SOIC8"}), ("STM32F103C8T6", {"LQFP48"})):
        assert OD.mpn_package_code(mpn, own) is None, (mpn, OD.mpn_package_code(mpn, own))


def test_another_makers_drawing_of_the_same_count_is_not_taken_for_a_tailed_part_number():
    from ecadlib import outline_docs as OD
    for mpn in ("EPM1270M256C5N", "EPM1270M256I5N", "EPM570M256C4N", "ICE40HX8K-BG121TR", "10M08SCM153C8G",
                "LCMXO2-1200HC-4MG132C"):
        own = _own(mpn)
        doc = [_OTHER.replace("256", next(iter(own))[3:])]
        assert not OD.names_it(doc, mpn, (), own, "https://www.latticesemi.com/pkg.pdf"), mpn
    # the document that prints the code is still taken
    assert OD.names_it(["M256 Micro FineLine BGA-256 package outline, 0.5 mm pitch, DIM MIN MAX"], "EPM1270M256C5N",
                       own={"BGA", "BGA256"})
    assert OD.names_it(["Intel MAX 10 M153 package: 153-pin Micro FineLine BGA (BGA-153), DIM MIN MAX"],
                       "10M08SCM153C8G", own={"BGA153"})


def test_with_no_code_in_the_part_number_a_drawing_naming_another_package_code_is_refused():
    from ecadlib import outline_docs as OD
    # no code in the part number: the family and count still match a plain drawing (GBU4A took Vishay's GBU outline)
    assert OD.names_it(["GBU25H08 GBU case outline, DIM MIN MAX, DIP-275"], "GBU4A", own={"DIP275"})
    assert OD.names_it([_OTHER], "XQ7Z100-1I", own={"BGA256"})            # BGA256 is the family's own name
    # ... but not a drawing that names another maker's code for that count
    assert not OD.names_it(["Package outline FTG256 fine-pitch BGA 17 x 17 mm (BGA-256), DIM MIN MAX"],
                           "XQ7Z100-1I", own={"BGA256"})
    assert not OD.names_it(["256-Pin FineLine BGA package outline FG256, 1.0 mm pitch, BGA-256, DIM MIN MAX"],
                           "XQ7Z100-1I", own={"BGA256"})
    # a family's own names are no other maker's code (SOIC-14 for an SO14, FBGA-256 for a BGA256)
    assert OD.names_it(["SOIC-14 package outline D0014A. 14 pins, DIM MIN MAX, SO-14"], "74HC14", own={"SO14"})


# ---------------------------------------------------------------- R-CF-2: the browser takes only the link's own PDF
_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
_HTML = (b"<!doctype html><html><body><h1>LM358DR</h1><a href='/lit/ds/lm358.pdf'>Datasheet</a>"
         b"<iframe src='/rohs/declaration.pdf'></iframe></body></html>")


class _Req:
    def __init__(self, nav):
        self.nav = nav

    def is_navigation_request(self):
        return self.nav


class _Resp:
    def __init__(self, url, ctype, body, nav, frame, size=None):
        self.url, self.status, self.request, self.frame, self._b = url, 200, _Req(nav), frame, body
        self.headers = {"content-type": ctype, **({"content-length": str(size)} if size else {})}
        self.read = False

    def body(self):
        self.read = True
        return self._b


class _Page:
    main_frame, sub = object(), object()

    def __init__(self, responses):
        self.cbs, self.url, self.responses = {}, "about:blank", responses

    def on(self, ev, cb):
        self.cbs.setdefault(ev, []).append(cb)

    def goto(self, url, **k):
        if url == "about:blank":
            return
        self.url = url
        for r in self.responses(self):
            for cb in self.cbs.get("response", []):
                cb(r)

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        return _HTML.decode()

    def close(self):
        pass


def _browser_get(responses, fetched=None):
    from ecadlib import pdf_fetch as PF

    class Ctx:
        def new_page(self):
            return _Page(responses)
    b = PF.Browser.__new__(PF.Browser)
    b.timeout, b.settle, b.challenge_s = 1, 0, 0
    b._start = lambda s: None
    b._fetch_in_page = lambda page, url: (fetched.append(url), (0, None, "", url, "capped"))[1]
    s = PF._State()
    s.ctx, s.warm = Ctx(), {"www.example-maker.com"}
    return b._get("https://www.example-maker.com/product/LM358DR", s)


def test_the_browser_never_returns_a_pdf_the_page_embedded_as_the_links_answer():
    url = "https://www.example-maker.com/product/LM358DR"
    st, data, ctype, final, err = _browser_get(lambda p: [
        _Resp(url, "text/html", _HTML, True, p.main_frame),
        _Resp("https://www.example-maker.com/rohs/declaration.pdf", "application/pdf", _PDF, False, p.sub)])[:5]
    assert data == _HTML and "html" in ctype and not err, (st, ctype, final, err)   # the page: pdf_link chooses
    # the link that is the PDF itself (the main frame's navigation) is still taken
    pdf = "https://www.example-maker.com/lit/ds/lm358.pdf"
    st, data, ctype, final, err = _browser_get(lambda p: [_Resp(pdf, "application/pdf", _PDF, True, p.main_frame)])[:5]
    assert data == _PDF and final == pdf and not err, (final, err)


def test_the_browser_does_not_read_an_oversized_pdf_body():
    from ecadlib.datasheet import MAX_DOWNLOAD
    big = _Resp("https://www.example-maker.com/lit/ds/lm358.pdf", "application/pdf", _PDF, True, None,
                size=MAX_DOWNLOAD + 1)
    fetched = []

    def responses(p):
        big.frame = p.main_frame
        return [big]
    st, data, ctype, final, err = _browser_get(responses, fetched)[:5]
    assert not big.read and fetched == [big.url] and data is None, (big.read, fetched, err)   # the capped fetch
