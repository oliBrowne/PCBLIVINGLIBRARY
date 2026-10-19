"""Cost before and after every AI step the Altium dialog, the KiCad window and the web UI start (ecadlib.ai_cost,
Workspace.librarian_run(ai=False) / ai_quote / ai_read), and the key store: keys are read from ecadlib's own store,
never from or into the environment. Offline: the Messages API is test_datasheet_batch's fake; no paid call is made.
    python3 -m tests.test_ai_cost"""
import contextlib, json, os, tempfile
from pathlib import Path
from unittest import mock

import requests

from ecadlib import ai_cost as AC, altium_menu as AM, datasheet as D, userconfig as UC
from ecadlib.app import Workspace
from tests import test_datasheet_batch as TB

KEY = TB.KEY


@contextlib.contextmanager
def _workspace():
    """A workspace with the batch tests' BOM (two datasheet parts) and their PDFs already downloaded, a stored
    Claude key (the environment has none) and the fake API."""
    with tempfile.TemporaryDirectory() as d:
        env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
        env["ECADLIB_HOME"] = str(Path(d) / "home")
        # not --offline (that sends nothing to the model either): the part sources fail at once, as at a dead proxy,
        # and the Messages API is the fake. Not a real dead proxy: Windows retries a refused connection for seconds
        api = TB.FakeAPI(tokens=20000)
        down = mock.patch.object(requests.Session, "request", side_effect=requests.exceptions.ProxyError("no network"))
        with mock.patch.dict(os.environ, env, clear=True), TB.patched(api), down:
            UC.set_key("ANTHROPIC_API_KEY", KEY)
            ws = Workspace(str(Path(d) / "ws"), [], providers=["digikey"])
            bom = ws.save_upload("bom.csv", TB.BOM2.encode())
            for url in ("https://example.com/ds.pdf", "https://www.lcsc.com/datasheet/C9999.pdf"):
                D.PdfStore(ws.root / "http_cache", TB.PdfHTTP({url: TB.PDF}), offline=False).fetch(url)
            yield ws, bom, api
        assert "ANTHROPIC_API_KEY" not in os.environ


def test_fill_prices_the_datasheet_reads_and_sends_nothing():
    with _workspace() as (ws, bom, api):
        r = ws.librarian_run(bom, ai=False)
        assert not api.of("/v1/messages"), "a paid call before the person said yes"
        q = r["ai"]
        assert q and {"DSPART1", "DSPART2"} <= set(q["names"]) and q["parts"] == len(q["names"]), q
        assert q["model"] == "claude-opus-5-5" and q["price_known"] and q["key_set"] and q["exact"], q
        assert 0 < q["usd"] <= q["usd_max"] and q["approve_usd"] >= q["usd_max"], q
        assert "claude-opus-5-5" in q["text"] and "about $" in q["text"] and "at most $" in q["text"], q["text"]
        assert KEY not in json.dumps(r)
        held = next(x for x in r["review"] if x["part"] == "DSPART1")
        assert held["status"] == "held", held
        assert ws.ai_quote(bom)["parts"] == q["parts"] and not api.of("/v1/messages")


def test_read_needs_the_ok_and_shows_the_real_cost_next_to_the_estimate():
    with _workspace() as (ws, bom, api):
        q = ws.librarian_run(bom, ai=False)["ai"]
        for kw, why in (({}, "approve the cost first"), ({"approved_usd": q["usd_max"] / 10}, "more than")):
            try:
                ws.ai_read(bom, over_limit_ok=True, **kw)
                raise AssertionError(kw)
            except ValueError as e:
                assert why in str(e), e
        assert not api.of("/v1/messages")
        r = ws.ai_read(bom, approved_usd=q["approve_usd"], over_limit_ok=q["over_limit"])
        assert len(api.of("/v1/messages")) >= q["parts"]
        c = r["ai_cost"]
        assert c["reads"] >= q["parts"] and 0 < c["actual_usd"] <= q["usd_max"] and c["estimate_usd"] == q["usd"], c
        assert "estimated about $" in c["text"] and c["text"].startswith("Cost: $"), c["text"]
        row = next(x for x in r["review"] if x["part"] == "DSPART1")
        assert row["footprint_from"].startswith("datasheet:"), row
        sent = len(api.of("/v1/messages"))
        r = ws.ai_read(bom, approved_usd=q["approve_usd"], over_limit_ok=True)    # nothing left: nothing sent
        assert r["ai_cost"] is None and len(api.of("/v1/messages")) == sent


