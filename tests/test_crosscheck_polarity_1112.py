"""Full review 2026-11-10, 3.4: two reads that name a pin the same but mark it active low in only one of them
disagree (the pin's sense is inverted), so the part is held instead of built with a guessed sense."""
from ecadlib import crosscheck as CC


def test_an_active_low_mark_in_one_read_only_is_a_disagreement():
    a = [("1", "Q"), ("2", "~{Q}"), ("3", "GND"), ("4", "VCC")]
    b = [("1", "Q"), ("2", "Q"), ("3", "GND"), ("4", "VCC")]
    out = CC.compare_pins(a, b)
    assert [d["field"] for d in out] == ["pin_names"] and "2" in out[0]["first"]


def test_two_spellings_of_active_low_agree():
    a = [("1", "~{RESET}"), ("2", "EN#"), ("3", "GND")]
    b = [("1", "RESET_N"), ("2", "/EN"), ("3", "GND")]
    assert CC.compare_pins(a, b) == []
