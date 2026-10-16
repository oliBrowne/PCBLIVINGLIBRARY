"""Offline tests for ecadlib.ds_route (pages and model per part, escalation, cost) through datasheet.Reader.

The API is a fake that answers by the model and the number of PDF pages in each request (Messages and Message
Batches shapes, as in test_datasheet_batch). Extractions are test_datasheet's SYNTHETIC fixtures; the fixture PDF is
SYNTHETIC text laid out like a regulator datasheet with two packages (ordering table, one drawing and one pad layout
per package), not a copy of any real document.
    python3 -m tests.test_ds_route"""
import base64, copy, io, json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import datasheet as D, ds_route as R, librarian as L, rules as RU, ws_status as WS
from tests import test_datasheet as TD, test_datasheet_batch as TB

KEY = TB.KEY
HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
LADDER = {"datasheet": {"route": "ladder"}}


def pdf_lines(pages):
    """A real PDF, one page per list of lines (so pypdf reads each line as a line)."""
    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = pypdf.PdfWriter()
    font = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                           NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for lines in pages:
        page = w.add_blank_page(612, 792)
        esc = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        body = " T* ".join(f"({esc(t)}) Tj" for t in lines) if lines else ""
        cs = DecodedStreamObject()
        cs.set_data(f"BT /F1 10 Tf 14 TL 72 740 Td {body} ET".encode())
        page[NameObject("/Contents")] = w._add_object(cs)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def ldo_pages(n=30):
    """SYNTHETIC two-package regulator datasheet: pin assignment p2, ordering table p11, SOT223 / SOT89 drawings p13 /
    p14, pad layouts p19 / p20, filler elsewhere."""
    pages = [[f"TESTLDO 1A low dropout regulator", f"Typical performance characteristics, page {i + 1}"] for i in range(n)]
    pages[0] = ["TESTLDO 1A low dropout regulator", "Features", "Available in SOT223 and SOT89"]
    pages[1] = ["Pin Assignment", "(Top View)", "SOT223: 1 ADJ/GND 2 VOUT 3 VIN", "SOT89: 1 ADJ/GND 2 VOUT 3 VIN"]
    for i in range(4, 10):
        pages[i] = ["Electrical Characteristics", "Parameter Symbol Conditions MIN TYP MAX Unit"]
    pages[10] = ["Ordering Information", "Package Part Number Marking Packing", "SOT223", "TESTLDO-H33TRG1 GH16D 4000/Tape & Reel",
                 "TESTLDO-HADJTRG1 GH15B 4000/Tape & Reel", "SOT89", "TESTLDO-R33TRG1 G28N 1000/Tape & Reel"]
    pages[12] = ["Package Outline Dimensions (All dimensions in mm)", "(1) Package Type: SOT223",
                 "Dim Min Max Typ", "A1 0.02 0.10", "E1 3.30 3.70", "D 6.30 6.70"]
    pages[13] = ["Package Outline Dimensions (All dimensions in mm)", "(2) Package Type: SOT89",
                 "Dim Min Max Typ", "A1 0.00 0.10", "E1 2.30 2.60", "D 4.40 4.60"]
    pages[18] = ["Suggested Pad Layout", "(1) Package Type: SOT223", "Dimensions Value (in mm)", "Z 8.400 G 4.000 X1 1.200"]
    pages[19] = ["Suggested Pad Layout", "(2) Package Type: SOT89", "Dimensions Value (in mm)", "Z 4.600 X 0.550"]
    pages[22] = ["Tape and reel information", "Reel dimensions"]
    return pages


def on_page(ext, page=1):
    """The fixture with every cited page set to `page` (a page of the PDF that was sent)."""
    e = copy.deepcopy(ext)

    def walk(v):
        if isinstance(v, dict):
            if "page" in v and isinstance(v["page"], int):
                v["page"] = page
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(e)
    return e


GOOD = on_page(TD.GOOD)
BAD = copy.deepcopy(GOOD)                        # name says 0.5 mm pitch, the drawing says 0.65: self-check fails
BAD["package"]["pitch"] = TD.dim("e", None, 0.65, None, page=1)
INCOMPLETE = copy.deepcopy(GOOD)                 # the body length could not be read on the pages sent
INCOMPLETE["package"]["body_length"] = None
INCOMPLETE["package"]["unresolved"] = ["body_length"]


