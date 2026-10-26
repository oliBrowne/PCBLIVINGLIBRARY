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


# ---------------------------------------------------------------- R-CE-1: spelled-out names swapped
MC = [("1", "SwC"), ("2", "SwE"), ("3", "TC"), ("4", "GND"), ("5", "CII"), ("6", "VCC"), ("7", "Ipk"), ("8", "DC")]


def test_spelled_out_names_swapped_and_printed_in_another_package_are_not_credited():
    mine = [("1", "Switch Emitter"), ("2", "Switch Collector"), ("3", "Timing Capacitor"), ("4", "GND"),
            ("5", "Comparator Inverting Input"), ("6", "VCC"), ("7", "Ipk Sense"), ("8", "Driver Collector")]
    text = ("SOIC-8: 1 Switch Collector 2 Switch Emitter 3 Timing Capacitor 4 GND 5 Comparator Inverting Input 6 VCC "
            "7 Ipk Sense 8 Driver Collector. DFN-8: 1 Switch Emitter 2 Switch Collector 3 Timing Capacitor")
    sc = _sc(mine, MC)
    assert not R.printed_names(sc, mine, MC, text), sc["fields"]["symbol_names"]
    assert not sc["fields"]["symbol_names"]["ok"]
    # unswapped, the same names are still credited
    good = [("1", "Switch Collector"), ("2", "Switch Emitter")] + mine[2:]
    sc = _sc(good, MC)
    R.printed_names(sc, good, MC, text)
    assert sc["fields"]["symbol_names"]["ok"], sc["fields"]["symbol_names"]


def test_spelled_out_swap_fails_the_scorer_on_a_big_symbol():
    # 18 of 20 names agree (over the 0.8 bar): the swap itself must fail it
    ref = MC[:4] + [(str(i), f"P{i}") for i in range(5, 21)]
    mine = [("1", "Switch Emitter"), ("2", "Switch Collector")] + ref[2:]
    f = S.score_symbol(mine, ref)["symbol_names"]
    assert not f["ok"] and f["moved"] == 2, f
    mine = [("1", "Switch Collector"), ("2", "Switch Emitter")] + ref[2:]
    assert S.score_symbol(mine, ref)["symbol_names"]["ok"]


# ---------------------------------------------------------------- R-CE-3: a maybe-billed split_steps redo
def test_a_maybe_billed_redo_is_marked_and_keeps_its_hold():
    from ecadlib.llm import run as LR
    from tests import test_llm as TL
    from tests.test_hunt1026_costfetch import _steps_body
    u = {"input_tokens": 1000, "output_tokens": 200}
    p = {"rails": ["split_steps", "json_repair", "schema_in_prompt", "feedback_retry"], "tier": "small",
         "guided": True, "assist": "auto"}
    prof = {"context": 8192, "pdf": False, "vision": False, "json_schema": False}
    answers = [TL.IDENT, TL.DIMS, TL.PINS]

    class Seq:
        def messages(self, body, headers=None):
            if not answers:                           # the redo: a read timeout, the API may have run it
                e = D.APIError("API request failed: ReadTimeout (may have been billed)")
                e.maybe_billed = True
                raise e
            return {"content": [{"type": "text", "text": json.dumps(answers.pop(0))}], "stop_reason": "end_turn",
                    "usage": u}
    inf = {"plan": p, "prof": prof, "ep": {"name": "x", "provider": "openai-compatible"}}
    msg = LR.ask({"body": _steps_body(), "part": "TESTPART", "package": None}, inf, None,
                 lambda m: ("failed", ["symbol: pin count"]), Seq(), allow_feedback=True, guard=lambda b: None)
    assert msg["ecadlib"]["feedback"].get("maybe_billed") is True, msg["ecadlib"]["feedback"]

    # the Reader: the first answer's hold gives way to its usage, the redo's hold (the last, the feedback guard's) stays
    # and is booked in cost.jsonl as maybe billed
    with tempfile.TemporaryDirectory() as d:
        rd = D.Reader(Path(d) / "o", Path(d) / "c", key=KEY, log=None, sleep=lambda s: None)
        model = "claude-opus-5-5"
        msg = {"type": "message", "model": model, "stop_reason": "end_turn", "content": [{"type": "text", "text": ""}],
               "usage": {"input_tokens": 10000, "output_tokens": 1000},
               "ecadlib": {"feedback": {"after": "error: ReadTimeout", "maybe_billed": True}}}
        rd._holds["k"], rd.committed_usd = [3.0, 1.25], 4.25
        from ecadlib import ds_route as DR
        usd = DR.usage_usd(model, msg["usage"])
        try:
            rd._store({"key": "k", "part": "TESTPART", "model": model}, msg, "sync")
        except D.DatasheetError:
            pass                                      # an empty answer: what it cost is what this test is about
        assert abs(rd.committed_usd - (usd + 1.25)) < 1e-9, (rd.committed_usd, usd)
        led = [json.loads(x) for x in (rd.dir / "cost.jsonl").read_text(encoding="utf-8").splitlines()]
        mb = [x for x in led if x.get("how") == "maybe billed"]
        assert len(mb) == 1 and mb[0]["usd"] == 1.25 and mb[0].get("purpose") == "feedback", led
        # no redo flag: the whole hold gives way to the usage, as before
        rd._holds["k2"], rd.committed_usd = [3.0, 1.25], 4.25
        plain = dict(msg, ecadlib={"feedback": {"after": "error: x"}})
        try:
            rd._store({"key": "k2", "part": "TESTPART", "model": model}, plain, "sync")
        except D.DatasheetError:
            pass
        assert abs(rd.committed_usd - usd) < 1e-9, rd.committed_usd


