"""Follow-ups of the 2026-10-24 blank-17 live run (Opus, batch): a cap for the out folder over all its runs, the
cross-check's label-only disagreements, and the maker's land credited without a page or unit per value."""
import json, tempfile
from pathlib import Path

from ecadlib.eval import runner as R


def _ledger(lib, *usd):
    d = Path(lib) / "datasheet"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cost.jsonl").write_text("".join(json.dumps({"part": "X", "model": "m", "usd": u}) + "\n" for u in usd))


def _raises(f):
    try:
        f()
    except ValueError as e:
        return str(e)
    raise AssertionError("no ValueError")


def test_a_folder_total_counts_what_the_folder_already_spent():
    # resumed under a $6 cap meant as the total: $1.98 already spent, so this run may send $4.02 at most
    with tempfile.TemporaryDirectory() as d:
        _ledger(d, 1.40, 0.58)
        said = []
        assert abs(R.total_cap(d, 6, log=said.append) - 4.02) < 1e-9
        assert "$1.98 already spent" in said[0]
        assert R.total_cap(d, 6, per_run=2, log=said.append) == 2          # the smaller of the two
        assert "already spent" in _raises(lambda: R.total_cap(d, 1.98, log=said.append))


def test_a_folder_total_with_unpriced_spend_sends_nothing():
    with tempfile.TemporaryDirectory() as d:
        _ledger(d, 1.0, None)
        assert "no price" in _raises(lambda: R.total_cap(d, 6, log=lambda *a: None))


def test_a_fresh_folder_has_the_whole_total():
    with tempfile.TemporaryDirectory() as d:
        assert R.total_cap(d, 6, log=lambda *a: None) == 6


def test_a_pin_named_by_its_own_number_agrees_with_any_name():
    from ecadlib import crosscheck as CC
    assert CC.numbered("1", "Pin_1") and CC.numbered("12", "PIN 12") and CC.numbered("3", "P3") and CC.numbered("1", "1")
    assert not CC.numbered("1", "Pin_10") and not CC.numbered("1", "VCC") and not CC.numbered("1", "P1A")
    a = [(str(i), f"Pin_{i}") for i in range(1, 14)]
    b = [(str(i), str(i)) for i in range(1, 14)]
    assert CC.compare_pins(a, b) == []                                  # SM13B-GHS-TB
    assert CC.compare_pins([("1", "Pin_1"), ("2", "Pin_2")], [("1", "GND"), ("2", "VCC")])   # a real name differs
    # a real disagreement still holds
    assert CC.compare_pins([("1", "GND"), ("2", "VCC")], [("1", "VCC"), ("2", "GND")])
