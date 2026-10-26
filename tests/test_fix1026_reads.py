"""Fixes 2026-10-26 (reads): Oliver's open questions on PR #196. Offline: no key, no network, no model.
    python3 -m tests.run_all test_fix1026_reads"""
import requests

from ecadlib import datasheet as D, rules as RU, userconfig as UC
from tests.fixtures_lite import lite

GATEWAY = ('[ai]\nprovider = "openai-compatible"\nbase_url = "https://llm-gateway.example.com/v1"\n'
           'model = "acme-pro-2"\napi_key_env = "ACME_KEY"\ndetect = false\n')   # a remote model with no known price


def _offline(monkeypatch, sent):
    monkeypatch.setattr(UC, "secret", lambda name, env=None: {"ACME_KEY": "dummy-acme"}.get(name))

    def offline(url, *a, **k):                        # nothing leaves this test: a request is recorded and fails
        sent.append(url)
        raise requests.ConnectionError("offline test")
    monkeypatch.setattr(requests, "post", offline)
    monkeypatch.setattr(requests, "get", offline)


def _guard(tmp_path, toml, yes=False, cap=None):
    (tmp_path / "r.toml").write_text(toml, encoding="utf-8")
    rd = D.Reader(tmp_path / "out", tmp_path / "cache", rules=RU.load(str(tmp_path / "r.toml")), log=None, yes=yes)
    if cap is not None:
        rd.set_cap(cap)
    _, body = D.build_request(("base64", b"%PDF-1.4"), "U1", "SOIC-8", None, rd.model)
    rd._guard_cost([{"key": "k", "name": "U1", "model": rd.model, "body": body, "url": "u.pdf", "sync": True,
                     "prov": {"pages": 40, "sent_as": "base64"}}])
    return rd


# ---------------------------------------------------------------- item 1: an unpriced remote model is not sent
@lite
def test_1_unpriced_remote_model_under_the_default_cap_is_refused(tmp_path, monkeypatch):
    """Before: only the default $10 cap applied, so a remote model with no known price was sent with a warning."""
    sent = []
    _offline(monkeypatch, sent)
    try:
        _guard(tmp_path, GATEWAY)
        raise AssertionError("an unpriced remote model was sent under the default cap")
    except D.DatasheetError as e:
        msg = str(e)
        assert "no price known for acme-pro-2" in msg and "nothing sent" in msg, msg
        assert "--yes" in msg and 'price_in' in msg, msg            # says how to send it
    try:                                              # a cap the person set can't be held either: refused
        _guard(tmp_path, GATEWAY, cap=10)
        raise AssertionError("an explicit cap let an unpriced model through")
    except D.DatasheetError as e:
        assert "no price known for acme-pro-2" in str(e) and "price_in" in str(e), e
    _guard(tmp_path, GATEWAY, yes=True)               # --yes: sent as the person said
    _guard(tmp_path, GATEWAY + "\n[datasheet]\nmax_cost_usd = 0\n")   # no cap, chosen: nothing to hold
    priced = GATEWAY + '\n[ai.profiles."acme-pro-2"]\nprice_in = 1.0\nprice_out = 5.0\n'
    rd = _guard(tmp_path, priced)                     # its price set: held by the cap like any model
    assert rd.committed_usd > 0, rd.committed_usd
    assert sent == [], sent


@lite
def test_1_cli_read_refuses_an_unpriced_model_without_yes(tmp_path, monkeypatch):
    from tests import test_datasheet_batch as TB
    sent = []
    _offline(monkeypatch, sent)
    (tmp_path / "r.toml").write_text(GATEWAY, encoding="utf-8")
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(TB.pdf_with(["U1 regulator", "Package outline"]))
    try:
        D.main([str(pdf), "--part", "U1", "--rules", str(tmp_path / "r.toml"), "-o", str(tmp_path / "cli")])
        raise AssertionError("ecadlib datasheet read sent an unpriced model")
    except SystemExit as e:
        assert "no price known for acme-pro-2" in str(e.code) and "--yes" in str(e.code), e.code
    assert sent == [], sent


