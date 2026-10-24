"""A read that places the pins with pin_positions is checked for which pins share a side (AH1806-W, 10-23 live run:
Diodes SC59, pin 2 alone on one side and pins 1 and 3 on the other; the read gave the SOT-23 order 1, 2 left and 3
right, the second read was never asked where the pins sit, and the part was built with pins 2 and 3 swapped). The
second read is asked for the pins along each side only when the first read placed them, so every other part's cached
check keeps its key. The comparison is free of the drawing's frame: which pins share a side and in what order along
it, the same seen from the top, turned or from the other end.
python3 -m tests.run_all test_crosscheck_pin_positions_1024"""
import json

from ecadlib import crosscheck as CC
from tests import test_datasheet as TD


def sot(pos, name="SOT-23"):
    return {"package_type": "gullwing", "name": name, "pin_count": len({str(p[0]) for p in pos}), "pitch": 0.95,
            "pin_positions": [{"number": str(n), "side": s, "offset": o} for n, s, o in pos]}


SOT23 = [(1, "L", -0.95), (2, "L", 0.95), (3, "R", 0)]
SC59_AH1806 = [(1, "L", -0.95), (3, "L", 0.95), (2, "R", 0)]       # the datasheet's: 2 alone, 1 and 3 together


def rows(*sides, n=None):
    return {"package_type": "gullwing", "name": "SC59", "pin_count": n or sum(map(len, sides)), "pitch": 0.95,
            "pin_rows": [{"pins": [str(n) for n in s]} for s in sides]}


def check(first, second):
    return CC.against_read(CC.critical(first), CC.critical(second), None, None)


def test_the_ah1806_swap_holds():
    c = check(sot(SOT23), rows([1, 3], [2]))
    assert c["status"] == "disagreed", c
    (d,) = [d for d in c["disagreed"] if d["field"] == "pin_positions"]
    assert d["first"] == "1 2 | 3" and d["second"] == "1 3 | 2", d


def test_the_same_pins_seen_turned_or_from_the_other_end_agree():
    for second in (rows([1, 2], [3]), rows([3], [2, 1]), rows([2, 1], [3])):
        c = check(sot(SOT23), second)
        assert c["status"] == "agreed" and "pin_positions" in c["agreed"], (second, c)


def test_order_along_a_side_counts():
    five = [(1, "L", -0.95), (2, "L", 0), (3, "L", 0.95), (4, "R", 0.95), (5, "R", -0.95)]
    assert check(sot(five, "SOT-23-5"), rows([1, 2, 3], [4, 5]))["status"] == "agreed"
    c = check(sot(five, "SOT-23-5"), rows([1, 3, 2], [4, 5]))
    assert c["status"] == "disagreed" and "pin_positions" in {d["field"] for d in c["disagreed"]}


def test_a_tab_or_pin_the_first_read_does_not_place_is_left_out():
    # D2PAK: the read places leads 1 and 3 (2 is cut); a second read listing the tab and the cut lead still agrees
    first = dict(sot([(1, "L", -2.54), (3, "L", 2.54)], "TO-263-2"), pitch=2.54)
    assert check(first, dict(rows([1, 2, 3], [4], n=2), pitch=2.54))["status"] == "agreed"


def test_a_second_read_without_rows_is_not_compared():
    c = check(sot(SOT23), {"package_type": "gullwing", "name": "SC59", "pin_count": 3, "pitch": 0.95})
    assert "pin_positions" in c["not_compared"] and c["status"] == "agreed", c


def test_only_a_placed_read_asks_for_rows_and_its_key_is_its_own():
    _, plain = CC.build_request(("url", "https://a/ds.pdf"), "P", None, "claude-opus-5-5", "high")
    _, placed = CC.build_request(("url", "https://a/ds.pdf"), "P", None, "claude-opus-5-5", "high", positions=True)
    assert "pin_rows" not in json.dumps(plain["output_config"]) and "pin_rows" in json.dumps(placed["output_config"])
    assert plain["system"][0]["text"] == CC.SYSTEM and placed["system"][0]["text"] != CC.SYSTEM
    assert placed["system"][0]["text"].startswith("You check the package")
    assert CC.version() == "9ecaae641981"                       # every cached check of an unplaced read keeps its key
    assert CC.version(True) != CC.version()


