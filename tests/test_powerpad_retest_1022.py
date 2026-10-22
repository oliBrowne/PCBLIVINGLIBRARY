"""The $3 live read of the two TI PowerPAD parts (2026-10-22) and the three general faults it showed:
TLC5951DAP read its land right (5.2 x 12.5 copper, 3.39 x 6.35 mask) but named the part by the mask opening, and the
self-check refused EP3.39x6.35 against the drawing's 5.05-6.35 x 2.69-3.39 midpoint; OPA569DWP's land page sizes only
the thermal land, so the read left the land out; the 22-page retry lost the land pages the 6-page try had sent."""
from ecadlib import datasheet as D, ds_route as DR, footprint_bench as B, footprint_gen as F
from ecadlib.eval import score as S

TLC = {"name": "HTSSOP-38-1EP_6.1x12.5mm_P0.65mm_EP3.39x6.35mm", "package_type": "gullwing", "pin_count": 38,
       "pitch": 0.65, "lead_span": {"min": 7.9, "max": 8.3}, "body_width": {"min": 6.0, "max": 6.2},
       "body_length": {"min": 12.4, "max": 12.6}, "terminal_width": {"min": 0.19, "max": 0.30},
       "terminal_length": {"min": 0.50, "max": 0.75},
       "exposed_pad": {"length": {"min": 5.05, "max": 6.35}, "width": {"min": 2.69, "max": 3.39}}}
TLC_LAND = {"pad_length": 1.8, "pad_width": 0.3, "pad_center": 3.7, "ep_across": 5.2, "ep_along": 12.5,
            "ep_mask_across": 3.39, "ep_mask_along": 6.35}
# OPA569DWP: the drawing as the live read gave it; p19 sizes only the thermal land
OPA = {"name": "SOIC-20-1EP_7.5x12.8mm_P1.27mm_EP3.3x3.9mm", "package_type": "gullwing", "pin_count": 20,
       "pitch": 1.27, "lead_span": {"min": 10.16, "max": 10.65}, "body_width": {"min": 7.45, "max": 7.59},
       "body_length": {"min": 12.70, "max": 12.95}, "terminal_width": {"min": 0.35, "max": 0.51},
       "terminal_length": {"min": 0.40, "max": 1.27},
       "exposed_pad": {"length": {"min": 3.49, "max": 4.32}, "width": {"min": 2.72, "max": 3.81}}}


def _ep_lines(spec):
    review = []
    D.self_check(spec, {"pins": []}, review)
    return [r for r in review if "name says EP" in r]


# ---------------------------------------------------------------- 1. the name's EP against the land page
def test_a_name_ep_that_is_the_lands_mask_opening_passes_the_self_check():
    assert _ep_lines(dict(TLC, land=TLC_LAND)) == []


def test_a_name_ep_that_is_the_lands_copper_passes_the_self_check():
    assert _ep_lines(dict(TLC, name="HTSSOP-38-1EP_6.1x12.5mm_P0.65mm_EP5.2x12.5mm", land=TLC_LAND)) == []


def test_a_name_ep_inside_the_drawings_range_passes_either_way_round():
    assert _ep_lines(dict(TLC)) == []                                        # 3.39 x 6.35: both range maxima
    assert _ep_lines(dict(TLC, name="HTSSOP-38-1EP_6.1x12.5mm_P0.65mm_EP6x3mm")) == []


def test_a_name_ep_that_neither_the_drawing_nor_the_land_gives_is_still_caught():
    bad = _ep_lines(dict(TLC, name="HTSSOP-38-1EP_6.1x12.5mm_P0.65mm_EP4.5x8mm", land=TLC_LAND))
    assert bad and "EP 4.5x8" in bad[0]
    # one side in range is not enough: both must fit, the same way round
    assert _ep_lines(dict(TLC, name="HTSSOP-38-1EP_6.1x12.5mm_P0.65mm_EP3x8mm"))


def test_tlc5951_as_read_builds_the_land_kicad_draws():
    text, rep = F.build(dict(TLC, land=TLC_LAND))
    assert not F.shorts(rep)
    assert rep["ep_mask"] == (3.39, 6.35) and rep["EP"][:2] == (5.2, 12.5)


