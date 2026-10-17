"""Offline tests for ecadlib.symbol_layout (grouped big-chip symbols). Run:  python3 -m tests.test_symbol_layout

Pin lists (tests/fixtures/symbols): an STM32F103-style 100-pin LQFP, an ESP32-WROOM-32-style module and an
FT232RL-style USB-UART bridge, typed from the vendors' pinout tables (names as printed there)."""
import copy, json, tempfile, xml.etree.ElementTree as ET
from pathlib import Path

from ecadlib import altium_lib as AL, kicad_export as X, rules as RU, rules_infer as RI, sexpr, symbol_layout as SL

FIX = Path(__file__).parent / "fixtures" / "symbols"
STM = SL.pins_from_csv(FIX / "stm32f103vet6_lqfp100.csv")
ESP = SL.pins_from_csv(FIX / "esp32_wroom_32.csv")
FTDI = SL.pins_from_csv(FIX / "ft232rl_ssop28.csv")
ORIENT_SIDE = {0: "right", 1: "top", 2: "left", 3: "bottom"}


def _groups(pins, rules=None, split=None):
    out = {}
    for p in SL.arrange_pins(pins, rules, split):
        out.setdefault(p["group"], []).append(p)
    return out


def _pins(sym):
    return [pr for pr in sym["prims"] if pr[0] == "pin"]


def _assert_tidy(sym, grid=100):
    assert SL.check(sym, grid) == [], SL.check(sym, grid)
    g = grid // 10
    parts = sym.get("prim_parts") or [1] * len(sym["prims"])
    for pr, u in zip(sym["prims"], parts):                  # pins sit on the body edge of their own part
        if pr[0] == "rect":
            assert all(v % g == 0 for v in pr[1:5]), pr
    rects = {u: pr for pr, u in zip(sym["prims"], parts) if pr[0] == "rect"}
    for pr, u in zip(sym["prims"], parts):
        if pr[0] != "pin":
            continue
        _, p, side, x, y, *_ = pr
        r = rects[u]
        if side in ("left", "right"):
            assert x == (r[1] if side == "left" else r[3]) and r[4] < y < r[2], (p, r)
        else:
            assert y == (r[2] if side == "top" else r[4]) and r[1] < x < r[3], (p, r)


def test_classify_names():
    c = lambda name, typ="io", num="1": SL.classify({"name": name, "type": typ, "num": num})
    cases = {"PA9/USART1_TX": "ports", "PH0-OSC_IN": "ports", "PC13-TAMPER-RTC": "ports", "GPIO12": "ports", "IO0": "ports",
             "P0.3": "ports", "VDD_3": "power", "3V3": "power", "VBAT": "power", "VDDA": "analog", "VREF+": "analog",
             "VSS_1": "ground", "VREF-": "ground", "AGND": "ground", "3V3OUT": "power_out", "VCAP_1": "power_out",
             "NRST": "reset", "~{RESET}": "reset", "R\\E\\S\\E\\T\\": "reset", "/RESET": "reset", "RESET#": "reset",
             "RST_N": "reset", "BOOT0": "reset", "EN": "reset", "OSC_IN": "clock", "XTAL1": "clock", "OSCI": "clock",
             "SWDIO": "debug", "TCK": "debug", "U0TXD": "debug", "TXD0": "debug", "USBDP": "usb", "USB_D-": "usb",
             "D+": "usb", "TXD": "uart", "~{CTS}": "uart", "DTR#": "uart", "SDA": "i2c", "I2C1_SCL": "i2c", "MOSI": "spi",
             "~{CS}": "spi", "SD2": "spi", "CANH": "can", "RMII_TXD0": "eth", "MDIO": "eth", "ADC1_IN3": "adc",
             "SENSOR_VP": "adc", "NC": "nc", "N/C": "nc", "DNC": "nc", "EP": "ep", "CBUS0": "other",
             "TRIG": "other_in", "THRES": "other_in", "FB": "other_in", "IN+": "other_in", "DISCH": "other_out", "PGOOD": "other_out",
             "SW": "other_out", "OUT": "power_out"}
    got = {n: c(n) for n in cases}
    assert got == cases, {n: (g, cases[n]) for n, g in got.items() if g != cases[n]}
    assert c("FOO", "output") == "other_out" and c("FOO", "power_in") == "power" and c("X", "nc") == "nc"
    assert c("GND", "power", "EP") == "ground" and c("THERMAL", "passive", "EP") == "ep"   # an exposed pad by its number
    assert SL.classify({"name": "FOO", "group": "debug"}) == "debug"
    assert SL.primary("PA13/JTMS-SWDIO") == "PA13" and SL.port_of("PA13/JTMS-SWDIO") == ("A", 13)
    assert SL.port_of("GPIO5") == ("IO", 5) and SL.port_of("P1_10") == ("1", 10) and SL.port_of("PD") is None
    assert SL.stack_key("VDD_4") == SL.stack_key("VDD") == "VDD" and SL.plain("~{EN}") == "EN"


