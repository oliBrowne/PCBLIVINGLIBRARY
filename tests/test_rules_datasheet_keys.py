"""Every [datasheet] setting the reader takes (datasheet.SETTINGS) can be set in a rules file: a key the reader reads
but the rules check rejects can never be turned on (web_search, #158/#170: "datasheet.web_search is not a known key")."""
import tempfile
from pathlib import Path

from ecadlib import datasheet as D, rules as RU


def _load(text):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ecadlib-rules.toml"
        p.write_text(text, encoding="utf-8")
        return RU.load(p)


def test_every_reader_setting_is_a_known_rules_key():
    assert set(D.SETTINGS) <= set(RU.DEFAULTS["datasheet"])


def test_rules_defaults_match_the_reader_defaults():
    assert {k: RU.DEFAULTS["datasheet"][k] for k in D.SETTINGS} == D.SETTINGS


def test_web_search_turns_on_from_a_rules_file():
    for text in ("[datasheet]\nweb_search = true\n",
                 "[datasheet]\nweb_search = true\nweb_search_model = \"claude-haiku-5-5\"\nweb_search_max_uses = 2\n"):
        assert _load(text)["datasheet"]["web_search"] is True, text


def test_no_searches_is_a_setting():
    r = _load("[datasheet]\nweb_search = true\nweb_search_max_uses = 0\n")
    assert r["datasheet"]["web_search_max_uses"] == 0


def test_a_bad_web_search_value_is_refused():
    for text in ("[datasheet]\nweb_search = \"yes\"\n", "[datasheet]\nweb_search_max_uses = 11\n",
                 "[datasheet]\nweb_search_max_uses = -1\n", "[datasheet]\nweb_search_model = \"not a model!\"\n"):
        try:
            _load(text)
        except RU.RulesError:
            continue
        raise AssertionError(f"accepted: {text!r}")


def test_the_web_search_keys_have_help():
    assert all(f"datasheet.{k}" in RU.HELP for k in ("web_search", "web_search_model", "web_search_max_uses"))
