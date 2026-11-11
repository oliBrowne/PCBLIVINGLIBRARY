"""Full review 2026-11-10, group 2: resolve's suggested pin map paired the unmatched pins and pads in sorted order
(B, C, E onto 1, 2, 3: wrong for a SOT-23 transistor) and one 'y' accepted it."""
from ecadlib import resolve as R


def test_no_pin_map_is_suggested_when_the_pairing_is_a_guess():
    c = R.compare(["B", "C", "E"], [("1", False), ("2", False), ("3", False)])
    assert not c["match"] and c["pin_map"] is None, c
    assert c["pins_without_pad"] == ["B", "C", "E"] and c["pads_without_pin"] == ["1", "2", "3"]
    c = R.compare(["1", "2", "A", "K"], [("1", False), ("2", False), ("3", False), ("4", False)])
    assert c["pin_map"] is None, c


def test_the_only_possible_pairing_is_still_suggested():
    assert R.compare(["1", "2"], [("1", False), ("3", False)])["pin_map"] == {"2": "3"}
    assert R.compare(["1", "S1"], [("1", False), ("SH", True)])["pin_map"] == {"S1": "SH"}
