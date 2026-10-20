"""Regression tests for the datasheet-reading debug sweep (group dsread: DS2-12 ..).
Plain functions (tests.run_all, no pytest). Every API is faked; nothing is sent anywhere."""
from ecadlib import datasheet as D
from tests.fixtures_lite import lite
from tests.test_bughunt_dsa import _rejected, _review, _wire


# ---- DS2-12: a toleranced or range value with its own unit skipped the DS-3 unit check
def test_ds2_12_toleranced_value_in_another_unit_is_rejected():
    for v in ("0.016~0.050 in", "0.016±0.002 in", "16±2 mil", "0.016 to 0.050 inch"):
        _rejected(_wire(lambda w, d: d["terminal_length"].update(min="", nom=v, max="")),
                  "terminal_length.nom", "'mm'")

    def printed_in_inches(w, d):                      # the same value, seen only in the unresolved note
        d["terminal_length"].update(min="", nom="0.4", max="")
        w["package"]["unresolved"] = ["terminal_length min/max (printed as 0.4±0.1 in)"]
    _rejected(_wire(printed_in_inches), "terminal_length", "'in'", "'mm'")

    res, (kind, _) = _review(lambda w, d: d["terminal_length"].update(min="", nom="0.3~0.5 mm", max=""))
    assert kind == "ok", (kind, res["needs_review"])  # the record's own unit written in the cell is fine
    assert {k: res["spec"]["terminal_length"][k] for k in ("min", "nom", "max")} == {"min": 0.3, "nom": 0.4, "max": 0.5}


# ---- DS2-14: a pin number printed as a circled digit ('①', ROHM / Toshiba) crashed the read with ValueError
def test_ds2_14_circled_digit_pin_number_is_a_review_line_not_a_crash():
    from ecadlib import crosscheck as CC
    for num in ("①", "1²"):                          # str.isdigit() is True for both, int() reads neither
        def mut(w, d, num=num):
            w["pins"][0]["num"] = num
        res, (kind, _) = _review(mut)
        assert f"symbol: non-numeric pin numbers [{num!r}] (footprint pads are 1..N, exposed pad N+1)" \
            in res["needs_review"], res["needs_review"]
        assert kind != "ok", kind
    diff = CC.compare_pins([("1", "VIN"), ("2", "GND")], [("①", "VIN"), ("2", "GND")])   # the cross-check's second read
    assert diff[0]["field"] == "pins" and "only here: ①" in diff[0]["second"], diff


# ---- DS2-15: a batch answer for a part not in the collecting run was cached with page_offset 0 and url ''
@lite
def test_ds2_15_batch_answer_for_a_part_not_in_this_run_keeps_its_pages(tmp_path):
    from tests import test_datasheet_batch as TB, test_ds_route as T
    pdfs = {"https://a/ldo.pdf": T.pdf_lines(T.ldo_pages())}
    api = T.ModelAPI(lambda m, n: T.on_page(T.GOOD, 4))           # every value cited on page 4 of the pages sent
    a, b = ("TESTLDO-H33TRG1", "SOT-223-3_TabPin2"), ("TESTLDO-R33TRG1", "SOT-89-3")
    src = [("https://a/ldo.pdf", "BOM")]
    with TB.patched(api):
        rd = T.reader(tmp_path, pdfs, batch=True, wait=False)       # run 1: A and B submitted (--no-wait)
        for part, pkg in (a, b):
            rd.submit(rd.job(part, src, pkg))
        rd.flush()
        rd = T.reader(tmp_path, pdfs, batch=True, wait=True)        # run 2: only A (--part A) collects the batch
        rd.submit(rd.job(a[0], src, a[1]))
        rd.flush()
        rd = T.reader(tmp_path, pdfs)                               # run 3: B, answered from the cache
        jb = rd.job(b[0], src, b[1])
        assert jb["offset"][3] == 14, jb["offset"]                  # page 4 sent = original page 14 (B's SOT89 drawing)
        res = rd.cached(jb)
    assert len(api.of("/v1/messages/batches")) == 1                 # B was paid for once, in run 1
    assert res["sidecar"]["pin_table"]["page"] == 14, res["sidecar"]["pin_table"]
    assert rd.results.get(jb["key"])["url"] == "https://a/ldo.pdf"


# ---- DS2-19: with [ai] provider = openai, counting tokens for a Claude rung sent the OpenAI key to api.anthropic.com
@lite
def test_ds2_19_count_tokens_for_a_claude_rung_never_carries_another_providers_key(tmp_path, monkeypatch):
    from ecadlib import userconfig as UC
    from tests import test_datasheet_batch as TB
    openai_key, claude_key = "sk-proj-OPENAI-test-111", "sk-ant-CLAUDE-test-222"
    rules = {"ai": {"provider": "openai", "model": "gpt-6-luna", "detect": False,
                    "endpoints": {"claude": {"provider": "anthropic"}}},
             "datasheet": {"route": "ladder", "ladder": ["gpt-6-luna", "claude:claude-opus-5-5"], "max_cost_usd": 5}}
    _, body = D.build_request(("base64", b"%PDF-1.4 fake"), "BGA-PART", "BGA-64", None, "claude:claude-opus-5-5")
    job = {"name": "BGA-PART", "model": "claude:claude-opus-5-5", "body": body, "url": "x.pdf",
           "prov": {"pages": 5, "sent_as": "base64"}}
    for stored, want in (({"OPENAI_API_KEY": openai_key, "ANTHROPIC_API_KEY": claude_key}, [claude_key]),
                         ({"OPENAI_API_KEY": openai_key}, [])):          # no Claude key: a rough estimate, nothing sent
        monkeypatch.setattr(UC, "secret", lambda name, env=None, s=stored: s.get(name))
        api = TB.FakeAPI()
        with TB.patched(api):
            rd = D.Reader(tmp_path / "out", tmp_path / "cache", rules=rules, log=None)
            rd.estimate([job])
        assert [c[2]["x-api-key"] for c in api.of("/count_tokens")] == want, api.calls
