"""Fixes 2026-10-26 (fx13): R-FX13-1..7 of the second PR #196 review (key names across providers, httpcache 404s,
outline package codes, viewer pages). Plain functions: python -m tests.run_all test_fix1026_fx13.
No network: nothing here talks to a model API or a web site."""
import os
from unittest import mock

from tests.fixtures_lite import lite

_ENV = {"OR_KEY": "sk-or-SECRET", "CLAUDE_KEY": "sk-ant-mine", "ECADLIB_NO_STORED_KEYS": "1"}
_CLEAR = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "MY_OPENAI")


def _env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in _CLEAR}
    env.update(_ENV)
    env.update(extra or {})
    return mock.patch.dict(os.environ, env, clear=True)


def _rules(user):
    import copy
    from ecadlib import rules as RU
    r = copy.deepcopy(RU.DEFAULTS)
    for s, v in user.items():
        if s == "agent":
            r["agent"]["proof"].update(v["proof"])
        else:
            r.setdefault(s, {})
            r[s].update(v)
    return r


# ---------------------------------------------------------------- R-FX13-1: eval --provider drops another provider's key
def test_eval_provider_anthropic_drops_the_datasheet_key_of_another_provider():
    from ecadlib import datasheet as D, llm as LL
    from ecadlib.eval.runner import ai_overrides
    r = _rules({"datasheet": {"provider": "openrouter", "model": "qwen/qwen3", "api_key_env": "OR_KEY"}})
    with _env():
        got = ai_overrides(r, provider="anthropic")
        ep = LL.endpoint(got, "datasheet")
        assert ep["provider"] == "anthropic" and ep["key_env"] != "OR_KEY", ep
        assert LL.key_for(ep) is None
        try:
            key = D.require_key(None, got)
        except D.DatasheetError:
            key = None
        assert key != "sk-or-SECRET", "the OpenRouter key went to Anthropic"
        # --api-key-env wins over [datasheet]'s key name
        got = ai_overrides(r, provider="anthropic", api_key_env="CLAUDE_KEY")
        assert D.require_key(None, got) == "sk-ant-mine"
        ep = LL.endpoint(got, "datasheet")
        assert ep["key_env"] == "CLAUDE_KEY" and LL.key_for(ep) == "sk-ant-mine", ep
        # --api-key-env with no --provider wins too
        got = ai_overrides(_rules({"datasheet": {"api_key_env": "OR_KEY"}}), api_key_env="CLAUDE_KEY")
        assert D.require_key(None, got) == "sk-ant-mine"
        # the same provider keeps the section's own key name
        got = ai_overrides(r, provider="openrouter", model="x/y")
        assert LL.endpoint(got, "datasheet")["key_env"] == "OR_KEY"
        mine = _rules({"datasheet": {"api_key_env": "CLAUDE_KEY"}})
        assert D.require_key(None, ai_overrides(mine, provider="anthropic")) == "sk-ant-mine"


# ---------------------------------------------------------------- R-FX13-2: the proof key comes from the proof endpoint
def test_visual_proof_on_anthropic_never_takes_the_datasheet_key_of_another_provider():
    from ecadlib import llm as LL, visual_check as V
    r = _rules({"datasheet": {"provider": "openrouter", "model": "qwen/qwen3", "api_key_env": "OR_KEY"},
                "agent": {"proof": {"provider": "anthropic", "model": "claude-opus-5-5"}}})
    with _env():
        ep = LL.endpoint(r, "proof")
        cfg = V._settings(r)
        assert ep["provider"] == "anthropic" and cfg["api_key_env"] == ep["key_env"] != "OR_KEY", cfg
    # the whole proof path: no Anthropic key -> skipped, nothing sent (the OpenRouter key never goes to Anthropic)
    from tests import test_visual_check as TV
    calls = []
    with TV.setup(key=None) as d, _env(), mock.patch.object(V.D.requests, "post", TV.fake_post(TV.message(TV.answer()), calls)):
        pr = V.prove(TV.item(d), d, r, out_dir=d / "out")
        assert pr["verdict"] == "skipped" and not calls, (pr["verdict"], pr["reason"],
                                                          [c["headers"].get("x-api-key") for c in calls])
        # with a Claude key, that key is the one sent
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-real"
        V.prove(TV.item(d), d, r, out_dir=d / "out2")
        assert calls and all(c["headers"].get("x-api-key") == "sk-ant-real" for c in calls), calls[0]["headers"]


