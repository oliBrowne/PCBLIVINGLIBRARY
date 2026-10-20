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

    def inch_column_noted(w, d):                      # read in mm; the note quotes the inch column of a dual-unit drawing
        d["terminal_length"].update(min="0.30", nom="0.40", max="0.50")
        w["package"]["unresolved"] = ["terminal_length (printed as 0.016±0.004 in)"]
    res, (kind, _) = _review(inch_column_noted)       # never merged, so nothing to reject: the note is left for review
    assert {k: res["spec"]["terminal_length"][k] for k in ("min", "nom", "max")} == {"min": 0.3, "nom": 0.4, "max": 0.5}
    assert res["spec"]["unresolved"] == ["terminal_length (printed as 0.016±0.004 in)"] and kind == "incomplete", kind

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


def test_ds2_14_circled_digits_in_names_and_pad_numbers_elsewhere_do_not_crash():
    from ecadlib import footprint_check as FC, footprint_gen as F, resolve as RS, symbol_layout as SL, visual_check as V
    from tests.test_footprints import SPEC
    review = []                                       # self_check runs on every read: its name is the model's text
    D.self_check({"name": "SOT-23-⑤", "package_type": "gullwing", "pin_count": 5}, {"pins": []}, review)
    assert D.name_facts("SOT-23-⑤")["count"] is None and D.name_facts("SOT-23-5")["count"] == 5
    for key in (SL._natural, V._natural, RS._nat):    # pin and candidate sort keys
        assert sorted(["10", "①", "2", "1"], key=key) == ["1", "2", "10", "①"], key.__module__
    text = F.build(SPEC("SOIC-8_3.9x4.9mm_P1.27mm"))[0].replace('(pad "8"', '(pad "⑧"')
    assert '(pad "⑧"' in text
    FC.check(text)                                    # numbering direction, pad ranges
    f = V.facts(V.parse_footprint(text))              # direction from the 7 numeric pads; ⑧ is still a pad
    assert (f["pads"], f["numbering"]) == (8, "counter-clockwise (top view)"), f
    assert FC._ranges(["1", "2", "3", "⑧"]) == "1-3, ⑧"


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
        assert [c[3]["model"] for c in api.of("/count_tokens")] == ["claude-opus-5-5"] * len(want)   # the API's id
    stored = {"OPENAI_API_KEY": openai_key, "ANTHROPIC_API_KEY": claude_key}   # a batch read on the Claude rung
    monkeypatch.setattr(UC, "secret", lambda name, env=None: stored.get(name))
    api = TB.FakeAPI(polls=0)
    with TB.patched(api):
        rd = D.Reader(tmp_path / "out", tmp_path / "cache", rules=dict(rules, datasheet=dict(
            rules["datasheet"], ladder=["claude:claude-opus-5-5"], cross_check="off")), log=None, batch=True, wait=True,
            http=TB.PdfHTTP({"https://a/ds.pdf": TB.PDF}), poll=0, sleep=lambda s: None, yes=True)   # (unpriced endpoint)
        rd.submit(rd.job("TESTPART", [("https://a/ds.pdf", "BOM")], "RGT"))
        rd.flush()
    batch = [c for c in api.calls if "/v1/messages/batches" in c[1]]      # create, poll, results
    assert len(batch) >= 3 and {c[2]["x-api-key"] for c in batch} == {claude_key}, [(c[0], c[1]) for c in batch]


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
    import contextlib, io
    api, said = TB.FakeAPI(), io.StringIO()
    with TB.patched(api), contextlib.redirect_stdout(said):   # ecadlib.toml is found from -o upwards, as on the command line
        rc = D.main([str(pdf), "--part", "TPS1", "--estimate", "-o", str(tmp_path / "out")])
    assert rc == 0
    assert api.calls == [], [(c[1], (c[2] or {}).get("x-api-key")) for c in api.calls]   # nothing sent to Anthropic
    assert "counted by count_tokens" not in said.getvalue() and "(rough: 3 pages" in said.getvalue(), said.getvalue()

    broken = tmp_path / "broken"                      # a typo in ecadlib.toml: the provider it names is unknown here
    broken.mkdir()
    (broken / "ecadlib.toml").write_text('[ai\nprovider = "openai"\n', encoding="utf-8")
    api, said = TB.FakeAPI(), io.StringIO()
    with TB.patched(api), contextlib.redirect_stdout(said):
        rc = D.main([str(pdf), "--part", "TPS1", "--estimate", "-o", str(broken / "out")])
    assert rc == 0 and api.calls == [], [(c[1], (c[2] or {}).get("x-api-key")) for c in api.calls]   # rough, sent nowhere
    assert "ecadlib.toml" in said.getvalue() and "rough" in said.getvalue(), said.getvalue()

    from ecadlib import app as A                      # the web UI's estimate: an OpenAI key stored is a key set
    ws = A.Workspace(tmp_path / "ws", [], providers=["digikey"])
    (ws.root / "ecadlib.toml").write_text((tmp_path / "ecadlib.toml").read_text(encoding="utf-8"), encoding="utf-8")
    bom = ws.save_upload("bom.csv", b"Designator,MPN\nU1,TPS1\n")
    ws._librarian_dir(ws._bom_path(bom)).mkdir(parents=True, exist_ok=True)
    (ws._librarian_dir(ws._bom_path(bom)) / "plan.json").write_text("{}", encoding="utf-8")
    _, body = D.build_request(("base64", pdf.read_bytes()), "TPS1", None, None, "gpt-6-sol")
    monkeypatch.setattr(D, "estimate_plan", lambda plan_f, rd, *a: rd.estimate(
        [{"name": "TPS1", "model": rd.model, "body": body, "url": str(pdf), "prov": {"pages": 3, "sent_as": "base64"}}]))
    est = ws.datasheet_estimate(bom)
    assert est["key_set"] and est["ai"]["key_set"] and not est["exact_input"], est


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


