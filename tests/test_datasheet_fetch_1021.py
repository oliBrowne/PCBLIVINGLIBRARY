"""Datasheet getting, 2026-10-21 (the 10-19 live run): an LCSC number found by MPN ("C10800 (via MPN)") gives the LCSC
link; a link the API could not fetch moves on to the next one; document-download links without .pdf on product pages;
a PDF that does not mention the part (a press release, another part's datasheet) is not taken as its datasheet.
    python3 -m tests.test_datasheet_fetch_1021"""
import json, tempfile

from ecadlib import bom_triage as T, datasheet as D, providers as P
from ecadlib.httpcache import CachedHTTP
from ecadlib.kicad_index import KiCadIndex
from tests import test_datasheet_batch as TB
from tests.test_datasheet_downloads import Scripted
from tests.test_offline import seed


# ---------------------------------------------------------------- 1. LCSC number found by MPN
def test_lcsc_number_takes_a_number_with_a_note():
    assert D.lcsc_number(ln={"lcsc": "C10800 (via MPN)"}) == "C10800"
    assert D.lcsc_number(row={"lcsc": " C7593  (via MPN) "}) == "C7593"
    assert D.lcsc_number(ln={"lcsc": "C2836293"}) == "C2836293"
    for bad in ("C10800x", "C12", "XC10800 (via MPN)", "C10800 via MPN", "see C10800", "C10800 (via MPN", "C1234C5678"):
        assert D.lcsc_number(ln={"lcsc": bad}) is None, bad


def test_a_triage_row_with_an_lcsc_number_by_mpn_gives_the_lcsc_link():
    with tempfile.TemporaryDirectory() as d:
        http = CachedHTTP(d, offline=True)
        ee = {"success": True, "result": {"title": "FAKE-PART", "dataStr": {"head": {"c_para": {
            "Manufacturer Part": "FAKE-PART", "Manufacturer": "ACME", "package": "SOT-23"}}, "shape": ["R~..."]},
            "packageDetail": {"title": "SOT-23-3_L2.9-W1.3", "dataStr": {"shape": ["PAD~..."]}}}}
        jlc = {"data": {"componentPageInfo": {"list": [{"componentCode": "C10800", "componentModelEn": "FAKE-PART",
                                                        "componentBrandEn": "ACME", "componentSpecificationEn": "SOT-23"}]}}}
        seed(http, "GET", P.EasyEDA.URL.format("C10800"), None, ee)
        seed(http, "POST", P.JLCPCBSearch.URL, json.dumps({"keyword": "FAKE-PART", "currentPage": 1, "pageSize": 10}), jlc)
        ln = {"mpn": "FAKE-PART", "designator": "Q9"}
        row = T.triage([ln], KiCadIndex([]), [P.JLCPCBSearch(http), P.EasyEDA(http)], http)[0]
        assert row["lcsc"] == "C10800 (via MPN)", row
        urls = D.datasheet_urls(None, ln, row)
        assert ("https://www.lcsc.com/datasheet/C10800.pdf", "LCSC datasheet URL pattern (C10800)") in urls, urls


# ---------------------------------------------------------------- 2. the API could not fetch the link either
def test_a_symbol_miss_whose_pdf_never_reached_the_model_is_no_pdf():
    from ecadlib.eval import runner as R
    row = lambda **k: dict({"pass": False, "source": "standard", "fields": {"symbol_pins": {"ok": False, "error": "x"}},
                            "tried": [], "errors": []}, **k)
    err = "datasheet: batch request errored: invalid_request_error: Unable to download the file. Please verify the URL"
    assert R.failure_type(row(tried=["standard: footprint SMB", err]), None, None) == "no_pdf"   # SM6T100A, 10-19 run
    assert R.failure_type(row(tried=["datasheet: failed (APIError: The request timed out while trying to download the "
                                     "file.)"]), None, None) == "no_pdf"
    assert R.failure_type(row(tried=["datasheet reader (BOM): symbol"]), None, None) == "symbol"   # it was read


