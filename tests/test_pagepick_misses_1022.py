"""The package drawings the page picker still missed on the 46 hand-labelled family datasheets (2026-10-22, PR #164's
free re-score). Each test is a general rule, shown on the kind of datasheet that exposed it. The page texts are
SYNTHETIC, laid out like the makers' pages, not copies."""
from ecadlib import ds_route as R

FILL = "Electrical characteristics VDD supply current typical conditions"


def _pages(n, **at):
    t = [FILL] * n
    for k, v in at.items():
        t[int(k[1:])] = v
    return t


# ---------------------------------------------------------------- package names the tokens did not know
def test_package_names_that_used_to_give_no_token():
    assert R.package_tokens("µTFBGA36") == {"BGA36"} == R.package_tokens("uTFBGA-36")      # ST STULPI01A
    assert R.package_tokens("DSBGA-6") == {"CSP6"} and R.package_tokens("YZP (DSBGA)") == {"CSP"}   # TI WCSP
    assert R.package_tokens("256-Pin Micro FineLine BGA") == {"BGA256"}                      # Altera MAX II
    assert R.package_tokens("100-Pin FineLine BGA") == {"BGA100"}
    assert R.package_tokens("14 LEAD PLASTIC SOIC") == {"SO14"}
    assert R.package_tokens("8 pins and 14 pins SOIC") == {"SO14"}                             # "and": not a name
    assert R.package_tokens("48-pin TQFP and 64-pin LQFP") == {"QFP48", "QFP64"}


def test_dpak_names_its_outline_so_the_lead_count_is_not_glued_on():
    # AUIPS6011R "Case Outline DPAK - 5 Leads" read as TO2525, so it never matched the TO-252 hint
    assert R.package_tokens("Case Outline DPAK - 5 Leads") == {"TO252"} == R.package_tokens("TO-252-4")
    assert R.package_tokens("D2PAK-7") == {"TO263"}


# ---------------------------------------------------------------- drawings with almost no text layer
def test_a_rohm_image_drawing_captioned_only_with_its_package_name_is_a_drawing():
    # BD33FC0FP p35: the drawing is an image, the only text is "Package Name TO252-3"
    texts = _pages(40, p34="Package Name TO252-3", p20="Ordering information BD33FC0FP TO252-3 reel 2000")
    rows = R.page_scores(texts, "BD33FC0FP", "TO-252-3")[0]
    assert rows[34]["drawing"] > 0, rows[34]
    pages, why = R.pick_pages(texts, 6, "BD33FC0FP", "TO-252-3")
    assert 35 in pages and "drawing" in why[35], (pages, why)
    # a short page naming only a family ("LQFP Package") is not taken for a drawing by this rule
    assert R.page_scores(_pages(5, p2="LQFP Package"), "X", "LQFP-48")[0][2]["drawing"] == 0


def test_ti_dsbga_size_box_is_a_drawing():
    # BQ51051BYFP p43: old "MECHANICAL DATA" image; the text layer is only the die-size box
    box = "D: Max = 3.039 mm, Min = 2.979 mm E: Max = 1.919 mm, Min = 1.859 mm"
    pages, why = R.pick_pages(_pages(50, p42=box), 6, "BQ51051BYFPR", "DSBGA-28")
    assert 43 in pages and "drawing" in why[43], (pages, why)
    assert R.page_scores(_pages(5, p1="Supply D: maximum 5 V E: max 3 V"), "X", "DSBGA-28")[0][1]["drawing"] == 0


# ---------------------------------------------------------------- whose package a page is about
def test_a_package_code_in_the_family_name_is_not_a_package_code():
    # STULPI01A: "ULPI" is the family's name, so every ULPI page counted as this package's
    toks, codes = R.ordering_codes(["Order code STULPI01A uTFBGA36 tape and reel"], "STULPI01A")
    assert "ULPI" not in codes and toks == {"BGA36"}, (toks, codes)
    toks, codes = R.ordering_codes(["BQ771800DPJR ACTIVE WSON DPJ 8 3000"], "BQ771800DPJR")   # after the digits it still is
    assert "DPJ" in codes


def test_a_code_on_the_parts_ordering_line_is_its_ti_designator():
    # TXB0101YZP: YZP is on its ordering line; p34's drawing number YZP0006 is this package, DCK0006A another
    texts = _pages(40, p5="ORDERING INFORMATION TXB0101YZPR YZP 2D 3000",
                   p33="MECHANICAL DATA DCK0006A min max A1 D1 E1 mm", p34="MECHANICAL DATA heightYZP0006A min max A1 mm")
    rows = R.page_scores(texts, "TXB0101YZPR", "DSBGA-6")[0]
    assert rows[34]["own"] and not rows[34]["other"] and rows[33]["other"], (rows[33], rows[34])


def test_a_ti_drawing_numbers_pin_count_decides_when_no_designator_is_known():
    # 16550: N0040A is the 40-pin DIP asked for, FN0044A (the PLCC's board layout) another package
    texts = _pages(40, p30="MECHANICAL DATA N0040A PLASTIC DUAL-IN-LINE min max",
                   p33="FN0044A board layout example", p20="Package outline see N0040A")
    rows = R.page_scores(texts, "TL16C550", "DIP-40")[0]
    assert rows[30]["own"] and rows[33]["other"], (rows[30], rows[33])
    pages, why = R.pick_pages(texts, 6, "TL16C550", "DIP-40")
    assert 31 in pages and 34 not in pages, (pages, why)


