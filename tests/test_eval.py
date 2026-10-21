"""Offline tests for ecadlib.eval: the shipped test set, the scorer, `ecadlib eval run` (replay / record / estimate),
`compare` and the set builder.  Run:  python3 -m tests.test_eval

References are KiCad numbers: the shipped set (ecadlib/eval/sets/kicad-v1.json) and footprint_bench's fixture. Claude's
answers are the SYNTHETIC extractions of tests/test_datasheet in the documented Messages API shape, so nothing here
says how accurate a real model is: these tests prove the harness scores what it is given."""
import copy, hashlib, json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import cli, datasheet as D, footprint_bench as FB, footprint_gen as F
from ecadlib.eval import build_set as B, runner as R, score as S
from tests import test_datasheet as TD, test_datasheet_batch as TB, test_librarian as TL

BENCH = json.loads((Path(__file__).parent / "fixtures" / "kicad_bench.json").read_text())
SOIC = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
VQFN = "Package_DFN_QFN:VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm"


def qfn_ext(ep=1.68, pitch=0.5):
    e = copy.deepcopy(TD.GOOD)
    e["package"]["name"] = f"VQFN-16-1EP_3x3mm_P{pitch}mm_EP{ep}x{ep}mm"
    e["package"]["pitch"] = TD.dim("e", None, pitch, None)
    e["package"]["exposed_pad"] = {"length": TD.dim("D2", None, ep, None), "width": TD.dim("E2", None, ep, None)}
    return e


def mini_set(tmp):
    """A two-case set with KiCad's own numbers (footprint_bench fixture) as references."""
    cases = [
        {"id": "soic-SOICPART", "family": "soic", "mpn": "SOICPART", "manufacturer": "ACME", "package": "SOIC-8", "footprint": SOIC,
         "datasheet": "https://example.com/soic.pdf", "description": "test part", "pins": [[str(i), f"P{i}"] for i in range(1, 9)],
         "from": {"kind": "test"}},
        {"id": "qfn-QFNPART", "family": "qfn", "mpn": "QFNPART", "manufacturer": "ACME", "package": "VQFN-16-1EP", "footprint": VQFN,
         "datasheet": "https://example.com/qfn.pdf", "description": "test part",
         "pins": [[p["num"], p["name"]] for p in TD.GOOD["pins"]], "from": {"kind": "test"}}]
    refs = {SOIC: BENCH["SOIC-8_3.9x4.9mm_P1.27mm"], VQFN: BENCH["VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm"]}
    f = Path(tmp) / "mini.json"
    f.write_text(json.dumps({"meta": {"name": "mini", "version": 1}, "cases": cases, "references": refs}))
    return f


def record_dir(d, answers):
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    for part, ext in answers.items():
        (d / f"{part}.json").write_text(json.dumps({"part": part, "model": "claude-opus-5-5", "estimate": None,
                                                    "job": {"url": f"https://example.com/{part}.pdf", "where": "BOM", "offset": 0,
                                                            "prov": {"url": f"https://example.com/{part}.pdf", "sent_as": "base64"}},
                                                    "message": TD.message(ext)}))
    return d


def run(tmp, out, **kw):
    kw.setdefault("sources", ["easyeda", "datasheet"])     # the datasheet path these tests are about (not `standard`)
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with mock.patch.dict(os.environ, env, clear=True):
        return R.run(mini_set(tmp), Path(tmp) / out, cache=Path(tmp) / "cache", http=kw.pop("http", None) or TL.FakeHTTP(),
                     log=lambda *a: None, **kw)


# ---------------------------------------------------------------- shipped set
def test_shipped_set_is_complete_and_kicad_passes_against_itself():
    d = R.load_set("kicad-v1")
    cases = d["cases"]
    assert 300 <= len(cases) <= 400 and d["meta"]["kicad_footprints"] and d["meta"]["license"].startswith("Numbers only")
    fams = {c["family"] for c in cases}
    assert fams == set(B.QUOTA), fams
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["footprint"] in d["references"] and c["mpn"] and c["from"]["kind"] in ("kicad-symbol", "kicad-footprint", "curated"), c
        assert c["family"] == "chip" or c["family"] == "connector" or c["manufacturer"], c
        res = S.score(d["references"][c["footprint"]], d["references"][c["footprint"]], c["pins"], c["pins"])
        assert res["pass"] and not res["errors"], (c["id"], res["errors"])
    assert sum(bool(c["datasheet"]) for c in cases) >= 300


def test_footprint_gen_matches_kicad_on_the_scorer():
    """Our IPC generator against KiCad (the footprint_bench comparison, through the eval scorer's looser tolerances)."""
    for name in ("SOIC-8_3.9x4.9mm_P1.27mm", "VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm", "SOT-23-5", "R_0603_1608Metric"):
        txt, _ = F.build(json.loads(FB.spec_path(name).read_text()))
        res = S.score(FB.numbers(txt), BENCH[name])
        assert res["pass"], (name, res["errors"])


