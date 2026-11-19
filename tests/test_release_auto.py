"""Fewer parts waiting on a person (2026-11-16, the 100-part real BOM: WHY_44_HELD.md). Every part released here is
released only on an independent second source agreeing; anything unsure keeps its hold.
Plain functions: python3 -m tests.run_all test_release_auto"""
from ecadlib import pin_text as PT, release_check as RC, rules as RU


# ---------------------------------------------------------------- 3D: a note, never a hold by itself
def test_a_3d_model_on_one_side_only_is_a_note_not_a_hold():
    assert not RC.parity_holds("3D model presence: KiCad ${KICAD10_3DMODEL_DIR}/X.step / Altium none")
    assert RC.parity_holds("pad 1 size: KiCad 1.0 x 0.6 / Altium 1.1 x 0.6")
    assert RC.parity_holds("courtyard extent: KiCad (-1, -1, 1, 1) / Altium (-2, -1, 1, 1)")


def test_allow_missing_3d_is_off_by_default_and_must_be_a_boolean():
    assert RU.DEFAULTS["release"]["allow_missing_3d"] is False
    assert not RC.allow_missing_3d(RU.DEFAULTS)
    assert RC.allow_missing_3d({"release": {"allow_missing_3d": True}})
    assert not RC.allow_missing_3d({"release": {"allow_missing_3d": "yes"}})
    errs = []
    try:
        RU.validate({"release": {"allow_missing_3d": "yes"}}, "ecadlib.toml")
    except RU.RulesError as e:
        errs.append(str(e))
    assert errs and "allow_missing_3d" in errs[0], errs


# ---------------------------------------------------------------- pins against the PDF's own text layer
TABLE = """Table 5-1. Pin Functions
PIN NAME SOIC SOT-23 TYPE DESCRIPTION
OUT 6, 7, 8 1 O Power-switch output
GND 1 2 — Ground
IN 2, 3 5 I Input voltage
OC 5 3 O Overcurrent open-drain output
EN 4 4 I Enable input"""


ONE = """Table 5-1. Pin Functions
NAME NUMBER DESCRIPTION
GND 1 Ground pin. Connect sensitive VFB to this GND at a single point.
SW 2 Switch node connection between high-side NFET and low-side NFET.
VIN 3 Input voltage supply pin.
VFB 4 Converter feedback input. Connect to output voltage with feedback resistor divider.
EN 5 Enable input control. Active high and must be pulled up to enable the device.
VBST 6 Supply input for the high-side NFET gate drive circuit."""


def test_pins_agree_with_a_one_package_table_of_the_text_layer():
    pins = [("1", "GND"), ("2", "SW"), ("3", "VIN"), ("4", "VFB"), ("5", "EN"), ("6", "VBST")]
    r = PT.check({4: ONE}, pins)
    assert r["status"] == "agreed" and r["page"] == 4 and r["column"] == 0, r    # numbers in the descriptions ignored


def test_a_table_whose_other_column_numbers_the_pins_otherwise_does_not_agree():
    """SOIC and SOT-23 columns (or MCP6L01 / MCP6L01R): one column fits, but the text does not say which column is
    this part's, so it stays with a person. Only a column that prints a pin number above the symbol's highest is
    plainly another package (the 8-pin SOIC column for the 5-pin SOT-23 symbol): that one is no doubt."""
    sot = [("1", "OUT"), ("2", "GND"), ("5", "IN"), ("3", "~{OC}"), ("4", "EN")]
    soic = [("6", "OUT"), ("7", "OUT"), ("8", "OUT"), ("1", "GND"), ("2", "IN"), ("3", "IN"), ("5", "~{OC}"), ("4", "EN")]
    r = PT.check({4: TABLE}, soic)                                       # the SOT-23 column could be an 8-pin part's
    assert r["status"] == "unchecked" and "another column" in r["why"], r
    r = PT.check({4: TABLE}, sot)
    assert r["status"] == "agreed" and r["column"] == 1, r               # pins 6, 7, 8 are not a 5-pin part's
    five = TABLE.replace("OUT 6, 7, 8 1", "OUT 5 1").replace("IN 2, 3 5", "IN 3 5").replace("OC 5 3", "OC 2 3") \
        .replace("GND 1 2", "GND 1 2")
    r = PT.check({4: five}, sot)                                         # two 5-pin columns: which is this part's?
    assert r["status"] == "unchecked" and "another column" in r["why"], r
    same = TABLE.replace("GND 1 2", "GND 2 2").replace("OUT 6, 7, 8 1", "OUT 1 1").replace("IN 2, 3 5", "IN 5 5") \
        .replace("OC 5 3", "OC 3 3")
    assert PT.check({4: same}, sot)["status"] == "agreed"                # every column gives the same numbers


