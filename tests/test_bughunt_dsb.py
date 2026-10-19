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


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_bughunt_dsb"]))