A, B = "https://www.st.com/resource/en/datasheet/sm6t.pdf", "https://www.mouser.com/datasheet/2/389/sm6t-1849.pdf"
NO_FETCH = {"type": "error", "error": {"type": "invalid_request_error",
                                       "message": "Unable to download the file. Please verify the URL and try again."}}
PLAIN = {"datasheet": {"download": "plain"}}                                  # no other way to download here


def sources(api):
    return [c[3]["messages"][0]["content"][0]["source"].get("url") for c in api.of("/v1/messages")]


def test_a_link_the_api_could_not_fetch_moves_on_to_the_next_one():
    api = TB.FakeAPI(script=[(400, NO_FETCH)])
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = D.Reader(d, d, key=TB.KEY, http=Scripted({A: [403], B: [403]}), log=None, sleep=lambda s: None, rules=PLAIN)
        j = rd.job("TESTPART", [(A, "BOM"), (B, "mouser datasheet link")], "RGT")
        assert j["prov"]["sent_as"] == "url" and j["url"] == A
        res = rd.read_now(j)
        assert sources(api) == [A, B], sources(api)                           # one request per link, each once
        assert res["sidecar"]["pdf"]["url"] == B and res["sidecar"]["pdf"]["where"] == "mouser datasheet link"
        tried = res["sidecar"]["pdf"]["attempts"]
        assert tried[-1]["url"] == A and "the API could not fetch it" in tried[-1]["error"], tried
        assert rd.committed_usd > 0 and not rd._holds                          # only the answer is paid for


def test_the_same_document_is_never_asked_for_twice_and_the_last_failure_stands():
    api = TB.FakeAPI(script=[(400, NO_FETCH), (400, NO_FETCH), (400, NO_FETCH)])
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = D.Reader(d, d, key=TB.KEY, http=Scripted({A: [403], B: [403], "http://www.st.com/x.pdf": [403],
                                                         "https://www.st.com/x.pdf": [403]}),
                      log=None, sleep=lambda s: None, rules=PLAIN)
        try:
            rd.read_now(rd.job("TESTPART", [(A, "BOM"), (B, "mouser")], "RGT"))
            raise AssertionError("answered")
        except D.APIError as e:
            assert "Unable to download" in str(e)
        assert sources(api) == [A, B] and not rd._holds and rd.committed_usd == 0, sources(api)
        api.calls.clear()
        try:                                                                   # http and https: one document
            rd.read_now(rd.job("TESTPART", [("http://www.st.com/x.pdf", "BOM"), ("https://www.st.com/x.pdf", "kicad")], "RGT"))
            raise AssertionError("answered")
        except D.APIError:
            pass
        assert sources(api) == ["https://www.st.com/x.pdf"], sources(api)
    api = TB.FakeAPI(script=[(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}})])
    with tempfile.TemporaryDirectory() as d, TB.patched(api):                # another error: no other link is tried
        rd = D.Reader(d, d, key=TB.KEY, http=Scripted({A: [403], B: [403]}), log=None, sleep=lambda s: None, rules=PLAIN)
        try:
            rd.read_now(rd.job("TESTPART", [(A, "BOM"), (B, "mouser")], "RGT"))
            raise AssertionError("answered")
        except D.APIError:
            pass
        assert sources(api) == [A], sources(api)


def test_a_batch_request_the_api_could_not_fetch_goes_again_with_the_next_link():
    seen = []

    def answer(cid):
        seen.append(cid)
        return ("errored", "invalid_request_error", "The request timed out while trying to download the file.") \
            if len(seen) == 1 else TB.TD.message(TB.TD.GOOD)
    api = TB.FakeAPI(answer=answer, polls=0)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = D.Reader(d, d, key=TB.KEY, http=Scripted({A: [403], B: [403]}), batch=True, poll=0, log=None,
                      sleep=lambda s: None, rules=PLAIN)
        j = rd.job("TESTPART", [(A, "BOM"), (B, "mouser")], "RGT")
        rd.submit(j)
        got = rd.flush()[j["key"]]
        assert isinstance(got, dict) and got["sidecar"]["pdf"]["url"] == B, got
        sent = [rq["params"]["messages"][0]["content"][0]["source"]["url"] for b in api.batches.values() for rq in b["requests"]]
        assert sent == [A, B], sent


