"""Family datasheets (one PDF for every package of a part family, 2026-10-22): the MPN's ordering key says which
package this part is, and only that package's pages are sent. Each test is a general rule, shown on the kind of
datasheet that exposed it. The page texts are SYNTHETIC, laid out like the makers' ordering keys, not copies."""
from ecadlib import ds_route as R

# Microchip-style product identification: one letter per field, the legend lists each field's letters
MCHP_KEY = """Ordering Information
ATSAME70 Q 21 A - A N T
Product Family SAME70 = General purpose microcontroller
Pin Count J = 64 pins N = 100 pins Q = 144 pins
Flash Memory Density 21 = 2048 Kbytes 20 = 1024 Kbytes 19 = 512 Kbytes
Device Variant A = Default variant
Package Type A = LQFP C = LFBGA/TFBGA
Temperature Operating Range N = -40 to +105C
Shipping Carrier Option T = Tape and Reel"""
MCHP_TABLE = """Table 2-1. Ordering Codes
ATSAME70J19A-AN 512 64
ATSAME70N19A-AN 512 100
ATSAME70Q19A-AN 512 144
ATSAME70Q21A-CN 2048 144
ATSAME70N21A-CN 2048 100"""

# NXP Kinetis-style part number fields: a two-letter package identifier with its pin count and family
NXP_KEY = """Part identification
Format: Q KL## A FFF R T PP CC N
Field Description Values
Q Qualification status M = Fully qualified, general market flow P = Prequalification
KL## Kinetis family KL24
FFF Program flash memory size 32 = 32 KB 64 = 64 KB 128 = 128 KB 256 = 256 KB
T Temperature range (C) V = -40 to 105
PP Package identifier FM = 32 QFN (5 mm x 5 mm) FT = 48 QFN (7 mm x 7 mm) LH = 64 LQFP (10 mm x 10 mm)
LK = 80 LQFP (12 mm x 12 mm)
CC Maximum CPU frequency (MHz) 4 = 48 MHz
Example part number MKL24Z32VFM4 MKL24Z64VLH4 MKL24Z128VLK4"""


def test_a_one_letter_pin_count_is_read_where_the_family_mpns_differ():
    # "ATSAME70Q19A-AN": Q, N and A each appear in the MPN, but only position 8 (Q) and the package letter after the
    # dash differ between the family's MPNs. The N of "-AN" is the temperature range on every part, not 100 pins
    toks, why, _ = R.ordering_key([MCHP_KEY, MCHP_TABLE], "ATSAME70Q19A-AN")
    assert toks == {"QFP144"}, (toks, why)
    assert any("Q = 144 pins" in w for w in why) and any("A = LQFP" in w for w in why), why


def test_another_member_of_the_family_decodes_to_its_own_package():
    assert R.ordering_key([MCHP_KEY, MCHP_TABLE], "ATSAME70N21A-CN")[0] == {"BGA100"}
    assert R.ordering_key([MCHP_KEY, MCHP_TABLE], "ATSAME70J19A-AN")[0] == {"QFP64"}


def test_a_two_letter_package_identifier_names_family_and_pins():
    toks, why, sizes = R.ordering_key([NXP_KEY], "MKL24Z32VLK4")
    assert toks == {"QFP80"} and sizes == {"QFP80": (12.0, 12.0)}, (toks, why, sizes)
    pages, picked = R.pick_pages([NXP_KEY, "Pinout 80 LQFP"] + ["Electrical"] * 10, 4, "MKL24Z32VLK4", None)
    assert picked["_from_pdf"] == ["QFP80 12x12mm"], picked
    assert R.ordering_key([NXP_KEY], "MKL24Z32VFM4")[0] == {"QFN32"}


def test_no_key_or_no_family_siblings_decodes_nothing():
    # a single-letter code is never taken without family MPNs to show where the letters vary; a document with no
    # legend gives nothing (the ordering table and the hint still work as before)
    assert R.ordering_key(["Pin Count J = 64 pins N = 100 pins Q = 144 pins"], "ATSAME70Q19A-AN")[0] == set()
    assert R.ordering_key(["Electrical characteristics VDD = 3.3 V"], "LM358DR")[0] == set()
    assert R.ordering_key([MCHP_KEY, MCHP_TABLE], "")[0] == set()