# ---------------------------------------------------------------- scorer catches real errors
def test_scorer_catches_wrong_pitch_mirrored_pin1_missing_ep():
    ref = BENCH["VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm"]
    good = S.score(ref, ref)
    assert good["pass"] and good["fields"]["pitch"]["ref"] == 0.5
    # wrong pitch: the same part built at 0.65 mm
    res = D.process(qfn_ext(pitch=0.65), "X", "u")
    txt, _ = F.build(res["spec"])
    bad = S.score(FB.numbers(txt), ref)
    assert not bad["pass"] and not bad["fields"]["pitch"]["ok"] and bad["fields"]["pitch"]["ours"] == 0.65, bad["errors"]
    # mirrored (pin 1 on the wrong side)
    mir = {"pads": [[p[0], -p[1], p[2], p[3], p[4], p[5]] for p in ref["pads"]], "courtyard": ref["courtyard"]}
    bad = S.score(mir, ref)
    assert not bad["pass"] and not bad["fields"]["pin1"]["ok"] and bad["fields"]["pitch"]["ok"], bad["errors"]
    assert any(e.startswith("pin1:") and "mirrored" in e for e in bad["errors"])
    # missing exposed pad
    noep = {"pads": [p for p in ref["pads"] if p[0] != "17"], "courtyard": ref["courtyard"]}
    bad = S.score(noep, ref)
    assert not bad["fields"]["pad_count"]["ok"] and "missing ['17']" in bad["fields"]["pad_count"]["error"], bad["errors"]
    # pad size, courtyard, origin (information only), no footprint at all
    big = {"pads": [[p[0], p[1] + 1, p[2], p[3] + 0.2, p[4], p[5]] for p in ref["pads"]],
           "courtyard": [ref["courtyard"][0] + 1, ref["courtyard"][1], ref["courtyard"][2] + 1.5, ref["courtyard"][3]]}
    res = S.score(big, ref)
    f = res["fields"]
    assert not f["pads"]["ok"] and not f["courtyard"]["ok"] and not f["origin"]["ok"] and f["pin1"]["ok"], res["errors"]
    moved = {"pads": [[p[0], p[1] + 1, p[2], p[3], p[4], p[5]] for p in ref["pads"]],
             "courtyard": [ref["courtyard"][0] + 1, ref["courtyard"][1], ref["courtyard"][2] + 1, ref["courtyard"][3]]}
    assert S.score(moved, ref)["pass"]                                       # another origin alone is not an error
    assert S.score(None, ref)["errors"] == ["footprint: no footprint"]


def test_thermal_vias_are_not_pads():
    ref = BENCH["QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm"]
    vias = BENCH["QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm_ThermalVias"]
    assert len(vias["pads"]) > len(ref["pads"])
    res = S.score(ref, vias)
    assert res["fields"]["pad_count"]["ok"], res["errors"]


def test_symbol_pins_and_names():
    ref = [("1", "~{RESET}"), ("2", "VDD"), ("3", "GND"), ("4", "PA0"), ("5", "PA1")]
    assert S.score_symbol([("1", "R\\E\\S\\E\\T\\"), ("2", "VDD"), ("3", "GND"), ("4", "PA0"), ("5", "PA1")], ref)["symbol_names"]["ok"]
    r = S.score_symbol([("1", "RESET"), ("2", "VCC"), ("3", "VSS"), ("4", "PB0"), ("5", "PA1")], ref)
    assert r["symbol_pins"]["ok"] and not r["symbol_names"]["ok"] and r["symbol_names"]["matched"] == 2
    r = S.score_symbol([("1", "A"), ("2", "B")], ref)
    assert not r["symbol_pins"]["ok"] and "missing ['3', '4', '5']" in r["symbol_pins"]["error"]
    assert "symbol_names" not in S.score_symbol([("1", ""), ("2", "")], [("1", "~"), ("2", "~")])   # R/C: numbers only
    assert S.score_symbol(None, ref)["symbol_pins"]["error"] == "no symbol"


# ---------------------------------------------------------------- run / replay / record / estimate / compare
def test_replay_runs_the_pipeline_and_scores():
    with tempfile.TemporaryDirectory() as d:
        rec = record_dir(Path(d) / "rec", {"SOICPART": TD.INCH, "QFNPART": qfn_ext()})
        with mock.patch.object(D.requests, "post", side_effect=AssertionError("replay must not call the API")):
            rep = run(d, "a", replay=rec)
        cs = {c["mpn"]: c for c in rep["cases"]}
        assert rep["meta"]["mode"] == "replay" and rep["meta"]["sources"] == ["easyeda", "datasheet"]
        assert cs["SOICPART"]["source"] == "datasheet" and cs["SOICPART"]["symbol_source"] == "datasheet"
        assert cs["SOICPART"]["pass"] and cs["QFNPART"]["pass"], (cs["SOICPART"]["errors"], cs["QFNPART"]["errors"])
        u = TD.message(TD.GOOD)["usage"]
        assert cs["QFNPART"]["tokens_in"] == u["input_tokens"] + u["cache_creation_input_tokens"] and cs["QFNPART"]["tokens_out"] == 2304
        want = (912 * 4 + 48210 * 4 * 1.25 + 2304 * 20) / 1e6
        assert abs(cs["QFNPART"]["usd"] - want) < 1e-6 and cs["QFNPART"]["cost_how"] == "recorded usage (replay)"
        s = rep["summary"]
        assert s["overall"]["pass"] == 2 and s["families"]["qfn"]["rate"] == 1.0 and s["by_source"]["datasheet"]["cases"] == 2
        md = (Path(d) / "a" / "report.md").read_text()
        assert "**Right: 2/2 (100.0 %)**" in md and "| qfn | 1 | 1 | 100.0 % | datasheet 1 |" in md
        # a wrong answer (0.65 mm pitch) and a missing recording: compare shows the flips
        rec2 = record_dir(Path(d) / "rec2", {"QFNPART": qfn_ext(pitch=0.65)})
        rep2 = run(d, "b", replay=rec2)
        cs2 = {c["mpn"]: c for c in rep2["cases"]}
        assert not cs2["QFNPART"]["pass"] and any(e.startswith("pitch:") for e in cs2["QFNPART"]["errors"])
        assert cs2["SOICPART"]["source"] == "none" and any("no recorded response for SOICPART" in t for t in cs2["SOICPART"]["tried"])
        cmp = R.compare(Path(d) / "a", Path(d) / "b")
        assert sorted(cmp["flipped"]["broken"]) == ["qfn-QFNPART", "soic-SOICPART"] and not cmp["flipped"]["fixed"]
        assert "pass -> fail (2)" in cmp["text"] and "-100.0" in cmp["text"]
        assert R.compare(Path(d) / "b", Path(d) / "a")["flipped"]["fixed"] == ["soic-SOICPART", "qfn-QFNPART"]


