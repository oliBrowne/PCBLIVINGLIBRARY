"""Offline tests for ecadlib.librarian and ecadlib.easyeda_convert. Run:  python3 -m tests.test_librarian

HTTP is a fake that serves a tiny KiCad library (GitLab tree pages + raw files) and one EasyEDA component.
The KiCad files are ecadlib-generated footprints (out/footprints, identical to KiCad's for R_0603/SOIC-8) and
hand-written symbols; the EasyEDA payload (tests/fixtures/easyeda/C2002.json) is SYNTHETIC. The datasheet
reader's API call is mocked with the synthetic extraction from tests/test_datasheet."""
import copy, json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import datasheet as D, easyeda_convert as EC, footprint_check as FC, kicad_import as K, librarian as L, \
    providers as P, sexpr
from ecadlib.httpcache import Response
from tests import test_datasheet as TD

ROOT = Path(__file__).parent.parent
FPS = ROOT / "out" / "footprints" / "ecadlib.pretty"
EE = json.loads((Path(__file__).parent / "fixtures" / "easyeda" / "C2002.json").read_text())


def ksym(name, pins, ref="U", footprint="", datasheet="~", desc=""):
    pins_txt = "\n".join(f'      (pin passive line (at -7.62 {2.54 * i:g} 0) (length 2.54) (name "P{n}" (effects (font (size 1.27 1.27)))) '
                         f'(number "{n}" (effects (font (size 1.27 1.27)))))' for i, n in enumerate(pins))
    return f"""(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor")
  (symbol "{name}" (in_bom yes) (on_board yes)
    (property "Reference" "{ref}" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "{name}" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "{footprint}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "{datasheet}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Description" "{desc}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (symbol "{name}_0_1" (rectangle (start -5.08 5.08) (end 5.08 -12.7) (stroke (width 0.254) (type default)) (fill (type background))))
    (symbol "{name}_1_1"
{pins_txt})))
"""


SYMS = {"Device:R": ksym("R", ["1", "2"], "R", desc="Resistor"),
        "Device:LED": ksym("LED", ["1", "2"], "D", desc="Light emitting diode"),
        "Regulator_Test:TPS1234DSG": ksym("TPS1234DSG", [str(i) for i in range(1, 9)], footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                                          datasheet="https://example.com/tps1234.pdf", desc="Test regulator"),
        "Connector_Generic:Conn_01x04": ksym("Conn_01x04", ["1", "2", "3", "4"], "J")}
FOOTS = {"Resistor_SMD:R_0603_1608Metric": (FPS / "R_0603_1608Metric.kicad_mod").read_text(),
         "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm": (FPS / "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod").read_text(),
         "LED_SMD:LED_0603_1608Metric": (FPS / "R_0603_1608Metric.kicad_mod").read_text().replace("R_0603_1608Metric", "LED_0603_1608Metric")}


class FakeHTTP:
    """Serves the fake KiCad library and EasyEDA payloads; every other host is 'blocked'. Counts requests."""
    offline = False

    def __init__(self, easyeda=None):
        self.routes, self.calls, self.log = {}, [], []
        for repo, files, dsuf, fsuf in (("kicad-symbols", SYMS, ".kicad_symdir", ".kicad_sym"), ("kicad-footprints", FOOTS, ".pretty", ".kicad_mod")):
            tree = [{"type": "tree", "path": f"{r.split(':')[0]}{dsuf}"} for r in files]
            tree += [{"type": "blob", "path": f"{r.split(':')[0]}{dsuf}/{r.split(':')[1]}{fsuf}"} for r in files]
            tree += [{"type": "blob", "path": "README.md"}]
            self.routes[L.TREE.format(repo=repo, page=1)] = json.dumps(tree)
            for r, text in files.items():
                lib, name = r.split(":")
                self.routes[K.GITLAB.format(repo=repo, path=f"{lib}{dsuf}/{name}{fsuf}")] = text
        for lcsc, payload in (easyeda or {}).items():
            self.routes[P.EasyEDA.URL.format(lcsc)] = json.dumps(payload)

    def get(self, url, headers=None, **kw):
        self.calls.append(url)
        if url in self.routes:
            return Response(200, self.routes[url])
        if "gitlab.com" in url:
            return Response(404, "")
        return Response(0, "", error="ProxyError: proxy CONNECT refused (403 Forbidden)")

    def post(self, url, body, headers=None, **kw):
        self.calls.append(url)
        return Response(0, "", error="ProxyError: proxy CONNECT refused (403 Forbidden)")