# body_length (along the row), body_width, lead_span, terminal_length, terminal_width, tab width (mm) and height_max
DPAK = (("6.35", "6.54", "6.73"), ("5.97", "6.10", "6.22"), ("9.40", "9.90", "10.41"), ("1.40", "1.59", "1.78"),
        ("0.63", "0.76", "0.89"), 5.2, "2.38")
D2PAK = (("10.00", "10.20", "10.40"), ("8.60", "9.00", "9.40"), ("14.60", "15.25", "15.90"), ("1.80", "2.30", "2.80"),
         ("0.66", "0.78", "0.91"), 7.0, "4.70")


def _tab_read(name, pitch, leads, tab, pin_count, size=DPAK):
    """A correct DPAK / D2PAK style read: the leads numbered `leads` in one row at `pitch` (a missing number is a cut
    lead: TO-252-2 has leads 1 and 3), the tab on the other side numbered `tab`; the pin table lists each number once."""
    import json
    d = lambda nm, lab, v: {"name": nm, "label": lab, "min": v[0], "nom": v[1], "max": v[2], "unit": "mm", "page": 9,
                            "figure": name}
    bl, bw, ls, tl, tw, w, h = size
    mid = (1 + int(leads[-1])) / 2
    wire = {"package": {"name": name, "description": name, "package_type": "gullwing", "drawing": name, "unresolved": [],
                        "dims": [d("body_length", "E", bl), d("body_width", "D", bw), d("lead_span", "H", ls),
                                 d("pitch", "e", ("", pitch, "")), d("terminal_length", "L", tl),
                                 d("terminal_width", "b", tw), d("height_max", "A", ("", "", h))],
                        "other_json": json.dumps({"pin_count": pin_count,
                                                  "pin_positions": [{"number": n, "side": "L", "offset": round(
                                                      (int(n) - mid) * float(pitch), 3)} for n in leads],
                                                  "tab": {"number": tab, "side": "R", "offset": 0,
                                                          "width": {"min": w - 0.2, "nom": w, "max": w + 0.2}}})},
            "pin_table": {"page": 3, "figure": "Pin functions", "rows": len(set(leads) | {tab})},
            "pins": [{"num": n, "name": f"P{n}", "type": "power", "side": "left", "page": 3}
                     for n in sorted(set(leads) | {tab}, key=int)],
            "symbol_description": "regulator", "notes": [], "embedded_instructions": []}
    return D.process(D.parse_message({"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(wire)}]}),
                     "LM2940", "ds.pdf")


# ---- DS2-24 (review): KiCad's names count the leads without the tab (TO-252-2: leads 1 and 3 + tab 2), so the
# self-check held a read with the schema's pin_count (3), and footprint_gen refuses 2: such a read could never pass
def test_ds2_24_a_name_that_counts_the_leads_without_the_tab_passes_the_self_check():
    from ecadlib import ds_route as DR
    for name, leads, tab, n in (("TO-252-2", ["1", "3"], "2", 3), ("TO-252-3_TabPin4", ["1", "2", "3"], "4", 4)):
        res = _tab_read(name, "2.29", leads, tab, n)
        assert DR.assess(res) == ("ok", []), (name, DR.assess(res), res["needs_review"])
    res = _tab_read("TO-252-2", "2.29", ["1", "2", "3"], "4", 4)   # 3 leads: not what the name says
    assert "self-check: name TO-252-2 says 2 pins, pin_count is 4" in res["needs_review"], res["needs_review"]


# ---- DS2-24 (review): the row-fill rule took pin_count // 2 per row for every gull-wing, so a part with all its leads
# in one row and the tab on the other (TO-263-5_TabPin3, TO-263-7_TabPin4) always "filled under 60 % of the body"
def test_ds2_24_a_single_row_of_leads_beside_a_tab_fills_the_body():
    from ecadlib import ds_route as DR
    for name, pitch, k, tab in (("TO-263-5_TabPin3", "1.70", 5, "3"), ("TO-263-7_TabPin4", "1.27", 7, "4")):
        res = _tab_read(name, pitch, [str(i) for i in range(1, k + 1)], tab, k, D2PAK)
        assert DR.assess(res) == ("ok", []), (name, DR.assess(res), res["needs_review"])
    res = _tab_read("TO-263-2", "2.54", ["1", "3"], "2", 3, D2PAK)   # a cut lead: the row still spans 3 pitches
    assert DR.assess(res) == ("ok", []), ("TO-263-2", DR.assess(res), res["needs_review"])
    review = []                                       # a listed row that is short for the body is still questioned
    D.sanity({"package_type": "gullwing", "pin_count": 5, "pitch": 0.65, "body_length": {"min": 10.0, "nom": 10.2, "max": 10.4},
              "pin_positions": [{"number": str(i), "side": "L", "offset": (i - 3) * 0.65} for i in range(1, 6)],
              "tab": {"number": "3", "side": "R"}}, review)
    assert review == ["pin rows: 5 pins x pitch 0.65 = 3.25 mm, under 60% of body_length 10.2 mm"], review


# ---- DS2-24 (review): a blank pin_count was filled from the name's first number, the outline of a SOT / TO name
# (SOT-223 -> 223, TO-252-2 -> 252), so a tab part's was never filled, or filled with nonsense
def test_ds2_24_a_blank_pin_count_of_a_tab_part_comes_from_its_name_and_pin_table():
    from ecadlib import ds_route as DR
    for res, n in ((_sot223("2.30", pin_count=None), 4), (_tab_read("TO-252-2", "2.29", ["1", "3"], "2", None), 3)):
        assert res["spec"].get("pin_count") == n, (res["spec"].get("pin_count"), res["needs_review"])
        assert DR.assess(res) == ("ok", []), (DR.assess(res), res["needs_review"])
    assert D._pin_count_from({"name": "SOT-223"}, {"pins": []}) == (None, "")   # SOT-223's name has no pin count
    tab2 = {"name": "SOT-223-3_TabPin2", "tab": {"number": "2"}, "pin_positions": [{"number": n} for n in "123"]}
    assert D._pin_count_from(tab2, {"pins": [{"num": n} for n in "1234"]}) == (None, "")   # the tab is pin 2: 4 isn't it


# ---- DS2-24 (review): counting each pad number once let a SOT-223 read without pin_positions (pitch 2.30, pin_count
# 4, pins 1-4, tab 2) build the standard dual row with the tab over leads 3 and 4, and the read passed
def test_ds2_24_a_tab_that_repeats_a_lead_number_needs_pin_positions():
    import copy
    from ecadlib import ds_route as DR, footprint_gen as F
    from tests.test_footprints import SPEC
    spec = dict(SPEC("SOT-223-3_TabPin2"), pitch=2.3, pin_count=4, pin_positions=None)
    try:
        F.build(spec)
        raise AssertionError("built tab 2 over the other row's leads")
    except ValueError as e:
        assert "a tab that repeats a lead number needs pin_positions" in str(e) and " but " not in str(e), e
    ext = copy.deepcopy(_sot223("2.30")["sidecar"]["extraction"])
    ext["package"].update(pin_positions=None, tab=dict(ext["package"]["tab"], number="2"))
    res = D.process(ext, "AMS1117-3.3", "ds.pdf")
    assert DR.assess(res)[0] == "failed", (DR.assess(res), res["needs_review"])
    assert any(r.startswith("footprint_gen.build failed") for r in res["needs_review"]), res["needs_review"]
    ext = copy.deepcopy(_sot223("2.30")["sidecar"]["extraction"])
    ext["package"]["pin_positions"][1]["side"] = "R"   # lead 2 read on the tab's side: it builds, under tab 4
    res = D.process(ext, "AMS1117-3.3", "ds.pdf")
    assert DR.assess(res) == ("failed", ["footprint_gen.build failed: the pads would short: pads 2 and 4 overlap by "
                                         "2.025 mm (check pitch, pin_count and terminal_length against the drawing)"]), \
        (DR.assess(res), res["needs_review"])


# ---- DS2-24 (review): the second read is asked for "terminals" (SOT-223 is commonly a 4-terminal part), so it may count
# the tab that is pin 2 again: the cross-check held a SOT-223-3_TabPin2 both reads got right. That text is part of
# every cached answer's key (D.schema_version(), CC.version()), so compare() allows it rather than the prompt changing
def test_ds2_24_a_second_read_counting_a_tab_that_repeats_a_lead_agrees():
    import json
    from ecadlib import crosscheck as CC
    d = lambda nm, lab, mn, nom, mx: {"name": nm, "label": lab, "min": mn, "nom": nom, "max": mx, "unit": "mm"}

    def second(n):
        a = {"package": {"name": "SOT-223", "package_type": "gullwing", "pin_count": n, "unresolved": [],
                         "dims": [d("pitch", "e", "", "2.30", ""), d("body_length", "D", "6.30", "6.50", "6.70"),
                                  d("body_width", "E1", "3.30", "3.50", "3.70"), d("lead_span", "E", "6.70", "7.00", "7.30")]},
             "pins": [{"num": n, "name": nm} for n, nm in (("1", "GND"), ("2", "VOUT"), ("3", "VIN"))],
             "embedded_instructions": []}
        return CC.parse({"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(a)}]})[0]
    first = CC.critical(_sot223("2.30", name="SOT-223-3_TabPin2", tab="2", pin_count=3)["spec"])
    assert CC.compare(first, second(4))["status"] == "agreed", CC.compare(first, second(4))
    assert CC.compare(first, second(3))["status"] == "agreed"
    for n in (2, 5):                                  # any other count still holds
        assert CC.compare(first, second(n))["disagreed"] == [{"field": "pin_count", "first": "3", "second": str(n)}]
    kicad = CC.critical(_sot223("2.30")["spec"])      # tab 4 has its own number: 4 is every count there is
    assert CC.compare(kicad, second(5))["status"] == "disagreed"


# ---- DS2-24 (review fixes): a tab or pin_positions a model gives in another shape ("4", 5) is the schema check's to
# report; the tab rules above must not crash the read on it
def test_ds2_24_a_tab_or_pin_positions_in_another_shape_is_a_review_line_not_a_crash():
    import copy
    from ecadlib import crosscheck as CC
    base = _sot223("2.30")["sidecar"]["extraction"]
    for tab, pos in (("4", None), (7, [1, "x"]), ({"number": "2"}, 5), ("4", "1 L, 2 L")):
        for n in (None, 4):
            ext = copy.deepcopy(base)
            ext["package"].update(tab=tab, pin_positions=pos, pin_count=n)
            res = D.process(ext, "AMS1117-3.3", "ds.pdf")
            CC.critical(res["spec"])
            assert any(r.startswith("schema:") for r in res["needs_review"]), (tab, pos, n, res["needs_review"])


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


@lite
def test_ds2_22_ten_dollars_approved_in_the_web_ui_is_a_cap_you_set(tmp_path, monkeypatch):
    from ecadlib import app as A, userconfig as UC
    from tests import test_datasheet_batch as TB
    monkeypatch.setattr(UC, "secret", lambda name, env=None: {"OPENAI_API_KEY": "sk-proj-OPENAI-test-111"}.get(name))
    ws = A.Workspace(tmp_path / "ws", [], providers=["digikey"])
    (ws.root / "ecadlib.toml").write_text('[ai]\nprovider = "openai"\nmodel = "gpt-6-sol"\ndetect = false\n', encoding="utf-8")
    bom = ws.save_upload("bom.csv", b"Designator,MPN\nU1,TPS1\n")
    out = ws._librarian_dir(ws._bom_path(bom))
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text("[]", encoding="utf-8")
    _, body = D.build_request(("base64", TB.PDF), "TPS1", None, None, "gpt-6-sol")
    monkeypatch.setattr(D, "estimate_plan", lambda plan_f, rd, *a: rd.estimate(           # about $0.11, at most $0.21
        [{"name": "TPS1", "model": rd.model, "body": body, "url": "ds.pdf", "prov": {"pages": 3, "sent_as": "base64"}}]))
    seen = []

    class Read(Exception):
        pass

    def fill_plan(plan_f, rd, *a, **k):               # the Reader the reads are held by, as the person approved them
        seen.append((rd.cfg["max_cost_usd"], rd.cap_set))
        raise Read
    monkeypatch.setattr(D, "fill_plan", fill_plan)
    for read in (lambda: ws.ai_read(bom, approved_usd=10.0), lambda: ws.datasheet_batch(bom, approved_usd=10.0)):
        try:
            read()
            raise AssertionError("not read")
        except Read:
            pass
    assert seen == [(10.0, True), (10.0, True)], seen   # $10.00 approved holds like $9.99: not the default cap


# ---- DS2-12 (the cross-check's second read): "0.016 in" in an mm record was read as 0.016 mm
def test_ds2_12_second_read_value_in_another_unit_is_not_compared_as_the_records_unit():
    import json
    from ecadlib import crosscheck as CC

    def second(nom):
        dim = {"name": "pitch", "label": "e", "min": "", "nom": nom, "max": "", "unit": "mm", "page": 1}
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(
            {"package": {"name": "SOIC-8", "package_type": "gullwing", "pin_count": 8, "dims": [dim]}, "pins": []})}]}
    assert CC.parse(second("1.27 mm"))[0]["pitch"] == (1.27, 1.27, 1.27)    # the record's own unit written: fine
    try:
        CC.parse(second("0.050 in"))
        raise AssertionError("0.050 in compared as 0.05 mm")
    except CC.Unavailable as e:                      # unchecked, said so: not a false disagreement
        assert "pitch.nom" in str(e) and "'in'" in str(e) and "'mm'" in str(e), e
