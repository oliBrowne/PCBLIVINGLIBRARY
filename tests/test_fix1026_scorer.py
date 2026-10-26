"""Fixes 2026-10-26 (scorer): second review of PR #196, costeval.md R-CE-1..4 and the weak tests it named.
Offline: fake clients, no key, no network.  python3 -m tests.run_all test_fix1026_scorer"""
import json, tempfile
from pathlib import Path

from ecadlib import datasheet as D
from ecadlib.eval import runner as R, score as S
from tests.fixtures_lite import lite

KEY = "sk-ant-test-SECRET-123"


def _sc(ours, ref):
    sc = S.score(None, None, ours, ref)
    return {"pass": False, "fields": dict(sc["fields"]), "errors": list(sc["errors"])}


# ---------------------------------------------------------------- R-CE-2: the maker's ground on KiCad's EP / PAD / NC
DFN = [("1", "SW"), ("2", "VIN"), ("3", "EN"), ("4", "GND"), ("5", "PG"), ("6", "SS"), ("7", "FB"), ("8", "BST")]


def test_maker_ground_on_kicads_exposed_pad_is_right():
    # KiCad: GND on pin 4 and the pad 9 named EP (or PAD); the maker names the pad VSS / GNDPAD / GND, GND still on 4.
    # One net on one more pin (A4950K, DAC60504, TXB0104RGY ...), not ground moved off its pin: main credited these
    for pad in ("EP", "PAD"):
        ref = DFN + [("9", pad)]
        for nm in ("VSS", "GNDPAD", "GND"):
            mine = DFN + [("9", nm)]
            f = S.score_symbol(mine, ref)["symbol_names"]
            assert f["ok"] and f["moved"] == 0, (pad, nm, f)


def test_maker_ground_on_kicads_nc_pin_is_right():
    ref = [("1", "VIN"), ("2", "GND"), ("3", "EN"), ("4", "NC"), ("5", "VOUT")]
    mine = [("1", "VIN"), ("2", "GND"), ("3", "EN"), ("4", "GND"), ("5", "VOUT")]
    f = S.score_symbol(mine, ref)["symbol_names"]
    assert f["moved"] == 0, f


def test_ground_taken_off_its_pin_onto_the_pad_is_still_a_swap():
    # ours GND on the pad and the pad's name on KiCad's GND pin: GND left its pin, a swap
    ref = DFN + [("9", "EP")]
    mine = DFN[:3] + [("4", "EP")] + DFN[4:] + [("9", "GND")]
    f = S.score_symbol(mine, ref)["symbol_names"]
    assert not f["ok"] and f["moved"] >= 1, f
    # GND moved onto KiCad's NC pin with a signal name where GND was: a swap too
    ref = [("1", "VIN"), ("2", "GND"), ("3", "EN"), ("4", "NC"), ("5", "VOUT")]
    mine = [("1", "VIN"), ("2", "EN"), ("3", "NC"), ("4", "GND"), ("5", "VOUT")]
    assert not S.score_symbol(mine, ref)["symbol_names"]["ok"]


def test_printed_ground_on_kicads_nc_pin_is_credited_when_ground_stays_on_its_pin():
    # the names count needs the printed names: "4 Mode Select" and "5 GND" (KiCad NC) both printed after their pin
    ref = [("1", "IN"), ("2", "GND"), ("3", "X7"), ("4", "NC")]
    ours = [("1", "IN"), ("2", "GND"), ("3", "Mode Select"), ("4", "GND")]
    sc = _sc(ours, ref)
    assert not sc["fields"]["symbol_names"]["ok"]
    assert R.printed_names(sc, ours, ref, "1 IN 2 GND 3 Mode Select 4 GND"), sc["fields"]["symbol_names"]
    # VIN / VOUT swapped and printed so in another package: still not credited (evalb-02)
    ref = [("1", "GND"), ("2", "VOUT"), ("3", "VIN")]
    ours = [("1", "GND"), ("2", "VIN"), ("3", "VOUT")]
    sc = _sc(ours, ref)
    assert not R.printed_names(sc, ours, ref, "SOT-223: 1 GND 2 VOUT 3 VIN. TO-92: 1 GND 2 VIN 3 VOUT.")


