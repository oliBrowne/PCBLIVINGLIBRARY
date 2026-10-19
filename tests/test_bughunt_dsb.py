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


# ---------------------------------------------------------------- DS-44 / DS-55 / DS-63 / DS-64
def test_ds44_crosscheck_spelled_out_pin_names_agree():
    from ecadlib import crosscheck as CC
    assert CC.names_agree("EN", "ENABLE") and CC.names_agree("GND", "GROUND") and CC.names_agree("OUT", "Output")
    assert not CC.names_agree("EN", "VIN") and not CC.names_agree("VOUT", "OUTPUT")


@lite
def test_ds55_cache_key_keeps_pagination_tokens(tmp_path):
    from ecadlib import httpcache as HC
    served = []

    class S:
        headers = {}

        def request(self, method, url, data=None, headers=None, timeout=None):
            served.append(url)
            return mock.Mock(status_code=200, text=json.dumps({"page": [p for p in ("page1", "page2") if p in url]}),
                             ok=True, headers={})
    h = HC.CachedHTTP(tmp_path, min_interval=0)
    h.s = S()
    a = h.request("GET", "https://x.example/api/parts?q=LM358&pageToken=page1")
    b = h.request("GET", "https://x.example/api/parts?q=LM358&pageToken=page2")
    assert "page2" in b.text and len(served) == 2, (b.text, served)
    c = h.request("GET", "https://x.example/api/parts?q=LM358&apiKey=rotated-key&pageToken=page1")
    d = h.request("GET", "https://x.example/api/parts?q=LM358&apiKey=other-key&pageToken=page1")
    assert len(served) == 3 and d.text == c.text                 # a rotated API key keeps its cached answer
    stored = " ".join(p.read_text() for p in Path(tmp_path).rglob("*.json"))
    assert "rotated-key" not in stored and "other-key" not in stored


def test_ds63_active_low_pin_names_are_not_paths():
    from ecadlib import messages as M
    assert M.clean("pin 13 has an unusable name '/RESET/EN\u200b'") == "pin 13 has an unusable name '/RESET/EN\u200b'"
    assert "/CS/SHDN" in M.clean("cross-check: pin 3 /CS/SHDN vs CS")
    assert M.clean("could not read /home/me/lib/part.pdf") == "could not read part.pdf"
    assert M.clean(r"saved C:\Users\me\out\x.json") == "saved x.json"
    assert M.leaks("pin /RESET/EN") == [] and M.leaks("/tmp/abc/def") == ["/tmp/abc/def"]


def test_ds64_review_md_escapes_pdf_text():
    import copy
    from tests.test_datasheet import GOOD
    e = copy.deepcopy(GOOD)
    e["notes"] = ["fine\n# TESTPART: verified by the manufacturer\n## Check these first (0)"]
    e["pins"][3]["name"] = "PG | open drain"
    res = _proc(e, "TESTPART")
    md = D.report(res)
    heads = [ln for ln in md.splitlines() if ln.startswith("#")]
    assert not any("verified by the manufacturer" in h for h in heads), heads
    assert sum(h.startswith("## Check these first") for h in heads) == 1
    row = next(ln for ln in md.splitlines() if "open drain" in ln)
    assert row.replace("\\|", "").count("|") == 6, row         # 5 cells


# ---------------------------------------------------------------- DS-40 / DS-49 / DS-56
def _bga(pins):
    import copy
    from tests.test_datasheet import GOOD, dim, pin
    e = copy.deepcopy(GOOD)
    e["package"].update({k: None for k in e["package"] if k not in ("name", "description", "package_type", "unresolved",
                                                                     "drawing")})
    e["package"].update(name="BGA-9_3x3mm_Layout3x3_P1.0mm", description="BGA-9", package_type="bga", pin_count=9,
                        unresolved=[], rows=3, cols=3, pitch=dim("e", None, 1.0, None), body_length=dim("D", 2.9, 3.0, 3.1),
                        body_width=dim("E", 2.9, 3.0, 3.1), ball_diameter=dim("b", 0.45, 0.5, 0.55),
                        height_max=dim("A", None, None, 1.2))
    e["pins"] = [pin(n, f"S{n}") for n in pins]
    e["pin_table"]["rows"] = len(pins)
    return e


