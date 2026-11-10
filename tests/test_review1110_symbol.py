"""Full review 2026-11-10, symbol re-layout: a pin number carrying two different signals is a fault the check reports
('pin number 2 is used by 2 different signals: SDA, WP'). relayout (and the two pin-list readers) kept only the first
pin of a number, so after the re-layout the second pin was gone and the check passed. A stacked supply pin (VSS on a
number twice) is still one pin."""
from ecadlib import symbol_layout as SL


def _sym(pins):
    return {"name": "X", "designator": "U?", "prims": [("rect", 0, 0, 40, -60)] + [
        ("pin", {"num": n, "name": nm, "type": "input"}, "left", 0, -10 * (i + 1), 30) for i, (n, nm) in enumerate(pins)]}


def _pins(sym):
    return [(p[1]["num"], p[1]["name"]) for p in sym["prims"] if p[0] == "pin"]


def test_relayout_keeps_both_signals_on_one_number_so_the_hold_still_fires():
    sym = _sym([("1", "VCC"), ("2", "SDA"), ("2", "WP"), ("3", "GND"), ("4", "SCL")])
    before = SL.shared_numbers([{"num": n, "name": nm} for n, nm in _pins(sym)])
    assert before == ["pin number 2 is used by 2 different signals: SDA, WP"], before
    new = SL.relayout(sym)
    assert sorted(_pins(new)) == sorted(_pins(sym)), _pins(new)
    assert SL.shared_numbers([{"num": n, "name": nm} for n, nm in _pins(new)]) == before
    assert any("pin number 2 is used by 2 different signals" in m for m in SL.check(new)), SL.check(new)


def test_a_stacked_supply_pin_is_still_drawn_once():
    sym = _sym([("1", "VCC"), ("2", "IN"), ("3", "GND"), ("3", "GND"), ("4", "OUT")])
    new = SL.relayout(sym)
    assert sorted(_pins(new)) == [("1", "VCC"), ("2", "IN"), ("3", "GND"), ("4", "OUT")], _pins(new)
    assert SL.unique_pins([{"num": "5", "name": "VSS"}, {"num": "5", "name": "VSS_2"}, {"num": "6", "name": "A"},
                           {"num": "6", "name": "B"}]) == [{"num": "5", "name": "VSS"}, {"num": "6", "name": "A"},
                                                           {"num": "6", "name": "B"}]