def test_stm32_lqfp100_grouping_and_order():
    g = _groups(STM)
    assert set(g) == {"power", "analog", "ground", "reset", "clock", "ports", "nc"}, set(g)
    assert [p["name"] for p in g["reset"]] == ["NRST", "BOOT0"] and [p["name"] for p in g["clock"]] == ["OSC_IN", "OSC_OUT"]
    ports = [SL.port_of(p["name"]) for p in g["ports"]]
    assert ports == sorted(ports) and len(ports) == 80                   # PA0..PA15, PB0..PB15, ... PE15 in order
    assert {p["side"] for p in g["power"] + g["analog"]} == {"top"} and {p["side"] for p in g["ground"]} == {"bottom"}
    assert {p["side"] for p in g["reset"] + g["clock"]} == {"left"} and g["nc"][0]["side"] == "right"
    sides = {}
    for p in g["ports"]:
        sides.setdefault(SL.port_of(p["name"])[0], set()).add(p["side"])
    assert sides == {"A": {"left"}, "B": {"left"}, "C": {"right"}, "D": {"right"}, "E": {"right"}}, sides   # whole ports, balanced
    stacked = [p for p in g["power"] + g["ground"] if p.get("stacked_on")]
    assert sorted(p["num"] for p in stacked) == ["10", "100", "11", "27", "28", "74", "75", "99"], stacked
    assert all(p["stacked_on"] in ("49", "50") for p in stacked)           # on VSS_1 (pin 49) / VDD_1 (pin 50)
    assert [p["name"] for p in g["power"]][:3] == ["VBAT", "VDD_1", "VDD_2"]


def test_stm32_symbol_reads_back_altium_and_kicad():
    sym = AL.ic_symbol("STM32F103VET6", STM, "MCU", footprint="LQFP-100_14x14mm_P0.5mm")   # 100 pins: grouped by default
    _assert_tidy(sym)
    pins = _pins(sym)
    assert len(pins) == 100 and "parts" not in sym
    at = {}
    for _, p, side, x, y, L, *_ in pins:
        assert L == 30                                                      # 300 mil, pin end on the 100 mil grid
        at.setdefault((x, y), []).append(p)
    assert len(at) == 100 - 8                                               # VDD x5 and VSS x5 stacked
    stacks = [ps for ps in at.values() if len(ps) > 1]
    assert sorted(len(s) for s in stacks) == [5, 5] and all(not q["show_name"] and not q["show_num"] for s in stacks for q in s[1:])
    rows = [y for (x, y) in at if x == 0]                                   # left column top->bottom: NRST, BOOT0, gap, OSC...
    left = sorted(((y, p[0]["name"]) for (x, y), p in at.items() if x == 0), reverse=True)
    assert [n for _, n in left[:4]] == ["NRST", "BOOT0", "OSC_IN", "OSC_OUT"] and left[1][0] - left[2][0] == 20   # one row gap
    assert len(rows) == 2 + 2 + 32
    with tempfile.TemporaryDirectory() as d:
        f = AL.write_schlib(Path(d) / "stm.SchLib", [sym])
        _, syms = AL.read_schlib(f)
        recs = [r for r in syms[0]["records"] if r.get("RECORD") == "2"]
        assert len(recs) == 100 and sorted(r["num"] for r in recs) == sorted(p["num"] for p in STM)
        assert all(r["x"] % 10 == 0 and r["y"] % 10 == 0 and r["length"] == 30 for r in recs)
        by = {r["num"]: r for r in recs}
        assert ORIENT_SIDE[by["14"]["orient"]] == "left" and ORIENT_SIDE[by["11"]["orient"]] == "top"
        assert ORIENT_SIDE[by["10"]["orient"]] == "bottom" and ORIENT_SIDE[by["46"]["orient"]] == "right"
        notes = []
        ks = X.symbol_from_schlib(f, "STM32F103VET6", notes)
        text = sexpr.dumps(["kicad_symbol_lib", ["version", X.SYM_VERSION], ks])
        back = sexpr.parse(text)
        kp = [p for u in sexpr.find(sexpr.first(back, "symbol"), "symbol") for p in sexpr.find(u, "pin")]
        assert len(kp) == 100
        hidden = [p for p in kp if "hide" in p]
        assert sorted(sexpr.first(p, "number")[1] for p in hidden) == ["10", "100", "11", "27", "28", "74", "75", "99"]
        assert all(p[1] == "passive" for p in hidden) and any("stacked pins" in n for n in notes), notes   # KLC S4.3
        for p in kp:                                                        # KiCad: 2.54 mm grid
            at_ = sexpr.first(p, "at")
            assert all(abs(float(v) / 2.54 - round(float(v) / 2.54)) < 1e-6 for v in at_[1:3]), at_


