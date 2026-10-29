"""Merged pads and per-side pins (2026-10-29). Two read fields the schema lacked:
- pin_sides: the pin numbers along each side of a no-lead package as the pin figure shows them ("" for a lead place
  with no pin), so rows that differ, a removed lead, or a pin read onto the wrong side (R-FX12-2a) can be built or
  caught;
- joined_pins (+ land.ep_center): leads that are one piece of metal with the exposed pad (a MOSFET's drain leads),
  built as the one pad of the maker's land, numbered with the lowest of them, and the symbol given one pin for it.
Old cached answers (read before these fields) still replay: the result cache falls back to the schema they were read
under. The specs are SYNTHETIC, except the KiCad TDSON-8-1 reference the merged pad is checked against."""
import json
from pathlib import Path

from ecadlib import crosscheck as CC, datasheet as D, footprint_bench as B, footprint_gen as F
from ecadlib.eval import score as S
from tests.fixtures_lite import lite

SET = Path(__file__).resolve().parents[1] / "ecadlib" / "eval" / "sets" / "kicad-v1.json"


def pads(spec):
    return {p[0]: tuple(round(v, 4) for v in p[1:5]) for p in B.numbers(F.build(spec)[0])["pads"]}


def dfn8(**kw):
    s = {"name": "DFN-8-1EP_3x3mm_P0.65mm", "package_type": "qfn", "pin_count": 8, "pitch": 0.65,
         "body_length": {"nom": 3.0}, "body_width": {"nom": 3.0}, "terminal_length": {"min": 0.3, "nom": 0.4, "max": 0.5},
         "terminal_width": {"min": 0.25, "max": 0.35}, "exposed_pad": {"length": {"nom": 1.5}, "width": {"nom": 2.4}}}
    s.update(kw)
    return s


# ---------------------------------------------------------------- pin_sides: the builder
def test_pin_sides_that_match_the_default_build_the_same_pads():
    a = pads(dfn8(pins_left_right=4, pins_top_bottom=0))
    b = pads(dfn8(pin_sides={"left": ["1", "2", "3", "4"], "bottom": [], "right": ["5", "6", "7", "8"], "top": []}))
    assert a == b, (a, b)
    assert F.rows(dfn8(pin_sides={"left": ["1", "2", "3", "4"], "right": ["5", "6", "7", "8"]})) == (4, 0)


def test_a_lead_place_with_no_pin_keeps_the_other_pads_in_their_places():
    full = pads(dfn8(pins_left_right=4, pins_top_bottom=0))
    gap = pads(dfn8(pin_count=7, pin_sides={"left": ["1", "2", "3", "4"], "right": ["5", "", "7", "8"]},
                    exposed_pad={"length": {"nom": 1.5}, "width": {"nom": 2.4}, "number": "9"}))
    assert "6" not in gap and len(gap) == 8, sorted(gap)            # 7 leads and the exposed pad
    for k in ("1", "4", "5", "7", "8"):
        assert gap[k] == full[k], (k, gap[k], full[k])


def test_pin_sides_that_do_not_run_counter_clockwise_are_refused():
    # review 2026-10-29: the right row written top to bottom (8 7 6 5 read as 5 6 7 8) would build every pad on the
    # other's place; pins run counter-clockwise from pin 1 on every real package, so anything else is a misread
    for sides in ({"left": ["1", "2", "3", "4"], "right": ["8", "7", "6", "5"]},
                  {"left": ["1", "2", "3", "4"], "right": ["6", "5", "7", "8"]},
                  {"left": ["1", "2", "3", "4"], "right": ["A", "B", "C", "D"]}):
        probs = F.sanity(dfn8(pin_sides=sides))
        assert any("counter-clockwise" in p for p in probs), (sides, probs)


def test_a_pin_sides_contradiction_is_a_hard_failed_read():
    from ecadlib import ds_route as DR
    review = []
    D.sanity(dfn8(pin_sides={"left": ["1", "2", "3", "4"], "right": ["5", "6", "7"]}, pin_count=7), review)
    hard = DR.hard_failures([r for r in review if r.startswith("pin_sides")])
    assert hard, review


