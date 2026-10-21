"""Reader holds, 2026-10-21 (research: /mnt/project-files/benchmark/research-1021/reader_findings.md; live benchmark
2026-10-19): a hold names the field the footprint needed, a blank height builds with a note and no paid re-read,
depopulated dual rows (TLP2361: pins 1,3,4,5,6 of an SO-6; UCC256301: 14 pins on SOIC-16 places) build from the pin
table's numbers, and a dimension the read left out comes from the dimension table read in code when its symbol is
unambiguous. The extractions are SYNTHETIC (made-up parts), not read from any datasheet."""
import copy

from ecadlib import datasheet as D, ds_route as DR, footprint_gen as F
from tests.test_datasheet import _complete, dim, pin


def _so(name, nums, pin_count=None, **pkg):
    """A gullwing read (SO-6 / SOIC-16 style, 1.27 mm pitch) with the pin table's numbers `nums`."""
    per = max(int(n) for n in nums) // 2
    p = {"name": name, "description": "synthetic SO", "package_type": "gullwing", "pin_count": pin_count,
         "pitch": dim("e", None, 1.27, None, page=20), "body_length": dim("D", per * 1.27 + 0.3, None, per * 1.27 + 0.6, page=20),
         "body_width": dim("E1", 3.8, 3.9, 4.0, page=20), "lead_span": dim("E", 5.8, 6.0, 6.2, page=20),
         "terminal_length": dim("L", 0.4, None, 1.27, page=20), "terminal_width": dim("b", 0.31, None, 0.51, page=20),
         "height_max": dim("A", None, None, 1.75, page=20), "exposed_pad": None, "unresolved": [], "drawing": "synthetic"}
    p.update(pkg)
    ext = {"package": p, "pin_table": {"page": 3, "figure": "Pin Functions", "rows": len(nums)},
           "pins": [pin(n, f"P{n}", "io", "left" if int(n) <= per else "right") for n in nums],
           "symbol_description": "synthetic part", "notes": [], "embedded_instructions": []}
    return _complete(ext)


def run(ext, **kw):
    return D.process(copy.deepcopy(ext), "TESTPART", "https://example.com/t.pdf", **kw)


# ---------------------------------------------------------------- 1. the hold names what the builder needed
def test_the_hold_names_the_field_the_footprint_needed_not_the_unresolved_list():
    spec = {"name": "SO-6", "package_type": "gullwing", "pitch": 1.27, "unresolved": ["height_max"]}
    msg = D.no_footprint(spec, KeyError("pin_count"))
    assert msg.startswith("no footprint: the datasheet read left ") and "pin_count" in msg, msg
    assert "body_length" in msg and "lead_span" in msg, msg           # every key the builder needs, not only the first
    assert "height_max" not in msg.split("(")[0], msg                 # the unresolved list only as an aside
    import re
    from ecadlib.eval import runner
    assert re.search(r"no footprint: the datasheet read left", msg)   # eval's blank_dimension bucket still finds it
    assert runner is not None
    msg = D.no_footprint({"name": "X", "package_type": "chip", "body_length": 1, "body_width": 1,
                          "terminal_length": 1}, KeyError("weird"))
    assert "'weird' blank (and did not list it as unresolved)" in msg, msg


def test_a_blank_height_builds_with_a_note_and_triggers_no_re_read():
    ext = _so("SOIC-8_P1.27mm", [str(i) for i in range(1, 9)], 8, height_max=None, unresolved=["height_max"])
    res = run(ext)
    assert "height_max" not in res["spec"]
    assert any("height not read" in r for r in res["needs_review"]), res["needs_review"]
    kind, why = DR.assess(res)
    assert kind == "ok", (kind, why)                  # no wider pages / better model just for the 3D height
    F.build(res["spec"])
    ext["package"]["unresolved"] = ["height_max", "lead_span (not printed)"]
    ext["package"]["lead_span"] = None
    kind, why = DR.assess(run(ext))
    assert kind == "incomplete" and "lead_span" in why[0] and "height_max" not in why[0], why