def n_pages(body):
    src = body["messages"][0]["content"][0]["source"]
    if src.get("type") != "base64":
        return None
    return D.pdf_pages(base64.b64decode(src["data"]))


class ModelAPI(TB.FakeAPI):
    """answer(model, pages sent) -> extraction dict, 'refusal', or a full message dict. Logs (model, pages) per answer."""

    def __init__(self, answer, **kw):
        super().__init__(**kw)
        self.by_model, self.sent = answer, []

    def _msg(self, body):
        m, n = body["model"], n_pages(body)
        self.sent.append((m, n))
        a = self.by_model(m, n)
        if a == "refusal":
            return dict(TD.message(GOOD, model=m), stop_reason="refusal", content=[],
                        stop_details={"type": "refusal", "category": "cyber", "explanation": "test"})
        return TD.message(a, model=m)

    def post(self, url, headers=None, json=None, timeout=None):
        if url.endswith("/v1/messages") and not self.script:
            self.calls.append(("POST", url, headers, json))
            return TB.R(200, self._msg(json))
        return super().post(url, headers, json, timeout)

    def _results(self, bid):
        return [{"custom_id": rq["custom_id"], "result": {"type": "succeeded", "message": self._msg(rq["params"])}}
                for rq in self.batches[bid]["requests"]]

    def get(self, url, headers=None, timeout=None):
        if "/results" in url:                       # answer once per request (the status poll does not count)
            self.calls.append(("GET", url, headers, None))
            bid = url.split("/batches/")[1].split("/")[0]
            return TB.R(200, None, text="\n".join(json.dumps(x) for x in self._results(bid)) + "\n")
        bid = url.split("/batches/")[1].split("/")[0]
        self.calls.append(("GET", url, headers, None))
        b = self.batches[bid]
        b["polls"] += 1
        n = len(b["requests"])
        return TB.R(200, TB.batch_obj(bid, "ended" if b["polls"] > self.polls else "in_progress", n, n if b["polls"] > self.polls else 0))


def reader(d, pdfs, rules=None, **kw):
    return D.Reader(Path(d) / "out", Path(d) / "cache", **{"key": KEY, "http": TB.PdfHTTP(pdfs), "log": None,
                                                         "rules": rules, "sleep": lambda s: None, "poll": 0, **kw})


def ledger(d):
    return R.read_ledger(Path(d) / "out" / "datasheet" / "cost.jsonl")


# ---------------------------------------------------------------- pages
def test_package_tokens_and_ordering_codes():
    assert R.package_tokens("SOIC-8_3.9x4.9mm_P1.27mm") == {"SO8"} and R.package_tokens("SOT-223-3_TabPin2") == {"SOT223"}
    assert R.package_tokens("LQFP-32_7x7mm_P0.8mm") == {"QFP32"} and R.package_tokens("8-Pin (WSON)") == {"SON"}
    assert R.package_tokens("Operating range -40 to 85 C, SO 2 and TO 5") == set()          # prose is not a package
    assert R.package_tokens("SOIC−16 WB") == {"SO16"}                                  # unicode minus
    texts = [" \n".join(p) for p in ldo_pages()]
    toks, codes = R.ordering_codes(texts, "TESTLDO-R33TRG1")
    assert toks == {"SOT89"}, toks                                                           # from the heading above it
    ti = ["PACKAGE OPTION ADDENDUM\nOrderable part number Status Package Pins\nBQ771800DPJR ACTIVE WSON DPJ 8 3000"]
    toks, codes = R.ordering_codes(ti, "BQ771800DPJR")
    assert "SON" in toks and "DPJ" in codes and "ACTIVE" not in codes, (toks, codes)


