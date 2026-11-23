"""[datasheet] reader = "assistant" (ecadlib.ds_assistant): a datasheet read with no API key and no cost. Each read's
pages and instructions go to a folder for the person's own assistant; its answer.json is then read exactly as a
model's answer would be. Nothing is ever sent to an AI service in this mode (1000-part real BOM, 2026-11-17: about
330 parts waited on a read that only a paid key or a hand patch could make)."""
import json
import os
import tempfile
from pathlib import Path

from ecadlib import datasheet as D, ds_assistant as DA, rules as RU
from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB
from tests.fixtures_lite import lite

ASSIST = {"datasheet": {"reader": "assistant"}}


def _api():
    """A fake API that fails the test if anything is sent to it."""
    def no(kind, model, body):
        raise AssertionError(f"sent to the API in assistant mode: {kind} {model}")
    return TC.API(no)


def _read(d, api, rules=ASSIST, part="TESTPART"):
    with TB.patched(api):
        rd = TC.reader(d, rules)
        return rd, rd.read_now(rd.job(part, [(TC.URL, "BOM")], TC.QFN))


def test_no_answer_yet_writes_the_request_and_sends_nothing():
    api = _api()
    with tempfile.TemporaryDirectory() as d:
        try:
            _read(d, api)
            raise AssertionError("no AssistantPending")
        except D.AssistantPending as e:
            msg = str(e)
        base = DA.root(Path(d) / "out")
        (f,) = [p for p in base.iterdir() if p.is_dir()]
        assert str(f) in msg and f.name.startswith("TESTPART-"), msg
        names = {p.name for p in f.iterdir()}
        assert {"request.md", "sent.pdf", "schema.json", "job.json"} <= names, names
        assert (f / "sent.pdf").read_bytes().startswith(b"%PDF"), "the pages a model would be sent"
        req = (f / "request.md").read_text(encoding="utf-8")
        assert "Part: TESTPART" in req and D.SYSTEM.split("\n")[0] in req and "answer.json" in req, req[:600]
        assert json.loads((f / "schema.json").read_text(encoding="utf-8"))["type"] == "object"
        assert DA.waiting(base) == [f.name]
        assert f"{f.name}/request.md" in (base / DA.README).read_text(encoding="utf-8")
    assert api.calls == [] and api.sent == []


def test_the_answer_is_read_like_a_models_and_cached():
    """The same extraction answered by the API and by answer.json gives the same result; still nothing is sent."""
    with tempfile.TemporaryDirectory() as d:
        _, want = _read(d, TC.API(lambda k, m, b: TD.GOOD), rules=None)
    api = _api()
    with tempfile.TemporaryDirectory() as d:
        try:
            _read(d, api)
        except D.AssistantPending:
            pass
        (f,) = [p for p in DA.root(Path(d) / "out").iterdir() if p.is_dir()]
        (f / "answer.json").write_text(json.dumps(TD.GOOD), encoding="utf-8")
        rd, got = _read(d, api)
        assert DA.waiting(rd.assistant) == []
        for k in ("spec", "pins"):
            assert got.get(k) == want.get(k), (k, got.get(k), want.get(k))
        (f / "answer.json").unlink()                # cached like a paid answer: not asked again
        _, again = _read(d, api)
        assert again.get("spec") == want.get("spec")
    assert api.calls == []


def test_an_answer_that_is_not_json_names_its_file():
    with tempfile.TemporaryDirectory() as d:
        try:
            _read(d, _api())
        except D.AssistantPending:
            pass
        (f,) = [p for p in DA.root(Path(d) / "out").iterdir() if p.is_dir()]
        (f / "answer.json").write_text("{ D: 3.0 ", encoding="utf-8")
        try:
            _read(d, _api())
            raise AssertionError("bad JSON accepted")
        except D.AssistantPending:
            raise AssertionError("bad JSON taken for no answer")
        except D.DatasheetError as e:
            assert "answer.json" in str(e) and "not JSON" in str(e), str(e)


def test_a_key_in_the_environment_is_never_used():
    with tempfile.TemporaryDirectory() as d:
        rd = TC.reader(d, ASSIST)                    # (TC.reader passes a key, as an ANTHROPIC_API_KEY would)
        assert rd.key is None and rd.assistant is not None and not rd.batch
        rd._guard_cost([{"key": "x", "model": D.MODEL, "body": {}}])      # nothing to cap: no error, no request
        assert rd._web_search("TESTPART", None, []) == (None, None)


@lite
def test_setting_rules_check_and_environment(tmp_path, monkeypatch):
    assert D.settings({})["reader"] == "api" and D.settings(ASSIST)["reader"] == "assistant"
    monkeypatch.setenv("ECADLIB_DATASHEET_READER", "assistant")
    assert D.settings({})["reader"] == "assistant"
    monkeypatch.setenv("ECADLIB_DATASHEET_READER", "api")
    assert D.settings(ASSIST)["reader"] == "api"
    monkeypatch.setenv("ECADLIB_DATASHEET_READER", "")
    f = Path(tmp_path) / "ecadlib.toml"
    f.write_text('[datasheet]\nreader = "chatgpt"\n', encoding="utf-8")
    try:
        RU.load(str(f))
        raise AssertionError("an unknown reader was accepted")
    except Exception as e:
        assert "datasheet.reader" in str(e), str(e)
    f.write_text('[datasheet]\nreader = "assistant"\n', encoding="utf-8")
    assert RU.load(str(f))["datasheet"]["reader"] == "assistant"