def test_split_into_parts():
    rules = {"symbols": dict(RU.DEFAULTS["symbols"], split_pins=64, unit_pins=48)}
    sym = AL.ic_symbol("STM32F103VET6", STM, rules=rules)
    _assert_tidy(sym)
    assert sym["parts"] == 3, sym.get("parts")
    pins = [(pr[1], u) for pr, u in zip(sym["prims"], sym["prim_parts"]) if pr[0] == "pin"]
    partA = {p["num"] for p, u in pins if u == 1}
    power = {p["num"] for p in STM if SL.classify(p) in ("power", "analog", "ground", "nc")}
    assert partA == power, partA ^ power                                   # part A: every power, ground and NC pin
    for u in (2, 3):
        assert sum(1 for _, uu in pins if uu == u) <= 48
    port_parts = {}
    for p, u in pins:
        if SL.port_of(p["name"]):
            port_parts.setdefault(SL.port_of(p["name"])[0], set()).add(u)
    assert all(len(v) == 1 for v in port_parts.values()), port_parts       # a port stays in one part
    with tempfile.TemporaryDirectory() as d:
        f = AL.write_schlib(Path(d) / "s.SchLib", [sym])
        hdr, syms = AL.read_schlib(f)
        assert hdr["PARTCOUNT0"] == "4" and int(syms[0]["records"][0]["PARTCOUNT"]) == 4
        assert {r["part"] for r in syms[0]["records"] if r.get("RECORD") == "2"} == {1, 2, 3}
        ks = X.symbol_from_schlib(f, "STM", [])
        units = {u[1] for u in sexpr.find(sexpr.parse(sexpr.dumps(ks)), "symbol")}
        assert units == {"STM_1_1", "STM_2_1", "STM_3_1"}, units
    assert SL.arrange(STM, rules, split=False)[0]["unit"] == 1 and len(SL.arrange(STM, rules, split=False)) == 1


def test_esp32_module():
    g = _groups(ESP)
    assert [p["name"] for p in g["reset"]] == ["EN"] and [p["name"] for p in g["debug"]] == ["RXD0", "TXD0"]
    assert [p["name"] for p in g["adc"]] == ["SENSOR_VP", "SENSOR_VN"] and [p["name"] for p in g["power"]] == ["3V3"]
    io = [SL.port_of(p["name"])[1] for p in g["ports"]]
    assert io == sorted(io) and len(io) == 22                               # IO36/IO39 are named SENSOR_VP/VN
    assert [p["name"] for p in g["spi"]] == ["SD0", "SD1", "SD2", "SD3", "CMD"]          # numbered names in number order
    gnd = g["ground"]
    assert [p["num"] for p in gnd] == ["1", "15", "38", "39"] and all(p.get("stacked_on") == "1" for p in gnd[1:])
    sym = AL.ic_symbol("ESP32-WROOM-32", ESP, layout=True)
    _assert_tidy(sym)
    hl = sum(1 for pr in _pins(sym) if pr[2] == "left")
    hr = sum(1 for pr in _pins(sym) if pr[2] == "right")
    assert abs(hl - hr) <= 8, (hl, hr)                                     # the IO block was shared between the sides