def test_a_legend_that_matches_twice_is_ambiguous_and_dropped():
    # two package legends that both fit the MPN at a varying place and disagree: no guess
    # ("N = QFN" and an -AT family member make the N of "-AN" a varying place: N = 100 pins and N = QFN now fit too)
    texts = [MCHP_KEY + "\nPackage Option N = QFN", MCHP_TABLE + "\nATSAME70Q19A-AT 512 144"]
    assert R.ordering_key(texts, "ATSAME70Q19A-AN")[0] == set()


def _family_pages():
    return [MCHP_KEY, MCHP_TABLE,
            "5. Pin configuration 64-lead LQFP Pinout (top view) Pin Signal Name",
            "5.2 100-lead LQFP Pinout (top view) Pin Signal Name",
            "5.3 144-lead LQFP Pinout (top view) Pin Signal Name",
            "Electrical characteristics",
            "6.1 64-lead LQFP package outline dimensions mm MIN MAX A1 D1 E1",
            "6.2 100-lead LQFP package outline dimensions mm MIN MAX A1 D1 E1",
            "6.3 144-lead LQFP package outline dimensions mm MIN MAX A1 D1 E1",
            "6.4 144-ball LFBGA package outline dimensions mm MIN MAX A1 D1 E1",
            "Revision history"]


def test_with_no_package_hint_the_decoded_package_picks_its_own_pages():
    # a BOM line with no footprint: before, the picker had nothing to tell the four outlines apart and sent the first
    pages, why = R.pick_pages(_family_pages(), 5, "ATSAME70Q19A-AN", None)
    assert 9 in pages and 5 in pages, (pages, why)
    assert not {7, 8, 10} & set(pages), (pages, why)
    assert "QFP144" in why["_package"]


def test_a_hint_that_disagrees_with_the_key_is_kept_and_the_key_adds_nothing_wrong():
    # the BOM's footprint is the person's choice: both stay "own", nothing is dropped
    pages, why = R.pick_pages(_family_pages(), 6, "ATSAME70Q19A-AN", "LQFP-100")
    assert 8 in pages and 9 in pages, (pages, why)


# ---------------------------------------------------------------- the standard land once the package is known
LQFP144_READ = {"name": "LQFP-144", "package_type": "qfp", "pin_count": 144, "pitch": {"nom": 0.5},
                "terminal_length": {"min": 0.45, "max": 0.75}, "terminal_width": {"min": 0.17, "max": 0.27},
                "unresolved": ["body_length", "body_width"]}


def test_a_read_with_no_body_names_the_standard_package_the_ordering_key_decoded():
    # the read has the pitch and pin count but left the body blank (a family drawing it couldn't tie to this part);
    # the PDF's own ordering key says QFP144, and only one checked package is an LQFP-144 at 0.5 mm
    from ecadlib import stdparts as SP
    assert SP.identify(LQFP144_READ)[0] is None
    name, how = SP.identify(LQFP144_READ, known="QFP144")
    assert name == "LQFP-144_20x20mm_P0.5mm", how
    assert "ordering information names QFP144" in how


def test_the_known_package_never_overrides_what_the_read_has():
    from ecadlib import stdparts as SP
    # the read's own pitch disagrees with every LQFP-144 / the known package has another pin count: nothing
    assert SP.identify(dict(LQFP144_READ, pitch={"nom": 0.4}), known="QFP144")[0] is None
    assert SP.identify(dict(LQFP144_READ, pin_count=100), known="QFP144")[0] is None
    # two checked packages fit (no family word, no pitch): ambiguous
    amb = {k: v for k, v in LQFP144_READ.items() if k not in ("name", "pitch")}
    assert SP.identify(amb, known="QFP144")[0] is None
    # a package family the table does not map
    assert SP.identify(LQFP144_READ, known="BGA144")[0] is None


def test_a_known_package_never_picks_another_land_in_the_table():
    # every quad / no-lead / SO table entry, read back with its body left blank and its own family and pin count as
    # the ordering key's package: it names itself, an entry with the same pads, or nothing
    from ecadlib import stdparts as SP
    from ecadlib import ds_route as DR
    hits = 0
    for n in SP.packages():
        s = SP.spec(n)
        tok = next(iter(DR.package_tokens(n.split("_")[0])), None)
        if not tok or s.get("package_type") not in ("qfp", "qfn", "gullwing"):
            continue
        s = {k: v for k, v in s.items() if k not in ("body_length", "body_width")}
        got, _ = SP.identify(s, known=tok)
        assert got is None or got == n or SP._same_land([n, got]), (n, got)
        hits += got is not None
    assert hits > 20, hits


