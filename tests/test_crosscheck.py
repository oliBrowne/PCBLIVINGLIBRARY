"""Offline tests for ecadlib.crosscheck (same part, same answer) and ecadlib.llm.sampling.   python3 -m tests.test_crosscheck

Nothing live: Claude answers come from fakes in the documented Messages API shape (test_datasheet_batch.FakeAPI), the
OpenAI-compatible side from test_llm's local stdlib server. Extractions are test_datasheet's SYNTHETIC fixtures; the
3-page PDF is SYNTHETIC text."""
import base64, copy, json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import crosscheck as CC, datasheet as D, ds_route as DR, rules as RU
from ecadlib.llm import profiles as PR, run as LR, sampling as SM
from tests import test_datasheet as TD, test_datasheet_batch as TB, test_ds_route as TR, test_eval as TE, test_llm as TLL

URL = "https://a/ds.pdf"
QFN = "VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm"            # not in the standard table: the second read decides
PDF3 = None


def pdf3():
    global PDF3
    if PDF3 is None:
        PDF3 = TR.pdf_lines([["TESTPART buck converter", "page one"], ["Pin Functions", "page two"],
                             ["Package outline RGT0016C", "page three"]])
    return PDF3


def first(**over):
    """The first read: TD.GOOD on page 1, as a VQFN with a 1.68 mm exposed pad (not a table package)."""
    e = TE.qfn_ext()
    e = TR.on_page(e)
    for k, v in over.items():
        e["package"][k] = v
    return e


def check_answer(pitch=0.5, pins=None, unit="mm", **pkg):
    """An answer in the second read's own (smaller) schema."""
    d = lambda lab, mn, nom, mx: {"label": lab, "min": mn, "nom": nom, "max": mx, "unit": unit, "page": 1, "figure": "outline"}
    p = {"name": QFN, "package_type": "qfn", "pin_count": 16, "pitch": pitch if isinstance(pitch, dict) else d("e", None, pitch, None),
         "body_length": d("D", 2.9, 3.0, 3.1), "body_width": d("E", 2.9, 3.0, 3.1), "lead_span": None,
         "exposed_pad": {"length": d("D2", None, 1.68, None), "width": d("E2", None, 1.68, None)}, "unresolved": []}
    p.update(pkg)
    num = lambda v: "" if v is None else f"{v:g}"
    dims = [{"name": k, "label": r["label"], "unit": r["unit"], **{x: num(r[x]) for x in ("min", "nom", "max")}}
            for k, r in [(k, p.pop(k)) for k in ("pitch", "body_length", "body_width", "lead_span")] if r]
    ep = p.pop("exposed_pad") or {}
    dims += [{"name": f"exposed_pad.{k}", "label": r["label"], "unit": r["unit"], **{x: num(r[x]) for x in ("min", "nom", "max")}}
             for k, r in ep.items() if r]
    p = dict(p, dims=dims, pin_count=p["pin_count"] or 0)
    return {"package": p, "pins": pins if pins is not None else [{"num": x["num"], "name": x["name"]} for x in TD.GOOD["pins"]],
            "embedded_instructions": []}


class API(TB.FakeAPI):
    """answer(kind, model, body) -> extraction / check answer; kind is 'check' for the second read. Records (kind, model)."""

    def __init__(self, by, **kw):
        super().__init__(**kw)
        self.by, self.sent = by, []

    def post(self, url, headers=None, json=None, timeout=None):
        if url.endswith("/v1/messages") and not self.script:
            kind = "check" if json["system"][0]["text"].startswith("You check the package") else "read"
            self.calls.append(("POST", url, headers, json))
            self.sent.append((kind, json["model"]))
            a = self.by(kind, json["model"], json)
            if a == "refusal":
                return TB.R(200, dict(TD.message(TD.GOOD, model=json["model"]), stop_reason="refusal", content=[]))
            return TB.R(200, TD.message(a, model=json["model"]))
        return super().post(url, headers, json, timeout)


def reader(d, rules=None, **kw):
    return D.Reader(Path(d) / "out", Path(d) / "cache", **{"key": TB.KEY, "http": TB.PdfHTTP({URL: pdf3()}), "log": None,
                                                         "rules": rules, "sleep": lambda s: None, "poll": 0, **kw})


def read(d, api, rules=None, package=QFN, part="TESTPART", **kw):
    with TB.patched(api):
        rd = reader(d, rules, **kw)
        return rd.read_now(rd.job(part, [(URL, "BOM")], package))


def pages_order(body):
    src = body["messages"][0]["content"][0]["source"]
    from ecadlib import ds_route
    return [t.split("\n")[-1].strip() for t in ds_route.page_texts(base64.b64decode(src["data"]))]