@lite
def test_1_eval_run_refuses_an_unpriced_model(tmp_path, monkeypatch):
    """eval run has no --yes: an unpriced model is held at the guard (a price in [ai.profiles] lets it run)."""
    from ecadlib.eval import runner as R
    rd = R.EvalReader(tmp_path / "lib", tmp_path / "c", "k", None, None, log=lambda *a: None)
    monkeypatch.setattr(rd, "estimate", lambda jobs: {"usd_max": None, "models": ["acme-pro-2"], "rows": [], "parts": 1})
    try:
        rd._guard_cost([{"key": "k", "name": "U1", "model": "acme-pro-2", "body": {}, "sync": True}])
        raise AssertionError("eval sent an unpriced model")
    except D.DatasheetError as e:
        assert "no price known for acme-pro-2" in str(e), e


@lite
def test_1_visual_check_refuses_an_unpriced_model_without_a_yes(tmp_path, monkeypatch):
    """[agent.proof] max_cost_usd is 0 (no cap) by default: an unpriced model was sent with no check at all."""
    from ecadlib import visual_check as VC
    got = VC.unpriced_refusal("acme-pro-2", 0.0, yes=False)
    assert got and "no price known for acme-pro-2" in got and "nothing sent" in got, got
    assert VC.unpriced_refusal("acme-pro-2", 0.0, yes=True) is None
    assert "max_cost_usd = 5" in VC.unpriced_refusal("acme-pro-2", 5.0, yes=True)   # a cap set can't be held