def test_the_librarian_uses_the_package_the_pdf_names():
    from types import SimpleNamespace
    from ecadlib import librarian as L
    run = SimpleNamespace(rules={"datasheet": {"standard_land": "auto"}})
    assert L.Run.standard_land(run, LQFP144_READ) is None
    name, how, why = L.Run.standard_land(run, LQFP144_READ, ["QFP144"])
    assert name == "LQFP-144_20x20mm_P0.5mm" and "body_length" in why
    assert L.Run.standard_land(run, LQFP144_READ, ["QFP144", "QFP100"]) is None   # two named: no guess


# ---------------------------------------------------------------- bookmarks that start on one page
def _bookmarked(pages, marks):
    """pdf_lines(pages) with bookmarks: marks = [(title, 0-based page, parent index or None, top in points or None)]."""
    import io, pypdf
    from pypdf.generic import Fit
    from tests.test_ds_route import pdf_lines
    w = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(pdf_lines(pages))))
    made = []
    for title, pg, parent, top in marks:
        made.append(w.add_outline_item(title, pg, parent=made[parent] if parent is not None else None,
                                       fit=Fit.xyz(top=top) if top is not None else Fit.fit()))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# SYNTHETIC, laid out like a big MCU family sheet: three bookmarks start on one page (the end of the 144-ball outlines
# and the start of the 144-lead pin table), and the next package's drawing bookmark starts half-way down a page whose
# top still holds this package's tables
SAME_MARKS = [("6. Package and Pinout", 1, None, 780), ("6.1 144-lead Packages", 1, 0, 780),
              ("6.1.1 144-pin LQFP Package Outline", 1, 1, 780), ("6.1.2 144-ball LFBGA/TFBGA", 2, 1, 760),
              ("6.1.3 144-ball UFBGA", 2, 1, 600), ("6.2 144-lead Package Pinout", 2, 0, 300),
              ("6.3 100-lead Packages", 8, 0, 780),
              ("62. Package Drawings", 10, None, 780), ("62.1 LQFP144", 10, 7, 780), ("62.2 LFBGA144", 11, 7, 350),
              ("62.3 LQFP100", 13, 7, 780)]


def _same_pages():
    pages = [["Ordering information"] for _ in range(15)]
    pages[1] = ["144-pin LQFP pin configuration (top view)"]
    pages[2] = ["144-ball BGA ball map", "Table 6-1. 144-lead pinout Pin Signal Name I/O Type"]
    for i in range(3, 8):
        pages[i] = ["Table 6-1. 144-lead pinout ...continued", "Pin Signal Name I/O Type"]
    pages[10], pages[11], pages[12], pages[13] = [], ["Table 62-1. Package weight 1.2 g JEDEC MS-026"], [], []
    return pages


def test_bookmarks_that_start_on_one_page_are_not_chained_as_one_another_s_children():
    marks = R.page_marks(_bookmarked(_same_pages(), SAME_MARKS))
    # page 3 holds the end of 6.1.1 (top), the BGA outlines and the start of 6.2: every section is on it, each with
    # its own chapter, and 6.2 is not a child of 6.1.3
    assert "6. Package and Pinout > 6.2 144-lead Package Pinout" in marks[2].split(" | "), marks[2]
    assert not any("6.1.3" in c and "6.2" in c for c in marks[2].split(" | ")), marks[2]
    assert marks[3] == "6. Package and Pinout > 6.2 144-lead Package Pinout", marks[3]
    # a bookmark half-way down a page: the page's top is still the section before it
    assert marks[11].split(" | ")[0] == "62. Package Drawings > 62.1 LQFP144", marks[11]
    assert marks[12] == "62. Package Drawings > 62.2 LFBGA144", marks[12]


def test_the_shared_pages_count_for_this_package():
    data = _bookmarked(_same_pages(), SAME_MARKS)
    texts, marks = R.page_texts(data), R.page_marks(data)
    pages, why = R.pick_pages(texts, 10, "ATSAME70Q19A-AN", "LQFP-144", marks)
    assert {11, 12} <= set(pages), (pages, why)                       # the LQFP144 drawing and its tables page
    assert {3, 4, 5} <= set(pages), (pages, why)                      # the pin table, from its first page on
    assert not {13, 14} & set(pages), (pages, why)


