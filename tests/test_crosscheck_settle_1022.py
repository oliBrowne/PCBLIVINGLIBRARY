"""Cross-check, 2026-10-22: two more held kinds that change nothing built are settled, everything else still holds.
(1) A standard table package the read gives with length and width the other way round (AT24CS01-MAHM, AL8860MP in the
2026-10-21 live benchmark) is built from the table, not held. (2) The exposed pad's own number: one read numbers it and
the other leaves it out (MAX22201), or names it by its net (OPA569DWP "V-" vs "PowerPAD", PCA9557BS "VSS" vs "exposed
center pad"). Read data: /mnt/project-files/benchmark/oct21/held/. Every rule has cases built to sneak a wrong part
through, which must stay held.   python3 -m tests.run_all test_crosscheck_settle_1022"""
from ecadlib import crosscheck as CC


def r(mn, nom, mx):
    return {"min": mn, "nom": nom, "max": mx}


# AT24CS01-MAHM's first read (UDFN-8 2 x 3, pad 1.5 x 1.3), table DFN-8-1EP_3x2mm_P0.5mm_EP1.3x1.5mm
AT24 = {"package_type": "qfn", "name": "UDFN-8-1EP_3x2mm_P0.5mm_EP1.3x1.5mm", "pin_count": 8, "pitch": 0.5,
        "body_length": r(1.9, 2.0, 2.1), "body_width": r(2.9, 3.0, 3.1), "terminal_length": r(0.3, 0.35, 0.4),
        "terminal_width": r(0.18, 0.25, 0.3), "exposed_pad": {"length": r(1.4, 1.5, 1.6), "width": r(1.2, 1.3, 1.4)},
        "pins_left_right": 4, "pins_top_bottom": 0}
# AL8860MP's first read (MSOP-8, pad 1.8 x 1.5), table MSOP-8-1EP_3x3mm_P0.65mm_EP1.5x1.8mm
AL88 = {"package_type": "gullwing", "name": "MSOP-8-1EP_3x3mm_P0.65mm_EP1.5x1.8mm", "pin_count": 8, "pitch": 0.65,
        "body_length": r(2.9, 3.0, 3.1), "body_width": r(2.9, 3.0, 3.1), "lead_span": r(4.7, 4.9, 5.1),
        "terminal_length": r(0.4, 0.6, 0.8), "terminal_width": r(0.22, 0.3, 0.38),
        "exposed_pad": {"length": r(1.6, 1.8, 2.0), "width": r(1.3, 1.5, 1.7)}}


class Reader:
    cfg = {"cross_check": "auto"}


def run(spec, pins=()):
    res = {"spec": dict(spec), "symbol": {"pins": [{"num": n, "name": s} for n, s in pins]}}
    return CC.run(Reader(), {"package": None, "fp_needed": True, "part": "X"}, res, "m")


# ---------------------------------------------------------------- (1) the table settles length and width
def test_a_table_package_read_the_other_way_round_is_built_from_the_table_not_held():
    for spec, name in ((AT24, "DFN-8-1EP_3x2mm_P0.5mm_EP1.3x1.5mm"), (AL88, "MSOP-8-1EP_3x3mm_P0.65mm_EP1.5x1.8mm")):
        rec = run(spec)
        assert rec["status"] != "disagreed" and rec["standard"] == name, rec
        c = rec["checks"][0]
        assert c["settled"] == "standard table" and "other way round" in c["tie_breaks"][0], c
    assert run(AL88)["status"] == "self-checked"        # (the read's own name chose the table: still reviewed)


def test_a_turn_with_any_other_difference_still_holds():
    for over in ({"pitch": 0.5}, {"pin_count": 10}, {"exposed_pad": None},
                 {"exposed_pad": {"length": r(2.4, 2.5, 2.6), "width": r(1.3, 1.5, 1.7)}}):   # a size, not a turn
        spec = {k: v for k, v in dict(AL88, **over).items() if v is not None}
        rec = run(spec)
        assert rec["status"] == "disagreed" and "standard" not in rec, (over, rec)


def test_a_second_read_that_disagrees_keeps_the_part_held():
    table = CC.against_table(CC.critical(AL88), "MSOP-8-1EP_3x3mm_P0.65mm_EP1.5x1.8mm", "the read's package name")
    second = {"source": "second read", "status": "disagreed", "disagreed": [{"field": "pitch", "first": "0.65",
                                                                             "second": "0.5"}]}
    assert CC._settle_turn([table, second]) is None and table["status"] == "disagreed"


