"""The symbol fixes from placing the released A365 parts in AD26 (2026-10-19, #47): SS34 was drawn backwards, R/C bodies
printed "1"/"2", the resistor was the IEC box (Oliver wants the US zigzag), 2-pin pin ends were on 50 mil, and the
STM32's stacked VDD_1..3 made Altium draw a junction dot. Each test reads the written Altium file back, as AD26 would."""
import json, re, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, kicad_export as X, kicad_import as K, rules as RU, sexpr, stdparts as SP, \
    symbol_layout as SL
from tests import test_librarian as TL
from tests import test_symbol_layout as TS

TWO_PIN = [k for k in SP.SYMBOLS]
DIODES = [k for k in SP.SYMBOLS if SP.SYMBOLS[k][1] == "D"]
STEP = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}         # Altium pin orientation -> direction from the body


def _altium(key):
    """stdparts symbol -> SchLib on disk -> (symbol dict, pin records read back)."""
    text, name = SP.symbol_text(key)
    sym = K.convert_symbol([K._find_symbol(sexpr.parse(text), name)], name, None, [], None, [])
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / f"{name}.SchLib"
        AL.write_schlib(f, [sym])
        _, syms = AL.read_schlib(f)
        ks = X.symbol_from_schlib(f, name, [])
    pins = [r for r in syms[0]["records"] if r.get("RECORD") == "2"]
    return sym, pins, syms[0]["records"], ks


def _hotspot(p):
    dx, dy = STEP[p["orient"]]
    return p["x"] + dx * p["length"], p["y"] + dy * p["length"]


def test_two_pin_symbols_read_back_without_pin_text():
    for key in TWO_PIN:
        _, pins, _, _ = _altium(key)
        assert len(pins) == 2, key
        for p in pins:
            assert not p["conglomerate"] & 0x10, (key, p["num"], "pin number shown")
            assert not p["conglomerate"] & 0x08, (key, p["num"], "pin name shown")
            assert not p["conglomerate"] & 0x04, (key, p["num"], "pin hidden: it would not connect")


def test_two_pin_hotspots_on_the_100_mil_grid():
    for key in TWO_PIN:
        _, pins, _, _ = _altium(key)
        ends = [_hotspot(p) for p in pins]
        assert all(x % 10 == 0 and y % 10 == 0 for x, y in ends), (key, ends)        # 10 units = 100 mil
        assert sorted(abs(v) for e in ends for v in e if v) == [20, 20], (key, ends)  # +-200 mil, one axis
        assert ends[0] != ends[1]


def test_pins_touch_the_body():
    """No gap between a pin's inner end and the drawing: the wire visibly reaches the part."""
    for key in TWO_PIN:
        graphics = [[(float(a), float(b)) for a, b in re.findall(r"\((?:xy|start|end|mid) (-?[\d.]+) (-?[\d.]+)\)", g)]
                    for g in SP.SYMBOLS[key][3]]
        for num, _n, _t, x, y, ang, ln in SP.SYMBOLS[key][4]:
            dx, dy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[ang]
            inner = (round(x + dx * ln, 3), round(y + dy * ln, 3))
            axis = 0 if dx else 1
            on_axis = [g for g in graphics if min(p[1 - axis] for p in g) <= 0 <= max(p[1 - axis] for p in g)]
            meets = [p for g in on_axis for p in g if abs(p[axis] - inner[axis]) < 1e-6]   # "+" and LED arrows aside
            assert meets, (key, num, inner)
            assert all(abs(p[axis]) <= abs(inner[axis]) + 0.64 for g in on_axis for p in g), (key, num)  # at most a bar hook past it


def test_cathode_pin_is_on_the_bar_side():
    """SS34 had pin 1 (K) at the triangle's flat side. The triangle apex is the cathode end; K must be on that side."""
    for key in DIODES:
        text = SP.symbol_text(key)[0]
        tri = next(p for p in re.findall(r"\(polyline \(pts ((?:\(xy [^)]+\) ?)+)\)", text) if p.count("(xy") == 4)
        xs = [float(x) for x in re.findall(r"\(xy (-?[\d.]+) ", tri)]   # the closed triangle: base x three times, apex once
        apex = next(x for x in xs if xs.count(x) == 1)
        pins = {p[0]: p for p in SP.SYMBOLS[key][4]}
        assert pins["1"][1] == "K" and pins["2"][1] == "A", key
        assert (pins["1"][3] < 0) == (apex < 0), (key, apex, pins["1"])
        _, recs, _, _ = _altium(key)
        k = next(p for p in recs if p["num"] == "1")
        assert (_hotspot(k)[0] < 0) == (apex < 0), (key, _hotspot(k), apex)