def test_parts_sharing_a_bom_footprint_name_are_flagged():
    """Two parts whose BOM says the same footprint name get one footprint file (librarian: one per name). The second
    part is scored on what it got, and the report says why."""
    with tempfile.TemporaryDirectory() as d:
        f = mini_set(d)
        data = json.loads(f.read_text())
        data["cases"].append(dict(data["cases"][0], id="soic-SOIC2", mpn="SOIC2", footprint=VQFN))   # same "SOIC-8", other land
        f.write_text(json.dumps(data))
        rec = record_dir(Path(d) / "rec", {"SOICPART": TD.INCH, "QFNPART": qfn_ext(), "SOIC2": TD.INCH})
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            rep = R.run(f, Path(d) / "o", cache=Path(d) / "cache", http=TL.FakeHTTP(), log=lambda *a: None, replay=rec)
        cs = {c["mpn"]: c for c in rep["cases"]}
        assert cs["SOIC2"]["footprint_shared_with"] == "SOICPART" and not cs["SOIC2"]["pass"] and not cs["SOICPART"]["footprint_shared_with"]
        assert rep["summary"]["shared_footprints"] == {"cases": 1, "failing": 1}
        assert "[footprint shared with SOICPART]" in (Path(d) / "o" / "report.md").read_text()


def test_record_then_replay_gives_the_same_report():
    pdfs = {"https://example.com/soic.pdf": TD.PDF, "https://example.com/qfn.pdf": TD.PDF}
    api = TB.FakeAPI(answer=lambda part: TD.message(TD.INCH if part == "SOICPART" else qfn_ext()))
    with tempfile.TemporaryDirectory() as d:
        with TB.patched(api), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": TB.KEY}):
            rep = R.run(mini_set(d), Path(d) / "live", cache=Path(d) / "cache", http=TB.PdfHTTP(pdfs), log=lambda *a: None,
                        record=Path(d) / "rec", model="claude-sonnet-5-5")
        # 2 reads + the cross-check's second read for QFNPART (its EP1.68 package is not in the standard table;
        # SOICPART is checked against the table for free)
        assert len(api.of("/v1/messages")) == 3 and rep["meta"]["mode"] == "live, recording"
        assert api.of("/v1/messages")[0][3]["model"] == "claude-sonnet-5-5"
        assert sorted(p.name for p in (Path(d) / "rec").iterdir()) == ["QFNPART.json", "SOICPART.json"]
        live = {c["mpn"]: c for c in rep["cases"]}
        assert all(c["pass"] for c in live.values()) and live["SOICPART"]["cost_how"] == "API usage", [c["errors"] for c in live.values()]
        assert live["QFNPART"]["cost_how"] == "API usage + cross-check read"
        assert live["QFNPART"]["usd"] == 2 * R.usd("claude-opus-5-5", TD.message(TD.GOOD)["usage"])   # priced as the served model
        rec = json.loads((Path(d) / "rec" / "QFNPART.json").read_text())
        assert rec["job"]["prov"]["sha256"] and rec["estimate"]["how"].startswith("rough")
        n = len(api.calls)
        rep2 = run(d, "replayed", replay=Path(d) / "rec")
        assert len(api.calls) == n
        assert [(c["id"], c["pass"], c["usd"], c["tokens_in"]) for c in rep2["cases"]] == \
               [(c["id"], c["pass"], c["usd"], c["tokens_in"]) for c in rep["cases"]]


def test_a_batch_run_records_half_price_and_replays_it():
    pdfs = {"https://example.com/soic.pdf": TD.PDF, "https://example.com/qfn.pdf": TD.PDF}
    api = TB.FakeAPI(answer=lambda part: TD.message(TD.INCH if part == "SOICPART" else qfn_ext()))
    with tempfile.TemporaryDirectory() as d:
        with TB.patched(api), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": TB.KEY}):
            rep = R.run(mini_set(d), Path(d) / "live", cache=Path(d) / "cache", http=TB.PdfHTTP(pdfs), log=lambda *a: None,
                        record=Path(d) / "rec", batch=True)
        assert rep["meta"]["mode"] == "live, recording, batch" and api.of("/v1/messages/batches"), api.calls
        live = {c["mpn"]: c for c in rep["cases"]}
        assert live["SOICPART"]["pass"] and "batch: half price" in live["SOICPART"]["cost_how"], live["SOICPART"]
        assert abs(live["SOICPART"]["usd"] - R.usd("claude-opus-5-5", TD.message(TD.GOOD)["usage"]) / 2) < 1e-9
        rep2 = run(d, "replayed", replay=Path(d) / "rec")
        assert [(c["id"], c["pass"], c["usd"]) for c in rep2["cases"]] == [(c["id"], c["pass"], c["usd"]) for c in rep["cases"]]