def test_pages_picked_for_this_package_and_widened():
    texts = [" \n".join(p) for p in ldo_pages()]
    got, why = R.pick_pages(texts, 6, "TESTLDO-H33TRG1", "SOT-223-3_TabPin2")
    got6 = got
    assert {2, 11, 13, 19} <= set(got) and not {14, 20} & set(got) and len(got) <= 6, (got, why)
    assert "drawing" in why[13] and "land" in why[19] and "ordering" in why[11] and why["_package"] == ["SOT223"]
    got, _ = R.pick_pages(texts, 6, "TESTLDO-R33TRG1", "SOT-89-3")
    assert {2, 11, 14, 20} <= set(got) and not {13, 19} & set(got), got
    got, _ = R.pick_pages(texts, 6, "TESTLDO-R33TRG1", None)          # no hint: the ordering table names the package
    assert {14, 20} <= set(got) and 13 not in got, got
    lv, _ = R.levels(texts, 6, "TESTLDO-H33TRG1", "SOT-223-3_TabPin2")
    assert lv == [got6, None], lv                     # 18 pages would pick the same pages: one step, then the whole PDF
    lv, _ = R.levels(texts, 3, "TESTLDO-H33TRG1", "SOT-223-3_TabPin2")
    assert len(lv) == 3 and len(lv[0]) == 3 and set(lv[0]) < set(lv[1]) and lv[2] is None, lv
    assert R.levels(texts[:5], 6)[0] == [None] and R.levels(texts, 0)[0] == [None]      # small PDF / budget 0: whole
    assert R.pick_pages(["", "  "], 6) == (None, {})                                        # no text layer
    # through the Reader: the request carries only the picked pages, mapped back to the original page numbers
    data = pdf_lines(ldo_pages())
    with tempfile.TemporaryDirectory() as d:
        rd = reader(d, {"https://a/ldo.pdf": data})
        j = rd.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2")
        assert isinstance(j["offset"], list) and 13 in j["offset"] and n_pages(j["body"]) == len(j["offset"]) <= 6
        assert "picked for this part" in j["prov"]["note"] and j["model"] == OPUS and j["route"]["route"] == "single"
        rd0 = reader(d, {"https://a/ldo.pdf": data}, rules={"datasheet": {"page_budget": 0}})   # today's behaviour
        j0 = rd0.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2")
        assert j0["offset"] == 0 and n_pages(j0["body"]) == 30