def test_usb_bridge():
    g = _groups(FTDI)
    assert [p["name"] for p in g["usb"]] == ["USBDP", "USBDM"] and g["usb"][0]["side"] == "left"
    assert [p["name"] for p in g["uart"]] == ["TXD", "DTR#", "RTS#", "RXD", "RI#", "DSR#", "DCD#", "CTS#"]   # datasheet order kept
    assert g["uart"][0]["side"] == "right" and [p["name"] for p in g["power_out"]] == ["3V3OUT"]
    assert [p["name"] for p in g["other"]] == ["CBUS0", "CBUS1", "CBUS2", "CBUS3", "CBUS4"]
    assert [p["name"] for p in g["ground"]] == ["GND", "GND", "GND", "AGND"] and [p["name"] for p in g["nc"]] == ["NC", "NC"]
    sym = AL.ic_symbol("FT232RL", FTDI, layout=True)
    _assert_tidy(sym)
    nc = [pr for pr in _pins(sym) if pr[1]["name"] == "NC"]
    assert len({(pr[3], pr[4]) for pr in nc}) == 2                         # NC pins are never stacked


def test_small_parts_unchanged_and_threshold():
    mk = lambda n: [{"num": str(i), "name": f"P{i}", "type": "io", "side": "left" if i % 2 else "right"} for i in range(1, n + 1)]
    old = {"symbols": {"layout_pins": 40}}                                  # the old threshold still works when set
    for n in (8, 39):
        assert AL.schlib_bytes([AL.ic_symbol("X", mk(n), rules=old)]) == AL.schlib_bytes([AL.ic_symbol("X", mk(n), layout=False)])
    assert AL.ic_symbol("X", mk(40), rules=old) != AL.ic_symbol("X", mk(40), layout=False)
    assert AL.ic_symbol("X", mk(8)) != AL.ic_symbol("X", mk(8), layout=False)   # default: every IC gets the house rule
    assert AL.ic_symbol("X", mk(60), rules={"layout_pins": 0}) == AL.ic_symbol("X", mk(60), layout=False)
    d = {"name": "J", "pins": FTDI, "layout": True}
    assert AL.symbol_from_json(d) == AL.ic_symbol("J", FTDI, layout=True)
    assert SL.wants_layout(1) and not SL.wants_layout(0) and SL.wants_layout(10, {"symbols": {"layout_pins": 10}})
    assert not SL.wants_layout(39, old) and SL.wants_layout(40, old)


def test_stack_modes_and_settings():
    none = AL.ic_symbol("S", STM, rules={"stack_power": "none"})
    _assert_tidy(none)
    assert len({(pr[3], pr[4]) for pr in _pins(none)}) == 100
    hide = AL.ic_symbol("S", STM, rules={"stack_power": "hide"})
    _assert_tidy(hide)
    hidden = [pr[1] for pr in _pins(hide) if pr[1].get("hidden")]
    assert len(hidden) == 8 and {p["net"] for p in hidden} == {"VDD", "VSS"}
    with tempfile.TemporaryDirectory() as d:
        f = AL.write_schlib(Path(d) / "h.SchLib", [hide])
        _, syms = AL.read_schlib(f)
        nets = [r for r in syms[0]["records"] if r.get("NAME") == "HiddenNetName"]
        assert sorted(r["TEXT"] for r in nets) == ["VDD"] * 4 + ["VSS"] * 4
    s50 = AL.ic_symbol("S", STM, rules={"grid": 50, "pin_length": 200})
    _assert_tidy(s50, 50)
    assert {pr[5] for pr in _pins(s50)} == {20}
    rules = {"order": ["ports"] + [g for g in SL.ORDER if g != "ports"], "sides": {"nc": "left"}}
    g = _groups(FTDI, rules)
    assert g["nc"][0]["side"] == "left"