# ---------------------------------------------------------------- 3. document-download links on product pages
def page(*hrefs):
    return ("<!DOCTYPE html><html><body>" + "".join(f'<a href="{h}">link</a>' for h in hrefs) + "</body></html>").encode()


def test_a_product_page_link_to_a_document_download_is_followed_when_it_gives_a_pdf():
    cases = [("https://www.broadcom.com/products/x/afbr-s50mv85i", "https://docs.broadcom.com/docs/AFBR-S50MV85I-DS"),
             ("https://www.infineon.com/cms/en/product/x/", "/dgdl/?fileId=5546d4625e37f35a015e3a0b0b3c1f45"),
             ("https://www.latticesemi.com/ice40", "/view_document?document_id=49312"),
             ("https://industry.panasonic.com/products/x", "https://industry.panasonic.com/file-download?id=1234")]
    for prod, link in cases:
        assert D.pdf_link(page("/about", link), prod) is None                 # the refusal check's view: unchanged
        got = D.pdf_link(page("/about", "/products/compare", link), prod, docs=True)
        want = link if link.startswith("https") else "https://" + prod.split("/")[2] + link
        assert got == want, (got, want)
        with tempfile.TemporaryDirectory() as d:
            st = D.PdfStore(d, Scripted({prod: [(200, page("/about", link), {})], want: [TB.PDF]}), sleep=lambda s: None)
            data, prov = st.fetch(prod)
            assert data == TB.PDF and prov["page_url"] == prod and prov["url"] == want, prov
            st = D.PdfStore(d + "/2", Scripted({prod: [(200, page(link), {})], want: [(200, page("/x"), {})]}),
                            sleep=lambda s: None)
            try:                                                               # a web page there: not taken
                st.fetch(prod)
                raise AssertionError("taken")
            except D.DatasheetError as e:
                assert "not a PDF" in str(e), e
    assert D.pdf_link(page("/a.pdf", "https://docs.broadcom.com/docs/X"), "https://www.broadcom.com/p", docs=True) \
        == "https://www.broadcom.com/a.pdf"                                    # a .pdf link still comes first
    assert D.pdf_link(page("/downloads", "/support/download-center", "mailto:x@y.z"), "https://m.example/p", docs=True) is None


# ---------------------------------------------------------------- 4. a PDF that does not mention the part
FILLER = " Features: low quiescent current, wide input range, thermal shutdown, small package, RoHS compliant."
FAMILY = {"AD5274BRM": "AD5272/AD5274 1024-/256-Position, 1% Resistor Tolerance Error, I2C Interface",   # Analog Devices
          "BAT160A": "BAT160 series Medium power Schottky barrier diodes",                              # Nexperia
          "MAX22201ATC+": "MAX22201-MAX22207 36V, 3.5A Brushed DC Motor Drivers",
          "MAX22203ATC+T": "MAX22201-MAX22207 36V, 3.5A Brushed DC Motor Drivers",                      # inside the range
          "C8051F320-GQ": "C8051F32x Full Speed USB Flash MCU Family",                                  # Silicon Labs
          "LTC2942-1": "LTC2942-1 Battery Gas Gauge with Temperature, Voltage Measurement",
          "LTC2942IDCB-1#PBF": "LTC2942-1 Battery Gas Gauge with Temperature, Voltage Measurement",
          "LTC4060EDHC#PBF": "LTC4060 Standalone Linear NiMH/NiCd Fast Battery Charger",
          "TPS26600PWPR": "TPS2660x 60-V, 2.23-A Industrial eFuse With Integrated Reverse Input Polarity Protection",
          "SM6T100A": "SM6T Transil, transient voltage surge suppressor (TVS) 600 W",
          "lm1117mpx-3.3/nopb": "LM1117 800-mA, Low-Dropout Linear Regulator"}


