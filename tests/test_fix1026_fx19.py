"""Fixes 2026-10-26 (fx19): R-FX19-1..5 of the PR #196 second re-review (a web-searched package drawing must be this
part's body, compliance documents are not datasheets, eval's --base-url never carries [ai]'s key to another server,
one rule for a "User Manual" whatever the route, openai and openai-compatible on one server are one server).
Plain functions: python -m tests.run_all test_fix1026_fx19. No network: synthetic PDFs, a fake model and a fake search."""
import copy, os, re
from pathlib import Path
from unittest import mock

from tests.fixtures_lite import lite

MPN = "LPC1768FBD100"
DS = "https://maker.example/ds/lpc17xx.pdf"
DWG = "https://other.example/pkg/qfp_outline.pdf"
OWN_LINE = f"{MPN} LQFP100 plastic low profile quad flat package; 100 leads; body 14 x 14 x 1.4 mm SOT407-1"
G_14x20 = dict(pitch=0.65, D1=20.0, E1=14.0, D=23.2, E=17.2, lr=30, tb=20, h=3.1)
G_RIGHT = dict(pitch=0.5, D1=14.0, E1=14.0, D=16.0, E=16.0, lr=25, tb=25, h=1.6)
DOC_14x20 = ["QFP100: plastic quad flat package; 100 leads; body 14 x 20 x 2.7 mm QFP-100",
             "PACKAGE OUTLINE DIMENSIONS (mm)", "DIM MIN NOM MAX",
             "A 2.70 3.10 D 23.20 D1 20.00 E 17.20 E1 14.00 e 0.65 b 0.22 0.38 L 0.73 1.03"]
DOC_RIGHT = ["LQFP100: plastic low profile quad flat package; 100 leads; body 14 x 14 x 1.4 mm SOT407-1 QFP-100",
             "PACKAGE OUTLINE DIMENSIONS (mm)", "DIM MIN NOM MAX",
             "A 1.60 D 16.00 D1 14.00 E 16.00 E1 14.00 e 0.5 b 0.17 0.27 L 0.45 0.75"]


