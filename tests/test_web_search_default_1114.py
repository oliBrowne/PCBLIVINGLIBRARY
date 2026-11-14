"""[datasheet] web_search ships on (2026-11-14): a part none of whose links gives a PDF, by any free way, is searched
for, billed to the person's own key. The second search (a package drawing the datasheet lacks) has its own switch,
off. The suite runs with the search off (tests/__init__.py); these tests check what ships and its off switches."""
import inspect
import os
import tempfile
from unittest import mock

import tests
from ecadlib import datasheet as D, librarian as L, messages as M, rules as RU
from tests.test_ds_search import DEAD, FOUND, Client, _answer, _reader


def shipped(fn):
    def run():
        with mock.patch.dict(D.SETTINGS, web_search=tests.SHIPPED_WEB_SEARCH[1]), \
                mock.patch.dict(RU.DEFAULTS["datasheet"], web_search=tests.SHIPPED_WEB_SEARCH[0]):
            return fn()
    run.__name__ = fn.__name__
    return run


def test_the_search_ships_on_and_the_package_search_off():
    assert tests.SHIPPED_WEB_SEARCH == (True, True), tests.SHIPPED_WEB_SEARCH
    assert D.SETTINGS["web_search_package"] is False and RU.DEFAULTS["datasheet"]["web_search_package"] is False
    assert "on by default" in RU.HELP["datasheet.web_search"] and "false" in RU.HELP["datasheet.web_search"]


@shipped
def test_with_no_setting_a_dead_link_is_searched_for():
    cl = Client(_answer([FOUND]))
    with tempfile.TemporaryDirectory() as d:
        job = _reader(d, cl).job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
        assert job["url"] == FOUND and job["base_prov"]["found_by"] == "web search" and len(cl.sent) == 1


@shipped
def test_false_in_the_rules_or_the_run_switch_never_searches():
    for ds, env in (({"web_search": False}, {}), ({}, {"ECADLIB_NO_WEB_SEARCH": "1"}),
                    ({"web_search": True, "web_search_package": True}, {"ECADLIB_NO_WEB_SEARCH": "1"})):
        cl = Client(_answer([FOUND]))
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, env):
            rd = _reader(d, cl, **ds)
            assert not rd.cfg["web_search"] and not rd.cfg["web_search_package"], (ds, env, rd.cfg)
            try:
                rd.job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
            except D.DatasheetError:
                pass
            assert not cl.sent, (ds, env)


@shipped
def test_the_default_search_is_not_the_package_search():
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, Client())
        assert rd.cfg["web_search"] is True and rd.cfg["web_search_package"] is False
    assert 'self.cfg.get("web_search_package")' in inspect.getsource(D.Reader._package_retry)


def test_a_searched_datasheet_says_so_in_the_review_with_the_off_switch():
    got = M.explain(D.SEARCHED)
    assert got["code"] == "datasheet.searched", got
    assert "billed to your API key" in got["text"] and "web_search = false" in got["fix"], got
    assert 'needs_review"].append(SEARCHED)' in inspect.getsource(D.Reader._result)


def test_a_part_with_no_link_and_no_key_is_not_sent_to_the_reader():
    """The search needs a key to bill: with none, a part with no link says so as before, not "needs datasheet reader"."""
    src = inspect.getsource(L)
    assert 'cfg.get("web_search") and (self.api_key or getattr(self.reader, "key", None))' in src


@shipped
def test_a_search_that_found_nothing_is_not_paid_for_again_but_a_failed_one_is_tried_again():
    """On by default, a part with no datasheet anywhere would cost a search on every run."""
    import json
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        for n, cl in enumerate((Client(_answer([])), Client(_answer([FOUND])))):
            try:
                _reader(d, cl).job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
                raise AssertionError("a PDF was found")
            except D.DatasheetError as e:
                assert len(cl.sent) == (1 if n == 0 else 0), (n, cl.sent)
                assert "found no PDF of it (not searched again for 30 days; delete \"TESTPART\"" in str(e), str(e)
        f = Path(d) / "out" / "datasheet" / "web_search.json"
        entry = json.loads(f.read_text())["TESTPART"]
        assert entry["none"] == "no datasheet", entry
        entry["at"] = "2026-01-01"                    # ... older than NONE_DAYS: searched again
        f.write_text(json.dumps({"TESTPART": entry}))
        cl = Client(_answer([FOUND]))
        assert _reader(d, cl).job("TESTPART", [(DEAD, "BOM")], "SOIC-8")["url"] == FOUND and len(cl.sent) == 1

    class Down:
        sent = 0

        def messages(self, body, headers=None):
            Down.sent += 1
            raise D.APIError("HTTP 529 overloaded", status=529)
    with tempfile.TemporaryDirectory() as d:
        for _ in range(2):
            try:
                _reader(d, Down()).job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
            except D.DatasheetError:
                pass
        assert Down.sent == 2 and not (Path(d) / "out" / "datasheet" / "web_search.json").exists()


@shipped
def test_the_search_runs_before_a_person_is_asked_to_download_anything():
    """The hand-off (the link opened in the person's browser) is the last resort: no way that runs by itself is left."""
    src = inspect.getsource(D.Reader.job)
    assert 0 < src.index("self._web_search(") < src.index('handoff="only"'), "the search must come before the hand-off"
    asked = []

    class Handoff:                                    # the store's hand-off: records what a person would be asked for
        def __getattr__(self, name):
            asked.append(name)
            raise AssertionError(f"the hand-off was used ({name}) though the search found the PDF")
    cl = Client(_answer([FOUND]))
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, cl)
        rd.store.handoff = Handoff()
        assert rd.job("TESTPART", [(DEAD, "BOM")], "SOIC-8")["url"] == FOUND and not asked, asked


@shipped
def test_a_search_that_failed_after_some_links_is_not_kept_as_nothing_found():
    from pathlib import Path

    class Half:
        def messages(self, body, headers=None):
            raise D.APIError("HTTP 529 overloaded", status=529)
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, Half())
        rd._search_cut = False
        try:
            rd.job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
        except D.DatasheetError:
            pass
        assert not (Path(d) / "out" / "datasheet" / "web_search.json").exists()
