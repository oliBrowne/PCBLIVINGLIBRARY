"""Offline tests for the live datasheet path: API client (retries, errors, logging), datasheet acquisition, page
selection, result cache, self-check, cost estimate and the Message Batches flow through the librarian.

The HTTP layer is a fake that answers in the documented shapes of the Messages, count_tokens and Message Batches
APIs (create -> message_batch object, retrieve -> processing_status / request_counts / results_url, results ->
JSONL of {custom_id, result: succeeded|errored|expired}). Extractions are the SYNTHETIC fixtures of test_datasheet.
    python3 -m tests.test_datasheet_batch"""
import copy, io, json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import datasheet as D, librarian as L, mcp_server as M
from ecadlib.app import Workspace
from tests import test_datasheet as TD, test_librarian as TL

KEY = "sk-ant-test-SECRET-123"


class R:
    def __init__(self, status, payload=None, text=None, headers=None):
        self.status_code, self.payload = status, payload
        self.text = text if text is not None else json.dumps(payload)
        self.headers = {"request-id": f"req_{status}", **(headers or {})}

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


def batch_obj(bid, status, n, ok=0, err=0, exp=0):
    return {"id": bid, "type": "message_batch", "processing_status": status,
            "request_counts": {"processing": n - ok - err - exp if status != "ended" else 0, "succeeded": ok, "errored": err,
                               "canceled": 0, "expired": exp},
            "ended_at": "2026-10-14T10:00:00Z" if status == "ended" else None, "created_at": "2026-10-14T09:00:00Z",
            "expires_at": "2026-10-15T09:00:00Z", "archived_at": None, "cancel_initiated_at": None,
            "results_url": f"https://api.anthropic.com/v1/messages/batches/{bid}/results" if status == "ended" else None}


class FakeAPI:
    """Messages + count_tokens + Batches. answer(custom_id or part) -> message dict, or ('errored', type, msg) / 'expired'.
    polls: how many retrieves say in_progress before ended. script: queued (status, payload) for /v1/messages."""

    def __init__(self, answer=None, polls=1, script=None, tokens=12345):
        self.answer = answer or (lambda cid: TD.message(TD.GOOD))
        self.polls, self.script, self.tokens = polls, list(script or []), tokens
        self.calls, self.batches, self.n = [], {}, 0

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, json))
        if url.endswith("/v1/messages"):
            if self.script:
                st, p = self.script.pop(0)
                return R(st, p, headers={"retry-after": "3"} if st == 429 else None)
            part = json["messages"][0]["content"][1]["text"].split("\n")[0].split(": ", 1)[1]
            return R(200, self.answer(part))
        if url.endswith("/count_tokens"):
            return R(200, {"input_tokens": self.tokens})
        if url.endswith("/v1/messages/batches"):
            self.n += 1
            bid = f"msgbatch_01Test{self.n}"
            self.batches[bid] = {"requests": json["requests"], "polls": 0}
            return R(200, batch_obj(bid, "in_progress", len(json["requests"])))
        raise AssertionError(url)

    def _results(self, bid):
        lines = []
        for rq in self.batches[bid]["requests"]:
            a = self.answer(rq["custom_id"])
            if a == "expired":
                res = {"type": "expired"}
            elif isinstance(a, tuple):
                res = {"type": "errored", "error": {"type": "error", "error": {"type": a[1], "message": a[2]}}}
            else:
                res = {"type": "succeeded", "message": a}
            lines.append({"custom_id": rq["custom_id"], "result": res})
        return lines[::-1]                                          # results come in any order

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers, None))
        bid = url.split("/batches/")[1].split("/")[0]
        if url.endswith("/results"):
            return R(200, None, text="\n".join(json.dumps(l) for l in self._results(bid)) + "\n")
        b = self.batches[bid]
        b["polls"] += 1
        n = len(b["requests"])
        if b["polls"] <= self.polls:
            return R(200, batch_obj(bid, "in_progress", n))
        res = self._results(bid)
        return R(200, batch_obj(bid, "ended", n, sum(r["result"]["type"] == "succeeded" for r in res),
                                sum(r["result"]["type"] == "errored" for r in res), sum(r["result"]["type"] == "expired" for r in res)))

    def of(self, kind):
        return [c for c in self.calls if c[1].endswith(kind)]