BOM = """Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet
R1,1,10k,R_0603_1608Metric,ACME,RC0603-10K,C1001,Resistor 10k 0603,https://example.com/r.pdf
D1,1,RED,LED_0603_1608Metric,ACME,LED-RED-0603,,LED red 0603,https://example.com/led.pdf
U1,1,TPS1234DSGR,SOIC-8_3.9x4.9mm_P1.27mm,ACME,TPS1234DSGR,C3003,Test regulator,
Q1,1,EEPART1,,ACME,EEPART1,C2002,EasyEDA-only part,
U3,1,DSPART1,,ACME,DSPART1,,Datasheet-only part,https://example.com/ds.pdf
U4,1,NOWHERE1,,,NOWHERE1,,,
"""


def ee_as(mpn, lcsc):
    e = copy.deepcopy(EE)
    e["result"]["dataStr"]["head"]["c_para"]["Manufacturer Part"] = e["result"]["title"] = mpn
    e["result"]["lcsc"]["number"] = lcsc
    return e


def run(d, http=None, bom=BOM, **kw):
    b = Path(d) / "bom.csv"
    b.write_text(bom)
    http = http or FakeHTTP({"C2002": EE, "C3003": ee_as("TPS1234DSGR", "C3003")})
    with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}, clear=True):
        items = L.run(b, [], Path(d) / "out", cache=Path(d) / "cache", http=http, log=lambda *a: None, **kw)
    return {i["name"]: i for i in items}, http


def test_easyeda_footprint_converter():
    notes = []
    text = EC.footprint_to_kicad(EE["result"]["packageDetail"]["dataStr"], notes=notes)
    t = sexpr.parse(text)
    pads = {p[1]: p for p in sexpr.find(t, "pad")}
    assert set(pads) == {"1", "2", "3"} and pads["1"][2] == "smd" and pads["1"][3] == "rect", pads
    assert sexpr.first(pads["1"], "at")[1:3] == ["-0.95", "1"] and sexpr.first(pads["3"], "size")[1:] == ["0.6", "0.9"]
    assert [l for l, _, _ in FC.check(text)] == ["warning"], FC.check(text)       # silk 0.18 mm from pad 1: warning only
    assert any("courtyard drawn by ecadlib" in n for n in notes) and any("TEXT x1" in n for n in notes), notes
    fp, _ = K.convert_footprint(text, "Q_TEST")
    assert len(fp["pads"]) == 3 and fp["name"] == "Q_TEST"


def test_easyeda_footprint_tht_slot_npth_polygon():
    data = {"head": {"x": 100, "y": 100}, "shape": [
        "PAD~ELLIPSE~100~100~6~6~11~~1~1.5~~0~g1~0~~Y~0",                         # round THT, drill 0.762
        "PAD~OVAL~120~100~6~12~11~~2~1.5~~90~g2~8~~Y~0",                          # slotted, rotated
        "PAD~POLYGON~140~100~4~4~1~~3~0~138 98 142 98 142 102 138 102~0~g3~0~~~0",  # polygon -> bbox
        "PAD~RECT~160~100~6~6~11~~4~1.5~~0~g4~0~~N~0",                            # unplated
        "HOLE~100~130~2~g5~0", "ARC~1~3~~M 0 0 A 1 1 0 0 1 2 2~~g6~0", "VIA~1~2~3~4~5~6"]}
    notes = []
    t = sexpr.parse(EC.footprint_to_kicad(data, "THT", notes))
    pads = list(sexpr.find(t, "pad"))
    kinds = [(p[1], p[2], p[3]) for p in pads]
    assert kinds == [("1", "thru_hole", "circle"), ("2", "thru_hole", "oval"), ("3", "smd", "rect"), ("4", "np_thru_hole", "rect"),
                     ("", "np_thru_hole", "circle")], kinds
    assert sexpr.first(pads[0], "drill")[1] == "0.762"
    assert sexpr.first(pads[1], "drill")[1:] == ["oval", "0.762", "2.032"] and sexpr.first(pads[1], "at")[3] == "90"
    assert sexpr.first(pads[2], "size")[1:] == ["1.016", "1.016"]
    assert any("polygon pad" in n for n in notes) and any("ARC x1" in n and "VIA x1" in n for n in notes), notes
    fp, _ = K.convert_footprint(EC.footprint_to_kicad(data, "THT"))
    assert [p.get("plated") for p in fp["pads"]] == [True, True, None, False, False]