def test_cached_only_rescores_a_live_run_without_sending():
    """#42: a live batch run whose answers the parser rejected is re-scored with nothing bought again: the collected
    batch's answers are downloaded again (free); a part that needs a read that was never made is reported, not sent."""
    pdfs = {"https://example.com/soic.pdf": TD.PDF, "https://example.com/qfn.pdf": TD.PDF}
    soic = copy.deepcopy(TD.INCH)
    soic["package"]["land"] = {"pad_length": 1.55, "pad_width": 0.6, "pad_center": 2.7}      # no pad_center_y
    api = TB.FakeAPI(answer=lambda part: TD.message(D.to_wire(soic) if part == "SOICPART" else qfn_ext()), polls=0)
    with tempfile.TemporaryDirectory() as d:
        kw = dict(cache=Path(d) / "cache", http=TB.PdfHTTP(pdfs), log=lambda *a: None, batch=True)
        with TB.patched(api), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": TB.KEY}):
            with mock.patch.object(D, "_fit", lambda v, s: v):            # the parser before the fix
                rep = R.run(mini_set(d), Path(d) / "live", **kw)
            sent = len(api.of("/v1/messages/batches")) + len(api.of("/v1/messages"))
            assert not {c["mpn"]: c for c in rep["cases"]}["SOICPART"]["pass"]
            rep2 = R.run(mini_set(d), Path(d) / "live", cached_only=True, **kw)
        assert len(api.of("/v1/messages/batches")) + len(api.of("/v1/messages")) == sent, "something was sent"
        cs = {c["mpn"]: c for c in rep2["cases"]}
        assert rep2["meta"]["mode"] == "cached answers only (nothing sent)" and cs["SOICPART"]["source"] == "datasheet", cs["SOICPART"]


def test_cached_only_scores_a_route_step_never_sent_from_the_saved_answer():
    """After a parser fix the route can ask for a step the live run never sent (it took another way past the
    rejected answer): --cached-only scores the part from its saved answer for the same datasheet, sending nothing."""
    pdfs = {"https://example.com/soic.pdf": TD.PDF, "https://example.com/qfn.pdf": TD.PDF}
    api = TB.FakeAPI(answer=lambda part: TD.message(TD.INCH if part == "SOICPART" else qfn_ext()), polls=0)
    with tempfile.TemporaryDirectory() as d:
        kw = dict(cache=Path(d) / "cache", http=TB.PdfHTTP(pdfs), log=lambda *a: None, batch=True)
        with TB.patched(api), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": TB.KEY}):
            rep = R.run(mini_set(d), Path(d) / "live", **kw)
            assert {c["mpn"]: c for c in rep["cases"]}["SOICPART"]["pass"]
            res = Path(d) / "live" / "lib" / "cache" / "datasheet_results"
            for f in res.glob("*.json"):                              # the saved answers now sit under other steps' keys
                if json.loads(f.read_text())["part"] == "SOICPART":
                    f.rename(f.with_name(f.name[:17] + "other-step-" + f.name[17:]))
            for f in (Path(d) / "live").rglob("batch_state.json"):    # and no batch holds an answer for this step
                f.unlink()
            sent = len(api.calls)
            rep2 = R.run(mini_set(d), Path(d) / "live", cached_only=True, **kw)
        assert not [c for c in api.calls[sent:] if c[0] == "POST"], "something was sent"
        s = {c["mpn"]: c for c in rep2["cases"]}["SOICPART"]
        assert s["pass"] and s["source"] == "datasheet", s


def test_estimate_mode_sends_nothing():
    pdfs = {"https://example.com/soic.pdf": TB.pdf_with(["SOIC-8 package"] * 3)}
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(D.requests, "post", side_effect=AssertionError("estimate mode must not call the API")):
        rep = run(d, "est", http=TB.PdfHTTP(pdfs))
    cs = {c["mpn"]: c for c in rep["cases"]}
    assert rep["meta"]["mode"].startswith("estimate") and rep["summary"]["overall"]["pass"] == 0
    s = cs["SOICPART"]
    assert s["source"] == "none" and s["cost_how"] == "estimate (rough: 3 pages x 3000)" and s["usd"] > 0, s
    assert cs["QFNPART"]["cost_how"].startswith("estimate (rough: 30 pages")      # not downloaded: unknown page count
    assert s["not_run"] and rep["summary"]["not_run"]["cases"] >= 1, rep["summary"]["not_run"]
    md = R.report_md(rep)                                         # no key: "not run", never "N parts were wrong"
    assert "Not run:" in md and "no API key" in md and "were wrong" not in md and "SOICPART" not in md.split("## By")[0], md


def test_select_interleaves_families_and_cli():
    cases = R.load_set("kicad-v1")["cases"]
    first = R.select(cases, limit=11)
    assert len({c["family"] for c in first}) == 11
    assert {c["family"] for c in R.select(cases, families=["bga"])} == {"bga"}
    assert [c["mpn"] for c in R.select(cases, ids=["RC0603FR-0710KL"])] == ["RC0603FR-0710KL"]
    with tempfile.TemporaryDirectory() as d:
        rec = record_dir(Path(d) / "rec", {"SOICPART": TD.INCH, "QFNPART": qfn_ext()})
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        assert cli.COMMANDS["eval"][0] == "ecadlib.eval.cli"
        from ecadlib.eval import cli as EC
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("ecadlib.httpcache.CachedHTTP", lambda *a, **k: TL.FakeHTTP()):
            assert EC.main(["run", "--set", str(mini_set(d)), "--replay", str(rec), "-o", str(Path(d) / "o"),
                            "--cache", str(Path(d) / "c"), "--tol", "pitch=0.02"]) == 0
        rep = json.loads((Path(d) / "o" / "report.json").read_text())
        assert rep["meta"]["tolerances"]["pitch"] == 0.02 and rep["summary"]["overall"]["pass"] == 2


# ---------------------------------------------------------------- set builder
SYM = """(kicad_symbol_lib (version 20241209)
  (symbol "{name}" (property "Reference" "U") (property "Value" "{name}") (property "Footprint" "{fp}")
    (property "Datasheet" "{ds}") (property "Description" "test")
    (symbol "{name}_1_1" {pins}))
)"""


def _pin(n, name):
    return f'(pin input line (at 0 0 0) (length 2.54) (name "{name}") (number "{n}"))'


