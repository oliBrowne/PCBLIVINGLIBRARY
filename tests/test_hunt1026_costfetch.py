"""Bug hunt 2026-10-26 (costfetch): spend safety and datasheet fetching. Offline: fake clients, no key, no network.
    python3 -m tests.run_all test_hunt1026_costfetch"""
import json, math

import requests

from ecadlib import ai_cost as AC, datasheet as D
from ecadlib.eval import runner as ER
from tests.fixtures_lite import lite

KEY = "sk-ant-test-SECRET-123"


def _job(key, model="claude-opus-5-5", pages=600):
    return {"key": key, "name": "P", "model": model, "sync": True, "url": "",
            "body": {"model": model, "max_tokens": 32000, "messages": []},
            "prov": {"pages": pages, "sent_as": "base64"}}


# ---------------------------------------------------------------- costfetch-01: a cap of nan / inf held nothing
@lite
def test_01_nan_or_inf_cap_never_lifts_the_cap(tmp_path, monkeypatch):
    """--max-cost-usd nan (argparse type=float takes it) made every `committed + worst > cap` False: 50 reads of
    $9.70 each passed a cap that was meant to hold them."""
    for cap in (float("nan"), float("inf"), -1.0):
        rd = D.Reader(tmp_path / "o", tmp_path / "c", key=KEY, log=None, sleep=lambda s: None)
        try:
            rd.set_cap(cap)
            rd._guard_cost([_job("k")])
            raise AssertionError(f"cap {cap} let a read through (committed {rd.committed_usd})")
        except (ValueError, D.DatasheetError):
            pass
        assert rd.committed_usd == 0, (cap, rd.committed_usd)
    rd = D.Reader(tmp_path / "o", tmp_path / "c", key=KEY, log=None, sleep=lambda s: None,
                  rules={"datasheet": {"max_cost_usd": float("nan")}})     # a rules table not through rules.load
    try:
        rd._guard_cost([_job("k")])
        raise AssertionError("a nan cap in the settings let a read through")
    except D.DatasheetError as e:
        assert "max_cost_usd" in str(e), e