def test_rules_symbols_section():
    r = RU.load({"symbols": {"layout_pins": 24, "split_pins": 120, "stack_power": "hide", "grid": 50, "pin_length": 250,
                             "sides": {"debug": "right"}}})
    assert r["symbols"]["layout_pins"] == 24 and r["symbols"]["unit_pins"] == 48 and r["symbols"]["sides"] == {"debug": "right"}
    assert SL.settings(r)["split_pins"] == 120 and SL.settings(None) == RU.DEFAULTS["symbols"]
    for bad, msg in (({"grid": 25}, "symbols.grid"), ({"pin_length": 250}, "symbols.pin_length"), ({"stack_power": "x"}, "stack_power"),
                     ({"order": ["power", "bogus"]}, "symbols.order"), ({"sides": {"ports": "middle"}}, "symbols.sides.ports"),
                     ({"sides": {"bogus": "left"}}, "symbols.sides.bogus"), ({"unit_pins": 2}, "symbols.unit_pins"),
                     ({"layout_pin": 3}, "did you mean symbols.layout_pins")):
        try:
            RU.load({"symbols": bad})
            raise AssertionError(f"{bad} accepted")
        except RU.RulesError as e:
            assert msg in str(e), (bad, str(e))
    text = RU.dumps(RU.DEFAULTS, only={f"symbols.{k}" for k in RU.DEFAULTS["symbols"]})
    assert "[symbols]" in text and "layout_pins = 1" in text and 'power_side = "top"' in text and "stack_power = \"stack\"" in text


def test_relayout_keeps_fields():
    plain = AL.ic_symbol("BIG", [dict(p, side="left") for p in STM[:48]], "desc", "U?", "FP", layout=False)
    plain["params"] = [("MPN", "BIG")]
    new = SL.relayout(plain)
    assert new["params"] == [("MPN", "BIG")] and new["footprint"] == "FP" and new["description"] == "desc"
    assert {pr[1]["num"] for pr in _pins(new)} == {p["num"] for p in STM[:48]}
    assert {pr[2] for pr in _pins(new)} == {"left", "right", "top", "bottom"}
    multi = dict(plain, parts=2, prim_parts=[1] * len(plain["prims"]))
    assert SL.relayout(multi) is multi


def test_cli_preview_and_kicad():
    with tempfile.TemporaryDirectory() as d:
        out, ks, svg = Path(d) / "o" / "stm.SchLib", Path(d) / "stm.kicad_sym", Path(d) / "stm.svg"
        rc = SL.main([str(FIX / "stm32f103vet6_lqfp100.csv"), "-o", str(out), "--kicad", str(ks), "--preview", str(svg),
                      "--split", "64", "--name", "STM32F103VET6", "--rules", str(_rules_file(d))])
        assert rc == 0 and out.exists()
        _, syms = AL.read_schlib(out)
        assert syms[0]["name"] == "STM32F103VET6" and int(syms[0]["records"][0]["PARTCOUNT"]) == 4
        t = sexpr.parse(ks.read_text())
        assert t[0] == "kicad_symbol_lib" and len(list(sexpr.find(sexpr.first(t, "symbol"), "symbol"))) == 3
        root = ET.fromstring(svg.read_text())
        texts = [e.text for e in root.iter("{http://www.w3.org/2000/svg}text")]
        assert "PA0-WKUP" in texts and "NRST" in texts and any(x and "part C" in x for x in texts)
        name, pins, _ = SL.load_pins(out)                                   # a SchLib in: re-layout of its pins
        assert name == "STM32F103VET6" and len(pins) == 100
        j = Path(d) / "pins.json"
        j.write_text(json.dumps({"symbol": {"name": "FT232RL", "pins": FTDI, "designator": "U?"}}))
        assert SL.load_pins(j)[0] == "FT232RL" and SL.load_pins(j)[1] == FTDI