def test_a_read_with_no_table_entry_is_never_turned_to_fit():
    # LTC2942-1: DFN-6 2 x 3 read, 3 x 2 printed, no table entry; released turned it was built with its axes swapped
    s = dict(AT24, name="DFN-6-1EP_2x3mm_P0.5mm_EP1.35x1.65mm", pin_count=6, pins_left_right=3,
             exposed_pad={"length": r(1.25, 1.35, 1.45), "width": r(1.55, 1.65, 1.75)})
    assert CC.standard_matches(None, s["name"]) == []
    printed = dict(s, body_length=s["body_width"], body_width=s["body_length"],
                   exposed_pad={"length": s["exposed_pad"]["width"], "width": s["exposed_pad"]["length"]})
    second = CC.against_read(CC.critical(s), CC.critical(printed), [], [])
    assert second["status"] == "disagreed" and any(CC.SWAPPED in (d.get("note") or "") for d in second["disagreed"])
    assert CC._settle_turn([second]) is None and second["status"] == "disagreed" and "settled" not in second


# ---------------------------------------------------------------- (2) the exposed pad's own number
def crit(n=12, ep=True):
    return {"pin_count": n, "has_ep": ep}


def test_one_read_numbering_the_exposed_pad_is_not_a_missing_pin():
    a = [(str(i), f"P{i}") for i in range(1, 13)] + [("13", "EP")]            # MAX22201: TDFN-12 + pad 13
    b = [(str(i), f"P{i}") for i in range(1, 13)]
    assert CC.compare_pins(a, b, CC.ep_numbers(crit(), crit(), a, b)) == []
    assert CC.compare_pins(a, b) != []                                         # (without the pad's number: as before)


def test_the_exposed_pad_named_by_its_net_agrees():
    for net, pad in (("V-", "PowerPAD"), ("V–", "PowerPAD"), ("VSS", "exposed center pad"),
                     ("NC", "Exposed Pad (DO NOT CONNECT)")):
        a = [(str(i), f"P{i}") for i in range(1, 21)] + [("21", net)]
        b = [(str(i), f"P{i}") for i in range(1, 21)] + [("21", pad)]
        assert CC.compare_pins(a, b, CC.ep_numbers(crit(20), crit(20), a, b)) == [], (net, pad)


def test_past_the_leads_but_no_exposed_pad_still_holds():
    a = [(str(i), f"P{i}") for i in range(1, 13)] + [("13", "EP")]
    b = [(str(i), f"P{i}") for i in range(1, 13)]
    assert CC.ep_numbers(crit(), crit(ep=False), a, b) == set()                 # the other read has no pad
    two = a + [("14", "EP2")]                                                   # two numbers past the leads: pins missed
    assert CC.ep_numbers(crit(), crit(), two, b) == set()
    b13 = b + [("14", "EP")]                                                    # 13 in one read, 14 in the other
    assert CC.ep_numbers(crit(), crit(), a, b13) == set() and CC.compare_pins(a, b13, CC.ep_numbers(crit(), crit(), a, b13))
    a2 = [(str(i), f"P{i}") for i in range(1, 13)] + [("13", "SDA")]            # both number it, neither calls it the pad
    b2 = [(str(i), f"P{i}") for i in range(1, 13)] + [("13", "SCL")]
    assert CC.compare_pins(a2, b2, CC.ep_numbers(crit(), crit(), a2, b2))[0]["field"] == "pin_names"


def test_a_tab_inside_the_leads_is_never_the_exposed_pad():
    # SOT-223: the tab is pin 2 again; "TAB" vs "VIN" is a real numbering disagreement
    a, b = [("1", "GND"), ("2", "VOUT"), ("3", "VIN")], [("1", "GND"), ("2", "TAB"), ("3", "VIN")]
    assert CC.compare_pins(a, b, CC.ep_numbers(crit(3), crit(3), a, b))[0]["field"] == "pin_names"


# ---------------------------------------------------------------- end to end: the librarian builds the table's land
def test_the_librarian_builds_a_turned_read_from_the_table_with_standard_land_off():
    import copy, tempfile
    from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB, test_ds_route as TR
    ext = copy.deepcopy(TD.INCH)                                     # SOIC-8 read with D and E1 the other way round
    p = ext["package"]
    p["body_length"], p["body_width"] = p["body_width"], p["body_length"]
    with tempfile.TemporaryDirectory() as d:
        items, _, _ = TB.run2(d, TC.API(lambda k, m, b: TR.on_page(ext)), reader_kw={"batch": False})
        a = items["DSPART1"]["librarian"]
        tried = [t for t in a["tried"] if t.startswith("datasheet reader")]
        assert tried[0].startswith("datasheet reader (BOM): footprint Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"), tried
        assert any("other way round" in r and r.startswith("standard land:") for r in a["review"]), a["review"]
        assert any(t.startswith("cross-check (auto): self-checked") for t in a["tried"]), a["tried"]
