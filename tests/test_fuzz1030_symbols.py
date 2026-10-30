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


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_fuzz1030_symbols"]))