# ---------------------------------------------------------------- 2. a land of the thermal pad only
def test_a_thermal_only_land_passes_the_schema_and_keeps_ipc_signal_pads():
    land = {"ep_across": 5.2, "ep_along": 12.5, "ep_mask_across": 3.39, "ep_mask_along": 6.35}
    spec = dict(TLC, land=land)
    review = []
    assert D.schema_check(spec, review), review
    ipc, _ = F.build(dict(TLC))
    text, rep = F.build(spec)
    sig = lambda t: [p for p in B.numbers(t)["pads"] if p[0] not in ("39", "")]
    assert sig(text) == sig(ipc)                                             # the signal pads are IPC-7351B's
    assert "land_source" not in rep and rep["EP"][:2] == (5.2, 12.5) and rep["ep_mask"] == (3.39, 6.35)
    assert F.lead_land(spec) is False and F.lead_land(dict(TLC, land=TLC_LAND)) is True


def test_a_thermal_only_land_is_not_held_as_a_missing_thermal_land():
    spec = dict(OPA, land={"ep_across": 7.59, "ep_along": 12.95, "ep_mask_across": 3.56, "ep_mask_along": 4.47},
                source={"datasheet": "http://www.ti.com/lit/ds/symlink/opa569.pdf"})
    assert D.thermal_land_missing(spec) is None
    assert D.thermal_land_missing(dict(spec, land=None))                     # no land: still held, as before


def test_opa569_minimum_copper_that_hits_the_leads_is_held_with_the_reason():
    # TI's minimum copper is as wide as the body, so the feet's heels reach it: held, never trimmed by guess (KiCad's
    # own 6.045 x 12.09 is its choice, not the datasheet's)
    spec = dict(OPA, land={"ep_across": 7.59, "ep_along": 12.95, "ep_mask_across": 3.56, "ep_mask_along": 4.47})
    _, rep = F.build(spec)
    why = F.shorts(rep)
    assert why and "trimmed clear of the leads by hand" in why


def test_a_part_land_is_flagged_and_an_empty_land_too():
    for land, want in (({"pad_length": 1.8, "pad_width": 0.3}, "but not pad_center"),
                       ({"ep_mask_across": 3.39, "ep_mask_along": 6.35}, "neither the signal pads")):
        review = []
        D.sanity(dict(TLC, land=land), review)
        assert any(want in r for r in review), review
    review = []
    D.sanity(dict(TLC, land={"ep_across": 5.2, "ep_along": 12.5}), review)
    assert not any("land gives" in r for r in review), review


def test_the_scorer_counts_a_thermal_only_land_by_its_thermal_pad():
    spec = dict(TLC, land={"ep_across": 5.2, "ep_along": 12.5})
    text, _ = F.build(spec)
    ml = S.maker_land(B.numbers(text), spec)
    assert ml["thermal_only"] and ml["followed"] and ml["ep_land"] == ["39"] and ml["pads"] == []
    other = dict(spec, land={"ep_across": 4.0, "ep_along": 9.0})            # an EP that is not the land's
    assert S.maker_land(B.numbers(text), other)["followed"] is False
    assert S.maker_land(B.numbers(text), dict(spec, land={})) is None


# ---------------------------------------------------------------- 3. a wider try keeps the narrower one's pages
def _doc():
    pages = ["TLC5951 24-channel LED driver. Features. Description."] + ["Electrical characteristics typical "
                                                                         "performance curves."] * 47
    pages += ["PACKAGE OUTLINE DAP (R-PDSO-G38) PowerPAD plastic small outline 38 pins. All linear dimensions are "
              "in millimeters. 12,60 12,40 6,20 6,00 0,65 0,30 0,19 8,30 7,90 1,20 MAX",
              "THERMAL PAD MECHANICAL DATA DAP (R-PDSO-G38) Exposed Thermal Pad Dimensions 6,35 5,05 3,39 2,69",
              "", "LAND PATTERN DATA DAP (R-PDSO-G38) solder mask defined pad 38x 0,30 1,80 0,65 5,20 12,50"]
    pages += ["PACKAGE OPTION ADDENDUM orderable device TLC5951DAP TLC5951DAPR"] * 2
    pages += ["Pin configuration and functions DAP package 38 pins top view. Pin functions table name no. I/O "
              "description"] + ["Application information. Detailed design procedure."] * 10
    return pages


