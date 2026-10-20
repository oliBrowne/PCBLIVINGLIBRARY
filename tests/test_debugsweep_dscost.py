"""Regression tests for the debug-sweep findings DS2-13 .. DS2-25 (prices, cost quotes, the cross-check's second read).
Offline: no key, no network, no paid call.
    python3 -m tests.run_all test_debugsweep_dscost"""
import contextlib, io, json

from ecadlib import costs as C, crosscheck as CC, datasheet as D, llm as LL, rules as RU
from ecadlib.llm import run as LR
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- DS2-13: a priced model behind a private gateway
@lite
def test_ds2_13_profile_price_holds_on_a_private_network_endpoint(tmp_path, monkeypatch):
    """A paid model behind a corporate gateway (10.x, *.internal) was priced $0 whatever [ai.profiles] said, so the
    cost cap, the cost ledger and `ecadlib costs` all saw it as free."""
    monkeypatch.setattr(LL, "_REG", {})
    monkeypatch.setattr(LR, "_INFO", {})
    toml = ('[ai]\nprovider = "openai-compatible"\nbase_url = "https://10.20.0.5:4000/v1"\nmodel = "gpt-5"\n'
            'detect = false\n[ai.profiles."gpt-5"]\nprice_in = 1.25\nprice_out = 10.0\n')
    f = tmp_path / "ecadlib.toml"
    f.write_text(toml, encoding="utf-8")
    LR.info("gpt-5", RU.load(str(f)))
    assert D.price_of("gpt-5") == (1.25, 10.0), D.price_of("gpt-5")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert C.main(["--rules", str(f)]) == 0
    assert "gpt-5: $1.25 in / $10 out" in out.getvalue(), out.getvalue()


# ---------------------------------------------------------------- DS2-16 / DS2-25: the cross-check's second read
def _second_read(nom, mn="", mx="", unit="mm", name="body_length"):
    """A second-read answer (crosscheck.output_schema's dims list) with one dimension, as the API returns it."""
    pkg = {"name": "VQFN-16", "package_type": "qfn", "pin_count": 16, "unresolved": [],
           "dims": [{"name": name, "label": "D", "unit": unit, "min": mn, "nom": nom, "max": mx}]}
    return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps({"package": pkg, "pins": [], "embedded_instructions": []})}]}


def test_ds2_16_second_read_takes_a_value_printed_with_its_tolerance():
    """The first read takes '3±0.1' and '2.9~3.1' as min/nom/max; the second read threw the same text away as 'not a
    finite number', so every such part ended 'unchecked' after a paid second read."""
    for nom in ("3±0.1", "2.9~3.1", "3 +0.1/-0.1"):
        got = CC.parse(_second_read(nom))[0]["body_length"]
        assert got == (3.0, 2.9, 3.1), (nom, got)


# ---------------------------------------------------------------- DS2-17: ask_above_usd = 0 and the house style guide
def test_ds2_17_style_guide_quote_asks_twice_when_ask_above_is_zero():
    """ask_above_usd = 0 means 'ask twice for any paid request' (DS-46); the style guide's quote let one yes through."""
    from ecadlib import ai_cost as AC
    q = AC.style_quote("You write a house style guide.", "the scan", {"type": "object"}, "claude-opus-5-5",
                       {"datasheet": {"ask_above_usd": 0}})
    assert q["usd_max"] > 0 and q["over_limit"] is True, q
    try:
        AC.approve(q, q["approve_usd"])
        raise AssertionError("a paid style guide was approved with one yes under ask_above_usd = 0")
    except ValueError as e:
        assert "confirm the higher cost first" in str(e), e


# ---------------------------------------------------------------- DS2-18: a dated Claude id on the cost sheet
@lite
def test_ds2_18_cost_sheet_prices_a_dated_claude_id_at_its_alias_rate(tmp_path, monkeypatch):
    """The reader prices claude-haiku-4-5-20251001 at the alias's $1 / $5 (DS-41); `ecadlib costs` said 'no price known'."""
    monkeypatch.chdir(tmp_path)                       # no ecadlib.toml found from here: the defaults
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = C.main(["--model", "claude-haiku-4-5-20251001"])
    assert rc == 0 and "claude-haiku-4-5-20251001: $1 in / $5 out" in out.getvalue(), (rc, out.getvalue())


# ---------------------------------------------------------------- DS2-23: an endpoint:model cross_check_model
@lite
def test_ds2_23_quote_prices_an_endpoint_cross_check_model_not_yet_used(tmp_path, monkeypatch):
    """With cross_check_model = "local:qwen2.5:7b" (free) or "oai:gpt-6-luna" (a built-in price) the dialogs' quote said
    'price unknown' for a priced Claude read, and approving that took the cap off (cap 0, yes)."""
    from ecadlib import ai_cost as AC
    monkeypatch.setattr(LL, "_REG", {})               # a fresh process: nothing has used the cross-check model yet
    monkeypatch.setattr(LR, "_INFO", {})
    est = {"rows": [{"part": "P", "model": "claude-opus-5-5", "input_tokens": 20000}], "mode": "synchronous",
           "usd": 0.1, "usd_max": 0.4, "models": {"claude-opus-5-5": 1}}
    ai = {"detect": False, "endpoints": {"local": {"provider": "ollama"}, "oai": {"provider": "openai"}}}
    q = AC.quote(est, RU.load({"ai": ai, "datasheet": {"cross_check_model": "local:qwen2.5:7b"}}))
    assert q["price_known"] and q["usd_max"] == 0.4, q              # a local second read costs $0
    assert AC.approve(q, q["approve_usd"]) == (0.4, False)           # held to what was approved
    q = AC.quote(est, RU.load({"ai": ai, "datasheet": {"cross_check_model": "oai:gpt-6-luna"}}))
    assert q["price_known"] and q["usd_max"] > 0.4, q
