"""Nexperia / NXP ordering codes end in a packing code: 74LVC1G04GW,125 or BAS16,215 (the reel, 12NC). Its 3 digits were
taken for the part number's numeric core, so the part's own datasheet, which prints 74LVC1G04 and 74LVC1G04GW but never
the reel, was said not to name the part (2026-11-19: 6 of 99 hand-downloaded datasheets: 74LVC1G04GW,125, BAS16,215,
BUK9Y4R4-40E,115, PESD5V0S1BL,315, PESD5V0U1BL,315, PUMH11,115). Plain functions:
python3 -m tests.run_all test_nexperia_packing_code_1119"""
from ecadlib import datasheet as D
from tests.test_datasheet_fetch_1021 import FILLER


def test_the_packing_code_is_not_the_part_number():
    assert D.mentions_part(["74LVC1G04 Single inverter Product data sheet" + FILLER], "74LVC1G04GW,125") is True
    assert D.mentions_part(["BAS16 series High-speed switching diodes" + FILLER], "BAS16,215") is True
    assert D.mentions_part(["PUMH11 NPN/NPN resistor-equipped transistors" + FILLER], "PUMH11,115") is True
    assert D.mentions_part(["BUK9Y4R4-40E N-channel 40 V, logic level MOSFET in LFPAK56" + FILLER],
                           "BUK9Y4R4-40E,115") is True


def test_another_parts_sheet_still_does_not():
    assert D.mentions_part(["74LVC1G08 Single 2-input AND gate" + FILLER], "74LVC1G04GW,125") is False
    assert D.mentions_part(["BAS21 series High-voltage switching diodes" + FILLER], "BAS16,215") is False
    assert D.mentions_part(["PUMH9 NPN/NPN resistor-equipped transistors" + FILLER], "PUMH11,115") is False
    # what is left must still be a part number: else "can't tell" (None) would let any PDF through
    assert D.mentions_part(["BAS21 series High-voltage switching diodes" + FILLER], "ABCD,123") is False
    assert D.mentions_part(["BAS21 series High-voltage switching diodes" + FILLER], "AB1,123") is False


def test_a_space_after_the_comma_is_the_same_code():
    assert D.mentions_part(["74LVC1G04 Single inverter Product data sheet" + FILLER], "74LVC1G04GW, 125") is True


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("ok")