def test_easyeda_symbol_converter():
    notes = []
    text = EC.symbol_to_kicad(EE["result"]["dataStr"], "EEPART1", "SOT23", {"MPN": "EEPART1"}, notes)
    sym = K.convert_symbol([K._find_symbol(sexpr.parse(text), "EEPART1")], "EEPART1", "SOT23")
    pins = {p[1]["num"]: (p[1]["name"], p[1]["type"], p[2], p[3], p[4], p[5]) for p in sym["prims"] if p[0] == "pin"}
    assert pins == {"1": ("G", "input", "left", -10, 5, 10), "2": ("S", "output", "left", -10, -5, 10),
                    "3": ("D", "io", "right", 10, 0, 10)}, pins
    assert ("MPN", "EEPART1") in sym["params"] and sym["designator"] == "Q?" and any("T x1" in n for n in notes)


def test_kicad_index_matching():
    with tempfile.TemporaryDirectory() as d:
        lib = L.KiCadLibrary(FakeHTTP(), Path(d) / "idx.json")
        assert lib.symbols["TPS1234DSG"] == ["Regulator_Test"] and "README" not in lib.symbols
        assert lib.find_symbol("TPS1234DSG")[0] == "Regulator_Test:TPS1234DSG"
        ref, how = lib.find_symbol("TPS1234DSGR")
        assert ref == "Regulator_Test:TPS1234DSG" and "ordering suffix 'R'" in how, how
        assert lib.find_symbol("TPS1234DSGRXYZW") == (None, None)                 # 5 extra chars, no separator
        assert lib.find_symbol("TPS12") == (None, None)
        assert lib.generic_connector("Conn_01x04")[0] == "Connector_Generic:Conn_01x04"
        assert lib.footprint("SOIC-8_3.9x4.9mm_P1.27mm") == "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
        assert lib.footprint("Wrong_Lib:SOIC-8_3.9x4.9mm_P1.27mm") is None
        assert lib.footprint_by_prefix("R_0603") == "Resistor_SMD:R_0603_1608Metric"
        assert Path(d, "idx.json").exists()
        lib2 = L.KiCadLibrary(FakeHTTP.__new__(FakeHTTP), Path(d) / "idx.json")       # cached: no HTTP needed
        assert lib2.symbols == lib.symbols
        lib._wild.append((__import__("re").compile("STM32F103C8T[A-Z0-9]"), ["MCU_ST_STM32F1:STM32F103C8Tx"]))
        assert lib.find_symbol("STM32F103C8T6")[0] == "MCU_ST_STM32F1:STM32F103C8Tx"


def test_wildcard_index():
    with tempfile.TemporaryDirectory() as d:
        http = FakeHTTP()
        tree = [{"type": "blob", "path": "MCU_ST_STM32F1.kicad_symdir/STM32F103C8Tx.kicad_sym"},
                {"type": "blob", "path": "Connector_Generic.kicad_symdir/Conn_01x04.kicad_sym"}]
        http.routes[L.TREE.format(repo="kicad-symbols", page=1)] = json.dumps(tree)
        lib = L.KiCadLibrary(http, Path(d) / "idx.json")
        assert lib.find_symbol("STM32F103C8T6") == ("MCU_ST_STM32F1:STM32F103C8Tx", "name with KiCad x wildcards")
        assert lib.find_symbol("CONN01504") == (None, None)                       # 01x04 is not a wildcard