def patched(api):
    return mock.patch.multiple(D.requests, post=api.post, get=api.get)


def pdf_with(texts):
    """A small real PDF, one page per text (Helvetica), so pypdf can read the text layer."""
    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = pypdf.PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    fref = w._add_object(font)
    for t in texts:
        page = w.add_blank_page(612, 792)
        cs = DecodedStreamObject()
        cs.set_data(f"BT /F1 12 Tf 72 700 Td ({t}) Tj ET".encode())
        page[NameObject("/Contents")] = w._add_object(cs)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): fref})})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class PdfHTTP(TL.FakeHTTP):
    """test_librarian's fake KiCad/EasyEDA HTTP plus binary downloads (get_bytes) for datasheet links."""

    def __init__(self, pdfs=None, **kw):
        super().__init__({"C2002": TL.EE}, **kw)
        self.pdfs, self.fetched = pdfs or {}, []

    def get_bytes(self, url):
        self.fetched.append(url)
        if url in self.pdfs:
            v = self.pdfs[url]
            return (200, v, "application/pdf" if D.is_pdf(v) else "text/html", url, None)
        return (0, None, "", url, "ProxyError: proxy CONNECT refused (403 Forbidden)")


BOM2 = TL.BOM.replace("U4,1,NOWHERE1,,,NOWHERE1,,,", "U5,1,DSPART2,,ACME,DSPART2,C9999,Second datasheet part,")
PDF = TD.PDF


def run2(d, api, http=None, reader_kw=None, **kw):
    http = http or PdfHTTP({"https://example.com/ds.pdf": PDF, "https://www.lcsc.com/datasheet/C9999.pdf": PDF})
    out = Path(d) / "out"
    rd = D.Reader(out, Path(d) / "cache", **{"key": KEY, "http": http, "batch": True, "poll": 0, "sleep": lambda s: None,
                                             "log": lambda *a: None, **(reader_kw or {})})
    with patched(api):
        items, _ = TL.run(d, http=http, bom=BOM2, reader=rd, **kw)
    return items, rd, http


# ---------------------------------------------------------------- client
def test_retries_backoff_and_errors():
    api = FakeAPI(script=[(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
                          (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}),
                          (200, TD.message(TD.GOOD))])
    waits = []
    with tempfile.TemporaryDirectory() as d, patched(api):
        c = D.Client(KEY, sleep=waits.append, log_file=Path(d) / "log.jsonl")
        _, body = D.build_request(("base64", PDF), "TESTPART")
        msg = c.messages(body)
        assert msg["stop_reason"] == "end_turn" and len(api.of("/v1/messages")) == 3
        assert len(waits) == 2 and 2 <= waits[0] <= 3 and waits[1] == 3.0, waits     # backoff, then retry-after
        log = (Path(d) / "log.jsonl").read_text()
        recs = [json.loads(l) for l in log.splitlines()]
        assert [r.get("status") for r in recs] == [529, 429, 200] and recs[2]["usage"]["output_tokens"] == 2304
        assert KEY not in log and D.base64.b64encode(PDF).decode() not in log and "base64 chars" in log, log[:400]
        assert recs[0]["request_id"] == "req_529"
    for status, etype, want in ((401, "authentication_error", "API key was rejected"), (402, "billing_error", "billing / quota"),
                                (400, "billing_error", "billing / quota"), (404, "not_found_error", "model is not available"),
                                (413, "request_too_large", "fewer pages")):
        api = FakeAPI(script=[(status, {"type": "error", "error": {"type": etype, "message": "x"}})] * 9)
        with patched(api):
            try:
                D.Client(KEY, sleep=lambda s: None).messages({})
                raise AssertionError(status)
            except D.APIError as e:
                assert want in str(e) and not e.retryable and len(api.calls) == 1, (status, str(e))
    api = FakeAPI(script=[(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})] * 9)
    with patched(api):
        try:
            D.Client(KEY, retries=2, sleep=lambda s: None).messages({})
            raise AssertionError("no error")
        except D.APIError as e:
            assert "overloaded" in str(e) and "gave up after 3 attempts" in str(e) and len(api.calls) == 3, str(e)


