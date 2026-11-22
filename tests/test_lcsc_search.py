"""The LCSC search route (providers.LCSCSearch): MPN -> LCSC number by the jlcsearch index, then LCSC's own product
page for each exact listing, for parts only LCSC sells when JLCPCB's search refuses. No network: every answer is a
fake built from the field paths the route reads. Run:  python -m tests.test_lcsc_search"""
import json
from urllib.parse import unquote

from ecadlib import bom_triage as T, plugins as PL, providers as P
from ecadlib.httpcache import Response
from ecadlib.kicad_index import KiCadIndex


class Http:
    """search: MPN -> list of jlcsearch rows; detail: LCSC number -> wmsc result dict (None = {"result": null});
    refuse: site names that answer 403."""
    def __init__(self, search=None, detail=None, refuse=()):
        self.search, self.detail, self.refuse, self.calls = search or {}, detail or {}, set(refuse), []

    def get(self, url, headers=None, **kw):
        self.calls.append(url)
        if url.startswith("https://jlcsearch.tscircuit.com/api/search?q="):
            if "jlcsearch" in self.refuse:
                return Response(403, "", error="HTTP 403")
            q = unquote(url.split("q=", 1)[1].split("&", 1)[0])
            return Response(200, json.dumps({"components": self.search.get(q, [])}))
        if url.startswith("https://wmsc.lcsc.com/ftps/wm/product/detail?productCode="):
            if "wmsc" in self.refuse:
                return Response(403, "", error="HTTP 403")
            code = url.rsplit("=", 1)[1]
            return Response(200, json.dumps({"code": 200, "result": self.detail.get(code)}))
        return Response(404, "", error="HTTP 404")

    def post(self, url, body, headers=None, **kw):          # JLCPCB's search: refused, as from the PC on 2026-11-17
        self.calls.append(url)
        return Response(403, "", error="HTTP 403")


def row(code, mfr, package="SOP-16", desc="USB to serial"):
    return {"lcsc": code, "mfr": mfr, "package": package, "is_basic": False, "description": desc, "stock": 10, "price": 1.0}


def page(code, model, brand="WCH", encap="SOP-16", pdf="https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/C84681.pdf"):
    return {"productCode": code, "productModel": model, "brandNameEn": brand, "encapStandard": encap, "pdfUrl": pdf,
            "productIntroEn": "USB-to-UART converter SOP-16", "stockNumber": 100}


def test_mpn_to_lcsc_number_with_maker_package_and_datasheet():
    http = Http({"CH340G": [row(14267, "CH340G")]}, {"C14267": page("C14267", "CH340G")})
    r = P.LCSCSearch(http).lookup({"mpn": "CH340G"})
    assert r.found and r.lcsc == "C14267" and r.error == "", r
    assert r.manufacturer == "WCH" and r.extra["makers"] == ["WCH"], (r.manufacturer, r.extra)
    assert r.package == "SOP-16" and r.extra["datasheet"].endswith("C84681.pdf"), r
    assert r.extra["description"] == "USB-to-UART converter SOP-16", r.extra


def test_only_an_exact_mpn_counts():
    # CH340 / CH340G-TR are other numbers: nothing is taken from a near match (hold rather than build wrong)
    http = Http({"CH340G": [row(1, "CH340"), row(2, "CH340G-TR")]}, {})
    r = P.LCSCSearch(http).lookup({"mpn": "CH340G"})
    assert r.found is False and r.lcsc == "" and r.error == "", r
    assert not any("wmsc" in c for c in http.calls), http.calls      # no product page read for a near match


def test_a_product_page_for_another_part_or_number_is_not_used():
    http = Http({"CH552T": [row(111367, "CH552T")]}, {"C111367": page("C111367", "CH554T")})
    r = P.LCSCSearch(http).lookup({"mpn": "CH552T"})
    assert r.found is False and "CH554T" in r.extra.get("note", ""), r
    http = Http({"CH552T": [row(111367, "CH552T")]}, {"C111367": page("C999", "CH552T")})
    r = P.LCSCSearch(http).lookup({"mpn": "CH552T"})          # answered for another number: the index row alone
    assert r.found and r.lcsc == "C111367" and r.manufacturer == "" and "datasheet" not in r.extra, r


