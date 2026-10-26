"""Bug hunt 2026-10-26 (evalb): the scorer must never credit a wrong part, nor fail a right one.
python3 -m tests.run_all test_hunt1026_evalb"""
import copy, random

from ecadlib.eval import runner as R, score as S
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- symbol: swapped pins spelled another way
def _pins(n=20):
    rp = [(str(i), f"P{i}") for i in range(1, n + 1)]
    rp[0], rp[1], rp[2] = ("1", "VI"), ("2", "VO"), ("3", "GND")
    return rp


def test_input_and_output_swapped_in_another_spelling_is_a_swap():
    # a regulator's input and output on each other's pins: ours VIN / VOUT where KiCad has VO / VI. VIN is KiCad's VI
    # (score.SPELLINGS), so this is two connections swapped, not naming noise, however many other pins agree
    rp = _pins()
    mp = list(rp)
    mp[0], mp[1] = ("1", "VOUT"), ("2", "VIN")
    f = S.score_symbol(mp, rp)["symbol_names"]
    assert not f["ok"] and f["moved"] == 2, f
    # spelled as KiCad does, the swap already failed
    mp[0], mp[1] = ("1", "VO"), ("2", "VI")
    assert not S.score_symbol(mp, rp)["symbol_names"]["ok"]


def test_a_spelling_on_its_own_pin_still_agrees():
    rp = _pins()
    mp = list(rp)
    mp[0], mp[1], mp[2] = ("1", "VIN"), ("2", "VOUT"), ("3", "VSS")
    f = S.score_symbol(mp, rp)["symbol_names"]
    assert f["ok"] and f["matched"] == 20, f


def test_ground_spelled_vss_on_a_signal_pin_is_on_the_wrong_pin():
    # ours VSS on pin 4 where KiCad has a signal, and KiCad's one GND on pin 3: ground moved, not a spelling
    rp = _pins()
    mp = list(rp)
    mp[3] = ("4", "VSS")
    f = S.score_symbol(mp, rp)["symbol_names"]
    assert not f["ok"] and f["moved"] == 1, f


def test_random_two_pin_swaps_never_pass():
    """Seeded property: a symbol whose names are KiCad's, each maybe spelled another way, with two pins' names swapped,
    never passes symbol_names, whatever its size."""
    rng = random.Random(1026)
    spell = {"VI": "VIN", "VO": "VOUT", "GND": "VSS", "EN": "Enable", "FB": "fb", "RESET": "~{RESET}", "PAD": "EP"}
    base = ["VI", "VO", "GND", "EN", "FB", "RESET", "PAD", "SDA", "SCL", "CLK", "MISO", "MOSI", "CS", "INT", "SW", "BOOT"]
    for _ in range(300):
        n = rng.randint(5, len(base))
        names = rng.sample(base, n) + [f"IO{i}" for i in range(rng.randint(0, 30))]
        ref = [(str(i), s) for i, s in enumerate(names, 1)]
        ours = [(num, spell.get(s, s) if rng.random() < 0.5 else s) for num, s in ref]
        i, j = rng.sample(range(n), 2)                  # two of the distinct named pins
        ours[i], ours[j] = (ours[i][0], ours[j][1]), (ours[j][0], ours[i][1])
        f = S.score_symbol(ours, ref)["symbol_names"]
        assert not f["ok"], (ref, ours, f)


# ---------------------------------------------------------------- printed_names: a swap printed elsewhere
def _sc(ours, ref):
    sc = S.score(None, None, ours, ref)
    return {"pass": False, "fields": dict(sc["fields"]), "errors": list(sc["errors"])}


def test_a_swap_printed_in_another_packages_pinout_is_not_credited():
    # KiCad (SOT-223): 1 GND 2 VOUT 3 VIN. Ours has VIN and VOUT swapped, as the datasheet's TO-92 pinout puts them;
    # "2 VIN" and "3 VOUT" are printed, but they are KiCad's own names on the other pin: a swap, not a spelling
    ref = [("1", "GND"), ("2", "VOUT"), ("3", "VIN")]
    ours = [("1", "GND"), ("2", "VIN"), ("3", "VOUT")]
    text = "SOT-223 pinout: 1 GND 2 VOUT 3 VIN. TO-92 pinout: 1 GND 2 VIN 3 VOUT."
    sc = _sc(ours, ref)
    assert not R.printed_names(sc, ours, ref, text)
    assert not sc["fields"]["symbol_names"]["ok"]
    # in another spelling (KiCad's VI / VO), the same
    ref2 = [("1", "GND"), ("2", "VO"), ("3", "VI")]
    sc = _sc(ours, ref2)
    assert not R.printed_names(sc, ours, ref2, text)


