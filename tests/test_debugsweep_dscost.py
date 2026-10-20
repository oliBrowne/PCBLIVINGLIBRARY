"""Regression tests for the debug-sweep findings DS2-13 .. DS2-25 (prices, cost quotes, the cross-check's second read).
Offline: no key, no network, no paid call.
    python3 -m tests.run_all test_debugsweep_dscost"""
import contextlib, io

from ecadlib import costs as C, datasheet as D, llm as LL, rules as RU
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
