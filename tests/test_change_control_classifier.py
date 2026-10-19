"""Change control (#105), the classifier: what the part fingerprint reads and how change_diff classes it.

Stacked and per-unit pins (every copy of a pin number is compared), units, alternate pin functions, custom pad
copper, pad copper layers, paste, pad and footprint attributes, value parsing (3.3V is not 3.0V, 100nF is not 100nH),
secondary MPN / manufacturer fields, and a library released by an older ecadlib (another fingerprint schema) that
must not show every part as changed after an upgrade."""
import json

from ecadlib import change_diff as CD, change_gate as CG, fingerprint as FP, ledger as LG
from tests.test_change_control import demo, gate, item, items_of, texts, write_symbol


# ---------------------------------------------------------------- upgrade: older fingerprint schema
def _age_ledger(lib):
    """Make every head look fingerprinted by an older ecadlib: schema 1, a fingerprint missing what this one reads,
    and its digest (the ledger line keeps the stored digest, as an old release would)."""
    lg = LG.Ledger(lib)
    raw = lg.path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in raw:
        e = json.loads(line)
        if e.get("fingerprint"):
            fp = lg.get_json(e["fingerprint"])
            fp["schema"] = 1
            for p in ((fp.get("symbol") or {}).get("pins") or {}).values():
                p.pop("inst", None)
            fp["hashes"] = FP.hashes(fp)
            e["fingerprint"], e["digest"] = lg.put_json(fp), fp["hashes"]["digest"]
        out.append(json.dumps(e, sort_keys=True, ensure_ascii=False))
    lg.path.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_library_released_by_an_older_ecadlib_shows_no_change_and_a_real_change_still_holds():
    plan, lib = demo()
    _age_ledger(lib)
    n = len(LG.Ledger(lib).lines())
    items = items_of(plan)
    g = gate(lib)
    rows = g.settle(items)
    assert rows == [], rows
    assert len(LG.Ledger(lib).lines()) == n, "nothing new recorded for unchanged parts"
    old = LG.Ledger(lib).fingerprint(LG.Ledger(lib).head("AO3401A"))
    assert old["schema"] == FP.SCHEMA and old["hashes"]["digest"] == FP.of_item(item(items, "AO3401A"), lib)["hashes"]["digest"]
    it = item(items, "AO3401A")
    g = gate(lib)
    write_symbol(lib, it, [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    [row] = g.settle(items)
    assert row["action"] == "hold" and any("pin 1 was G now S" in t for t in texts(row["changes"])), row