# ---------------------------------------------------------------- second read
def test_second_read_agrees_is_cached_costed_and_reported():
    api = API(lambda k, m, b: first() if k == "read" else check_answer())
    with tempfile.TemporaryDirectory() as d:
        res = read(d, api)
        assert api.sent == [("read", "claude-opus-5-5"), ("check", "claude-opus-5-5")]
        cc = res["sidecar"]["cross_check"]
        assert cc["mode"] == "auto" and cc["status"] == "agreed" and res["cross_check"] is cc
        (c,) = cc["checks"]
        assert c["source"] == "second read" and {"pin_count", "pitch", "body_length", "ep_length", "pins", "pin_names"} <= set(c["agreed"])
        body = api.calls[-1][3]
        assert D.check_shape(check_answer(), CC.output_schema()) is None          # the fixture is schema-shaped
        assert body["output_config"]["format"]["schema"] == CC.output_schema() and body["max_tokens"] == CC.CHECK_MAX_TOKENS
        assert pages_order(body) == ["page three", "page two", "page one"]          # same pages, reverse order
        assert "Part: TESTPART" in body["messages"][0]["content"][1]["text"]
        led = TR.ledger(d)
        assert [r["verdict"] for r in led] == ["ok", "cross-check"] and led[1]["purpose"] == "cross_check" and led[1]["usd"] > 0
        md = (Path(d) / "out" / "datasheet" / "TESTPART.review.md").read_text()
        assert "## Cross-check: agreed" in md and "Setting: [datasheet] cross_check = auto" in md
        # a re-run costs nothing: both answers come from the result cache, and give the same record
        res2 = read(d, api)
        assert len(api.sent) == 2 and res2["sidecar"]["cross_check"]["status"] == "agreed"
        assert res2["sidecar"]["cross_check"]["checks"][0]["via"] == "cached"


def test_pitch_disagreement_holds_with_both_values():
    api = API(lambda k, m, b: first() if k == "read" else check_answer(pitch=0.65))
    with tempfile.TemporaryDirectory() as d:
        try:
            read(d, api)
            raise AssertionError("a pitch disagreement was accepted")
        except CC.Held as e:
            assert isinstance(e, D.DatasheetError) and e.record["status"] == "disagreed"
            assert "pitch read 0.5 mm, second read 0.65 mm" in str(e)
        md = (Path(d) / "out" / "datasheet" / "TESTPART.review.md").read_text()
        assert "| pitch | 0.5 mm | 0.65 mm |" in md and "Neither value was picked" in md
        side = json.loads((Path(d) / "out" / "datasheet" / "TESTPART.extraction.json").read_text())
        assert side["cross_check"]["status"] == "disagreed"
        # a value printed in mil that converts to the same mm agrees (units handled in code, both sides)
        api2 = API(lambda k, m, b: first() if k == "read" else check_answer(pitch=0.5 / 0.0254, unit="mil",
                                                                             body_length=None, body_width=None, exposed_pad=None))
    with tempfile.TemporaryDirectory() as d:
        try:
            read(d, api2)
            raise AssertionError("an exposed pad the second read did not see was accepted")
        except CC.Held as e:                          # ... but no exposed pad in the second read is a disagreement
            assert "exposed pad read yes, second read none" in str(e) and "pitch" not in [x["field"] for c in
                                                                                           e.record["checks"] for x in c["disagreed"]]


def test_pin_table_disagreement_holds():
    pins = [{"num": x["num"], "name": x["name"]} for x in TD.GOOD["pins"]]
    pins[4]["name"] = "VSENSE"                        # pin 5 is FB in the first read
    api = API(lambda k, m, b: first() if k == "read" else check_answer(pins=pins[:-1] + [{"num": "17", "name": "PGND"}]))
    with tempfile.TemporaryDirectory() as d:
        try:
            read(d, api)
            raise AssertionError("a pin-name disagreement was accepted")
        except CC.Held as e:
            assert "pin names read 5 FB, second read 5 VSENSE" in str(e)
    api = API(lambda k, m, b: first() if k == "read" else check_answer(pins=pins[:10]))
    with tempfile.TemporaryDirectory() as d:
        try:
            read(d, api)
            raise AssertionError("a pin-count disagreement was accepted")
        except CC.Held as e:
            assert "pin numbers read 17 pins, only here: 11, 12" in str(e)
    a = [("1", "~{RESET}"), ("2", "VDD")]
    assert CC.compare_pins(a, [("1", "RESET#"), ("2", "vdd")]) == []          # overbar marks and case are not differences


