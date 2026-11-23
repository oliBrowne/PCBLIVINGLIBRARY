"""A read that copies the maker's land pad by pad (custom / connector pad_table) is held until a source checks the pads
themselves (#279: SiT1533 pad 3 read 0.2 wide at 0.9 for the drawing's 0.4 at 0.8, released on a second read that
agreed on the body alone). The second read is now asked for the pad table too, and the two pad tables are compared
pad by pad: the same numbers, each pad's place and size within 0.05 mm, the whole footprint turned by a quarter turn
at most (never mirrored). Free 1000-part replay 2026-11-23: 17 crystals, switches, LEDs and connectors held for it.
python3 -m tests.run_all test_crosscheck_pad_table_1123"""
import copy
import json

from ecadlib import crosscheck as CC


def r(mn, nom, mx):
    return {"min": mn, "nom": nom, "max": mx}


SIT = {"unit": "mm", "y_axis": "up", "view": "top", "origin": "center", "footprint_origin": "center",
       "default_pad": {"type": "smd", "shape": "rect"},
       "pads": [{"number": "1", "x": -0.8, "y": 0, "w": 0.4, "h": 1.4}, {"number": "2", "x": -0.1, "y": -0.45, "w": 0.2, "h": 0.5},
                {"number": "3", "x": 0.8, "y": 0, "w": 0.4, "h": 1.4}, {"number": "4", "x": 0.1, "y": 0.45, "w": 0.2, "h": 0.5}]}


def first(table=SIT):
    return {"package_type": "custom", "name": "SiT1533", "pin_count": 4, "body_length": r(1.95, 2.0, 2.05),
            "body_width": r(1.15, 1.2, 1.25), "pad_table": copy.deepcopy(table)}


def second(table=SIT):
    s = {"package_type": "custom", "name": "SiT1533", "pin_count": 4, "body_length": {"nom": 2.0}, "body_width": {"nom": 1.2}}
    if table is not None:
        s["pad_table"] = copy.deepcopy(table)
    return s


def read(a, b):
    return CC.against_read(CC.critical(a), CC.critical(b), None, None)


def moved(table, num, **kw):
    t = copy.deepcopy(table)
    for p in t["pads"]:
        if p["number"] == num:
            p.update(kw)
    return t


def test_the_same_pads_agree_and_check_the_pads():
    c = read(first(), second())
    assert c["status"] == "agreed" and "pads" in c["agreed"], c
    first_c = CC.critical(first())
    assert CC.pad_checks([dict(c, source="second read")], first_c), c


def test_a_pad_read_at_another_place_or_size_disagrees():
    c = read(first(), second(moved(SIT, "3", x=0.9, w=0.2)))
    assert c["status"] == "disagreed" and any(d["field"] == "pads" for d in c["disagreed"]), c
    assert read(first(), second(moved(SIT, "2", h=0.6)))["status"] == "disagreed"


def test_small_rounding_still_agrees():
    assert read(first(), second(moved(SIT, "1", x=-0.79, w=0.41)))["status"] == "agreed"


def test_a_quarter_turn_and_another_origin_agree():
    t = copy.deepcopy(SIT)
    for p in t["pads"]:
        p["x"], p["y"] = -p["y"] + 5.0, p["x"] + 3.0                 # turned 90 degrees, origin off centre
        p["w"], p["h"] = p["h"], p["w"]
    t["origin"] = "datum"
    assert read(first(), second(t))["status"] == "agreed"


def test_a_mirror_image_disagrees():
    t = copy.deepcopy(SIT)
    for p in t["pads"]:
        p["y"] = -p["y"]                                             # bottom view taken for top: pads 2 and 4 swap sides
    assert read(first(), second(t))["status"] == "disagreed"


def test_other_pad_numbers_disagree():
    t = copy.deepcopy(SIT)
    t["pads"][3]["number"] = "5"
    assert read(first(), second(t))["status"] == "disagreed"


def test_no_pad_table_from_the_second_read_checks_no_pads():
    c = read(first(), second(None))
    assert "pads" in c["not_compared"], c
    assert not CC.pad_checks([dict(c, source="second read")], CC.critical(first()))


def test_the_check_asks_for_the_pad_table_only_when_the_read_placed_the_pads():
    plain, pads = CC.output_schema(), CC.output_schema(pads=True)
    assert "pad_table_json" not in plain["properties"]["package"]["properties"]
    assert "pad_table_json" in pads["properties"]["package"]["properties"]
    assert CC.version() != CC.version(pads=True) and "pad_table_json" in CC.system(pads=True)
    assert CC.system() == CC.SYSTEM and CC.version() == CC.version(False, False)   # other checks keep their cache