def test_build_set_from_a_kicad_checkout():
    with tempfile.TemporaryDirectory() as d:
        sym, fp = Path(d) / "sym", Path(d) / "fp"
        for lib, name, fpref, ds, n in (("Interface", "MAX3232E", SOIC, "https://www.analog.com/x.pdf", 8),
                                        ("Interface", "STM32F103C8Tx", SOIC, "https://www.st.com/x.pdf", 8),     # wildcard: skipped
                                        ("Interface", "NODS1234", SOIC, "~", 8),                                  # no datasheet
                                        ("Interface", "DISTI1234", SOIC, "https://www.mouser.com/x.pdf", 8),      # no maker known
                                        ("Amplifier", "QFN5678", VQFN, "https://www.ti.com/x.pdf", 17)):
            (sym / f"{lib}.kicad_symdir").mkdir(parents=True, exist_ok=True)
            (sym / f"{lib}.kicad_symdir" / f"{name}.kicad_sym").write_text(
                SYM.format(name=name, fp=fpref, ds=ds, pins=" ".join(_pin(i, f"P{i}") for i in range(1, n + 1))))
        txt, _ = F.build(json.loads(FB.spec_path("SOIC-8_3.9x4.9mm_P1.27mm").read_text()))
        (fp / "Package_SO.pretty").mkdir(parents=True)
        (fp / "Package_SO.pretty" / "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod").write_text(txt)
        (fp / "Package_DFN_QFN.pretty").mkdir(parents=True)
        (fp / "Package_DFN_QFN.pretty" / "VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm.kicad_mod").write_text(
            F.build(json.loads(FB.spec_path("VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm").read_text()))[0])
        jst = (fp / "Connector_JST.pretty")
        jst.mkdir(parents=True)
        (jst / "JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical.kicad_mod").write_text(
            F.build(json.loads(FB.spec_path("JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical").read_text()))[0]
            .replace('(descr "', '(descr "JST XH (http://www.jst-mfg.com/product/pdf/eng/eXH.pdf) ', 1))
        cases, refs = B.build(sym, fp, log=lambda *a: None)
        by = {c["mpn"]: c for c in cases}
        assert set(by) >= {"MAX3232E", "QFN5678", "B4B-XH-A"} and not set(by) & {"STM32F103C8Tx", "NODS1234", "DISTI1234"}, by
        m = by["MAX3232E"]
        assert (m["family"], m["package"], m["manufacturer"], m["from"]) == \
               ("soic", "SOIC-8", "Analog Devices", {"kind": "kicad-symbol", "symbol": "Interface:MAX3232E"})
        assert m["pins"][0] == ["1", "P1"] and len(m["pins"]) == 8
        j = by["B4B-XH-A"]
        assert (j["family"], j["manufacturer"], j["package"], j["datasheet"]) == \
               ("connector", "JST", "", "http://www.jst-mfg.com/product/pdf/eng/eXH.pdf") and [p[0] for p in j["pins"]] == ["1", "2", "3", "4"]
        assert set(refs) == {c["footprint"] for c in cases if c["footprint"] in refs}
        out = Path(d) / "set.json"
        B.write_set(out, [c for c in cases if c["footprint"] in refs], refs, sym, fp, "t")
        data = json.loads(out.read_text())
        assert data["meta"]["name"] == "t" and data["cases"] and all(c["footprint"] in data["references"] for c in data["cases"])
    assert B.short_package("Package_DFN_QFN:Maxim_TDFN-12-1EP_3x3mm_P0.5mm") == "TDFN-12-1EP"
    assert B.short_package("Diode_SMD:D_SOD-123") == "SOD-123" and B.short_package("Resistor_SMD:R_0603_1608Metric") == "0603"
    assert B.classify("Package_TO_SOT_SMD:TO-252-2") == "power" and B.classify("Package_DIP:DIP-8_W7.62mm") == "tht"
    assert B.maker("https://assets.nexperia.com/documents/x.pdf") == "Nexperia" and B.maker("https://www.mouser.com/x") is None


def test_numbers_ignores_a_drill_offset_without_a_hole():
    """An SMD pad with only (drill (offset ...)) (KiCad's Fairchild_LSOP-8) used to raise in footprint_bench.numbers."""
    txt = """(footprint "X" (layer "F.Cu")
  (pad "1" smd rect (at 0 0) (size 1 2) (drill (offset 0 0.266)) (layers "F.Cu" "F.Paste" "F.Mask")))"""
    assert FB.numbers(txt)["pads"] == [["1", 0.0, 0.0, 1.0, 2.0, 0.0]]


# ---------------------------------------------------------------- review fixes (rvK)
def test_symbol_names_swapped_between_pins_fail():
    """Two pins' names swapped (VIN on pin 2, VOUT on pin 1) used to pass at 8/10 names agreeing."""
    ref = [("1", "VIN"), ("2", "VOUT")] + [(str(i), f"P{i}") for i in range(3, 11)]
    mine = [("1", "VOUT"), ("2", "VIN")] + ref[2:]
    r = S.score_symbol(mine, ref)
    assert not r["symbol_names"]["ok"] and r["symbol_names"]["moved"] == 2, r
    noise = [("1", "VIN"), ("2", "OUT")] + ref[2:]          # a different name, not another pin's: tolerated as before
    assert S.score_symbol(noise, ref)["symbol_names"]["ok"]
    gnd = [("1", "GND"), ("2", "GND"), ("3", "GND")]         # repeated names are never "moved"
    assert S.score_symbol([("1", "GND"), ("2", "GND"), ("3", "VSS")], gnd)["symbol_names"].get("moved") == 0


def test_eval_usd_has_the_haiku_long_prompt_tier():
    """Claude Haiku 5.5 is $0.50 / $2.50 per MTok above 100K prompt tokens (claude-api skill): a whole-PDF read used to
    be priced at $0.10 in the eval report."""
    assert abs(R.usd("claude-haiku-5-5", {"input_tokens": 200_000, "output_tokens": 1000}) - (200_000 * 0.5 + 1000 * 2.5) / 1e6) < 1e-9
    assert abs(R.usd("claude-haiku-5-5", {"input_tokens": 1000, "output_tokens": 1000}) - 0.6 / 1000) < 1e-9