def test_a_side_with_no_leads_and_rows_of_their_own_length():
    s = {"name": "QFN-10_2x3mm_P0.5mm", "package_type": "qfn", "pin_count": 10, "pitch": 0.5,
         "body_length": {"nom": 2.0}, "body_width": {"nom": 3.0}, "terminal_length": {"nom": 0.4},
         "terminal_width": {"nom": 0.25},
         "pin_sides": {"left": ["1", "2", "3", "4"], "bottom": ["5", "6"], "right": ["7", "8", "9", "10"], "top": []}}
    got = pads(s)
    assert len(got) == 10 and got["5"][1] > 0 and got["6"][1] > 0, got   # bottom row, nothing on top
    assert abs(got["5"][0] + got["6"][0]) < 1e-6                        # centred on its side


def test_uneven_opposite_rows_are_refused_not_guessed():
    s = dfn8(pin_count=7, pin_sides={"left": ["1", "2", "3", "4"], "right": ["5", "6", "7"]})
    probs = F.sanity(s)
    assert any("pin_sides" in p and "lead place" in p for p in probs), probs


def test_pin_sides_that_do_not_add_up_are_refused():
    bad = [
        ({"left": ["1", "2", "3", "4"], "right": ["5", "6", "7", "7"]}, "twice"),
        ({"left": ["1", "2", "3", "4"], "right": ["5", "6", "7", ""]}, "pin_count"),
        ({"left": ["2", "1", "3", "4"], "right": ["5", "6", "7", "8"]}, "pin 1"),
    ]
    for sides, word in bad:
        probs = F.sanity(dfn8(pin_sides=sides))
        assert any("pin_sides" in p and word in p for p in probs), (sides, probs)


def test_pin_sides_against_a_row_split_that_says_otherwise_is_a_failed_read():
    # R-FX12-2a: QFN-24 4x5 7/5 read 6/6 looks like a real part by its margins; the figure's own sides tell them apart
    s = {"name": "QFN-24_4x5mm_P0.5mm", "package_type": "qfn", "pin_count": 24, "pitch": 0.5,
         "body_length": {"nom": 4.0}, "body_width": {"nom": 5.0}, "terminal_length": {"nom": 0.4},
         "terminal_width": {"nom": 0.25}, "pins_left_right": 6, "pins_top_bottom": 6,
         "pin_sides": {"left": [str(i) for i in range(1, 8)], "bottom": [str(i) for i in range(8, 13)],
                       "right": [str(i) for i in range(13, 20)], "top": [str(i) for i in range(20, 25)]}}
    probs = F.sanity(s)
    assert any("pins_left_right" in p and "pin_sides" in p for p in probs), probs


def test_crosscheck_sides_read_pin_sides():
    s = dfn8(pin_sides={"left": ["1", "2", "3", "4"], "bottom": [], "right": ["5", "", "7", "8"], "top": []})
    assert CC.sides(s) == [("1", "2", "3", "4"), ("5", "7", "8")], CC.sides(s)
    assert CC.sides(dfn8()) is None                                      # no figure read: as before


# ---------------------------------------------------------------- joined_pins: one merged pad from the maker's land
def tdson(**kw):
    """Infineon PG-TDSON-8 in the builder's frame (rows left / right): leads 5-8 run into the drain pad. The land is
    KiCad's TDSON-8-1 (4 pads 0.85 x 0.5 at 2.9; the drain pad 4.55 x 4.41, 1.05 off the centre)."""
    s = {"name": "TDSON-8-1", "package_type": "qfn", "pin_count": 8, "pitch": 1.27,
         "body_length": {"min": 5.7, "max": 6.1}, "body_width": {"min": 4.8, "max": 5.35},
         "terminal_length": {"min": 0.45, "max": 0.65}, "terminal_width": {"min": 0.34, "max": 0.54},
         "exposed_pad": {"length": {"min": 4.05, "max": 4.25}, "width": {"min": 3.9, "max": 4.4}},
         "pin_sides": {"left": ["1", "2", "3", "4"], "bottom": [], "right": ["5", "6", "7", "8"], "top": []},
         "joined_pins": ["5", "6", "7", "8"],
         "land": {"pad_length": 0.85, "pad_width": 0.5, "pad_center": 2.9, "ep_across": 4.55, "ep_along": 4.41,
                  "ep_center": 1.05}}
    s.update(kw)
    return s


def kicad(name):
    return json.loads(SET.read_text(encoding="utf-8"))["references"][name]


