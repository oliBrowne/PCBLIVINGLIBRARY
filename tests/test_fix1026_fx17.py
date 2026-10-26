"""Fixes 2026-10-26 (fixes17 review: R-FX17-1..6). Offline: no key, no network, no model.
    python3 -m tests.run_all test_fix1026_fx17"""
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-FX17-1: QFN row allowance strictly under 2.5 pitches
def test_fx17_1_one_pin_off_qfn_split_at_0p4_fails_the_read():
    """Before: 2.25 pitches + 0.1 mm, inclusive, is exactly 2.5 pitches at 0.4 mm, so a split one pin off a part whose
    true margins are half a pitch apart (QFN-24 3x4 7/5 read 6/6, QFN-42 5x6 12/9 read 13/8, VQFN-46 5x6 13/10 read
    14/9) passed. The allowance now stays under 2.5 pitches; the right reads and UQFN-10 1.3x1.8 still pass."""
    from tests.test_fix1026_fx12 import _qfn_read
    parts = (((24, (3, 4), 0.4, 0.2, 0.4, (1.65, 2.65)), (7, 5), (6, 6)),
             ((42, (5, 6), 0.4, 0.2, 0.4, (3.7, 4.7)), (12, 9), (13, 8)),
             ((46, (5, 6), 0.4, 0.2, 0.4, (2.8, 3.8)), (13, 10), (14, 9)))
    for args, right, wrong in parts:
        assert not any("end margins" in w for w in _qfn_read(*args, *right)[1]), (args, right)
        kind, why = _qfn_read(*args, *wrong)
        assert kind == "failed" and any("end margins" in w for w in why), (args, wrong, kind, why)
    assert not any("end margins" in w for w in _qfn_read(10, (1.3, 1.8), 0.35, 0.18, 0.4, None, 2, 3)[1])
