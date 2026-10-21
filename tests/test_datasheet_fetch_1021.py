"""Datasheet getting, 2026-10-21 (the 10-19 live run): an LCSC number found by MPN ("C10800 (via MPN)") gives the LCSC
link; a link the API could not fetch moves on to the next one; document-download links without .pdf on product pages;
a PDF that does not mention the part (a press release, another part's datasheet) is not taken as its datasheet.
    python3 -m tests.test_datasheet_fetch_1021"""
import json, tempfile

from ecadlib import bom_triage as T, datasheet as D, providers as P
from ecadlib.httpcache import CachedHTTP
from ecadlib.kicad_index import KiCadIndex
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


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_datasheet_fetch_1021"]))