def _ext(g, blank):
    from tests import test_datasheet as TD
    dim, f = TD.dim, "package outline"
    e = copy.deepcopy(TD.GOOD)
    p = e["package"]
    p.update({"name": f"LQFP-100_{g['E1']:g}x{g['D1']:g}mm_P{g['pitch']:g}mm", "package_type": "qfp", "pin_count": 100,
              "description": f"QFP-100 {g['E1']:g}x{g['D1']:g} mm {g['pitch']} mm pitch",
              "pitch": dim("e", None, g["pitch"], None, page=4, figure=f),
              "body_length": dim("D1", None, g["D1"], None, page=4, figure=f),
              "body_width": dim("E1", None, g["E1"], None, page=4, figure=f),
              "lead_span": dim("E", None, g["E"], None, page=4, figure=f),
              "lead_span_y": dim("D", None, g["D"], None, page=4, figure=f),
              "terminal_length": dim("L", 0.45, 0.6, 0.75, page=4, figure=f),
              "terminal_width": dim("b", 0.22, 0.3, 0.38, page=4, figure=f),
              "height_max": dim("A", None, None, g["h"], page=4, figure=f), "pins_left_right": g["lr"],
              "pins_top_bottom": g["tb"], "exposed_pad": None, "unresolved": [], "drawing": "QFP outline"})
    if blank:
        for k in ("body_length", "body_width", "lead_span", "lead_span_y", "pitch", "terminal_length",
                  "terminal_width", "height_max"):
            p[k] = None
        p["unresolved"], p["name"] = ["body_length", "body_width", "lead_span", "pitch"], "LQFP-100"
    sides = ["left", "bottom", "right", "top"]
    e["pins"] = [TD.pin(i, f"P0_{i}", "io", sides[min((i - 1) // 25, 3)]) for i in range(1, 101)]
    for x in e["pins"]:
        x["page"] = 2
    e["pin_table"] = {"page": 2, "figure": "Table 4. Pin description", "rows": 100}
    e["symbol_description"] = f"{MPN} microcontroller"
    return e


def _e2e(tmp_path, doc, g, own_line=OWN_LINE, cross_check=None):
    """The reviewer's wrongbody_e2e.py: a datasheet with no drawing, a web search that finds `doc`, a fake model whose
    first read leaves the body blank and whose read with the drawing appended gives geometry g."""
    from ecadlib import datasheet as D
    from tests.test_ds_route import pdf_lines, ModelAPI, HAIKU
    from tests import test_datasheet_batch as TB, test_librarian as TL
    from tests.test_ds_search import Client, _answer
    datasheet = [[f"{MPN} 32-bit ARM Cortex-M3 microcontroller; up to 512 kB flash", "Ordering information", own_line],
                 ["Pinning information", "Table 4. Pin description"]
                 + [f"P0_{i} {i} I/O general purpose digital input/output pin" for i in range(1, 30)],
                 ["Electrical characteristics", "supply voltage 2.4 V to 3.6 V, operating temperature -40 to 85 C",
                  "Supply current 30 mA typical at 100 MHz; deep power-down 2 uA; flash endurance 100000 cycles"]]
    api = ModelAPI(lambda m, n: _ext(g, n == 3))
    cl = Client(_answer([DWG]))
    bom = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
           f"U1,1,{MPN},,NXP,{MPN},,MCU,{DS}\n")
    d = str(tmp_path)
    http = TB.PdfHTTP({DS: pdf_lines(datasheet), DWG: pdf_lines([doc])})
    ds_cfg = {"web_search": True, "web_search_model": HAIKU, **({"cross_check": cross_check} if cross_check else {})}
    rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=http, log=None, client=cl, sleep=lambda s: None,
                  poll=0, rules={"datasheet": ds_cfg})
    with TB.patched(api):
        items, _ = TL.run(d, http=http, bom=bom, reader=rd)
    it = items[MPN] if MPN in items else list(items.values())[0]
    built = list((Path(d) / "out").rglob("*.kicad_mod"))
    return it, built, api


def _tried(it):
    return " | ".join(str(x) for x in (it.get("librarian") or {}).get("tried") or [])


# ---------------------------------------------------------------- R-FX19-1: a searched drawing must be this part's body
def test_printed_body_reads_the_ordering_line_of_this_package_only():
    from ecadlib import ds_route as DR, outline_docs as OD
    own = DR.package_tokens("LQFP100")
    got = OD.printed_body([OWN_LINE, f"LPC1768FET100 TFBGA100 plastic thin fine-pitch ball grid array; body 9 x 9 x 0.7 mm "
                                     "SOT926-1"], MPN, own)
    assert got == {"bodies": [[14.0, 14.0]], "pitches": [], "outlines": ["SOT407-1"]}, got
    ti = OD.printed_body(["MSP430G2x53IPW28 TSSOP (28) 9.70 mm × 4.40 mm"], "MSP430G2553IPW28",
                         DR.package_tokens("TSSOP28"))
    assert ti["bodies"] == [[4.4, 9.7]], ti


def test_wrong_body_or_pitch_documents_are_told_apart_from_the_right_one():
    """od_extra.py's wrong-body documents, each against what the part's own datasheet prints for its package."""
    from ecadlib import ds_route as DR, outline_docs as OD
    cases = [  # (mpn, own, the datasheet's line, wrong documents, a right document)
        (MPN, "QFP100", OWN_LINE,
         ["QFP100 plastic quad flat package 100 leads body 14 x 20 x 2.7 mm 0.65 mm pitch (other maker) QFP-100",
          "PQFP 100-lead 14x20 mm MQFP-100 0.65 mm (Microchip PF)", "LQFP 100 20 x 20 mm 0.65 pitch QFP-100"],
         "LQFP100 14 x 14 x 1.4 mm SOT407-1 QFP-100"),
        ("LPC1114FBD48/302", "QFP48", "LPC1114FBD48/302 LQFP48 plastic low profile quad flat package; 48 leads; "
         "body 7 x 7 x 1.4 mm SOT313-2", ["TQFP48 9 x 9 mm 0.65 mm pitch (other maker) QFP-48"], "LQFP48 7 x 7 mm QFP-48"),
        ("LPC824M201JHI33", "QFN33", "LPC824M201JHI33 HVQFN33 plastic thermal enhanced very thin quad flat package; "
         "no leads; 33 terminals; body 5 x 5 x 0.85 mm SOT1210-1", ["QFN33 4 x 4 mm 0.4 mm pitch (other maker) QFN-33"],
         "HVQFN33 5 x 5 x 0.85 mm SOT1210-1"),
        ("PIC32MZ2048EFH144-I/PH", "QFP144", "144-Lead Thin Quad Flatpack (PH) - 16x16x1 mm Body TQFP144 0.40 mm pitch",
         ["LQFP144 20 x 20 mm 0.5 mm pitch (ST) QFP-144"], "TQFP144 16 x 16 x 1 mm 0.40 mm pitch"),
        ("MSP430G2553IPW28", "TSSOP28", "MSP430G2x53IPW28 TSSOP (28) 9.70 mm × 4.40 mm",
         ["TSSOP28 9.7 x 6.1 mm 0.65 (wide 6.1 mm body) TSSOP-28"], "TSSOP28 9.7 x 4.4 mm 0.65 mm pitch"),
        ("STM32F407VGT6", "QFP100", "LQFP100 14 x 14 mm STM32F407VG",
         ["QFP100 body 14 x 20 x 2.7 mm 0.65 mm pitch QFP-100"], "LQFP100 14 x 14 x 1.4 mm, 0.5 mm pitch"),
    ]
    for mpn, own_s, line, wrong, right in cases:
        own = DR.package_tokens(DR.norm_text(own_s))
        facts = OD.printed_body([line], mpn, own)
        assert OD.has_facts(facts), (mpn, facts)
        for doc in wrong:
            assert OD.body_mismatch(OD.printed_body([doc], mpn, own), facts), (mpn, doc, facts)
        found = OD.printed_body([right], mpn, own)
        assert OD.body_mismatch(found, facts) is None and OD.body_tied(found, facts), (mpn, right, found, facts)


def test_a_read_of_another_body_is_held_by_the_datasheets_own_numbers():
    from ecadlib import outline_docs as OD
    facts = {"bodies": [[14.0, 14.0]], "pitches": [0.5], "outlines": ["SOT407-1"]}
    assert OD.read_mismatch({"body_length": 20.0, "body_width": 14.0, "pitch": 0.65}, facts)
    assert OD.read_mismatch({"body_length": 14.0, "body_width": 14.0, "pitch": 0.8}, facts)
    assert OD.read_mismatch({"body_length": 14.0, "body_width": 14.0, "pitch": 0.5}, facts) is None
    assert OD.read_mismatch({"body_length": None, "body_width": None, "pitch": None}, facts) is None
    # crosscheck.critical's (min, nom, max)
    assert OD.read_mismatch({"body_length": (19.9, 20.0, 20.1), "body_width": (14.0, 14.0, 14.0), "pitch": None}, facts)
    assert OD.read_mismatch({"body_length": (13.9, 14.0, 14.1), "body_width": (None, 14.0, None), "pitch": None},
                            facts) is None


@lite
def test_e2e_a_14x20_drawing_is_refused_for_a_14x14_part(tmp_path):
    it, built, _ = _e2e(tmp_path, DOC_14x20, G_14x20)
    assert not built, built
    assert (it.get("source") or {}).get("footprint") is None, it.get("source")


@lite
def test_e2e_a_drawing_with_only_a_dimension_table_is_held_on_the_read(tmp_path):
    """The document prints no body line to compare, so it is taken; the read built from it (14 x 20) is held against
    the datasheet's 14 x 14 before anything is written."""
    doc = ["QFP100 package outline QFP-100"] + DOC_14x20[1:]
    it, built, _ = _e2e(tmp_path, doc, G_14x20)
    assert not built, built
    assert "not this part's package" in _tried(it) and "14 x 14" in _tried(it), _tried(it)


@lite
def test_e2e_nothing_ties_a_searched_drawing_to_the_body_so_it_is_not_taken(tmp_path):
    """The datasheet prints only "LQFP100" (no body, pitch or outline code): a drawing of the right family and count
    that does not name the part is not taken, even one of the right size."""
    it, built, _ = _e2e(tmp_path, ["LQFP100 package outline QFP-100"] + DOC_RIGHT[1:], G_RIGHT,
                          own_line=f"{MPN} LQFP100 plastic low profile quad flat package; 100 leads")
    assert not built, built


@lite
def test_e2e_the_right_drawing_still_builds(tmp_path):
    it, built, _ = _e2e(tmp_path, DOC_RIGHT, G_RIGHT)
    assert len(built) == 1, (built, _tried(it))
    t = built[0].read_text()
    xs = [float(m.group(1)) for m in re.finditer(r"\(pad \"?\d+\"? smd \w+ \(at ([-\d.]+) [-\d.]+", t)]
    assert 15.0 < max(xs) - min(xs) < 16.0, (max(xs) - min(xs))


def test_a_second_read_of_web_searched_pages_is_self_checked_not_agreed():
    from ecadlib import crosscheck as CC
    agreed = {"source": "second read", "against": "second read (m)", "status": "agreed", "compared": ["pitch", "body_length"],
              "agreed": ["pitch", "body_length"], "disagreed": [], "not_compared": []}
    res = {"spec": {"name": "LQFP-100", "package_type": "qfp", "pin_count": 100, "pitch": 0.65, "body_length": 20.0,
                    "body_width": 14.0}, "symbol": {"pins": []}}

    class R:
        cfg = {"cross_check": "auto"}
    with mock.patch.object(CC, "second_read", lambda *a, **k: dict(agreed)), \
            mock.patch.object(CC, "standard_matches", lambda *a, **k: []), \
            mock.patch.object(CC, "held_anyway", lambda *a, **k: None):
        plain = CC.run(R(), {"part": MPN, "prov": {}}, res, "m")
        web = CC.run(R(), {"part": MPN, "prov": {"outline": [{"code": "web search", "from": DWG, "first_page": 4}]}},
                     res, "m")
    assert plain["status"] == "agreed", plain
    assert web["status"] == "self-checked", web
    assert "self-checked only" in CC.summary_line(web) and DWG in CC.summary_line(web), CC.summary_line(web)


# ---------------------------------------------------------------- R-FX19-2: a compliance document is not the datasheet
PAGE = "https://www.maker.com/product/LM358DR"
COMPLIANCE_DOCS = {
    "rohs": "RoHS Certificate of Compliance\nTexas Instruments certifies that LM358DR SOIC-8 complies with Directive "
            "2011/65/EU. Lead (Pb) < 1000 ppm.",
    "material": "Material Declaration\nProduct: LM358DR Package: SOIC (D) 8 pins. Total weight 76 mg. Substances: Cu, Sn.",
    "reach": "REACH Compliance Statement\nLM358DR: no SVHC above 0.1% w/w.",
    "conformity": "Declaration of Conformity\nLM358DR operational amplifier"}


def _page_fetch(body, pdf):
    import tempfile
    from ecadlib import datasheet as D
    from tests.test_datasheet_downloads import Scripted
    with tempfile.TemporaryDirectory() as d:
        http = Scripted({PAGE: [(200, f"<html><body>{body}</body></html>".encode(), {})],
                         "https://www.maker.com/x/doc.pdf": [pdf]})
        try:
            D.PdfStore(d, http, sleep=lambda s: None).fetch(PAGE, "BOM", part="LM358DR", mpn="LM358DR")
            return "taken"
        except D.DatasheetError as e:
            return str(e)


def test_an_embedded_or_linked_compliance_document_is_not_taken_as_the_datasheet():
    from tests import test_datasheet_batch as TB
    for body in ("<iframe src='/x/doc.pdf'></iframe>",
                 "<a href='/document/datasheet/LM358'>Datasheet</a><iframe src='/x/doc.pdf'></iframe>"):
        for k, txt in COMPLIANCE_DOCS.items():
            got = _page_fetch(body, TB.pdf_with([txt, "page 2"]))
            assert got != "taken" and "not a datasheet" in got, (body, k, got)


def test_a_datasheet_with_a_rohs_or_reach_compliant_banner_is_still_taken():
    from ecadlib import datasheet as D
    for first in ("LM358DR\nRoHS Compliant\nDual Operational Amplifier", "LM358DR Dual Op Amp\nREACH compliant, "
                  "halogen free\nFeatures", "LM358DR\nRoHS and REACH compliant\nPin Configuration"):
        assert D.not_a_datasheet([first]) is None, first


# ---------------------------------------------------------------- R-FX19-3: eval --base-url never takes [ai]'s key along
def _env19():
    keep = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")}
    keep.update({"AI_KEY": "sk-ai-gw1", "PROXY_KEY": "sk-proxy", "OPENAI_API_KEY": "sk-openai", "ECADLIB_NO_STORED_KEYS": "1"})
    return mock.patch.dict(os.environ, keep, clear=True)


def _rules19(ai, ds=None, proof=None):
    from ecadlib import rules as RU
    r = copy.deepcopy(RU.DEFAULTS)
    r["ai"] = dict(r.get("ai") or {}, **ai)
    r["datasheet"].update(ds or {})
    r["agent"]["proof"].update(proof or {})
    return r


def _ep(rules, section="datasheet"):
    from ecadlib import llm as LL
    ep = LL.endpoint(rules, section)
    return ep["base_url"], ep["key_env"], LL.key_for(ep)


def test_eval_base_url_alone_never_sends_the_ai_key_to_another_server():
    from ecadlib.eval.runner import ai_overrides
    G1, G2 = "https://gw1.example.com/v1", "https://gw2.example.net/v1"
    with _env19():
        r = _rules19({"provider": "openai-compatible", "base_url": G1, "model": "m", "api_key_env": "AI_KEY"})
        for kw in ({"base_url": G2}, {"provider": "openai-compatible", "base_url": G2}):
            base, env, key = _ep(ai_overrides(r, **kw))
            assert base == G2 and env != "AI_KEY" and key != "sk-ai-gw1", (kw, base, env)
        # a corporate proxy's key never goes to api.openai.com
        r = _rules19({"provider": "openai", "base_url": "https://proxy.corp/v1", "model": "m", "api_key_env": "PROXY_KEY"})
        base, env, key = _ep(ai_overrides(r, base_url="https://api.openai.com/v1"))
        assert base == "https://api.openai.com/v1" and env != "PROXY_KEY" and key != "sk-proxy", (base, env)


def test_eval_overrides_keep_the_ai_key_on_its_own_server():
    from ecadlib.eval.runner import ai_overrides
    G1 = "https://gw1.example.com/v1"
    with _env19():
        r = _rules19({"provider": "openai-compatible", "base_url": G1, "model": "m", "api_key_env": "AI_KEY"})
        for kw in ({"base_url": G1 + "/"}, {"base_url": "https://GW1.example.com/v1"}, {"assist": "off"},
                   {"provider": "openai-compatible", "model": "m2"}):
            base, env, key = _ep(ai_overrides(r, **kw))
            assert env == "AI_KEY" and key == "sk-ai-gw1", (kw, base, env)
        # --api-key-env always wins
        base, env, key = _ep(ai_overrides(r, base_url="https://gw2.example.net/v1", api_key_env="PROXY_KEY"))
        assert env == "PROXY_KEY", env
