"""reader = "assistant": the cross-check's second read is asked of the assistant too, in its own folder (2026-11-23).
With no key the second read was never made, so a footprint built from a read could only wait for a person (79 parts
of the 1000-part replay). Through the API that second read is the same model reading again; a fresh assistant answer
from the pages alone is no weaker, and it is judged by the same code.
Plain functions: python3 -m tests.run_all test_assistant_second_read_1123"""
import json
import tempfile
from pathlib import Path

from ecadlib import datasheet as D, ds_assistant as DA
from tests import test_crosscheck as TC, test_datasheet as TD, test_ds_assistant as TA

ASSIST = TA.ASSIST


def _first_answered(d, api):
    try:
        TA._read(d, api)
    except D.AssistantPending:
        pass
    (f,) = [p for p in DA.root(Path(d) / "out").iterdir() if p.is_dir()]
    (f / "answer.json").write_text(json.dumps(TC.first()), encoding="utf-8")
    return f


def _check_folders(d):
    return [p for p in DA.root(Path(d) / "out").iterdir() if p.is_dir() and "-check-" in p.name]


def test_the_second_read_is_asked_of_the_assistant_in_its_own_folder():
    api = TA._api()
    with tempfile.TemporaryDirectory() as d:
        _first_answered(d, api)
        _, got = TA._read(d, api)
        cc = got.get("cross_check") or {}
        sr = [c for c in cc.get("checks") or () if c.get("source") == "second read"]
        assert sr and sr[0]["status"] == "unchecked" and "waiting for your assistant" in sr[0]["why"], cc
        (c,) = _check_folders(d)
        assert c.name.startswith("TESTPART-check-"), c.name
        req = (c / "request.md").read_text(encoding="utf-8")
        assert "second read" in req.lower() and "do not open" in req.lower(), req[:800]   # its own read, from the pages
        assert (c / "sent.pdf").is_file() and (c / "schema.json").is_file()
        assert c.name in DA.waiting(DA.root(Path(d) / "out"))
    assert api.calls == []


def test_an_agreeing_second_read_from_the_assistant_counts_like_the_apis():
    """The same check answer through the API and through answer.json gives the same cross-check verdict."""
    check = lambda k, m, b: TC.first() if k == "read" else TC.check_answer()
    with tempfile.TemporaryDirectory() as d:
        want = TC.read(d, TC.API(check))
    api = TA._api()
    with tempfile.TemporaryDirectory() as d:
        _first_answered(d, api)
        TA._read(d, api)
        (c,) = _check_folders(d)
        (c / "answer.json").write_text(json.dumps(check("check", "", {})), encoding="utf-8")
        rd, got = TA._read(d, api)
        assert (got.get("cross_check") or {}).get("status") == (want.get("cross_check") or {}).get("status"), \
            (got.get("cross_check"), want.get("cross_check"))
        assert DA.waiting(rd.assistant) == []
        sr = [x for x in got["cross_check"]["checks"] if x.get("source") == "second read"]
        assert sr and sr[0].get("via") == "assistant" and sr[0]["against"] == "second read (your assistant)", sr
        (c / "answer.json").unlink()                # cached like a paid answer, still named as the assistant's
        _, again = TA._read(d, api)
        sr = [x for x in again["cross_check"]["checks"] if x.get("source") == "second read"]
        assert sr and sr[0]["against"] == "second read (your assistant)", sr
    assert api.calls == []


def test_a_disagreeing_second_read_from_the_assistant_holds_the_part():
    api = TA._api()
    with tempfile.TemporaryDirectory() as d:
        _first_answered(d, api)
        TA._read(d, api)
        (c,) = _check_folders(d)
        (c / "answer.json").write_text(json.dumps(TC.check_answer(pitch=0.65)), encoding="utf-8")
        try:
            TA._read(d, api)
            raise AssertionError("not held")
        except D.DatasheetError as e:
            assert "disagree" in str(e).lower() or "cross-check" in str(e).lower(), e
    assert api.calls == []
