"""Review 2026-10-22, datasheet search and fetch: the web search's cost cap (turns with no usage, continuations that
resend the search results), the "only PDF there is" fallback after the search, the Downloads hand-off taking only a
file that names the part, search links to private addresses, aggregator links, mentions_part siblings and 'x'
families, get_check across model names, a stale saved search link, part_arg quoting and web_search_max_uses = 0.
Plain functions (tests.run_all, no pytest). The API, the network and DNS are faked.
    python3 -m tests.test_review_1022_search"""
import json
import shlex
import tempfile
from pathlib import Path

from ecadlib import datasheet as D, ds_search as WS
from tests import test_datasheet_batch as TB
from tests.fixtures_lite import lite
from tests.test_datasheet_downloads import Scripted
from tests.test_ds_search import _answer, Client, _reader, DEAD, FOUND

MODEL = "claude-opus-5-5"
FILLER = " Features: low quiescent current, wide input range, thermal shutdown, small package, RoHS compliant."


def _est():
    return WS.estimate(MODEL, WS.MAX_USES, MODEL in D.FALLBACK_MODELS)


def _job(rd, part="TESTPART", urls=((DEAD, "BOM"),)):
    try:
        return rd.job(part, list(urls), "SOIC-8")
    except D.DatasheetError:
        return None


# ---------------------------------------------------------------- 1. the search's cost cap
def test_turns_with_no_usage_each_hold_the_worst_case():
    def noun(stop):                                   # a gateway that sends no usage block
        a = _answer([], stop=stop)
        a.pop("usage")
        return a
    est = _est()
    cl = Client(noun("pause_turn"), noun("pause_turn"), noun("end_turn"))
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, cl, web_search=True, max_cost_usd=est * 1.3)   # room for one search, not two
        _job(rd)
        assert len(cl.sent) == 1, len(cl.sent)        # a turn of unknown cost holds est: no room for another
        assert rd.committed_usd >= len(cl.sent) * est - 1e-9, rd.committed_usd
    cl = Client(noun("pause_turn"), noun("end_turn"))
    links, usage, turns, err = WS.search(cl, "TESTPART", MODEL)
    assert abs(WS.usd(MODEL, turns, unknown=est) - 2 * est) < 1e-9 and WS.usd(MODEL, turns) is None


def test_a_continuation_that_resends_the_results_is_bounded_by_what_was_read():
    def big(stop, tin):
        return dict(_answer([], stop=stop), usage={"input_tokens": tin, "output_tokens": 800,
                                                   "server_tool_use": {"web_search_requests": 1}})
    cl = Client(big("pause_turn", 80000), big("end_turn", 175000))
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, cl, web_search=True, max_cost_usd=1.0)
        _job(rd)
        assert len(cl.sent) == 1 and rd.committed_usd <= 1.0, (len(cl.sent), rd.committed_usd)
    cl = Client(_answer([], stop="pause_turn"), _answer([FOUND]))
    with tempfile.TemporaryDirectory() as d:            # small turns under a roomy cap are still continued
        job = _job(_reader(d, cl, web_search=True, max_cost_usd=5.0))
        assert len(cl.sent) == 2 and job["url"] == FOUND


def test_the_estimate_holds_max_tokens_of_output():
    body = WS.request_body("X", MODEL)
    assert WS.EST_TOKENS[1] >= body["max_tokens"]
    p = D.price_of(MODEL)
    assert abs(WS.estimate(MODEL, 1) - ((WS.EST_TOKENS[0] * p[0] + body["max_tokens"] * p[1]) / 1e6 + WS.SEARCH_USD)) < 1e-9


def test_max_uses_zero_means_no_search():
    cl = Client(_answer([FOUND]))
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, cl, web_search=True, web_search_max_uses=0)
        try:
            rd.job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
            assert False, "no PDF without a search"
        except D.DatasheetError as e:
            assert "web_search_max_uses = 0" in str(e), str(e)
        assert cl.sent == []