# ---------------------------------------------------------------- standard table
def test_standard_table_agrees_for_free_and_a_mismatch_holds():
    soic = TR.on_page(TD.INCH)                        # SOIC-8 printed in inches: the table's SOIC-8 3.9x4.9 agrees
    api = API(lambda k, m, b: soic)
    with tempfile.TemporaryDirectory() as d:
        res = read(d, api, package="SOIC-8_3.9x4.9mm_P1.27mm")
        assert api.sent == [("read", "claude-opus-5-5")]                         # no second request
        cc = res["sidecar"]["cross_check"]
        assert cc["status"] == "agreed" and [c["against"] for c in cc["checks"]] == ["standard:SOIC-8_3.9x4.9mm_P1.27mm"]
        assert {"pin_count", "pitch", "body_length", "body_width", "lead_span"} <= set(cc["checks"][0]["agreed"])
    wide = copy.deepcopy(soic)                        # the drawing is a 5.3 mm (208 mil) body: not the 3.9 mm SOIC-8
    wide["package"]["body_width"] = TD.dim("E1", 0.205, 0.209, 0.212, "in", 1, "D0008A")
    wide["package"]["lead_span"] = TD.dim("E", 0.300, 0.307, 0.315, "in", 1, "D0008A")
    api = API(lambda k, m, b: wide)
    with tempfile.TemporaryDirectory() as d:
        try:
            read(d, api, package="SOIC-8_3.9x4.9mm_P1.27mm")
            raise AssertionError("a 5.3 mm body was accepted as the standard 3.9 mm SOIC-8")
        except CC.Held as e:
            assert "standard:SOIC-8_3.9x4.9mm_P1.27mm: body width read 5.3086 (5.207..5.3848) mm, table 3.9 (3.8..4) mm" in str(e)
            assert "lead span read" in str(e)
        assert {k for k, _ in api.sent} == {"read"}            # (a self-check failure got its feedback turn) no check read
    # a BOM package that is not the package read (VQFN-16 answer for a SOT-223 part) holds
    api = API(lambda k, m, b: TR.GOOD)
    with tempfile.TemporaryDirectory() as d:
        try:
            read(d, api, package="SOT-223-3_TabPin2")
            raise AssertionError
        except CC.Held as e:
            assert "standard:SOT-223-3_TabPin2: pin count read 16, table 3" in str(e)


def test_pad_table_read_counts_its_numbered_exposed_pad():
    """A pad-table (custom) read counts every numbered pad, so a WSON-12 with its exposed pad numbered 13 has
    pin_count 13; the standard table says 12 + EP. That used to hold every such part (6 cases of the eval gate)."""
    read = CC.critical({"name": "WSON-12-1EP_4x4mm_P0.5mm_EP2.6x3mm_ThermalVias", "package_type": "custom", "pin_count": 13})
    rec = CC.against_table(read, "WSON-12-1EP_4x4mm_P0.5mm_EP2.6x3mm", "the read's package name")
    assert rec["status"] == "agreed" and rec["agreed"] == ["pin_count"], rec
    assert CC.against_table(dict(read, pin_count=14), "WSON-12-1EP_4x4mm_P0.5mm_EP2.6x3mm", "x")["status"] == "disagreed"
    assert CC.against_table(dict(read, pin_count=12), "WSON-12-1EP_4x4mm_P0.5mm_EP2.6x3mm", "x")["status"] == "agreed"
    noep = CC.critical({"name": "SOIC-8_3.9x4.9mm_P1.27mm", "package_type": "custom", "pin_count": 9})
    assert CC.against_table(noep, "SOIC-8_3.9x4.9mm_P1.27mm", "x")["status"] == "disagreed"    # no EP: one more is wrong


def test_tolerances_and_ranges():
    t = CC.critical({"pin_count": 8, "pitch": 1.27, "body_width": {"min": 3.8, "nom": 3.9, "max": 4.0}})
    r = CC.critical({"pin_count": 8, "pitch": 1.27, "body_width": {"min": 3.81, "nom": 3.91, "max": 3.99}})
    assert CC.compare(r, t, "table")["status"] == "agreed"
    far = CC.critical({"pin_count": 8, "pitch": 1.27, "body_width": {"min": 3.5, "max": 4.2}})   # no nominal printed
    assert CC.compare(far, t, "table")["status"] == "agreed"                   # table nominal inside the printed range
    assert CC.compare(far, CC.critical({"pin_count": 8, "pitch": 1.27, "body_width": 4.5}), "read")["status"] == "disagreed"
    assert CC.compare(r, CC.critical({"pin_count": 8, "pitch": 1.28}), "read")["status"] == "agreed"      # 0.01 mm
    assert CC.compare(r, CC.critical({"pin_count": 8, "pitch": 1.29}), "read")["status"] == "disagreed"
    assert CC.compare(CC.critical({"pin_count": 8}), CC.critical({"pin_count": 8}), "read")["status"] == "unchecked"