def test_the_family_datasheet_names_the_part():
    for mpn, title in FAMILY.items():
        assert D.mentions_part([title + FILLER, "page 2"], mpn) is True, (mpn, title)


def test_another_parts_datasheet_or_a_press_release_does_not():
    ltc4054 = "LTC4054-4.2/LTC4054X-4.2 Standalone Linear Li-Ion Battery Charger with Thermal Regulation in ThinSOT"
    assert D.mentions_part([ltc4054 + FILLER], "LTC4060EDHC") is False         # the link LTC4060EDHC's row gave
    assert D.mentions_part([ltc4054 + FILLER], "LTC4060EDHC#PBF") is False     # "LTC40" is not the LTC4060
    press = ("Microchip Technology Announces New Family of Secure Microcontrollers. CHANDLER, Ariz. -- Microchip "
             "Technology Inc. (Nasdaq: MCHP) today announced ...")
    assert D.mentions_part([press], "MCP2221A-I/SL") is False
    assert D.mentions_part(["MAX22201-MAX22207 motor drivers" + FILLER], "MAX22208ATC") is False   # outside the range
    assert D.mentions_part(["AD5272/AD5274" + FILLER], "AD5280BRUZ") is False


def test_no_text_or_no_part_number_is_never_a_rejection():
    assert D.mentions_part(None, "LTC4060EDHC") is None                       # no text layer could be read
    assert D.mentions_part(["", " 1 ", ""], "LTC4060EDHC") is None             # a scanned PDF
    for part in ("", None, "ABC", "RESISTOR"):                                 # nothing that names a part
        assert D.mentions_part(["LTC4054" + FILLER], part) is None, part


def test_the_store_says_the_pdf_does_not_mention_the_part_and_the_next_link_is_tried():
    wrong, right = TB.pdf_with(["LTC4054 Standalone Linear Li-Ion Battery Charger" + FILLER]), \
        TB.pdf_with(["LTC4060 Standalone Linear NiMH/NiCd Fast Battery Charger" + FILLER])
    a, b = "https://www.analog.com/ltc4060.pdf", "https://www.mouser.com/datasheet/2/609/4060fb.pdf"
    with tempfile.TemporaryDirectory() as d:
        st = D.PdfStore(d, Scripted({a: [wrong], b: [right]}), sleep=lambda s: None)
        assert st.fetch(a)[0] == wrong                                         # no part named: taken as before
        try:
            st.fetch(a, mpn="LTC4060EDHC#PBF")                                 # (from the cache this time)
            raise AssertionError("taken")
        except D.DatasheetError as e:
            assert str(e) == f"the PDF at {a} doesn't mention LTC4060EDHC#PBF", e
        assert st.fetch(b, mpn="LTC4060EDHC#PBF")[0] == right
        rd = D.Reader(d, d, key=TB.KEY, http=Scripted({a: [wrong], b: [right]}), log=None, sleep=lambda s: None)
        j = rd.job("LTC4060EDHC#PBF", [(a, "BOM"), (b, "mouser datasheet link")], "DFN-10", mpn="LTC4060EDHC#PBF")
        assert j["url"] == b and j["prov"]["attempts"][0]["error"] == f"the PDF at {a} doesn't mention LTC4060EDHC#PBF", j["prov"]
        try:                                                                   # the wrong PDF alone: never sent to the API
            rd.job("LTC4060EDHC#PBF", [(a, "BOM")], "DFN-10", mpn="LTC4060EDHC#PBF")
            raise AssertionError("sent")
        except D.DatasheetError as e:
            assert "no datasheet link gave a PDF" in str(e) and "doesn't mention LTC4060EDHC#PBF" in str(e), e
        j = rd.job("U7", [(a, "BOM")], "DFN-10")                               # no MPN known: not checked
        assert j["url"] == a and j["data"] == wrong


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_datasheet_fetch_1021"]))
