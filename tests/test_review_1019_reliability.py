"""More tests for the 2026-10-19 reliability fixes: fallbacks said out loud, the $10 default cap, the Altium .pas
files, eval without a key, and the unofficial sources opt-in. Each test runs the real code path end to end where it
can (librarian run, CLI main, altium-menu build) rather than the helper alone."""
import contextlib, io, json, os, tempfile
from pathlib import Path
from unittest import mock

import tests
from ecadlib import (ai_cost as AC, altium_forms as AF, altium_menu as AM, altium_native as AN, datasheet as D,
                     doctor as DR, fallbacks as FB, librarian as L, llm as LL, providers as P, rules as RU,
                     rules_infer as I, upstream as UP)
from ecadlib.eval import runner as R
from ecadlib.llm import run as LR
from tests import test_datasheet as TD, test_eval as TE, test_librarian as TL

KEY = "sk-test-1019"


def _quiet():
    return contextlib.ExitStack()


def _err():
    return contextlib.redirect_stderr(io.StringIO())


# ================================================================ 1. fallbacks
def test_guide_rails_fallback_is_noted():
    FB.take()
    with _err(), mock.patch.object(LR, "info", side_effect=RuntimeError("bad [ai] preset")):
        assert LR.wants({}, "json_repair") is False
    got = FB.take()
    assert len(got) == 1 and "guide rails are unknown" in got[0] and "json_repair" in got[0], got


def test_rules_layers_fallback_is_noted():
    FB.take()
    with _err(), tempfile.TemporaryDirectory() as d:
        (Path(d) / "ecadlib.toml").write_text("[datasheet\nbroken = ")       # a rules file that does not parse
        assert UP._rules_layers(d) is None
    got = FB.take()
    assert len(got) == 1 and "layers" in got[0] and "default layers" in got[0], got


def test_doctor_provider_fallback_is_noted():
    FB.take()
    with _err(), mock.patch.object(LL, "endpoint", side_effect=ValueError("unknown provider 'x'")):
        assert DR._ai_provider({}) == "anthropic"
        DR._ai_key_env({})
    got = FB.take()
    assert got and all("model setup did not resolve" in g for g in got), got


def test_note_is_thread_safe_and_keeps_order():
    import threading
    FB.take()
    with _err():
        ts = [threading.Thread(target=FB.note, args=(f"thing {i % 5} failed",)) for i in range(50)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    got = FB.take()
    assert sorted(got) == [f"thing {i} failed" for i in range(5)], got


def test_librarian_review_leads_with_a_fallback_from_the_run():
    """A fallback during a real librarian run reaches that run's review.md and review.html, and only that run's."""
    FB.take()
    real = L.write_review

    def noisy(*a, **k):                                   # a setting failing while the run builds its parts
        FB.note("house-style parameter name for 'Manufacturer' did not load", KeyError("names"), instead="Manufacturer")
        return real(*a, **k)
    with tempfile.TemporaryDirectory() as d, _err():
        with mock.patch.object(L, "write_review", noisy):
            TL.run(d)
        md = (Path(d) / "out" / "review.md").read_text(encoding="utf-8")
        html = (Path(d) / "out" / "review.html").read_text(encoding="utf-8")
        assert "1 setting fell back to a default" in md and "'Manufacturer' did not load" in md, md[:1500]
        assert "fell back to a default" in html
        TL.run(d, refresh=[])                               # the next run had no fallback: its review says nothing
        assert "fell back" not in (Path(d) / "out" / "review.md").read_text(encoding="utf-8")
    assert FB.peek() == []


# ================================================================ 2. the $10 default cap
def _reader(d, **cfg):
    rules = {"datasheet": cfg} if cfg else None
    return D.Reader(d, d, key=KEY, http=None, log=None, rules=rules)


def _jobs(n=3):
    return [{"name": f"P{i}", "url": "https://example.com/x.pdf", "body": {"model": D.MODEL, "messages": [], "max_tokens": 8000},
             "prov": {"pages": 40}} for i in range(n)]


def _est(usd_max, model=D.MODEL, n=3):
    return {"usd_max": usd_max, "parts": n, "rows": [{"usd_max": None if usd_max is None else usd_max / n}] * n,
            "models": {model: n}}


def test_default_cap_holds_with_yes_and_is_cumulative():
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d)
        rd.yes = True                                     # --yes does not lift the cap
        with mock.patch.object(rd, "estimate", return_value=_est(6.0)):
            rd._guard_cost(_jobs())                       # $6 of $10: sent
            try:
                rd._guard_cost(_jobs())                   # another $6 would make $12: refused
                raise AssertionError("cumulative cap")
            except D.DatasheetError as e:
                assert "already spent or held" in str(e) and "--max-cost-usd" in str(e), e