# ---------------------------------------------------------------- R-FX13-7: proof provider vs [datasheet]'s resolved one
def test_proof_on_the_datasheet_provider_by_default_keeps_its_key_name():
    from ecadlib import llm as LL
    r = _rules({"ai": {"provider": "openai", "model": "gpt-5"}, "datasheet": {"api_key_env": "MY_OPENAI"},
                "agent": {"proof": {"provider": "openai", "model": "gpt-5"}}})
    with _env({"MY_OPENAI": "sk-openai-mine"}):
        for s in ("datasheet", "proof"):
            ep = LL.endpoint(r, s)
            assert ep["key_env"] == "MY_OPENAI" and LL.key_for(ep) == "sk-openai-mine", (s, ep)
        # a proof on another provider still drops it
        r2 = _rules({"ai": {"provider": "openai", "model": "gpt-5"}, "datasheet": {"api_key_env": "MY_OPENAI"},
                     "agent": {"proof": {"provider": "openrouter", "model": "x/y"}}})
        assert LL.endpoint(r2, "proof")["key_env"] == "OPENROUTER_API_KEY"
        # no [ai] provider: [datasheet] defaults to anthropic, so an anthropic proof keeps a Claude key name
        r3 = _rules({"datasheet": {"api_key_env": "CLAUDE_KEY"}, "agent": {"proof": {"provider": "anthropic"}}})
        assert LL.endpoint(r3, "proof")["key_env"] == "CLAUDE_KEY"


# ---------------------------------------------------------------- R-FX13-3: GitLab's own 404 page for a raw file is cached
class _R:
    def __init__(self, status, text):
        self.status_code, self.text, self.ok = status, text, 200 <= status < 300


_GL404 = "<!DOCTYPE html>\n<html class=\"gl-light\" lang=\"en\"><head><title>Not Found · GitLab</title></head><body>404</body></html>"


@lite
def test_gitlab_404_page_for_a_missing_kicad_file_is_cached_and_read_offline(tmp_path, monkeypatch):
    from ecadlib import httpcache as H, kicad_import as K
    n = []
    h = H.CachedHTTP(tmp_path / "c", min_interval=0)
    monkeypatch.setattr(h.s, "request", lambda *a, **k: n.append(1) or _R(404, _GL404))
    path = "Package_SO.pretty/SOIC-8_Nope.kicad_mod"
    assert K.fetch(h, "kicad-footprints", path) is None
    assert K.fetch(h, "kicad-footprints", path) is None
    assert len(n) == 1, f"{len(n)} requests: the origin's 404 was not cached"
    for u in ("https://gitlab.com/kicad/libraries/kicad-symbols/-/raw/master/Device.kicad_sym",
              "https://example.com/models/X.step"):
        h.get(u)
        assert h.get(u).from_cache, u
    off = H.CachedHTTP(tmp_path / "c", min_interval=0, offline=True)
    assert K.fetch(off, "kicad-footprints", path) is None              # offline: the cached "not there", no raise
    # an API (JSON) URL keeps the proxy-page protection
    api = "https://api.digikey.com/products/v4/search/LM358DR/productdetails"
    h.get(api)
    assert not h.get(api).from_cache
    assert not h.get("https://example.com/api/v1/parts.json").from_cache


# ---------------------------------------------------------------- R-FX13-4: a maker's own outline code is no other package
def _taken(mpn, own, text):
    from ecadlib import ds_route as DR, outline_docs as OD
    return OD.names_it([text], mpn, (), DR.package_tokens(DR.norm_text(own)), "")