# ---------------------------------------------------------------- modes, unavailable checks
def test_modes_off_always_skipped_and_unchecked():
    soic = TR.on_page(TD.INCH)
    with tempfile.TemporaryDirectory() as d:
        api = API(lambda k, m, b: first())
        res = read(d, api, rules={"datasheet": {"cross_check": "off"}})
        assert api.sent == [("read", "claude-opus-5-5")] and "cross_check" not in res["sidecar"]
    with tempfile.TemporaryDirectory() as d:                                       # always: the table AND a second read
        api = API(lambda k, m, b: soic if k == "read" else check_answer(
            name="SOIC-8_3.9x4.9mm_P1.27mm", package_type="gullwing", pin_count=8, pitch={"label": "e", "min": None, "nom": 50, "max": None, "unit": "mil", "page": 1, "figure": "outline"},
            body_length=None, body_width=None, exposed_pad=None, pins=[{"num": str(i), "name": f"P{i}"} for i in range(1, 9)]))
        res = read(d, api, rules={"datasheet": {"cross_check": "always", "cross_check_model": "claude-sonnet-5-5"}},
                   package="SOIC-8_3.9x4.9mm_P1.27mm")
        cc = res["sidecar"]["cross_check"]
        assert api.sent == [("read", "claude-opus-5-5"), ("check", "claude-sonnet-5-5")]   # another model for the check
        assert [c["source"] for c in cc["checks"]] == ["standard table", "second read"] and cc["status"] == "agreed", cc
    with tempfile.TemporaryDirectory() as d:                                       # auto: footprint from another source
        api = API(lambda k, m, b: first())
        with TB.patched(api):
            rd = reader(d)
            res = rd.read_now(rd.job("TESTPART", [(URL, "BOM")], QFN, fp_needed=False))
        assert api.sent == [("read", "claude-opus-5-5")] and res["sidecar"]["cross_check"]["status"] == "skipped"
    with tempfile.TemporaryDirectory() as d:                                       # the check read refused: unchecked
        api = API(lambda k, m, b: first() if k == "read" else "refusal")
        res = read(d, api)
        assert res["sidecar"]["cross_check"]["status"] == "unchecked"
        assert any(r.startswith("cross-check: second read (claude-opus-5-5): not checked (the second read stopped (refusal))")
                   for r in res["needs_review"]), res["needs_review"]


def test_rules_validate_cross_check_and_temperature():
    r = RU.load({"datasheet": {"cross_check": "always", "cross_check_model": "local:qwen2.5:7b"}, "ai": {"temperature": 0.3}})
    assert r["datasheet"]["cross_check"] == "always" and RU.load(None, start=tempfile.gettempdir())["datasheet"]["cross_check"] == "auto"
    for bad, msg in (({"datasheet": {"cross_check": "sometimes"}}, "datasheet.cross_check"),
                     ({"datasheet": {"cross_check_model": "bad model!"}}, "datasheet.cross_check_model"),
                     ({"ai": {"temperature": 3}}, "ai.temperature"), ({"ai": {"temperature": "cold"}}, "ai.temperature")):
        try:
            RU.load(bad)
            raise AssertionError(bad)
        except RU.RulesError as e:
            assert msg in str(e), e


# ---------------------------------------------------------------- librarian, batch, review
def test_librarian_reports_agreed_and_held_parts():
    def answer(kind, model, body):
        part = body["messages"][0]["content"][1]["text"].split("\n")[0].split(": ", 1)[1]
        if kind == "read":
            return first()
        return check_answer(pitch=0.65) if part == "DSPART2" else check_answer()
    api = API(answer)
    with tempfile.TemporaryDirectory() as d:
        items, rd, _ = TB.run2(d, api, reader_kw={"batch": False})
        a, b = items["DSPART1"], items["DSPART2"]
        assert a["cross_check"]["status"] == "agreed" and a["footprint"].get("file")
        assert b["cross_check"]["status"] == "disagreed" and not b["footprint"].get("file") and b["status"] == "held"
        assert any("pitch read 0.5 mm, second read 0.65 mm" in r for r in b["librarian"]["review"])
        md = (Path(d) / "out" / "review.md").read_text()
        assert "## Cross-checks of datasheet reads" in md and "| DSPART2 | disagreed |" in md and "| DSPART1 | agreed |" in md
        assert "The two datasheet reads disagree on pitch (0.5 mm vs 0.65 mm)" in md     # both values, in plain words
        assert "Check the datasheet and pick one, or release anyway." in md
        assert "cross-check" in (Path(d) / "out" / "datasheet" / "cost.jsonl").read_text()


def test_batch_answers_are_cross_checked_too():
    api = API(lambda k, m, b: check_answer(pitch=0.65) if k == "check" else first(),
              answer=lambda cid: TD.message(first()))
    with tempfile.TemporaryDirectory() as d:
        items, rd, _ = TB.run2(d, api)
        assert len(api.of("/v1/messages/batches")) == 1                       # first reads in one batch
        assert [k for k, _ in api.sent] == ["check", "check"]                  # the second reads, synchronously
        assert all(items[p]["cross_check"]["status"] == "disagreed" and items[p]["status"] == "held" for p in ("DSPART1", "DSPART2"))