def test_batch_create_never_sent_twice_and_key_stays_home():
    api = FakeAPI()
    calls = []
    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return R(500, {"type": "error", "error": {"type": "api_error", "message": "Internal"}})
    with mock.patch.multiple(D.requests, post=post, get=api.get):
        try:
            D.Client(KEY, sleep=lambda s: None).batch_create([{"custom_id": "a", "params": {}}])
            raise AssertionError("no error")
        except D.APIError as e:
            assert len(calls) == 1 and "HTTP 500" in str(e)            # may have gone through: not retried
        try:
            D.Client(KEY).batch_results({"id": "x", "results_url": "https://evil.example/results"})
            raise AssertionError("no error")
        except D.APIError as e:
            assert "refusing to send the API key" in str(e)
    api = FakeAPI()                                                     # many big parts: split over several batches
    with tempfile.TemporaryDirectory() as d, patched(api), mock.patch.object(D, "BATCH_BYTES", 1000):
        rd = D.Reader(d, d, key=KEY, http=PdfHTTP({"https://a/ds.pdf": PDF}), batch=True, poll=0, sleep=lambda s: None, log=None)
        for n in ("P/1", "P.1", "P2"):
            rd.submit(rd.job(n, [("https://a/ds.pdf", "BOM")], name=n))
        res = rd.flush()
        creates = api.of("/v1/messages/batches")
        assert len(creates) == 3 and all(isinstance(r, dict) for r in res.values()), res
        ids = [c[3]["requests"][0]["custom_id"] for c in creates]
        assert ids == ["P_1", "P_1", "P2"], ids                         # one per batch, so no clash inside a batch
        assert D.custom_id("P/1", {"P_1"}) == "P_1_2" and D.custom_id("x" * 80, set()) == "x" * 60


def test_key_from_rules_env():
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with mock.patch.dict(os.environ, dict(env, MY_CLAUDE_KEY="sk-from-rules"), clear=True):
        assert D.require_key(rules={"datasheet": {"api_key_env": "MY_CLAUDE_KEY"}}) == "sk-from-rules"
        try:
            D.require_key()
            raise AssertionError("no error")
        except D.DatasheetError as e:
            assert "ANTHROPIC_API_KEY is not set" in str(e)
    from ecadlib import rules as RU
    for bad, want in (({"datasheet": {"api_key_env": "sk-ant-abc 123"}}, "NAME of an environment variable"),
                      ({"datasheet": {"effort": "huge"}}, "datasheet.effort"), ({"datasheet": {"auto_pages": 9999}}, "0 to 600")):
        try:
            RU.load(bad)
            raise AssertionError(bad)
        except RU.RulesError as e:
            assert want in str(e), e
    assert RU.load({"datasheet": {"model": "claude-sonnet-5-5", "max_cost_usd": 5}})["datasheet"]["model"] == "claude-sonnet-5-5"