def _rules_file(d):
    f = Path(d) / "ecadlib_rules.json"
    f.write_text(json.dumps({"symbols": {"unit_pins": 48}}))
    return f


def test_easyeda_big_symbol_grouped_and_exported_the_same():
    from tests import test_librarian as TL
    ee = copy.deepcopy(TL.EE)
    names = [p["name"] for p in STM[:48]]
    shape = ["R~390~290~~~20~500~#880000~1~0~none~gge1~0"]
    for i, n in enumerate(names, 1):
        y = 295 + 10 * i
        shape.append(f"P~show~1~{i}~380~{y}~0~gge{i}p~0^^380~{y}^^M 380 {y} h 10~#880000^^1~380~{y}~0~{n}~start~~~#0000FF"
                     f"^^1~380~{y}~0~{i}~end~~~#0000FF^^0~380~{y}^^0~M 0 0")
    ee["result"]["dataStr"]["shape"] = shape
    ee["result"]["dataStr"]["head"]["c_para"]["Manufacturer Part"] = ee["result"]["title"] = "BIGEE1"
    ee["result"]["lcsc"]["number"] = "C4004"
    bom = TL.BOM.split("\n")[0] + "\nU9,1,BIGEE1,,ACME,BIGEE1,C4004,EasyEDA 48-pin part,\n"
    with tempfile.TemporaryDirectory() as d:
        http = TL.FakeHTTP({"C4004": ee})
        items, _ = TL.run(d, http, bom=bom)
        it = items["BIGEE1"]
        assert it["source"]["symbol"] == "easyeda:C4004", it["source"]
        assert any("48 pins grouped by function" in n for n in it["librarian"]["notes"]), it["librarian"]["notes"]
        _, syms = AL.read_schlib(Path(d) / "out" / it["symbol"]["file"])
        recs = [r for r in syms[0]["records"] if r.get("RECORD") == "2"]
        assert {ORIENT_SIDE[r["orient"]] for r in recs} == {"left", "right", "top", "bottom"}   # no longer one long column
        rep = X.export(Path(d) / "out" / "plan.json", Path(d) / "kicad", "lib", include_held=True, http=http, log=lambda *a: None)
        row = next(r for r in rep["parts"] if r["part"] == "BIGEE1")
        assert "read back" in row["symbol"] and "grouped by function" in row["symbol"], row["symbol"]
        ks = sexpr.parse((Path(d) / "kicad" / "lib.kicad_sym").read_text())
        sym = next(s for s in sexpr.find(ks, "symbol") if s[1] == X.kname("BIGEE1"))
        sides = {sexpr.first(p, "at")[3] for u in sexpr.find(sym, "symbol") for p in sexpr.find(u, "pin")}
        assert len(sides) == 4, sides


def test_infer_symbols_from_schlib():
    with tempfile.TemporaryDirectory() as d:
        for i in range(3):
            AL.write_schlib(Path(d) / f"s{i}.SchLib", [AL.ic_symbol(f"S{i}", FTDI, layout=True, rules={"pin_length": 200})])
        lib = RI.scan([d])
        got = {s["key"]: s["value"] for s in RI.infer_symbols(lib)}
        assert got == {"symbols.pin_length": 200}, got                     # the 100 mil grid is ecadlib's already
        prop = RI.propose([d])
        assert "pin_length = 200" in prop["toml"] and not prop["errors"], prop["errors"]


def test_workspace_and_mcp_preview_symbol():
    from ecadlib import app, mcp_server as M
    with tempfile.TemporaryDirectory() as d:
        ws = app.Workspace(Path(d) / "ws", libs=[], offline=True)
        (ws.root / "ecadlib_rules.json").write_text('{"symbols": {"split_pins": 64}}')      # JSON: Python 3.10 too
        r = ws.preview_symbol(STM, "STM32F103VET6")
        assert r["parts"] == 3 and r["problems"] == [] and r["svg"].startswith("<svg") and "A:top:power" in r["groups"], r["groups"]
        r = M.call_tool(ws, "preview_symbol", {"pins": FTDI, "name": "FT232RL", "split": 0, "stack": "none", "save": True})
        assert r["parts"] == 1 and r["file"].endswith("FT232RL.SchLib") and (ws.root / r["file"]).exists()
        assert len(r["groups"]["A:bottom:ground"]) == 4                     # stack none: every ground pin drawn
        try:
            ws.preview_symbol(FTDI, "FT232RL", save=True)
            raise AssertionError("overwrote")
        except FileExistsError:
            pass
        assert "preview_symbol" in {t["name"] for t in M.TOOLS}


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_symbol_layout"]))