# ---------------------------------------------------------------- frontier and small models
def test_small_model_second_read_goes_through_its_rails():
    def answer(body):
        sys_txt = body["messages"][0]["content"]
        if "You check the package" in sys_txt:                   # the check read: PDF as page text, schema in the prompt
            seen.append(body)
            ans = check_answer(pitch=pitch[0])
            return TLL.chat("```json\n" + json.dumps(ans) + "\n```")
        st = TLL.step_of(body)
        if st == "identify":
            return TLL.chat(json.dumps(dict(TLL.IDENT, name=QFN)))
        if st == "dims":
            dd = copy.deepcopy(TLL.DIMS)
            dd["exposed_pad"] = {"length": TD.dim("D2", None, 1.68, None, page=2), "width": TD.dim("E2", None, 1.68, None, page=2)}
            return TLL.chat(json.dumps(dd))
        return TLL.chat(json.dumps(TLL.PINS))
    for want in (0.5, 0.65):
        seen, pitch = [], [want]
        with TLL.FakeOpenAI(answer) as srv, tempfile.TemporaryDirectory() as d:
            rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=TB.PdfHTTP({URL: TLL._pdf2()}), log=None,
                          rules=TLL.small_rules(srv.base), sleep=lambda s: None)
            try:
                res = rd.read_now(rd.job("TESTPART", [(URL, "BOM")]))
                held = None
            except CC.Held as e:
                held = e
            chats = srv.chats()
        (chk,) = seen
        assert '"type": "file"' not in json.dumps(chk) and "=== PDF page" in json.dumps(chk)      # page text, not the PDF
        assert "JSON schema" in chk["messages"][0]["content"]                                     # schema in the prompt
        assert all(c.get("temperature") == 0.0 and c.get("seed") == SM.SEED for c in chats)       # repeatable sampling
        if want == 0.5:
            assert held is None and res["sidecar"]["cross_check"]["status"] == "agreed", res["sidecar"]["cross_check"]
            assert res["sidecar"]["ai"]["sampling"] == {"temperature": 0.0, "seed": SM.SEED}
        else:
            assert held is not None and "pitch read 0.5 mm, second read 0.65 mm" in str(held)


def test_frontier_claude_check_request_is_plain_and_has_no_sampling():
    api = API(lambda k, m, b: first() if k == "read" else check_answer())
    with tempfile.TemporaryDirectory() as d:
        read(d, api)
    chk = api.calls[-1][3]
    assert "temperature" not in chk and "seed" not in chk and chk["fallbacks"] == "default"   # Opus 5.5 rejects temperature
    assert chk["messages"][0]["content"][0]["type"] == "document"                             # the PDF itself
    assert all("temperature" not in c[3] for c in api.calls if c[0] == "POST")


# ---------------------------------------------------------------- sampling
def test_sampling_only_where_the_model_takes_it():
    ep = lambda m, p="anthropic": {"model": m, "provider": p}
    assert SM.params(ep("claude-opus-5-5"), PR.profile("claude-opus-5-5")) == {}
    for m in ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-7", "claude-fable-5-1"):
        assert SM.params(ep(m), PR.profile(m)) == {}, m
    assert SM.params(ep("claude-opus-4-6"), PR.profile("claude-opus-4-6")) == {"temperature": 0.0}
    assert SM.params(ep("claude-haiku-4-5"), {}, 0.2) == {"temperature": 0.2}
    oc = lambda m: SM.params(ep(m, "openai-compatible"), PR.profile(m, "openai-compatible"))
    assert oc("gpt-5") == {} and oc("o3") == {} and oc("deepseek-reasoner") == {}
    assert oc("qwen2.5:7b") == oc("gpt-4o") == {"temperature": 0.0, "seed": SM.SEED}
    assert SM.params(ep("gpt-4o", "openai-compatible"), {}, "off") == {}
    assert SM.params(ep("gpt-5", "openai-compatible"), {"sampling": True}) == {"temperature": 0.0, "seed": SM.SEED}   # profile override
    assert LR.info("claude-opus-5-5", None)["sampling"] == {}
    assert PR.check_field("sampling", True) is None and PR.check_field("sampling", "yes")


