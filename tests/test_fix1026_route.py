"""Fixes 2026-10-26 (route): review route.md R-RT-1..5 on PR #196. Offline: no key, no network, no model.
    python3 -m tests.run_all test_fix1026_route"""
from ecadlib import ds_route as DR
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-RT-1
OVERLAP = "pad_table: pads 1 and 2 overlap by 0.700 mm"


def test_rrt1_a_hard_failure_is_held_even_when_a_field_is_also_unresolved():
    """assess() calls a read 'incomplete' whenever a field is unresolved, and the hold only looked at 'failed': a read
    whose pads overlap was built as long as one field was also left blank (pitch doubled + one unresolved field)."""
    res = {"needs_review": [OVERLAP], "spec": {"unresolved": ["lead_span (not printed)"]}}
    assert DR.assess(res)[0] == "incomplete"
    assert DR.hold_reasons(res) == [OVERLAP]
    res3 = {"needs_review": [OVERLAP, "pin 3: has no source page"], "spec": {}}
    assert DR.hold_reasons(res3) == [OVERLAP]
    # the incomplete lines themselves never hold, nor does an unresolved field named like a failed check
    res4 = {"needs_review": ["pin 3: has no source page"], "spec": {"unresolved": ["lead_span min (not printed)"]}}
    assert DR.hold_reasons(res4) == []
    # a soft line (self-check) next to an unresolved field still does not hold, as for a failed read
    res5 = {"needs_review": ["self-check: pitch 0.45 is not a standard pitch"], "spec": {"unresolved": ["lead_span"]}}
    assert DR.hold_reasons(res5) == []
    # fp_needed=False: only the symbol's hard lines count, incomplete or not
    assert DR.hold_reasons(res, fp_needed=False) == []


def test_rrt1_an_incomplete_answer_with_a_hard_failure_ranks_below_a_plain_incomplete_one():
    """The route keeps its best answer: an incomplete one that also fails a hard check ranks with the failed ones,
    below an incomplete answer that fails nothing."""
    unres = "unresolved: lead_span (not printed)"
    assert DR.effective_kind("incomplete", [unres, OVERLAP]) == "failed"
    assert DR.effective_kind("incomplete", [unres]) == "incomplete"
    assert DR.effective_kind("incomplete", [unres, "self-check: pitch 0.45 is not a standard pitch"]) == "incomplete"
    assert DR.effective_kind("ok", []) == "ok"
    from ecadlib.llm import run as LR
    # the retry (second) is better when the first had a hard failure and the retry is only incomplete
    assert LR._better("incomplete", [unres, OVERLAP], "incomplete", [unres, "pin 3: has no source page"])
    assert not LR._better("incomplete", [unres], "incomplete", [unres, OVERLAP])


@lite
def test_rrt1_the_route_keeps_the_plain_incomplete_answer_over_one_with_overlapping_pads(tmp_path, monkeypatch):
    """Reader._walk: answer 1 incomplete and clean, answer 2 incomplete with overlapping pads (fewer gaps): keep 1."""
    from ecadlib import datasheet as D
    rd = D.Reader(tmp_path / "out", tmp_path / "cache", log=None)
    verdicts = {"k1": ("incomplete", ["unresolved: a, b", "pin 3: has no source page", "pin 4: has no source page"]),
                "k2": ("incomplete", ["unresolved: a", OVERLAP])}
    monkeypatch.setattr(rd, "_judge", lambda job, rec: verdicts[job["key"]] + ({"spec": {"unresolved": []}},))
    rd.results = {"k1": {"message": {}}, "k2": {"message": {}}}
    steps = {(0, 0): "k1", (0, 1): "k2"}

    def at(job, rung, level):
        return dict(job, rung=rung, level=level, key=steps[(rung, level)], final=False)
    monkeypatch.setattr(rd, "_at", at)
    monkeypatch.setattr(rd, "_package_retry", lambda *a: None)
    rd.router = DR.Router({"max_attempts": 2})
    job = {"key": "k1", "rung": 0, "level": 0, "levels": [1, 2], "model": "m", "offset": [0],
           "route": {"attempts": []}}
    out = rd._walk(job)
    assert out["final"] is True and out["key"] == "k1", out