def test_family_mpns_with_a_tape_and_reel_suffix_still_show_where_the_key_varies():
    table = MCHP_TABLE.replace("-AN ", "-ANT ").replace("-CN ", "-CNT ")
    assert R.ordering_key([MCHP_KEY, table], "ATSAME70Q19A-AN")[0] == {"QFP144"}


def test_the_body_size_the_ordering_key_prints_names_the_land():
    # KL24: no drawing in the PDF at all; "LK = 80 LQFP (12 mm x 12 mm)" leaves one checked 80-pin LQFP (the 14 x 14 mm
    # 0.65 mm one is out), so the read's pin list can go on the maker's standard package
    from ecadlib import stdparts as SP
    read = {"name": "LQFP-80", "package_type": "qfp", "pin_count": 80, "unresolved": ["pitch", "body_length", "body_width"]}
    assert SP.identify(read, known="QFP80")[0] is None                 # 12 x 12 or 14 x 14: ambiguous
    name, how = SP.identify(read, known="QFP80 12x12mm")
    assert name == "LQFP-80_12x12mm_P0.5mm", how
    assert SP.identify(dict(read, body_length={"nom": 14.0}, body_width={"nom": 14.0}), known="QFP80 12x12mm")[0] is None


def test_a_body_size_after_the_family_is_not_its_pin_count():
    assert R.package_tokens("16 pins QFN 3x3") == {"QFN16"}
    assert R.package_tokens("64 pin LQFP 10x10") == {"QFP64"}
    assert R.package_tokens("LQFP 10x10 mm") == {"QFP"}
    assert R.package_tokens("LQFP-64 10x10") == {"QFP64"} and R.package_tokens("SOT-23") == {"SOT23"}


def test_greptile_two_bodies_for_one_package_give_no_size():
    key = NXP_KEY + "\nPP Package identifier LK = 80 LQFP (14 mm x 14 mm)"
    toks, _, sizes = R.ordering_key([key], "MKL24Z32VLK4")
    assert toks == {"QFP80"} and sizes == {}, (toks, sizes)


def test_greptile_the_read_s_exposed_pad_narrows_a_known_package():
    # VQFN-16 3x3 comes with two exposed pads; the read's 1.45 x 1.45 pad leaves one even with the body blank
    from ecadlib import stdparts as SP
    read = {"name": "VQFN-16", "package_type": "qfn", "pin_count": 16, "pitch": {"nom": 0.5},
            "exposed_pad": {"length": {"nom": 1.45}, "width": {"nom": 1.45}}, "unresolved": ["body_length", "body_width"]}
    name, how = SP.identify(read, known="QFN16")
    assert name and "1.45x1.45" in name, how


def test_greptile_pin_count_chapters_never_make_a_page_this_package_alone():
    texts = ["Ordering", "drawing page"]
    marks = ["", "6. Package Outlines > 6.1 144-lead Package Outline | 6. Package Outlines > 6.2 144-ball Package Outline"]
    rows = R.page_scores(texts, "ATSAME70Q19A-AN", "LQFP-144", marks)[0]
    assert rows[1]["own"] and not rows[1]["solo"], rows[1]


# ---------------------------------------------------------------- PC page-pick score regressions (2026-10-22)
def _ti_logic_pages():
    """SYNTHETIC, laid out like a TI logic sheet with five packages: one pinout figure for all of them, an orderable
    addendum, and one drawing per package named by its TI drawing number."""
    pages = [["CD4099B quad bilateral switch, electrical characteristics and typical curves"] for _ in range(32)]
    pages[2] = ["Figure 4-1. N, J, D, NS, or PW Packages 14-Pin PDIP, CDIP, SOIC, SOP, or TSSOP Top View",
                "Pin Functions PIN NAME NO. I/O DESCRIPTION"]
    pages[18] = ["PACKAGE OPTION ADDENDUM", "Orderable part number Status Package type Package drawing Pins",
                 "CD4099BE ACTIVE PDIP N 14 25", "CD4099BM ACTIVE SOIC D 14 50", "CD4099BNSR ACTIVE SO NS 14 2000",
                 "CD4099BPW ACTIVE TSSOP PW 14 90"]
    pages[23] = ["SOIC - 1.75 mm max heightD0014A", "PACKAGE OUTLINE SMALL OUTLINE INTEGRATED CIRCUIT"]
    pages[24] = ["D0014A SOIC - 1.75 mm max height", "EXAMPLE BOARD LAYOUT"]
    pages[26] = ["SOP - 2.00 mm max heightNS0014A", "PACKAGE OUTLINE SMALL OUTLINE PACKAGE"]
    pages[27] = ["NS0014A SOP - 2.00 mm max height", "EXAMPLE BOARD LAYOUT"]
    pages[29] = ["PW0014A TSSOP - 1.2 mm max height", "PACKAGE OUTLINE SMALL OUTLINE PACKAGE"]
    return [" \n".join(p) for p in pages]