# ---------------------------------------------------------------- 2. the "only PDF there is" after the search
WRONG = TB.pdf_with(["LTC4054 Standalone Linear Li-Ion Battery Charger" + FILLER])
RIGHT = TB.pdf_with(["LTC4060 Standalone Linear NiMH/NiCd Fast Battery Charger" + FILLER])
A, B = "https://www.maker.example/ltc4060.pdf", "https://www.maker.example/media/ltc4060fb.pdf"


def _ltc(d, cl, web_search=True):
    rd = D.Reader(d, d, key=TB.KEY, http=Scripted({A: [WRONG], B: [RIGHT]}), log=None, sleep=lambda s: None, client=cl,
                  rules={"datasheet": {"web_search": web_search, "cross_check": "off", "download": "plain"}})
    return rd.job("LTC4060EDHC#PBF", [(A, "BOM")], "DFN-10", mpn="LTC4060EDHC#PBF")


def test_a_pdf_of_another_part_waits_for_the_search():
    with tempfile.TemporaryDirectory() as d:
        cl = Client(_answer([B]))
        j = _ltc(d, cl)
        assert len(cl.sent) == 1 and j["url"] == B and j["data"] == RIGHT and not j["base_prov"].get("unnamed")
    with tempfile.TemporaryDirectory() as d:          # the search found nothing: the only PDF there is, flagged
        cl = Client(_answer([]))
        j = _ltc(d, cl)
        assert len(cl.sent) == 1 and j["url"] == A and j["base_prov"].get("unnamed")
    with tempfile.TemporaryDirectory() as d:          # no search: as before
        j = _ltc(d, Client(), web_search=False)
        assert j["url"] == A and j["base_prov"].get("unnamed")


# ---------------------------------------------------------------- 3. the Downloads hand-off
def _handoff(tmp_path, monkeypatch, name, data, part="TPS7A0218"):
    monkeypatch.setenv("ECADLIB_DOWNLOAD", "headers")
    url = "https://www.maker.example/download?fileId=8812"       # a document-download link: its stem is "download"
    dl = tmp_path / "Downloads"
    dl.mkdir()
    rd = D.Reader(tmp_path / "out", tmp_path / "cache", key=TB.KEY, http=TB.PdfHTTP({url: 403}), log=None,
                  sleep=lambda s: None, poll=0, handoff=True, rules={"datasheet": {"downloads_folder": str(dl)}})
    t, h = [0.0], rd.store.handoff
    h.open_url = lambda u: (dl / name).write_bytes(data) or True
    h.say, h.clock, h.sleep = (lambda m: None), (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))
    rd.store.fns["headers"] = lambda u, p: (403, b"<html>Access Denied</html>", "text/html", u, None)
    rd.store.missing = {}
    try:
        job = rd.job(part, [(url, "BOM")], "SOT-23", mpn=part)
    except D.DatasheetError:
        job = None
    return job, rd


@lite
def test_an_unrelated_download_named_like_a_generic_link_is_left_alone(tmp_path, monkeypatch):
    other = TB.pdf_with(["Invoice 2026-114 for services rendered, total due 120 EUR, payable within 30 days" + FILLER])
    job, rd = _handoff(tmp_path, monkeypatch, "download (3).pdf", other)
    assert job is None or job["base_prov"].get("file") != "download (3).pdf", job and job["base_prov"]
    assert not list((tmp_path / "cache" / "datasheets").glob("*.pdf"))   # never kept, so never read later


@lite
def test_a_download_named_for_the_part_but_of_another_part_is_left_alone(tmp_path, monkeypatch):
    sib = TB.pdf_with(["TPS7B6950 automotive LDO" + FILLER])
    job, rd = _handoff(tmp_path, monkeypatch, "TPS7A0218.pdf", sib)
    assert job is None or job["base_prov"].get("file") != "TPS7A0218.pdf"
    assert not list((tmp_path / "cache" / "datasheets").glob("*.pdf"))


