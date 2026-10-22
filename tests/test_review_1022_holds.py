"""Review 2026-10-22, holds: a part is held rather than built wrong. Leads joined to the exposed pad found from the
builder's own pin split and from names that agree, two-sided packages known by a vendor-prefixed name, a maker's land
that does not cover its lead, a pin table that just leaves a pin out, and TI known from its maker / bare host.
The specs are SYNTHETIC (made-up parts)."""
from ecadlib import datasheet as D, footprint_gen as F, footprint_bench as B


def pads(spec):
    return {p[0]: p[1:5] for p in B.numbers(F.build(spec)[0])["pads"]}


def drain8(names, pad):
    return [{"num": str(i), "name": n} for i, n in enumerate(names, 1)] + [{"num": "9", "name": pad}]


# ---------------------------------------------------------------- 1. the pin split as the builder lays it out
def test_rows_are_the_builders_split():
    assert F.rows({"pin_count": 8, "pins_left_right": 4}) == (4, 0)
    assert F.rows({"pin_count": 16}) == (4, 4)
    assert F.rows({"pin_count": 12, "pins_left_right": 4, "pins_top_bottom": 2}) == (4, 2)


def test_joined_to_pad_holds_a_two_sided_read_that_gave_only_pins_left_right():
    spec = {"name": "WDFN-8_5x6mm", "package_type": "qfn", "pin_count": 8, "pins_left_right": 4,
            "exposed_pad": {"length": {"nom": 4}, "width": {"nom": 4}}}
    line = D.joined_to_pad(spec, drain8("SSSGDDDD", "D"))
    assert line and "leads 5-8" in line, line


def test_a_dfn_pad_is_turned_when_the_read_gave_only_pins_left_right():
    base = {"name": "DFN-12-1EP_3x3mm_P0.45mm", "package_type": "qfn", "pitch": 0.45, "pin_count": 12,
            "body_length": {"nom": 3.0}, "body_width": {"nom": 3.0}, "terminal_length": {"min": 0.3, "nom": 0.4, "max": 0.5},
            "terminal_width": {"min": 0.18, "max": 0.3}, "height_max": 0.8,
            "exposed_pad": {"length": {"nom": 2.38}, "width": {"nom": 1.65}}}
    full, short = pads(dict(base, pins_left_right=6, pins_top_bottom=0)), pads(dict(base, pins_left_right=6))
    assert full == short, (full["1"], short["1"], full["13"], short["13"])
    assert abs(full["13"][2] - 1.65) < 1e-6                           # turned: 1.65 across the rows


# ---------------------------------------------------------------- 2. two-sided names with a vendor prefix
def test_vendor_prefixed_dfn_son_names_are_two_sided():
    for name in ("WDFN-8_2x2mm", "PG-TDSON-8", "Infineon_PG-TDSON-8", "Texas_VSONP-8_5x6mm", "S-PVSON-N8", "TDFN-8",
                 "X2SON-4", "LFCSP-8-1EP_3x2mm_P0.5mm", "WSON-10"):
        spec = {"name": name, "package_type": "qfn", "pin_count": 8}
        D.two_sided(spec, [])
        assert (spec.get("pins_left_right"), spec.get("pins_top_bottom")) == (4, 0), name


def test_four_sided_names_stay_four_sided():
    for name, n in (("QFN-16_3x3mm", 16), ("LFCSP-16-1EP_3x3mm_P0.5mm", 16), ("LFCSP-32", 32), ("VQFN-20", 20),
                    ("TSOP-8", 8), ("MSOP-8", 8), ("PERSONALITY-8", 8), ("TQFN-12", 12)):
        spec = {"name": name, "package_type": "qfn", "pin_count": n}
        D.two_sided(spec, [])
        assert "pins_left_right" not in spec, name


def test_a_vendor_prefixed_son_with_drain_leads_on_the_pad_is_held():
    spec = {"name": "PG-TDSON-8", "package_type": "qfn", "pin_count": 8, "exposed_pad": {"length": {"nom": 4}, "width": {"nom": 4}}}
    D.two_sided(spec, [])
    assert D.joined_to_pad(spec, drain8("SSSGDDDD", "D"))