def test_over_the_limit_needs_a_second_yes():
    with _workspace() as (ws, bom, api):
        ws.save_rules('[datasheet]\nask_above_usd = 0.0001\n', overwrite=True)
        q = ws.librarian_run(bom, ai=False)["ai"]
        assert q["over_limit"] and q["limit_usd"] == 0.0001 and "more than your limit" in q["text"], q
        try:
            ws.ai_read(bom, approved_usd=q["approve_usd"])
            raise AssertionError("over the limit")
        except ValueError as e:
            assert "over your limit" in str(e) and not api.of("/v1/messages"), e
        ws.save_rules('[datasheet]\nmax_cost_usd = 50\nask_above_usd = 0.0001\n', overwrite=True)
        assert not ws.ai_quote(bom)["over_limit"]                                  # max_cost_usd wins when set


def test_quote_without_key_or_price():
    with _workspace() as (ws, bom, api):
        UC.remove_key("ANTHROPIC_API_KEY")
        q = ws.librarian_run(bom, ai=False)["ai"]
        assert not q["key_set"] and "No API key" in q["text"] and not api.of("/count_tokens"), q
        try:
            ws.ai_read(bom, approved_usd=99, over_limit_ok=True)
            raise AssertionError("no key")
        except ValueError as e:
            assert "No API key" in str(e)
    est = {"rows": [{"part": "X", "model": "claude-unpriced-9", "input_tokens": 1000, "usd": None, "usd_max": None}],
           "usd": None, "usd_max": None, "models": {"claude-unpriced-9": 1}}
    q = AC.quote(est, {})
    assert not q["price_known"] and q["over_limit"] and q["approve_usd"] is None and "does not know" in q["text"], q
    try:
        AC.approve(q, 5, True)
        raise AssertionError("unknown price")
    except ValueError as e:
        assert "unknown" in str(e)
    assert AC.approve(q, None, False, price_unknown_ok=True) == (0.0, True)
    assert AC.quote({"rows": []}) is None
    assert AC.money(0) == "$0 (free)" and AC.money(1.234) == "$1.23" and AC.money(0.0123) == "$0.012"
    # AD26 showed "at most $2.20" next to an approval of $2.21: the text and the approval are now one number
    assert AC.up(2.2043) == 2.21 and AC.up(2.2) == 2.2 and AC.up(0.0123) == 0.013 and AC.up(0) == 0
    for worst in (2.2043, 1.0, 0.0123, 0.5049):
        q = {"parts": 1, "model": "m", "key_set": True, "price_known": True, "usd": worst / 2, "usd_max": worst,
             "cross_check": False, "over_limit": False, "limit_usd": 5, "limit_name": "max_cost_usd"}
        q["approve_usd"] = AC.up(worst)
        assert f"at most {AC.money(q['approve_usd'])}" in AC.text(q) and q["approve_usd"] >= worst, (worst, AC.text(q))
        assert AC.approve(q, q["approve_usd"]) == (q["approve_usd"], False)


def test_altium_and_kicad_dialogs_offer_the_ai_step_with_its_cost():
    from ecadlib import kicad_menu as KM
    from tests.test_tool_packages import _plugin_module
    with _workspace() as (ws, bom, api):
        cfg = {"workspace": ws.root, "library": ws.root / "altium_library", "name": "ecadlib", "offline": False}
        res = AM.run_action(cfg, "fill", {"project": str(ws._bom_path(bom))})
        assert not api.of("/v1/messages"), "fill must not call the model"
        assert res["offer_ai"] == "1" and res["ai_model"] == "claude-opus-5-5" and res["ai_approve"], res
        assert res["ai_button"].startswith("Read with AI (about $") and "at most $" in res["ai_text"], res
        assert res["ai_over"] == "1" and "more than your" in res["ai_confirm"], res        # default limit $1.00
        with tempfile.TemporaryDirectory() as d:                   # what result.ini carries for the dialog
            AM.write_result(Path(d) / "result.ini", res)
            back = AM.read_ini(Path(d) / "result.ini")["result"]
            assert back["ai_approve"] == res["ai_approve"] and back["offer_ai"] == "1"
        try:                                                       # the menu runner passes on what the dialog approved
            AM.run_action(cfg, "ai-read", {"bom": res["ai_bom"], "approved_usd": res["ai_approve"]})
            raise AssertionError("over the limit without the second yes")
        except ValueError as e:
            assert "over your limit" in str(e) and not api.of("/v1/messages")
        res2 = AM.run_action(cfg, "ai-read", {"bom": res["ai_bom"], "approved_usd": res["ai_approve"],
                                              "over_limit_ok": res["ai_over"], "price_unknown_ok": res["ai_unknown"]})
        assert api.of("/v1/messages") and res2["ai_cost_text"].startswith("Cost: $"), res2
        rows = [res2[f"row{i}"] for i in range(1, int(res2["rows"]) + 1)]
        assert rows[0].startswith("AI estimate|about $") and rows[1].startswith("AI cost|$"), rows
        assert "estimated about" in res2["message"], res2["message"]
        # one part from Add part: its own quote and read, not the whole parts list's
        part = AM.run_action(cfg, "add-part", {"mpn": "NOTHINGLIKEIT1"})
        assert "offer_ai" not in part or part.get("ai_part") == part.get("part"), part
    mod, _ = _plugin_module()                                      # the KiCad window's command line for the AI step
    c = mod.job_command(["ecadlib"], "J", "ai-read", "b.kicad_pcb", "LM358DR", bom="uploads/x.csv", part="LM358DR",
                        approved_usd="0.42", over_limit_ok="1", price_unknown_ok="")
    assert c[-7:] == ["--bom", "uploads/x.csv", "--part", "LM358DR", "--approved-usd", "0.42", "--over-limit-ok"], c
    assert "--price-unknown-ok" not in c and "ai-read" in KM.ACTIONS and "ai-read" in AM.ACTIONS
    with mock.patch.object(AM, "ai_read", lambda cfg, req, progress=None: {"status": "ok", "req": req}) as _:
        got = KM.run_action({}, "ai-read", "b.kicad_pcb", "LM358DR", ai={"bom": "x.csv", "approved_usd": "0.42"})
    assert got["req"] == {"bom": "x.csv", "approved_usd": "0.42", "mpn": "LM358DR"}, got