def test_resistor_body_is_the_zigzag_in_altium():
    _, _, recs, _ = _altium("R_US")
    kinds = [r.get("RECORD") for r in recs]
    assert "6" in kinds and "14" not in kinds, kinds                      # polyline, no rectangle
    poly = next(r for r in recs if r.get("RECORD") == "6")
    n = int(poly["LOCATIONCOUNT"])
    assert n == 10, poly
    xs = [int(poly.get(f"X{i}", 0)) for i in range(1, n + 1)]
    assert max(xs) > 0 > min(xs) and max(xs) == -min(xs), xs               # symmetric zigzag
    _, _, recs, _ = _altium("R")
    assert "14" in [r.get("RECORD") for r in recs]                          # the IEC box is still there for iec


def test_symbols_read_back_tidy_and_into_kicad():
    for key in TWO_PIN:
        sym, _, _, ks = _altium(key)
        assert SL.text_problems(sym) == [], (key, SL.text_problems(sym))
        k = sexpr.dumps(ks)
        assert "(pin_numbers hide)" in k and "hide" in k.split("pin_names")[1][:40], (key, k[:300])


def test_librarian_output_has_the_fixed_symbols():
    """End to end from a BOM, standard source only (no network), as the sensor-board run that reached AD26."""
    bom = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
           "R1,1,10k,R_0402_1005Metric,Yageo,RC0402FR-0710KL,,Resistor 10k 0402,https://example.com/r.pdf\n"
           "C1,1,100n,C_0402_1005Metric,Murata,GRM155R71C104KA88D,,Capacitor 100nF 0402,https://example.com/c.pdf\n"
           "D1,1,SS34,D_SMA,onsemi,SS34,,Schottky diode 40V 3A SMA,https://example.com/d.pdf\n")
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, bom=bom, http=TL.FakeHTTP(), sources=["standard"])
        out = Path(d) / "out"
        got = {}
        for it in items.values():
            assert it["symbol"], (it["name"], it["librarian"]["tried"])
            _, syms = AL.read_schlib(out / it["symbol"]["file"])
            recs = syms[0]["records"]
            pins = {r["num"]: r for r in recs if r.get("RECORD") == "2"}
            assert all(not p["conglomerate"] & 0x18 for p in pins.values()), (it["name"], pins)
            assert all(v % 10 == 0 for p in pins.values() for v in _hotspot(p)), (it["name"], pins)
            got[it["source"]["symbol"]] = (recs, pins)
        assert set(got) == {"standard:R_US", "standard:C", "standard:D_Schottky"}, set(got)
        assert "6" in [r.get("RECORD") for r in got["standard:R_US"][0]]
        k = got["standard:D_Schottky"][1]["1"]
        assert k["name"] == "K" and _hotspot(k)[0] < 0, k                  # cathode left, where the bar is


def test_iec_house_style_keeps_the_box_end_to_end():
    bom = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
           "R1,1,10k,R_0402_1005Metric,Yageo,RC0402FR-0710KL,,Resistor 10k 0402,https://example.com/r.pdf\n")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ecadlib.toml"
        p.write_text('[symbols]\nresistor = "iec"\n')
        items, _ = TL.run(d, bom=bom, http=TL.FakeHTTP(), sources=["standard"], rules=RU.load(p))
        it = next(iter(items.values()))
        assert it["source"]["symbol"] == "standard:R", it["source"]
        _, syms = AL.read_schlib(Path(d) / "out" / it["symbol"]["file"])
        assert "14" in [r.get("RECORD") for r in syms[0]["records"]]


def test_altium_template_resistor_reads_back():
    for style, rec in (("us", "6"), ("iec", "14")):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "r.SchLib"
            AL.write_schlib(f, [AL.resistor_symbol(style=style)])
            _, syms = AL.read_schlib(f)
            kinds = [r.get("RECORD") for r in syms[0]["records"]]
            assert rec in kinds and kinds.count("2") == 2, (style, kinds)


# ---------------------------------------------------------------- power pins: own end each (stack_power = "none")
def _ends(sym):
    return [(x, y) for _, p, side, x, y, *_ in TS._pins(sym)]


def test_every_fixture_ic_gets_one_end_per_pin_by_default():
    for name, pins in (("STM32F103VET6", TS.STM), ("ESP32-WROOM-32", TS.ESP), ("FT232RL", TS.FTDI)):
        sym = AL.ic_symbol(name, pins, layout=True)
        TS._assert_tidy(sym)
        assert SL.text_problems(sym) == [], (name, SL.text_problems(sym))
        ends = _ends(sym)
        assert len(ends) == len(set(ends)) == len(pins), (name, len(ends), len(set(ends)), len(pins))


def test_stm32_power_pins_all_visible_after_altium_round_trip():
    sym = AL.ic_symbol("STM32F103VET6", TS.STM, layout=True)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "stm.SchLib"
        AL.write_schlib(f, [sym])
        _, syms = AL.read_schlib(f)
    pins = [r for r in syms[0]["records"] if r.get("RECORD") == "2"]
    power = [p for p in pins if re.match(r"VDD_|VSS_", p["name"])]
    assert len(power) == 10, [p["name"] for p in power]                    # VDD_1..5, VSS_1..5
    assert all(p["conglomerate"] & 0x18 == 0x18 and not p["conglomerate"] & 0x04 for p in power), power
    assert len({_hotspot(p) for p in pins}) == len(pins) == 100