def test_eval_cost_counts_every_answer_of_the_route_and_replays_it():
    """Sonnet refuses, Opus answers: both answers were paid for, so the case costs both (live and replayed)."""
    from tests import test_ds_route as TR

    class API(TR.ModelAPI):
        def _msg(self, body):
            ext = TD.INCH if "SOICPART" in json.dumps(body) else qfn_ext()
            self.by_model = lambda m, n: "refusal" if m != TR.OPUS else ext
            return super()._msg(body)
    api = API(lambda m, n: None)
    pdfs = {"https://example.com/soic.pdf": TD.PDF, "https://example.com/qfn.pdf": TD.PDF}
    rules = {"datasheet": {"route": "ladder"}}
    with tempfile.TemporaryDirectory() as d:
        with TB.patched(api), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": TB.KEY}):
            rep = R.run(mini_set(d), Path(d) / "live", cache=Path(d) / "cache", http=TB.PdfHTTP(pdfs), log=lambda *a: None,
                        record=Path(d) / "rec", rules=rules, sources=["datasheet"])
        assert [m for m, _ in api.sent] == [TR.SONNET, TR.OPUS] * 2 + [TR.OPUS], api.sent   # + QFNPART's cross-check read
        u = TD.message(TD.GOOD)["usage"]
        want = R.usd(TR.SONNET, u) + R.usd(TR.OPUS, u)
        live = {c["mpn"]: c for c in rep["cases"]}
        for c in live.values():
            chk = c["mpn"] == "QFNPART"                       # its cross-check read counts too
            assert c["pass"] and abs(c["usd"] - want - (R.usd(TR.OPUS, u) if chk else 0)) < 1e-6, c
            assert c["cost_how"] == "API usage + 1 earlier answer" + (" + cross-check read" if chk else ""), c
            assert c["tokens_out"] == (3 if chk else 2) * 2304
        rep2 = run(d, "replayed", replay=Path(d) / "rec", sources=["datasheet"])
        assert [(c["id"], c["pass"], c["usd"], c["tokens_in"]) for c in rep2["cases"]] == \
               [(c["id"], c["pass"], c["usd"], c["tokens_in"]) for c in rep["cases"]]


def test_parts_without_a_datasheet_pdf_are_reported_apart():
    """#42: a dead or refused maker link never reaches the model; the report gives the rate without those parts too."""
    from ecadlib.eval import runner as RN
    row = lambda i, ok, src, tried=(): {"id": i, "family": "qfn", "pass": ok, "source": src, "fields": {}, "usd": None,
                                        "tokens_in": 0, "tokens_out": 0, "tried": list(tried)}
    rows = [row("a", True, "datasheet"), row("b", False, "datasheet"),
            row("c", False, "none", ["datasheet: failed (DatasheetError: no datasheet link gave a PDF: x: HTTP 404)"]),
            row("d", False, "none", ["easyeda: failed (HTTP 403)"])]
    s = RN.summarize(rows)
    assert s["no_datasheet"] == {"cases": 1, "ids": ["c"]}, s["no_datasheet"]
    assert (s["overall"]["pass"], s["overall"]["cases"]) == (1, 4) and (s["with_datasheet"]["pass"], s["with_datasheet"]["cases"]) == (1, 3)


def test_kicad_subscript_pin_names_match_plain_ones():
    """KiCad writes V_{SS}; a datasheet says VSS: the same pin name (CD4066BNS failed on this alone)."""
    assert S.norm_name("V_{SS}") == S.norm_name("VSS") and S.norm_name("V_{DD}") == S.norm_name("Vdd")
    assert S.norm_name("~{RESET}") == S.norm_name("RESET") and S.norm_name("V_{SS}") != S.norm_name("VDD")


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            f(); print("ok", k)


def test_spelled_out_pin_names_and_through_hole_diodes():
    """NCP662 (benchmark 2026-10-18): the datasheet's "Enable" is KiCad's "EN". 1N5400 / 1N4002: the eval BOM names
    a through-hole diode D, as a real BOM does, so the standard source treats it as a diode."""
    assert S.norm_name("Enable") == S.norm_name("EN") and S.norm_name("Ground") == "GND"
    assert S.norm_name("ENA") == "ENA"                                     # only whole words
    assert S.norm_name("N.C.") == S.norm_name("NC") == S.norm_name("N.C")         # TLP2745
    with tempfile.TemporaryDirectory() as d:
        cases = [c for c in R.load_set("kicad-v1")["cases"] if c["mpn"] in ("1N5400", "1N4002")]
        rows = (Path(d) / "b.csv")
        R.write_bom(cases, rows)
        assert [ln.split(",")[0][0] for ln in rows.read_text().splitlines()[1:]] == ["D", "D"]


