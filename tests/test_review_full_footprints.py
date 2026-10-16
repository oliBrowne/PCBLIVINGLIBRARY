"""Regression tests for the full review of the footprint area (footprint_gen, stdparts, crosscheck)."""
from ecadlib import crosscheck as CC, footprint_gen as F, stdparts as S


def test_kemet_flex_termination_mpn_is_imperial_not_tdk_metric():
    # KEMET C0603X563K1RACTU is a 0603 (X = flexible termination, 563 = 56 nF); it used to match TDK's metric
    # pattern (C0603 + "X5") and resolve to the 0201 land
    for mpn, want in (("C0603X563K1RACTU", "C_0603_1608Metric"), ("C0603X683M1RACTU", "C_0603_1608Metric"),
                      ("C1206F224K5RACTU", "C_1206_3216Metric"), ("C0603C104K5RACTU", "C_0603_1608Metric"),
                      ("C0603X5R0J104M", "C_0201_0603Metric"), ("C1608X7R1H104K", "C_0603_1608Metric"),
                      ("C1005X8L1E104K", "C_0402_1005Metric")):                       # TDK metric stays metric
        assert S.resolve([""], "C", mpn)["footprint"] == want, mpn
    assert S.resolve(["0603"], "C", "C0603X563K1RACTU")["footprint"] == "C_0603_1608Metric"


def _htssop(ep_x, ep_y):
    return {"name": "HTSSOP-28-1EP_4.4x9.7mm_P0.65mm", "package_type": "gullwing", "pin_count": 28, "pitch": 0.65,
            "body_length": {"min": 9.6, "max": 9.8}, "body_width": {"min": 4.3, "max": 4.5}, "lead_span": {"min": 6.2, "max": 6.6},
            "terminal_length": {"min": 0.45, "max": 0.75}, "terminal_width": {"min": 0.19, "max": 0.30},
            "exposed_pad": {"length": {"nom": ep_x}, "width": {"nom": ep_y}}}


def test_gullwing_exposed_pad_checked_against_the_right_body_axis():
    # EP length is along X (across the rows, body_width E1), EP width along the rows (body_length D):
    # a real 2.75 x 6.2 mm pad on a 4.4 x 9.7 mm HTSSOP-28 was rejected as "as big as the body"
    ok = _htssop(2.75, 6.2)
    F.validate(ok)
    assert F.sanity(ok) == []
    _, rep = F.build(ok)
    assert rep["EP"][:2] == (2.75, 6.2)
    assert F.sanity(_htssop(5.0, 3.0))                   # wider than the 4.4 mm body across X: caught


def test_pin_positions_with_exposed_pad_and_integer_numbers():
    pos = [{"number": i + 1, "side": "L" if i < 4 else "R",
            "offset": (i - 1.5) * 1.27 if i < 4 else (1.5 - (i - 4)) * 1.27} for i in range(8)]
    spec = {"name": "SO8EP", "package_type": "gullwing", "lead_span": {"nom": 6.0}, "body_width": {"nom": 3.9},
            "body_length": {"nom": 4.9}, "terminal_width": {"nom": 0.4}, "terminal_length": {"nom": 0.6},
            "pin_positions": pos, "exposed_pad": {"length": {"nom": 2.4}, "width": {"nom": 3.1}}}
    F.validate(spec)
    txt, rep = F.build(spec)                              # used to raise KeyError 'pin_count'
    assert '(pad "9" smd rect' in txt
    assert rep["pads"][0]["num"] == "1" and "warning" not in rep
    assert not any(c["rule"] == "pin1" for c in rep["checks"])


def test_weak_table_match_does_not_accept_a_part_alone():
    # D_SOD-123 is a pad-table entry: the table compares only the pin count. That is not enough to skip the second
    # read, and when the second read cannot be made the part must stay unchecked, not "agreed"
    class R:
        cfg = {}
    res = {"spec": {"name": "D_SOD-123", "package_type": "gullwing", "pin_count": 2, "lead_span": {"nom": 9.9}},
           "symbol": {"pins": []}}
    orig = CC.second_read
    CC.second_read = lambda *a, **k: {"source": "second read", "against": "second read (m)", "status": "unchecked",
                                      "why": "refused", "compared": [], "agreed": [], "disagreed": [], "not_compared": []}
    try:
        rec = CC.run(R(), {"package": None, "part": "X"}, res, "m")
    finally:
        CC.second_read = orig
    assert [c["source"] for c in rec["checks"]] == ["standard table", "second read"]
    assert rec["checks"][0]["compared"] == ["pin_count"]
    assert rec["status"] == "unchecked"


def test_sipex_fixed_1117_is_gnd_not_adj():
    # SPX1117M3-L-3-3 is the fixed 3.3 V part (Sipex/MaxLinear write 3.3 as 3-3): pin 1 is GND, not ADJ
    from ecadlib.stdparts import symbol_text
    for mpn, want in [("SPX1117M3-L-3-3/TR", "GND"), ("SPX1117M3-L-5-0/TR", "GND"), ("SPX1117M3-L/TR", "ADJ"),
                      ("AMS1117-3.3", "GND"), ("AMS1117", "ADJ")]:
        text, _ = symbol_text("REG:1117", mpn)
        assert (f'"{want}"' in text) and (f'"{"ADJ" if want == "GND" else "GND"}"' not in text), (mpn, want)
