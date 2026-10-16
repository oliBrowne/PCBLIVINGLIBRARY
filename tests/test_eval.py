"""Offline tests for ecadlib.eval: the shipped test set, the scorer, `ecadlib eval run` (replay / record / estimate),
`compare` and the set builder.  Run:  python3 -m tests.test_eval

References are KiCad numbers: the shipped set (ecadlib/eval/sets/kicad-v1.json) and footprint_bench's fixture. Claude's
answers are the SYNTHETIC extractions of tests/test_datasheet in the documented Messages API shape, so nothing here
says how accurate a real model is: these tests prove the harness scores what it is given."""
import copy, json, os, tempfile
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
        assert "**Pass: 2/2 (100.0 %)**" in md and "| qfn | 1 | 1 | 100.0 % | datasheet 1 |" in md
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
        assert len(api.of("/v1/messages")) == 2 and rep["meta"]["mode"] == "live, recording"
        assert api.of("/v1/messages")[0][3]["model"] == "claude-sonnet-5-5"
        assert sorted(p.name for p in (Path(d) / "rec").iterdir()) == ["QFNPART.json", "SOICPART.json"]
        live = {c["mpn"]: c for c in rep["cases"]}
        assert all(c["pass"] and c["cost_how"] == "API usage" for c in live.values()), [c["errors"] for c in live.values()]
        assert live["QFNPART"]["usd"] == R.usd("claude-opus-5-5", TD.message(TD.GOOD)["usage"])   # priced as the served model
        rec = json.loads((Path(d) / "rec" / "QFNPART.json").read_text())
        assert rec["job"]["prov"]["sha256"] and rec["estimate"]["how"].startswith("rough")
        n = len(api.calls)
        rep2 = run(d, "replayed", replay=Path(d) / "rec")
        assert len(api.calls) == n
        assert [(c["id"], c["pass"], c["usd"], c["tokens_in"]) for c in rep2["cases"]] == \
               [(c["id"], c["pass"], c["usd"], c["tokens_in"]) for c in rep["cases"]]


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


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            f(); print("ok", k)