def test_clones_under_one_mpn_name_no_maker_and_the_boms_maker_is_preferred():
    rows = [row(2001, "WS2812B", "SMD5050"), row(2002, "WS2812B", "SMD5050")]
    pages = {"C2001": page("C2001", "WS2812B", "Clone Co", "SMD5050"),
             "C2002": page("C2002", "WS2812B", "Worldsemi", "SMD5050")}
    r = P.LCSCSearch(Http({"WS2812B": rows}, pages)).lookup({"mpn": "WS2812B"})
    assert r.found and sorted(r.extra["makers"]) == ["Clone Co", "Worldsemi"], r.extra
    r = P.LCSCSearch(Http({"WS2812B": rows}, pages)).lookup({"mpn": "WS2812B", "manufacturer": "Worldsemi"})
    assert r.lcsc == "C2002" and r.manufacturer == "Worldsemi", r


def test_an_unread_listing_means_no_maker_is_named():
    # two exact listings, only one product page answers: the other may be a clone's, so no single maker is claimed
    rows = [row(3001, "NTAG213"), row(3002, "NTAG213")]
    http = Http({"NTAG213": rows}, {"C3001": page("C3001", "NTAG213", "Clone")})
    r = P.LCSCSearch(http).lookup({"mpn": "NTAG213"})
    assert r.found and r.extra["makers"] == ["Clone"] and r.extra["makers_partial"], r.extra
    t = T.triage([{"designator": "U1", "mpn": "NTAG213"}], KiCadIndex([]), [P.LCSCSearch(http)], None)[0]
    assert t["src_manufacturer"] == "" and "not all with a known maker" in t["notes"], t
    # the BOM names NXP, LCSC's one readable listing is a clone's: the clone is not taken as the only maker
    t = T.triage([{"designator": "U1", "mpn": "NTAG213", "manufacturer": "NXP"}], KiCadIndex([]), [P.LCSCSearch(http)], None)[0]
    assert t["src_manufacturer"] == "", t


def test_a_product_page_naming_no_maker_counts_as_unread():
    rows = [row(3001, "NTAG213"), row(3002, "NTAG213")]
    pages = {"C3001": page("C3001", "NTAG213", "NXP"), "C3002": page("C3002", "NTAG213", "")}
    http = Http({"NTAG213": rows}, pages)
    assert P.LCSCSearch(http).lookup({"mpn": "NTAG213"}).extra["makers_partial"]
    t = T.triage([{"designator": "U1", "mpn": "NTAG213"}], KiCadIndex([]), [P.LCSCSearch(http)], None)[0]
    assert t["src_manufacturer"] == "", t                     # NXP is not filled: the other listing may be a clone


def test_only_the_first_listings_product_pages_are_read():
    n = P.LCSCSearch.DETAIL_CAP + 2
    rows = [row(4000 + i, "AMS1117-3.3", "SOT-223") for i in range(n)]
    pages = {f"C{4000 + i}": page(f"C{4000 + i}", "AMS1117-3.3", "AMS", "SOT-223") for i in range(n)}
    http = Http({"AMS1117-3.3": rows}, pages)
    r = P.LCSCSearch(http).lookup({"mpn": "AMS1117-3.3"})
    assert sum("wmsc" in c for c in http.calls) == P.LCSCSearch.DETAIL_CAP, http.calls
    assert r.found and r.extra["makers_partial"], r.extra     # the unread ones: no single maker


def test_a_failing_second_lcsc_search_does_not_unverify_the_line():
    # JLCPCB answered "no exact match"; the jlcsearch index is down: the line is not "source unreachable"
    class JlcEmpty(Http):
        def post(self, url, body, headers=None, **kw):
            return Response(200, json.dumps({"code": 200, "data": {"componentPageInfo": {"list": []}}}))
    http = JlcEmpty(refuse={"jlcsearch"})
    t = T.triage([{"designator": "U1", "mpn": "ZZ1"}], KiCadIndex([]), [P.JLCPCBSearch(http), P.LCSCSearch(http)], None)[0]
    assert t["status"] == "needs_footprint_generation" and "lcsc_search: the jlcsearch parts index refused" in t["notes"], t


def test_a_part_whose_lcsc_number_was_found_is_not_called_a_bad_mpn():
    import csv, tempfile
    from pathlib import Path
    from ecadlib import altium_menu as AM
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "triage.csv"
        def write(notes, lcsc=""):
            with f.open("w", encoding="utf-8", newline="") as h:
                w = csv.DictWriter(h, ["mpn", "status", "src", "lcsc", "notes"])
                w.writeheader()
                w.writerow({"mpn": "X1", "status": "needs_footprint_generation", "src": "", "lcsc": lcsc, "notes": notes})
        held = {"status": "held"}
        write("jlcpcb: no exact MPN match; lcsc: no exact MPN match")
        assert AM.not_found(f, "X1", held)
        write("jlcpcb_search: JLCPCB refused the request (403); lcsc: no exact MPN match")
        assert AM.not_found(f, "X1", held)                    # the LCSC search's own answer counts too
        write("jlcpcb: no exact MPN match; lcsc: MPN -> C123", "C123 (via MPN)")
        assert not AM.not_found(f, "X1", held)