def test_joined_leads_and_pad_build_as_the_makers_one_pad():
    got = pads(tdson())
    ref = {p[0]: tuple(p[1:5]) for p in kicad("Package_TO_SOT_SMD:TDSON-8-1")["pads"]}
    assert sorted(got) == sorted(ref), (sorted(got), sorted(ref))
    for k, r in ref.items():
        assert all(abs(a - b) < 1e-3 for a, b in zip(got[k], r)), (k, got[k], r)


def test_the_merged_pad_scores_against_kicads():
    ours = B.numbers(F.build(tdson())[0])
    fields = S.score_footprint(ours, kicad("Package_TO_SOT_SMD:TDSON-8-1"))
    bad = {k: f["error"] for k, f in fields.items() if not f["ok"] and k not in S.INFO_ONLY and k != "courtyard"}
    assert not bad, bad


def test_joined_pins_with_the_land_lift_the_joined_lead_hold():
    pins = [{"num": str(i), "name": n} for i, n in enumerate("SSSGDDDD", 1)] + [{"num": "9", "name": "D"}]
    assert D.joined_to_pad(tdson(), pins) is None
    old = {k: v for k, v in tdson().items() if k not in ("joined_pins", "pin_sides")}   # a read from before the field
    old["pins_left_right"] = 4                                           # (two_sided: a TDSON's two rows)
    assert D.joined_to_pad(old, pins)                                    # is held as it always was


def test_joined_pins_without_the_makers_land_stay_held():
    pins = [{"num": str(i), "name": n} for i, n in enumerate("SSSGDDDD", 1)] + [{"num": "9", "name": "D"}]
    s = tdson(land={"pad_length": 0.85, "pad_width": 0.5, "pad_center": 2.9})
    line = D.joined_to_pad(s, pins)
    assert line and "land" in line, line
    try:
        F.build(s)
    except ValueError as e:
        assert "joined" in str(e) and "land" in str(e), e
    else:
        raise AssertionError("a merged pad was built with no land to size it")


def test_joined_pins_that_are_not_one_whole_side_are_refused():
    for joined in (["5", "6", "7"], ["4", "5", "6", "7", "8"], ["9"]):
        probs = F.sanity(tdson(joined_pins=joined))
        assert any("joined_pins" in p for p in probs), (joined, probs)


def test_a_merged_land_that_misses_its_leads_is_refused():
    s = tdson(land=dict(tdson()["land"], ep_center=0.0))                 # centred: short of leads 5-8 by 1 mm
    try:
        F.build(s)
    except ValueError as e:
        assert "joined" in str(e) and "lead" in str(e), e
    else:
        raise AssertionError("a merged pad that leaves its leads bare was built")


def test_maker_land_follows_an_off_centre_merged_pad():
    ours = B.numbers(F.build(tdson())[0])
    ml = S.maker_land(ours, tdson())
    assert ml and ml["followed"] and "5" in ml["ep_land"], ml


def ext_for(spec, pins):
    pkg = {k: None for k in D.SCHEMA["properties"] if k not in D.SKIP}
    pkg.update({"name": spec["name"], "description": "", "package_type": spec["package_type"], "drawing": "PG-TDSON-8",
                "unresolved": []})
    for k, v in spec.items():
        if k in D.DIMS or k in D.NUM_DIMS or k == "exposed_pad":
            continue
        pkg[k] = v
    raw = lambda d: dict({x: d.get(x) for x in ("min", "nom", "max")}, label="X", unit="mm", page=1, figure="F")
    for k in D.DIMS:
        if spec.get(k):
            pkg[k] = raw(spec[k])
    for k in D.NUM_DIMS:
        if spec.get(k):
            pkg[k] = raw({"nom": spec[k]})
    pkg["exposed_pad"] = {x: raw(spec["exposed_pad"][x]) for x in ("length", "width")}
    return {"package": pkg, "pin_table": {"page": 1, "figure": "pins", "rows": spec["pin_count"]},
            "pins": [dict(p, type="passive", side="left", page=1) for p in pins], "symbol_description": "MOSFET",
            "notes": [], "embedded_instructions": []}


