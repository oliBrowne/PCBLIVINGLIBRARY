"""A tab package's maker land gives the tab's own pad too (BAT160A / BAT160C Nexperia SOT223 reflow footprint: 1.2 mm
leads with a 3.6 x 1.2 mm tab land; LP38693MP-1.8 TI NDC0005A: 1 x 1.5 mm leads, 3.3 x 1.5 mm tab land). With
land.tab_length / tab_width / tab_center read, the tab is built from the maker's land like the leads, not held; with
the leads' land alone it is still held (tab_land_unread). Numbers here are SYNTHETIC, laid out like those lands.
python3 -m tests.run_all test_tab_land_1024"""
import re

from ecadlib import datasheet as D, footprint_gen as FG


def sot223(**land):
    return {"package_type": "gullwing", "name": "SOT-223-3_TabPin4", "pin_count": 4, "pitch": 2.3,
            "lead_span": {"min": 6.7, "nom": 7.0, "max": 7.3}, "body_length": {"min": 6.3, "nom": 6.5, "max": 6.7},
            "body_width": {"min": 3.3, "nom": 3.5, "max": 3.7}, "terminal_length": {"min": 0.75, "nom": 0.9, "max": 1.05},
            "terminal_width": {"min": 0.6, "nom": 0.7, "max": 0.8}, "height_max": 1.8,
            "pin_positions": [{"number": str(n), "side": "L", "offset": o} for n, o in ((1, -2.3), (2, 0.0), (3, 2.3))],
            "tab": {"number": "4", "side": "R", "offset": 0, "width": {"min": 2.9, "nom": 3.0, "max": 3.1}},
            "land": dict({"pad_length": 2.0, "pad_width": 1.2, "pad_center": 3.15}, **land)}


def pads(text):
    return {m[0]: tuple(map(float, m[1:])) for m in
            re.findall(r'\(pad "?(\w+)"? smd \w+ \(at ([-\d.]+) ([-\d.]+)(?: [-\d.]+)?\) \(size ([\d.]+) ([\d.]+)\)', text)}


def test_the_tab_is_built_from_the_makers_tab_land():
    s = sot223(tab_length=2.0, tab_width=3.6, tab_center=3.15)
    assert D.tab_land_unread(s) is None
    p = pads(FG.build(s)[0])
    assert p["4"] == (3.15, 0.0, 2.0, 3.6), p
    assert p["1"][0] == -3.15 and p["1"][2:] == (2.0, 1.2), p


def test_a_tab_land_with_its_own_centre_and_the_leads_land_alone():
    p = pads(FG.build(sot223(tab_length=3.3, tab_width=1.5, tab_center=2.5))[0])
    assert p["4"] == (2.5, 0.0, 3.3, 1.5), p
    assert D.tab_land_unread(sot223()), "the leads' land alone still holds"
    assert D.tab_land_unread(sot223(tab_length=2.0, tab_width=3.6)), "a tab land with no centre is not placed"


def test_the_schema_offers_the_tab_land():
    props = D.SCHEMA["properties"]["land"]["properties"]
    assert {"tab_length", "tab_width", "tab_center"} <= set(props)


def test_the_scorer_counts_a_tab_on_the_makers_tab_land_as_the_makers_land():
    from ecadlib.eval import score as S
    s = sot223(tab_length=2.0, tab_width=3.6, tab_center=3.15)
    leads = [[str(n), -3.15, y, 2.0, 1.2, 0.0] for n, y in ((1, -2.3), (2, 0.0), (3, 2.3))]
    mine = {"pads": leads + [["4", 3.15, 0.0, 2.0, 3.6, 0.0]], "courtyard": [-4.4, -3.6, 4.4, 3.6]}
    ml = S.maker_land(mine, s)
    assert ml["followed"] and "4" in ml["pads"], ml
    off = {"pads": leads + [["4", 3.15, 0.0, 2.15, 3.3, 0.0]], "courtyard": mine["courtyard"]}   # an IPC tab
    assert "4" not in S.maker_land(off, s)["pads"]
    assert "4" not in S.maker_land(mine, sot223())["pads"]   # no tab land read: the tab is no maker's pad


def test_a_tab_land_with_its_own_centre_scores_as_the_makers_land():
    # Greptile #181: the part's centre is not the pads' box midpoint when the tab sits at its own tab_center
    from ecadlib.eval import score as S
    s = sot223(tab_length=3.3, tab_width=1.5, tab_center=2.5)
    leads = [[str(n), -3.15, y, 2.0, 1.2, 0.0] for n, y in ((1, -2.3), (2, 0.0), (3, 2.3))]
    mine = {"pads": leads + [["4", 2.5, 0.0, 3.3, 1.5, 0.0]], "courtyard": [-4.4, -3.6, 4.4, 3.6]}
    ml = S.maker_land(mine, s)
    assert ml["followed"] and "4" in ml["pads"], ml
    shifted = {"pads": leads + [["4", 2.9, 0.0, 3.3, 1.5, 0.0]], "courtyard": mine["courtyard"]}
    assert "4" not in S.maker_land(shifted, s)["pads"]
