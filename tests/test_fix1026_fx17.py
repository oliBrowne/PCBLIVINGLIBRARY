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


# ---------------------------------------------------------------- R-FX17-5: a merged ledger refuses only its parts
def test_fx17_5_a_merged_ledger_refuses_only_that_part_and_names_the_way_out():
    """Before (ddb01cd): after a two-branch merge in which both released r2 of KT-0603R, Ledger.head() raised in every
    publisher, so the whole library was refused (kicad-export: 0 parts); following the hint (one release line per
    revision) still failed on branch B's .ecadlib/marks file, which nothing named; `part status` said "no held changes".
    Now the publisher refuses KT-0603R alone, the message names the marks file to delete, `part status` lists it, and
    deleting that file (the ledger keeping branch A's r2) mends it."""
    import contextlib, io, json
    from ecadlib import change_cli as CC, ledger as LG
    from tests.test_fix1026_gate2 import KT, _demo, _ledger, _publish, _two_branches
    for keep_both in (True, False):
        w, lib = _demo()
        _two_branches(lib, keep_both)
        n, plan_before = len(_ledger(lib)), (lib / "plan.json").read_bytes()
        out, _g = _publish(lib)
        assert out[KT] and "r2" in out[KT] and ".ecadlib/marks/" in out[KT], out
        assert not any(str(v).startswith("refused") for k, v in out.items() if k != KT), out   # the rest goes
        assert len(_ledger(lib)) == n and (lib / "plan.json").read_bytes() == plan_before
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CC.main(["status", str(lib / "plan.json")])
        assert rc and KT in buf.getvalue() and ".ecadlib/marks/" in buf.getvalue(), buf.getvalue()
    # keep_both=False: the ledger kept branch A's r2; the hint names branch B's mark, and deleting it mends the part
    why = LG.Ledger(lib).trouble(KT)
    stray = [m for m in (lib / ".ecadlib/marks").iterdir() if m.name in why]
    assert len(stray) == 1 and stray[0].name.startswith(f"{KT}@r2@"), (why, stray)
    stray[0].unlink()
    assert LG.Ledger(lib).head(KT)["revision"] == 2
    out, _g = _publish(lib)
    assert out[KT] is None, out


# ---------------------------------------------------------------- R-FX17-6: TC / CO / BO / RCO only on output or untyped pins
def test_fx17_6_ntc_and_passive_tc_pins_are_not_counter_outputs():
    """Before (b5f7183): N?TC made a charger's NTC thermistor input (LT3652, LTC4054...) an output, and the name rule
    beat a passive type (MC33063's timing capacitor TC, typed passive), so both moved right. The counter output names
    (CO, BO, TC, RCO) now count for untyped or output pins only, and NTC is no TC."""
    from ecadlib import symbol_layout as SL
    for t in ("passive", "input", ""):
        assert SL.classify({"num": "1", "name": "NTC", "type": t}) != "other_out", t
    assert SL.classify({"num": "3", "name": "TC", "type": "passive"}) != "other_out"
    for t in ("passive", "power_in"):
        for n in ("CO", "BO", "RCO", "~{TC}"):
            assert SL.classify({"num": "12", "name": n, "type": t}) != "other_out", (n, t)
    for n in ("~{CO}", "~{BO}", "CO", "BO", "TC", "~{TC}", "RCO", "~{RCO}"):   # 74LS192 / 74HC161: still outputs
        for t in ("", "unspecified", "output"):
            assert SL.classify({"num": "12", "name": n, "type": t}) == "other_out", (n, t)
        assert SL.classify({"num": "12", "name": n, "type": "input"}) == "other_in", n