def test_pick_pages_puts_the_given_pages_in_first():
    got, why = DR.pick_pages(_doc(), 6, "TLC5951DAP", "HTSSOP-38", first=[3, 49])
    assert {3, 49} <= set(got) and len(got) <= 6
    assert "sent on the narrower try" in why[3]


def test_every_wider_level_keeps_the_pages_of_the_one_before():
    lv, _ = DR.levels(_doc(), 4, "TLC5951DAP", "HTSSOP-38", len(_doc()))
    lists = [L for L in lv if isinstance(L, list)]
    assert lists and lv[-1] is None
    for a, b in zip(lists, lists[1:]):
        assert set(a) <= set(b), (a, b)


def _blank_pdf(n):
    import io
    import pypdf
    w = pypdf.PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_a_whole_pdf_retry_cut_to_the_limit_keeps_the_pages_the_first_try_sent():
    # the last level is the whole PDF; over the page limit, prepare() cuts it with its own text picker, which finds
    # nothing on image-only pages: the earlier try's pages go in first
    data = _blank_pdf(12)
    sent, offset, _, note = D.prepare(data, None, 5, 100, first=[9, 10, 11])
    assert offset == [9, 10, 11] and D.pdf_pages(sent) == 3, note
    sent, offset, _, _ = D.prepare(data, None, 2, 100, first=[9, 10, 11])
    assert offset == [9, 10]                                                 # never past the limit
    assert D.prepare(data, None, 20, 100, first=[9])[1] == 0                 # within the limit: the whole PDF


# ---------------------------------------------------------------- review of the fixes (independent review, 10-22)
def test_a_thermal_land_smaller_than_the_exposed_pad_fails_the_read_and_is_never_scored_right():
    from ecadlib import ds_route as R
    spec = dict(TLC, land={"ep_across": 4.4, "ep_along": 3.1})              # under the pad's 5.05 x 2.69 minimum
    review = []
    D.sanity(spec, review)
    bad = [r for r in review if "smaller than the exposed pad" in r]
    assert bad and R.FAILED.search(bad[0])
    text, _ = F.build(spec)
    ml = S.maker_land(B.numbers(text), spec)
    assert ml["followed"] is False and ml["ep_land"] == [] and ml["implausible"]
    review = []
    D.sanity(dict(TLC, land=TLC_LAND), review)                             # 5.2 x 12.5 around it: fine
    assert not any("smaller than the exposed pad" in r for r in review)


def test_a_part_land_fails_the_read_so_the_route_retries_it():
    from ecadlib import ds_route as R
    for land in ({"pad_length": 1.8, "pad_width": 0.3}, {"ep_mask_across": 3.39, "ep_mask_along": 6.35}, {}):
        review = []
        D.sanity(dict(TLC, land=land), review)
        assert any(R.FAILED.search(r) for r in review if "land gives" in r), review


def test_a_name_matching_the_mask_alone_does_not_excuse_a_misread_pad():
    # no thermal copper in the land: the drawing's pad is built, so the name must agree with the drawing
    spec = dict(TLC, exposed_pad={"length": {"nom": 7.0}, "width": {"nom": 4.0}},
                land={"ep_mask_across": 3.39, "ep_mask_along": 6.35})
    assert _ep_lines(spec)


def test_the_whole_pdf_cut_keeps_the_first_trys_pages_before_later_ones():
    data = _blank_pdf(60)
    first = [49, 50, 51, 52] + list(range(1, 9))                             # level 0's pages, then the wider level's
    _, offset, _, _ = D.prepare(data, None, 6, 100, first=first)
    assert offset == [1, 2, 49, 50, 51, 52]


def test_a_mask_the_builder_discards_does_not_excuse_the_name():
    # Greptile on #169: a 6 x 13 mask cannot open on 5.2 x 12.5 copper, so the whole land is open and a name EP6x13
    # agrees with nothing that is built
    land = {"ep_across": 5.2, "ep_along": 12.5, "ep_mask_across": 6.0, "ep_mask_along": 13.0}
    assert _ep_lines(dict(TLC, name="HTSSOP-38-1EP_6.1x12.5mm_P0.65mm_EP6x13mm", land=land))
    turned = dict(TLC_LAND, ep_mask_across=6.35, ep_mask_along=3.39)      # read the other way round: turned, drawn
    assert _ep_lines(dict(TLC, land=turned)) == []