def test_a_mix_of_two_package_columns_is_not_agreed():
    mixed = [("1", "OUT"), ("1", "GND"), ("5", "IN"), ("3", "OC"), ("4", "EN")]   # GND from the SOIC column
    assert PT.check({4: TABLE}, mixed)["status"] != "agreed"


def test_swapped_pins_are_never_agreed():
    swapped = [("2", "OUT"), ("1", "GND"), ("5", "IN"), ("3", "OC"), ("4", "EN")]
    assert PT.check({4: TABLE}, swapped)["status"] != "agreed"


def test_no_text_layer_or_no_table_leaves_it_unchecked():
    pins = [("1", "OUT"), ("2", "GND")]
    assert PT.check({1: "", 2: "   "}, pins)["status"] == "unchecked"
    assert PT.check({1: "A datasheet page with no pin table at all."}, pins)["status"] == "unchecked"
    assert PT.check({4: TABLE}, [("1", "OUT")])["status"] == "unchecked"          # one pin proves nothing


def test_nc_pins_are_not_needed_and_numbers_may_come_first():
    text = "PIN NAME FUNCTION\n1 OUTA Output A\n2 -INA Inverting input A\n3 +INA Noninverting\n4 V- Negative supply"
    pins = [("1", "OUTA"), ("2", "−INA"), ("3", "+INA"), ("4", "V-"), ("5", "NC")]
    assert PT.check({3: text}, pins)["status"] == "agreed"


# ---------------------------------------------------------------- Mouser's NXP-style description names the case
def test_mouser_slash_description_gives_the_case_of_this_type_only():
    from ecadlib import bom_triage as BT
    assert BT.slash_case("BC847C/SOT23/TO-236AB", "BC847C,215") == "SOT23/TO-236AB"
    assert BT.slash_case("74HC595D/SOT109/SO16", "74HC595D,118") == "SOT109/SO16"
    assert BT.slash_case("BC847B/SOT23", "BC847C,215") == ""               # another type's description
    assert BT.slash_case("ESD Protection Diodes / TVS Diodes", "SMBJ5.0A") == ""
    assert BT.slash_case("", "X") == ""


# ---------------------------------------------------------------- a datasheet build released only when confirmed
def _res(cc_status="agreed", via="part search package", review=(), pins=None):
    cc = {"status": cc_status, "checks": [{"status": cc_status, "source": "standard table", "via": via,
                                           "against": "standard:SOIC-8_3.9x4.9mm_P1.27mm"}]}
    return {"cross_check": cc, "needs_review": list(review), "symbol": {"pins": pins or []}}


def test_a_footprint_read_needs_an_independent_agreeing_source():
    from ecadlib import librarian as L
    assert L.read_confirmed(_res(), {}, ["footprint"])
    assert not L.read_confirmed(_res("self-checked", "the read's package name"), {}, ["footprint"])
    assert not L.read_confirmed(_res("unchecked"), {}, ["footprint"])
    assert not L.read_confirmed(_res(review=["the PDF at x doesn't mention LM393DR: the datasheet PDF does not seem "
                                             "to name this part"]), {}, ["footprint"])


def test_a_symbol_read_needs_the_pdf_text_to_agree():
    from ecadlib import librarian as L
    pins = [{"num": "1", "name": "OUT"}, {"num": "2", "name": "GND"}, {"num": "5", "name": "IN"},
            {"num": "3", "name": "OC"}, {"num": "4", "name": "EN"}]
    res = _res(pins=pins)
    assert not L.read_confirmed(res, {}, ["symbol"])                    # no PDF bytes: unchecked, held
    import ecadlib.pin_text as PTm
    old = PTm.page_texts
    same = TABLE.replace("GND 1 2", "GND 2 2").replace("OUT 6, 7, 8 1", "OUT 1 1").replace("IN 2, 3 5", "IN 5 5")         .replace("OC 5 3", "OC 3 3")
    PTm.page_texts = lambda data, limit=80: {4: same}
    try:
        assert L.read_confirmed(res, {"data": b"%PDF"}, ["symbol"])
        bad = _res(pins=[dict(p, num={"1": "2", "2": "1"}.get(p["num"], p["num"])) for p in pins])
        assert not L.read_confirmed(bad, {"data": b"%PDF"}, ["symbol"])
        assert not L.read_confirmed(_res("unchecked", pins=pins), {"data": b"%PDF"}, ["footprint", "symbol"])
    finally:
        PTm.page_texts = old