# ---------------------------------------------------------------- R-CE-4: a web search 4xx is not billed
def test_a_web_search_http_error_is_not_booked_as_billed():
    from tests.test_ds_search import _reader, _fails, DEAD
    from ecadlib import ds_search as WS

    class Err:
        def __init__(self, e):
            self.e, self.sent = e, []

        def messages(self, body, headers=None):
            self.sent.append(body)
            raise self.e
    est = WS.estimate("claude-opus-5-5", WS.MAX_USES, fallback=True)
    for e, billed in ((D.APIError("API HTTP 401: authentication_error", 401, "authentication_error"), False),
                      (D.APIError("API HTTP 400: invalid_request_error", 400, "invalid_request_error"), False),
                      (D.APIError("API HTTP 500: api_error", 500, "api_error", True), False),
                      (D.DatasheetError("timed out"), True)):
        if isinstance(e, D.APIError) and e.status is None:
            e.maybe_billed = True
        with tempfile.TemporaryDirectory() as d:
            rd = _reader(d, Err(e), web_search=True)
            assert "search failed" in _fails(rd)
            want = est if billed else 0.0
            assert abs(rd.committed_usd - want) < 1e-9, (e, rd.committed_usd)
            led = [json.loads(x) for x in open(Path(d) / "out" / "datasheet" / "cost.jsonl")]
            assert [round(x["usd"], 6) for x in led if x["via"] == "web search"] == [round(want, 6)], led
    # a timeout the Client marks maybe billed: held
    e = D.APIError("API request failed: ReadTimeout")
    e.maybe_billed = True
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, Err(e), web_search=True)
        _fails(rd)
        assert abs(rd.committed_usd - est) < 1e-9