# ---------------------------------------------------------------- 2. depopulated dual rows
def test_depopulated_so6_builds_pads_at_the_pin_table_positions():
    """TLP2361: SO-6 outline, pin 2 absent; pin_count left blank (name 6, table 5 disagree)."""
    res = run(_so("SO-6_P1.27mm", ["1", "3", "4", "5", "6"]))
    s = res["spec"]
    assert s["pin_count"] == 5, s.get("pin_count")
    pos = {str(p["number"]): (p["side"], p["offset"]) for p in s["pin_positions"]}
    assert pos == {"1": ("L", -1.27), "3": ("L", 1.27), "4": ("R", 1.27), "5": ("R", 0.0), "6": ("R", -1.27)}, pos
    assert any("pin_positions" in r and "2" in r for r in res["needs_review"]), res["needs_review"]
    kind, why = DR.assess(res)
    assert kind == "ok", (kind, why)
    _, rep = F.build(s)
    assert sorted(p["num"] for p in rep["pads"]) == ["1", "3", "4", "5", "6"], rep["pads"]


def test_depopulated_soic_14_on_16_places_from_the_pin_numbers():
    """UCC256301: SOIC-14-16, pins 1,3..12,14..16; the ordering table gives 14 pins."""
    nums = ["1"] + [str(i) for i in range(3, 13)] + ["14", "15", "16"]
    res = run(_so("SOIC-14-16_P1.27mm", nums), facts={"pins": 14, "page": 40, "part": "TESTPART",
                                                                 "package": "SOIC", "code": "DDB"})
    s = res["spec"]
    assert s["pin_count"] == 14 and len(s["pin_positions"]) == 14
    off = {str(p["number"]): p["offset"] for p in s["pin_positions"]}
    assert off["1"] == -4.445 and off["3"] == round(-4.445 + 2 * 1.27, 4) and off["16"] == -4.445, off
    assert DR.assess(res)[0] == "ok", DR.assess(res)
    _, rep = F.build(s)
    assert len(rep["pads"]) == 14


def test_a_depopulated_name_without_positions_is_never_built_as_a_full_row():
    """The latent bug: an ordering-table pin_count of 14 on SOIC-14-16 would build 7 pads per side."""
    res = run(_so("SOIC-14-16_P1.27mm", [str(i) for i in range(1, 15)], 14))   # numbers are not positions
    assert "pin_positions" not in res["spec"]
    assert DR.assess(res)[0] == "failed", DR.assess(res)
    try:
        F.build(res["spec"])
        raise AssertionError("built 7 pads per side for 14 pins on 16 places")
    except ValueError as e:
        assert "14-16" in str(e) or "16 places" in str(e), e


def test_depop_is_not_guessed_when_the_table_could_just_be_short():
    """Pins 1..5 of an SO-6 (the last one missing): a short table, not positions. Not built as 5 pads."""
    res = run(_so("SO-6_P1.27mm", ["1", "2", "3", "4", "5"]))
    assert "pin_positions" not in res["spec"]
    assert DR.assess(res)[0] != "ok"


# ---------------------------------------------------------------- 3. dims the read left out, from the table read in code
QFP_TABLE = {"page": 9, "unit": "mm", "rows": {
    "A": {"min": None, "nom": None, "max": 1.2, "span": False}, "A1": {"min": 0.05, "nom": None, "max": 0.15, "span": False},
    "D": {"min": 12.0, "nom": None, "max": None, "span": True}, "D1": {"min": 10.0, "nom": None, "max": None, "span": True},
    "E": {"min": 12.0, "nom": None, "max": None, "span": True}, "E1": {"min": 10.0, "nom": None, "max": None, "span": True},
    "L": {"min": 0.45, "nom": 0.6, "max": 0.75, "span": False}, "b": {"min": 0.17, "nom": 0.22, "max": 0.27, "span": False},
    "e": {"min": 0.5, "nom": None, "max": None, "span": True}}}


