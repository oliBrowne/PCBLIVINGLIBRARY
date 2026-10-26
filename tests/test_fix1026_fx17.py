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


# ---------------------------------------------------------------- R-FX17-2: a "conflict:" chip_size is no size
def _chip_item(mpn, src):
    return {"parameters": [{"name": "Manufacturer Part Number", "value": mpn}], "source": {"footprint": src},
            "footprint": {}}


def test_fx17_2_kicad_land_names_with_an_odd_metric_code_are_not_held():
    """Before: SP.chip_size(strict=True) answers 'conflict:2220/conflict:5650 metric?' for KiCad's C_2220_5650Metric
    (KiCad's metric 5650 is not the table's 5750) and the release check took that string as a size, holding
    GRM55DR72J224KW01L (a 2220). A conflict is no size read; KiCad's IMP_METMetric names its imperial size."""
    from ecadlib import release_check as RC
    for mpn, src in (("GRM55DR72J224KW01L", "kicad:Capacitor_SMD:C_2220_5650Metric"),
                     ("C5750X7R1H106K230KA", "kicad:C_2220_5650Metric_Pad1.97x5.40mm_HandSolder"),
                     ("LQM31PN1R0M00L", "kicad:Inductor_SMD:L_1206_3216Metric_Pad1.22x1.90mm_HandSolder"),
                     ("GRM188R71C104KA01D", "kicad:Capacitor_SMD:C_0603_1608Metric")):
        assert RC.chip_size_problem(_chip_item(mpn, src)) is None, (mpn, src)
    # a real mismatch on those same land names is still held
    for mpn, src in (("GRM188R71C104KA01D", "kicad:Capacitor_SMD:C_2220_5650Metric"),
                     ("GRM55DR72J224KW01L", "kicad:Inductor_SMD:L_1206_3216Metric_Pad1.22x1.90mm_HandSolder"),
                     ("GRM155R71C104KA88D", "kicad:Capacitor_SMD:C_0603_1608Metric")):
        assert "chip size" in (RC.chip_size_problem(_chip_item(mpn, src)) or ""), (mpn, src)


# ---------------------------------------------------------------- R-FX17-3: distributor package aliases are one land
def test_fx17_3_distributor_aliases_name_the_same_land():
    """Before (583a195): two names no rule reads were one land only when _norm made them equal, so a distributor's own
    spelling of the same package held the line. Their aliases are canonicalised first; real differences stay held."""
    from ecadlib import altium_export as A
    same = (("TO-252-3, DPak (2 Leads + Tab), SC-63", "TO-252-2"), ("DPAK-3", "TO-252-2"), ("TO-252-2(DPAK)", "TO-252-2"),
            ("SOT-363-6", "SOT-363_SC-70-6"), ("D2PAK-3", "TO-263-2"), ("TO-263-3, D2Pak (2 Leads + Tab), TO-263AB", "TO-263-2"),
            ('8-DIP (0.300", 7.62mm)', "DIP-8_W7.62mm"), ("SOIC-Wide-16", "SOIC-16W_7.5x10.3mm"),
            ("PowerPAK SO-8", "PowerPAK_SO-8_Single"))
    for a, b in same:
        assert not A.lands_differ(a, b), (a, b)
        assert not A.lands_differ(b, a), (b, a)
    differ = (("TO-263-2", "TO-252-2"), ("SOT-563", "SOT-363_SC-70-6"), ("SOT-563-6", "SOT-363_SC-70-6"),
              ("TO-247-3", "TO-220-3_Vertical"), ("D2PAK-3", "TO-252-2"), ("DPAK-3", "TO-263-2"), ("TO-252-3", "TO-252-2"),
              ("TO-252-5, DPak (4 Leads + Tab)", "TO-252-2"), ("SOIC-Wide-16", "SOIC-16_3.9x9.9mm_P1.27mm"),
              ('8-DIP (0.300", 7.62mm)', "DIP-8_W10.16mm"), ('8-DIP (0.300", 7.62mm)', "DIP-14_W7.62mm"),
              ("PowerPAK SO-8", "PowerPAK_SO-8_Dual"), ("SOT-363-6", "SOT-23-6"), ("DPAK-3", "TO-252-2_TabPin1"))
    for a, b in differ:
        assert A.lands_differ(a, b) and A.lands_differ(b, a), (a, b)


# ---------------------------------------------------------------- R-FX17-4: gap cut only between two pads that were apart
def test_fx17_4_same_number_or_touching_pads_are_no_gap_cut():
    """Before (5545275): a pad growing 0.02 mm next to a pad of its own number (a segmented exposed pad, a dual-pitch
    CP_Radial's two pad 1s) or a pad it already touched counted as a gap cut, so the edit was geometry-major (26 of
    1,482 KiCad footprints). Those peers are skipped; a real cut between two separate pads is still major."""
    from ecadlib import change_diff as CD
    from tests.test_fix1026_gate2 import _kfp
    seg = lambda w: _kfp([("1", -3, 0, 0.5, 0.3), ("2", 3, 0, 0.5, 0.3), ("3", 0, 0, w, 1.0), ("3", 1.05, 0, 1.0, 1.0)])
    assert CD.worst(CD.diff(seg(1.0), seg(1.02))) == "geometry-minor"
    touch = lambda w: _kfp([("1", 0, 0, w, 1.0), ("2", 0.9, 0, 1.0, 1.0), ("3", 5, 0, 1, 1)])
    assert CD.worst(CD.diff(touch(1.0), touch(1.02))) == "geometry-minor"
    apart = lambda w: _kfp([("1", -3, 0, 0.5, 0.3), ("2", 3, 0, 0.5, 0.3), ("3", 0, 0, w, 1.0), ("4", 1.05, 0, 1.0, 1.0)])
    ch = CD.diff(apart(1.0), apart(1.08))
    assert CD.worst(ch) == "geometry-major" and any("gap to pad 4" in c["text"] for c in ch), ch