def test_a_makers_own_outline_code_does_not_hold_the_right_package_document():
    right = [("XCZU3EG-1SBVA484E", "BGA484", "SBVA484 package: BGA-484, 19 x 19 mm, 0.8 mm pitch"),
             ("XC9572XL-10VQG64C", "QFP64", "VQG64 package: TQFP-64 10 x 10 mm"),
             ("XC95144XL-10TQG100C", "QFP100", "TQG100 package: TQFP-100 14 x 14 mm"),
             ("EP4CE22F17C8N", "BGA256", "F256 FineLine BGA-256 17 x 17 mm 1.0 mm pitch"),
             ("5CEBA4F23C7N", "BGA484", "F484 FineLine BGA-484 23 x 23 mm"),
             ("LTC6811HG-1", "SSOP48", "48-Lead SSOP-48 (G48) package"),
             ("ADXL345BCCZ", "LGA14", "14-Terminal LGA-14 (CC-14-1)"),
             ("ADM3053BRWZ", "SO20", "20-Lead SOIC-20 wide body (RW-20)"),
             ("ADA4805-2ARMZ", "MSOP10", "10-Lead MSOP-10 (RM-10)"),
             ("AD7124-4BRUZ", "TSSOP24", "24-Lead TSSOP-24 (RU-24)"),
             ("TL074CDR", "SO14", "D (R-PDSO-G14) PLASTIC SMALL OUTLINE SOIC-14"),
             ("TPS7A4701RGWR", "QFN20", "RGW (S-PVQFN-N20) QFN-20 5 x 5"),
             ("TMS320F28035PAGT", "QFP64", "PAG (S-PQFP-G64) TQFP-64 10 x 10"),
             ("MCP2515-I/SO", "SO18", "18-Lead SOIC-18 (SO) 7.50 mm C04-051")]
    for mpn, own, text in right:
        assert _taken(mpn, own, text), (mpn, text)
    # another maker's code for a ball grid of this count is still refused when the part number names no package
    assert not _taken("XQ7Z100-1I", "BGA256", "Package outline FTG256 fine-pitch BGA 17 x 17 mm (BGA-256), DIM MIN MAX")
    assert not _taken("XQ7Z100-1I", "BGA256", "256-Pin FineLine BGA package outline FG256, 1.0 mm pitch, BGA-256")


def test_the_package_code_with_a_two_digit_speed_grade_or_four_letters_is_read():
    from ecadlib import ds_route as DR, outline_docs as OD
    for mpn, own, code in (("XC9572XL-10VQG64C", "QFP64", "VQG64"), ("XCZU3EG-1SBVA484E", "BGA484", "SBVA484")):
        assert OD.mpn_package_code(mpn, DR.package_tokens(own)) == code, (mpn, OD.mpn_package_code(mpn, DR.package_tokens(own)))


# ---------------------------------------------------------------- R-FX13-5: logic part numbers, whole-token codes
def test_logic_part_numbers_carry_no_package_code_and_codes_match_whole_tokens():
    from ecadlib import ds_route as DR, outline_docs as OD
    for mpn, own in (("SN74HC14N", "DIP14"), ("SN74HC14DR", "SO14"), ("SN74HC14PWR", "TSSOP14"), ("CD74HC14E", "DIP14"),
                     ("MC74HC14ADR2G", "SO14"), ("SN74LVC14APWR", "TSSOP14"), ("MSP430G2553IPW20R", "TSSOP20")):
        got = OD.mpn_package_code(mpn, DR.package_tokens(own))
        assert got is None, (mpn, got)
    for mpn, own, text in (("SN74HC14DR", "SO14", "SOIC-14 D0014A package"), ("SN74HC14N", "DIP14", "PDIP-14 N0014A package"),
                           ("CD74HC14E", "DIP14", "PDIP-14 N0014A package"), ("MC74HC14ADR2G", "SO14", "SOIC-14 CASE 751A-03"),
                           ("SN74LVC14APWR", "TSSOP14", "TSSOP-14 PW0014A package"),
                           ("MSP430G2553IPW20R", "TSSOP20", "PW (R-PDSO-G20) TSSOP-20 package PW0020A")):
        assert _taken(mpn, own, text), (mpn, text)
    # a code is printed as a whole token: "17 x 17 mm 256-ball" holds no M256, "lead 100" no FBD100
    for mpn, own, text in (("EPM1270M256C5N", "BGA256", "FineLine BGA-256 package, body 17 x 17 mm 256-ball"),
                           ("EPM1270M256", "BGA256", "FineLine BGA-256 package, 17 x 17 mm, pitch 1.0 mm 256 balls"),
                           ("LPC1768FBD100", "QFP100", "LQFP-100 package of another maker: 14 x 14 mm, lead 100, 0.5 mm pitch")):
        assert not _taken(mpn, own, text), (mpn, text)
    assert _taken("EPM1270M256C5N", "BGA256", "M256 Micro FineLine BGA-256 package outline, 0.5 mm pitch")
    assert _taken("LPC1768FBD100", "QFP100", "LQFP100 package FBD100, 14 x 14 mm, SOT407-1")
