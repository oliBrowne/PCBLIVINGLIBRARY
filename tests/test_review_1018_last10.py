"""Review 2026-10-18 of PRs #86-#94: MCP confinement through save_rules, slow job results kept, one BOM lock per
file, batch pricing in `eval run`, dimensions printed with BSC/TYP/(ref), the exposed-pad name rule, the web UI
favicon and the Nexar token advice. Run:  python3 -m tests.test_review_1018_last10"""
import json, re, tempfile, threading
from pathlib import Path

from ecadlib import crosscheck as C, datasheet as D, mcp_server as M
from ecadlib.app import Workspace
from ecadlib.eval import runner as R
from tests.test_mcp_clients import _call, _text

REPO = Path(__file__).resolve().parent.parent


def test_confined_save_rules_cannot_point_outside_the_workspace():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as out:
        ws = Workspace(d)
        ws.mcp_options = M.Options(confine=True)
        for toml in (f'[style]\nprofile = "{out}/pwn.json"\n', '[style]\nprofile = "../pwn.json"\n',
                     f'[agent.watch]\nsources = [{{type = "folder", ref = "{out}"}}]\n', '[style]\nprofile = "C:\\\\x\\\\p.json"\n'):
            r = _call(ws, "save_rules", {"toml": toml, "overwrite": True})["result"]
            assert r["isError"] and "outside the workspace" in _text({"result": r}), (toml, r)
        assert not Path(d, "ecadlib.toml").exists()
        assert M._rule_paths('[style]\nprofile = "styles/house.json"\n[a]\nb = "https://x/y"\n') == []
        ws.mcp_options = M.Options()                                   # stdio without --confine: allowed
        r = _call(ws, "save_rules", {"toml": f'[style]\nprofile = "{out}/p.json"\n', "overwrite": True})["result"]
        assert "outside the workspace" not in _text({"result": r}), r


def test_slow_job_result_survives_quick_calls():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        ws.mcp_options = M.Options(wait=0.2)
        gate, real = threading.Event(), M.call_tool

        def slow(ws_, name, args, progress=None):
            if name == "librarian_run":
                gate.wait(5)
                return {"ready": 7}
            return real(ws_, name, args, progress)
        M.call_tool = slow
        try:
            job = json.loads(_text(_call(ws, "librarian_run", {"bom": "b.csv"})))
            for i in range(25):
                _call(ws, "workspace_info", {}, i=10 + i)
            busy = _call(ws, "release_check", {"bom": "./b.csv"})["result"]
            assert busy["isError"] and job["job"] in _text({"result": busy}), "./b.csv is the same BOM"
            gate.set()
            r = _call(ws, "job_status", {"id": job["job"], "wait_seconds": 5})
            assert '"ready":7' in _text(r).replace(" ", ""), _text(r)
        finally:
            gate.set()
            M.call_tool = real


def test_eval_batch_prices_earlier_answers_by_how_they_were_sent():
    u = {"input_tokens": 100000, "output_tokens": 1000}
    m = "claude-sonnet-5-5"
    one = R.usd(m, u)
    case = {"usage": u, "model": m, "batch": True, "via": "batch",
            "attempts": [{"model": m, "usage": u, "via": "batch"}, {"model": m, "usage": u, "via": "batch"}]}
    assert abs(R._cost(case, m)[2] - one) < 1e-6, "two half-price answers cost one full one"
    case["attempts"][0]["via"] = "live"
    assert abs(R._cost(case, m)[2] - 1.5 * one) < 1e-6
    del case["attempts"][0]["via"]                                     # an older recording: the kept answer's way
    assert abs(R._cost(case, m)[2] - one) < 1e-6
    src = (REPO / "ecadlib/eval/runner.py").read_text()
    assert re.search(r"again = run\([^)]*batch=batch\)", src, re.S), "repeats keep --batch"


def test_wire_dimensions_as_printed():
    for v, want in (("0.50 BSC", 0.5), ("1.27 TYP", 1.27), ("0.40 REF", 0.4), ("(0.2)", 0.2), ("\u22120.05", -0.05),
                    ("[0.3]", 0.3), ("0.5 max", 0.5), ("1.2 mm", 1.2), ("", None)):
        assert D._wire_num(v, "x") == want, (v, D._wire_num(v, "x"))
    try:
        D._wire_num("see note 3", "x")
    except D.DatasheetError:
        pass
    else:
        raise AssertionError("text is still refused")


def test_exposed_pad_name_only_stands_in_for_ground_or_pad():
    for a, b in (("TAB", "VIN"), ("GND (pad)", "OUT"), ("EN", "PAD")):
        assert not C.names_agree(a, b), (a, b)
    for a, b in (("GND", "PowerPAD"), ("EP", "VSS"), ("TAB", "GND"), ("PGND", "EPAD"), ("K", "cathode (K)")):
        assert C.names_agree(a, b), (a, b)


def test_ui_favicon_and_token_advice():
    for f in ("index.html", "setup.html"):
        s = (REPO / "ecadlib/ui/static" / f).read_text()
        href = re.search(r'href="(data:image/svg\+xml,[^"]*)"', s).group(1)
        assert "#" not in href, f"{f}: a bare # cuts the data URI"
        assert "NEXAR_TOKEN: restart" not in s
    from ecadlib import app
    import ecadlib.userconfig as UC
    real = UC.secret
    UC.secret = lambda k: None
    try:
        app._nexar_token()
    except ValueError as e:
        assert "setup page" in str(e) and "before starting" not in str(e), e
        assert str(e).split(":")[0] in (REPO / "docs/START_HERE.md").read_text(), "START_HERE quotes the real message"
    finally:
        UC.secret = real


def test_small_paid_estimate_is_not_shown_as_free_and_style_quote_checks_the_key():
    from ecadlib import ai_cost as AC
    assert AC.money(0) == "$0 (free)" and AC.money(0.0004) == "under $0.001" and AC.money(0.0034) == "$0.003"
    q = AC.style_quote("s", "u", {}, "claude-haiku-5-5", key_set=False)
    assert q["key_set"] is False and "No API key" in q["text"], q
    assert AC.style_quote("s", "u", {}, "claude-haiku-5-5")["key_set"] is True