def test_ground_printed_on_a_supply_pin_is_not_credited():
    ref = [("1", "VCC"), ("2", "IN"), ("3", "OUT"), ("4", "GND")]
    ours = [("1", "AGND"), ("2", "IN"), ("3", "OUT"), ("4", "GND")]
    sc = _sc(ours, ref)
    assert not R.printed_names(sc, ours, ref, "Pin 1 AGND analog ground (other package)")
    assert not sc["fields"]["symbol_names"]["ok"]


def test_the_datasheets_spelled_out_names_are_still_credited():
    ref = [("1", "SwC"), ("2", "SwE"), ("3", "TC"), ("4", "GND")]
    ours = [("1", "Switch Collector"), ("2", "Switch Emitter"), ("3", "Timing Capacitor"), ("4", "GND")]
    sc = _sc(ours, ref)                                 # short_form credits these already; printed_names keeps it
    R.printed_names(sc, ours, ref, "1 Switch Collector 2 Switch Emitter 3 Timing Capacitor 4 GND")
    assert sc["fields"]["symbol_names"]["ok"]
    # a name the scorer can't tie to KiCad's by itself, printed after its pin's number: still credited
    ref = [("1", "IN"), ("2", "OUT"), ("3", "GND"), ("4", "X7")]
    ours = [("1", "IN"), ("2", "OUT"), ("3", "GND"), ("4", "Mode Select")]
    sc = _sc(ours, ref)
    assert not sc["fields"]["symbol_names"]["ok"]
    assert R.printed_names(sc, ours, ref, "4 Mode Select: tie high for PWM")
    assert sc["fields"]["symbol_names"]["ok"]


# ---------------------------------------------------------------- footprint tolerances (pinned, they passed)
def _soic8(row=5.4, pitch=1.27):
    pads = [[str(i + 1), -row / 2, -1.5 * pitch + i * pitch, 1.55, 0.6, 0] for i in range(4)]
    pads += [[str(8 - i), row / 2, -1.5 * pitch + i * pitch, 1.55, 0.6, 0] for i in range(4)]
    return {"pads": pads, "courtyard": [-3.7, -2.7, 3.7, 2.7]}


def test_footprint_tolerances_and_wrong_lands():
    ref = _soic8()
    rng = random.Random(7)
    for _ in range(200):                                # a pad moved: within TOL["pos"] passes, past it fails
        m = copy.deepcopy(ref)
        k, d = rng.randrange(8), rng.choice([0.02, 0.05, 0.06, 0.1, 0.3]) * rng.choice([1, -1])
        m["pads"][k][rng.choice([1, 2])] += d
        r = S.score(m, ref)
        # moving one pad shifts the pad box (and so the alignment) at most by d / 2
        if abs(d) > 2 * S.TOL["pos"] + 1e-9:
            assert not r["pass"], (k, d)
        if abs(d) <= S.TOL["pos"] / 2:
            assert r["pass"], (k, d, r["errors"])
    mir = copy.deepcopy(ref)
    for p in mir["pads"]:
        p[2] = -p[2]
    assert not S.score(mir, ref)["pass"]                # mirrored numbering
    assert S.score(S._turn(ref, 1), ref)["pass"]        # the whole land turned: the same land
    each = copy.deepcopy(ref)
    for p in each["pads"]:
        p[3], p[4] = p[4], p[3]
    assert not S.score(each, ref)["pass"]               # each pad turned in place
    assert not S.score(_soic8(row=5.0), ref)["pass"]    # pitch right, rows wrong
    miss = copy.deepcopy(ref); miss["pads"].pop()
    assert not S.score(miss, ref)["pass"]
    tab = copy.deepcopy(ref); tab["pads"].append(["9", 0, 0, 2.0, 3.0, 0])
    assert not S.score(tab, ref)["pass"]
    sw = copy.deepcopy(ref); sw["pads"][1][0], sw["pads"][2][0] = "3", "2"
    assert not S.score(sw, ref)["pass"]