def test_an_ordering_line_naming_another_package_does_not_outvote_the_hint():
    # AUIPS6011R: a TO220 on the ordering lines made the TO220 outline "own" next to the DPAK asked for
    texts = _pages(20, p1="Ordering information AUIPS6011R DPAK-5 tape; AUIPS6011R also see TO220 tube",
                   p9="Case Outline - TO220 min max A1 D1 E1 mm", p12="Case Outline DPAK - 5 Leads min max A1 D1 E1 mm")
    rows, own = R.page_scores(texts, "AUIPS6011R", "TO-252-4")[:2]
    assert "TO220" not in own and rows[9]["other"] and rows[12]["own"], (own, rows[9], rows[12])
    pages, why = R.pick_pages(texts, 6, "AUIPS6011R", "TO-252-4")
    assert 13 in pages and "drawing" in why[13] and 10 not in pages, (pages, why)
    assert R._agree(set(), {"TO220"}) == {"TO220"}                    # no hint: the PDF decides
    assert R._agree({"QFP"}, {"QFP144", "BGA144"}) == {"QFP144"}      # a family hint: the PDF's count is kept


def test_altera_ordering_code_names_the_bga_and_its_pin_count():
    # EPM1270M256: M = Micro FineLine BGA, then the pin count in the MPN; the 100-pin FineLine BGA pages are another
    texts = _pages(130, p0="MAX II ordering EPM1270F256C5 EPM1270T144C5 EPM1270M256C5",
                   p1="Package type T = TQFP F = FineLine BGA M = Micro FineLine BGA",
                   p111="100-Pin FineLine BGA package outline dimensions min max A1 D1 E1 mm",
                   p116="256-Pin Micro FineLine BGA package outline dimensions min max A1 D1 E1 mm")
    assert R.ordering_key(texts, "EPM1270M256C5")[0] == {"BGA256"}
    pages, why = R.pick_pages(texts, 6, "EPM1270M256C5", "FBGA-256")
    assert 117 in pages and "drawing" in why[117] and 112 not in pages, (pages, why)


def test_an_adi_drawing_sharing_its_page_with_the_ordering_guide_is_a_drawing():
    # ADAU1702 p52: "OUTLINE DIMENSIONS" over the LQFP drawing, then the ORDERING GUIDE with a "13" Tape and Reel" row;
    # that row made the whole page a tape-and-reel page, so the drawing scored 0
    p52 = ("ADAU1702 Data Sheet Page 52 of 52\nOUTLINE DIMENSIONS\nCOMPLIANT TO JEDEC STANDARDS MS-026-BBC\nTOP VIEW "
           "(PINS DOWN)\n0.27 0.22 0.17 0.50 BSC LEAD PITCH 1.60 MAX 9.20 9.00 SQ 8.80 7.20 7.00 SQ 6.80\n" + FILL * 3 +
           "\nFigure 40. 48-Lead Low-Profile Quad Flat Package [LQFP] (ST-48) Dimensions shown in millimeters\n"
           "ORDERING GUIDE\nModel Temperature Range Package Description Package Option\nADAU1702JSTZ 0C to +70C "
           "48-Lead LQFP ST-48\nADAU1702JSTZ-RL 0C to +70C 48-Lead LQFP in 13\" Tape and Reel ST-48")
    texts = _pages(52, p51=p52, p41="Register map pin control bits pin configuration", p1="Pin configuration LQFP-48 top view")
    assert R.page_scores(texts, "ADAU1702JSTZ", "LQFP-48")[0][51]["drawing"] > 0
    pages, why = R.pick_pages(texts, 6, "ADAU1702JSTZ", "LQFP-48")
    assert 52 in pages and "drawing" in why[52], (pages, why)
    # a page that is about tape and reel still counts as one
    reel = "TAPE AND REEL INFORMATION\nReel diameter A0 B0 K0 package outline drawing min max A1 D1 E1 mm"
    assert R.page_scores(_pages(5, p2=reel), "X", "LQFP-48")[0][2]["drawing"] < 3


def test_powerpad_land_prose_needs_a_size_and_wins_at_a_small_budget():
    # #164 review: layout advice ("connect the thermal land ... 0.3 mm vias") is not a land page; a label and its
    # size on separate lines (pdfplumber figure callouts) still are
    advice = "Layout guidelines: connect the thermal land to the ground plane with 0.3 mm vias, 1.2 mm pitch."
    land = "FIGURE 16. 20-Pin DWP PowerPAD PCB\nThermal Land\n299 mils x 510 mils Minimum Area"
    texts = _pages(21, p12=advice, p19=land, p6="Pin configuration SOIC-20 top view")
    rows = R.page_scores(texts, "OPA569DWP", "SOIC-20")[0]
    assert rows[12]["land"] == 0 and rows[19]["land"] > 0, (rows[12], rows[19])
    for b in (3, 4, 5):
        pages, why = R.pick_pages(texts, b, "OPA569DWP", "SOIC-20")
        assert 20 in pages and 13 not in pages, (b, pages, why)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