# ---------------------------------------------------------------- a KiCad land picked by name vs the read's own land
def _mod(pads):
    body = "".join(f'  (pad "{n}" smd roundrect (at {x} {y}) (size {w} {h}) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
                   for n, x, y, w, h in pads)
    return f'(footprint "X" (layer "F.Cu")\n{body})\n'


def test_lands_agree_within_tolerance_and_turned():
    from ecadlib import librarian as L
    kicad = _mod([("1", -4.85, 0, 2.9, 5.4), ("2", 4.85, 0, 2.9, 5.4)])
    read = _mod([("1", -4.9, 0, 2.9, 5.4), ("2", 4.9, 0, 2.9, 5.4)])
    assert L.lands_agree(read, kicad)[0]
    turned = _mod([("1", 0, -4.85, 5.4, 2.9), ("2", 0, 4.85, 5.4, 2.9)])
    ok, how = L.lands_agree(turned, kicad)
    assert ok and "turned" in how, how


def test_lands_that_differ_or_swap_pins_do_not_agree():
    from ecadlib import librarian as L
    kicad = _mod([("1", -1.15, -0.875, 1.3, 1.05), ("2", 1.15, -0.875, 1.3, 1.05),
                  ("3", 1.15, 0.875, 1.3, 1.05), ("4", -1.15, 0.875, 1.3, 1.05)])
    bigger = _mod([("1", -1.4, -0.875, 1.3, 1.05), ("2", 1.4, -0.875, 1.3, 1.05),
                   ("3", 1.4, 0.875, 1.3, 1.05), ("4", -1.4, 0.875, 1.3, 1.05)])
    assert not L.lands_agree(bigger, kicad)[0]                         # 0.25 mm wider: another land
    mirrored = _mod([("1", 1.15, -0.875, 1.3, 1.05), ("2", -1.15, -0.875, 1.3, 1.05),
                     ("3", -1.15, 0.875, 1.3, 1.05), ("4", 1.15, 0.875, 1.3, 1.05)])
    assert not L.lands_agree(mirrored, kicad)[0]                       # a mirror is never a turn
    three = _mod([("1", -1, 0, 1, 1), ("2", 1, 0, 1, 1), ("3", 0, 1, 1, 1)])
    assert not L.lands_agree(three, kicad)[0]


def test_a_distributor_case_group_names_one_land_or_none():
    from ecadlib import librarian as L
    assert L.case_land("TO-236-3, SC-59, SOT-23-3") == "SOT-23"
    assert L.case_land('8-SOIC (0.154", 3.90mm Width)') == "SOIC-8_3.9x4.9mm_P1.27mm"
    assert L.case_land("SC-74A, SOT-753") == "SOT-23-5"
    assert L.case_land("SOD-123") == "D_SOD-123"
    assert L.case_land("TO-236-3, SOT-323") is None                      # two packages named: none
    assert L.case_land("32-TQFP") is None and L.case_land("") is None    # no body size: no land


# ---------------------------------------------------------------- review 11-16: wrong-part paths
def test_a_variant_table_on_another_page_keeps_the_part_with_a_person():
    """MCP6L01 / MCP6L01R: one page's table agrees, another page names the same pins with the supplies swapped. The
    text does not say which table is this part's, whichever page comes first."""
    pins = [("1", "VOUT"), ("2", "VSS"), ("3", "VIN+"), ("4", "VIN-"), ("5", "VDD")]
    a = "PIN NAME FUNCTION\n1 VOUT Output\n2 VSS Negative supply\n3 VIN+ Input\n4 VIN- Input\n5 VDD Positive supply"
    b = a.replace("2 VSS", "5 VSS").replace("5 VDD", "2 VDD")
    assert PT.check({3: a}, pins)["status"] == "agreed"
    for pages in ({3: a, 4: b}, {3: b, 4: a}):
        assert PT.check(pages, pins)["status"] != "agreed", pages
    assert PT.check({3: a, 4: a}, pins)["status"] == "agreed"            # the same table twice still agrees


def test_a_kicad_land_is_not_confirmed_by_a_read_that_may_not_be_this_part():
    from ecadlib import librarian as L

    class R(L.Run):
        def __init__(self):
            pass
    st = {"verify": ["footprint X chosen by footprint named after the MPN"], "tried": [],
          "footprint": {"source": "kicad:Package_SO:X", "kicad_mod": "x.kicad_mod"}}
    res = {"spec": {"land": {"x": 1}}, "needs_review": ["the datasheet PDF does not seem to name this part"]}
    assert R().kicad_land_confirmed(st, res) is False and st["verify"]