def test_the_answer_rows_are_read_back():
    ans = {"package": {"name": "SC59", "package_type": "gullwing", "pin_count": 3, "dims": [], "unresolved": [],
                       "pin_rows": [{"pins": ["1", "3"]}, {"pins": ["2"]}]},
           "pins": [{"num": "1", "name": "VDD"}, {"num": "2", "name": "OUT"}, {"num": "3", "name": "GND"}],
           "embedded_instructions": []}
    second, pins, _ = CC.parse(TD.message(ans))
    assert second["sides"] == [("1", "3"), ("2",)] and len(pins) == 3


class Reader:
    cfg, effort = {}, "high"

    def __init__(self, ans):
        self.ans, self.bodies = ans, []

    def check_message(self, job, model, headers, body, key, pages=None):
        self.bodies.append((key, body))
        return TD.message(self.ans), "api"


def test_second_read_asks_for_rows_when_the_first_read_placed_pins():
    ans = {"package": {"name": "SC59", "package_type": "gullwing", "pin_count": 3, "dims": [], "unresolved": [],
                       "pin_rows": [{"pins": ["1", "3"]}, {"pins": ["2"]}]}, "pins": [], "embedded_instructions": []}
    rd = Reader(ans)
    rec = CC.second_read(rd, {"url": "https://a/ds.pdf", "part": "AH1806-W"}, CC.critical(sot(SOT23)), None, "claude-opus-5-5")
    assert rec["status"] == "disagreed" and "pin_rows" in json.dumps(rd.bodies[0][1]["output_config"])
    rd2 = Reader(ans)
    CC.second_read(rd2, {"url": "https://a/ds.pdf", "part": "X"}, CC.critical(sot(SOT23) | {"pin_positions": None}),
                   None, "claude-opus-5-5")
    assert "pin_rows" not in json.dumps(rd2.bodies[0][1]["output_config"])
    assert rd.bodies[0][0] != rd2.bodies[0][0].replace("X", "AH1806-W")


def test_a_standard_table_alone_does_not_settle_where_the_pins_sit():
    # KiCad's SOT-23 table has the SOT-23 numbering the AH1806-W read copied: it cannot confirm the maker's order
    ans = {"package": {"name": "SC59", "package_type": "gullwing", "pin_count": 3, "dims": [], "unresolved": [],
                       "pin_rows": [{"pins": ["1", "3"]}, {"pins": ["2"]}]}, "pins": [], "embedded_instructions": []}
    rd = Reader(ans)
    spec = dict(sot(SOT23), lead_span={"nom": 2.4}, body_length={"nom": 2.9}, body_width={"nom": 1.3})
    out = CC.run(rd, {"url": "https://a/ds.pdf", "part": "AH1806-W", "package": "SOT-23"},
                 {"spec": spec, "symbol": {"pins": []}}, "claude-opus-5-5")
    assert rd.bodies and out["status"] == "disagreed", out


class NoAnswer(Reader):
    def check_message(self, job, model, headers, body, key, pages=None):
        self.bodies.append((key, body))
        raise CC.Unavailable("no answer: no key for the second read")


def test_placed_pins_with_no_second_read_are_unchecked_even_when_a_table_agrees():
    # Greptile #179: a strong table match must not mark the maker's pin order checked when the second read is missing
    spec = dict(sot(SOT23), lead_span={"nom": 2.4}, body_length={"nom": 2.9}, body_width={"nom": 1.3})
    job = {"url": "https://a/ds.pdf", "part": "AH1806-W", "package": "SOT-23"}
    out = CC.run(NoAnswer(None), job, {"spec": spec, "symbol": {"pins": []}}, "claude-opus-5-5")
    assert out["status"] == "unchecked", out
    ans = {"package": {"name": "SC59", "package_type": "gullwing", "pin_count": 3, "dims": [], "unresolved": [],
                       "pin_rows": []}, "pins": [], "embedded_instructions": []}
    out = CC.run(Reader(ans), job, {"spec": spec, "symbol": {"pins": []}}, "claude-opus-5-5")
    assert out["status"] == "unchecked", out             # an answer with no rows confirms nothing about them either