# ---------------------------------------------------------------- acquisition
def test_datasheet_urls_and_pdf_store():
    item = {"parameters": [{"name": "ComponentLink2URL", "value": "https://lcsc.example/x.pdf"},
                           {"name": "Supplier 1", "value": "LCSC"}, {"name": "Supplier Part Number 1", "value": "C1234"}]}
    urls = D.datasheet_urls(item, {"datasheet": "https://bom.example/a.pdf"}, {}, {"datasheet": ["https://kicad.example/k.pdf", "KiCad symbol X"]})
    assert urls == [("https://bom.example/a.pdf", "BOM"), ("https://kicad.example/k.pdf", "KiCad symbol X"),
                    ("https://lcsc.example/x.pdf", "LCSC datasheet link"),
                    ("https://www.lcsc.com/datasheet/C1234.pdf", "LCSC datasheet URL pattern (C1234)")], urls
    assert D.datasheet_urls({}, {"datasheet": "file:///etc/passwd"}, {}, {}) == []
    html = b"<!DOCTYPE html><html><body>LCSC viewer</body></html>"
    with tempfile.TemporaryDirectory() as d:
        http = PdfHTTP({"https://a/ds.pdf": PDF, "https://a/view": html})
        st = D.PdfStore(d, http)
        data, prov = st.fetch("https://a/ds.pdf", "BOM")
        assert data == PDF and prov["sha256"] == D.hashlib.sha256(PDF).hexdigest() and prov["where"] == "BOM" and not prov["from_cache"]
        assert json.loads(next((Path(d) / "datasheets").glob("*.json")).read_text())["url"] == "https://a/ds.pdf"
        data, prov = st.fetch("https://a/ds.pdf")
        assert prov["from_cache"] and http.fetched.count("https://a/ds.pdf") == 1
        for url, want in (("https://a/view", "not a PDF (an HTML page"), ("https://blocked/x.pdf", "ProxyError")):
            try:
                st.fetch(url)
                raise AssertionError(url)
            except D.DatasheetError as e:
                assert want in str(e), e
        try:
            D.PdfStore(d, http, offline=True).fetch("https://new/x.pdf")
            raise AssertionError("offline")
        except D.DatasheetError as e:
            assert "offline" in str(e)
        # Reader.job: first link is HTML -> skipped; blocked link -> URL source; nothing usable -> error
        rd = D.Reader(d, d, key=KEY, http=http, log=None)
        j = rd.job("TESTPART", [("https://a/view", "BOM"), ("https://a/ds.pdf", "LCSC")], "RGT")
        assert j["where"] == "LCSC" and j["body"]["messages"][0]["content"][0]["source"]["type"] == "base64"
        assert j["prov"]["attempts"][0]["error"].endswith("bytes)") and j["body"]["fallbacks"] == "default"
        j = rd.job("TESTPART", [("https://a/view", "BOM"), ("https://blocked/x.pdf", "pattern")])
        assert j["body"]["messages"][0]["content"][0]["source"] == {"type": "url", "url": "https://blocked/x.pdf"}
        try:
            rd.job("TESTPART", [("https://a/view", "BOM")])
            raise AssertionError("html only")
        except D.DatasheetError as e:
            assert "no datasheet link gave a PDF" in str(e)


def test_auto_page_selection_and_page_map():
    texts = [f"Electrical characteristics page {i}" for i in range(1, 61)]
    texts[2] = "Pin Configuration and Functions"
    texts[49] = "PACKAGE OUTLINE RGT0016C VQFN"
    texts[51] = "Recommended land pattern"
    data = pdf_with(texts)
    sent, pmap, hint, note = D.prepare(data, None, auto_pages=8)
    import pypdf
    assert isinstance(pmap, list) and {3, 50, 51, 52} <= set(pmap) and len(pmap) <= 8, pmap
    assert len(pypdf.PdfReader(io.BytesIO(sent)).pages) == len(pmap) and "picked by text" in note, note
    assert D._pg(pmap.index(50) + 1, pmap) == 50 and D._pg(2, 5) == 7
    sent, off, _, note = D.prepare(data, None, auto_pages=0)                   # 0 = always all pages
    assert sent == data and off == 0 and note == "all 60 pages sent"
    sent, off, _, _ = D.prepare(data, (50, 52))
    assert off == 49 and len(pypdf.PdfReader(io.BytesIO(sent)).pages) == 3
    # the original page numbers end up in the provenance
    ext = copy.deepcopy(TD.GOOD)
    res = D.process(ext, "TESTPART", "u", offset=pmap)
    assert res["sidecar"]["pin_table"]["page"] == pmap[2] and res["sidecar"]["dimensions"]["body_length"]["page"] == 31