def test_a_ti_designator_from_the_addendum_picks_that_drawing_number():
    # SOIC-14 (D) and SOP-14 (NS) are both "SO14": the addendum's designator and the drawing number tell them apart
    texts = _ti_logic_pages()
    ns, why = R.pick_pages(texts, 6, "CD4099BNS", "SO-14")
    assert 27 in ns and not {24, 25} & set(ns), (ns, why)
    m, why = R.pick_pages(texts, 6, "CD4099BM", "SO-14")
    assert 24 in m and not {27, 28} & set(m), (m, why)


def test_a_pin_page_naming_this_package_among_others_stays_this_package_s():
    # TPS26600 / CD4066B: one pin table for several packages, under a bookmark chapter; the continuation page goes too
    texts = ["Device", "x", "x", "PWP (HTSSOP, 16) RGE (VQFN, 24) Pin Configuration Top View",
             "Pin Functions PIN NAME HTSSOP VQFN TYPE DESCRIPTION OUT 15 23 Power",
             "Pin Functions (continued) PIN NAME HTSSOP VQFN TYPE DESCRIPTION EN 3 4 Input",
             "Specifications Absolute maximum ratings"] + ["Electrical"] * 20
    marks = ["", "", "", "5 Device Comparison Table | 6 Pin Configuration and Functions",
             "6 Pin Configuration and Functions", "6 Pin Configuration and Functions",
             "7 Specifications"] + ["7 Specifications"] * 20
    pages, why = R.pick_pages(texts, 6, "TPS26600PWP", "HTSSOP-16-1EP", marks)
    assert {4, 5, 6} <= set(pages), (pages, why)


def test_a_temperature_range_is_no_to_package():
    assert R.package_tokens("LTC2942CDCB-1#PBF -40°C TO 85°C") == set()
    assert R.package_tokens("LTC2942IDCB-1 –40°C to 85°C 6-Lead (2mm × 3mm) Plastic DFN") <= {"SON", "SON6"}


def test_a_part_number_is_no_ti_drawing_number():
    assert [m.group(1) for m in R._DWG_ID.finditer("UCC1895J UCC2895DW D0020A heightPW0014A")] == ["D", "PW"]


def test_a_powerpad_land_given_in_prose_counts_as_land():
    # OPA569DWP: no land drawing, only "FIGURE 16. 20-Pin DWP PowerPAD PCB Etch and Via Pattern" with sizes in prose
    texts = ["OPA569 power amplifier"] * 3 + ["Applications: the thermal land under the device carries heat away"] + \
        ["FIGURE 16. 20-Pin DWP PowerPAD PCB Etch and Via Pattern", "Thermal pad area: 140 mils x 176 mils (3.56mm x "
         "4.47mm) with 24 vias. Thermal Land 299 mils x 510 mils Minimum Area (7.59mm x 12.95mm) (Copper)"]
    texts = [texts[0], texts[1], texts[2], texts[3], " \n".join(texts[4:])] + ["Electrical characteristics"] * 20
    rows = R.page_scores(texts, "OPA569DWP", "SOIC-20")[0]
    assert rows[4]["land"] > 0 and rows[3]["land"] == 0, (rows[3], rows[4])
    # the prose alone, without the "Etch and Via Pattern" caption, still counts
    for prose in ("Thermal pad area: 140 mils x 176 mils (3.56mm x 4.47mm) with 24 vias.",
                  "Thermal Land 299 mils x 510 mils Minimum Area (7.59mm x 12.95mm) (Copper)"):
        rows = R.page_scores(texts[:4] + [prose] + texts[5:], "OPA569DWP", "SOIC-20")[0]
        assert rows[4]["land"] > 0, (prose, rows[4])
