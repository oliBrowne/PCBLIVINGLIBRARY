"""Fallbacks that change behaviour are said out loud (stderr and review.md), never carried on silently."""
import contextlib, io, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_project as AP, fallbacks as FB, llm as LL, reports, rules as RU, visual_check as V


def _quiet():
    return contextlib.redirect_stderr(io.StringIO())


def test_note_prints_once_and_take_clears():
    FB.take()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        FB.note("x did not load", ValueError("bad"), instead="y")
        FB.note("x did not load", ValueError("bad"), instead="y")
    assert FB.peek() == ["x did not load (ValueError: bad); using y instead"]
    assert err.getvalue().count("warning: x did not load") == 1
    assert FB.take() and FB.take() == []


def test_model_setting_failure_is_reported_not_silent():
    """visual_check used to fall back to the default model quietly: you could pick one model and be billed for another."""
    FB.take()
    with _quiet(), mock.patch.object(LL, "endpoint", side_effect=RuntimeError("no such provider")):
        s = V._settings({"agent": {"proof": {"model": "claude-haiku-5-5"}}})
    assert s["model"] == "claude-haiku-5-5"
    got = FB.take()
    assert len(got) == 1 and "model setting did not resolve" in got[0] and "claude-haiku-5-5" in got[0], got


def test_house_parameter_names_failure_is_reported():
    FB.take()
    with _quiet(), mock.patch.object(RU, "param_name", side_effect=KeyError("names")):
        assert AP._param_names("Manufacturer", ["Manufacturer"], {"parameters": {}}) == ["Manufacturer"]
    got = FB.take()
    assert len(got) == 1 and "house-style parameter name for 'Manufacturer'" in got[0], got


def test_review_md_leads_with_the_fallbacks():
    with tempfile.TemporaryDirectory() as d:
        items = [{"name": "R1", "status": "ok", "source": {"symbol": "standard", "footprint": "standard"},
                  "librarian": {"tried": [], "review": []}}]
        reports.write_review_files(items, d, "bom.csv", ["### x"], ["house-style parameter name for 'MPN' did not load"])
        md = (Path(d) / "review.md").read_text(encoding="utf-8")
        assert "fell back to a default" in md and "did not load" in md, md
        assert md.index("fell back") < md.index("### x"), md
        reports.write_review_files(items, d, "bom.csv", ["### x"])
        assert "fell back" not in (Path(d) / "review.md").read_text(encoding="utf-8")
