"""Fixes 2026-10-26 (reads): Oliver's open questions on PR #196. Offline: no key, no network, no model.
    python3 -m tests.run_all test_fix1026_reads"""
import requests

from ecadlib import datasheet as D, rules as RU, userconfig as UC
from tests.fixtures_lite import lite

GATEWAY = ('[ai]\nprovider = "openai-compatible"\nbase_url = "https://llm-gateway.example.com/v1"\n'
           'model = "acme-pro-2"\napi_key_env = "ACME_KEY"\ndetect = false\n')   # a remote model with no known price


def _offline(monkeypatch, sent):
    monkeypatch.setattr(UC, "secret", lambda name, env=None: {"ACME_KEY": "dummy-acme"}.get(name))

    def offline(url, *a, **k):                        # nothing leaves this test: a request is recorded and fails
        sent.append(url)
        raise requests.ConnectionError("offline test")
    monkeypatch.setattr(requests, "post", offline)
    monkeypatch.setattr(requests, "get", offline)


def _guard(tmp_path, toml, yes=False, cap=None):
    (tmp_path / "r.toml").write_text(toml, encoding="utf-8")
    rd = D.Reader(tmp_path / "out", tmp_path / "cache", rules=RU.load(str(tmp_path / "r.toml")), log=None, yes=yes)
    if cap is not None:
        rd.set_cap(cap)
    _, body = D.build_request(("base64", b"%PDF-1.4"), "U1", "SOIC-8", None, rd.model)
    rd._guard_cost([{"key": "k", "name": "U1", "model": rd.model, "body": body, "url": "u.pdf", "sync": True,
                     "prov": {"pages": 40, "sent_as": "base64"}}])
    return rd


# ---------------------------------------------------------------- item 1: an unpriced remote model is not sent
@lite
def test_1_unpriced_remote_model_under_the_default_cap_is_refused(tmp_path, monkeypatch):
    """Before: only the default $10 cap applied, so a remote model with no known price was sent with a warning."""
    sent = []
    _offline(monkeypatch, sent)
    try:
        _guard(tmp_path, GATEWAY)
        raise AssertionError("an unpriced remote model was sent under the default cap")
    except D.DatasheetError as e:
        msg = str(e)
        assert "no price known for acme-pro-2" in msg and "nothing sent" in msg, msg
        assert "--yes" in msg and 'price_in' in msg, msg            # says how to send it
    try:                                              # a cap the person set can't be held either: refused
        _guard(tmp_path, GATEWAY, cap=10)
        raise AssertionError("an explicit cap let an unpriced model through")
    except D.DatasheetError as e:
        assert "no price known for acme-pro-2" in str(e) and "price_in" in str(e), e
    _guard(tmp_path, GATEWAY, yes=True)               # --yes: sent as the person said
    _guard(tmp_path, GATEWAY + "\n[datasheet]\nmax_cost_usd = 0\n")   # no cap, chosen: nothing to hold
    priced = GATEWAY + '\n[ai.profiles."acme-pro-2"]\nprice_in = 1.0\nprice_out = 5.0\n'
    rd = _guard(tmp_path, priced)                     # its price set: held by the cap like any model
    assert rd.committed_usd > 0, rd.committed_usd
    assert sent == [], sent


@lite
def test_1_cli_read_refuses_an_unpriced_model_without_yes(tmp_path, monkeypatch):
    from tests import test_datasheet_batch as TB
    sent = []
    _offline(monkeypatch, sent)
    (tmp_path / "r.toml").write_text(GATEWAY, encoding="utf-8")
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(TB.pdf_with(["U1 regulator", "Package outline"]))
    try:
        D.main([str(pdf), "--part", "U1", "--rules", str(tmp_path / "r.toml"), "-o", str(tmp_path / "cli")])
        raise AssertionError("ecadlib datasheet read sent an unpriced model")
    except SystemExit as e:
        assert "no price known for acme-pro-2" in str(e.code) and "--yes" in str(e.code), e.code
    assert sent == [], sent