def test_web_ui_shows_the_cost_and_reads_only_what_was_approved():
    from tests.test_ui import serve, wait_job
    with _workspace() as (ws, bom, api):
        srv, call = serve(ws)
        try:
            st, j = call("POST", "/api/librarian/run", {"bom": bom})
            res = wait_job(call, j["job"])
            assert res["state"] == "done" and not api.of("/v1/messages"), res
            q = res["result"]["ai"]
            assert q and q["price_known"] and q["approve_usd"], q
            st, j = call("POST", "/api/ai/quote", {"bom": bom})
            assert wait_job(call, j["job"])["result"]["ai"]["parts"] == q["parts"]
            assert call("POST", "/api/ai/read", {"bom": bom, "approved_usd": "lots"})[0] == 400
            st, j = call("POST", "/api/ai/read", {"bom": bom, "approved_usd": q["approve_usd"]})   # no second yes
            res = wait_job(call, j["job"])
            assert res["state"] == "error" and "over your limit" in res["error"] and not api.of("/v1/messages"), res
            st, j = call("POST", "/api/ai/read", {"bom": bom, "approved_usd": q["approve_usd"], "over_limit_ok": True})
            res = wait_job(call, j["job"])
            assert res["state"] == "done" and res["result"]["ai_cost"]["actual_usd"] > 0, res
            assert KEY not in json.dumps(res)
            st, page = call("GET", "/")
            for s in (b"/api/ai/read", b'id="aibox"', b"Nothing is sent until you click", b"/api/style/guide"):
                assert s in page, s
        finally:
            srv.shutdown()


def test_house_style_guide_is_priced_before_the_model_writes_it():
    from ecadlib import house_style as HS
    with _workspace() as (ws, bom, api):
        lib = Path(__file__).resolve().parent.parent / "examples" / "kicad_lib"
        r = ws.learn_house_style([str(lib)], ["Name resistors RES_<size>_<value>"], ai=True, ask_first=True)
        q = r["ai"]
        assert q["step"] == "style" and q["model"] == "claude-opus-5-5" and 0 < q["usd"] <= q["usd_max"], q
        assert "style guide" in q["text"] and not api.of("/v1/messages")
        try:
            ws.style_guide()
            raise AssertionError("no approval")
        except ValueError as e:
            assert "approve the cost first" in str(e)
        msg = {"model": "claude-opus-5-5", "stop_reason": "end_turn", "usage": {"input_tokens": 3000, "output_tokens": 500},
               "content": [{"type": "text", "text": json.dumps({"guide": ["Name resistors RES_<size>_<value>"], "settings": [],
                                                                 "pin_style": "", "description_templates": [], "conflicts": []})}]}
        with mock.patch.object(HS, "_sender", lambda inf: (lambda body: msg)):
            g = ws.style_guide(approved_usd=q["approve_usd"], over_limit_ok=True)
        c = g["ai_cost"]
        assert c["actual_usd"] == round(3000 * 4 / 1e6 + 500 * 20 / 1e6, 6) and c["estimate_usd"] == q["usd"], c


if __name__ == "__main__":
    from tests.run_all import main
    main([__name__.split(".")[-1]])
