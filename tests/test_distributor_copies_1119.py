"""Distributors' copies of a datasheet, 2026-11-19: on the 1000-part run 47 parts got no PDF because the maker's site
refused a script (Würth, Panasonic, Hirose, Richtek, ...). The official part searches (DigiKey, Mouser, Nexar) often
give a copy the distributor hosts itself (mouser.com/datasheet/..., mm.digikey.com/...); ecadlib already tried those
after the maker's link. These tests cover what was added:
- a '#' in a link's file name (Mouser's Murata copies: LQH3NPN100MJ0#.pdf) is part of the name, not a fragment;
- a distributor's copy says so where the PDF came from ("a distributor's copy, mouser.com");
- the maker's own link stays first (Oliver, 11-19: the official datasheet when it can be got, the distributor's copy
  only when it can't, and then it is used).
    python3 -m tests.test_distributor_copies_1119"""
import tempfile

from ecadlib import datasheet as D
from tests import test_datasheet_batch as TB
from tests.test_datasheet_downloads import Scripted

FILLER = " Features: low quiescent current, wide input range, thermal shutdown, small package, RoHS compliant."
MAKER = "https://www.we-online.com/catalog/datasheet/{}.pdf"
COPY = "https://www.mouser.com/datasheet/3/445/1/{}.pdf"
PLAIN = {"datasheet": {"download": "plain", "handoff": "off", "archive": False, "web_search": False}}


def pdf(mpn):
    return TB.pdf_with([f"{mpn} WE-PD SMT Power Inductor" + FILLER])


# ---------------------------------------------------------------- 1. '#' in a file name
def test_a_hash_in_a_file_name_is_part_of_the_name():
    assert D.clean_url("https://www.mouser.com/datasheet/3/76/1/LQH3NPN100MJ0#.pdf") == \
        "https://www.mouser.com/datasheet/3/76/1/LQH3NPN100MJ0%23.pdf"
    assert D.clean_url("https://x.example/LT8610#PBF.PDF") == "https://x.example/LT8610%23PBF.PDF"
    for keep in ("https://x.example/ds.pdf#page=3", "https://x.example/ds#section", "https://x.example/a.pdf"):
        assert D.clean_url(keep) == keep, keep
    row = {"src_datasheet": "https://www.mouser.com/datasheet/3/76/1/BLM18AG601SN1#.pdf", "src": "mouser"}
    assert D.datasheet_urls(None, {"mpn": "BLM18AG601SN1D"}, row)[0][0].endswith("BLM18AG601SN1%23.pdf")


# ---------------------------------------------------------------- 2. which copy it is
def test_a_distributors_copy_says_so():
    for url, host in (("https://www.mouser.com/datasheet/3/445/1/x.pdf", "mouser.com"),
                      ("https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/1/x.pdf", "digikey.com"),
                      ("https://www.lcsc.com/datasheet/C1234.pdf", "lcsc.com"),
                      ("https://datasheets.octopart.com/x.pdf", "octopart.com"),
                      ("https://www.farnell.com/datasheets/123.pdf", "farnell.com")):
        assert D.distributor_host(url) == host, url
    for url in ("https://www.we-online.com/x.pdf", "https://www.mouser.com.evil.example/x.pdf",
                "https://notmouser.com/x.pdf", "https://www.ti.com/lit/ds/symlink/tps2660.pdf", "not a link"):
        assert D.distributor_host(url) is None, url
    row = {"src": "digikey", "src_datasheet": MAKER.format("744043100"), "src_datasheet_more": COPY.format("744043100")}
    urls = D.datasheet_urls(None, {"mpn": "744043100"}, row)
    assert urls == [(MAKER.format("744043100"), "digikey datasheet link"),
                    (COPY.format("744043100"), "digikey datasheet link (another copy; a distributor's copy, mouser.com)")], urls


