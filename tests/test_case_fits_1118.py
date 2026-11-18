"""A part search's case that is the symbol's own land without naming it alone (2026-11-18 hold audit, 1000-part run):
DigiKey's '8-SOIC (0.209", 5.30mm Width)' fits two KiCad lands, '24-VFQFN Exposed Pad' gives no body, '10-TFSOP, 10-MSOP
(0.118", 3.00mm Width)' is its 3 mm group. KiCad's symbol for the part (ATtiny85-20S, CP2104, INA226) names one of them.
The case still confirms the package: the same family, pin count, exposed pad, and body (the width for a leaded
package; the device package's body, '24-QFN (4x4)', for a leadless one), and no KiCad land of that description has
another pitch. Plain functions: python3 -m tests.run_all test_case_fits_1118"""
from ecadlib import librarian as L

NAMES = ["SOIC-8_5.3x5.3mm_P1.27mm", "SOIC-8_5.3x6.2mm_P1.27mm", "SOIC-8_3.9x4.9mm_P1.27mm",
         "TSSOP-10_3x3mm_P0.5mm", "MSOP-10_3x3mm_P0.5mm", "TSSOP-8_3x3mm_P0.65mm", "TSSOP-8_4.4x3mm_P0.65mm",
         "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm", "VQFN-24-1EP_4x4mm_P0.5mm_EP2.5x2.5mm_ThermalVias",
         "Texas_RTW_WQFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm_ThermalVias", "QFN-24-1EP_5x5mm_P0.65mm_EP3.2x3.2mm",
         "TSSOP-20_4.4x6.5mm_P0.65mm", "TSSOP-20_4.4x5mm_P0.5mm", "SSOP-20_3.9x8.7mm_P0.635mm",
         "SOIC-20W_7.5x12.8mm_P1.27mm", "DFN-10-1EP_2.6x2.6mm_P0.5mm_EP1.3x2.2mm", "Nordic_AQFN-73-1EP_7x7mm_P0.5mm"]


def F(case, device, land):
    return L.case_fits_land(case, device, land, NAMES)


def test_a_leaded_case_fits_the_land_of_its_family_pin_count_and_width():
    assert F('8-SOIC (0.209", 5.30mm Width)', "8-SOIC", "SOIC-8_5.3x5.3mm_P1.27mm")
    assert F('8-SOIC (0.209", 5.30mm Width)', "", "SOIC-8_5.3x6.2mm_P1.27mm")     # (the symbol says which length)
    assert not F('8-SOIC (0.209", 5.30mm Width)', "", "SOIC-8_3.9x4.9mm_P1.27mm")  # another width
    assert not F('8-SOIC (0.154", 3.90mm Width)', "", "SOIC-8_5.3x5.3mm_P1.27mm")
    assert not F('14-SOIC (0.209", 5.30mm Width)', "", "SOIC-8_5.3x5.3mm_P1.27mm")  # another pin count
    assert F('20-SOIC (0.295", 7.50mm Width)', "20-SOIC", "SOIC-20W_7.5x12.8mm_P1.27mm")
    assert F('20-SSOP (0.154", 3.90mm Width)', "20-SSOP", "SSOP-20_3.9x8.7mm_P0.635mm")
    assert F('10-TFSOP, 10-MSOP (0.118", 3.00mm Width)', "10-VSSOP", "TSSOP-10_3x3mm_P0.5mm")
    assert F('8-TSSOP, 8-MSOP (0.118", 3.00mm Width)', "8-TSSOP", "TSSOP-8_3x3mm_P0.65mm")
    assert not F('8-TSSOP (0.173", 4.40mm Width)', "", "TSSOP-8_3x3mm_P0.65mm")


def test_two_pitches_of_one_family_count_and_width_are_not_told_apart_by_the_case():
    assert not F('20-TSSOP (0.173", 4.40mm Width)', "20-TSSOP", "TSSOP-20_4.4x6.5mm_P0.65mm")
    assert not F('20-TSSOP (0.173", 4.40mm Width)', "20-TSSOP", "TSSOP-20_4.4x5mm_P0.5mm")