def test_default_cap_uses_the_rough_estimate_without_a_request():
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d)
        seen = []
        real = rd.estimate
        rd.estimate = lambda jobs, **k: (seen.append(rd.key), real(jobs, **k))[1]
        with mock.patch.object(D.requests, "post", side_effect=AssertionError("no count_tokens for the default cap")):
            rd._guard_cost(_jobs(1))
        assert seen == [None] and rd.key == KEY           # estimated with no key, and the key is put back
        rd.cfg["max_cost_usd"] = 5.0                      # a cap you set is checked exactly (count_tokens)
        seen.clear()
        with mock.patch.object(rd, "estimate", side_effect=lambda jobs, **k: (seen.append(rd.key), _est(1.0, n=1))[1]):
            rd._guard_cost(_jobs(1))
        assert seen == [KEY]


def test_unpriced_model_default_cap_sends_your_cap_refuses():
    with tempfile.TemporaryDirectory() as d:
        said = []
        rd = _reader(d)
        rd.say = said.append
        with mock.patch.object(rd, "estimate", return_value=_est(None, "qwen2.5:7b")):
            rd._guard_cost(_jobs())                       # a local model: the default cap can't stop it
            rd._guard_cost(_jobs())
        assert len(said) == 1 and "no price known for qwen2.5:7b" in said[0], said   # said once per run
        rd = _reader(d, max_cost_usd=2.0)
        with mock.patch.object(rd, "estimate", return_value=_est(None, "qwen2.5:7b")):
            try:
                rd._guard_cost(_jobs())
                raise AssertionError("a cap you set holds for an unpriced model")
            except D.DatasheetError as e:
                assert "no price known" in str(e) and "--yes" in str(e)
            rd.yes = True
            rd._guard_cost(_jobs())                       # approved as unpriced


def test_zero_means_no_cap():
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, max_cost_usd=0)
        with mock.patch.object(rd, "estimate", side_effect=AssertionError("no estimate without a cap")):
            rd._guard_cost(_jobs(50))


def test_dialog_limit_ignores_the_default_cap():
    assert AC.limit(None) == (1.0, "ask_above_usd")
    assert AC.limit({"datasheet": {"max_cost_usd": 10.0}}) == (1.0, "ask_above_usd")
    assert AC.limit({"datasheet": {"max_cost_usd": 25.0}}) == (25.0, "max_cost_usd")
    assert AC.limit({"datasheet": {"max_cost_usd": 0}}) == (1.0, "ask_above_usd")


def test_eval_run_max_cost_usd_reaches_the_reader():
    seen = {}
    real = R.EvalReader.__init__

    def spy(self, *a, **k):
        real(self, *a, **k)
        seen["cap"] = self.cfg["max_cost_usd"]
    with tempfile.TemporaryDirectory() as d, mock.patch.object(R.EvalReader, "__init__", spy):
        TE.run(d, "a")
        assert seen["cap"] == 10.0
        TE.run(d, "b", max_cost_usd=3.5)
        assert seen["cap"] == 3.5


def test_rules_file_cap_is_validated_and_documented():
    assert RU.load({"datasheet": {"max_cost_usd": 25}})["datasheet"]["max_cost_usd"] == 25
    assert "default 10.00" in RU.HELP["datasheet.max_cost_usd"] and "--yes does not lift it" in RU.HELP["datasheet.max_cost_usd"]


# ================================================================ 4. Altium .pas files
def test_pas_files_ship_as_package_data():
    toml = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    assert '"altium_scripts/*.pas"' in toml
    assert sorted(p.name for p in AN.SCRIPTS.glob("*.pas")) == sorted([AM.UNIT, AF.DIALOG_UNIT, AN.UNIT])