def test_a_refusal_stops_that_site_for_the_run_and_is_an_error_not_a_miss():
    http = Http(refuse={"jlcsearch"})
    prov = P.LCSCSearch(http)
    r1, r2 = prov.lookup({"mpn": "A1"}), P.LCSCSearch(http).lookup({"mpn": "B2"})
    assert r1.found is None and "refused the request (403)" in r1.error, r1
    assert "not asked again this run" in r2.error, r2
    assert sum("jlcsearch" in c for c in http.calls) == 1, http.calls


def test_bad_answers_are_errors_not_crashes():
    class Bad(Http):
        def get(self, url, headers=None, **kw):
            return Response(200, "<html>not json</html>")
    r = P.LCSCSearch(Bad()).lookup({"mpn": "CH340G"})
    assert r.found is None and r.error.startswith("unparseable"), r
    http = Http({"X1": [{"lcsc": "C12;rm -rf", "mfr": "X1"}, {"lcsc": True, "mfr": "X1"}, "junk"]})
    assert P.LCSCSearch(http).lookup({"mpn": "X1"}).found is False      # only a real LCSC number is used
    assert P._lcsc_code(7593) == "C7593" and P._lcsc_code("C7593") == "C7593" and P._lcsc_code("x") == ""


def test_lookup_by_lcsc_number_reads_the_product_page():
    http = Http({}, {"C14267": page("C14267", "CH340G")})
    r = P.LCSCSearch(http).lookup_lcsc("C14267")
    assert r.found and r.mpn == "" and r.extra["description"] == "USB-to-UART converter SOP-16", r
    assert P.LCSCSearch(Http()).lookup_lcsc("C1").found is False             # {"result": null}: no such part
    assert P.LCSCSearch(Http()).lookup_lcsc("nope").error


def test_triage_finds_the_lcsc_number_when_jlcpcbs_search_refuses():
    http = Http({"CH340G": [row(14267, "CH340G")]}, {"C14267": page("C14267", "CH340G")})
    rows = T.triage([{"designator": "U1", "mpn": "CH340G"}], KiCadIndex([]),
                    [P.JLCPCBSearch(http), P.LCSCSearch(http)], None)
    r = rows[0]
    assert r["lcsc"] == "C14267 (via MPN)", r
    assert "lcsc: MPN -> C14267" in r["notes"] and "JLCPCB refused" in r["notes"], r["notes"]
    assert r["status"] != "unverified (source unreachable)", r["status"]
    assert r["src_manufacturer"] == "WCH" and r["src_datasheet"].endswith("C84681.pdf"), r


def test_triage_skips_the_search_once_the_lcsc_number_is_known():
    http = Http({}, {"C14267": page("C14267", "CH340G")})
    T.triage([{"designator": "U1", "mpn": "CH340G", "lcsc": "C14267"}], KiCadIndex([]), [P.LCSCSearch(http)], None)
    assert not any("jlcsearch" in c for c in http.calls), http.calls


def test_default_order_and_unofficial_fence():
    names = P.DEFAULT_ORDER.split(",")
    assert names.index("jlcpcb_search") < names.index("lcsc_search") < names.index("easyeda"), names
    got = [c.name for c in PL.select("easyeda,lcsc_search", None)]
    assert got == ["lcsc_search", "easyeda"], got                       # always before EasyEDA, which it feeds
    assert P.LCSCSearch not in P.fence([P.LCSCSearch, P.Nexar], {"providers": {"unofficial": False}})


def test_sources_check_reads_the_lcsc_route():
    from ecadlib import sources_check as SC
    good = Http({"NE555DR": [row(7593, "NE555DR", "SOIC-8")]}, {"C7593": page("C7593", "NE555DR", "Texas Instruments", "SOIC-8")})
    got = {(r[0], r[2]) for r in SC.check_lcsc(good)}
    assert got == {("lcsc_search", "ok"), ("lcsc_detail", "ok")}, got
    moved = Http({"NE555DR": [{"code": "C7593", "model": "NE555DR"}]}, {"C7593": {"code": "C7593"}})
    got = {(r[0], r[2]) for r in SC.check_lcsc(moved)}
    assert got == {("lcsc_search", "changed"), ("lcsc_detail", "changed")}, got
    got = {(r[0], r[2]) for r in SC.check_lcsc(Http(refuse={"jlcsearch", "wmsc"}))}
    assert got == {("lcsc_search", "unreachable"), ("lcsc_detail", "unreachable")}, got


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
    print("ok")
