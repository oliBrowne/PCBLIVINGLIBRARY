"""Fixes 2026-10-26 (fx16): R-FX16-1..5 of the PR #196 re-review (a section's key name never goes to another server,
embedded PDFs never beat the datasheet link, package codes at the end of the first segment, easyeda module 404s).
Plain functions: python -m tests.run_all test_fix1026_fx16. No network: nothing here talks to a model API or a web site."""
import os
from unittest import mock

from tests.fixtures_lite import lite

_KEYS = {"GW1_KEY": "sk-gw1-secret", "GW2_KEY": "sk-gw2", "PROXY_KEY": "sk-proxy-secret", "OPENAI_API_KEY": "sk-openai",
         "OR_KEY": "sk-or-secret", "ECADLIB_NO_STORED_KEYS": "1"}
_CLEAR = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY")
GW0, GW1, GW2 = "https://gw0.example.org/v1", "https://gw1.example.com/v1", "https://gw2.example.net/v1"
PROXY = "https://openai-proxy.corp.example/v1"


def _env():
    env = {k: v for k, v in os.environ.items() if k not in _CLEAR}
    env.update(_KEYS)
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


# ---------------------------------------------------------------- R-FX16-1: eval overrides keep a key only for its server
def _eval_key(rules, **kw):
    from ecadlib import llm as LL
    from ecadlib.eval.runner import ai_overrides
    ep = LL.endpoint(ai_overrides(rules, **kw), "datasheet")
    return ep["base_url"], LL.key_for(ep)