def test_the_second_reads_pad_table_is_parsed():
    ans = {"package": {"name": "x", "package_type": "custom", "pin_count": 4,
                       "dims": [{"name": "body_length", "label": "L", "min": "", "nom": "2.0", "max": "", "unit": "mm"}],
                       "pad_table_json": json.dumps(SIT)}, "pins": []}
    msg = {"content": [{"type": "text", "text": json.dumps(ans)}], "stop_reason": "end_turn"}
    crit, _, _ = CC.parse(msg)
    assert crit.get("pad_layout"), crit
    bad = dict(ans, package=dict(ans["package"], pad_table_json="{not json"))
    crit, _, _ = CC.parse({"content": [{"type": "text", "text": json.dumps(bad)}], "stop_reason": "end_turn"})
    assert not crit.get("pad_layout")


def test_two_pads_of_one_number_each_need_their_own_match():
    sw = {"unit": "mm", "y_axis": "up", "view": "top", "origin": "center",
          "pads": [{"number": "1", "x": -3, "y": 2, "w": 1, "h": 1}, {"number": "1", "x": -3, "y": -2, "w": 1, "h": 1},
                   {"number": "2", "x": 3, "y": 2, "w": 1, "h": 1}, {"number": "2", "x": 3, "y": -2, "w": 1, "h": 1}]}
    a = dict(first(sw), pin_count=2, package_type="custom")
    assert read(a, dict(second(sw), pin_count=2))["status"] == "agreed"
    bad = copy.deepcopy(sw)
    bad["pads"][1].update(x=-3, y=2)                                 # both pad 1s at one place
    assert read(a, dict(second(bad), pin_count=2))["status"] == "disagreed"


def test_a_pad_table_with_an_unnumbered_pad_checks_no_pads():
    t = copy.deepcopy(SIT)
    t["pads"][2]["number"] = ""
    c = read(first(), second(t))
    assert "pads" in c["not_compared"] and "pads" not in c["agreed"], c
    assert "do not number pads in reading order" in CC.system(pads=True)


# ---------------------------------------------------------------- review 2026-11-23 (pad-by-pad check)
def test_a_pad_turned_by_its_rotation_is_compared_as_built():
    t = moved(SIT, "1", rotation=90)                                  # built as 1.4 x 0.4
    assert read(first(t), second())["status"] == "disagreed"
    t2 = moved(moved(SIT, "1", w=1.4, h=0.4), "1", rotation=90)       # 1.4 x 0.4 turned: 0.4 x 1.4 again
    assert read(first(t2), second())["status"] == "agreed"
    assert CC.pad_layout(first(moved(SIT, "1", rotation=45))) is None


def test_holes_polygons_and_npth_are_not_compared():
    tht = copy.deepcopy(SIT)
    tht["default_pad"] = {"type": "tht", "shape": "circle"}
    for p in tht["pads"]:
        p["drill"] = 0.1
    assert CC.pad_layout(first(tht)) is None
    poly = copy.deepcopy(SIT)
    poly["pads"][0] = {"number": "1", "polygon": [{"x": -1.0, "y": -0.7}, {"x": -0.6, "y": -0.7}, {"x": -0.8, "y": 0.7}]}
    assert CC.pad_layout(first(poly)) is None
    npth = copy.deepcopy(SIT)
    npth["pads"].append({"type": "npth", "x": 5, "y": 5, "drill": 1.0})
    assert CC.pad_layout(first(npth)) is None
    c = read(first(tht), second())
    assert "pads" not in c["agreed"], c


def test_a_span_0_1_mm_longer_disagrees():
    two = {"unit": "mm", "y_axis": "up", "view": "top", "origin": "center",
           "pads": [{"number": "1", "x": -1.0, "y": 0, "w": 0.6, "h": 1.0}, {"number": "2", "x": 1.0, "y": 0, "w": 0.6, "h": 1.0}]}
    wide = copy.deepcopy(two)
    wide["pads"][1]["x"] = 1.1
    a, b = dict(first(two), pin_count=2), dict(second(wide), pin_count=2)
    assert read(a, b)["status"] == "disagreed"
    sit = moved(SIT, "3", x=0.9)                                       # #279's own place error, width aside
    assert read(first(), second(sit))["status"] == "disagreed"


def test_a_hostile_pad_table_never_stops_the_read():
    deep = "[" * 20000 + "]" * 20000
    huge = json.dumps({"unit": "mm", "arrays": [{"count": 3000000, "x": 0, "y": 0, "dx": 0.1, "dy": 0, "first_number": 1,
                                                  "w": 0.1, "h": 0.1}]})
    for pt in (deep, huge, json.dumps({"pads": ["x", 1]})):
        ans = {"package": {"name": "x", "package_type": "custom", "pin_count": 4,
                           "dims": [{"name": "body_length", "label": "L", "min": "", "nom": "2.0", "max": "", "unit": "mm"}],
                           "pad_table_json": pt}, "pins": []}
        crit, _, _ = CC.parse({"content": [{"type": "text", "text": json.dumps(ans)}], "stop_reason": "end_turn"})
        assert not crit.get("pad_layout") and crit.get("body_length"), crit
