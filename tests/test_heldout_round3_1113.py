"""Held-out test round 3 (2026-11-13): tied image-only drawings, a pin table longer than the page budget, and the
housing outline asked for in a connector's task text."""
from ecadlib import datasheet as D, ds_route as DR

FILL = "Electrical characteristics. Supply current 10 uA typical over the full temperature range of the device."


def _pdf(pages, n):
    out = [FILL] * n
    for i, t in pages.items():
        out[i - 1] = t
    return out


def test_tied_image_only_drawings_are_all_sent():
    # AM26LS31CD: N, D, W and J drawings at the back with no text layer, text drawings of other packages between
    pages = {3: "AM26LS31 Pin Configuration (TOP VIEW) D, N or NS package", 17: "PACKAGE OPTION ADDENDUM AM26LS31CD "
             "ACTIVE SOIC D 16", 24: "", 25: "PACKAGE OUTLINE TSSOP-16 Min Max mm A1 D1 E1", 28: "",
             29: "PACKAGE OUTLINE SSOP-16 Min Max mm A1 D1 E1", 32: "", 34: "",   # text drawings: other packages
             4: "Pin Functions PIN NO. NAME TYPE", 5: "Device comparison ordering information",
             6: "Pin descriptions pin name", 18: "PACKAGE OPTION ADDENDUM orderable"}   # pages that fill the budget
    texts = _pdf(pages, 35)
    rows = DR.page_scores(texts, "AM26LS31CD", "SOIC-16_3.9x9.9mm_P1.27mm")[0]
    tied = [i + 1 for i in (23, 27, 31, 33) if rows[i]["drawing"]]
    got, why = DR.pick_pages(texts, 6, "AM26LS31CD", "SOIC-16_3.9x9.9mm_P1.27mm")
    assert set(tied) <= set(got), (tied, got, why)
    assert 28 in got


def test_one_image_drawing_is_not_padded():
    texts = _pdf({3: "Pin Configuration (TOP VIEW)", 20: "Package outline Min Max mm A1 D1 E1 SOIC-8", 28: ""}, 30)
    got, why = DR.pick_pages(texts, 6, "XYZ123", "SOIC-8_3.9x4.9mm_P1.27mm")
    assert not any("tied" in " ".join(why.get(p, [])) for p in got)


SIG = ("7.2 Signal Descriptions Table 7-1 describes the signals. Table 7-1. Signal Descriptions TERMINAL I/O(1) "
       "DESCRIPTION NAME NO.(4) PZ ZCA, ZQW P6.4/CB4/A4 1 A1 I/O General-purpose digital I/O")
SIG_C = "Table 7-1. Signal Descriptions (continued) TERMINAL I/O(1) DESCRIPTION NAME NO.(4) PZ ZCA, ZQW P1.0 21 J2 I/O"
DWG = "PACKAGE OUTLINE ZCA0113A NFBGA - 1 mm max height BALL GRID ARRAY Min Max mm A1 D1 E1 BGA-113"


def test_a_continued_pin_table_goes_in_whole_past_the_budget():
    pages = {11: SIG, **{p: SIG_C for p in range(12, 18)}, 115: DWG, 116: DWG}
    texts = _pdf(pages, 121)
    got, why = DR.pick_pages(texts, 6, "MSP430F5333IZQW", "BGA-113")
    assert set(range(11, 18)) <= set(got), (got, why)
    assert 115 in got
    assert len(got) <= 6 + DR.PIN_TABLE_EXTRA


def test_connector_task_text_asks_for_the_housing_outline():
    assert "pad_table.body" in D.task_text("B20B-PUDSS", "Connector_JST:JST_PUD_B20B-PUDSS_2x10_P2.00mm_Vertical")
    assert "pad_table.body" in D.task_text("X", "PinHeader_2x05_P2.54mm_Vertical")
    assert "pad_table.body" not in D.task_text("LM358", "SOIC-8_3.9x4.9mm_P1.27mm")
    assert "pad_table.body" not in D.task_text("LM358")