def test_each_source_and_review():
    with tempfile.TemporaryDirectory() as d:
        items, http = run(d)
        src = {n: (i["source"]["symbol"], i["source"]["footprint"]) for n, i in items.items()}
        assert src["RES-0603-10k"] == ("kicad:Device:R", "kicad:Resistor_SMD:R_0603_1608Metric"), src
        assert src["LED-RED-0603"] == ("kicad:Device:LED", "kicad:LED_SMD:LED_0603_1608Metric"), src
        assert src["TPS1234DSGR"] == ("kicad:Regulator_Test:TPS1234DSG", "kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"), src
        assert src["EEPART1"] == ("easyeda:C2002", "easyeda:C2002"), src
        assert src["DSPART1"] == (None, None) and src["NOWHERE1"] == (None, None), src
        out = Path(d) / "out"
        for n in ("RES-0603-10k", "LED-RED-0603", "TPS1234DSGR", "EEPART1"):
            assert items[n]["status"] == "ready", (n, items[n]["hold_reasons"])
            assert (out / items[n]["symbol"]["file"]).exists() and (out / items[n]["footprint"]["kicad_mod"]).exists()
        assert items["RES-0603-10k"]["symbol"]["file"] == "models/SchLib/Resistor.SchLib"
        assert items["TPS1234DSGR"]["field_sources"]["ComponentLink2URL"] == "LCSC datasheet URL pattern (not fetched)"
        assert any("ordering suffix" in r for r in items["TPS1234DSGR"]["librarian"]["review"])
        assert not any("easyeda" in t for t in items["TPS1234DSGR"]["librarian"]["tried"])   # kicad came first, easyeda not needed
        tried = " | ".join(items["DSPART1"]["librarian"]["tried"])
        assert "needs datasheet reader (no API key): https://example.com/ds.pdf (BOM)" in tried, tried
        assert "kicad: no symbol" in tried and "easyeda: no LCSC number" in tried, tried
        assert "datasheet: no datasheet URL known" in items["NOWHERE1"]["librarian"]["tried"]
        nw = items["NOWHERE1"]["hold_reasons"]
        assert items["NOWHERE1"]["status"] == "held" and "fields: Manufacturer is empty" in nw and "fields: no datasheet link" in nw, nw
        plan = json.loads((out / "plan.json").read_text())
        assert {i["name"]: i["status"] for i in plan} == {n: i["status"] for n, i in items.items()}
        rev = (out / "review.md").read_text()
        held = rev.split("## Held: fix these first")[1].split("\n## ")[0]
        assert "### DSPART1 (held)" in held and "### RES-0603-10k" not in held, held
        assert "The datasheet reader is not set up (no model API key)." in rev or "needs datasheet reader (no API key)" in rev
        assert (out / "review.html").exists() and "DSPART1" in (out / "review.html").read_text()
        for f in ("review.csv", "release_check.md", "release_check.csv", "triage.md", "librarian_state.json"):
            assert (out / f).exists(), f


def test_fallback_order():
    with tempfile.TemporaryDirectory() as d:
        items, _ = run(d, sources=["easyeda", "kicad", "datasheet"])
        assert items["TPS1234DSGR"]["source"] == {"symbol": "easyeda:C3003", "footprint": "easyeda:C3003"}, items["TPS1234DSGR"]["source"]
        assert items["RES-0603-10k"]["source"]["symbol"] == "kicad:Device:R"      # C1001 is blocked: falls through to kicad
        assert "easyeda C1001: ProxyError" in items["RES-0603-10k"]["librarian"]["tried"][0]
    with tempfile.TemporaryDirectory() as d:
        items, _ = run(d, sources=["kicad"])
        assert items["EEPART1"]["source"] == {"symbol": None, "footprint": None} and items["EEPART1"]["status"] == "held"
    with tempfile.TemporaryDirectory() as d:                 # the KiCad index is down: next source, reported
        http = FakeHTTP({"C2002": EE})
        del http.routes[L.TREE.format(repo="kicad-symbols", page=1)]
        items, _ = run(d, http=http)
        assert items["EEPART1"]["source"]["footprint"] == "easyeda:C2002"
        assert "kicad: library index unavailable" in items["RES-0603-10k"]["librarian"]["tried"][0]


def test_easyeda_mpn_mismatch_not_used():
    with tempfile.TemporaryDirectory() as d:
        bom = BOM.replace("EEPART1,C2002", "EEPART2,C2002")
        items, _ = run(d, bom=bom, sources=["easyeda"])
        it = items["EEPART2"]
        assert it["source"]["footprint"] is None and any("LCSC C2002 is EEPART1, not EEPART2" in r for r in it["librarian"]["review"])