def test_footprint_bench_report_with_no_pad_number_in_common():
    # a generated footprint that shares no pad number with KiCad's has no worst offset: the report printed nothing
    # but a TypeError (None formatted as a float)
    from ecadlib import footprint_bench as FB
    r = FB.compare({"pads": [["9", 0, 0, 1, 1, 0]], "courtyard": []}, {"pads": [["1", 0, 0, 1, 1, 0]], "courtyard": [0, 0, 1, 1]})
    assert r["worst_pos"] is None and not r["all_pads_ok"] and r["missing_in_ours"] == ["1"] and r["extra_in_ours"] == ["9"]
    s = {"footprints": 1, "unavailable": 0, "pads": 1, "pads_ok": 0, "footprints_all_pads_ok": 0, "courtyards_ok": 0,
         "check_clean": 1}
    txt = FB.report([dict(name="X", shows="s", **r)], s)
    assert "0/1" in txt and "KiCad-only pads ['1'], ours-only ['9']" in txt, txt


# ---------------------------------------------------------------- report: built wrong apart from held
def test_the_report_counts_built_wrong_apart_from_parts_not_built():
    # the 10-23 live report said "46 parts were wrong" where 11 were built wrong and 35 held / not built: a held part
    # is the safe outcome (built wrong must be 0), so the two are counted apart in report.json and report.md
    row = lambda i, ok, src, **k: {"id": i, "family": "qfn", "mpn": i, "pass": ok, "source": src, "fields": {},
                                   "usd": None, "tokens_in": 0, "tokens_out": 0, "tried": [], "errors": ["x: y"], **k}
    rows = [row("a", True, "standard"), row("b", False, "datasheet", kicad_differs_maker_land=True),
            row("c", False, "datasheet"), row("d", False, "standard"), row("e", False, "none"),
            row("f", False, "none", tried=["datasheet: failed (DatasheetError: no datasheet link gave a PDF: x)"])]
    s = R.summarize(rows)
    assert s["outcome"] == {"right": 2, "built_wrong": ["c", "d"], "not_built": ["e", "f"], "not_run": 0, "cases": 6}, s
    assert s["outcome"]["right"] == s["overall"]["right"]
    md = R.report_md({"meta": {"set": "mini", "set_version": 1, "model": "m", "sources": ["standard", "datasheet"],
                               "mode": "live", "cases": 6, "wall_s": 1, "wall_s_per_part": 0.2}, "summary": s, "cases": rows})
    head = md.split("\n")[2]
    assert "Right: 2/6" in head and "Built wrong: 2" in head and "2 not built" in head and "4 parts are not right" in head, head


@lite
def test_compare_flips_on_right_not_only_on_kicads_match(tmp_path, monkeypatch):
    # a part right by the maker's land in run A and built wrong in run B is broken (it was not listed: both runs
    # "fail" KiCad's match); one that moves from KiCad's match to the datasheet's is not broken
    import json
    row = lambda i, ok, **k: {"id": i, "family": "qfn", "mpn": i, "pass": ok, "source": "datasheet", "fields": {},
                              "usd": None, "tokens_in": 0, "tokens_out": 0, "tried": [], "errors": ["pads: off"], **k}
    a = [row("m", False, kicad_differs_maker_land=True), row("k", True), row("w", False)]
    b = [row("m", False), row("k", False, kicad_differs_body=True), row("w", False, kicad_differs_maker_land=True)]
    for name, rows in (("a", a), ("b", b)):
        rep = {"meta": {"model": "x", "mode": "replay", "cases": 3}, "summary": R.summarize(rows), "cases": rows}
        (tmp_path / f"{name}.json").write_text(json.dumps(rep))
    cmp = R.compare(tmp_path / "a.json", tmp_path / "b.json")
    assert cmp["flipped"]["broken"] == ["m"] and cmp["flipped"]["fixed"] == ["w"], cmp["flipped"]
    assert cmp["families"]["overall"][0]["right_rate"] == cmp["families"]["overall"][1]["right_rate"]
