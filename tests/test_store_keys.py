"""Shared store: part keys (ecadlib/store_keys.py)."""
from ecadlib import store_keys as SK


def test_key_same_part_different_spelling():
    assert SK.key("Texas Instruments", "NE555DR") == SK.key("TI", "ne555-dr") == "TEXASINSTRUMENTS|NE555DR"


def test_key_keeps_decimal_point_and_suffix():
    assert SK.key("Littelfuse", "SMAJ5.0A") != SK.key("Littelfuse", "SMAJ50A")
    assert SK.key("TI", "LM358DR") != SK.key("TI", "LM358D")          # a suffix is never guessed away


def test_key_placeholders():
    assert SK.key("TI", "N/A") == "" and SK.key("TI", "") == ""
    assert SK.key("N/A", "NE555DR") == "|NE555DR" and SK.maker_of("|NE555DR") == ""


def test_shard_by_mpn_only():
    a, b = SK.key("TI", "NE555DR"), SK.key("", "NE555DR")
    assert SK.shard(a) == SK.shard(b) == SK.shard("ne555dr") and len(SK.shard(a)) == 2
    assert SK.key_id(a) != SK.key_id(b) and len(SK.key_id(a)) == 32


def test_digest_is_order_free():
    assert SK.digest({"a": 1, "b": [1, 2]}) == SK.digest({"b": [1, 2], "a": 1})
    assert SK.dumps({"b": "µ", "a": 1}) == '{"a":1,"b":"µ"}'
