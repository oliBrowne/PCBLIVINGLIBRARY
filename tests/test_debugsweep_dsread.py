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
def _ds2_15(d, between=None, lose_create=0):
    """Run 1 submits parts A and B (--no-wait; with lose_create its create answer is lost, so run 2 adopts the batch
    from the listing), `between(batch_state)` edits the state file, run 2 collects with only A, run 3 reads B from the
    cache. -> (B's result, B's cached record, batch create calls)."""
    import json
    from tests import test_datasheet_batch as TB, test_ds_route as T

    class API(TB.ListingAPI, T.ModelAPI):                           # answers by pages sent; a create can lose its answer
        pass
    pdfs = {"https://a/ldo.pdf": T.pdf_lines(T.ldo_pages())}
    api = API(lose_create=lose_create, answer=lambda m, n: T.on_page(T.GOOD, 4))   # every value cited on page 4 sent
    a, b = ("TESTLDO-H33TRG1", "SOT-223-3_TabPin2"), ("TESTLDO-R33TRG1", "SOT-89-3")
    src = [("https://a/ldo.pdf", "BOM")]
    with TB.patched(api):
        rd = T.reader(d, pdfs, batch=True, wait=False)              # run 1: A and B submitted (--no-wait)
        for part, pkg in (a, b):
            rd.submit(rd.job(part, src, pkg))
        try:
            rd.flush()
        except D.APIError as e:
            assert lose_create and "may have been created" in str(e), e
        if between:
            f = rd.dir / "batch_state.json"
            st = json.loads(f.read_text(encoding="utf-8"))
            between(st)
            f.write_text(json.dumps(st), encoding="utf-8")
        rd = T.reader(d, pdfs, batch=True, wait=True)               # run 2: only A (--part A) collects the batch
        rd.submit(rd.job(a[0], src, a[1]))
        rd.flush()
        rd = T.reader(d, pdfs)                                      # run 3: B, answered from the cache
        jb = rd.job(b[0], src, b[1])
        assert jb["offset"][3] == 14, jb["offset"]                  # page 4 sent = original page 14 (B's SOT89 drawing)
        res = rd.cached(jb)
    return res, rd.results.get(jb["key"]), len(api.of("/v1/messages/batches"))


@lite
def test_ds2_15_batch_answer_for_a_part_not_in_this_run_keeps_its_pages(tmp_path):
    res, rec, creates = _ds2_15(tmp_path / "new")
    assert creates == 1                                             # B was paid for once, in run 1
    assert res["sidecar"]["pin_table"]["page"] == 14, res["sidecar"]["pin_table"]
    assert rec["url"] == "https://a/ldo.pdf"

    def before_the_fix(st):                                         # a batch_state.json written before 'sent' existed
        for bt in st["batches"]:
            bt.pop("sent", None)
    res, rec, creates = _ds2_15(tmp_path / "old", before_the_fix)
    assert creates == 1 and rec["page_offset"] == 0, rec["page_offset"]   # cached by the collecting run without its pages
    assert res["sidecar"]["pin_table"]["page"] == 14, res["sidecar"]["pin_table"]   # the job's own pages still hold

    res, rec, creates = _ds2_15(tmp_path / "lost", lose_create=1)   # the create's answer lost: run 2 adopts the batch
    assert creates == 1                                             # found in the listing, never sent again
    assert res["sidecar"]["pin_table"]["page"] == 14, res["sidecar"]["pin_table"]
    assert rec["url"] == "https://a/ldo.pdf" and rec["page_offset"][3] == 14, rec


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


# ---- DS2-20: `ecadlib datasheet PDF --part X --estimate` ignored the rules and uploaded the PDF to Anthropic
@lite
def test_ds2_20_single_part_estimate_follows_the_configured_provider(tmp_path, monkeypatch):
    from ecadlib import userconfig as UC
    from tests import test_datasheet_batch as TB
    stored = {"OPENAI_API_KEY": "sk-proj-OPENAI-test-111", "ANTHROPIC_API_KEY": "sk-ant-CLAUDE-test-222"}
    monkeypatch.setattr(UC, "secret", lambda name, env=None: stored.get(name))
    (tmp_path / "ecadlib.toml").write_text('[ai]\nprovider = "openai"\nmodel = "gpt-6-sol"\ndetect = false\n',
                                           encoding="utf-8")
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(TB.pdf_with(["TPS1 regulator", "Package outline", "Pin functions"]))
    api = TB.FakeAPI()
    with TB.patched(api):                             # ecadlib.toml is found from -o upwards, as on the command line
        rc = D.main([str(pdf), "--part", "TPS1", "--estimate", "-o", str(tmp_path / "out")])
    assert rc == 0
    assert api.calls == [], [(c[1], (c[2] or {}).get("x-api-key")) for c in api.calls]   # nothing sent to Anthropic