def test_a_footprint_on_the_makers_land_is_told_apart_from_a_misread():
    """CD4066BM (benchmark 2026-10-18): TI's example board layout is 1.55 x 0.6 pads at +-2.7; KiCad's SO-14 works
    its land out from the dimensions (1.95 at +-2.475). Ours follows the datasheet: reported, not counted as a pass."""
    def so14(pl, pc, ep=None):
        pads = [[str(i + 1), -pc, -3.81 + 1.27 * i, pl, 0.6, 0.0] for i in range(7)]
        pads += [[str(14 - i), pc, -3.81 + 1.27 * i, pl, 0.6, 0.0] for i in range(7)]
        if ep:
            pads.append(["15", 0.0, 0.0, ep, ep, 0.0])
        return {"pads": pads, "courtyard": [-pc - pl / 2 - 0.25, -4.58, pc + pl / 2 + 0.25, 4.58]}
    spec = {"land": {"pad_length": 1.55, "pad_width": 0.6, "pad_center": 2.7}}
    mine, ref = so14(1.55, 2.7), so14(1.95, 2.475)
    sc = S.score(mine, ref)
    ml = S.maker_land(mine, spec)
    assert not sc["pass"] and ml["followed"] and S.only_maker_land(sc["fields"], ml), (sc["errors"], ml)
    assert S.maker_land(mine, {"land": {}}) is None and S.maker_land(None, spec) is None
    assert not S.maker_land(so14(1.3, 2.7), spec)["followed"]                     # not the land the datasheet gives
    mine_ep = so14(1.55, 2.7, ep=2.0)                                             # a wrong exposed pad is still a miss
    sc = S.score(mine_ep, so14(1.55, 2.7, ep=3.4))
    ml = S.maker_land(mine_ep, spec)
    assert ml["followed"] and ml["ep"] == ["15"] and not S.only_maker_land(sc["fields"], ml)
    rows = [{"id": "a", "family": "soic", "pass": False, "source": "datasheet", "fields": {}, "tokens_in": 0,
             "tokens_out": 0, "usd": 0, "maker_land": {"followed": True}, "kicad_differs_maker_land": True, "tried": []}]
    assert R.summarize(rows)["maker_land"] == {"given": 1, "followed": 1, "kicad_differs": ["a"], "body_differs": []}


def test_a_checked_reference_erratum_scores_the_datasheets_pins_right():
    """AH1806-W (benchmark 2026-10-18): KiCad's symbol swaps SC59 pins 2 and 3; the Diodes datasheet has 2 GND,
    3 OUTPUT. Ours as the datasheet says passes, with the erratum named; ours as KiCad says, or wrong, does not pass by it."""
    er = R.load_errata("kicad-v1")["AH1806-W"]
    ref = [("1", "VDD"), ("2", "OUTPUT"), ("3", "GND")]
    ours = [("1", "VDD"), ("2", "GND"), ("3", "OUTPUT")]
    sc = S.score(None, None, ours, ref)
    sc["fields"].pop("footprint")
    sc["pass"], sc["errors"] = S.verdict(sc["fields"]), [e for e in sc["errors"] if not e.startswith("footprint")]
    assert not sc["pass"] and R.apply_errata(sc, ours, er) == ["symbol_names"] and sc["pass"] and not sc["errors"], sc
    assert "AH1806" in sc["fields"]["symbol_names"]["erratum"]
    wrong = [("1", "GND"), ("2", "VDD"), ("3", "OUTPUT")]
    sc = S.score(None, None, wrong, ref)
    assert R.apply_errata(sc, wrong, er) == [] and not sc["pass"]
    assert "kicad-v1.errata" not in R.available() and R.load_errata("nonexistent.json") == {}


def test_a_wrong_variant_reference_is_excused_only_when_we_built_the_datasheets_package():
    """PCA9557BS (live benchmark 2026-10-19): the datasheet's BS part is SOT629-1, 4 x 4 mm at 0.65 mm; KiCad's reference
    is the 3 x 3 mm 0.5 mm HVQFN-16. Ours at 0.65 mm passes by the erratum; ours at any other pitch does not."""
    er = R.load_errata("kicad-v1")["PCA9557BS"]
    f = lambda ok=False, **k: dict(ok=ok, error="" if ok else "x", **k)
    for ours, excused in ((0.65, True), (0.8, False)):
        sc = {"pass": False, "errors": ["pitch: x", "pads: x", "pin1: x", "courtyard: x"],
              "fields": {"pitch": f(ours=ours, ref=0.5), "pads": f(), "pin1": f(), "courtyard": f(), "pad_count": f(True)}}
        got = R.apply_errata(sc, None, er)
        assert (sorted(got) == ["courtyard", "pads", "pin1", "pitch"] and sc["pass"]) if excused else (got == [] and not sc["pass"])
    assert "O0 - O7" in R.load_errata("kicad-v1")["27C256"]["symbol_names"]["why"]


def test_cached_only_rescores_after_the_schema_changes():
    """A new field in the reader's schema changes every answer's cache key: --cached-only still scores the saved
    answers (sync and batch runs alike), sending nothing."""
    pdfs = {"https://example.com/soic.pdf": TD.PDF, "https://example.com/qfn.pdf": TD.PDF}
    for batch in (False, True):
        api = TB.FakeAPI(answer=lambda part: TD.message(TD.INCH if part == "SOICPART" else qfn_ext()), polls=0)
        with tempfile.TemporaryDirectory() as d:
            kw = dict(cache=Path(d) / "cache", http=TB.PdfHTTP(pdfs), log=lambda *a: None, batch=batch)
            with TB.patched(api), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": TB.KEY}):
                rep = R.run(mini_set(d), Path(d) / "live", **kw)
                before = {c["mpn"]: c["pass"] for c in rep["cases"]}
                sent = len(api.calls)
                with mock.patch.object(D, "schema_version", lambda: "000000000000"):
                    rep2 = R.run(mini_set(d), Path(d) / "live", cached_only=True, **kw)
            assert not [c for c in api.calls[sent:] if c[0] == "POST"], ("something was sent", batch)
            assert {c["mpn"]: c["pass"] for c in rep2["cases"]} == before and any(before.values()), (batch, rep2["cases"])


