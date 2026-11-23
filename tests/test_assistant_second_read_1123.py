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


def _second_read(got):
    return [x for x in (got.get("cross_check") or {}).get("checks") or () if x.get("source") == "second read"][0]


def test_a_copy_of_the_first_answer_is_no_second_read():
    """Review 2026-11-23: the first read's answer.json copied into the check folder agreed with itself and released
    the footprint. A full extraction (or the first answer itself) in the check folder is refused, and not cached."""
    api = TA._api()
    with tempfile.TemporaryDirectory() as d:
        f = _first_answered(d, api)
        TA._read(d, api)
        (c,) = _check_folders(d)
        (c / "answer.json").write_bytes((f / "answer.json").read_bytes())
        _, got = TA._read(d, api)
        sr = _second_read(got)
        assert sr["status"] == "unchecked" and "own schema" in sr["why"], sr
        assert got["cross_check"]["status"] != "agreed"
        (c / "answer.json").write_text(json.dumps(TC.check_answer()), encoding="utf-8")   # a real second read, then
        _, got = TA._read(d, api)
        assert _second_read(got)["status"] == "agreed", _second_read(got)


def test_a_wrong_shape_check_answer_is_not_kept_once_fixed():
    api = TA._api()
    with tempfile.TemporaryDirectory() as d:
        _first_answered(d, api)
        TA._read(d, api)
        (c,) = _check_folders(d)
        for bad in ("{}", '{"package": {}}', "not json"):
            (c / "answer.json").write_text(bad, encoding="utf-8")
            _, got = TA._read(d, api)                 # held with the reason, never an error for the whole part
            assert _second_read(got)["status"] == "unchecked", (bad, _second_read(got))
        (c / "answer.json").write_text(json.dumps(TC.check_answer()), encoding="utf-8")
        _, got = TA._read(d, api)
        assert _second_read(got)["status"] == "agreed", _second_read(got)


def test_names_a_folder_name_cannot_hold_get_folders_of_their_own():
    """TESTPART/A and TESTPART#A (and FOO, FOO_) stemmed to one folder, so one part could use the other's reads."""
    key = "check-0123456789abcdef"
    names = ["TESTPART/A", "TESTPART#A", "TESTPART_A", "FOO", "FOO_"]
    got = {DA.folder("/x", {"name": n, "key": key}).name for n in names}
    assert len(got) == len(names), got
    # (was exactly "IND-10UH-NRS4018-abcdef0123": the key's first 10 characters, which are only the PDF's hash, so every
    # request of one PDF shared the folder and its answer.json; the name now ends in 8 characters of the whole key's
    # hash, review 2026-11-23)
    plain = DA.folder("/x", {"name": "IND-10UH-NRS4018", "key": "abcdef0123456"}).name
    assert plain.startswith("IND-10UH-NRS4018-abcdef0123-") and len(plain) == len("IND-10UH-NRS4018-abcdef0123-") + 8, plain
    a = DA.folder("/x", {"name": "P", "key": "check-0123abcdef"}).name
    b = DA.folder("/x", {"name": "P", "key": "check-0123fedcba"}).name
    assert a != b, a                                   # a check's key gets more than four characters


def test_a_changed_check_is_a_new_folder_and_a_utf16_answer_holds():
    a = DA.folder("/x", {"name": "P", "key": "check-0123456789abcdef-sonnet-v2-aaaaaa111"}).name
    b = DA.folder("/x", {"name": "P", "key": "check-0123456789abcdef-sonnet-v2-bbbbbb222"}).name
    assert a != b, a
    api = TA._api()
    with tempfile.TemporaryDirectory() as d:
        _first_answered(d, api)
        TA._read(d, api)
        (c,) = _check_folders(d)
        (c / "answer.json").write_text(json.dumps(TC.check_answer()), encoding="utf-16")
        _, got = TA._read(d, api)
        assert _second_read(got)["status"] == "unchecked", _second_read(got)