def test_incomplete_answer_widens_pages_before_changing_model():
    data = pdf_lines(ldo_pages())
    api = ModelAPI(lambda m, n: GOOD if n == 30 else INCOMPLETE)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        res = reader(d, {"https://a/ldo.pdf": data}).read_now(
            reader(d, {"https://a/ldo.pdf": data}).job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2"))
        assert [m for m, _ in api.sent] == [OPUS] * 2 and api.sent[0][1] <= 6 and api.sent[1][1] == 30, api.sent
        assert res["needs_review"] == [] and [a["verdict"] for a in res["sidecar"]["route"]["attempts"]] == ["incomplete", "ok"]
        assert [r["verdict"] for r in ledger(d)] == ["incomplete", "ok"] and ledger(d)[0]["next"] == "more pages"
    # ladder + always incomplete: haiku on the picked pages -> all pages, then the next models, stopped by max_attempts
    api = ModelAPI(lambda m, n: INCOMPLETE)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ldo.pdf": data}, rules=LADDER)
        res = rd.read_now(rd.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2"))
        assert [m for m, _ in api.sent] == [HAIKU, HAIKU, SONNET, OPUS] and api.sent[1][1] == api.sent[3][1] == 30, api.sent
        assert "body_length" in res["spec"]["unresolved"]                          # the last answer is kept, flagged
        assert ledger(d)[-1].get("next") is None


def test_answers_cached_before_page_picking_are_kept():
    data = pdf_lines(ldo_pages())
    api = ModelAPI(lambda m, n: GOOD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ldo.pdf": data}, rules={"datasheet": {"page_budget": 0}})        # the old behaviour
        rd.read_now(rd.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2"))
        assert api.sent == [(OPUS, 30)]
        rd = reader(d, {"https://a/ldo.pdf": data})                                               # page_budget = 6
        j = rd.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2")
        assert j["final"] and j["offset"] == 0 and rd.cached(j) is not None and len(api.sent) == 1


# ---------------------------------------------------------------- routing and escalation
def test_router_picks_model_by_package_pins_and_pages():
    r = R.Router(RU.load(LADDER)["datasheet"])
    pick = lambda pkg, n=10: r.ladder[r.start(pkg, n)[0]]
    assert r.ladder == [HAIKU, SONNET, OPUS]
    assert pick("R_0603_1608Metric") == pick("D_SOD-123") == pick("SOT-23") == pick("C_0805_2012Metric") == HAIKU
    assert pick("SOIC-8_3.9x4.9mm_P1.27mm") == pick("QFN-32-1EP_5x5mm_P0.5mm") == pick("SOT-23-6") == SONNET
    assert pick("LQFP-64_10x10mm_P0.5mm") == pick("BGA-100_9x9mm") == pick("USB_C_Receptacle") == pick("SOMEPART") == OPUS
    assert pick("SOIC-8_3.9x4.9mm_P1.27mm", 120) == OPUS and pick("R_0603_1608Metric", 120) == SONNET   # long datasheets
    two = R.Router({"route": "ladder", "ladder": [SONNET, OPUS], "model": OPUS})
    assert two.ladder[two.start("R_0603_1608Metric")[0]] == SONNET and two.ladder[two.start("LQFP-64")[0]] == OPUS
    # default rules: one model for everything (today's behaviour); an explicit model is always one model
    dflt = R.Router(RU.load(None, start=tempfile.gettempdir())["datasheet"])
    assert dflt.ladder == [OPUS] and dflt.start("R_0603_1608Metric")[0] == 0
    assert R.Router(RU.load(LADDER)["datasheet"], model=SONNET).ladder == [SONNET]
    assert r.next_step(0, 0, 1, "failed", 1) == (1, 0) and r.next_step(2, 0, 1, "failed", 1) is None      # top: stop
    assert r.next_step(0, 0, 3, "incomplete", 1) == (0, 1) and r.next_step(0, 2, 3, "incomplete", 1) == (1, 2)
    assert r.next_step(0, 0, 3, "ok", 1) is None and r.next_step(0, 0, 3, "failed", 4) is None


def test_failed_check_escalates_and_stops_at_the_top():
    api = ModelAPI(lambda m, n: GOOD if m == OPUS else BAD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER)
        j = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        assert j["model"] == HAIKU and rd.cached(j) is None
        res = rd.read_now(j)
        assert [m for m, _ in api.sent] == [HAIKU, SONNET, OPUS] and res["needs_review"] == []
        assert res["sidecar"]["route"]["model"] == OPUS and res["sidecar"]["model"] == OPUS
        assert [(r["model"], r["verdict"], r.get("next")) for r in ledger(d)] == [
            (HAIKU, "failed", SONNET), (SONNET, "failed", OPUS), (OPUS, "ok", None)]
        assert "How it was read" in (Path(d) / "out" / "datasheet" / "TESTPART.review.md").read_text()
        # a re-run starts at the answer that passed: the cheap failures are cached as failures, never as the answer
        n = len(api.calls)
        rd2 = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER)
        j2 = rd2.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        assert j2["model"] == OPUS and j2["final"] and rd2.cached(j2)["needs_review"] == [] and len(api.calls) == n
    api = ModelAPI(lambda m, n: BAD)                                                     # nothing passes
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER)
        res = rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric"))
        assert [m for m, _ in api.sent] == [HAIKU, SONNET, OPUS]                         # stops at the top
        assert any(r.startswith("self-check:") for r in res["needs_review"]) and res["sidecar"]["model"] == OPUS
        rd2 = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER)
        assert rd2.cached(rd2.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")) is not None
        assert len(api.sent) == 3                                                        # the top answer is cached
    api = ModelAPI(lambda m, n: BAD)                                                     # escalate = false
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules={"datasheet": {"route": "ladder", "escalate": False}})
        rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric"))
        assert api.sent == [(HAIKU, None)]


def test_refusal_on_a_cheap_model_escalates_and_is_not_paid_twice():
    api = ModelAPI(lambda m, n: "refusal" if m == HAIKU else GOOD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER)
        res = rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "D_SOD-123"))
        assert [m for m, _ in api.sent] == [HAIKU, SONNET] and res["needs_review"] == []
        assert ledger(d)[0]["verdict"] == "error" and "refusal" in ledger(d)[0]["error"]
        rd2 = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER)
        j = rd2.job("TESTPART", [("https://a/ds.pdf", "BOM")], "D_SOD-123")
        assert j["model"] == SONNET and rd2.cached(j) is not None and len(api.sent) == 2