def test_librarian_holds_the_part_then_builds_it_from_the_answer():
    """Through the librarian (tests.test_datasheet_batch.run2): first run held with the folder named, nothing sent;
    after answer.json, the next run builds the part from it."""
    api = _api()
    with tempfile.TemporaryDirectory() as d:
        items, rd, _ = TB.run2(d, api, reader_kw={"batch": False, "rules": ASSIST})
        it = items["DSPART1"]
        tried = " ".join(it["librarian"]["tried"])
        assert it["status"] == "held" and "waiting for your assistant" in tried, tried
        assert not it.get("footprint", {}).get("kicad_mod")
        (f,) = [p for p in rd.assistant.iterdir() if p.is_dir() and p.name.startswith("DSPART1")]
        (f / "answer.json").write_text(json.dumps(TD.GOOD), encoding="utf-8")
        items, _, _ = TB.run2(d, api, reader_kw={"batch": False, "rules": ASSIST})
        it = items["DSPART1"]
        assert "waiting for your assistant" not in " ".join(it["librarian"]["tried"]), it["librarian"]["tried"]
        assert str(it["source"]["symbol"] or "").startswith("datasheet:") and it["source"]["footprint"], it["source"]
    assert api.calls == []


def test_readme_lists_only_what_this_run_asked_for():
    """A folder left by an earlier run (its part since removed, or its PDF changed) is not listed as waiting."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        old = base / "GONE-0123456789"
        old.mkdir()
        (old / "request.md").write_text("x", encoding="utf-8")
        assert DA.waiting(base) == ["GONE-0123456789"]              # no index yet: every folder on disk
        new = base / "NOW-abcdefghij"
        new.mkdir()
        (new / "request.md").write_text("x", encoding="utf-8")
        assert DA.index(base, {"NOW-abcdefghij"}) == ["NOW-abcdefghij"]
        assert "GONE" not in (base / DA.README).read_text(encoding="utf-8")
        (new / "answer.json").write_text("{}", encoding="utf-8")
        assert DA.waiting(base) == []


def test_a_pdf_ecadlib_could_not_download_is_asked_of_the_assistant_then_read():
    """34 of the PC's 1000-part reads (2026-11-17) had no PDF: Littelfuse, Nexperia, Mouser and Molex links refuse
    scripts. The request says to download it with a browser into datasheet/assistant/pdfs/<MPN>.pdf; the next run reads
    that file like one in the [datasheet] folder and asks for the read with its pages."""
    api, dead = _api(), "https://maker.example/BAS16.pdf"
    with tempfile.TemporaryDirectory() as d:
        with TB.patched(api):
            rd = TC.reader(d, ASSIST)
            rd.offline = True                          # nothing here can download the link
            job = rd.job("BAS16,215", [(dead, "BOM")], "SOD-323")
            assert job["prov"].get("sent_as") == "url", job["prov"]
            try:
                rd.read_now(job)
                raise AssertionError("no AssistantPending")
            except D.AssistantPending:
                pass
        base = rd.assistant
        (f,) = [p for p in base.iterdir() if p.is_dir()]
        req = (f / "request.md").read_text(encoding="utf-8")
        assert "pdfs/BAS16,215.pdf" in req and dead in req, req[:900]
        assert "no PDF yet" in (base / DA.README).read_text(encoding="utf-8")
        DA.pdf_folder(base).mkdir()
        (DA.pdf_folder(base) / "BAS16,215.pdf").write_bytes(TC.pdf3())
        with TB.patched(api):
            rd = TC.reader(d, ASSIST)
            rd.offline = True
            job = rd.job("BAS16,215", [(dead, "BOM")], "SOD-323")
        assert job["prov"].get("sent_as") != "url" and "saved by your assistant" in job["prov"]["where"], job["prov"]
        # the file saved here is another part's sheet ("TESTPART buck converter"). Until review 2026-11-23 it was read
        # as BAS16's on its file name alone; it is still read (there is no other PDF), but flagged like a downloaded
        # PDF that does not name the part, and that review line keeps the part with a person
        assert "doesn't mention BAS16,215" in job["prov"]["unnamed"], job["prov"]
        (DA.pdf_folder(base) / "BAS16,215.pdf").write_bytes(TC.TR.pdf_lines([
            ["BAS16 high-speed switching diode", "SOD-323", "Product data sheet"]]))     # (text enough to tell the part by:
        #                             with less it cannot be told, and that is flagged too since review 2026-11-23, round 2)
        with TB.patched(api):
            rd = TC.reader(d, ASSIST)
            rd.offline = True
            job = rd.job("BAS16,215", [(dead, "BOM")], "SOD-323")
        assert "unnamed" not in job["prov"] and "saved by your assistant" in job["prov"]["where"], job["prov"]
    assert api.calls == []
