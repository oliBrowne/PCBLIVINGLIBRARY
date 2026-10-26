"""#186 false holds (review 2026-10-25): the right package document is kept for a part number whose last number only
looks like a pin count, an ordering form with a plating / reel tail, and a datasheet carrying an end-of-life banner;
the wrong documents #186 refuses stay refused."""
from ecadlib import datasheet as D, outline_docs as OD

SOIC14 = ["SOIC-14 package outline D0014A. 14 pins, pitch 1.27 mm. DIM MIN MAX A 1.75 A1 0.10 0.25 E1 3.80 4.00 "
          "D 8.55 8.75 b 0.31 0.51 e 1.27 BSC. All linear dimensions are in millimeters."]


def test_a_part_number_is_no_package_code():
    assert OD.mpn_package_code("74HC14", {"SO14"}) is None              # the 14th 74HC part, 14 pins by chance
    assert OD.mpn_package_code("AT24C08", {"SO8"}) is None              # an 8-kbit EEPROM: "08" is no 8
    assert OD.mpn_package_code("XYZ-T23", {"SOT23"}) is None            # SOT23 names the outline, not 23 pins
    assert OD.names_it(SOIC14, "74HC14", own={"SO14"})


def test_the_package_codes_that_are_codes_stay():
    assert OD.mpn_package_code("EPM1270M256", {"BGA256"}) == "M256"
    assert OD.mpn_package_code("M2GL090T-FG484", {"BGA484"}) == "FG484"
    assert OD.mpn_package_code("ICE40HX8K-BG121", {"BGA121"}) == "BG121"
    # a leaded package's code in the first segment is not required, with an ordering tail (EPM240T100C5N) or
    # without (R-FX16-4: MSP430G2553IPW20, LPC1768FBD100 refused the maker's own drawings)
    assert OD.mpn_package_code("EPM240T100", {"QFP100"}) is None
    assert OD.mpn_package_code("EPM240T100C5N", {"QFP100"}) is None


def test_an_ordering_forms_plating_or_reel_tail_is_not_the_part():
    jst = ["EH connector S13B-EH side entry, 13 circuits, pitch 2.5 mm. Dimensions in mm, tolerance +-0.2. " * 2]
    assert D.mentions_part(jst, "S13B-EH(LF)(SN)", whole=True)
    torex = ["XC6206P332MR 3.3 V positive voltage regulator SOT-23 package outline dimensions in mm, min max. " * 2]
    assert D.mentions_part(torex, "XC6206P332MR-G", whole=True)
    xh = ["XH connector S13B-XH side entry, 13 circuits, pitch 2.5 mm. Dimensions in mm, tolerance +-0.2. " * 2]
    assert not D.mentions_part(xh, "S13B-EH(LF)(SN)", whole=True)       # another series is still another part


def test_a_status_banner_or_title_block_field_is_no_notice():
    assert D.a_notice(["PACKAGE OUTLINE\nSOIC-14 D0014A\nPCN 2019-114\nDIM MIN MAX\nA 1.75"]) is None
    assert D.a_notice(["END OF LIFE\nLM9999 Low-noise amplifier\nFeatures"]) is None
    assert D.a_notice(["Last Time Buy: 2027-03-31\nNC3XYZ-1 Datasheet\nFeatures"]) is None
    assert D.not_a_datasheet(["END OF LIFE\nLM9999 Low-noise amplifier\nFeatures"]) is None
    assert D.a_notice(["Product Change Notification PCN-2023-118\nAffected parts"])
    assert D.a_notice(["End of Life Notification\nAffected parts: LM9999"])
    assert D.not_a_datasheet(["Product Discontinuation Notice\nLM9999"])