def _qfp_pkg():
    d = lambda lab, mn, nm, mx: {"label": lab, "min": mn, "nom": nm, "max": mx, "unit": "mm", "page": 3, "figure": ""}
    return {"package_type": "qfp", "body_length": d("D1", None, 10.0, None), "body_width": d("E1", None, 10.0, None),
            "pitch": d("e", None, 0.5, None), "lead_span": None, "terminal_length": None, "terminal_width": None,
            "height_max": None, "unresolved": ["height_max", "lead_span", "terminal_length", "terminal_width"]}


def test_blank_qfp_dims_are_filled_from_the_dimension_table():
    pkg, review = _qfp_pkg(), []
    D.table_check(pkg, [QFP_TABLE], [2, 5, 9], review)             # sent page 3 = page 9 of the PDF
    assert pkg["lead_span"]["nom"] == 12.0 and pkg["lead_span"]["label"] == "E" and pkg["lead_span"]["page"] == 3
    assert (pkg["terminal_length"]["min"], pkg["terminal_length"]["max"]) == (0.45, 0.75)
    assert pkg["terminal_width"]["label"] == "b" and pkg["height_max"]["max"] == 1.2
    assert pkg["unresolved"] == [], pkg["unresolved"]
    assert sum("filled from the dimension table p.9, read in code" in r for r in review) == 4, review
    assert not any(DR.INCOMPLETE.search(r) or DR.FAILED.search(r) for r in review), review


def test_no_fill_when_the_symbol_is_ambiguous_or_the_table_disagrees():
    t = copy.deepcopy(QFP_TABLE)
    t["rows"]["bp"] = {"min": 0.2, "nom": None, "max": 0.3, "span": False}     # b and bp: which is the lead width?
    t["rows"]["HE"] = {"min": 11.8, "nom": None, "max": 12.2, "span": False}   # E and HE: which is the span?
    pkg, review = _qfp_pkg(), []
    D.table_check(pkg, [t], [2, 5, 9], review)
    assert pkg["terminal_width"] is None and pkg["lead_span"] is None
    assert pkg["terminal_length"] is not None                      # L alone is not ambiguous
    assert "terminal_width" in pkg["unresolved"] and "lead_span" in pkg["unresolved"]
    t = copy.deepcopy(QFP_TABLE)
    t["rows"]["D1"] = {"min": 14.0, "nom": None, "max": None, "span": True}    # body disagrees: another package's
    pkg, review = _qfp_pkg(), []
    D.table_check(pkg, [t], [2, 5, 9], review)
    assert pkg["lead_span"] is None and pkg["terminal_length"] is None, pkg
    pkg = _qfp_pkg()                                               # one matched row only: not enough to trust it
    pkg["pitch"] = pkg["body_length"] = None
    D.table_check(pkg, [QFP_TABLE], [2, 5, 9], [])
    assert pkg["lead_span"] is None and pkg["terminal_length"] is None


def test_a_qfp_with_blank_dims_builds_after_the_table_fills_them():
    ext = {"package": dict(_qfp_pkg(), name="LQFP-64_10x10mm_P0.5mm", description="synthetic", pin_count=64,
                           pins_left_right=16, pins_top_bottom=16, exposed_pad=None, drawing="synthetic"),
           "pin_table": {"page": 2, "figure": "Pinout", "rows": 64},
           "pins": [pin(i, f"P{i}", "io") for i in range(1, 65)], "symbol_description": "synthetic MCU",
           "notes": [], "embedded_instructions": []}
    res = run(_complete(ext), offset=[2, 5, 9], tables=[QFP_TABLE])
    s = res["spec"]
    assert s["lead_span"] == {"nom": 12.0} and s["height_max"] == 1.2 and not s.get("unresolved"), s
    F.build(s)