@lite
def test_01_nan_total_cap_refuses(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    (lib / "datasheet").mkdir(parents=True)
    (lib / "datasheet" / "cost.jsonl").write_text(json.dumps({"usd": 3.0}) + "\n", encoding="utf-8")
    for total, per_run in ((float("nan"), None), (float("inf"), None), (20.0, float("nan")), (-1.0, None)):
        try:
            got = ER.total_cap(lib, total, per_run, log=lambda *a: None)
        except ValueError:
            continue
        assert math.isfinite(got) and got <= 17.0, (total, per_run, got)


def test_01_cli_caps_refuse_nan():
    import argparse
    for bad in ("nan", "inf", "-1", "abc"):
        try:
            AC.usd_arg(bad)
            raise AssertionError(bad)
        except argparse.ArgumentTypeError:
            pass
    assert AC.usd_arg("0") == 0.0 and AC.usd_arg("2.5") == 2.5


# ---------------------------------------------------------------- costfetch-02: a paid request re-sent after a timeout
def test_02_openai_compat_never_resends_a_request_that_may_have_been_billed():
    """A read timeout after the request reached a paid server was retried 4 more times: up to 5 billed generations
    under the one worst case the cost cap held for it. datasheet.Client never re-sends such a request."""
    from ecadlib.llm.openai_compat import OpenAICompatClient
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        raise requests.exceptions.ReadTimeout("read timed out")
    cl = OpenAICompatClient("https://api.example.com/v1", "sk-x", {"price_in": 1.0, "price_out": 2.0},
                            sleep=lambda s: None, post=post, log_file=None, say=None, model="gpt-x")
    try:
        cl.chat({"model": "gpt-x", "messages": []})
        raise AssertionError("no error")
    except D.APIError as e:
        assert e.maybe_billed and "sk-x" not in str(e), e
    assert len(calls) == 1, calls
    calls.clear()

    def refused(url, headers=None, json=None, timeout=None):     # no connection made: safe to send again
        calls.append(url)
        raise requests.exceptions.ConnectTimeout("connect timed out")
    cl._post = refused
    try:
        cl.chat({"model": "gpt-x", "messages": []})
    except D.APIError as e:
        assert not e.maybe_billed
    assert len(calls) == 5, calls
    calls.clear()
    local = OpenAICompatClient("http://localhost:11434/v1", None, {}, sleep=lambda s: None, post=post,
                               log_file=None, say=None, model="qwen")      # a free local server: a retry costs nothing
    try:
        local.chat({"model": "qwen", "messages": []})
    except D.APIError:
        pass
    assert len(calls) == 5, calls


# ---------------------------------------------------------------- costfetch-03: the rough estimate missed images / text
@lite
def test_03_rough_estimate_counts_zoom_images_and_a_feedback_answer(tmp_path, monkeypatch):
    """Without count_tokens (the default cap's keyless estimate, every non-Claude model) the hold was pages x 3000:
    the zoom close-ups (up to 6 images, ~27K tokens) and a feedback turn's first answer were not in it, so a
    truncated batch answer could be billed above the hold."""
    from ecadlib.llm import rails as LRL
    from tests import test_datasheet_batch as TB
    data = TB.pdf_with(["PACKAGE OUTLINE DRAWING dimensions mm SOIC-8"] + [f"page {i} electrical" for i in range(12)]
                       + ["RECOMMENDED LAND PATTERN footprint pad"])
    rd = D.Reader(tmp_path, tmp_path, http=TB.PdfHTTP({"https://a/ds.pdf": data}), batch=False, log=None,
                  rules={"datasheet": {"zoom_drawings": True}})
    j = rd.job("P1", [("https://a/ds.pdf", "BOM")], "SOIC-8")
    n_img = sum(b["type"] == "image" for b in j["body"]["messages"][0]["content"])
    plain = dict(j, body=dict(j["body"], messages=[dict(j["body"]["messages"][0], content=[
        b for b in j["body"]["messages"][0]["content"] if b["type"] != "image"])]))
    base = rd.estimate([plain])["input_tokens"]
    if D.no_zoom() is None:
        assert n_img >= 2, n_img
    assert rd.estimate([j])["input_tokens"] >= base + n_img * D.IMG_TOKENS
    answer = {"content": [{"type": "text", "text": "x" * 60000}]}
    fb = LRL.feedback_body(plain["body"], answer, ["pad mismatch"])
    assert rd.estimate([dict(plain, body=fb)])["input_tokens"] >= base + 60000 // 3 - 1500
    assert D.rough_extra_tokens(plain["body"]) == 0          # an ordinary request: priced as before


# ---------------------------------------------------------------- costfetch-04: the web search's hold
def test_04_search_hold_grows_with_max_uses_and_takes_the_long_prompt_price():
    """ds_search.estimate held 90K input tokens at the base price whatever web_search_max_uses said (up to 10): ten
    searches read ~300K tokens, and Haiku 5.5 (the default search model) bills 5x over 100K, so one search could be
    billed several times its hold."""
    from ecadlib import ds_route as DR, ds_search as WS
    for model in ("claude-haiku-5-5", "claude-opus-5-5"):
        for uses in (1, 3, 4, 10):
            tin = WS.EST_TOKENS[0] * max(1, uses / WS.MAX_USES)
            usage = {"input_tokens": int(tin), "output_tokens": WS.MAX_TOKENS}
            billed = DR.usage_usd(model, usage) + WS.SEARCH_USD * uses
            assert WS.estimate(model, uses) >= billed - 1e-9, (model, uses, WS.estimate(model, uses), billed)
    assert abs(WS.estimate("claude-haiku-5-5", 3) - ((90000 * 0.10 + 4096 * 0.50) / 1e6 + 0.03)) < 1e-12   # unchanged
    turn = {"usage": {"input_tokens": 95000, "output_tokens": 500}}
    nxt = WS.next_turn("claude-haiku-5-5", [turn])
    assert nxt >= DR.usage_usd("claude-haiku-5-5", {"input_tokens": 95500 + 90000, "output_tokens": 4096}), nxt


# ---------------------------------------------------------------- costfetch-05: a BOM link's redirect to this machine
class _Raw:
    """urllib3's response body as read_capped reads it (read1 / read with decode_content)."""

    def __init__(self, body):
        import io as _io
        self.b = _io.BytesIO(body)

    def read1(self, n=-1, decode_content=True):
        return self.b.read1(n)

    def read(self, n=-1, decode_content=True, **kw):
        return self.b.read(n)

    def stream(self, n=65536, decode_content=True):
        while True:
            c = self.b.read(n)
            if not c:
                return
            yield c

    def close(self):
        pass

    def release_conn(self):
        pass


class _Adapter(requests.adapters.BaseAdapter):
    """A transport that answers from a table and records every URL requested (nothing leaves the process)."""

    def __init__(self, table):
        super().__init__()
        self.table, self.seen = table, []

    def send(self, request, **kw):
        import io as _io
        self.seen.append(request.url)
        status, headers, body = self.table.get(request.url, (404, {}, b"no"))
        r = requests.Response()
        r.status_code, r.url, r.request = status, request.url, request
        r.headers.update(headers)
        r.raw = _Raw(body)
        return r

    def close(self):
        pass


class _HTTP:
    offline, min_interval = False, 0

    def __init__(self, adapter):
        self.s = requests.Session()
        self.s.trust_env = False
        self.s.mount("http://", adapter)
        self.s.mount("https://", adapter)


@lite
def test_05_bom_link_redirect_to_loopback_or_metadata_is_never_requested(tmp_path, monkeypatch):
    """A BOM link is refused when it names 127.0.0.1 or 169.254.169.254, but one that redirects there was followed:
    the request reached the metadata service / a local admin page, and only its answer was thrown away afterwards."""
    from ecadlib import pdf_fetch as PF
    for target in ("http://169.254.169.254/latest/meta-data/iam/security-credentials/",
                   "http://127.0.0.1:8080/admin/reboot", "http://localhost/x.pdf"):
        ad = _Adapter({"https://maker.example.com/ds.pdf": (302, {"location": target}, b"")})
        st = D.PdfStore(tmp_path / "c", http=_HTTP(ad))
        try:
            st.fetch("https://maker.example.com/ds.pdf")
            raise AssertionError("fetched")
        except D.DatasheetError as e:
            assert "169.254" in str(e) or "127.0.0.1" in str(e) or "localhost" in str(e), e
        assert ad.seen == ["https://maker.example.com/ds.pdf"], ad.seen
        s = requests.Session()
        s.trust_env = False
        ad2 = _Adapter({"https://maker.example.com/ds.pdf": (302, {"location": target}, b"")})
        s.mount("https://", ad2)
        s.mount("http://", ad2)
        got = PF.headers_get("https://maker.example.com/ds.pdf", session=s)
        assert got[1] is None and got[4], got
        assert ad2.seen == ["https://maker.example.com/ds.pdf"], ad2.seen
    if PF.impersonate_available():                    # curl_cffi followed every redirect of a BOM link by itself
        class Resp:
            def __init__(self, url, status, loc=None):
                self.url, self.status_code, self.headers = url, status, {"location": loc} if loc else {}

            def iter_content(self):
                yield b"secret"

            def close(self):
                pass

        class Sess:
            def __init__(self):
                self.seen = []

            def get(self, url, allow_redirects=True, **kw):
                self.seen.append(url)
                if url.startswith("https://maker"):
                    return Resp(url, 302, "http://169.254.169.254/latest/meta-data/")
                return Resp(url, 200)
        imp = PF.Impersonate()
        imp._s = Sess()
        got = imp.get("https://maker.example.com/ds.pdf")
        assert got[1] is None and "169.254" in got[4], got
        assert imp._s.seen == ["https://maker.example.com/ds.pdf"], imp._s.seen
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    ad = _Adapter({"https://maker.example.com/a.pdf": (302, {"location": "http://10.0.0.5/ds.pdf"}, b""),
                   "http://10.0.0.5/ds.pdf": (200, {"content-type": "application/pdf"}, pdf)})
    st = D.PdfStore(tmp_path / "c2", http=_HTTP(ad))
    got = st._get("https://maker.example.com/a.pdf")         # your own network's datasheet server: still allowed
    assert got[0] == 200 and got[1] == pdf, got[:5]


# ---------------------------------------------------------------- costfetch-06: a maybe-billed request left no record
def test_06_a_request_that_may_have_been_billed_is_in_the_cost_ledger():
    """A read timeout on /v1/messages (the API may have run and billed the request) kept its hold for this run, but
    cost.jsonl got no line: a run resumed with --max-total-usd counted that spend as $0."""
    import tempfile
    from tests import test_datasheet_batch as TB

    class Timeout(TB.FakeAPI):
        def post(self, url, headers=None, json=None, timeout=None):
            if url.endswith("/v1/messages"):
                self.calls.append(("POST", url, headers, json))
                raise requests.exceptions.ReadTimeout("read timed out")
            return super().post(url, headers, json, timeout)
    api = Timeout(tokens=20000)
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = D.Reader(d, d, key=KEY, http=TB.PdfHTTP({"https://a/ds.pdf": TB.PDF}), log=None, sleep=lambda s: None,
                      rules={"datasheet": {"max_cost_usd": 5.0}})
        try:
            rd.read_now(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT"))
            raise AssertionError("no error")
        except D.DatasheetError as e:
            assert "billed" in str(e), e
        assert len(api.of("/v1/messages")) == 1                  # never sent twice
        held = rd.committed_usd
        got = AC.spent(rd.dir.parent)
        assert held > 0 and got["reads"] == 1 and abs(got["usd"] - round(held, 4)) < 1e-4, (held, got)
        rec = json.loads((rd.dir / "cost.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        assert rec["how"] == "maybe billed" and rec["part"] == "TESTPART", rec


# ---------------------------------------------------------------- costfetch-07: the small-model steps lost billed usage
def _steps_body():
    import base64
    from tests import test_ds_route as TR
    pdf = TR.pdf_lines([["TESTPART 3 A buck converter", "Pin Functions", "Pin No. Name Type", "1 SW O"],
                        ["Package outline RGT0016C VQFN", "Dim Min Nom Max", "D 2.90 3.00 3.10", "e 0.50 BSC"]])
    return {"model": "tiny-3b", "max_tokens": 16000, "messages": [{"role": "user", "content": [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                        "data": base64.b64encode(pdf).decode()}}, {"type": "text", "text": "read"}]}],
        "output_config": {"format": {"type": "json_schema", "schema": D.output_schema()}}}


def test_07_split_steps_failure_keeps_what_the_billed_calls_used():
    """identify answered (billed), then the dims call failed with HTTP 500 after its retries: the APIError carried no
    usage and was not maybe_billed, so the Reader gave back the whole hold and cost.jsonl got nothing for a paid call.
    A refusal in a step lost that step's usage the same way."""
    from ecadlib.llm import rails as RL
    from tests import test_llm as TL
    u = {"input_tokens": 1000, "output_tokens": 200}
    p = {"rails": ["split_steps", "json_repair", "schema_in_prompt"], "tier": "small", "guided": True}
    prof = {"context": 8192, "pdf": False, "vision": False, "json_schema": False}

    class Client:
        def __init__(self, fail):
            self.n, self.fail = 0, fail

        def messages(self, body, headers=None):
            self.n += 1
            if self.n == 1:
                return {"content": [{"type": "text", "text": json.dumps(TL.IDENT)}], "stop_reason": "end_turn", "usage": u}
            if self.fail == "api":
                raise D.APIError("HTTP 500: api_error (gave up after 5 attempts)", 500, "api_error", True)
            return {"content": [{"type": "text", "text": ""}], "stop_reason": "refusal", "usage": u}
    for fail in ("api", "refusal"):
        try:
            RL.run_steps(Client(fail), _steps_body(), prof, p, "TESTPART", None, [])
            raise AssertionError("no error")
        except D.DatasheetError as e:
            want = 1000 if fail == "api" else 2000
            assert (getattr(e, "usage", None) or {}).get("input_tokens") == want, (fail, getattr(e, "usage", None))
            if fail == "api":
                assert getattr(e, "maybe_billed", False), "an API error after billed steps must keep its hold"
    # the redo after a failing gate: its billed calls were dropped when it failed and the first answer was kept
    from ecadlib.llm import run as LR
    answers = [TL.IDENT, TL.DIMS, TL.PINS, "not json", "still not json"]

    class Seq:
        def messages(self, body, headers=None):
            a = answers.pop(0)
            return {"content": [{"type": "text", "text": a if isinstance(a, str) else json.dumps(a)}],
                    "stop_reason": "end_turn", "usage": u}
    inf = {"plan": dict(p, rails=p["rails"] + ["feedback_retry"], assist="auto"), "prof": prof,
           "ep": {"name": "x", "provider": "openai-compatible"}}
    job = {"body": _steps_body(), "part": "TESTPART", "package": None}
    msg = LR.ask(job, inf, None, lambda m: ("failed", ["symbol: pin count"]), Seq(), allow_feedback=True)
    assert msg["usage"]["input_tokens"] == 5000, msg["usage"]          # 3 steps + the redo's 2 attempts


# ---------------------------------------------------------------- costfetch-08: a 200 whose body did not read
def test_08_a_200_with_an_unreadable_body_keeps_its_hold():
    """HTTP 200 on /v1/messages (or /chat/completions) whose body is not JSON (cut off by a proxy, an HTML page) was a
    plain APIError: the Reader gave the hold back and wrote nothing, though the generation ran and is billed."""
    from unittest import mock
    from ecadlib.llm.openai_compat import OpenAICompatClient
    from tests import test_datasheet_batch as TB
    sent = []

    def post(url, headers=None, json=None, timeout=None, **kw):
        sent.append(url)
        return TB.R(200, None, text='{"id": "msg_1", "content": [{"type": "te')
    with mock.patch.object(D.requests, "post", post):
        try:
            D.Client(KEY, sleep=lambda s: None, log_file=None).messages({"model": "claude-opus-5-5", "messages": []})
            raise AssertionError("no error")
        except D.APIError as e:
            assert e.maybe_billed and KEY not in str(e), e
    assert len(sent) == 1, sent
    sent.clear()
    cl = OpenAICompatClient("https://api.example.com/v1", "sk-x", {}, sleep=lambda s: None, post=post, log_file=None,
                            say=None, model="gpt-x")
    try:
        cl.chat({"model": "gpt-x", "messages": []})
        raise AssertionError("no error")
    except D.APIError as e:
        assert e.maybe_billed, e
    assert len(sent) == 1, sent


# ---------------------------------------------------------------- costfetch-09: a damaged ledger line
@lite
def test_09_spent_skips_damaged_lines_and_never_sums_nan(tmp_path, monkeypatch):
    """A ledger line that is JSON but not an object crashed ai_cost.spent (AttributeError) after a paid run, and a
    "usd": NaN line (json writes NaN) made the folder total NaN, so --max-total-usd's cap became NaN: no cap."""
    f = AC.ledger(tmp_path)
    f.parent.mkdir(parents=True)
    f.write_text("\n".join([json.dumps({"usd": 1.5, "model": "m"}), "5", "[1, 2]", '{"usd": NaN}',
                            json.dumps({"usd": "abc"}), json.dumps({"usd": -3}), "{broken"]) + "\n", encoding="utf-8")
    got = AC.spent(tmp_path)
    assert got["usd"] == 1.5 and got["reads"] == 4 and got["unpriced"] == 3, got
    try:
        ER.total_cap(tmp_path, 10.0, log=lambda *a: None)
        raise AssertionError("an unpriced line must refuse a folder total")
    except ValueError as e:
        assert "no price" in str(e), e


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_hunt1026_costfetch"]))
