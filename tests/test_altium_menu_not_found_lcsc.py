"""Add part from MPN in Altium (2026-10-20, AD26): "SOSET 16M 20PF 10PPM was not found: no part source knows this part
number", for a YXC crystal (X322512MSB4SI, LCSC C2975360) that LCSC has. python3 -m tests.test_altium_menu_not_found_lcsc

1. text that reads as a description (values with units) is turned back as "not a part number", nothing looked up;
2. an LCSC number or lcsc.com link typed in is looked up by that number (the EasyEDA route);
3. a not-found while [providers] unofficial is off says LCSC was never asked and which file turns it on, instead
   of claiming no part source knows the part."""
from unittest import mock

from ecadlib import altium_menu as AM, rules as RU
from tests import test_altium_menu as TM
from tests.fixtures_lite import lite


def test_description_or_part_number():
    for desc in ("SOSET 16M 20PF 10PPM", "10k 0603 1%", "Capacitor 100nF 16V X7R 0402", "crystal 16MHz 20pF"):
        assert AM.looks_like_description(desc), desc
    for mpn in ("X322512MSB4SI", "CL05B104KO5NNNC", "LM358DR", "1N4148W-7-F", "NE555 DR", "C2975360", "", "BAT54 SOT23"):
        assert not AM.looks_like_description(mpn), mpn


def test_lcsc_input():
    assert AM.lcsc_input("C2975360") == AM.lcsc_input("c2975360 ") == "C2975360"
    assert AM.lcsc_input("https://www.lcsc.com/product-detail/C2975360.html") == "C2975360"
    assert AM.lcsc_input("lcsc.com/product-detail/C2975360.html") == "C2975360"
    for not_lcsc in ("X322512MSB4SI", "CL05B104KO5NNNC", "C12", "https://example.com/C2975360.html"):
        assert AM.lcsc_input(not_lcsc) == "", not_lcsc


@lite
def test_description_is_turned_back_before_any_lookup(tmp_path):
    menu, cfg = TM._setup(tmp_path / "m")
    with mock.patch.object(AM, "add_to_parts", side_effect=AssertionError("nothing may be looked up")):
        r = AM.run_job(TM._job(menu, {"action": "add-part", "mpn": "SOSET 16M 20PF 10PPM"}))
    assert r["status"] == "error" and r["reason"] == "description" and r["big"] == "Not a part number", r
    assert "looks like a description" in r["message"] and "manufacturer part number" in r["fix"], r


@lite
def test_lcsc_number_with_lcsc_off_says_so(tmp_path):
    menu, cfg = TM._setup(tmp_path / "m")
    with mock.patch.dict(RU.DEFAULTS["providers"], unofficial=False), \
            mock.patch.object(AM, "add_to_parts", side_effect=AssertionError("nothing may be looked up")):
        r = AM.run_job(TM._job(menu, {"action": "add-part", "mpn": "https://www.lcsc.com/product-detail/C2975360.html"}))
    assert r["status"] == "error" and r["reason"] == "not_searched" and r["big"] == "Not found", r
    assert "C2975360 is an LCSC number" in r["message"] and "LCSC was not asked" in r["message"], r
    assert "unofficial = true" in r["fix"] and "ecadlib.toml" in r["fix"], r


@lite
def test_lcsc_number_with_lcsc_on_goes_in_as_the_lcsc_column(tmp_path):
    menu, cfg = TM._setup(tmp_path / "m")
    seen = {}

    def fake_add(root, mpn, manufacturer="", **hints):
        seen.update(mpn=mpn, **hints)
        raise ValueError("stop here")
    with mock.patch.dict(RU.DEFAULTS["providers"], unofficial=True), mock.patch.object(AM, "add_to_parts", fake_add):
        AM.run_job(TM._job(menu, {"action": "add-part", "mpn": "C2975360"}))
    assert seen.get("mpn") == "C2975360" and seen.get("lcsc") == "C2975360", seen


@lite
def test_not_found_with_lcsc_off_names_the_switch(tmp_path):
    menu, cfg = TM._setup(tmp_path / "m")
    with mock.patch.dict(RU.DEFAULTS["providers"], unofficial=False), mock.patch.object(AM, "not_found", lambda *a: True):
        r = AM.run_job(TM._job(menu, {"action": "add-part", "mpn": "X322512MSB4SI"}))
    assert r["status"] == "error" and r["reason"] == "not_searched" and r["big"] == "Not found", r
    assert "not found in the official part sources" in r["message"] and "LCSC was not asked" in r["message"], r
    assert "no part source knows" not in r["message"], r
    assert "unofficial = true" in r["fix"] and str(cfg["workspace"]) in r["fix"], r
    # with LCSC on, a not-found really means no source knows it
    with mock.patch.dict(RU.DEFAULTS["providers"], unofficial=True), mock.patch.object(AM, "not_found", lambda *a: True):
        r = AM.run_job(TM._job(menu, {"action": "add-part", "mpn": "X322512MSB4SI"}))
    assert r["reason"] == "not_found" and "no part source knows" in r["message"], r


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_altium_menu_not_found_lcsc"]))