# ---------------------------------------------------------------- eval
def test_eval_consistency_and_cross_check_columns():
    from ecadlib.eval import cli as EC, runner as R
    with tempfile.TemporaryDirectory() as d:
        rec = TE.record_dir(Path(d) / "rec", {"SOICPART": TD.INCH, "QFNPART": TE.qfn_ext()})
        r = json.loads((rec / "QFNPART.json").read_text())
        r["check"] = {"model": "claude-opus-5-5", "message": TD.message(check_answer())}   # the recorded second read
        (rec / "QFNPART.json").write_text(json.dumps(r))
        with mock.patch.object(D.requests, "post", side_effect=AssertionError("replay must not call the API")):
            rep = TE.run(d, "a", replay=rec, repeat=2)
        cs = {c["mpn"]: c for c in rep["cases"]}
        assert cs["SOICPART"]["cross_check"] == "self-checked" and cs["QFNPART"]["cross_check"] == "agreed"   # DS-13: the
        # table entry was found only by the read's own package name (no BOM package) / an independent second read
        assert cs["QFNPART"]["files"]["kicad_mod"] and cs["QFNPART"]["files"]["pcblib"] and cs["QFNPART"]["files"]["schlib"]
        co = rep["summary"]["consistency"]
        assert co == {"cases": 2, "same": 2, "rate": 1.0, "differ": [], "from": "recorded answers", "runs": 2}, co
        assert rep["summary"]["cross_check"] == {"agreed": 1, "self-checked": 1}
        md = (Path(d) / "a" / "report.md").read_text()
        assert "**Consistency:** 2/2 parts gave the same files when run again (recorded answers, 2 runs)." in md
        # a run whose recording disagrees: different files and cross-check status, and the CLI says so
        r["check"]["message"] = TD.message(check_answer(pitch=0.65))
        (rec / "QFNPART.json").write_text(json.dumps(r))
        b = TE.run(d, "b", replay=rec)
        assert {c["mpn"]: c["cross_check"] for c in b["cases"]}["QFNPART"] == "disagreed"
        c = R.consistency(Path(d) / "a", Path(d) / "b")
        assert c["same"] == 1 and c["differ"][0]["id"] == "qfn-QFNPART" and c["differ"][0]["cross_check"] == ["agreed", "disagreed"]
        assert set(c["differ"][0]["files"]) == {"kicad_mod", "pcblib", "schlib"}
        assert EC.main(["consistency", str(Path(d) / "a"), str(Path(d) / "a" / "repeat-2")]) == 0
        # the write time in a .PcbLib header is not a different answer (the records are compared, not the bytes)
        import re
        pl = next((Path(d) / "a" / "lib").rglob("*.PcbLib"))
        raw = pl.read_bytes()
        other = re.sub(rb"TIME=\d\d:\d\d:\d\d", b"TIME=01:02:03", re.sub(rb"DATE=\d\d/\d\d/\d{4}", b"DATE=01/01/2001", raw))
        assert other != raw and len(other) == len(raw)
        f2 = Path(d) / "copy.PcbLib"
        f2.write_bytes(other)
        assert R.file_fingerprint(f2) == R.file_fingerprint(pl)
        assert EC.main(["consistency", str(Path(d) / "a"), str(Path(d) / "b"), "--json"]) == 2


def test_workspace_and_mcp_list_cross_checks():
    from ecadlib import app as APP, mcp_server as M
    with tempfile.TemporaryDirectory() as d:
        ws = APP.Workspace(Path(d), providers=[])
        (Path(d) / "bom.csv").write_text("Ref,MPN\nU1,X\n")
        out = ws._librarian_dir(ws._bom_path("bom.csv"))
        out.mkdir(parents=True)
        plan = [{"name": "A", "status": "ready", "cross_check": {"status": "agreed", "summary": "standard:QFN agrees (pitch)"}},
                {"name": "B", "status": "held", "cross_check": {"status": "disagreed", "summary": "pitch read 0.5 mm, second read 0.65 mm"}},
                {"name": "C", "status": "ready"}]
        (out / "plan.json").write_text(json.dumps(plan))
        r = ws.cross_checks("bom.csv")
        assert [p["part"] for p in r["parts"]] == ["A", "B"] and r["counts"]["agreed"] == 1 and r["counts"]["disagreed"] == 1
        assert M.call_tool(ws, "cross_checks", {"bom": "bom.csv"}) == r
        assert any(t["name"] == "cross_checks" for t in M.TOOLS)


def test_pin_names_that_differ_only_in_wording_agree():
    # the 2026-10-18 benchmark held 18 parts on wording like these
    assert CC.compare_pins([(1, "K"), (2, "A"), (5, "VO"), (21, "GND"), (7, "V_{SS}")],
                          [(1, "cathode (K)"), (2, "anode"), (5, "VO (output)"), (21, "PowerPAD"), (7, "VSS")]) == []
    d = CC.compare_pins([(1, "VCC"), (2, "D")], [(1, "GND"), (2, "S")])
    assert d and d[0]["field"] == "pin_names" and "1 VCC" in d[0]["first"] and "2 D" in d[0]["first"]


# ---------------------------------------------------------------- 2026-10-19 benchmark holds
R = lambda lo, nom, hi: {"min": lo, "nom": nom, "max": hi}
DFN8 = "DFN-8-1EP_3x2mm_P0.5mm_EP1.3x1.5mm"