def test_eval_override_never_sends_the_datasheet_key_to_another_server():
    ds_gw1 = {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"}
    with _env():
        # --base-url gw2: GW1's key never goes to gw2
        base, key = _eval_key(_rules({"datasheet": ds_gw1}), provider="openai-compatible", base_url=GW2, model="m")
        assert base == GW2.rstrip("/") and key != "sk-gw1-secret", (base, key)
        # [ai] on gw0: GW1's key never goes to gw0
        r = _rules({"ai": {"provider": "openai-compatible", "base_url": GW0, "model": "m"}, "datasheet": ds_gw1})
        base, key = _eval_key(r, provider="openai-compatible")
        assert base == GW0 and key != "sk-gw1-secret", (base, key)
        # a corporate proxy key never goes to api.openai.com
        r = _rules({"datasheet": {"provider": "openai", "base_url": PROXY, "model": "gpt-5", "api_key_env": "PROXY_KEY"}})
        base, key = _eval_key(r, provider="openai", model="gpt-5")
        assert base == "https://api.openai.com/v1" and key == "sk-openai", (base, key)


def test_eval_override_keeps_the_datasheet_key_on_its_own_server():
    with _env():
        # the same gateway again (written out with a trailing slash): its key stays
        r = _rules({"datasheet": {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"}})
        assert _eval_key(r, provider="openai-compatible", base_url=GW1 + "/", model="m")[1] == "sk-gw1-secret"
        # [ai] already on gw1, [datasheet] only names the key: the same server
        r = _rules({"ai": {"provider": "openai-compatible", "base_url": GW1, "model": "m"},
                    "datasheet": {"api_key_env": "GW1_KEY"}})
        assert _eval_key(r, provider="openai-compatible") == (GW1, "sk-gw1-secret")
        # a preset to the same preset (default server both times)
        r = _rules({"datasheet": {"provider": "openrouter", "model": "q/q", "api_key_env": "OR_KEY"}})
        assert _eval_key(r, provider="openrouter", model="x/y")[1] == "sk-or-secret"
        # --api-key-env always wins
        r = _rules({"datasheet": {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"}})
        assert _eval_key(r, provider="openai-compatible", base_url=GW2, model="m", api_key_env="GW2_KEY")[1] == "sk-gw2"


# ---------------------------------------------------------------- R-FX16-3: [agent.proof] on its own server
def _proof(rules):
    from ecadlib import llm as LL
    ep = LL.endpoint(rules, "proof")
    return ep["base_url"], LL.key_for(ep)


def test_proof_on_its_own_server_never_gets_the_datasheet_key():
    with _env():
        r = _rules({"ai": {"provider": "openai-compatible", "base_url": GW1, "model": "m"},
                    "datasheet": {"api_key_env": "GW1_KEY"},
                    "agent": {"proof": {"provider": "openai-compatible", "base_url": GW2, "model": "m"}}})
        base, key = _proof(r)
        assert base == GW2 and key != "sk-gw1-secret", (base, key)
        r = _rules({"datasheet": {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"},
                    "agent": {"proof": {"provider": "openai-compatible", "base_url": GW2, "model": "m"}}})
        base, key = _proof(r)
        assert base == GW2 and key != "sk-gw1-secret", (base, key)
        # the proxy key goes only where the proof goes when that is the proxy ([ai]'s base_url for the same provider)
        r = _rules({"ai": {"provider": "openai", "base_url": PROXY, "model": "gpt-5"},
                    "datasheet": {"api_key_env": "PROXY_KEY"}, "agent": {"proof": {"provider": "openai", "model": "gpt-5"}}})
        assert _proof(r) == (PROXY, "sk-proxy-secret")
        # a proof sent to api.openai.com itself never gets the proxy key
        r["agent"]["proof"]["base_url"] = "https://api.openai.com/v1"
        assert _proof(r) == ("https://api.openai.com/v1", "sk-openai")


def test_proof_on_the_datasheet_server_keeps_its_key():
    with _env():
        r = _rules({"datasheet": {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"},
                    "agent": {"proof": {"provider": "openai-compatible", "model": "m"}}})
        assert _proof(r) == (GW1, "sk-gw1-secret")
        r = _rules({"datasheet": {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"},
                    "agent": {"proof": {"provider": "openai-compatible", "base_url": GW1 + "/", "model": "m"}}})
        assert _proof(r)[1] == "sk-gw1-secret"
        r = _rules({"datasheet": {"provider": "openai-compatible", "base_url": GW1, "model": "m", "api_key_env": "GW1_KEY"}})
        assert _proof(r) == (GW1, "sk-gw1-secret")


# ---------------------------------------------------------------- R-FX16-2: an embedded PDF never beats the datasheet link
_B = "https://www.maker.com/product/LM358DR"


def _link(body, docs=True):
    from ecadlib import datasheet as D
    return D.pdf_link(f"<html><body>{body}</body></html>", _B, docs=docs, part="LM358DR")


def test_pdf_link_prefers_the_datasheet_href_or_doc_link_over_an_embedded_pdf():
    # a datasheet href on another host + an iframe RoHS file named for the part: the datasheet
    assert _link("<a href='https://www.ti.com/lit/lm358.pdf'>Datasheet</a><iframe src='/rohs/LM358DR_RoHS.pdf'></iframe>") \
        == "https://www.ti.com/lit/lm358.pdf"
    assert _link("<a href='https://www.ti.com/lit/lm358.pdf'>Datasheet</a><iframe src='/rohs/LM358DR_RoHS.pdf'></iframe>",
                 docs=False) == "https://www.ti.com/lit/lm358.pdf"
    # a document-download link (no .pdf) + an embedded certificate or change notice: the document link
    got = _link("<a href='/dgdl/Infineon-IRF540N-DataSheet-v01_01-EN?fileId=5546d'>Datasheet</a>"
                "<iframe src='/compliance/rohs-cert.pdf'></iframe>")
    assert "/dgdl/" in got, got
    assert _link("<a href='/getfile?docid=ds-lm358'>Datasheet</a><embed src='/pcn/PCN-2026-04.pdf' type='application/pdf'>") \
        == "https://www.maker.com/getfile?docid=ds-lm358"
    # a viewer page with nothing else still gives its embedded PDF (R-FX13-6)
    assert _link("<iframe src='https://cdn.maker.com/docs/LM358-datasheet.pdf#page=1'></iframe>") == \
        "https://cdn.maker.com/docs/LM358-datasheet.pdf"
    assert _link("<object data='/getdoc?id=123' type='application/pdf'></object>", docs=False) == \
        "https://www.maker.com/getdoc?id=123"


def test_a_page_whose_pdf_opens_as_a_change_notice_is_not_the_datasheet():
    import tempfile
    from ecadlib import datasheet as D
    from tests import test_datasheet_batch as TB
    from tests.test_datasheet_downloads import Scripted
    pcn = TB.pdf_with(["Product Change Notification\nPCN-2026-04 LM358DR assembly site change. Affected: LM358DR "
                       "SOIC-8, LM358DGKR. Features low power, wide supply range, RoHS compliant parts."])
    good = TB.pdf_with(["LM358DR dual operational amplifier. Features low power, wide supply range.", "Pin Functions"])
    page = b"<html><body><embed src='/pcn/PCN-2026-04.pdf' type='application/pdf'></body></html>"
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({_B: [(200, page, {})], "https://www.maker.com/pcn/PCN-2026-04.pdf": [pcn]})
        try:
            got = D.PdfStore(d, http, sleep=lambda s: None).fetch(_B, "BOM", part="LM358DR", mpn="LM358DR")
        except D.DatasheetError as e:
            assert "not a datasheet" in str(e), e
        else:
            raise AssertionError(f"a change notice was taken for the datasheet: {got[1]}")
    with tempfile.TemporaryDirectory() as d:                     # a real datasheet behind the page is still taken
        page = b"<html><body><a href='/ds/lm358.pdf'>Datasheet</a></body></html>"
        http = Scripted({_B: [(200, page, {})], "https://www.maker.com/ds/lm358.pdf": [good]})
        data, meta = D.PdfStore(d, http, sleep=lambda s: None).fetch(_B, "BOM", part="LM358DR", mpn="LM358DR")
        assert data == good and meta["page_url"] == _B, meta


# ---------------------------------------------------------------- R-FX16-5: a portal 404 for EasyEDA's model host
class _R:
    def __init__(self, status, text):
        self.status_code, self.text, self.ok, self.headers = status, text, 200 <= status < 300, {}
        self.content = text.encode()


@lite
def test_a_portal_404_page_for_the_easyeda_model_host_is_not_cached(tmp_path, monkeypatch):
    from ecadlib import easyeda_3d as E3, httpcache as H
    n = []
    portal = "<!DOCTYPE html><html><body>Blocked by proxy policy</body></html>"
    h = H.CachedHTTP(tmp_path / "c", min_interval=0)
    monkeypatch.setattr(h.s, "request", lambda *a, **k: n.append(1) or _R(404, portal))
    url = E3.STEP_URL.format("0123456789abcdef0123456789abcdef")
    assert h.get(url).status == 404
    assert not h.get(url).from_cache and len(n) == 2, "a captive portal's 404 was cached for the STEP host"
    # a raw file host's own 404 page is still cached (R-FX13-3)
    u = "https://gitlab.com/kicad/libraries/kicad-symbols/-/raw/master/Device.kicad_sym"
    h.get(u)
    assert h.get(u).from_cache


# ---------------------------------------------------------------- R-FX16-4: package codes that end the first segment
def _named(mpn, own, text):
    from ecadlib import ds_route as DR, outline_docs as OD
    return OD.names_it([text], mpn, (), DR.package_tokens(DR.norm_text(own)), "")


def test_a_leaded_package_code_ending_the_part_number_does_not_refuse_the_makers_own_drawing():
    for mpn, own, doc in [
            ("MSP430G2553IPW20", "TSSOP20", "PW (R-PDSO-G20) PLASTIC SMALL OUTLINE TSSOP-20"),
            ("MSP430G2553IN20", "DIP20", "N (R-PDIP-T20) PLASTIC DUAL-IN-LINE PACKAGE DIP-20"),
            ("MSP430G2553IPW28", "TSSOP28", "PW0028A TSSOP - 1.2 mm max height TSSOP-28"),
            ("LPC1768FBD100", "QFP100", "LQFP100: plastic low profile quad flat package; 100 leads; body 14 x 14 x 1.4 mm "
                                        "SOT407-1 QFP-100"),
            ("LPC1114FBD48/302", "QFP48", "LQFP48: plastic low profile quad flat package; 48 leads; SOT313-2 QFP-48"),
            ("LPC824M201JHI33", "QFN33", "HVQFN33: plastic thermal enhanced very thin quad flat package; no leads; "
                                        "33 terminals SOT617-11 QFN-33"),
            ("PIC32MZ2048EFH144-I/PH", "QFP144", "144-Lead Thin Quad Flatpack (PH) 16x16 mm C04-155 QFP-144")]:
        assert _named(mpn, own, doc), mpn
    # a grid's code in the part number is still required: the 0.5 mm Micro FineLine M256 is no 1.0 mm FineLine 256
    assert not _named("EPM1270M256C5N", "BGA256", "F256 FineLine BGA (FBGA) package outline, 17 x 17 mm body, 1.0 mm "
                                                  "ball pitch, 256 balls BGA-256")
    assert _named("EPM1270M256C5N", "BGA256", "M256 Micro FineLine BGA (MBGA) 11 x 11 mm 0.5 mm pitch 256 balls BGA-256")


def test_a_one_letter_code_is_the_parts_own_only_before_its_body_size():
    # Intel's F169 for EP4CGX15BF14C8N (F + body size 14 mm) is the maker's own
    assert _named("EP4CGX15BF14C8N", "BGA169", "F169 FineLine BGA 14 x 14 mm 1.0 mm BGA-169")
    assert _named("EP4CE22F17C8N", "BGA256", "F256 FineLine BGA (FBGA) package outline, 17 x 17 mm body BGA-256")
    assert not _named("EP4CGX15BF14C8N", "BGA169", "U169 Ultra FineLine BGA 11 x 11 mm BGA-169")
    # TMS320F28377's F is the device's (Flash), not a package letter: another maker's F337 is not its ZWT
    assert not _named("TMS320F28377DZWTT", "BGA337", "F337 FineLine BGA 0.8 mm (other maker) BGA-337")
    assert _named("TMS320F28377DZWTT", "BGA337", "ZWT (S-PBGA-N337) PLASTIC BALL GRID ARRAY 16 x 16 mm 0.8 mm BGA-337")
