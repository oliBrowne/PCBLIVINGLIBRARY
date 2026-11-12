"""Tab packages (deep-dive 2026-11-12, item 3): a SOT-89 read gets KiCad's checked land, as DPAK / D2PAK / SOT-223
reads already did, and a lead span read far too long on a one-sided tab package is held. General rules, no part."""
import copy
from ecadlib import datasheet as D, stdparts as SP
from tests import test_fix1026_rd as T


def _pins(r):
    return [(q["num"], q["name"]) for q in r["symbol"]["pins"]]


def test_kicads_sot89_land_has_lead_2_running_into_the_tab():
    tab, leads, body = SP._tab_entry("SOT-89-3")
    assert tab == "2" and leads == {"1": -1.5, "2": 0.0, "3": 1.5} and body == 4.5
    assert SP._tab_entry("LFPAK56")[0] == "5" and "5" not in SP._tab_entry("LFPAK56")[1]   # a base apart from the leads


def test_a_sot89_read_names_kicads_sot89_land():
    r = T.read(T.SOT89)
    assert SP.identify_tab(r["spec"], _pins(r))[0] == "SOT-89-3"
    for name in ("TO-243AA", "SC-62"):
        r = T.read(dict(T.SOT89, name=name))
        assert SP.identify_tab(r["spec"], _pins(r))[0] == "SOT-89-3", name


def test_a_sot89_read_with_its_body_misread_is_not_named():
    r = T.read(dict(T.SOT89, body_length=T.sc(T.SOT89["body_length"], 2)))
    assert SP.identify_tab(r["spec"], _pins(r))[0] is None


def test_a_lead_span_far_past_the_body_on_a_one_sided_tab_package_is_held():
    for t in (T.SOT89, T.SOT223, T.DPAK):
        assert D._feet_outside(T.read(t)["spec"]) == [], t["name"]
        bad = dict(copy.deepcopy(t), name="X", description="X", lead_span=T.sc(t["lead_span"], 2))
        assert any("one side" in m for m in D._feet_outside(T.read(bad)["spec"])), t["name"]