# ---------------------------------------------------------------- R-FP-1..3: datasheet read checks (second review of #196)
def _wire_read(name, dims, pins, other, ptype="gullwing", desc=None):
    """A synthetic model answer through process() and assess() (footprint-scripts/rw.py of the review)."""
    import json
    from ecadlib import ds_route as DR
    d = lambda nm, v: {"name": nm, "label": nm[:2], "min": str(v[0]), "nom": str(v[1]), "max": str(v[2]), "unit": "mm",
                       "page": 9, "figure": name}
    wire = {"package": {"name": name, "description": desc or name, "package_type": ptype, "drawing": name, "unresolved": [],
                        "dims": [d(k, v) for k, v in dims.items()], "other_json": json.dumps(other)},
            "pin_table": {"page": 3, "figure": "Pin functions", "rows": len(pins)},
            "pins": [{"num": n, "name": nm, "type": "passive", "side": "left", "page": 3} for n, nm in pins],
            "symbol_description": "x", "notes": [], "embedded_instructions": []}
    res = D.process(D.parse_message({"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(wire)}]}),
                    "XPART", "ds.pdf")
    return DR.assess(res), res["needs_review"]


QFP_14X20 = {"body_length": (13.9, 14.0, 14.1), "body_width": (19.9, 20.0, 20.1), "lead_span": (16.95, 17.2, 17.45),
             "lead_span_y": (22.95, 23.2, 23.45), "pitch": ("", 0.65, ""), "terminal_length": (0.73, 0.88, 1.03),
             "terminal_width": (0.22, 0.3, 0.38), "height_max": ("", "", 3.4)}


def test_rfp1_a_rectangular_qfp_read_right_passes_the_feet_check():
    """JEDEC PQFP-100 14x20: lead_span crosses body_length (footprint_gen's frame); _feet_outside paired it with
    body_width and failed every correct rectangular QFP."""
    from ecadlib import footprint_gen as F
    other = {"pin_count": 100, "pins_left_right": 30, "pins_top_bottom": 20}
    (kind, why), rev = _wire_read("PQFP-100_14x20mm_P0.65mm", QFP_14X20, [(str(i), f"P{i}") for i in range(1, 101)],
                                  other, ptype="qfp")
    assert not any("does not fit outside the body" in r for r in rev), rev
    spec = {"package_type": "qfp", **{k: {"min": v[0], "nom": v[1], "max": v[2]} for k, v in QFP_14X20.items()
                                      if k not in ("pitch", "height_max")}}
    assert D._feet_outside(spec) == [] and F.sanity(dict(spec, name="Q", pin_count=100, pins_left_right=30,
                                                         pins_top_bottom=20, pitch=0.65)) == []
    bad = dict(spec, lead_span={"min": 14.0, "nom": 14.2, "max": 14.4})    # X span read close to the 14 mm body
    assert any("lead_span max 14.4 - body_length min 13.9" in r for r in D._feet_outside(bad)), D._feet_outside(bad)



def test_rfp3_a_j_lead_part_named_by_its_jedec_outline_is_not_checked_as_gull_wing():
    plcc = {"package_type": "qfp", "name": "MO-047-28", "description": "28-pin plastic chip carrier",
            "terminal_length": {"min": 0.8, "nom": 1.0, "max": 1.2}, "lead_span": {"min": 12.2, "nom": 12.4, "max": 12.6},
            "body_length": {"min": 11.4, "nom": 11.5, "max": 11.6}, "body_width": {"min": 11.4, "nom": 11.5, "max": 11.6}}
    assert D._feet_outside(plcc) == [], D._feet_outside(plcc)
    for name in ("MS-016-AB", "SOJ-26", "PLCC-44", "J-bend 20"):
        assert D._feet_outside(dict(plcc, name=name, description="")) == [], name
    assert D._feet_outside(dict(plcc, name="LQFP-28", description="")) != []    # a gull-wing QFP is still checked



SOT143 = {"body_length": (2.8, 2.9, 3.0), "body_width": (1.2, 1.3, 1.4), "lead_span": (2.1, 2.3, 2.5),
          "pitch": ("", 1.9, ""), "terminal_length": (0.15, 0.3, 0.45), "terminal_width": (0.38, 0.43, 0.48),
          "height_max": ("", "", 1.1)}
SOT143_POS = [{"number": "1", "side": "L", "offset": -0.75}, {"number": "2", "side": "L", "offset": 0.95},
              {"number": "3", "side": "R", "offset": 0.95}, {"number": "4", "side": "R", "offset": -0.95}]
SOT143_PINS = [("1", "S"), ("2", "D"), ("3", "G2"), ("4", "G1")]


def test_rfp2_a_reverse_pinned_part_named_so_passes_with_a_note_for_the_person():
    """NXP SOT143R (BF998R): numbered clockwise seen from the top by design. The counter-clockwise rule failed it and
    told the model as fact that pins run counter-clockwise: a model that complied built it mirrored."""
    rev_pos = [dict(p, side="R" if p["side"] == "L" else "L") for p in SOT143_POS]
    assert _wire_read("SOT-143", SOT143, SOT143_PINS, {"pin_count": 4, "pin_positions": SOT143_POS})[0] == ("ok", [])
    for name, desc in (("SOT-143R", None), ("SOT143R", None), ("SOT-143", "SOT-143 reverse pinning (BF998R)")):
        (kind, why), rev = _wire_read(name, SOT143, SOT143_PINS, {"pin_count": 4, "pin_positions": rev_pos}, desc=desc)
        assert kind == "ok", (name, why)
        assert any("clockwise" in r and "reverse" in r for r in rev), rev       # the person checks it
        assert not any("numbered counter-clockwise seen from the top" in r for r in rev), rev


def test_rfp2_clockwise_without_a_reverse_name_fails_without_telling_the_model_it_is_impossible():
    rev_pos = [dict(p, side="R" if p["side"] == "L" else "L") for p in SOT143_POS]
    (kind, why), rev = _wire_read("SOT-143", SOT143, SOT143_PINS, {"pin_count": 4, "pin_positions": rev_pos})
    assert kind == "failed", why
    line = next(r for r in why if r.startswith("pin_positions"))
    assert "mirrored" in line and "reverse" in line, line                        # either is possible: the drawing says
    assert "pins are numbered counter-clockwise seen from the top" not in line, line
    (kind, why), _ = _wire_read("SOT-143", SOT143, SOT143_PINS, {"pin_count": 4, "pin_positions": rev_pos},
                                desc="SOT-143 with reverse current protection")    # not a reverse-pinning marker
    assert kind == "failed", why


def test_rfp2_one_side_clockwise_the_other_counter_clockwise_still_fails():
    mixed = [dict(p) for p in SOT143_POS]
    mixed[2]["offset"], mixed[3]["offset"] = -0.95, 0.95                          # R side now runs down: mixed
    (kind, why), rev = _wire_read("SOT-143R", SOT143, SOT143_PINS, {"pin_count": 4, "pin_positions": mixed})
    assert kind == "failed" and any("mirrored" in r for r in why), why



# ---------------------------------------------------------------- item 2: a read that still fails at the end is held
def _msg(name, dims, pins, other, ptype="gullwing"):
    import json
    d = lambda nm, v: {"name": nm, "label": nm[:2], "min": str(v[0]), "nom": str(v[1]), "max": str(v[2]), "unit": "mm",
                       "page": 9, "figure": name}
    wire = {"package": {"name": name, "description": name, "package_type": ptype, "drawing": name, "unresolved": [],
                        "dims": [d(k, v) for k, v in dims.items()], "other_json": json.dumps(other)},
            "pin_table": {"page": 3, "figure": "Pin functions", "rows": len(pins)},
            "pins": [{"num": n, "name": nm, "type": "passive", "side": "left", "page": 3} for n, nm in pins],
            "symbol_description": "x", "notes": [], "embedded_instructions": []}
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(wire)}], "model": "claude-opus-5-5"}