def test_datasheet_source_held_until_allowed():
    ext = copy.deepcopy(TD.GOOD)
    calls = []
    def fake_call(headers, body, key, timeout=600):
        calls.append((body["messages"][0]["content"][0]["source"], key))
        return ext, TD.message(ext)
    with tempfile.TemporaryDirectory() as d, mock.patch.object(D, "call", fake_call):
        items, _ = run(d, api_key="sk-test")
        it = items["DSPART1"]
        assert calls == [({"type": "url", "url": "https://example.com/ds.pdf"}, "sk-test")], calls
        assert it["source"] == {"symbol": "datasheet:https://example.com/ds.pdf", "footprint": "datasheet:https://example.com/ds.pdf"}
        assert it["status"] == "held" and any(r.startswith("verify: datasheet reader output") for r in it["hold_reasons"]), it["hold_reasons"]
        assert abs(it["release_check"]["height"] - 1.0) < 1e-3 and not any(r.startswith(("pins", "footprint", "height")) for r in it["hold_reasons"]), it["hold_reasons"]
        assert list((Path(d) / "out" / "datasheet").glob("DSPART1.review.md"))
        items, _ = run(d, api_key="sk-test", allow=["DSPART1"])
        assert items["DSPART1"]["status"] == "ready" and items["DSPART1"]["release_check"]["result"] == "allowed"
        assert items["DSPART1"]["allow"]["failures"] and len(calls) == 1          # resumed: no second API call
        items, _ = run(d, api_key="sk-test")                                       # --allow is kept in the plan
        assert items["DSPART1"]["status"] == "ready"


def test_resume_idempotent_and_refresh():
    with tempfile.TemporaryDirectory() as d:
        first, http1 = run(d)
        out = Path(d) / "out"
        snap = {f.name: f.stat().st_mtime_ns for f in (out / "models").rglob("*.*")}
        again, http2 = run(d)
        kicad_raw = [u for u in http2.calls if "/-/raw/" in u]
        assert not kicad_raw, kicad_raw                       # finished parts skipped, index cached
        assert {f.name: f.stat().st_mtime_ns for f in (out / "models").rglob("*.*")} == snap
        strip = lambda items: {n: {k: v for k, v in i.items() if k != "librarian"} for n, i in items.items()}
        assert strip(again) == strip(first)
        state = json.loads((out / "librarian_state.json").read_text())
        assert state["TPS1234DSGR"]["skipped"] and not state["NOWHERE1"]["skipped"]     # unfinished parts are retried
        _, http3 = run(d, refresh=["TPS1234DSGR"])
        assert any("TPS1234DSG.kicad_sym" in u for u in http3.calls) and not any("Device.kicad_symdir" in u for u in http3.calls)
        _, http4 = run(d, refresh=[])
        assert any("Device.kicad_symdir/R.kicad_sym" in u for u in http4.calls)
        _, http5 = run(d, bom=BOM.replace("Resistor 10k 0603,https://example.com/r.pdf", "Resistor 10k 0603,https://example.com/r2.pdf"))
        assert any("Device.kicad_symdir/R.kicad_sym" in u for u in http5.calls)  # its BOM line changed: redone
        assert not any("TPS1234DSG.kicad_sym" in u for u in http5.calls)


def test_cli():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d) / "bom.csv"
        b.write_text(BOM)
        http = FakeHTTP({"C2002": EE})
        with mock.patch.object(L, "CachedHTTP", lambda *a, **k: http), \
             mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}, clear=True):
            rc = L.main([str(b), "-o", str(Path(d) / "out"), "--cache", str(Path(d) / "cache"), "--sources", "kicad,easyeda"])
        assert rc == 2 and (Path(d) / "out" / "plan.json").exists()