def test_cache_key_has_model_and_pages():
    data = pdf_lines(ldo_pages())
    with tempfile.TemporaryDirectory() as d:
        rd = reader(d, {"https://a/ldo.pdf": data}, rules=LADDER)
        j = rd.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2")
        same = rd.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2")
        keys = {rd._at(j, r, lv)["key"] for r in range(3) for lv in range(len(j["levels"]))}
        assert same["key"] == j["key"] and len(j["levels"]) >= 2 and len(keys) == 3 * len(j["levels"]), keys
        assert j["model"].replace("-", "") in j["key"].replace("-", "").replace("_", "")
        assert D.ResultCache.key("ab" * 8, OPUS, "P", None, [1, 2], "high") != D.ResultCache.key("ab" * 8, OPUS, "P", None, [1, 3], "high")


def test_batch_rounds_escalate_and_resume_without_paying_twice():
    api = ModelAPI(lambda m, n: GOOD if m == SONNET else BAD, polls=0)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER, batch=True)
        j = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        rd.submit(j)
        out = rd.flush()
        assert list(out) == [j["key"]] and isinstance(out[j["key"]], dict), out
        assert out[j["key"]]["sidecar"]["model"] == SONNET and len(api.of("/v1/messages/batches")) == 2
        st = json.loads((Path(d) / "out" / "datasheet" / "batch_state.json").read_text())
        assert [list(b["models"].values()) for b in st["batches"]] == [[HAIKU], [SONNET]]
        reqs = [c[3]["requests"][0]["params"] for c in api.of("/v1/messages/batches")]
        assert "fallbacks" not in reqs[0] and reqs[0]["model"] == HAIKU and reqs[1]["model"] == SONNET
        assert all(r["via"].startswith("batch msgbatch_01Test") for r in ledger(d))
        assert abs(ledger(d)[0]["usd"] - R.usage_usd(HAIKU, TD.message(GOOD)["usage"], batch=True)) < 1e-9
    # --no-wait: the escalation waits for the next run, which goes straight to the next model
    api = ModelAPI(lambda m, n: GOOD if m == SONNET else BAD, polls=0)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER, batch=True, wait=False)
        j = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        rd.submit(j)
        assert str(rd.flush()[j["key"]]).startswith("pending: next step claude-sonnet-5-5")
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER, batch=True)
        j = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        assert j["model"] == SONNET
        rd.submit(j)
        assert rd.flush()[j["key"]]["sidecar"]["model"] == SONNET and [m for m, _ in api.sent] == [HAIKU, SONNET]


def test_librarian_through_the_ladder_reports_cost():
    api = ModelAPI(lambda m, n: GOOD if m == OPUS else BAD)
    with tempfile.TemporaryDirectory() as d:
        bom = TB.BOM2.replace("U3,1,DSPART1,,ACME", "U3,1,DSPART1,SOIC-8_3.9x4.9mm_P1.27mm,ACME")   # starts on Sonnet
        http = TB.PdfHTTP({"https://example.com/ds.pdf": TB.PDF, "https://www.lcsc.com/datasheet/C9999.pdf": TB.PDF})
        rd = reader(d, {}, rules=LADDER, http=http)
        with TB.patched(api):
            items, _ = TB.TL.run(d, http=http, bom=bom, reader=rd)
        a = items["DSPART1"]
        assert a["source"]["symbol"].startswith("datasheet:") and [m for m, _ in api.sent] == [SONNET, OPUS, OPUS]   # DSPART2: no hint
        assert any("read by claude-opus-5-5 after" in t for t in a["librarian"]["tried"]), a["librarian"]["tried"]
        md = (Path(d) / "out" / "review.md").read_text()
        assert "## Datasheet reads: tokens and cost" in md and "| DSPART1 | claude-sonnet-5-5 |" in md and "-> claude-" in md
        s = WS.status(d)                                                  # the workspace summary sums every ledger
        assert s["datasheet"]["answers"] == len(api.sent) and s["datasheet"]["usd"] > 0 and OPUS in s["datasheet"]["by_model"]
        assert "datasheet  " in WS.render(s) and "Claude answers for 2 parts" in WS.render(s)


