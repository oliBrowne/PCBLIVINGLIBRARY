"""2026-10-30 property-based hunt (symbols / part data): counterexamples found by fuzzing, as plain regression tests.
Run:  python3 -m tests.test_fuzz1030_symbols"""
import tempfile
from pathlib import Path

from tests.fixtures_lite import lite  # noqa: F401  (the suite's convention)


def _hc12x(oe):
    """A 74HC125 (oe='~{%sOE}': active-low enables) / 74HC126 (oe='%sOE': active-high) quad buffer pin list."""
    pins = []
    for ch, (e, a, y) in enumerate(((1, 2, 3), (4, 5, 6), (10, 9, 8), (13, 12, 11)), 1):
        pins += [{"num": str(e), "name": oe % ch, "type": "input"}, {"num": str(a), "name": f"{ch}A", "type": "input"},
                 {"num": str(y), "name": f"{ch}Y", "type": "tristate"}]
    return pins + [{"num": "7", "name": "GND", "type": "power"}, {"num": "14", "name": "VCC", "type": "power"}]


def test_dedupe_symbol_keeps_overbar_apart():
    """dedupe compared pin names with the overbar dropped: a 74HC125 (~{1OE}, active low) and a 74HC126 (1OE, active
    high) symbol were one symbol, and --merge pointed one part at the other's symbol."""
    from ecadlib import altium_lib as AL, dedupe as DD
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d, "a.SchLib"), [AL.ic_symbol("74HC125", _hc12x("~{%sOE}"))])
        AL.write_schlib(Path(d, "b.SchLib"), [AL.ic_symbol("74HC126", _hc12x("%sOE"))])
        AL.write_schlib(Path(d, "c.SchLib"), [AL.ic_symbol("74HC125_COPY", _hc12x("~{%sOE}"))])
        po = {n: DD.sym_pinout(AL.read_schlib(Path(d, f"{n}.SchLib"))[1][0]) for n in "abc"}
    assert DD.sym_hash(po["a"]) != DD.sym_hash(po["b"])
    assert DD.sym_hash(po["a"]) == DD.sym_hash(po["c"])          # the same symbol twice is still one


def test_dedupe_symbol_keeps_pin_types_apart():
    """dedupe compared pin numbers and names only: a 74HC04 (push-pull outputs) and a 74HC05 (open-drain outputs) were
    one symbol, so --merge gave the 74HC05 output pins and ERC flagged its wired-OR (or missed two driven outputs)."""
    from ecadlib import altium_lib as AL, dedupe as DD
    def hex_inv(out):
        pins = []
        for ch, (a, y) in enumerate(((1, 2), (3, 4), (5, 6), (9, 8), (11, 10), (13, 12)), 1):
            pins += [{"num": str(a), "name": f"{ch}A", "type": "input"}, {"num": str(y), "name": f"{ch}Y", "type": out}]
        return pins + [{"num": "7", "name": "GND", "type": "power"}, {"num": "14", "name": "VCC", "type": "power"}]
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d, "a.SchLib"), [AL.ic_symbol("74HC04", hex_inv("output"))])
        AL.write_schlib(Path(d, "b.SchLib"), [AL.ic_symbol("74HC05", hex_inv("open_collector"))])
        po = {n: DD.sym_pinout(AL.read_schlib(Path(d, f"{n}.SchLib"))[1][0]) for n in "ab"}
    assert DD.sym_hash(po["a"]) != DD.sym_hash(po["b"])


def test_fmt_value_keeps_r_notation_and_other_units_on_caps_and_inductors():
    """fmt_value read the ohm letter R on an inductor or capacitor as the decimal point of the base unit: a '4R7'
    inductor (maker code for 4.7 uH) was named 4.7H, a '4R7' capacitor (4.7 pF) 4.7F; a '10uH' value on a capacitor
    line was renamed 10uF. A value in another part's unit is kept as written."""
    from ecadlib import rules as RU
    for raw, cat in (("4R7", "Inductors"), ("10R", "Inductors"), ("4R7", "Capacitors"), ("0R5", "Capacitors"),
                     ("10uH", "Capacitors"), ("100nF", "Inductors"), ("10uF", "Resistors"), ("10kΩ", "Capacitors")):
        for style in ("si_unit", "rkm", "si"):
            assert RU.fmt_value(raw, cat, style) == raw, (raw, cat, style, RU.fmt_value(raw, cat, style))
    # values in the part's own unit, or with none stated, are restyled as before
    assert RU.fmt_value("4R7", "Resistors", "si_unit") == "4.7"
    assert RU.fmt_value("4.7k Ohm", "Resistors", "rkm") == "4K7"
    assert RU.fmt_value("4u7", "Inductors", "si_unit") == "4.7uH"
    assert RU.fmt_value("100N", "Capacitors", "si_unit") == "100nF"
    assert RU.fmt_value("0.1uF 50V", "Capacitors", "rkm_lower") == "100n"


def test_shared_numbers_flags_two_numbered_signals_on_one_pin():
    """shared_numbers took any two names that differ by an index after a separator for one stack: IN_1 and IN_2 (a
    dual comparator's two inputs) or GPIO_1 and GPIO_2 on one pin number passed the release check's R-SY-3, a short
    on the board. Only supply / ground instances (VSS, VSS_2) are one net."""
    from ecadlib import symbol_layout as SL
    pins = lambda *nn: [{"num": "7", "name": n} for n in nn]
    for a, b in (("IN_1", "IN_2"), ("GPIO_1", "GPIO_2"), ("OUT-1", "OUT-2"), ("SDA_1", "SDA_2")):
        assert SL.shared_numbers(pins(a, b)), (a, b)
    for a, b in (("VSS", "VSS_2"), ("GND_1", "GND_2"), ("VDD", "VDD_1"), ("IN_1", "IN_1")):
        assert SL.shared_numbers(pins(a, b)) == [], (a, b)


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_fuzz1030_symbols"]))


def test_shared_numbers_keeps_suffixed_supply_instances_one_net():
    """Review: VIN + VIN_2, VCC + VCC_3, EPAD + EPAD_2 on one pad are one supply's (or the pad's) instances, as GND /
    GND_2 are; classifying the suffixed name ('VIN_2' -> other) called them a short and the release check held them."""
    from ecadlib.symbol_layout import shared_numbers
    for a, b in (("VIN", "VIN_2"), ("VIN_1", "VIN_2"), ("VCC", "VCC_3"), ("EPAD", "EPAD_2"), ("GND", "GND_2")):
        assert shared_numbers([{"num": "5", "name": a}, {"num": "5", "name": b}]) == [], (a, b)
    assert shared_numbers([{"num": "5", "name": "IN_1"}, {"num": "5", "name": "IN_2"}])