# ---------------------------------------------------------------- self-check
def test_self_check_flags_disagreements():
    def rev(mut):
        e = copy.deepcopy(TD.GOOD)
        mut(e["package"])
        return [r for r in D.process(e, "TESTPART", "u")["needs_review"] if r.startswith("self-check")]
    assert rev(lambda p: None) == []
    assert any("says 20 pins" in r for r in rev(lambda p: p.update(name="VQFN-20-1EP_3x3mm_P0.5mm_EP1.45x1.45mm")))
    assert any("name says pitch 0.4" in r for r in rev(lambda p: p.update(name="VQFN-16-1EP_3x3mm_P0.4mm_EP1.45x1.45mm")))
    assert any("name says body 4x4" in r for r in rev(lambda p: p.update(name="VQFN-16-1EP_4x4mm_P0.5mm_EP1.45x1.45mm")))
    assert any("says EP 2.1x2.1" in r for r in rev(lambda p: p.update(name="VQFN-16-1EP_3x3mm_P0.5mm_EP2.1x2.1mm")))
    assert any("name VQFN-16_3x3mm_P0.5mm has no EP" in r for r in rev(lambda p: p.update(name="VQFN-16_3x3mm_P0.5mm")))
    r = rev(lambda p: p["pitch"].update(nom=0.55))
    assert any("not a standard pitch" in x for x in r) and any("name says pitch 0.5" in x for x in r), r
    r = rev(lambda p: p["pitch"].update(nom=2.0))
    assert any("pitch 2.0 mm is outside the usual 0.35-1.27 mm for qfn" in x for x in r), r
    assert D.name_facts("SOT-23-5")["count"] == 5 and D.name_facts("SOT-23")["count"] is None
    assert D.name_facts("SOIC-8_3.9x4.9mm_P1.27mm") == {"count": 8, "ep": False, "pitch": 1.27, "body": (3.9, 4.9), "ep_size": None}
    assert D.name_facts("HTSSOP-20-1EP_4.4x6.5mm_P0.65mm_EP3.4x6.5mm")["ep"]
    # a family the schema gains later (another helper adds BGA, connectors): generic checks + an info line, no crash
    spec = {"name": "BGA-64_8x8mm_P0.8mm", "package_type": "bga", "pin_count": 64, "pitch": 0.8,
            "body_length": {"nom": 8.0}, "body_width": {"nom": 8.0}}
    review = []
    info = D.self_check(spec, {"pins": [{"num": f"{r}{c}"} for r in "ABCDEFGH" for c in range(1, 9)]}, review)
    assert review == [] and "no family norms for package_type 'bga'" in info[0], (review, info)
    review = []
    D.check_pins(spec, {"pins": [{"num": f"{r}{c}", "name": "IO"} for r in "ABCDEFGH" for c in range(1, 9)],
                        "pin_table": {"rows": 64}}, review)
    assert review == [], review