def test_stack_and_hide_still_work_when_asked():
    for mode, check in (("stack", lambda s: len(set(_ends(s))) == 100 - 8),
                        ("hide", lambda s: sum(1 for pr in TS._pins(s) if pr[1].get("hidden")) == 8)):
        sym = AL.ic_symbol("STM32F103VET6", TS.STM, layout=True, rules={"symbols": dict(RU.DEFAULTS["symbols"], stack_power=mode)})
        assert check(sym), mode


def test_stack_power_setting_is_validated_and_documented():
    assert RU.DEFAULTS["symbols"]["stack_power"] == "none"
    assert RU.HELP["symbols.stack_power"].startswith("none (default")
    try:
        RU.load({"symbols": {"stack_power": "pile"}})
        raise AssertionError("stack_power = pile accepted")
    except RU.RulesError as e:
        assert "stack_power" in str(e)
    for mode in SL.STACKS:
        assert RU.load({"symbols": {"stack_power": mode}})["symbols"]["stack_power"] == mode


# ---------------------------------------------------------------- EasyEDA diodes: the triangle (PT path) was dropped
EE_DIODES = sorted((Path(__file__).parent / "fixtures" / "easyeda_diodes").glob("C*.json"))   # REAL: JLC/EasyEDA symbols


def _ee(f):
    from ecadlib import easyeda_convert as EC
    d = json.loads(f.read_text(encoding="utf-8"))
    notes = []
    text = EC.symbol_to_kicad(d["symbol"], "X", notes=notes)
    pins = {m.group(2): float(m.group(1)) for m in
            re.finditer(r'\(pin \w+ line \(at (-?[\d.]+) -?[\d.]+ \d+\).*?\(name "([^"]*)"', text)}
    tris = [[float(x) for x in re.findall(r"\(xy (-?[\d.]+) ", ln)] for ln in text.splitlines()
            if "polyline" in ln and ln.count("(xy") == 4]
    return d, text, pins, [xs for xs in tris if len(set(xs)) == 2], notes


def test_easyeda_diodes_keep_their_triangle_and_polarity():
    assert len(EE_DIODES) == 15, EE_DIODES
    for f in EE_DIODES:
        d, text, pins, tris, notes = _ee(f)
        assert tris, (f.name, d["title"], "no triangle: the PT path was dropped")
        assert not any("PT" in n for n in notes), (f.name, notes)
        xs = tris[0]
        apex = next(x for x in xs if xs.count(x) == 1)
        assert (pins["K"] < 0) == (apex < 0), (f.name, d["title"], pins, apex)     # cathode at the apex, where the bar is


def test_easyeda_ss34_reads_back_in_altium_the_right_way_round():
    f = next(p for p in EE_DIODES if p.name == "C8678.json")                    # SS34, the part placed in AD26
    _, text, _, _, _ = _ee(f)
    sym = K.convert_symbol([K._find_symbol(sexpr.parse(text), "X")], "SS34", None, [], None, [])
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d) / "s.SchLib", [sym])
        _, syms = AL.read_schlib(Path(d) / "s.SchLib")
    recs = syms[0]["records"]
    k = next(r for r in recs if r.get("RECORD") == "2" and r["name"] == "K")
    shapes = [[float(r.get(f"X{i}", 0)) for i in range(1, int(r["LOCATIONCOUNT"]) + 1)]
              for r in recs if r.get("RECORD") in ("6", "7") and r.get("LOCATIONCOUNT")]
    xs = next(x for x in shapes if len(set(x)) == 2 and any(x.count(v) == 1 for v in x))   # the triangle
    apex = next(x for x in xs if xs.count(x) == 1)
    assert (_hotspot(k)[0] < 0) == (apex < 0), (k, xs)
    assert all(v % 10 == 0 for v in _hotspot(k)), k                              # EasyEDA's 200 mil pin ends stay on grid


def test_path_runs():
    from ecadlib import easyeda_convert as EC
    assert EC._path_runs("M 5 -6 L -5 0 L 5 7 Z") == ([[(5, -6), (-5, 0), (5, 7), (5, -6)]], False)
    assert EC._path_runs("M0,0 h 10 v 5 Z m 1 1 l 2 2")[0] == [[(0, 0), (10, 0), (10, 5), (0, 0)], [(1, 1), (3, 3)]]
    assert EC._path_runs("M 0 0 L 1 1 2 2") == ([[(0, 0), (1, 1), (2, 2)]], False)    # implicit line-tos
    assert EC._path_runs("M0,0 A 1 1 0 0 1 2 2")[1] is True                       # arcs are reported, not drawn
    assert EC._path_runs("") == ([], False)