def _librarian_read(ext):
    """The part DSPART1 read with this answer through the librarian (tests.test_datasheet_batch.run2): its state."""
    import tempfile
    from tests import test_crosscheck as TC, test_datasheet_batch as TB, test_ds_route as TR
    with tempfile.TemporaryDirectory() as d:
        items, _, _ = TB.run2(d, TC.API(lambda k, m, b: TR.on_page(ext)), reader_kw={"batch": False})
        return items["DSPART1"]


def test_2_a_last_answer_failing_a_layout_check_is_held_before_anything_is_written():
    """Before: built with its review items (the eval counted it built: built wrong if it was wrong), held only later
    by the release check's verify. Judged after every rescue the route has (the cross-check's settle, a checked
    standard land): tests/test_crosscheck_settle_1022 keeps a turned SOIC-8 read built from the table."""
    import copy
    from tests import test_datasheet as TD
    ext = copy.deepcopy(TD.GOOD)
    ext["package"]["name"] = "XQFN-16_3x3mm"                  # no checked package to fall back on
    ext["package"]["exposed_pad"] = None
    ext["pins"] = ext["pins"][:16]
    ext["package"]["terminal_width"].update(min=0.45, nom=0.5, max=0.55)   # leads as wide as the pitch: they touch
    it = _librarian_read(ext)
    a = it["librarian"]
    assert any(D.HELD_FAILED in t and "leads would touch" in t for t in a["tried"]), a["tried"]
    assert not it.get("footprint", {}).get("kicad_mod"), it.get("footprint")
    ext["package"]["terminal_width"].update(min=0.18, nom=0.25, max=0.3)    # the same read, right: built
    it = _librarian_read(ext)
    assert not any(D.HELD_FAILED in t for t in it["librarian"]["tried"]), it["librarian"]["tried"]
    assert it.get("footprint", {}).get("kicad_mod") or it["footprint"].get("file"), it.get("footprint")


def test_2_a_read_failing_only_a_soft_check_is_still_built(tmp_path, monkeypatch):
    """LT3652IDD (benchmark 2026-10-23): a real 0.45 mm pitch fails self-check's standard-pitch list; it was built
    right and counted right. Holding it would lose a right part, so self-check lines never hold."""
    from ecadlib import ds_route as DR
    dims = dict(SOT143, pitch=("", 1.85, ""))
    pos = [dict(p, offset=o) for p, o in zip(SOT143_POS, (-0.925, 0.925, 0.925, -0.925))]
    msg = _msg("SOT-143", dims, SOT143_PINS, {"pin_count": 4, "pin_positions": pos})
    res = D.process(D.parse_message(msg), "XPART", "ds.pdf")
    kind, why = DR.assess(res)
    assert kind == "failed" and all(w.startswith("self-check:") for w in why), why
    assert DR.hold_reasons(res) == []
    assert DR.hold_reasons({"needs_review": ["schema: (root): 'pad_table' is a required property"]}) == []   # KTY83
    assert DR.hold_reasons({"needs_review": ["terminal_width max 0.6 >= pitch 0.5: leads would touch"]}) != []
    bank = "symbol: AIN13 on pins 5, 18, and the AIN bank has no AIN4: a channel read onto another channel's pin (check the pin table)"
    assert DR.hold_reasons({"needs_review": [bank]}, fp_needed=False) == [bank]     # wired to the wrong channel
    for soft in ("symbol: pins with no pad on the footprint ['2'] (the footprint's pads are 1, 3, 4)",   # TO-252-2
                 "symbol: footprint pads with no pin ['B10'] (check the pad numbers against the pin table)",   # NC ball
                 "symbol: pin 1 has an unusable name ''"):
        assert DR.hold_reasons({"needs_review": [soft]}) == [], soft             # eval gate answers scored right