# ---------------------------------------------------------------- 3. names that agree, not only equal
def test_joined_to_pad_matches_names_that_agree():
    s = {"package_type": "gullwing", "name": "PQFN-8", "pin_count": 8, "pitch": 1.27, "exposed_pad": {"length": 4.0, "width": 3.0}}
    for leads, pad in (("SSSGDDDD", "D (exposed pad)"), (["S"] * 3 + ["G"] + ["DRAIN"] * 4, "D"), ("SSSGDDDD", "DRAIN"),
                       (["SW"] * 4 + ["IN", "EN", "FB", "BST"], "SW")):
        assert D.joined_to_pad(s, drain8(leads, pad)), (leads, pad)


def test_joined_to_pad_leaves_pad_names_and_unrelated_nets_alone():
    s = {"package_type": "gullwing", "name": "PQFN-8", "pin_count": 8, "pitch": 1.27, "exposed_pad": {"length": 4.0, "width": 3.0}}
    gnd = ["IN", "EN", "FB", "PG", "GND", "GND", "GND", "GND"]
    for leads, pad in ((gnd, "PAD"), (gnd, "EP"), (gnd, "EPAD"), (gnd, "GND (PAD)"), (gnd, "Exposed pad"),
                       (["IN", "EN", "FB", "PG"] + ["NC"] * 4, "PAD"), (["IN", "EN", "FB", "PG"] + ["NC"] * 4, "NC"),
                       ("SSSGDDDD", "S"), (["VIN"] * 4 + ["VOUT"] * 4, "SW"), ("SSSGDDDD", "G"),
                       (["IN", "EN", "FB", "PG"] + ["EP"] * 4, "VIN")):
        assert D.joined_to_pad(s, drain8(leads, pad)) is None, (leads, pad)


# ---------------------------------------------------------------- 4. a maker's land that does not cover its lead
SOT143 = {"name": "SOT-143", "package_type": "gullwing", "pitch": 1.9, "pin_count": 4,
          "body_length": {"min": 2.8, "max": 3.0}, "body_width": {"min": 1.2, "max": 1.4}, "lead_span": {"min": 2.1, "max": 2.5},
          "terminal_length": {"min": 0.3, "max": 0.5}, "terminal_width": {"min": 0.3, "max": 0.5}, "height_max": 1.1,
          "pin_positions": [{"number": "1", "side": "L", "offset": -0.75}, {"number": "2", "side": "L", "offset": 0.95},
                            {"number": "3", "side": "R", "offset": 0.95}, {"number": "4", "side": "R", "offset": -0.95}],
          "land": {"pad_length": 0.7, "pad_width": 0.6, "pad_center": 1.0}}
DFN8 = {"name": "WDFN-8_2x2mm_P0.5mm", "package_type": "qfn", "pitch": 0.5, "body_length": {"nom": 2.0},
        "body_width": {"nom": 2.0}, "terminal_length": {"min": 0.25, "max": 0.35}, "terminal_width": {"min": 0.2, "max": 0.3},
        "height_max": 0.8, "pin_count": 8, "pins_left_right": 4, "pins_top_bottom": 0,
        "land": {"pad_length": 0.78, "pad_width": 0.3, "pad_center": 0.76}}


def _held(spec):
    from ecadlib import ds_route as DR
    review = []
    D.sanity(spec, review)
    return [r for r in review if DR.FAILED.search(r)]


def test_a_land_that_does_not_cover_its_lead_is_held():
    for spec in (dict(SOT143, land=dict(SOT143["land"], pin1_width=0.1)), dict(SOT143, land=dict(SOT143["land"], pin1_length=0.05)),
                 dict(SOT143, land=dict(SOT143["land"], pad_width=0.1)), dict(DFN8, land=dict(DFN8["land"], pin1_width=0.08)),
                 dict(DFN8, land=dict(DFN8["land"], pin1_length=0.12)), dict(DFN8, land=dict(DFN8["land"], pad_width=0.08))):
        held = _held(spec)
        assert held and all(h.startswith("land pattern:") for h in held), (spec["land"], held)