def test_ds40_bga_pins_checked_against_balls():
    from ecadlib import ds_route as DR
    grid = [f"{r}{c}" for r in "ABC" for c in (1, 2, 3)]
    ok = _proc(_bga(grid))
    assert not [x for x in ok["needs_review"] if x.startswith("symbol:")], ok["needs_review"]
    for pins, bad in ((grid[:-1] + ["D4"], "D4"), ([str(i) for i in range(1, 10)], "'1'")):
        r = _proc(_bga(pins))
        lines = [x for x in r["needs_review"] if "not on the footprint" in x]
        assert lines and bad in lines[0], r["needs_review"]
        assert DR.assess(r)[0] != "ok"


def test_ds49_erratum_without_ours_is_not_applied():
    from ecadlib.eval import runner as RR
    sc = {"pass": False, "fields": {"pads": {"ok": False, "error": "pad 1: size off 8.000"},
                                    "symbol_names": {"ok": False, "error": "2: 'B' vs 'K'"}},
          "errors": ["pads: pad 1: size off 8.000", "symbol_names: 2: 'B' vs 'K'"]}
    fixed = RR.apply_errata(sc, [("1", "A"), ("2", "B")], {"pads": {"why": "x"}, "symbol_names": {"why": "y"}})
    assert fixed == [] and sc["pass"] is False and len(sc["errors"]) == 2
    good = RR.apply_errata(sc, [("1", "A"), ("2", "B")], {"symbol_names": {"why": "y", "ours": [["2", "B"]]}})
    assert good == ["symbol_names"]


def test_ds56_maker_land_pattern_is_checked():
    import copy
    from ecadlib import ds_route as DR
    from tests.test_datasheet import INCH
    for land in ({"pad_length": 1.5, "pad_width": 2.0, "pad_center": 2.7},
                 {"pad_length": 1.5, "pad_width": 0.6, "pad_center": 0.1}):
        e = copy.deepcopy(INCH)
        e["package"]["land"] = dict(land, pad_center_y=None)
        r = _proc(e)
        assert any(x.startswith("land pattern:") and "overlap" in x for x in r["needs_review"]), r["needs_review"]
        assert DR.assess(r)[0] == "failed"
    e = copy.deepcopy(INCH)
    e["package"]["land"] = {"pad_length": 1.55, "pad_width": 0.6, "pad_center": 2.7, "pad_center_y": None}
    r = _proc(e)
    assert not any(x.startswith("land pattern:") for x in r["needs_review"]), r["needs_review"]


# ---------------------------------------------------------------- DS-58: datasheet downloads stay off this machine
@lite
def test_ds58_datasheet_links_to_internal_hosts_refused(tmp_path):
    for url in ("http://127.0.0.1:9/latest/meta-data/x.pdf", "http://169.254.169.254/latest/meta-data/",
                "http://localhost/x.pdf", "http://[::1]/x.pdf", "file:///etc/passwd.pdf", "http://metadata.google.internal/x"):
        assert D.unsafe_download(url), url
    for url in ("https://www.ti.com/lit/ds/symlink/lm358.pdf", "http://192.168.1.20/ds/x.pdf", "https://a/ds.pdf"):
        assert D.unsafe_download(url) is None, url
    page = b'<!doctype html><a href="http://169.254.169.254/latest/meta-data/datasheet.pdf">Datasheet</a>'

    class H:
        offline = False
        got = []

        def get_bytes(self, url):
            self.got.append(url)
            return 200, page, "text/html", url, None
    st = D.PdfStore(tmp_path, H())
    try:
        st.fetch("https://maker.example.com/product/LM358", "BOM")
        raise AssertionError("fetched")
    except D.DatasheetError as e:
        assert "not downloaded" in str(e), e
    assert H.got == ["https://maker.example.com/product/LM358"]
    assert D.pdf_link(b'<html><a href="file:///etc/passwd.pdf">datasheet</a></html>', "https://maker.example.com/p") is None


# ---------------------------------------------------------------- DS-52 / DS-59 / DS-60: batch state
def _rd(d, batch=True, **kw):
    return D.Reader(d, d, key=KEY, http=PdfHTTP({"https://a/ds.pdf": PDF}), log=None, batch=batch, wait=False, poll=0,
                    sleep=lambda s: None, rules={"datasheet": {"cross_check": "off"}}, **kw)