@lite
def test_1_eval_run_refuses_an_unpriced_model(tmp_path, monkeypatch):
    """eval run has no --yes: an unpriced model is held at the guard (a price in [ai.profiles] lets it run)."""
    from ecadlib.eval import runner as R
    rd = R.EvalReader(tmp_path / "lib", tmp_path / "c", "k", None, None, log=lambda *a: None)
    monkeypatch.setattr(rd, "estimate", lambda jobs: {"usd_max": None, "models": ["acme-pro-2"], "rows": [], "parts": 1})
    try:
        rd._guard_cost([{"key": "k", "name": "U1", "model": "acme-pro-2", "body": {}, "sync": True}])
        raise AssertionError("eval sent an unpriced model")
    except D.DatasheetError as e:
        assert "no price known for acme-pro-2" in str(e), e


@lite
def test_1_visual_check_refuses_an_unpriced_model_without_a_yes(tmp_path, monkeypatch):
    """[agent.proof] max_cost_usd is 0 (no cap) by default: an unpriced model was sent with no check at all."""
    from ecadlib import visual_check as VC
    got = VC.unpriced_refusal("acme-pro-2", 0.0, yes=False)
    assert got and "no price known for acme-pro-2" in got and "nothing sent" in got, got
    assert VC.unpriced_refusal("acme-pro-2", 0.0, yes=True) is None
    assert "max_cost_usd = 5" in VC.unpriced_refusal("acme-pro-2", 5.0, yes=True)   # a cap set can't be held


# ---------------------------------------------------------------- R-FP-1..3: datasheet read checks (second review of #196)
def _wire_read(name, dims, pins, other, ptype="gullwing", desc=None):
    """A synthetic model answer through process() and assess() (footprint-scripts/rw.py of the review)."""
    import json
    from ecadlib import ds_route as DR
    d = lambda nm, v: {"name": nm, "label": nm[:2], "min": str(v[0]), "nom": str(v[1]), "max": str(v[2]), "unit": "mm",
                       "page": 9, "figure": name}
    wire = {"package": {"name": name, "description": desc or name, "package_type": ptype, "drawing": name, "unresolved": [],
                        "dims": [d(k, v) for k, v in dims.items()], "other_json": json.dumps(other)},
            "pin_table": {"page": 3, "figure": "Pin functions", "rows": len(pins)},
            "pins": [{"num": n, "name": nm, "type": "passive", "side": "left", "page": 3} for n, nm in pins],
            "symbol_description": "x", "notes": [], "embedded_instructions": []}
    res = D.process(D.parse_message({"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(wire)}]}),
                    "XPART", "ds.pdf")
    return DR.assess(res), res["needs_review"]


QFP_14X20 = {"body_length": (13.9, 14.0, 14.1), "body_width": (19.9, 20.0, 20.1), "lead_span": (16.95, 17.2, 17.45),
             "lead_span_y": (22.95, 23.2, 23.45), "pitch": ("", 0.65, ""), "terminal_length": (0.73, 0.88, 1.03),
             "terminal_width": (0.22, 0.3, 0.38), "height_max": ("", "", 3.4)}


def test_rfp1_a_rectangular_qfp_read_right_passes_the_feet_check():
    """JEDEC PQFP-100 14x20: lead_span crosses body_length (footprint_gen's frame); _feet_outside paired it with
    body_width and failed every correct rectangular QFP."""
    from ecadlib import footprint_gen as F
    other = {"pin_count": 100, "pins_left_right": 30, "pins_top_bottom": 20}
    (kind, why), rev = _wire_read("PQFP-100_14x20mm_P0.65mm", QFP_14X20, [(str(i), f"P{i}") for i in range(1, 101)],
                                  other, ptype="qfp")
    assert not any("does not fit outside the body" in r for r in rev), rev
    spec = {"package_type": "qfp", **{k: {"min": v[0], "nom": v[1], "max": v[2]} for k, v in QFP_14X20.items()
                                      if k not in ("pitch", "height_max")}}
    assert D._feet_outside(spec) == [] and F.sanity(dict(spec, name="Q", pin_count=100, pins_left_right=30,
                                                         pins_top_bottom=20, pitch=0.65)) == []
    bad = dict(spec, lead_span={"min": 14.0, "nom": 14.2, "max": 14.4})    # X span read close to the 14 mm body
    assert any("lead_span max 14.4 - body_length min 13.9" in r for r in D._feet_outside(bad)), D._feet_outside(bad)


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_fix1026_reads"]))
