"""EasyEDA / JLCPCB search (undocumented endpoints) are off until the librarian opts in; `ecadlib init` asks."""
from unittest import mock

import tests
from ecadlib import firstrun as FR, providers as P, rules as RU


def test_shipped_default_is_off():
    assert tests.SHIPPED_UNOFFICIAL is False             # what ships (the test package turns it on for old fixtures)
    with mock.patch.dict(RU.DEFAULTS["providers"], unofficial=False):
        assert not P.unofficial_allowed(None) and not P.unofficial_allowed({"providers": {}})
        assert P.unofficial_allowed({"providers": {"unofficial": True}})
        kept = P.fence([P.EasyEDA, P.JLCPCBSearch, P.Nexar], None)
        assert P.EasyEDA not in kept and P.JLCPCBSearch not in kept and P.Nexar in kept, kept


def _ask(answers, existing=None):
    it = iter(answers)
    return FR.ask_setup(existing or {}, {"tool": "altium", "libs": ["none"], "workspace": "ws"},
                        inp=lambda *_: next(it), out=lambda *_: None, environ={})


def test_init_asks_and_writes_the_answer():
    with mock.patch.dict(RU.DEFAULTS["providers"], unofficial=False):
        ans = _ask(["", "y", "n"])                       # online yes, EasyEDA yes, datasheet reader no
        assert ans["unofficial"] is True and FR.setup_raw(ans)["providers"] == {"unofficial": True}, ans
        ans = _ask(["", "", "n"])                        # Enter keeps the default: off
        assert ans["unofficial"] is False and FR.setup_raw(ans)["providers"] == {"unofficial": False}, ans
        ans = _ask(["", "", "n"], {"providers": {"unofficial": True}})   # Enter keeps what the file says
        assert ans["unofficial"] is True, ans
        ans = _ask(["n", "n"])                           # no online lookups: not asked, nothing written
        assert ans["unofficial"] is None and "providers" not in FR.setup_raw(ans), ans