@lite
def test_a_download_whose_text_names_the_part_is_taken(tmp_path, monkeypatch):
    own = TB.pdf_with(["TPS7A02 Nanopower IQ 200-mA Low-Dropout Regulator TPS7A0218" + FILLER])
    job, rd = _handoff(tmp_path, monkeypatch, "download (4).pdf", own)
    assert job["base_prov"].get("file") == "download (4).pdf" and job["base_prov"].get("via") == "handoff"


# ---------------------------------------------------------------- 4. search links to private addresses
def test_search_links_to_private_addresses_are_not_fetched():
    cl = Client(_answer(["http://10.0.0.5:8080/internal/report.pdf", "http://[fd00::1]/a.pdf", "http://100.64.1.2/b.pdf"],
                        results=["http://192.168.1.1/cgi-bin/datasheet?x=1", "http://127.0.0.1/c.pdf"]))
    with tempfile.TemporaryDirectory() as d:
        rd = _reader(d, cl, web_search=True)
        _job(rd)
        assert rd.store.http.fetched == [DEAD], rd.store.http.fetched


@lite
def test_a_search_link_whose_name_resolves_privately_is_refused(tmp_path, monkeypatch):
    names = {"intranet.maker.com": ["10.1.2.3"], "www.maker.com": ["93.184.216.34"], "both.maker.com": ["8.8.8.8", "::1"]}
    monkeypatch.setattr(D, "_resolve", lambda host: names.get(host, []))
    assert D.unsafe_download("https://intranet.maker.com/x.pdf", public=True)
    assert D.unsafe_download("https://both.maker.com/x.pdf", public=True)
    assert D.unsafe_download("https://www.maker.com/x.pdf", public=True) is None
    assert D.unsafe_download("http://10.0.0.5/x.pdf") is None            # a BOM link to your own network stays allowed
    assert D.unsafe_download("http://10.0.0.5/x.pdf", public=True)
    assert D.unsafe_download("http://localhost/x.pdf", public=True)
    cl = Client(_answer(["https://intranet.maker.com/ds.pdf", FOUND]))
    rd = _reader(tmp_path, cl, web_search=True)
    job = rd.job("TESTPART", [(DEAD, "BOM")], "SOIC-8")
    assert job["url"] == FOUND and "https://intranet.maker.com/ds.pdf" not in rd.store.http.fetched


# ---------------------------------------------------------------- 5. aggregators
def test_aggregator_links_are_dropped_and_datasheet_is_matched_in_the_path_only():
    msg = _answer(["https://www.alldatasheet.com/datasheet-pdf/pdf/1/TI/LM317.pdf", "https://www.ti.com/lit/ds/lm317.pdf",
                   "https://pdf.datasheet4u.com/x.pdf"],
                  results=["https://www.datasheetarchive.com/lm317.pdf", "https://datasheetspdf.com/pdf/123/x.pdf",
                           "https://www.datasheets360.com/part/LM317", "https://www.mydatasheet.example/page.html",
                           "https://www.mouser.example/datasheet/2/lm317.html", "https://www.ti.com/lm317"])
    assert WS.links_in(msg["content"]) == ["https://www.ti.com/lit/ds/lm317.pdf",
                                           "https://www.mouser.example/datasheet/2/lm317.html"], WS.links_in(msg["content"])


# ---------------------------------------------------------------- 6. mentions_part
PAD = " This datasheet describes the device family features, ordering information and package drawings. "


def test_a_sibling_family_does_not_name_the_part():
    for mpn, text in (("STM32F103C8T6", "STM32L476xx Ultra-low-power Arm Cortex-M4 MCU"),
                      ("TPS7A4701RGWR", "TPS7B6950-Q1 automotive LDO"),
                      ("PIC16F877A", "PIC16F15313 8-pin microcontroller"),
                      ("ESP32-S3-WROOM-1", "ESP32-C3-MINI-1 module"),
                      ("LTC4060EDHC", "LTC4054 standalone charger"),
                      ("SN74HC595N", "SNx4HC165 8-Bit Parallel-Load Shift Registers")):
        assert D.mentions_part([text + PAD], mpn) is False, (mpn, text)