# ---------------------------------------------------------------- cost
def test_usage_usd_and_estimate_per_model():
    u = {"input_tokens": 912, "cache_creation_input_tokens": 48210, "cache_read_input_tokens": 1000, "output_tokens": 2304}
    want = (912 * 4 + 48210 * 4 * 1.25 + 1000 * 0.20 + 2304 * 20) / 1e6                  # Opus 5.5: cache read $0.20
    assert abs(R.usage_usd(OPUS, u) - want) < 1e-6 and abs(R.usage_usd(OPUS, u, batch=True) - want / 2) < 1e-6
    assert abs(R.usage_usd(SONNET, {"input_tokens": 1000, "output_tokens": 1000}) - (2 + 10) / 1000) < 1e-9
    assert abs(R.usage_usd(HAIKU, {"input_tokens": 1000, "output_tokens": 1000}) - (0.10 + 0.50) / 1000) < 1e-9
    assert abs(R.usage_usd(HAIKU, {"input_tokens": 200_000, "output_tokens": 0}) - 0.50 * 0.2) < 1e-9   # >100K prompt tier
    assert R.usage_usd("claude-unknown-9", u) is None
    s = R.summarize([{"name": "A", "model": HAIKU, "usd": 0.01, "how": "usage", "input_tokens": 10, "output_tokens": 5},
                     {"name": "A", "model": OPUS, "usd": 0.1, "how": "usage", "next": None},
                     {"name": "B", "model": OPUS, "usd": 0.2, "how": "estimate", "next": "x"}])
    assert s["parts"] == 2 and s["usd"] == 0.11 and s["usd_estimated"] == 0.2 and s["by_part"]["A"]["models"] == [HAIKU, OPUS]
    data = pdf_lines(ldo_pages())
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, env, clear=True):
        rd = reader(d, {"https://a/ldo.pdf": data, "https://a/ds.pdf": TD.PDF}, rules=LADDER, key=None, batch=True)
        jobs = [rd.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2"),
                rd.job("BIGPART", [("https://a/ds.pdf", "BOM")], "LQFP-64_10x10mm_P0.5mm")]
        est = rd.estimate(jobs)
        assert est["models"] == {HAIKU: 1, OPUS: 1} and [r["model"] for r in est["rows"]] == [HAIKU, OPUS]
        r0 = est["rows"][0]
        assert r0["pages_sent"] <= 6 and r0["pages_total"] == 30 and r0["how"].startswith(f"rough: {r0['pages_sent']} pages")
        assert abs(r0["usd"] - (r0["input_tokens"] * 0.10 + D.OUT_TOKENS * 0.50) / 1e6 * 0.5) < 1e-6
        assert est["usd_max_route"] > est["usd_max"] and "routed" in D.format_estimate(est) and "pages sent" in D.format_estimate(est)