def test_a_dfn_read_turned_90_degrees_agrees_with_the_table():
    """AT24CS01-MAHM: D x E = 2 x 3 as printed, KiCad's DFN-8 3 x 2. The same land pattern turned, when the pin rows
    turn with the body (top/bottom instead of left/right)."""
    at = CC.critical({"package_type": "qfn", "pin_count": 8, "pitch": 0.5, "body_length": R(1.9, 2, 2.1),
                      "body_width": R(2.9, 3, 3.1), "exposed_pad": {"length": R(1.4, 1.5, 1.6), "width": R(1.2, 1.3, 1.4)},
                      "pins_left_right": 0, "pins_top_bottom": 4})
    rec = CC.against_table(at, DFN8, "BOM package")
    assert rec["status"] == "agreed" and rec["turned"] == ["body", "exposed pad"], rec
    assert {"body_length", "body_width", "ep_length", "ep_width"} <= set(rec["agreed"])
    assert "body and exposed pad with length and width swapped" in CC.summary_line({"status": "agreed", "checks": [rec]})
    # rows left/right in a 2 mm wide body: the pads would sit 0.5 mm in from KiCad's, not turned with it
    rec = CC.against_table(dict(at, rows=(4, 0)), DFN8, "x")
    assert rec["status"] == "disagreed" and "turned" not in rec
    assert [d["field"] for d in rec["disagreed"]] == ["body_length", "body_width", "ep_length", "ep_width"]
    assert "but the pads would not turn with it" in rec["disagreed"][0]["note"]
    # the body turned but its exposed pad not: the pad runs the other way across the body
    alone = dict(at, ep_length=at["ep_width"], ep_width=at["ep_length"])
    assert CC.against_table(alone, DFN8, "x")["status"] == "disagreed"


def test_an_exposed_pad_turned_on_its_own_holds():
    """AL8860MP: MSOP-8 3 x 3, EP 1.8 x 1.5 read, 1.5 x 1.8 in the table. footprint_gen lays EP length across the rows
    (the table's 1.5), so the read would build the pad turned: still a disagreement, with a note saying why."""
    al = CC.critical({"package_type": "gullwing", "pin_count": 8, "pitch": 0.65, "body_length": R(2.9, 3, 3.1),
                      "body_width": R(2.9, 3, 3.1), "lead_span": R(4.7, 4.9, 5.1),
                      "exposed_pad": {"length": R(1.6, 1.8, 2), "width": R(1.3, 1.5, 1.7)}})
    rec = CC.against_table(al, "MSOP-8-1EP_3x3mm_P0.65mm_EP1.5x1.8mm", "BOM package")
    assert rec["status"] == "disagreed" and [d["field"] for d in rec["disagreed"]] == ["ep_length", "ep_width"], rec
    line = CC.summary_line({"status": "disagreed", "checks": [rec]})
    assert "exposed pad length read 1.8 (1.6..2) mm, table 1.5 (1.3..1.7) mm - the same with length and width swapped" in line
    second = dict(al, ep_length=al["ep_width"], ep_width=al["ep_length"])
    assert CC.compare(al, second, "read")["status"] == "disagreed"            # nor against a second read


def test_a_second_read_giving_d_and_e_the_other_way_agrees_on_the_body():
    """IRFS4115 (D2PAK): body 10.16 x 9.015 read in footprint_gen's axes, 9.015 x 10.16 as printed. A gullwing's pads
    stand on the lead span, not the body. The tab (none vs an exposed pad, 3 pins vs 4) is still a disagreement."""
    first = CC.critical({"package_type": "gullwing", "pin_count": 3, "pitch": 2.54, "body_length": R(9.65, 10.16, 10.67),
                         "body_width": R(8.38, 9.015, 9.65)})
    second = CC.critical({"package_type": "gullwing", "pin_count": 3, "pitch": 2.54, "body_length": R(8.38, 9.015, 9.65),
                          "body_width": R(9.65, 10.16, 10.67), "exposed_pad": {"length": 8, "width": 10}})
    rec = CC.compare(first, second, "read")
    assert rec["turned"] == ["body"] and {"body_length", "body_width"} <= set(rec["agreed"]), rec
    assert [d["field"] for d in rec["disagreed"]] == ["has_ep"]
    pins = CC.compare_pins([("1", "G"), ("2", "D"), ("3", "S")], [("1", "G"), ("2", "D"), ("3", "S"), ("4", "D")])
    assert [d["field"] for d in pins] == ["pins"] and pins[0]["second"] == "4 pins, only here: 4"
    # a qfn's pads stand on the body, and a second read gives no rows: a turned body there holds
    q = lambda c: dict(c, package_type="qfn", has_ep=False)
    assert CC.compare(q(first), q(second), "read")["status"] == "disagreed"


