"""Regression tests for the bug-hunt findings DS-34 .. DS-66 (datasheet reader, model layer, cost guard, httpcache,
openai_compat). Offline: fake HTTP layers and local test servers only; no key, no paid call.
    python3 -m tests.test_bughunt_dsb"""
import json, os, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from ecadlib import datasheet as D
from tests.fixtures_lite import lite
from tests.test_datasheet_batch import FakeAPI, PdfHTTP, R, KEY, PDF, patched


def _serve(handler, host="127.0.0.1"):
    srv = ThreadingHTTPServer((host, 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- DS-66: the key never follows a redirect
def test_ds66_api_key_not_sent_on_redirect():
    got = []

    class Other(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got.append(self.headers.get("x-api-key"))
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(500)
            self.send_header("content-length", "0")
            self.end_headers()
    other = _serve(Other)

    class Base(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(307)
            self.send_header("location", f"http://127.0.0.1:{other.server_address[1]}/steal")
            self.send_header("content-length", "0")
            self.end_headers()
    base = _serve(Base)
    try:
        c = D.Client("sk-ant-SECRET-KEY-123456", base=f"http://127.0.0.1:{base.server_address[1]}", retries=0,
                     sleep=lambda s: None)
        try:
            c.messages({"model": "m", "max_tokens": 1, "messages": []})
            raise AssertionError("no error")
        except D.APIError as e:
            assert "redirect" in str(e) and "not followed" in str(e), e
        assert got == [], got
    finally:
        base.shutdown()
        other.shutdown()


# ---------------------------------------------------------------- DS-50: no key over plain http to a remote host
def test_ds50_plain_http_remote_base_refused():
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(url)
        raise D.requests.ConnectionError("stop")
    with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "http://llm-gateway.example.com"}), \
            mock.patch.object(D.requests, "post", post):
        try:
            D.Client("sk-ant-real-key-123456", sleep=lambda s: None, retries=0).messages({"model": "m"})
            raise AssertionError("no error")
        except D.APIError as e:
            assert "unencrypted" in str(e)
    assert sent == []
    D.Client("k", base="http://127.0.0.1:9")                  # a local test server / LAN gateway stays allowed
    D.Client("k", base="https://llm-gateway.example.com")


# ---------------------------------------------------------------- DS-48: gateway credentials never logged
def test_ds48_base_url_credentials_not_logged():
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "api_log.jsonl"

        def post(url, headers=None, json=None, timeout=None):
            raise D.requests.ConnectionError(f"HTTPConnectionPool: cannot reach {url}")
        with mock.patch.object(D.requests, "post", post):
            c = D.Client(KEY, base="http://gwuser:SuperSecretGwPass@127.0.0.1:9", retries=0, log_file=log)
            try:
                c.messages({"model": "m", "messages": []})
                raise AssertionError("no error")
            except D.APIError as e:
                assert "SuperSecretGwPass" not in str(e) and "gwuser" not in str(e), e
        text = log.read_text()
        assert "SuperSecretGwPass" not in text and '"url": "http://127.0.0.1:9/v1/messages"' in text, text
    from ecadlib.llm.openai_compat import OpenAICompatClient
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "log.jsonl"

        def post(url, headers=None, json=None, timeout=None):
            return R(401, {"error": {"message": "bad key"}})
        c = OpenAICompatClient("https://u:SuperSecretGwPass@gw.example/v1", "k", {"json_schema": True}, post=post,
                               log_file=log, sleep=lambda s: None)
        try:
            c.chat({"model": "m", "messages": []})
            raise AssertionError("no error")
        except D.APIError as e:
            assert "SuperSecretGwPass" not in str(e)
        assert "SuperSecretGwPass" not in log.read_text()


# ---------------------------------------------------------------- DS-34: batch results through a gateway
def test_ds34_gateway_collects_batch_with_anthropic_results_url():
    api = FakeAPI(polls=0)                      # results_url names https://api.anthropic.com/...
    with tempfile.TemporaryDirectory() as d, patched(api), \
            mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://llm-gateway.example.com"}):
        rd = D.Reader(d, d, key=KEY, http=PdfHTTP({"https://a/ds.pdf": PDF}), log=None, batch=True, poll=0,
                      sleep=lambda s: None, rules={"datasheet": {"cross_check": "off"}})
        rd.submit(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT"))
        got = rd.flush()
        assert all(isinstance(v, dict) for v in got.values()), got
        assert {c[1].split("/v1")[0] for c in api.calls} == {"https://llm-gateway.example.com"}
        st = json.loads((Path(d) / "datasheet" / "batch_state.json").read_text())
        assert [b["collected"] for b in st["batches"]] == [True]
        try:                                    # any other host is still refused
            D.Client(KEY).batch_results({"id": "x", "results_url": "https://evil.example/v1/messages/batches/x/results"})
            raise AssertionError("no error")
        except D.APIError as e:
            assert "refusing to send the API key" in str(e)


# ---------------------------------------------------------------- DS-53: only a format 400 drops the schema
def test_ds53_non_format_400_keeps_schema():
    from ecadlib.llm.openai_compat import OpenAICompatClient
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json.get("response_format"))
        return R(400, {"error": {"message": "bad request: messages too long"}})
    c = OpenAICompatClient("https://x.example/v1", "k", {"json_schema": True, "pdf": True}, post=post, sleep=lambda s: None)
    body = {"model": "m", "max_tokens": 10, "system": [{"type": "text", "text": "s"}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "output_config": {"format": {"type": "json_schema", "schema": {"type": "object"}}}}
    try:
        c.messages(body)
        raise AssertionError("no error")
    except D.APIError:
        pass
    assert len(calls) == 1 and calls[0]["type"] == "json_schema", calls
    assert c.format_fallback is None


# ---------------------------------------------------------------- DS-35 / DS-36: pad-table reads
def _connector(x2=5.0, names=("A", "B")):
    import copy
    from tests.test_datasheet import GOOD, pin
    e = copy.deepcopy(GOOD)
    e["package"].update({k: None for k in e["package"] if k not in ("name", "description", "package_type", "unresolved",
                                                                     "drawing")})
    e["package"].update(name="CONN-2_P5mm", description="2-pin", package_type="connector", pin_count=2, unresolved=[],
                        pad_table={"unit": "mm", "y_axis": "up", "view": "top", "origin": "center", "center_at": None,
                                   "footprint_origin": None, "default_pad": None, "rows": None,
                                   "pads": [{"number": "1", "x": 0, "y": 0, "w": 2, "h": 2, "shape": "rect"},
                                            {"number": "2", "x": x2, "y": 0, "w": 2, "h": 2, "shape": "rect"}]})
    e["pins"] = [pin(1, names[0]), pin(2, names[1])]
    e["pin_table"]["rows"] = 2
    return e


def _proc(e, part="T"):
    import copy
    from tests.test_datasheet import message
    return D.process(D.parse_message(message(D.to_wire(copy.deepcopy(e)))), part, "x")


def test_ds36_clean_pad_table_read_is_ok():
    from ecadlib import ds_route as DR
    r = _proc(_connector())
    assert DR.assess(r) == ("ok", []), DR.assess(r)
    assert any("copied as the model gave it" in x for x in r["needs_review"])     # the advice is still shown


def test_ds35_pad_table_problems_are_messages():
    r = _proc(_connector(x2=1.0))
    lines = [x for x in r["needs_review"] if x.startswith("pad_table:")]
    assert lines and all("{" not in x and "'level'" not in x for x in lines), r["needs_review"]
    assert any("overlap" in x for x in lines)


# ---------------------------------------------------------------- DS-57: single-part CLI
def _cli(tmp, answers, rules=""):
    import contextlib, io
    from tests.test_datasheet import message
    api = FakeAPI(script=[(200, a if "content" in a else message(D.to_wire(a))) for a in answers])
    (tmp / "t.pdf").write_bytes(PDF)
    (tmp / "r.toml").write_text(rules)
    out = io.StringIO()
    with patched(api), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), contextlib.redirect_stdout(out):
        os.environ.pop("ANTHROPIC_BASE_URL", None)
        try:
            rc = D.main([str(tmp / "t.pdf"), "--part", "TESTPART", "--package", "RGT", "-o", str(tmp / "out"),
                         "--rules", str(tmp / "r.toml")])
        except SystemExit as e:
            rc = e.code
    return rc, api, out.getvalue()


@lite
def test_ds57_unparseable_paid_answer_is_saved(tmp_path):
    from tests.test_datasheet import GOOD, message
    w = D.to_wire(GOOD)
    w["package"]["other_json"] = "{not json"
    rc, api, _ = _cli(tmp_path, [message(w), message(w)])
    assert rc and "not JSON" in str(rc) and "--from-response" in str(rc), rc
    saved = json.loads((tmp_path / "out" / "TESTPART.response.json").read_text())
    assert saved["failed"] and saved["message"]["content"]


# ---------------------------------------------------------------- DS-47 / DS-46 / DS-65: quotes and prices
def test_ds47_quote_prices_the_request_the_run_sends():
    from ecadlib import rules as RU
    api = FakeAPI(tokens=20000)
    with tempfile.TemporaryDirectory() as d, patched(api):
        http = PdfHTTP({"https://a/ds.pdf": PDF})
        rules = RU.load({"datasheet": {"cross_check": "off"}})
        q_rd = D.Reader(d, d, key=KEY, http=http, log=None, rules=rules, batch=False, estimate=True)
        qj = q_rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT")
        quoted = q_rd.estimate([qj])["usd_max"]
        rd = D.Reader(d, d + "/run", key=KEY, http=http, log=None, rules=rules, batch=False)
        rj = rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT")
        assert "fallbacks" in qj["body"] and qj["body"].keys() == rj["body"].keys()
        assert quoted >= rd.estimate([rj])["usd_max"] - 1e-9


def test_ds46_ask_above_zero_always_asks():
    from ecadlib import ai_cost as AC
    est = {"rows": [{"part": "P", "model": "claude-opus-5-5", "input_tokens": 1000}], "mode": "synchronous",
           "usd": 0.05, "usd_max": 0.4, "models": {"claude-opus-5-5": 1}}
    rules = {"datasheet": {"ask_above_usd": 0, "cross_check": "off"}}
    assert AC.quote(est, rules)["over_limit"] is True
    assert AC.quote(est, {"datasheet": {"ask_above_usd": 1.0, "cross_check": "off"}})["over_limit"] is False
    assert AC.quote(est, {"datasheet": {"max_cost_usd": 0, "ask_above_usd": 1.0, "cross_check": "off"}})["over_limit"] is False


def test_ds65_nan_price_rejected_and_unknown():
    from ecadlib.llm import profiles as PR
    from ecadlib import llm as LL
    assert PR.check_field("price_in", float("nan"))
    assert PR.check_field("price_out", float("inf"))
    assert PR.check_field("price_in", 0.5) is None
    with mock.patch.dict(LL._REG, {"acme-nan": {"endpoint": {"local": False}, "profile": {"price_in": float("nan"),
                                                                                          "price_out": 1.0}}}):
        assert LL.price("acme-nan") is None


# ---------------------------------------------------------------- DS-39 / DS-41 / DS-42 / DS-43 / DS-51 / DS-61
def test_ds39_eval_tol_rejects_bad_numbers():
    import contextlib, io
    from ecadlib.eval import cli as EC
    for bad in ("pos=abc", "pos=nan", "pos=-1"):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                EC.main(["run", "--tol", bad, "--out", "/nonexistent/never-written"])
                raise AssertionError(f"{bad} accepted")
            except SystemExit as e:
                assert e.code == 2, (bad, e.code)
        assert "--tol" in err.getvalue(), err.getvalue()


def test_ds41_dated_model_ids_behave_like_their_alias():
    from ecadlib.llm import sampling as SM
    for dated, alias in (("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
                         ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5")):
        assert D.output_config(dated, "high", {}) == D.output_config(alias, "high", {})
        assert "effort" not in D.output_config(dated, "high", {})
        assert D.page_limit(dated) == D.page_limit(alias) == 100
        assert D.price_of(dated) == D.price_of(alias) is not None
        assert SM.takes_sampling(dated) == SM.takes_sampling(alias) is True
    assert SM.takes_sampling("claude-opus-5-5") is False


@lite
def test_ds42_unknown_part_name_is_an_error(tmp_path):
    import contextlib, io
    (tmp_path / "plan.json").write_text(json.dumps([{"name": "TESTPART", "parameters": [], "footprint": {}}]))
    for verb in ("estimate", "batch"):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}):
            rc = D.main([verb, str(tmp_path / "plan.json"), "--part", "TESTPRAT", "--offline", "--cache",
                         str(tmp_path / "cache")])
        assert rc == 1 and "no part named TESTPRAT" in err.getvalue() and "TESTPART" in err.getvalue(), (rc, err.getvalue())
    assert "nothing to read" in D.format_estimate({"rows": [], "parts": 0})


def test_ds43_malformed_content_is_rejected_not_a_crash():
    from ecadlib.llm import rails as RL
    for m in ({"type": "message", "content": None, "stop_reason": "end_turn"},
              {"type": "message", "content": [{"type": "text", "text": None}], "stop_reason": "end_turn"},
              {"type": "message", "content": "hello", "stop_reason": "end_turn"}):
        try:
            D.parse_message(m)
            raise AssertionError("accepted")
        except D.DatasheetError as e:
            assert "not JSON" in str(e)
        assert RL.text_of(m) == ""
        fb = RL.feedback_body({"model": "m", "messages": []}, m, ["x"])
        assert fb["messages"][0]["content"] == [{"type": "text", "text": ""}]


def test_ds51_huge_page_range_is_refused_by_page_count():
    from tests.test_datasheet_batch import pdf_with
    data = pdf_with(["a", "b", "c"])
    try:
        D.trim(data, (2, 999_999_999))
        raise AssertionError("accepted")
    except D.DatasheetError as e:
        assert "the PDF has 3 pages" in str(e)


def test_ds61_tsot_names_give_the_pin_count():
    assert D.name_facts("TSOT-23-5")["count"] == 5
    assert D.name_facts("TSOT-23-6_MK06A")["count"] == 6
    assert D.name_facts("SOT-23-5")["count"] == 5
    assert D.name_facts("SOIC-8_3.9x4.9mm_P1.27mm")["count"] == 8


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_bughunt_dsb"]))
