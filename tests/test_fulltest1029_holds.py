"""2026-10-29 full test in Altium: what no longer holds a part or hides a verdict. Nexar's plan-limit answer is the
account, not the network (a bad MPN said "Held", a BOM "source refused/unreachable 37"); no datasheet link is a note."""
import tempfile

from ecadlib import bom_triage as T, release_check as RC, rules as RU
from tests.test_release_check import make


def test_account_limit_is_not_unreachable():
    nexar = "You have exceeded your part limit of 0. Please upgrade your plan. Contact us at api@nexar.com for assistance."
    for e in (nexar, "401 Unauthorized", "monthly quota exceeded", "invalid API key"):
        assert T.ACCOUNT_LIMIT.search(e), e
    for e in ("timeout", "HTTP 429 rate limited", "HTTP 403: the site blocks automated part searches from here",
              "connection refused"):
        assert not T.ACCOUNT_LIMIT.search(e), e                  # the network or a refusal: still unverified


def test_no_datasheet_link_is_a_note_unless_asked_to_hold():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        it["parameters"] = [p for p in it["parameters"] if p["name"] != "ComponentLink2URL"]
        f, w, _ = RC.check_item(it, d)
        assert not f and any(x.startswith("no datasheet link") for x in w), (f, w)
        hold = {**RU.DEFAULTS, "release": {**RU.DEFAULTS["release"], "datasheet": "hold"}}
        assert [f"{r}: {m}" for r, m in RC.check_item(it, d, hold)[0]] == ["fields: no datasheet link"]
    RU.validate({"release": {"datasheet": "warn"}})
    try:
        RU.validate({"release": {"datasheet": "maybe"}})
        raise AssertionError("[release] datasheet = maybe was accepted")
    except Exception as e:
        assert "release.datasheet" in str(e), e