def test_a_real_swap_of_different_sizes_still_disagrees():
    a = CC.critical({"package_type": "gullwing", "pin_count": 8, "pitch": 1.27, "body_length": 2, "body_width": 3})
    for other in ((4, 2), (3, 4), (2, 4)):                    # 2 x 3 vs 4 x 2, 3 x 4, 2 x 4
        b = CC.critical({"package_type": "gullwing", "pin_count": 8, "pitch": 1.27, "body_length": other[0], "body_width": other[1]})
        rec = CC.compare(a, b, "read")
        assert rec["status"] == "disagreed" and "turned" not in rec, (other, rec)
    sq = CC.critical({"package_type": "gullwing", "pin_count": 8, "pitch": 1.27, "body_length": 3, "body_width": 3})
    assert CC.compare(sq, sq, "read")["status"] == "agreed" and "turned" not in CC.compare(sq, sq, "read")


def test_active_low_marks_in_any_notation_agree():
    # AM26LS31CN: ~{G} in one read, G with a combining overline (U+0305) in the other
    assert CC.compare_pins([("12", "~{G}"), ("4", "~{RESET}")], [("12", "G̅"), ("4", "R̄ĒS̄ĒT̄")]) == []
    for name in ("RESET#", "/RESET", "nRESET", "RESET_N", "R̅E̅S̅E̅T̅", "R\\E\\S\\E\\T\\"):
        assert CC.compare_pins([("1", "~{RESET}")], [("1", name)]) == [], name
    assert CC.compare_pins([("1", "RESET_N")], [("1", "nRESET")]) == []
    # _N is only an active-low mark against another one: RESET_N vs plain RESET, or a differential leg, still differ
    for a, b in (("RESET_N", "RESET"), ("CLK_N", "CLK_P"), ("~{G}", "S"), ("G̅", "~{S}")):
        assert CC.compare_pins([("1", a)], [("1", b)]), (a, b)


def test_a_package_type_alone_still_holds_and_says_so():
    """TC2030 connector vs custom, BD33FC0FP gullwing vs custom: the type picks the builder, so the part is held, but
    the message says every number agrees. IR4301 (custom vs qfn, 14 vs 15 pins, 0.65 vs 0.775 pitch) and
    BSC026N08NS5 (9 pins vs 8) are real."""
    for a, b in (("connector", "custom"), ("gullwing", "custom")):
        first = CC.critical({"package_type": a, "pin_count": 6, "pitch": 1.27, "body_length": 5, "body_width": 4})
        rec = CC.compare(first, dict(first, package_type=b), "read")
        assert rec["status"] == "disagreed" and [d["field"] for d in rec["disagreed"]] == ["package_type"]
        assert rec["disagreed"][0]["note"] == "only the type label differs: pin count, pitch, body length, body width agree"
        line = CC.summary_line({"status": "disagreed", "checks": [dict(rec, against="second read (m)", source="second read")]})
        assert f"package type read {a}, second read {b} - only the type label differs" in line
        assert "| package type | " + a + " | " + b + " - only the type label differs" in "\n".join(
            CC.report_md({"status": "disagreed", "mode": "auto", "checks": [dict(rec, against="x", source="second read")]}))
    ir = CC.compare(CC.critical({"package_type": "custom", "pin_count": 14, "pitch": 0.65}),
                    CC.critical({"package_type": "qfn", "pin_count": 15, "pitch": 0.775}), "read")
    assert [d["field"] for d in ir["disagreed"]] == ["package_type", "pin_count", "pitch"] and "note" not in ir["disagreed"][0]
    bsc = CC.compare_pins([(str(i), "S") for i in range(1, 10)], [(str(i), "S") for i in range(1, 9)])
    assert [d["field"] for d in bsc] == ["pins"] and bsc[0]["first"] == "9 pins, only here: 9"


def test_a_type_only_note_goes_when_the_pins_disagree_too():
    pins = [{"num": x["num"], "name": x["name"]} for x in TD.GOOD["pins"]]
    pins[4]["name"] = "VSENSE"
    api = API(lambda k, m, b: first() if k == "read" else check_answer(package_type="gullwing", pins=pins))
    with tempfile.TemporaryDirectory() as d:
        try:
            read(d, api)
            raise AssertionError("held expected")
        except CC.Held as e:
            (c,) = e.record["checks"]
            assert [x["field"] for x in c["disagreed"]] == ["package_type", "pin_names"] and "note" not in c["disagreed"][0]
    api = API(lambda k, m, b: first() if k == "read" else check_answer(package_type="gullwing"))
    with tempfile.TemporaryDirectory() as d:
        try:
            read(d, api)
            raise AssertionError("held expected")
        except CC.Held as e:
            assert "package type read qfn, second read gullwing - only the type label differs: " in str(e)
            md = (Path(d) / "out" / "datasheet" / "TESTPART.review.md").read_text()
            assert "only the type label differs" in md


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