def test_the_symbol_gets_one_pin_for_the_merged_pad():
    pins = [{"num": str(i), "name": n} for i, n in enumerate("SSSGDDDD", 1)] + [{"num": "9", "name": "D"}]
    res = D.process(ext_for(tdson(), pins), "BSC000N00", "http://example.com/ds.pdf")
    got = [(p["num"], p["name"]) for p in res["symbol"]["pins"]]
    assert got == [("1", "S"), ("2", "S"), ("3", "S"), ("4", "G"), ("5", "D")], got
    bad = [r for r in res["needs_review"] if r.startswith(("symbol:", "footprint_gen"))]
    assert not bad, bad
    assert len(res["sidecar"]["pins"]) == 9 and res["sidecar"]["merged_pins"] == ["1", "2", "3", "4", "5"]


def test_merged_pins_named_apart_are_a_failed_read():
    pins = [{"num": str(i), "name": n} for i, n in enumerate("SSSGDDGD", 1)] + [{"num": "9", "name": "D"}]
    res = D.process(ext_for(tdson(), pins), "BSC000N00", "http://example.com/ds.pdf")
    line = next((r for r in res["needs_review"] if r.startswith("joined_pins:")), "")
    assert "implausible" in line, res["needs_review"]
    assert len(res["symbol"]["pins"]) == 9                            # not merged: the read is failed, not rewired


# ---------------------------------------------------------------- old cached answers still replay
def test_the_schema_before_these_fields_is_a_known_legacy_version():
    assert D.legacy_schema_version() == "af365d52d7c1"           # main before 2026-10-29: no pin_sides / joined_pins
    assert D.schema_version() != D.legacy_schema_version()


@lite
def test_a_cached_answer_read_under_the_legacy_schema_is_replayed(tmp_path):
    rc = D.ResultCache(tmp_path)
    key = D.ResultCache.key("ab" * 16, "claude-opus-5-5", "PART", None, [0, None], "high")
    old = key.replace(f"-{D.schema_version()}-", f"-{D.legacy_schema_version()}-")
    assert old != key
    rc.put(old, {"message": {"content": []}, "model": "claude-opus-5-5"})
    got = rc.get(key)
    assert got and got.get("legacy_schema") == D.legacy_schema_version(), got
    rc.put(key, {"message": {"content": [{"type": "text", "text": "new"}]}})
    assert "legacy_schema" not in rc.get(key)                         # a read under this schema wins


def test_the_rule_book_counts_the_leads_a_merged_pad_took_in():
    from ecadlib import footprint_rules as R
    for name in ("TDSON-8-1EP_5.15x5.9mm_P1.27mm_EP4.15x4.15mm", "TDSON-8_5.15x5.9mm_P1.27mm"):
        txt = F.build(tdson(name=name))[0]
        assert "joined_pins=5,6,7,8" in txt
        bad = [m for lvl, rule, m in R.check(txt) if lvl == "error"]
        assert not bad, (name, bad)
    plain = F.build(dfn8(name="DFN-8-1EP_3x3mm_P0.65mm", pins_left_right=4, pins_top_bottom=0))[0]
    assert "joined_pins" not in plain


def test_the_merged_pad_does_not_put_rows_on_the_wrong_axis():
    # review 2026-10-29: a QFN-16 merged on its bottom side measured the right row against the pad as a top/bottom row
    s = {"name": "QFN-16-1EP_4x4mm_P0.65mm", "package_type": "qfn", "pin_count": 16, "pitch": 0.65,
         "body_length": {"nom": 4.0}, "body_width": {"nom": 4.0}, "terminal_length": {"nom": 0.4},
         "terminal_width": {"nom": 0.3}, "exposed_pad": {"length": {"nom": 2.2}, "width": {"nom": 2.2}},
         "joined_pins": ["5", "6", "7", "8"],
         "land": {"ep_across": 2.2, "ep_along": 3.6, "ep_center": 0.7}}
    txt, rep = F.build(s)
    assert "warning" not in rep, rep.get("warning")
    assert all(v is None or v > 0 for v in rep["ep_room"]), rep["ep_room"]


def test_a_merged_pad_whose_number_is_a_lead_is_not_merged():
    s = tdson(pin_count=7, pin_sides={"left": ["1", "2", "3", "4"], "right": ["", "6", "7", "8"]},
              joined_pins=["6", "7", "8"])
    pins = [{"num": str(i), "name": n} for i, n in zip((1, 2, 3, 4, 6, 7, 8), "SSSGDDD")] + [{"num": "9", "name": "D"}]
    review = []
    assert D.merge_pins(s, pins, review) is None and any("implausib" in r for r in review), review