def test_a_land_that_covers_its_lead_is_not_held():
    for spec in (SOT143, dict(SOT143, land=dict(SOT143["land"], pin1_width=0.7)), DFN8,
                 dict(DFN8, land=dict(DFN8["land"], pin1_length=0.88))):
        assert not _held(spec), (spec["land"], _held(spec))
    quad = {"name": "QFN-16_3x3mm_P0.5mm", "package_type": "qfn", "pitch": 0.5, "pin_count": 16, "body_length": {"nom": 3.0},
            "body_width": {"nom": 4.0}, "terminal_length": {"min": 0.3, "max": 0.5}, "terminal_width": {"min": 0.18, "max": 0.3},
            "land": {"pad_length": 0.8, "pad_width": 0.25, "pad_center": 1.4, "pad_center_y": 1.9}}
    assert not _held(quad), _held(quad)
    assert _held(dict(quad, land=dict(quad["land"], pad_center_y=1.4)))           # top/bottom pads inside the body edge


# ---------------------------------------------------------------- 5. a pin table that just leaves a pin out
def test_a_pin_table_missing_a_pin_is_not_taken_as_depopulated():
    spec = {"name": "SOIC-8_3.9x4.9mm_P1.27mm", "package_type": "gullwing", "pitch": 1.27}
    ext = {"pins": [{"num": str(i)} for i in (1, 2, 3, 4, 5, 6, 8)]}
    review = []
    assert D._depop_positions(spec, ext, review) is None
    assert review and review[0].startswith("symbol:") and "7" in review[0], review
    review = []
    assert D._depop_positions(dict(spec, pin_count=7), ext, review) is None and review


def test_a_name_that_says_which_places_are_empty_is_depopulated():
    spec = {"name": "SOIC-8-N7_3.9x4.9mm_P1.27mm", "package_type": "gullwing", "pitch": 1.27}
    ext = {"pins": [{"num": str(i)} for i in (1, 2, 3, 4, 5, 6, 8)]}
    pos, places, absent = D._depop_positions(spec, ext)
    assert places == 8 and absent == [7] and len(pos) == 7
    review = []
    assert D._depop_positions(spec, {"pins": [{"num": str(i)} for i in (1, 2, 3, 4, 5, 7, 8)]}, review) is None and review
    pos, places, absent = D._depop_positions({"name": "SOIC-14-16_P1.27mm", "package_type": "gullwing", "pitch": 1.27},
                                             {"pins": [{"num": str(i)} for i in [1] + list(range(3, 13)) + [14, 15, 16]]})
    assert places == 16 and absent == [2, 13]


# ---------------------------------------------------------------- 6. TI known from its maker's spelling or bare host
def test_ti_maker_spellings_and_bare_host_hold_the_powerpad():
    spec = {"package_type": "gullwing", "exposed_pad": {"length": {"nom": 5.7}, "width": {"nom": 3}},
            "land": {"pad_length": 1.5, "pad_width": 0.3, "pad_center": 3.4}}
    for maker in ("Texas Instruments", "TI", "Texas-Instruments", "TexasInstruments", "Texas Instruments Inc.",
                  "Burr-Brown (TI)", "TI(德州仪器)", "texas instruments"):
        assert D.thermal_land_missing(spec, maker), maker
    for maker in ("Tiger Electronics", "Titan Micro", "STMicroelectronics", "Analog Devices", ""):
        assert D.thermal_land_missing(spec, maker) is None, maker
    for url in ("http://www.ti.com", "https://ti.com", "https://www.ti.com/lit/ds/symlink/x.pdf", "www.ti.com?x=1"):
        assert D.thermal_land_missing(spec, "", url), url
    for url in ("https://www.mouser.com/ti.com.pdf", "https://notti.com/x.pdf", "https://www.ti.company.com/x"):
        assert D.thermal_land_missing(spec, "", url) is None, url