NE555 = [("1", "GND"), ("2", "TRIG"), ("3", "OUT"), ("4", "RST"), ("5", "CONT"), ("6", "THRES"), ("7", "DISCH"), ("8", "VCC")]


def _side_of(sym):
    return {pr[1]["name"]: pr[2] for pr in _pins(sym)}


def test_house_rule_small_ic_both_variants():
    """Oliver's rule: inputs left, outputs right, power top, ground bottom; or top and bottom empty with power at the
    top of the left side and ground at its bottom. An 8-pin 555 with no pin types gets it from the names alone."""
    pins = [{"num": n, "name": m, "type": "passive"} for n, m in NE555]
    sym = AL.ic_symbol("NE555", pins)
    _assert_tidy(sym)
    s = _side_of(sym)
    assert s["VCC"] == "top" and s["GND"] == "bottom", s
    assert {s[n] for n in ("TRIG", "THRES", "CONT", "RST")} == {"left"} and {s[n] for n in ("OUT", "DISCH")} == {"right"}, s
    sym = AL.ic_symbol("NE555", pins, rules={"symbols": {"power_side": "left"}})
    _assert_tidy(sym)
    s = _side_of(sym)
    assert set(s.values()) == {"left", "right"} and s["VCC"] == s["GND"] == "left", s
    left = sorted((pr for pr in _pins(sym) if pr[2] == "left"), key=lambda pr: -pr[4])
    assert left[0][1]["name"] == "VCC" and left[-1][1]["name"] == "GND"           # power top-left, ground bottom-left
    assert RU.load({"symbols": {"power_side": "left"}})["symbols"]["power_side"] == "left"
    try:
        RU.load({"symbols": {"power_side": "middle"}})
        raise AssertionError("bad power_side accepted")
    except RU.RulesError as e:
        assert "power_side" in str(e)


def test_type_beats_a_direction_guess_and_regulator():
    c = lambda n, t="passive": SL.classify({"name": n, "type": t})
    assert c("A", "output") == "other_out" and c("OUT2", "input") == "other_in"   # the type is known: it wins
    ldo = [{"num": "1", "name": "IN", "type": "power_in"}, {"num": "2", "name": "GND", "type": "power_in"},
           {"num": "3", "name": "EN"}, {"num": "4", "name": "NC"}, {"num": "5", "name": "OUT", "type": "power_out"}]
    s = _side_of(AL.ic_symbol("LDO", ldo))
    assert s["EN"] == "left" and s["OUT"] == "right" and s["GND"] == "bottom", s


def test_text_overlap_found_like_the_altium_ne555():
    """The NE555DR symbol from EasyEDA in Altium (16 Oct): top pin names ran down into the side pin names and GND ran up
    into THRES / CONT. text_problems sees it; the house layout of the same pins has none."""
    pin = lambda n, side, x, y: ("pin", {"num": n, "name": dict(NE555)[n]}, side, x, y, 20)
    bad = {"name": "NE555", "designator": "U?", "prims": [("rect", 0, 0, 40, -50), pin("8", "top", 15, 0), pin("4", "top", 25, 0),
                                         pin("7", "left", 0, -15), pin("2", "left", 0, -25), pin("6", "left", 0, -35),
                                         pin("3", "right", 40, -20), pin("5", "right", 40, -30), pin("1", "bottom", 20, -50)]}
    probs = SL.text_problems(bad)
    assert any("pin 8 name overlaps pin 7 name" in p for p in probs) and any("pin 1 name" in p for p in probs), probs
    assert SL.is_box_ic(bad)
    good = SL.relayout(bad)
    assert SL.text_problems(good) == [] and SL.check(good) == []