def test_live_harness_pins_datasheets_types_failures_and_publishes():
    """Oliver's live harness: every run records the PDF's SHA-256, the prompt version and why each part failed; a
    pins file flags a datasheet that changed since; `eval publish` writes the results page with a one-line headline."""
    pdfs = {"https://example.com/soic.pdf": TD.PDF, "https://example.com/qfn.pdf": TD.PDF}
    soic = copy.deepcopy(TD.INCH)
    soic["package"]["pitch"] = {"nom": 2.54}                                  # a wrong pitch: the wrong variant
    api = TB.FakeAPI(answer=lambda part: TD.message(D.to_wire(soic) if part == "SOICPART" else qfn_ext()), polls=0)
    with tempfile.TemporaryDirectory() as d:
        kw = dict(cache=Path(d) / "cache", http=TB.PdfHTTP(pdfs), log=lambda *a: None)
        with TB.patched(api), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": TB.KEY}):
            rep = R.run(mini_set(d), Path(d) / "live", **kw)
        cs = {c["mpn"]: c for c in rep["cases"]}
        assert rep["meta"]["prompt_version"] == D.schema_version() and cs["QFNPART"]["prompt_version"] == D.schema_version()
        ds = cs["QFNPART"]["datasheet"]
        assert ds["sha256"] == hashlib.sha256(TD.PDF).hexdigest() and ds["url"] == "https://example.com/qfn.pdf"
        assert cs["QFNPART"]["datasheet_pin"] == "new" and cs["QFNPART"]["failure"] is None
        bad = cs["SOICPART"]
        assert not bad["pass"] and bad["failure"] in R.FAILURE_TYPES and bad["failure"] != "no_pdf", bad
        pm = rep["summary"]["pad_match"]["built"]
        assert pm["of"] > 0 and 0 < pm["right"] <= pm["of"] and rep["summary"]["failures"][bad["failure"]] == [bad["id"]]
        pins = Path(d) / "pins.json"
        assert R.write_pins(Path(d) / "live", pins) == 2
        doc = json.loads(pins.read_text())
        doc["cases"]["QFNPART"]["sha256"] = "0" * 64                          # the maker changed the PDF since
        pins.write_text(json.dumps(doc))
        with TB.patched(api), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": TB.KEY}):
            rep2 = R.run(mini_set(d), Path(d) / "live", pins=pins, **kw)
        cs2 = {c["mpn"]: c for c in rep2["cases"]}
        assert cs2["QFNPART"]["datasheet_pin"] == "changed" and cs2["SOICPART"]["datasheet_pin"] == "same"
        assert "CHANGED since pinned" in R.report_md(rep2)
        head = R.publish(Path(d) / "live", Path(d) / "results.md")
        page = (Path(d) / "results.md").read_text()
        assert head.startswith(f"{rep2['meta']['model']}, 2 real parts: 1 right (50") and "pad match" in head, head
        assert "## Why parts failed" in page and bad["failure"] in page and "benchmark.md" in page


def test_failure_types_from_the_benchmark():
    """The failure types of real #42 misses, from their report rows."""
    row = lambda **k: dict({"pass": False, "source": "datasheet", "fields": {}, "tried": [], "errors": []}, **k)
    f = lambda ok=False, **k: dict(ok=ok, error="" if ok else "x", **k)
    so = {"pads": [[str(i), 0, i, 1, 0.6, 0] for i in range(1, 9)] + [["9", 0, 0, 3, 3, 0]], "courtyard": []}
    assert R.failure_type(row(source="none", tried=["datasheet: no datasheet URL known"]), None, None) == "no_pdf"
    for err in ("The request timed out while trying to download the file. Please try again later.",   # LTM4671 (Opus run)
                "Unable to connect to the remote server. Please verify the URL is correct and accessible."):  # LTV-352T
        assert R.failure_type(row(source="none", tried=["standard: no standard package named",
                                                        f"datasheet: batch request errored: invalid_request_error: {err}"]),
                              None, None) == "no_pdf"
    assert R.failure_type(row(source="none", tried=["no footprint: the datasheet read left pitch unresolved"]), None, None) == "blank_dimension"
    assert R.failure_type(row(source="none", cross_check="disagreed"), None, None) == "held_cross_check"
    assert R.failure_type(row(fields={"pitch": f(ours=25.4, ref=1.0)}), so, {}) == "wrong_unit"
    assert R.failure_type(row(fields={"pitch": f(ours=0.65, ref=0.5)}), so, {}) == "wrong_variant"     # PCA9557BS
    assert R.failure_type(row(fields={"pin1": f(), "pads": f(bad_pads=["1"])}), so, {}) == "mirrored"  # S10B-PH-K
    assert R.failure_type(row(fields={"pads": f(bad_pads=["9"])}), so, {}) == "thermal_pad"            # BQ24004
    assert R.failure_type(row(fields={"pads": f(bad_pads=["1", "2"])}), so, {"pad_table": {"arrays": []}}) == "misread_table"
    assert R.failure_type(row(fields={"pads": f(bad_pads=["1"]), "courtyard": f()}), so, {}) == "misread_dimension"
    assert R.failure_type(row(fields={"symbol_names": f()}), so, {}) == "symbol"
    assert R.failure_type(row(kicad_differs_maker_land=True), so, {}) == "maker_land"
    assert R.failure_type(row(**{"pass": True}), so, {}) is None
    assert "kicad-v1.pins" not in R.available()


def test_a_live_run_does_not_charge_the_estimate_of_a_part_with_no_answer():
    """#42 Opus run: 26 batch requests errored (the API could not fetch the PDF) and were billed nothing, but the report
    added their estimates ($0.63 each), $32.41 against $16.00 really spent."""
    used = {"estimate": {"input_tokens": 101702, "output_tokens": 6000, "usd": 0.62851, "how": "rough: 30 pages x 3000"}}
    tin, tout, usd, how = R._cost(used, "claude-opus-5-5", live=True)
    assert (tin, tout, usd) == (0, 0, 0.0) and "nothing billed" in how and "$0.63" in how
    assert R._cost(used, "claude-opus-5-5")[2] == 0.62851                          # no key: the estimate is the answer