def test_a_crlf_checkout_gives_the_same_bytes():
    """git on Windows (autocrlf) checks the .pas out with CRLF; Altium must still get exactly one CR per line."""
    with tempfile.TemporaryDirectory() as d:
        for name in (AM.UNIT, AF.DIALOG_UNIT, AN.UNIT):
            (Path(d) / name).write_bytes((AN.SCRIPTS / name).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        want = {n: AN.script_text(n) for n in (AM.UNIT, AF.DIALOG_UNIT, AN.UNIT)}
        with mock.patch.object(AN, "SCRIPTS", Path(d)):
            for n, t in want.items():
                got = AN.script_text(n)
                assert got == t and "\r\r" not in got, n


def test_altium_menu_build_writes_the_pas_files_unchanged():
    with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
        AM.build(Path(d) / "menu", workspace=Path(d) / "ws", library=Path(d) / "lib", command="ecadlib")
        for name, text in ((AM.UNIT, AM.pas_text()), (AF.DIALOG_UNIT, AF.dialog_pas_text()), (AN.UNIT, AN.pas_text())):
            got = (Path(d) / "menu" / name).read_bytes()
            assert got == text.encode("utf-8"), name
            assert b"@VERSION@" not in got
        assert AM.pas_problems(AM.pas_text(), known=AM.unit_names(AF.dialog_pas_text()) | AM.unit_names(AN.pas_text())) == []


# ================================================================ 7. eval without a key
def test_no_key_failure_type_and_cli_line():
    pdfs = {"https://example.com/soic.pdf": TE.TB.pdf_with(["SOIC-8 package"] * 3)}
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(D.requests, "post", side_effect=AssertionError("nothing is sent without a key")):
        rep = TE.run(d, "est", http=TE.TB.PdfHTTP(pdfs))
        s = rep["summary"]
        assert s["not_run"]["cases"] >= 1 and set(s["failures"]) == {"no_key"}, s["failures"]
        assert s["overall"]["cases"] == s["ran"]["cases"] + s["not_run"]["cases"]
        md = R.report_md(rep)
        assert "Not run:" in md and "Wrong parts" not in md, md
        assert "would cost about" in md and "nothing billed" in md
        assert "no_key" in md                             # the "Why parts failed" table names it


def test_replay_never_reports_not_run():
    with tempfile.TemporaryDirectory() as d:
        rec = TE.record_dir(Path(d) / "rec", {"SOICPART": TD.GOOD, "QFNPART": TD.GOOD})
        rep = TE.run(d, "rep", replay=rec)
        assert rep["summary"]["not_run"]["cases"] == 0 and not any(c["not_run"] for c in rep["cases"])
        assert "Not run" not in R.report_md(rep)


# ================================================================ 8. unofficial sources opt-in
def _shipped():
    return mock.patch.dict(RU.DEFAULTS["providers"], unofficial=tests.SHIPPED_UNOFFICIAL)


def test_librarian_leaves_easyeda_out_by_default():
    logs = []
    with tempfile.TemporaryDirectory() as d, _shipped():
        b = Path(d) / "bom.csv"
        b.write_text(TL.BOM)
        http = TL.FakeHTTP({"C2002": TL.EE})
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}, clear=True):
            items = {i["name"]: i for i in L.run(b, [], Path(d) / "out", cache=Path(d) / "cache", http=http, log=logs.append)}
        assert any("easyeda source skipped" in str(x) for x in logs), logs
        assert not (items["EEPART1"]["source"].get("footprint") or "").startswith("easyeda"), items["EEPART1"]["source"]
        assert not [u for u in getattr(http, "calls", []) if "easyeda.com" in str(u)]
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}, clear=True):
            items = {i["name"]: i for i in L.run(b, [], Path(d) / "out2", cache=Path(d) / "cache", http=http,
                                                    log=lambda *a: None, rules={"providers": {"unofficial": True}})}
        assert (items["EEPART1"]["source"].get("footprint") or "").startswith("easyeda"), items["EEPART1"]["source"]


def test_init_flag_writes_the_choice_without_asking():
    never = lambda *_: (_ for _ in ()).throw(AssertionError("asked"))
    with tempfile.TemporaryDirectory() as d, _shipped():
        for flag, want in (("--unofficial", True), ("--no-unofficial", False)):
            out = Path(d) / f"{want}.toml"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                assert I.main(["--from", d, "-o", str(out), "--yes", flag], inp=never, environ={}) == 0
            raw = RU.parse_text(out.read_text(encoding="utf-8"), out.name)
            assert raw["providers"]["unofficial"] is want, raw.get("providers")
            assert P.unofficial_allowed(RU.load(str(out))) is want
        out = Path(d) / "none.toml"                       # --yes without the flag: nothing written, the default (off)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert I.main(["--from", d, "-o", str(out), "--yes"], inp=never, environ={}) == 0
        raw = RU.parse_text(out.read_text(encoding="utf-8"), out.name)
        assert "unofficial" not in (raw.get("providers") or {}) and not P.unofficial_allowed(RU.load(str(out)))


def test_plugins_and_triage_fence_follow_the_default():
    with _shipped():
        assert P.fence(P.ALL, None) == [c for c in P.ALL if getattr(c, "official", True)]
        assert P.fence(P.ALL, {"providers": {"unofficial": True}}) == list(P.ALL)