def _sot223(pitch, name="SOT-223", tab="4", pin_count=4):
    """A correct SOT-223 read (AMS1117 style: 3 leads at e, the tab numbered 4 as KiCad's SOT-223 has it, or 2 as
    SOT-223-3_TabPin2 has it: then the pin table has 3 rows)."""
    import json
    d = lambda nm, lab, mn, nom, mx: {"name": nm, "label": lab, "min": mn, "nom": nom, "max": mx, "unit": "mm", "page": 9,
                                      "figure": "SOT-223"}
    wire = {"package": {"name": name, "description": "SOT-223", "package_type": "gullwing", "drawing": "SOT-223",
                        "unresolved": [],
                        "dims": [d("body_length", "D", "6.30", "6.50", "6.70"), d("body_width", "E1", "3.30", "3.50", "3.70"),
                                 d("lead_span", "E", "6.70", "7.00", "7.30"), d("pitch", "e", "", pitch, ""),
                                 d("terminal_length", "L", "0.90", "1.00", "1.10"),
                                 d("terminal_width", "b", "0.66", "0.75", "0.84"), d("height_max", "A", "", "", "1.80")],
                        "other_json": json.dumps({"pin_count": pin_count,
                                                  "pin_positions": [{"number": "1", "side": "L", "offset": -2.3},
                                                                    {"number": "2", "side": "L", "offset": 0},
                                                                    {"number": "3", "side": "L", "offset": 2.3}],
                                                  "tab": {"number": tab, "side": "R", "offset": 0,
                                                          "width": {"min": 2.9, "nom": 3.0, "max": 3.1}}})},
            "pin_table": {"page": 3, "figure": "Pin functions", "rows": 3 if tab == "2" else 4},
            "pins": [{"num": n, "name": nm, "type": "power", "side": s, "page": 3} for n, nm, s in
                     [("1", "GND", "left"), ("2", "VOUT", "right"), ("3", "VIN", "left"), ("4", "VOUT", "right")][:3 if tab == "2" else 4]],
            "symbol_description": "LDO", "notes": [], "embedded_instructions": []}
    return D.process(D.parse_message({"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(wire)}]}),
                     "AMS1117-3.3", "ds.pdf")


# ---- DS2-24: a SOT-223-3_TabPin2 read (tab = pin 2) was held whatever pin_count it gave: 4 wanted a pin 4 in the
# table, 3 (signal pins, as package_schema.json defines pin_count) was refused by footprint_gen ("a tab counts")
def test_ds2_24_sot223_tab_on_pin2_reads_with_the_schemas_pin_count():
    from ecadlib import crosscheck as CC, ds_route as DR, footprint_gen as F
    from tests.test_footprints import SPEC
    res = _sot223("2.30", name="SOT-223-3_TabPin2", tab="2", pin_count=3)   # 3 signal pins, the tab is pin 2 again
    assert DR.assess(res) == ("ok", []), (DR.assess(res), res["needs_review"])
    spec = SPEC("SOT-223-3_TabPin2")                  # the bundled spec counts as the standard table does: agreed
    assert CC.against_table(CC.critical(spec), "SOT-223-3_TabPin2", "x")["status"] == "agreed"
    _, rep = F.build(spec)
    assert sorted(p["num"] for p in rep["pads"]) == ["1", "2", "2", "3"], rep["pads"]
    F.build(dict(spec, pin_count=4))                  # a spec file written when the generator said "a tab counts"
    try:
        F.build(dict(spec, pin_count=5))
        raise AssertionError("pin_count 5 built for 3 pins and a tab")
    except ValueError as e:
        assert "pin_count is 5" in str(e), e


# ---- DS2-21: the 2.30 mm pitch every SOT-223 drawing prints (JEDEC TO-261AA, e = 2.30 BSC) failed the self-check
def test_ds2_21_sot223_pitch_2_30_is_a_standard_pitch():
    from ecadlib import ds_route as DR
    res = _sot223("2.30")
    assert not [r for r in res["needs_review"] if "not a standard pitch" in r], res["needs_review"]
    assert DR.assess(res)[0] == "ok", DR.assess(res)
    review = []                                       # a pitch nothing uses is still questioned
    D.self_check({"pitch": 2.2}, {"pins": []}, review)
    assert review == ["self-check: pitch 2.2 mm is not a standard pitch (0.3, 0.35, 0.4, 0.5, 0.635, 0.65, 0.8, 0.95, "
                      "1.0, 1.1, 1.25, 1.27 ...): unit or reading error?"], review


# ---- DS2-22: a max_cost_usd the person set to 10 (the value docs/api-keys.md shows) was taken for the default cap
@lite
def test_ds2_22_a_cap_set_to_ten_holds_like_any_cap_you_set(tmp_path, monkeypatch):
    import requests
    from ecadlib import ai_cost as AC, rules as RU, userconfig as UC
    from tests import test_datasheet_batch as TB
    monkeypatch.setattr(UC, "secret", lambda name, env=None: {"ACME_KEY": "dummy-acme"}.get(name))
    sent = []

    def offline(url, *a, **k):                        # nothing leaves this test: a request is recorded and fails
        sent.append(url)
        raise requests.ConnectionError("offline test")
    monkeypatch.setattr(requests, "post", offline)
    monkeypatch.setattr(requests, "get", offline)
    gateway = ('[ai]\nprovider = "openai-compatible"\nbase_url = "https://llm-gateway.example.com/v1"\n'
               'model = "acme-pro-2"\napi_key_env = "ACME_KEY"\ndetect = false\n')   # a model with no known price
    (tmp_path / "unset.toml").write_text(gateway, encoding="utf-8")
    (tmp_path / "set.toml").write_text(gateway + "\n[datasheet]\nmax_cost_usd = 10\n", encoding="utf-8")
    said = []

    def guard(toml):
        rules = RU.load(str(tmp_path / toml))
        rd = D.Reader(tmp_path / "out", tmp_path / "cache", rules=rules, log=said.append)
        _, body = D.build_request(("base64", b"%PDF-1.4"), "U1", "SOIC-8", None, rd.model)
        rd._guard_cost([{"key": "k", "name": "U1", "model": rd.model, "body": body, "url": "u.pdf", "sync": True,
                         "prov": {"pages": 40, "sent_as": "base64"}}])
        return rules

    rules = guard("unset.toml")                       # no cap in the file: the default one can't stop it, said aloud
    assert "the default $10 cap can't be checked" in said[0] and AC.limit(rules) == (1.0, "ask_above_usd"), said
    try:
        guard("set.toml")
        raise AssertionError("max_cost_usd = 10 in the file did not hold an unpriced model")
    except D.DatasheetError as e:
        assert "no price known for acme-pro-2" in str(e) and "max_cost_usd = 10" in str(e), e
    assert AC.limit(RU.load(str(tmp_path / "set.toml"))) == (10.0, "max_cost_usd")   # the dialogs ask twice above it
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(TB.pdf_with(["U1 regulator", "Package outline"]))
    try:                                              # --max-cost-usd 10 on the command line is a cap you set too
        D.main([str(pdf), "--part", "U1", "--rules", str(tmp_path / "unset.toml"), "--max-cost-usd", "10",
                "-o", str(tmp_path / "cli")])
        raise AssertionError("--max-cost-usd 10 did not hold an unpriced model")
    except SystemExit as e:
        assert "no price known for acme-pro-2" in str(e.code), e.code
    assert sent == [], sent
    from ecadlib.eval import runner as R                # `ecadlib eval run --max-cost-usd 10` likewise
    from tests import test_eval as TE
    seen, real = [], R.EvalReader.__init__

    def spy(self, *a, **k):
        real(self, *a, **k)
        seen.append(self.cap_set)
    monkeypatch.setattr(R.EvalReader, "__init__", spy)
    TE.run(str(tmp_path), "eval", max_cost_usd=10.0)
    assert seen and all(seen), seen