def test_its_own_family_names_it_with_x_for_one_character():
    for mpn, text in (("SN74HC595N", "SNx4HC595 8-Bit Shift Registers With 3-State Output Registers"),
                      ("SN74LVC1G08DBVR", "SN74LVC1G08 Single 2-Input Positive-AND Gate"),
                      ("STM32F103C8T6", "STM32F103x8 STM32F103xB Medium-density performance line"),
                      ("TPS7A4701RGWR", "TPS7A47 36-V, 1-A, 4-uVRMS, RF LDO Voltage Regulator TPS7A4701"),
                      ("PIC16F877A", "PIC16F87XA 28/40/44-Pin Enhanced Flash Microcontrollers PIC16F877A"),
                      ("ESP32-S3-WROOM-1", "ESP32-S3-WROOM-1 ESP32-S3-WROOM-1U Datasheet"),
                      ("ATTINY85-20PU", "ATtiny85 8-bit AVR Microcontroller with 8K Bytes In-System Programmable Flash"),
                      ("ATMEGA32U4-AU", "ATmega16U4 ATmega32U4 8-bit Microcontroller with USB")):
        assert D.mentions_part([text + PAD], mpn) is True, (mpn, text)


# ---------------------------------------------------------------- 7. get_check
def test_get_check_never_takes_another_models_check():
    with tempfile.TemporaryDirectory() as d:
        rc = D.ResultCache(d)
        key = lambda m: "check-" + D.ResultCache.key("a" * 64, m, "LM317", "SOT-223", [0, None], "high|check:3")
        rc.put(key("claude-opus-5-5"), {"message": {"model": "claude-opus-5-5"}})
        assert rc.get_check(key("claude-opus-5")) is None
        old = key("claude-opus-5").rsplit("-", 2)
        rc.put(f"{old[0]}-0123456789ab-{old[2]}", {"message": {"model": "claude-opus-5"}})   # an earlier reader schema
        assert rc.get_check(key("claude-opus-5"))["message"]["model"] == "claude-opus-5"
        assert rc.get_check(key("claude-opus-5-5"))["message"]["model"] == "claude-opus-5-5"


# ---------------------------------------------------------------- 8. a saved search link that died
def test_a_saved_link_that_fails_is_dropped_and_searched_again():
    with tempfile.TemporaryDirectory() as d:
        found = WS.Found(Path(d) / "out" / "datasheet")
        found.put("TESTPART", DEAD)
        cl = Client(_answer([FOUND]))
        job = _reader(d, cl, web_search=True).job("TESTPART", [], "SOIC-8")
        assert job["url"] == FOUND and len(cl.sent) == 1
        assert found.get("TESTPART") == FOUND
        assert [f.name for f in found.file.parent.iterdir() if f.name.startswith("web_search")] == ["web_search.json"]
        found.drop("TESTPART")
        assert found.get("TESTPART") is None and json.loads(found.file.read_text()) == {}


# ---------------------------------------------------------------- 9. part_arg
def test_part_arg_is_safe_to_paste_into_a_shell():
    assert D.part_arg("LM317") == "LM317" and D.part_arg("LTC4060EDHC#PBF") == "LTC4060EDHC#PBF"
    assert D.part_arg("DSPART1 REV A") == '"DSPART1 REV A"'          # nothing expands: the double quotes messages reads
    assert D.part_arg("X$(id)") == "'X$(id)'" and D.part_arg("#1") == '"#1"'
    for s in ('X$(touch pwned)', 'A "B"', "it's", "a`id`b", "HX-2.54 直针"):
        q = D.part_arg(s)
        assert shlex.split(q) == [s.strip()], (s, q)
        assert "$(" not in q or q.startswith("'"), q

