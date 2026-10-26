"""Fixes for Oliver's 2026-10-26 answers (fixer "pins"): KiCad export pin types of SchLib read-back parts (symexp Q1)
and footprint numbering holds (fpgen D1). Plain functions (tests.run_all, no pytest)."""
from pathlib import Path

from ecadlib import altium_lib as AL, kicad_export as X, sexpr
from tests.fixtures_lite import lite

LDO = [{"num": "1", "name": "VIN", "type": "power_in"}, {"num": "2", "name": "GND", "type": "ground"},
       {"num": "3", "name": "EN", "type": "input"}, {"num": "4", "name": "NC", "type": "nc"},
       {"num": "5", "name": "VOUT", "type": "power_out"}, {"num": "6", "name": "BYP", "type": "power_out"}]


def _plan(tmp_path, pins, source="datasheet:https://example.com/ldo.pdf", types=None, name="LDO1", designator="U?"):
    sym = AL.symbol_from_json({"name": name, "pins": pins, "designator": designator})
    (tmp_path / "SchLib").mkdir(exist_ok=True)
    AL.write_schlib(tmp_path / "SchLib" / f"{name}.SchLib", [sym])
    item = {"name": name, "source": {"symbol": source}, "symbol": {"file": f"SchLib/{name}.SchLib", "ref": name}}
    if types is not None:
        item["symbol"]["kicad_types"] = types
    return item


def _types(item, plan_dir):
    sym, _ = X.load_symbol(item, Path(plan_dir), None, [], item["name"], [])
    return {str(sexpr.first(p, "number")[1]).strip('"'): p[1] for u in sexpr.find(sym, "symbol")
            for p in sexpr.find(u, "pin") if "hide" not in p}


# symexp Q1: the types the datasheet read gave, recorded with the plan's symbol, come back in the KiCad export
@lite
def test_q1_recorded_types_come_back(tmp_path):
    from ecadlib import librarian as LB
    types = LB.kicad_only_types(LDO)
    assert types == {"4": "no_connect", "5": "power_out", "6": "power_out"}, types
    got = _types(_plan(tmp_path, LDO, types=types), tmp_path)
    assert got["5"] == "power_out" and got["6"] == "power_out" and got["4"] == "no_connect", got
    assert got["1"] == "power_in" and got["2"] == "power_in" and got["3"] == "input", got


# symexp Q1 fallback: a plan from before the types were recorded: power pins named like a regulator output, and NC
@lite
def test_q1_fallback_by_name(tmp_path):
    got = _types(_plan(tmp_path, LDO), tmp_path)
    assert got["5"] == "power_out" and got["4"] == "no_connect", got
    assert got["6"] == "power_in" and got["1"] == "power_in", got          # BYP: no name rule, left as read


# a relay's NC is the normally closed contact, never a no-connect; a decoupling VCAP pin is not a rail driver
@lite
def test_q1_fallback_leaves_relay_contacts_and_vcap(tmp_path):
    relay = [{"num": "1", "name": "COIL1", "type": "passive"}, {"num": "2", "name": "COIL2", "type": "passive"},
             {"num": "3", "name": "COM", "type": "passive"}, {"num": "4", "name": "NC", "type": "passive"},
             {"num": "5", "name": "NO", "type": "passive"}]
    got = _types(_plan(tmp_path, relay, name="RLY", designator="K?"), tmp_path)
    assert got["4"] == "passive", got
    mcu = [{"num": "1", "name": "VDD", "type": "power_in"}, {"num": "2", "name": "VCAP_1", "type": "power_in"},
           {"num": "3", "name": "VSS", "type": "ground"}, {"num": "4", "name": "PA0", "type": "io"}]
    got = _types(_plan(tmp_path, mcu, name="MCU"), tmp_path)
    assert got["2"] == "power_in", got


# a part whose symbol comes from a KiCad library (read back here because the source cannot be fetched) keeps the
# types its .SchLib gives: no name guesses on a curated symbol
@lite
def test_q1_kicad_source_unchanged(tmp_path):
    got = _types(_plan(tmp_path, LDO, source="local:Nope:LDO1"), tmp_path)
    assert got["5"] == "power_in" and got["4"] == "passive", got


# the librarian copies the recorded types into the plan item, and drops them when the symbol came from elsewhere
@lite
def test_q1_apply_state_carries_types(tmp_path):
    from ecadlib import librarian as LB
    item = {"name": "X", "symbol": {"ref": "X"}, "footprint": {"ref": "F"}, "parameters": []}
    st = {"symbol": {"file": "SchLib/X.SchLib", "source": "datasheet:u", "kicad_types": {"5": "power_out"}},
          "footprint": None, "found": {}}
    LB.apply_state(item, st, {})
    assert item["symbol"]["kicad_types"] == {"5": "power_out"}
    st["symbol"] = {"file": "SchLib/X.SchLib", "source": "kicad:Lib:X"}
    LB.apply_state(item, st, {})
    assert "kicad_types" not in item["symbol"]