def test_2_eval_names_the_hold():
    from ecadlib.eval import runner as R
    row = {"pass": False, "source": "none", "tried": [f"datasheet reader: failed ({D.HELD_FAILED}: pin_positions ...)"]}
    assert R.failure_type(row, None, None) == "held_failed_read"



# ---------------------------------------------------------------- R-PR-3: one QFP frame for every check and the build
def _lqfp128(bl, bw, ls, lsy):
    import copy
    from tests import test_datasheet as TD
    dim = lambda lbl, lo, nom, hi: {"label": lbl, "min": lo, "nom": nom, "max": hi, "unit": "mm", "page": 5,
                                    "figure": "LQFP128"}
    e = copy.deepcopy(TD.GOOD)
    e["package"].update(name="LQFP-128_14x20mm_P0.5mm", description="LQFP-128 14x20", package_type="qfp", pin_count=128,
                        pitch=dim("e", None, 0.5, None), body_length=dim(*bl), body_width=dim(*bw), lead_span=dim(*ls),
                        lead_span_y=dim(*lsy), terminal_length=dim("L", 0.45, 0.6, 0.75),
                        terminal_width=dim("b", 0.17, 0.22, 0.27), exposed_pad=None, pins_left_right=38,
                        pins_top_bottom=26, drawing="MS-026 BHB")
    e["pins"] = [{"num": str(i), "name": f"P{i}", "type": "passive", "side": "left", "page": 3} for i in range(1, 129)]
    e["pin_table"]["rows"] = 128
    return e


def test_rpr3_a_rectangular_qfp_read_either_way_round_passes_and_builds_the_same_pads():
    """LQFP-128 14x20 read by its JEDEC labels (body_length D1 = 20, body_width E1 = 14, lead_span E = 16,
    lead_span_y D = 22): footprint_gen refused it ("lead_span 16 is not bigger than body_length 20 ... swapped?"),
    while the other frame failed _feet_outside. Both are the same part: the spans place the pads."""
    import re
    from ecadlib import ds_route as DR, footprint_gen as F
    D1, E1, E, Dy = ("D1", 19.9, 20, 20.1), ("E1", 13.9, 14, 14.1), ("E", 15.8, 16, 16.2), ("D", 21.8, 22, 22.2)
    built = []
    for e in (_lqfp128(D1, E1, E, Dy), _lqfp128(E1, D1, E, Dy)):   # JEDEC labels; footprint_gen's frame (X first)
        res = D.process(e, "TESTPART", "ds.pdf")
        assert DR.assess(res) == ("ok", []), DR.assess(res)
        txt, rep = F.build(res["spec"])
        pads = re.findall(r'\(pad "(\d+)" smd \w+ \(at ([-\d.]+) ([-\d.]+)', txt)
        assert len(pads) == 128 and pads[0][1:] == ("-7.675", "-9.25"), pads[0]
        built.append(re.sub(r"\(tstamp [^)]*\)|\(uuid [^)]*\)", "", txt))
    assert built[0] == built[1]                       # the same footprint, body outline included
    bad = _lqfp128(D1, E1, ("E", 13.8, 14.0, 14.2), Dy)   # E read as E1: the X span fits neither body
    assert DR.assess(D.process(bad, "TESTPART", "ds.pdf"))[0] == "failed"



# ---------------------------------------------------------------- R-PR-4: an exposed pad numbered after gap-numbered leads
def _soic14_16_ep(ep_num):
    import copy
    from tests import test_datasheet as TD
    dim = lambda lbl, lo, nom, hi: {"label": lbl, "min": lo, "nom": nom, "max": hi, "unit": "mm", "page": 5, "figure": "X"}
    e = copy.deepcopy(TD.GOOD)
    p = e["package"]
    p.update(name="SOIC-14-16-1EP_3.9x9.9mm_P1.27mm", description="SOIC-16 with pins 13, 14 removed, EP",
             package_type="gullwing", pin_count=14, pitch=dim("e", None, 1.27, None), body_length=dim("D", 9.8, 9.9, 10.0),
             body_width=dim("E1", 3.8, 3.9, 4.0), lead_span=dim("E", 5.8, 6.0, 6.2), terminal_length=dim("L", 0.4, None, 1.27),
             terminal_width=dim("b", 0.31, None, 0.51), pins_left_right=None, pins_top_bottom=None,
             exposed_pad={"length": dim("E2", None, 2.0, None), "width": dim("D2", None, 2.0, None)}, drawing="X")
    p["pin_positions"] = [{"number": str(i), "side": "L", "offset": round((i - 1 - 3.5) * 1.27, 4)} if i <= 8 else
                          {"number": str(i), "side": "R", "offset": round((3.5 - (i - 9)) * 1.27, 4)}
                          for i in list(range(1, 13)) + [15, 16]]
    e["pins"] = [{"num": str(i), "name": f"P{i}", "type": "passive", "side": "left", "page": 3}
                 for i in list(range(1, 13)) + [15, 16]] + [{"num": ep_num, "name": "GND", "type": "power", "side": "left",
                                                              "page": 3}]
    e["pin_table"]["rows"] = 14
    return e