# ---------------------------------------------------------------- weak tests made end-to-end
@lite
def test_cli_cap_nan_is_refused_by_the_real_parser(tmp_path, monkeypatch):
    """--max-cost-usd nan through the real command line (ecadlib eval run, ecadlib datasheet): argparse refuses it
    before anything is built or sent."""
    import contextlib, io
    from ecadlib import cli
    from ecadlib.eval import runner as ER
    monkeypatch.setattr(ER, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran with a nan cap")))
    monkeypatch.setattr(D, "Reader", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Reader with a nan cap")))
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    for argv in (["eval", "run", "--max-cost-usd", "nan"], ["eval", "run", "--max-total-usd", "inf"],
                 ["eval", "run", "--max-cost-usd", "-1"],
                 ["datasheet", str(tmp_path / "x.pdf"), "--part", "X", "--max-cost-usd", "nan"]):
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                cli.main(argv)
            raise AssertionError(f"{argv} was not refused")
        except SystemExit as e:
            assert e.code == 2, (argv, e.code, err.getvalue())
        assert "max-" in err.getvalue() and "usd" in err.getvalue(), err.getvalue()


def test_bom_case_code_mm_0603_is_not_built_on_an_imperial_0603_land():
    """metric_header end to end, read_bom -> triage: a bare 0603 in a 'Case Code (mm)' column is the metric 0603 (an
    inch 0201), never the inch 0603; in a column naming both systems a bare 0603 is not turned into an 0201."""
    from ecadlib import bom_triage as T
    from ecadlib.kicad_index import KiCadIndex
    idx = KiCadIndex([])
    idx.symbols = []
    with tempfile.TemporaryDirectory() as d:
        for head, wrong in (("Case Code (mm)", "0603"), ("Case (Imperial/Metric)", "0201")):
            f = Path(d) / "bom.csv"
            f.write_text(f"Designator,Comment,{head},Quantity\nR1,10k,0603,1\n", encoding="utf-8")
            lines, _ = T.read_bom(f)
            rows = T.triage(lines, idx, [], None)
            assert len(rows) == 1 and rows[0]["package"] != wrong, (head, rows[0])
            assert wrong not in (rows[0].get("local_footprint") or ""), (head, rows[0])


@lite
def test_lifecycle_st_part_never_takes_stackpoles_status(tmp_path, monkeypatch):
    """same_maker end to end through the lifecycle check: a BOM line by "ST" never gets Stackpole's listing (its
    status, stock or maker), from Mouser (a list of listings) or DigiKey (one product)."""
    from ecadlib import lifecycle as L
    from tests.test_lifecycle import FakeHTTP
    monkeypatch.setenv("MOUSER_API_KEY", "k")
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "id")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "s")
    parts = {"a": {"mpn": "XC0603A", "manufacturer": "ST", "lcsc": "", "name": "XC0603A", "sources": ["bom"],
                   "component_id": "", "a365_value": None}}
    mouser = {"Errors": [], "SearchResults": {"NumberOfResult": 2, "Parts": [
        {"ManufacturerPartNumber": "XC0603A", "Manufacturer": "Stackpole Electronics Inc", "LifecycleStatus": "Obsolete",
         "AvailabilityInStock": "0"},
        {"ManufacturerPartNumber": "XC0603A", "Manufacturer": "Bourns", "LifecycleStatus": "Obsolete",
         "AvailabilityInStock": "0"}]}}
    out, _ = L.check_parts(parts, [L.MouserLifecycle(FakeHTTP({"search/partnumber": (200, mouser)}))])
    assert out["a"]["status"] == "unknown" and "Stackpole" not in out["a"]["raw"], out["a"]
    dk = {"Product": {"ManufacturerProductNumber": "XC0603A", "Manufacturer": {"Name": "Stackpole Electronics Inc"},
                      "ProductStatus": {"Status": "Obsolete"}, "QuantityAvailable": 0}}
    out, _ = L.check_parts(parts, [L.DigiKeyLifecycle(FakeHTTP({"oauth2/token": (200, {"access_token": "t"}),
                                                                 "productdetails": (200, dk)}))])
    assert out["a"]["status"] == "unknown" and not out["a"]["results"][0]["found"], out["a"]
    # by STMicroelectronics: taken
    mouser["SearchResults"]["Parts"][1]["Manufacturer"] = "STMicroelectronics"
    out, _ = L.check_parts(parts, [L.MouserLifecycle(FakeHTTP({"search/partnumber": (200, mouser)}))])
    assert out["a"]["status"] == "obsolete" and out["a"]["results"][0]["manufacturer"] == "STMicroelectronics", out["a"]
