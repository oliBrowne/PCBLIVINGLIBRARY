"""--cached-only, 2026-10-22: an answer saved from another step of a part's route is scored with the pages that read
was sent, not the pages this run's picker would send. TLC5951DAP's 10-21 answer read pages 4,5,15,39,49,50 (zoom
49,50); the re-score's sidecar said "picked for this part: 4-5,39,49-51" and zoom 49-51, naming a land page (51) the
scored read never saw.   python3 -m tests.test_eval_saved_pages_1022"""
from ecadlib.eval import runner as R

TODAY = {"url": "https://www.ti.com/lit/ds/tlc5951.pdf", "sha256": "ab", "sent_as": "base64", "sent_bytes": 9,
         "pages_sent": [4, 5, 39, 49, 50, 51], "zoom_pages": [49, 50, 51], "picked_why": {51: ["land"]},
         "note": "6 of 56 pages sent (picked for this part: 4-5,39,49-51; first try)"}


def test_the_saved_reads_own_pages_replace_todays_pick():
    p = R.saved_prov(TODAY, [4, 5, 15, 39, 49, 50])
    assert p["pages_sent"] == [4, 5, 15, 39, 49, 50] and "4-5,15,39,49-50" in p["note"] and "51" not in p["note"], p
    assert "zoom_pages" not in p and "picked_why" not in p and "picked for this part" not in p["note"]
    assert p["url"] == TODAY["url"] and p["sha256"] == "ab"                 # the PDF itself is the same one
    assert TODAY["pages_sent"] == [4, 5, 39, 49, 50, 51]                     # the job's own record is not changed


def test_a_saved_read_with_no_page_list_claims_none():
    for off in (0, None):
        p = R.saved_prov(TODAY, off)
        assert "pages_sent" not in p and "picked for this part" not in p["note"] and "no page list" in p["note"], p
    assert "from 13" in R.saved_prov(TODAY, 12)["note"]


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
