"""SC-74-6 (Nexperia SOT457, JEITA SC-74) is a checked table entry (benchmark 2026-10-25: IP4234CZ6 was read as a
1.5 x 2.9 mm TSOP-6, whose IPC pads came out 0.175 mm small against KiCad's SC-74-6_1.55x2.9mm_P0.95mm, and the BOM
names SC-74-6). The land is KiCad's hand-made one: pads 1.6 x 0.6 at +-1.15, pitch 0.95.
python3 -m tests.run_all test_stdparts_sc74_1025"""
from ecadlib import stdparts as SP

NAME = "SC-74-6_1.55x2.9mm_P0.95mm"


def test_sc74_names_pick_the_checked_land():
    for text in ("SC-74-6", "SC-74", "SOT457", "SOT-457", "SOT457 (SC-74-6)"):
        r = SP.resolve([text], "U", "IP4234CZ6")
        assert r["footprint"] == NAME, (text, r)
    assert SP.kicad_ref(NAME) == "Package_TO_SOT_SMD:" + NAME


def test_the_land_is_kicads():
    pads = {p["number"]: p for p in SP.packages()[NAME]["pad_table"]["pads"]}
    assert sorted(pads) == list("123456")
    assert all((abs(p["x"]), p["w"], p["h"]) == (1.15, 1.6, 0.6) for p in pads.values())
    assert (pads["1"]["x"], pads["1"]["y"], pads["4"]["x"], pads["4"]["y"]) == (-1.15, -0.95, 1.15, 0.95)
    text, _ = SP.build(NAME)
    assert text.count("(pad ") == 6


def test_sot26_stays_ambiguous():
    assert SP.resolve(["SOT-26"], "U")["footprint"] is None          # sold as SOT-23-6 and as SC-74