# ---------------------------------------------------------------- cache + estimate
def test_result_cache_makes_reruns_free():
    api = FakeAPI()
    with tempfile.TemporaryDirectory() as d, patched(api):
        rd = D.Reader(d, d, key=KEY, http=PdfHTTP({"https://a/ds.pdf": PDF}), log=None)
        j = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT")
        assert rd.cached(j) is None
        res = rd.read_now(j)
        assert res["needs_review"] == [] and len(api.of("/v1/messages")) == 1
        assert (Path(d) / "datasheet" / "TESTPART.review.md").exists() and (Path(d) / "datasheet" / "api_log.jsonl").exists()
        assert rd.cached(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT"))["spec"]["name"] == res["spec"]["name"]
        assert len(api.of("/v1/messages")) == 1
        other = D.Reader(d, d, key=KEY, http=PdfHTTP({"https://a/ds.pdf": PDF}), model="claude-sonnet-5-5", log=None)
        assert other.cached(other.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT")) is None       # model is in the key
        with mock.patch.object(D, "SYSTEM", D.SYSTEM + " v2"):                                         # so is the schema/prompt
            assert rd.cached(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT")) is None
        bad = FakeAPI(answer=lambda p: dict(TD.message(TD.GOOD), stop_reason="refusal", content=[]))
        with patched(bad):
            j2 = rd.job("OTHERPART", [("https://a/ds.pdf", "BOM")])
            try:
                rd.read_now(j2)
                raise AssertionError("refusal")
            except D.DatasheetError:
                pass
            assert rd.cached(j2) is None                                                               # refusals not cached


def test_estimate_and_cost_cap():
    data = pdf_with([f"page {i}" for i in range(10)])
    with tempfile.TemporaryDirectory() as d:
        http = PdfHTTP({"https://a/ds.pdf": data})
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            rd = D.Reader(d, d, http=http, batch=True, log=None)
            est = rd.estimate([rd.job("P1", [("https://a/ds.pdf", "BOM")])])
        assert not est["exact_input"] and est["rows"][0]["how"].startswith("rough: 10 pages")
        tin = est["input_tokens"]
        assert abs(est["usd"] - (tin * 4 + D.OUT_TOKENS * 20) / 1e6 * 0.5) < 0.01 and est["usd_max"] > est["usd"]
        api = FakeAPI(tokens=20000)
        with patched(api):
            rd = D.Reader(d, d, key=KEY, http=http, batch=True, log=None, rules={"datasheet": {"max_cost_usd": 0.01}})
            j = rd.job("P1", [("https://a/ds.pdf", "BOM")])
            est = rd.estimate([j])
            assert est["exact_input"] and est["rows"][0]["how"] == "count_tokens" and est["input_tokens"] > 20000
            assert set(api.of("/count_tokens")[0][3]) == {"model", "system", "messages"}
            rd.submit(j)
            try:
                rd.flush()
                raise AssertionError("cap")
            except D.DatasheetError as e:
                assert "max_cost_usd" in str(e) and not api.of("/batches"), e
        assert "datasheet estimate: 1 parts" in D.format_estimate(est)


# ---------------------------------------------------------------- batch through the librarian
def test_librarian_batch_one_request_per_part():
    def answer(cid):
        return ("errored", "invalid_request_error", "bad pdf") if cid == "DSPART2" else TD.message(TD.GOOD)
    api = FakeAPI(answer=answer, polls=2)
    with tempfile.TemporaryDirectory() as d:
        items, rd, http = run2(d, api)
        creates = api.of("/v1/messages/batches")
        assert len(creates) == 1 and not api.of("/v1/messages")                     # nothing synchronous
        reqs = creates[0][3]["requests"]
        assert sorted(r["custom_id"] for r in reqs) == ["DSPART1", "DSPART2"], reqs
        p = reqs[0]["params"]
        assert "fallbacks" not in p and "anthropic-beta" not in creates[0][2] and creates[0][2]["x-api-key"] == KEY
        assert p["output_config"]["format"]["schema"] == D.output_schema() and p["max_tokens"] == D.MAX_TOKENS
        assert "cache_control" not in p["messages"][0]["content"][0] and p["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert len([c for c in api.calls if c[0] == "GET" and not c[1].endswith("/results")]) == 3   # 2 in_progress + ended
        assert "https://www.lcsc.com/datasheet/C9999.pdf" in http.fetched
        a, b = items["DSPART1"], items["DSPART2"]
        assert a["source"] == {"symbol": "datasheet:https://example.com/ds.pdf", "footprint": "datasheet:https://example.com/ds.pdf"}
        assert a["status"] == "held" and any(r.startswith("verify: datasheet reader output") for r in a["hold_reasons"])
        assert any("datasheet reader (BOM, batch msgbatch_01Test1)" in t for t in a["librarian"]["tried"]), a["librarian"]["tried"]
        assert b["source"] == {"symbol": None, "footprint": None}
        assert any("batch request errored: invalid_request_error: bad pdf" in t for t in b["librarian"]["tried"]), b["librarian"]["tried"]
        st = json.loads((Path(d) / "out" / "datasheet" / "batch_state.json").read_text())
        assert st["batches"][0]["collected"] and st["batches"][0]["errors"] == {"DSPART2": "batch request errored: invalid_request_error: bad pdf"}
        side = json.loads((Path(d) / "out" / "datasheet" / "DSPART1.extraction.json").read_text())
        assert side["pdf"]["where"] == "BOM" and side["pdf"]["sha256"] and side["via"] == "batch msgbatch_01Test1"
        # re-run: DSPART1 is finished (skipped); DSPART2 goes into a new batch alone
        api.answer = lambda cid: TD.message(TD.GOOD)
        items, _, _ = run2(d, api)
        creates = api.of("/v1/messages/batches")
        assert len(creates) == 2 and [r["custom_id"] for r in creates[1][3]["requests"]] == ["DSPART2"]
        assert items["DSPART2"]["source"]["symbol"].startswith("datasheet:https://www.lcsc.com/datasheet/C9999.pdf")


def test_batch_resumes_without_paying_twice():
    api = FakeAPI(polls=1)
    with tempfile.TemporaryDirectory() as d:
        items, _, _ = run2(d, api, reader_kw={"wait": False})                    # submit and return
        assert len(api.of("/v1/messages/batches")) == 1
        assert any("pending msgbatch_01Test1; run again to collect" in t for t in items["DSPART1"]["librarian"]["tried"])
        assert items["DSPART1"]["source"]["symbol"] is None
        items, _, _ = run2(d, api)                                                # same batch picked up, collected
        assert len(api.of("/v1/messages/batches")) == 1 and api.of("/results")
        assert items["DSPART1"]["source"]["symbol"].startswith("datasheet:") and items["DSPART2"]["source"]["footprint"]
        n = len(api.calls)
        items, _, _ = run2(d, api, refresh=["DSPART1"])                            # refreshed part: from the result cache
        assert len(api.calls) == n and items["DSPART1"]["source"]["symbol"].startswith("datasheet:")
        assert any("datasheet reader (BOM, cached)" in t for t in items["DSPART1"]["librarian"]["tried"])


def test_expired_and_no_key_and_sync_fallback():
    api = FakeAPI(answer=lambda cid: "expired" if cid == "DSPART1" else TD.message(TD.GOOD))
    with tempfile.TemporaryDirectory() as d:
        items, _, _ = run2(d, api)
        assert any("batch request expired (re-run to submit it again)" in t for t in items["DSPART1"]["librarian"]["tried"])
    api = FakeAPI()
    with tempfile.TemporaryDirectory() as d:
        items, _, _ = run2(d, api, reader_kw={"batch": False})
        assert len(api.of("/v1/messages")) == 2 and not api.of("/batches")
        assert api.of("/v1/messages")[0][3]["fallbacks"] == "default"            # sync calls keep the refusal fallback
        assert items["DSPART1"]["source"]["footprint"].startswith("datasheet:")


def test_fill_plan_and_cli():
    api = FakeAPI()
    with tempfile.TemporaryDirectory() as d:
        http = PdfHTTP({"https://example.com/ds.pdf": PDF, "https://www.lcsc.com/datasheet/C9999.pdf": PDF})
        TL.run(d, http=http, bom=BOM2)                                            # no key: datasheet parts stay open
        plan = Path(d) / "out" / "plan.json"
        before = {i["name"]: i for i in json.loads(plan.read_text())}
        assert before["DSPART1"]["source"]["symbol"] is None
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True), patched(api), mock.patch("ecadlib.httpcache.CachedHTTP", lambda *a, **k: http):
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                assert D.main(["estimate", str(plan), "--cache", str(Path(d) / "cache")]) == 0
            out = buf.getvalue()
            assert "datasheet estimate: 2 parts" in out and "rough" in out and not api.calls, out
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}):
                rc = D.main(["batch", str(plan), "--cache", str(Path(d) / "cache"), "--poll", "0"])
        assert rc == 2 and len(api.of("/v1/messages/batches")) == 1                # held for verify -> 2
        after = {i["name"]: i for i in json.loads(plan.read_text())}
        for n in ("DSPART1", "DSPART2"):
            assert after[n]["source"]["footprint"].startswith("datasheet:") and after[n]["status"] == "held"
            assert any(r.startswith("verify: datasheet reader output") for r in after[n]["hold_reasons"])
        assert after["RES-0603-10k"]["status"] == before["RES-0603-10k"]["status"] == "ready"
        assert "DSPART1" in (Path(d) / "out" / "release_check.md").read_text()
        state = json.loads((Path(d) / "out" / "librarian_state.json").read_text())
        assert state["DSPART1"]["footprint"]["file"]
        items, _ = TL.run(d, http=http, bom=BOM2)                                 # librarian keeps the filled parts
        assert items["DSPART1"]["source"]["footprint"].startswith("datasheet:")


def test_workspace_and_mcp_tools():
    names = {t["name"] for t in M.TOOLS}
    assert {"datasheet_batch", "datasheet_estimate"} <= names
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, [], providers=["digikey"], offline=True)
        bom = ws.save_upload("bom.csv", BOM2.encode())
        for url in ("https://example.com/ds.pdf", "https://www.lcsc.com/datasheet/C9999.pdf"):   # downloaded earlier
            store = D.PdfStore(ws.root / "http_cache", PdfHTTP({url: PDF}), offline=False)
            store.fetch(url)
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            try:
                ws.datasheet_estimate(bom)
                raise AssertionError("no plan")
            except ValueError as e:
                assert "run the librarian" in str(e)
            out = ws._librarian_dir(ws._bom_path(bom))
            TL.run(d, http=PdfHTTP(), bom=BOM2)
            out.mkdir(parents=True, exist_ok=True)
            for f in (Path(d) / "out").iterdir():                               # the plan the librarian made, in the workspace
                if f.is_file():
                    (out / f.name).write_bytes(f.read_bytes())
            import shutil
            shutil.copytree(Path(d) / "out" / "models", out / "models")
            est = ws.datasheet_estimate(bom)
            assert est["parts"] == 2 and not est["key_set"] and est["usd"] > 0, est
            try:
                ws.datasheet_batch(bom)
                raise AssertionError("no key")
            except ValueError as e:
                assert "ANTHROPIC_API_KEY is not set" in str(e)
        api = FakeAPI(polls=1)                                                  # first status check: in_progress
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), patched(api):
            lines = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "datasheet_batch", "arguments": {"bom": bom}}}]
            buf = io.BytesIO()
            M.serve(ws, io.BytesIO("\n".join(map(json.dumps, lines)).encode() + b"\n"), buf)
        resp = json.loads(buf.getvalue().decode().splitlines()[0])
        body = json.loads(resp["result"]["content"][0]["text"])
        assert not resp["result"]["isError"], body
        assert body.get("batches") and body["batches"][0]["id"] == "msgbatch_01Test1" and body["pending"] == 1, {k: body[k] for k in ("batches", "pending", "log")}
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), patched(api):
            r = ws.datasheet_batch(bom, wait=True)
        assert r["pending"] == 0 and len(api.of("/v1/messages/batches")) == 1
        row = next(x for x in r["review"] if x["part"] == "DSPART1")
        assert row["footprint_from"].startswith("datasheet:") and "verify" in row["why"], row


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