def test_only_plain_ic_boxes_are_relaid():
    pin = lambda n, side, x, y: ("pin", {"num": n, "name": n}, side, x, y, 20)
    box = {"designator": "U?", "prims": [("rect", 0, 0, 40, -40), pin("1", "left", 0, -10), pin("2", "left", 0, -20),
                                         pin("3", "right", 40, -10)]}
    assert SL.is_box_ic(box)
    assert not SL.is_box_ic(dict(box, designator="J?"))                         # connector: pin order is the drawing
    assert not SL.is_box_ic(dict(box, designator="Q?"))                         # a 3-pin Q is a transistor
    assert not SL.is_box_ic(dict(box, prims=box["prims"] + [("polyline", [(5, -5), (20, -20)])]))   # a drawing inside
    assert not SL.is_box_ic(dict(box, prims=box["prims"][:1] + [pin("1", "left", -10, -10)] + box["prims"][2:]))


def test_easyeda_small_ic_gets_the_house_rule():
    from tests import test_librarian as TL
    ee = copy.deepcopy(TL.EE)
    at = {"8": (395, 290), "4": (405, 290), "7": (380, 305), "2": (380, 315), "6": (380, 325), "3": (430, 300),
          "5": (430, 310), "1": (400, 340)}
    shape = ["R~390~290~~~30~50~#880000~1~0~none~gge1~0"]
    for n, name in NE555:
        x, y = at[n]
        path = {380: f"M 380 {y} h 10", 430: f"M 430 {y} h -10"}.get(x) or (f"M {x} 280 v 10" if y == 290 else f"M {x} 350 v -10")
        cx, cy = (x, 280) if y == 290 else (x, 350) if y == 340 else (x, y)
        shape.append(f"P~show~0~{n}~{cx}~{cy}~0~gge{n}p~0^^{cx}~{cy}^^{path}~#880000^^1~{cx}~{cy}~0~{name}~start~~~#0000FF"
                     f"^^1~{cx}~{cy}~0~{n}~end~~~#0000FF^^0~{cx}~{cy}^^0~M 0 0")
    ds = ee["result"]["dataStr"]
    ds["shape"] = shape
    ds["head"]["c_para"]["pre"] = "U?"
    ds["head"]["c_para"]["Manufacturer Part"] = ee["result"]["title"] = "NE555DRX"
    ee["result"]["lcsc"]["number"] = "C4005"
    bom = TL.BOM.split("\n")[0] + "\nU1,1,NE555DRX,,TI,NE555DRX,C4005,timer,\n"
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, TL.FakeHTTP({"C4005": ee}), bom=bom)
        it = items["NE555DRX"]
        assert any("8 pins grouped by function" in n for n in it["librarian"]["notes"]), it["librarian"]["notes"]
        _, syms = AL.read_schlib(Path(d) / "out" / it["symbol"]["file"])
        side = {r["name"]: ORIENT_SIDE[r["orient"]] for r in syms[0]["records"] if r.get("RECORD") == "2"}
        assert side["VCC"] == "top" and side["GND"] == "bottom" and side["TRIG"] == "left" and side["OUT"] == "right", side


def test_dual_op_amp_pins_go_one_channel_at_a_time():
    """LM358 in Altium (PR #29 run): inputs came out 1IN-, 2IN-, 1IN+, 2IN+; per amplifier reads better. Buses keep their order."""
    names = [("1OUT", "output"), ("1IN-", "input"), ("1IN+", "input"), ("GND", "power_in"),
             ("2IN+", "input"), ("2IN-", "input"), ("2OUT", "output"), ("VCC", "power_in")]
    pins = [{"name": n, "num": str(i + 1), "type": t} for i, (n, t) in enumerate(names)]
    left = [p["name"] for p in SL.arrange_pins(pins) if p["side"] == "left"]
    assert left == ["1IN-", "1IN+", "2IN-", "2IN+"], left
    bus = [{"name": f"{s}{i}", "num": str(n), "type": "bidirectional"} for n, (s, i) in enumerate([(s, i) for s in "AD" for i in range(4)])]
    assert [p["name"] for p in SL.arrange_pins(bus)][:4] == ["A0", "A1", "A2", "A3"]