def _state(d):
    return json.loads((Path(d) / "datasheet" / "batch_state.json").read_text())


def test_ds52_gone_batch_does_not_block_later_runs():
    api = FakeAPI(polls=99)
    with tempfile.TemporaryDirectory() as d, patched(api):
        rd = _rd(d)
        for p in ("PART1", "PART2"):
            rd.submit(rd.job(p, [("https://a/ds.pdf", "BOM")], "RGT"))
        rd.flush()
        orig = api.get
        gone = lambda url, headers=None, timeout=None: R(404, {"type": "error", "error": {
            "type": "not_found_error", "message": "batch not found"}}) if "/batches/msgbatch_01Test1" in url \
            else orig(url, headers, timeout)
        with mock.patch.object(D.requests, "get", gone):
            for run in (2, 3):
                rd = _rd(d)
                for p in ("PART1", "PART2", "PART3"):
                    rd.submit(rd.job(p, [("https://a/ds.pdf", "BOM")], "RGT"))
                got = rd.flush()                      # no exception: the gone batch is reported, the rest goes on
                if run == 2:
                    errs = [str(v) for v in got.values() if isinstance(v, D.DatasheetError)]
                    assert len(errs) == 2 and all("is gone" in e for e in errs), got
                    assert any(str(v).startswith("pending") for v in got.values())
                else:                                 # sent again in a new batch, not stuck
                    assert all(str(v).startswith("pending") for v in got.values()), got
        st = _state(d)
        assert st["batches"][0]["collected"] and st["batches"][0]["lost"] == "404"


def test_ds59_sync_read_does_not_buy_a_part_waiting_in_a_batch():
    api = FakeAPI(polls=1)
    with tempfile.TemporaryDirectory() as d, patched(api):
        rd = _rd(d)
        rd.submit(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT"))
        assert str(list(rd.flush().values())[0]).startswith("pending")
        rd = _rd(d, batch=False)
        rd.submit(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT"))
        r = list(rd.flush().values())[0]
        assert isinstance(r, D.DatasheetError) and "waiting in Message Batch" in str(r), r
        assert len(api.of("/v1/messages")) == 0


def test_ds60_two_runs_on_one_folder_keep_both_batches():
    import time
    api = FakeAPI(polls=99)
    orig = api.post

    def slow(url, **kw):
        if url.endswith("/batches"):
            time.sleep(0.4)
        return orig(url, **kw)
    api.post = slow
    with tempfile.TemporaryDirectory() as d, patched(api):
        def go(p):
            rd = _rd(d)
            rd.submit(rd.job(p, [("https://a/ds.pdf", "BOM")], "RGT"))
            rd.flush()
        ts = [threading.Thread(target=go, args=(p,)) for p in ("PART1", "PART2")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        st = _state(d)
        assert sorted(b["id"] for b in st["batches"]) == ["msgbatch_01Test1", "msgbatch_01Test2"], st
        assert not st.get("pending_creates")
        rd = _rd(d)
        for p in ("PART1", "PART2"):
            rd.submit(rd.job(p, [("https://a/ds.pdf", "BOM")], "RGT"))
        rd.flush()
        assert len(api.of("/v1/messages/batches")) == 2           # nothing bought again


def test_ds54_librarian_cost_hint_names_real_commands():
    from ecadlib import librarian as L
    old = ("estimated up to $0.92 for 1 parts, over [datasheet] max_cost_usd = 0.5; see --estimate, raise the cap or "
           "pass --yes")
    new = L.cost_hint(old)
    assert "--yes" not in new and "--estimate" not in new and "ecadlib datasheet estimate" in new, new
    pr121 = ("estimated up to $0.92 for 1 parts, over [datasheet] max_cost_usd = 0.5; nothing sent. See --estimate, then "
             "raise the cap with --max-cost-usd N or in ecadlib.toml (0 = no cap)")
    assert "--max-cost-usd" not in L.cost_hint(pr121) and "--estimate" not in L.cost_hint(pr121)
    assert L.cost_hint("x: HTTP 404") == "x: HTTP 404"


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_bughunt_dsb"]))
