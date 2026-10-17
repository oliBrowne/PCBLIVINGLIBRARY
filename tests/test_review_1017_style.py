"""Review 1017: house style pin notation, `style learn` paths, symbol power_side = "left"."""
import json
from pathlib import Path

from ecadlib import house_style as H, symbol_layout as SL
from tests.fixtures_lite import lite


def test_two_overbars_in_one_name_are_not_one_active_low_pin():
    assert H.pin_low("~{RD}/~{WR}") == ("~{RD}/~{WR}", None)
    assert H.convert_pin("~{RD}/~{WR}", "hash") == "~{RD}/~{WR}"
    assert H.convert_pin("~{RESET}", "hash") == "RESET#"


def test_differential_negative_leg_is_not_renamed():
    prof = {"pins": {"active_low": "overbar"}}
    sym, ren = H.adapt_symbol({"pins": [{"num": "1", "name": "CLK_P"}, {"num": "2", "name": "CLK_N"},
                                        {"num": "3", "name": "RESET_N"}]}, prof)
    assert [p["name"] for p in sym["pins"]] == ["CLK_P", "CLK_N", "~{RESET}"]
    assert ren == ["RESET_N -> ~{RESET}"]
    got = H.infer_pins(["CLK_P", "CLK_N", "TX_P", "TX_N", "D0_P", "D0_N", "RESET#", "CS#", "WR#"])
    assert got["active_low"] == "hash" and got["active_low_pins"] == 3


@lite
def test_learn_reads_setup_libs_relative_to_the_rules_file(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "lib").mkdir(parents=True)
    (proj / "ecadlib.toml").write_text('[setup]\nlibs = ["lib"]\n', encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert H.main(["--rules", str(proj / "ecadlib.toml"), "learn", "--json"]) == 0
    prof = json.loads((proj / "house_style.json").read_text(encoding="utf-8"))
    assert [Path(p) for p in prof["learned_from"]] == [(proj / "lib").resolve()]


@lite
def test_learn_from_relative_path_is_kept_absolute(tmp_path, monkeypatch):
    (tmp_path / "lib").mkdir()
    monkeypatch.chdir(tmp_path)
    assert H.main(["--profile", str(tmp_path / "hs.json"), "learn", "--from", "lib", "--json"]) == 0
    prof = json.loads((tmp_path / "hs.json").read_text(encoding="utf-8"))
    assert prof["learned_from"] == [str((tmp_path / "lib").resolve())]


@lite
def test_profile_that_is_not_an_object_is_a_style_error(tmp_path):
    f = tmp_path / "house_style.json"
    f.write_text("[]", encoding="utf-8")
    try:
        H.load(path=f)
    except H.StyleError as e:
        assert "not a house style profile" in str(e)
    else:
        raise AssertionError("no StyleError")


def test_power_side_left_puts_ground_at_the_bottom_of_the_left_side():
    pins = [{"num": "1", "name": "VCC", "type": "power"}, {"num": "2", "name": "GND", "type": "ground"},
            {"num": "3", "name": "IN", "type": "input"}] + \
           [{"num": str(10 + i), "name": f"OUT{i}", "type": "output"} for i in range(8)]
    sym = SL.build("X", pins, rules={"symbols": {"power_side": "left"}})
    pins_at = {pr[1]["name"]: pr for pr in sym["prims"] if pr[0] == "pin"}
    body = next(pr for pr in sym["prims"] if pr[0] == "rect")
    lowest = min(pr[4] for pr in pins_at.values())
    assert pins_at["GND"][2] == "left" and pins_at["GND"][4] == lowest
    assert pins_at["VCC"][2] == "left" and pins_at["VCC"][4] == max(pr[4] for pr in pins_at.values())
    assert lowest > body[4] and not any(pr[2] in ("top", "bottom") for pr in pins_at.values())
    assert SL.check(sym) == []