def test_rpr4_an_exposed_pad_numbered_after_gap_numbered_leads_is_read_and_built_with_its_number():
    """SOIC-14-16 + EP: leads 1-12, 15, 16 and the thermal pad 17 in the pin table. check_pins wanted EP = 15
    (pin_count + 1), which is a lead, and no read schema can carry exposed_pad.number: held forever."""
    import re
    from ecadlib import ds_route as DR, footprint_gen as F
    res = D.process(_soic14_16_ep("17"), "TESTPART", "ds.pdf")
    kind, why = DR.assess(res)
    assert kind == "ok", why
    assert res["spec"]["exposed_pad"]["number"] == "17"
    txt, _ = F.build(res["spec"])
    assert sorted(re.findall(r'\(pad "(\d+)"', txt), key=int)[-3:] == ["15", "16", "17"]
    kind, why = DR.assess(D.process(_soic14_16_ep("15"), "TESTPART", "ds.pdf"))   # the EP on a lead's number
    assert kind != "ok", why



def test_2_false_hold_sweep_cases_are_not_held():
    """The second review's false-hold sweep (1,601 correct specs): three checks that failed right parts would have
    held them under the failed-read hold. A pad that fits the body turned (footprint_gen turns it), a row exactly as
    long as the body (float noise), and a real tiny DFN pad are not held."""
    from ecadlib import ds_route as DR
    mm = lambda a, b, c=None: {"min": a, "nom": c if c is not None else round((a + b) / 2, 4), "max": b}
    rev = []
    D.sanity({"package_type": "gullwing", "pin_count": 8, "pitch": 1.27, "body_length": mm(4.8, 5.0),
              "body_width": mm(3.8, 4.0), "lead_span": mm(5.8, 6.2), "terminal_length": mm(0.4, 1.27),
              "terminal_width": mm(0.31, 0.51), "exposed_pad": {"length": mm(2.31, 2.51), "width": mm(3.71, 3.91)}}, rev)
    assert not any(r.startswith("exposed_pad.") for r in rev), rev            # SOIC-8-1EP 2.41 x 3.81
    rev = []
    D.sanity({"package_type": "gullwing", "pin_count": 8, "pitch": 1.27, "body_length": mm(4.8, 5.0),
              "body_width": mm(3.8, 4.0), "lead_span": mm(5.8, 6.2), "terminal_length": mm(0.4, 1.27),
              "terminal_width": mm(0.31, 0.51), "exposed_pad": {"length": mm(4.9, 5.1), "width": mm(3.71, 3.91)}}, rev)
    assert any(r.startswith("exposed_pad.length") for r in rev), rev          # too big either way: still failed
    rev = []
    D.sanity({"package_type": "qfn", "pin_count": 8, "pitch": 0.9, "pins_left_right": 4, "pins_top_bottom": 0,
              "body_length": mm(1.4, 1.6), "body_width": mm(3.1, 3.3), "terminal_length": mm(0.3, 0.5),
              "terminal_width": mm(0.5, 0.6)}, rev)
    assert not any("span 3.30 mm > body_width" in r for r in rev), rev       # MicroCrystal C7 SON-8
    small = "exposed_pad.width: 0.275 mm is implausibly small for an exposed pad (unit mislabelled?)"
    assert DR.hold_reasons({"needs_review": [small]}) == []                   # a 1 x 1 DFN's real pad
    slip = "exposed_pad.width: 0.057 mm is implausibly small for an exposed pad (unit mislabelled?)"
    assert DR.hold_reasons({"needs_review": [slip]}) == [slip]                # an inch value labelled mm