# ---------------------------------------------------------------- 3. the maker's own first, the copy when it can't be got
def test_the_makers_datasheet_is_read_when_it_can_be_got():
    a = "61300621121"
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({MAKER.format(a): [pdf(a)], COPY.format(a): [pdf(a)]})
        rd = D.Reader(d, d, key=TB.KEY, http=http, log=None, sleep=lambda s: None, rules=PLAIN)
        row = {"src": "digikey", "src_datasheet": MAKER.format(a), "src_datasheet_more": COPY.format(a)}
        j = rd.job(a, D.datasheet_urls(None, {"mpn": a}, row), "WE-PD", mpn=a)
        assert j["url"] == MAKER.format(a) and http.fetched == [MAKER.format(a)], http.fetched
        assert "distributor" not in j["prov"], j["prov"]


def test_a_refused_makers_link_moves_on_to_the_distributors_copy_and_says_so():
    a = "744043100"
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({MAKER.format(a): [403], COPY.format(a): [pdf(a)]})
        rd = D.Reader(d, d, key=TB.KEY, http=http, log=None, sleep=lambda s: None, rules=PLAIN)
        row = {"src": "digikey", "src_datasheet": MAKER.format(a), "src_datasheet_more": COPY.format(a)}
        j = rd.job(a, D.datasheet_urls(None, {"mpn": a}, row), "WE-PD", mpn=a)
        assert j["url"] == COPY.format(a) and "a distributor's copy, mouser.com" in j["where"], j["where"]
        assert j["prov"]["distributor"] == "mouser.com" and "HTTP 403" in j["prov"]["attempts"][0]["error"], j["prov"]
        assert http.fetched == [MAKER.format(a), COPY.format(a)], http.fetched


def test_a_distributors_copy_of_another_part_is_not_taken():
    a = "61300621121"
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({MAKER.format(a): [403], COPY.format(a): [pdf("744043470")]})
        rd = D.Reader(d, d, key=TB.KEY, http=http, log=None, sleep=lambda s: None, rules=PLAIN)
        row = {"src": "digikey", "src_datasheet": MAKER.format(a), "src_datasheet_more": COPY.format(a)}
        j = rd.job(a, D.datasheet_urls(None, {"mpn": a}, row), "WE-PD", mpn=a)
        # nothing named the part: the copy is read only as the flagged last resort, never silently as the part's own
        assert "doesn't mention 61300621121" in j["prov"].get("unnamed", ""), j["prov"]


# ---------------------------------------------------------------- 4. the assistant's request lists every link
def test_the_assistants_request_lists_the_distributors_copy_after_the_makers_link():
    """The 1000-part run (2026-11-19): a read waiting for its PDF named only the maker's link, so the download by hand
    (and the Mac's script) tried Hirose's / Richtek's refusing link and never Mouser's copy the part search gave."""
    from ecadlib import ds_assistant as DA
    from tests import test_crosscheck as TC
    a = "RT9193-33GB"
    maker, copy = "https://www.richtek.com/SaveDownload.aspx?specid=RT9193", "https://www.mouser.com/datasheet/3/5968/1/DS9193_18.pdf"
    row = {"src": "digikey", "src_datasheet": maker, "src_datasheet_more": copy}
    with tempfile.TemporaryDirectory() as d:
        rd = TC.reader(d, {"datasheet": {"reader": "assistant"}})
        rd.offline = True                              # nothing here can download either link
        job = rd.job(a, D.datasheet_urls(None, {"mpn": a}, row), "SOT-23-5", mpn=a)
        assert job["url"] == maker, job["url"]        # the maker's own link first
        DA.request(rd.assistant, job)
        (f,) = [p for p in rd.assistant.iterdir() if p.is_dir()]
        req = (f / "request.md").read_text(encoding="utf-8")
        assert maker in req and copy in req and req.index(maker) < req.index(copy), req[:1500]
        assert "a distributor's copy, mouser.com" in req, req[:1500]
        order = req[req.index("Links to try, in order"):]
        assert order.index(maker) < order.index(copy), order[:800]


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_distributor_copies_1119"]))