def _ee_0603():
    """An EasyEDA chip-resistor land shaped like LCSC's 0603 ones: two 0.9 x 1.0 mm pads 1.6 mm apart, and a silk
    outline (0.15 mm line) drawn straight across the pads, plus a short silk mark clear of the copper."""
    u = lambda mm: round(mm / EC.U, 4)
    pad = lambda n, x: f"PAD~RECT~{4000 + u(x)}~3000~{u(0.9)}~{u(1.0)}~1~~{n}~0~~0~g{n}~0~~Y~0"
    box = " ".join(f"{4000 + u(x)} {3000 + u(y)}" for x, y in ((-1.4, -0.45), (1.4, -0.45), (1.4, 0.45), (-1.4, 0.45), (-1.4, -0.45)))
    top = " ".join(f"{4000 + u(x)} {3000 + u(-1.0)}" for x in (-1.4, 1.4))
    return {"head": {"x": 4000, "y": 3000}, "shape": [pad(1, -0.8), pad(2, 0.8), f"TRACK~0.6~3~~{box}~g9~0",
                                                      f"TRACK~0.6~3~~{top}~g10~0",
                                                      f"CIRCLE~{4000 + u(-0.8)}~3000~0.3~0.5~3~g11~0"]}


def test_easyeda_silk_is_cut_back_from_the_pads():
    data = _ee_0603()
    notes = []
    text = EC.footprint_to_kicad(data, "R0603", notes)
    # without the cut the outline crosses both pads: footprint_check says 0.000 mm and the part is held
    with mock.patch.object(EC, "_clear_silk", lambda lines, circles, copper, notes: (lines, circles)):
        before = EC.footprint_to_kicad(data, "R0603")
    assert any(lv == "error" and "0.000 mm from pad 1" in m for lv, _, m in FC.check(before)), FC.check(before)
    assert not [f for f in FC.check(text) if f[1] == "silk"], FC.check(text)
    silk = [l for l in text.splitlines() if "F.SilkS" in l]
    t = sexpr.parse(text)
    lines = [ln for ln in sexpr.find(t, "fp_line") if sexpr.first(ln, "layer")[1] == "F.SilkS"]
    ys = sorted({sexpr.first(ln, "start")[2] for ln in lines})
    assert ys == ["-1"], silk                                   # the outline over the pads went; the clear line stayed whole
    assert [sexpr.first(lines[0], k)[1] for k in ("start", "end")] == ["-1.4", "1.4"], silk
    assert not [g for g in sexpr.find(t, "fp_poly") if sexpr.first(g, "layer")[1] == "F.SilkS"]   # the dot on pad 1
    assert any("cut back to 0.2 mm" in n for n in notes) and any("circle(s) left out" in n for n in notes), notes
    # the SOT-23 fixture's pin-1 dot (0.18 mm from pad 1, a warning) is kept
    t2 = EC.footprint_to_kicad(EE["result"]["packageDetail"]["dataStr"])
    assert [g for g in sexpr.find(sexpr.parse(t2), "fp_poly") if sexpr.first(g, "layer")[1] == "F.SilkS"]


def test_part_search_description_fills_an_empty_one():
    assert P.describe("  100mW Thick Film\nResistors 10k  ", "Chip Resistor", "0603") == "100mW Thick Film Resistors 10k"
    assert P.describe("", "Chip Resistor - Surface Mount", "0603") == "Chip Resistor - Surface Mount, 0603"
    assert P.describe("", "", "0603") == "" and P.describe(None, None, None) == ""          # a package alone is no description
    ee = copy.deepcopy(EE)
    assert P.parse_easyeda(ee, P.SourceResult("easyeda")).extra["description"] == "synthetic SOT-23 part"
    ee["result"]["description"], ee["result"]["tags"] = "", ["Bipolar Transistors"]
    assert P.parse_easyeda(ee, P.SourceResult("easyeda")).extra["description"] == \
        "Bipolar Transistors, SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR"
    ee["result"]["tags"] = []
    assert "description" not in P.parse_easyeda(ee, P.SourceResult("easyeda")).extra

    class JLC(FakeHTTP):
        def post(self, url, body, headers=None, **kw):
            self.calls.append(url)
            item = {"componentCode": "C2002", "componentModelEn": "EEPART1", "componentBrandEn": "ACME",
                    "componentSpecificationEn": "SOT-23", "describe": "NPN 40V 200mA SOT-23 transistor"}
            return Response(200, json.dumps({"data": {"componentPageInfo": {"list": [item]}}}))
    with tempfile.TemporaryDirectory() as d:
        bom = "Designator,Qty,Value,Manufacturer,Mfr. Part #\nQ1,1,EEPART1,ACME,EEPART1\n"
        items, _ = run(d, JLC({"C2002": ee}), bom=bom, sources=["easyeda"])
        it = items["EEPART1"]
        assert it["description"] == "NPN 40V 200mA SOT-23 transistor", it["description"]
        assert it["field_sources"]["Description"] == "LCSC/JLCPCB part data C2002", it["field_sources"]
        assert not any("Description is empty" in h for h in it.get("hold_reasons", [])), it.get("hold_reasons")