def test_pages_cli_shows_pages_and_model():
    import contextlib
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "ldo.pdf"
        f.write_bytes(pdf_lines(ldo_pages()))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert D.main(["pages", str(f), "--part", "TESTLDO-H33TRG1", "--package", "SOT-223-3_TabPin2", "--json"]) == 0
        out = json.loads(buf.getvalue())
        assert out["pages_total"] == 30 and out["steps"][0]["count"] <= 6 and 13 in out["steps"][0]["pages"]
        assert out["steps"][-1]["pages"] == "all" and out["model"] == OPUS and out["why"]["_package"] == ["SOT223"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            D.main(["pages", str(f), "--budget", "0"])
        assert "first try" in buf.getvalue() and "30 pages  all" in buf.getvalue()


def test_rules_validate_route_settings():
    for bad, word in (({"route": "cheap"}, "datasheet.route"), ({"ladder": []}, "datasheet.ladder"),
                      ({"ladder": ["Claude Haiku"]}, "datasheet.ladder"), ({"page_budget": 1000}, "datasheet.page_budget"),
                      ({"max_attempts": 0}, "datasheet.max_attempts"), ({"escalate": "yes"}, "datasheet.escalate")):
        try:
            RU.validate({"datasheet": bad}, "t.toml")
            raise AssertionError(bad)
        except RU.RulesError as e:
            assert word in str(e), e
    RU.validate({"datasheet": {"route": "ladder", "ladder": [SONNET, OPUS], "page_budget": 0, "max_attempts": 3}}, "t.toml")
    assert D.settings(RU.load(None, start=tempfile.gettempdir()))["route"] == "single"


# ---------------------------------------------------------------- review fixes (rvK)
def test_max_attempts_never_ends_on_a_cheaper_model():
    """3 page levels x 3 models with max_attempts = 4 used to end on Sonnet's incomplete answer (Opus never tried)."""
    data = pdf_lines(ldo_pages())
    api = ModelAPI(lambda m, n: INCOMPLETE)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ldo.pdf": data}, rules={"datasheet": {"route": "ladder", "page_budget": 3}})
        j = rd.job("TESTLDO-H33TRG1", [("https://a/ldo.pdf", "BOM")], "SOT-223-3_TabPin2")
        assert len(j["levels"]) == 3
        res = rd.read_now(j)
        assert len(api.sent) == 4 and api.sent[-1] == (OPUS, 30), api.sent
        assert res["sidecar"]["route"]["model"] == OPUS
    r = R.Router({"route": "ladder", "max_attempts": 2})
    assert r.next_step(0, 0, 3, "failed", 1) == (2, 0) and r.next_step(0, 0, 3, "incomplete", 1) == (2, 2)
    assert r.next_step(2, 0, 3, "incomplete", 1) == (2, 1) and r.next_step(0, 0, 3, "failed", 2) is None


def test_sync_refusal_is_costed_from_its_usage():
    api = ModelAPI(lambda m, n: "refusal" if m == HAIKU else GOOD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER)
        res = rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "D_SOD-123"))
        led = ledger(d)
        assert len(led) == 2 and led[0]["how"] == "usage" and led[0]["output_tokens"] == 2304, led
        assert abs(led[0]["usd"] - R.usage_usd(HAIKU, TD.message(GOOD)["usage"])) < 1e-9
        assert res["sidecar"]["route"]["attempts"][0]["usage"]["output_tokens"] == 2304      # the eval sums these


def test_no_key_is_not_a_failed_step():
    """Without a key nothing is sent: no cost is logged and no step of the route is cached as failed."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    api = ModelAPI(lambda m, n: GOOD)
    with tempfile.TemporaryDirectory() as d, TB.patched(api), mock.patch.dict(os.environ, env, clear=True):
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER, key=None)
        try:
            rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric"))
            raise AssertionError("no key must raise")
        except D.DatasheetError as e:
            assert "not set" in str(e)
        assert ledger(d) == [] and api.sent == []
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules=LADDER)
        assert rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")["model"] == HAIKU


def test_stale_failed_step_at_the_end_of_the_route_is_not_paid_in_a_loop():
    """A refusal cached as a failed step under a longer ladder, then the ladder shortened: the step is the last one
    now; read_now must raise after one call, not call again and again."""
    def answer(m, n):
        assert len(api.sent) < 10, "paid again and again"
        return "refusal"
    api = ModelAPI(answer)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules={"datasheet": {"route": "ladder", "ladder": [HAIKU, OPUS]}})
        j = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        try:
            rd.read_now(j)
        except D.DatasheetError:
            pass
        n = len(api.sent)
        rd = reader(d, {"https://a/ds.pdf": TD.PDF}, rules={"datasheet": {"route": "ladder", "ladder": [HAIKU]}})
        j = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "R_0603_1608Metric")
        try:
            rd.read_now(j)
            raise AssertionError("must raise")
        except D.DatasheetError as e:
            assert "refusal" in str(e)
        assert len(api.sent) == n + 1, api.sent


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
