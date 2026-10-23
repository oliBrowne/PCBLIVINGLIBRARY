"""Review 2026-10-22 (librarian / crosscheck): holds a standard land must not skip, a package type set aside only when
nothing built changes, BOM passive values described only in the part's own unit, a place read as "no pin" that is
still built as a pin.   python3 -m tests.run_all test_review_1022_librarian"""
import copy, tempfile

from ecadlib import crosscheck as CC, datasheet as D, librarian as L
from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB, test_ds_route as TR
from tests.test_crosscheck_tiebreak_1021 import read, nominal

dim = TD.dim


def powerpad(joined):
    ext = {"package": {"name": "HTSSOP-16-1EP_4.4x5mm_P0.65mm_EP3x3mm", "description": "HTSSOP-16 PowerPAD",
                       "package_type": "gullwing", "pin_count": 16, "pitch": dim("e", None, 0.65, None, "mm", 12),
                       "body_length": dim("D", 4.9, 5.0, 5.1, "mm", 12), "body_width": dim("E1", 4.3, 4.4, 4.5, "mm", 12),
                       "lead_span": dim("E", 6.2, 6.4, 6.6, "mm", 12), "terminal_length": dim("L", 0.45, 0.6, 0.75, "mm", 12),
                       "terminal_width": dim("b", 0.19, None, 0.30, "mm", 12), "height_max": dim("A", None, None, 1.2, "mm", 12),
                       "pins_left_right": None, "pins_top_bottom": None,
                       "exposed_pad": {"length": dim("D2", None, 3.0, None, "mm", 12), "width": dim("E2", None, 3.0, None, "mm", 12)},
                       "unresolved": [], "drawing": "PWP0016"},
           "pin_table": {"page": 2, "figure": "Pin Configuration", "rows": 17},
           "symbol_description": "Synthetic PowerPAD part", "notes": [], "embedded_instructions": []}
    side = lambda i: "left" if i <= 8 else "right"
    ext["pins"] = [TD.pin(i, f"P{i}" if not joined or i <= 8 else "SW", "io", side(i)) for i in range(1, 17)] + \
        [TD.pin(17, "SW" if joined else "PowerPAD", "power" if not joined else "io", "right")]
    return ext


def built(ext, maker, mode):
    api, bom = TC.API(lambda k, m, b: TR.on_page(TD._complete(copy.deepcopy(ext)))), TB.BOM2
    TB.BOM2 = bom.replace("ACME,DSPART1,", f"{maker},DSPART1,")
    try:
        with tempfile.TemporaryDirectory() as d:
            items, _, _ = TB.run2(d, api, reader_kw={"batch": False}, rules={"datasheet": {"standard_land": mode}})
    finally:
        TB.BOM2 = bom
    return items["DSPART1"]


def test_a_standard_land_never_skips_the_thermal_land_and_joined_pad_holds():
    for mode in ("auto", "always"):
        for ext, maker, why in ((powerpad(False), "Texas Instruments", "thermal land"), (powerpad(True), "ACME", "SW")):
            a = built(ext, maker, mode)
            assert not (a.get("footprint") or {}).get("file"), (mode, why, a.get("footprint"))
            tried = [t for t in a["librarian"]["tried"] if t.startswith("datasheet")]
            assert any("no footprint" in t and why in t for t in tried), (mode, tried)
            assert not any(r.startswith("standard land:") for r in a["librarian"]["review"]), a["librarian"]["review"]


def test_custom_vs_connector_is_kept_when_the_type_decides_the_body_hold():
    # a housed horizontal connector read "custom" with no body: as a connector it would be held (body_unread)
    first = {"package_type": "custom", "name": "JST_PHD_S28B-PHDSS_2x14_P2.00mm_Horizontal", "pin_count": 28, "pitch": 2.0,
             "pad_table": {"arrays": [{"count": 14, "x": 0, "y": 0, "dx": -2, "dy": 0}]}}
    # since 2026-10-22 (27C256) a custom pad table with no body is held too: the type no longer decides the hold, so
    # it is a word only, and the read is still held for its blank body
    assert D.body_unread(dict(first, package_type="connector")) and D.body_unread(first)
    c = read(first, nominal(package_type="connector", pitch=2.0, pin_count=28))
    assert c["status"] == "agreed" and c.get("tie_breaks"), c
    # where only one of the two types would hold (a bare pad pattern is held as neither), the type still decides it
    from ecadlib import crosscheck as CCm
    one = {"package_type": "custom", "unread_as": {"custom": False, "connector": True}}
    assert not CCm._label_only(one, {"package_type": "connector"})
    # with its body read, the type is still a word only (TC2030)
    housed = dict(first, body_length={"nom": 12.0}, body_width={"nom": 8.0})
    c = read(housed, nominal(package_type="connector", pitch=2.0, pin_count=28))
    assert c["status"] == "agreed" and c.get("tie_breaks"), c


def test_a_place_the_first_read_marks_missing_but_builds_still_disagrees():
    ext = copy.deepcopy(TD.INCH)
    ext["pins"][1]["name"] = "Missing"
    pins = [(p["num"], p["name"]) for p in D.process(TD._complete(ext), "TESTPART", "https://a/ds.pdf")["symbol"]["pins"]]
    if any(n == "Missing" for _, n in pins):                  # the built symbol still has it as a pin
        assert CC.compare_pins(pins, [p for p in pins if p[0] != "2"]), pins
        assert CC.compare_pins(pins, pins)                    # both reads say no pin: the build still has one
    first = [(str(n), f"P{n}") for n in range(1, 9) if n != 2]
    assert CC.compare_pins(first, sorted(first + [("2", "Missing")])) == []      # the second read's "no pin" is no pin


def desc(folder, value):
    item = {"folder": f"Passives/{folder}", "parameters": [{"name": "Value", "value": value}], "footprint": {"kicad": "X_0603"}}
    return L.passive_description(item, {}, {"value": value})


def test_a_passive_value_is_described_only_in_its_own_unit():
    for folder, value in (("Inductors", "600R@100MHz"), ("Capacitors", "104"), ("Capacitors", "0.1"), ("Resistors", "1002"),
                          ("Capacitors", "3.3V"), ("Resistors", "5V"), ("Resistors", "2A"), ("Capacitors", "10M"),
                          ("Inductors", "100"), ("Resistors", "100n"), ("Capacitors", "10k"), ("Inductors", "10uF")):
        assert desc(folder, value) == "", (folder, value, desc(folder, value))
    for folder, value, want in (("Resistors", "10k/1%", "RES 10kΩ 1% 0603"), ("Resistors", "100 ohm", "RES 100Ω 0603"),
                                ("Resistors", "2.2M", "RES 2.2MΩ 0603"), ("Resistors", "4k7", "RES 4.7kΩ 0603"),
                                ("Resistors", "0R", "RES 0Ω 0603"), ("Resistors", "4R7", "RES 4.7Ω 0603"),
                                ("Capacitors", "22pF NP0", "CAP 22pF NP0 0603"), ("Capacitors", "1uF/50V", "CAP 1uF 50V 0603"),
                                ("Capacitors", "100n", "CAP 100nF 0603"), ("Capacitors", "4N7", "CAP 4.7nF 0603"),
                                ("Capacitors", "4.7u", "CAP 4.7uF 0603"), ("Inductors", "10uH 2A", "IND 10uH 0603"),
                                ("Inductors", "4.7u", "IND 4.7uH 0603")):
        assert desc(folder, value) == want, (folder, value, desc(folder, value))


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
    print("ok")