class _Gitlab429(FakeHTTP):
    """A KiCad tree whose page 2 answers HTTP 429 (or times out) until `ok` is set."""
    def __init__(self, fail="HTTP 429"):
        super().__init__()
        self.fail, self.ok = fail, False
        page1 = json.loads(self.routes[L.TREE.format(repo="kicad-symbols", page=1)])
        self.routes[L.TREE.format(repo="kicad-symbols", page=1)] = json.dumps(page1 + [{"type": "blob", "path": "README.md"}] * 100)
        self.timeouts = []

    def get(self, url, headers=None, timeout=None, **kw):
        if "tree" in url:
            self.timeouts.append(timeout)
        if url == L.TREE.format(repo="kicad-symbols", page=2) and not self.ok:
            self.calls.append(url)
            return Response(429, "", error="HTTP 429") if self.fail == "HTTP 429" else \
                Response(0, "", error="ReadTimeout: read timed out. (read timeout=20)")
        if url == L.TREE.format(repo="kicad-symbols", page=2):
            self.calls.append(url)
            return Response(200, "[]")
        return super().get(url, headers)


def test_kicad_index_backs_off_after_a_failed_build_and_resumes():
    for fail, hold in (("HTTP 429", L.KiCadLibrary.RETRY_AFTER_429), ("timeout", L.KiCadLibrary.RETRY_AFTER)):
        with tempfile.TemporaryDirectory() as d:
            idx, t = Path(d) / "idx.json", [1000.0]
            http = _Gitlab429(fail)
            lib = L.KiCadLibrary(http, idx, now=lambda: t[0])
            assert not lib.symbols and "page 2" in lib.error and not idx.exists(), lib.error
            assert set(http.timeouts) == {L.KiCadLibrary.PAGE_TIMEOUT}, http.timeouts     # short per-page timeout
            retry = json.loads(idx.with_name("idx.json.retry.json").read_text())
            assert retry["after"] == 1000.0 + hold, retry
            n = len(http.calls)
            t[0] += hold - 1                                     # within the pause: no request at all
            lib = L.KiCadLibrary(http, idx, now=lambda: t[0])
            assert len(http.calls) == n and not lib.symbols and "not retried until" in lib.error, lib.error
            lib = L.KiCadLibrary(http, idx, refresh=True, now=lambda: t[0])        # --refresh-index tries now
            assert len(http.calls) > n and not lib.symbols
            t[0] += hold + 1                                     # after it: tries again; the network works now
            http.ok = True
            lib = L.KiCadLibrary(http, idx, now=lambda: t[0])
            assert lib.symbols["TPS1234DSG"] == ["Regulator_Test"] and lib.error is None and idx.exists()
            assert not idx.with_name("idx.json.retry.json").exists()


def test_http_client_takes_a_per_request_timeout():
    from ecadlib.httpcache import CachedHTTP
    with tempfile.TemporaryDirectory() as d:
        h = CachedHTTP(d, min_interval=0, timeout=60)
        seen = []

        def req(method, url, data=None, headers=None, timeout=None):
            seen.append(timeout)
            raise __import__("requests").ConnectionError("no network here")
        h.s.request = req
        assert h.get("https://gitlab.com/x", timeout=20).error and h.get("https://gitlab.com/y").error
        assert seen == [20, 60], seen



def test_review_names_the_real_target_and_csvs_open_in_excel():
    import pandas as pd
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "run").mkdir()
        items, _ = run(Path(d) / "run")
        out = Path(d) / "run" / "out"
        assert "ready for Altium 365" not in (out / "review.md").read_text(encoding="utf-8-sig")   # no workspace named
        for f in ("review.csv", "triage.csv", "release_check.csv"):
            raw = (out / f).read_bytes()
            assert raw[:3] == b"\xef\xbb\xbf", f                    # UTF-8 with a BOM: Excel shows ± and µ, not Â±
            assert not pd.read_csv(out / f, dtype=str).columns[0].startswith("\ufeff"), f

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