# ---------------------------------------------------------------- fixes2 (re-review): QFP reads (fixes2-scripts/qfpbrute.py)
def _qdim(lbl, nom, tol=0.1):
    return {"label": lbl, "min": round(nom - tol, 3), "nom": nom, "max": round(nom + tol, 3), "unit": "mm", "page": 5,
            "figure": "QFP"}


def _qfp_read(c):
    """A synthetic QFP answer from a case {name, n, e, bl, bw, ls, lsy, nlr, ntb, b, epl, epw} -> (assess, hold, pads
    or "refused")."""
    import copy, re
    from ecadlib import ds_route as DR, footprint_gen as F
    from tests import test_datasheet as TD
    e = copy.deepcopy(TD.GOOD)
    n = c["n"]
    e["package"].update(name=c["name"], description=c["name"], package_type="qfp", pin_count=n, pitch=_qdim("e", c["e"]),
                        body_length=_qdim("D1", c["bl"]), body_width=_qdim("E1", c["bw"]), lead_span=_qdim("D", c["ls"], 0.2),
                        lead_span_y=_qdim("E", c["lsy"], 0.2) if c.get("lsy") else None,
                        terminal_length=_qdim("L", 0.6, 0.15), terminal_width=_qdim("b", c.get("b", 0.22), 0.05),
                        exposed_pad={"length": _qdim("D2", c["epl"]), "width": _qdim("E2", c["epw"])} if c.get("epl") else None,
                        pins_left_right=c.get("nlr"), pins_top_bottom=c.get("ntb"), drawing="X", height_max=_qdim("A", 1.5))
    e["pins"] = [{"num": str(i), "name": f"P{i}", "type": "passive", "side": "left", "page": 3} for i in range(1, n + 1)]
    if c.get("epl"):
        e["pins"].append({"num": str(n + 1), "name": "EP", "type": "power", "side": "left", "page": 3})
    e["pin_table"]["rows"] = n
    res = D.process(e, "TESTPART", "ds.pdf")
    try:
        txt, _ = F.build(res["spec"])
        pads = sorted((m[0], round(float(m[1]), 3), round(float(m[2]), 3), round(float(m[3]), 3), round(float(m[4]), 3))
                      for m in re.findall(r'\(pad "(\d+)" smd \w+ \(at ([-\d.]+) ([-\d.]+)[^)]*\) \(size ([\d.]+) ([\d.]+)', txt))
    except Exception:
        pads = "refused"
    return DR.assess(res)[0], DR.hold_reasons(res), pads


QFP52 = {"name": "QFP-52_12x14mm_P0.8mm", "n": 52, "e": 0.8, "bl": 12, "bw": 14, "ls": 14, "lsy": 16, "nlr": 14, "ntb": 12,
         "b": 0.35}


def test_fx2_1_spans_swapped_on_a_rectangular_qfp_never_build_wrong():
    """QFP-52 12x14 read with lead_span / lead_span_y swapped (body and rows right): qfp_frame turned the body to fit
    the wrong spans and built every toe 1 mm off. The body turns only when the pin rows agree."""
    kind, _, truth = _qfp_read(QFP52)
    assert kind == "ok" and truth != "refused"
    kind, hold, pads = _qfp_read(dict(QFP52, ls=16, lsy=14))
    assert pads == "refused" or pads == truth, (kind, hold)
    assert kind != "ok", kind
    turned = _qfp_read(dict(QFP52, bl=14, bw=12))                 # the body read by its JEDEC labels: still turned
    assert turned[0] == "ok" and turned[2] == truth, turned[:2]
    for t in ({"name": "QFP-48_12x14mm_P0.8mm", "n": 48, "e": 0.8, "bl": 12, "bw": 14, "ls": 14, "lsy": 16, "nlr": 13,
               "ntb": 11, "b": 0.35},
              {"name": "QFP-40_10x12mm_P0.8mm", "n": 40, "e": 0.8, "bl": 10, "bw": 12, "ls": 12, "lsy": 14, "nlr": 11,
               "ntb": 9, "b": 0.35}):
        right = _qfp_read(t)[2]
        got = _qfp_read(dict(t, ls=t["lsy"], lsy=t["ls"]))[2]
        assert got in ("refused", right), t["name"]


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_fix1026_reads"]))