def test_a_leadless_case_needs_the_device_packages_body():
    assert F("24-WFQFN Exposed Pad", "24-QFN (4x4)", "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm")
    assert F("24-VFQFN Exposed Pad", "24-QFN (4x4)", "VQFN-24-1EP_4x4mm_P0.5mm_EP2.5x2.5mm_ThermalVias")
    assert F("24-WFQFN Exposed Pad", "24-WQFN (4x4)", "Texas_RTW_WQFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm_ThermalVias")
    assert F("10-XFDFN Exposed Pad", "10-DFN (2.6x2.6)", "DFN-10-1EP_2.6x2.6mm_P0.5mm_EP1.3x2.2mm")
    assert not F("24-WFQFN Exposed Pad", "", "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm")           # no body given
    assert not F("24-WFQFN Exposed Pad", "24-QFN", "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm")
    assert not F("24-WFQFN Exposed Pad", "24-QFN (5x5)", "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm")   # another body
    assert not F("24-WFQFN", "24-QFN (4x4)", "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm")            # no exposed pad said
    assert not F("32-WFQFN Exposed Pad", "32-QFN (4x4)", "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm")
    assert not F("24-TSSOP Exposed Pad", "24-QFN (4x4)", "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm")  # another family


def test_a_case_or_land_of_another_shape_fits_nothing():
    assert not F("73-VFQFN Dual Rows, Exposed Pad", "73-QFN (7x7)", "Nordic_AQFN-73-1EP_7x7mm_P0.5mm")
    assert not F("TO-261-4, TO-261AA", "SOT-223-3", "SOT-223-3_TabPin2")
    assert not F("SOT-23-6 Thin, TSOT-23-6", "", "SOT-23-6")
    assert not F("", "", "SOIC-8_5.3x5.3mm_P1.27mm") and not F('8-SOIC (0.209", 5.30mm Width)', "", "")
    assert not F('8-SOIC (0.209", 5.30mm Width), 4 Leads', "", "SOIC-8_5.3x5.3mm_P1.27mm")


# ---------------------------------------------------------------- the free check's PDF: every link, as the reader tries
class _Store:
    def __init__(self, good):
        self.good, self.asked = good, []

    def fetch(self, url, where, **kw):
        self.asked.append(url)
        if url != self.good:
            raise RuntimeError("HTTP 403")
        return b"%PDF-1.4 x", {}


def test_the_free_check_tries_the_makers_url_pattern_after_two_dead_links():
    """LMV321IDBVR: the distributor's link and the KiCad symbol's were refused, the third (TI's URL pattern) is the
    PDF. The free pin check asked for two links only and called the part's datasheet unreadable."""
    import os
    run = L.Run.__new__(L.Run)
    run._pdfs, run.http, run.reader, run.rules = _Store("https://www.ti.com/lit/ds/symlink/lmv321.pdf"), None, None, None
    item = {"parameters": [{"name": "Manufacturer Part Number", "value": "LMV321IDBVR"},
                           {"name": "Manufacturer", "value": "Texas Instruments"}]}
    row = {"src": "digikey", "src_datasheet": "https://rocelec.widen.net/view/pdf/x/slos263w.pdf",
           "src_datasheet_more": "https://www.mouser.com/datasheet/x.pdf"}
    os.environ["ECADLIB_NO_ARCHIVE"] = "1"
    try:
        data, url = run.datasheet_pdf(item, {"mpn": "LMV321IDBVR", "manufacturer": "Texas Instruments"}, row, {"found": {}}, "LMV321IDBVR")
        assert data and url == "https://www.ti.com/lit/ds/symlink/lmv321.pdf", (url, run._pdfs.asked)
        run._pdfs = _Store("nowhere")
        assert run.datasheet_pdf(item, {"mpn": "LMV321IDBVR"}, row, {"found": {}}, "LMV321IDBVR") == (None, None)
        assert 2 < len(run._pdfs.asked) <= L.Run.PDF_LINKS
    finally:
        del os.environ["ECADLIB_NO_ARCHIVE"]


def test_no_pdf_is_said_as_no_pdf_not_as_a_scanned_one():
    from ecadlib import pin_text as PT
    v = PT.check_pdf(None, [("1", "IN"), ("2", "GND")])
    assert v["status"] == "unchecked" and "gave a PDF" in v["why"], v


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("ok")
