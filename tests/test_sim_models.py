"""Offline tests for ecadlib.sim_models. Run:  python3 -m tests.test_sim_models

Model files under tests/fixtures/sim are HAND-WRITTEN (not vendor models). Plans come from tests.test_librarian's fake
librarian run (RES-0603-10k, LED-RED-0603, TPS1234DSGR, EEPART1 = a G/S/D transistor, DSPART1, NOWHERE1). HTTP is a
fake: no network."""
import copy, io, json, shutil, tempfile, zipfile
from pathlib import Path
from unittest import mock

from ecadlib import altium365 as A365, altium_lib as AL, cli, kicad_export as X, mcp_server as M, sexpr, sim_models as S
from ecadlib.httpcache import Response
from tests import test_librarian as TL

FIX = Path(__file__).parent / "fixtures" / "sim"
VENDOR = FIX / "vendor"


def _plan(d, models=(VENDOR,), attach=True, mutate=None, **kw):
    items, http = TL.run(d)
    plan = Path(d) / "out" / "plan.json"
    if mutate:
        its = json.loads(plan.read_text())
        mutate({i["name"]: i for i in its})
        plan.write_text(json.dumps(its))
    rep = S.run(plan, list(models), attach=attach, http=http, log=lambda *a: None, **kw)
    rep["_http"] = http
    return plan, {i["name"]: i for i in json.loads(plan.read_text())}, rep


def _part(rep, name):
    return next(p for p in rep["parts"] if p["part"] == name)


# ---------------------------------------------------------------- parsers
def test_parse_spice():
    p = S.parse_spice((VENDOR / "EEPART1.lib").read_text())
    assert not p["errors"], p
    assert [(s["name"], s["pins"], s["params"]) for s in p["subckts"]] == [("EEPART1", ["D", "G", "S"], {"RG": "1.5"})]
    assert p["models"] == []                                   # .MODELs inside the .subckt are local to it
    p = S.parse_spice((VENDOR / "led_red.mod").read_text())
    assert p["models"] == [{"name": "LED_RED_0603", "type": "D"}] and not p["subckts"]
    p = S.parse_spice((FIX / "broken.lib").read_text())
    assert any("no .ends" in e for e in p["errors"]), p
    p = S.parse_spice("* x\n.SUBCKT OPA 1 2\n+ 3 4 5 ; comment\nR1 1 2 1k\n.ENDS\n.include other.lib\n.model Q1 NPN(BF=100)\n")
    assert p["subckts"][0]["pins"] == ["1", "2", "3", "4", "5"] and p["includes"] == ["other.lib"]
    assert p["models"] == [{"name": "Q1", "type": "NPN"}]
    assert S.parse_spice("$CDNENCSTART\nabc\n.subckt X 1 2\n.ends\n")["encrypted"]
    assert S.parse_spice("\x00\x01binary")["errors"] == ["binary file, not a SPICE netlist"]
    assert S.parse_spice("nothing here")["errors"] == ["no .subckt or .model found"]


def test_parse_ibis():
    p = S.parse_ibis((FIX / "bridge.ibs").read_text(), "bridge.ibs")
    assert not p["errors"] and not p["warnings"], p
    assert p["version"] == "5.1" and p["file_name"] == "bridge.ibs" and p["models"] == ["IO33", "IN33"]
    c = p["components"][0]
    assert c["name"] == "TPS1234" and c["manufacturer"] == "ACME" and len(c["pins"]) == 8
    assert c["pins"][0] == {"pin": "1", "signal": "P1", "model": "IO33"} and "R_pkg" in c["package"]
    p = S.parse_ibis((FIX / "comment_char.ibs").read_text(), "comment_char.ibs")
    assert p["version"] == "4.2" and p["components"][0]["pins"][0] == {"pin": "A1", "signal": "VDD", "model": "POWER"}
    assert any("MISSINGMODEL" in e for e in p["errors"]), p
    assert any("[File Name]" in w for w in p["warnings"]) and any("[End]" in w for w in p["warnings"]), p
    assert S.parse_ibis(".subckt X 1 2\n.ends\n")["errors"][0].startswith("no [IBIS Ver]")
    assert S.parse_model((FIX / "bridge.ibs").read_text(), "x.txt")["format"] == "ibis"


def test_numbers_and_values():
    assert [S.spice_num(v) for v in (1e4, 1e6, 4.7e-6, 1e-7, 2.2, 0)] == ["10k", "1Meg", "4.7u", "100n", "2.2", "0"]
    assert S.value_of("4k7") == 4700 and abs(S.value_of("100NF") - 1e-7) < 1e-18 and abs(S.value_of("4.7uF") - 4.7e-6) < 1e-15
    assert S.value_of("10k 1%") == 1e4 and S.value_of("RED") is None


# ---------------------------------------------------------------- pin mapping
def test_map_pins():
    opamp = [("1", "OUT"), ("2", "-IN"), ("3", "+IN"), ("4", "V-"), ("5", "V+")]
    m = S.map_pins(["IN+", "IN-", "VCC", "VEE", "OUT"], opamp)
    assert m["status"] == "verified" and m["map"] == {"3": "IN+", "2": "IN-", "5": "VCC", "4": "VEE", "1": "OUT"}, m
    m = S.map_pins(["A", "K"], [("1", "P1"), ("2", "P2")], "D")
    assert m["status"] == "verify" and m["map"] == {"1": "K", "2": "A"}, m          # pin 1 = cathode
    assert S.map_pins(["A", "K"], [("1", "K"), ("2", "A")], "D")["status"] == "verified"
    m = S.map_pins(["1", "2", "3"], [("1", "X"), ("2", "Y"), ("3", "Z")])
    assert m["status"] == "verify" and m["method"].startswith("model node names") and m["map"]["2"] == "2"
    m = S.map_pins(["IN", "OUT", "COM"], [("1", "P1"), ("2", "P2"), ("3", "P3")])
    assert m["status"] == "verify" and m["method"].startswith("by position"), m
    m = S.map_pins(["D", "G", "S"], [("1", "P1"), ("2", "P2")])
    assert m["status"] == "mismatch" and not m["map"] and "not attached" in m["notes"][-1]
    m = S.map_pins(["VIN", "GND", "VOUT"], [("1", "GND"), ("2", "VOUT"), ("3", "VIN"), ("4", "GND"), ("5", "NC")])
    assert m["status"] == "verified" and m["map"] == {"3": "VIN", "1": "GND", "2": "VOUT"}, m
    assert any("share the name" in n for n in m["notes"]) and any("5:NC" in n for n in m["notes"]), m
    assert S.map_pins(["1", "2"], [("1", "1"), ("2", "2")], "R")["status"] == "verified"
    assert S.map_pins(["D", "G", "S"], None)["status"] == "verify"
    assert S.canon("~{SHDN}") == S.canon("S\\H\\D\\N\\") == "SHDNN"


def test_check_ibis_pins():
    comp = S.parse_ibis((FIX / "bridge.ibs").read_text())["components"][0]
    sym = [(str(i), f"P{i}") for i in range(1, 9)]
    r = S.check_ibis_pins(comp, sym, pads=8)
    assert r["status"] == "verified" and r["map"]["5"] == "P5", r
    r = S.check_ibis_pins(comp, sym, pads=9)
    assert any("9 pads" in n for n in r["notes"])
    r = S.check_ibis_pins(comp, [(str(i), f"X{i}") for i in range(1, 9)])
    assert r["status"] == "verify" and any("0 of 8" in n for n in r["notes"]), r
    r = S.check_ibis_pins(comp, [(f"A{i}", "X") for i in range(1, 30)])
    assert r["status"] == "mismatch", r


# ---------------------------------------------------------------- built-in passives
def test_builtin_passives():
    item = {"name": "CAP-0603-100nF", "folder": "Components/Capacitors", "footprint": {"ref": "C_0603_1608Metric"},
            "parameters": [{"name": "Value", "value": "100nF"}, {"name": "ESR", "value": "20m"}]}
    m, why = S.builtin(item, "C")
    assert why is None and m["model_type"] == "subckt" and m["altium"]["per_part"], m
    p = S.parse_spice(m["text"])
    assert not p["errors"] and p["subckts"][0]["pins"] == ["1", "2"] and p["subckts"][0]["name"] == m["name"], p
    assert "C1 1 n1 100n" in m["text"] and "R1 n1 2 20m" in m["text"]
    item["parameters"] = item["parameters"][:1]
    m, _ = S.builtin(item, "C", rules={"sim": {"capacitor_esr": "5m", "capacitor_esl": "0.5n"}})
    assert "R1 n1 n2 5m" in m["text"] and "L1 n2 2 500p" in m["text"], m["text"]
    m, _ = S.builtin(item, "C")
    assert m["model_type"] == "ideal" and m["kicad"] == {"Sim.Device": "C", "Sim.Params": "c=100n"}
    assert S.builtin(dict(item, parameters=[{"name": "Value", "value": "DNP"}]), "C")[0] is None
    l = {"name": "L1", "parameters": [{"name": "Value", "value": "4.7uH"}, {"name": "DCR", "value": "0.1"}]}
    m, _ = S.builtin(l, "L")
    assert "L1 1 n1 4.7u" in m["text"] and "R1 n1 2 100m" in m["text"]


def test_classify():
    it = lambda cat, desc="", fp="", mpn="X1": {"name": mpn, "folder": f"Components/{cat}", "description": desc, "footprint": {"ref": fp},
                                                "parameters": [{"name": "Manufacturer Part Number", "value": mpn}]}
    assert S.classify(it("Resistors")) == ("R", "builtin") and S.classify(it("Capacitors", fp="CP_Elec_5x5")) == ("CP", "builtin")
    assert S.classify(it("Diodes", "LED red")) == ("LED", "spice") and S.classify(it("Diodes", "Schottky")) == ("D", "spice")
    assert S.classify(it("Transistors", "N-channel MOSFET")) == ("MOS", "spice")
    assert S.classify(it("Transistors"), [("1", "B"), ("2", "C"), ("3", "E")]) == ("BJT", "spice")
    assert S.classify(it("ICs", "USB to UART bridge", mpn="CH340C")) == ("DIGITAL", "ibis")
    assert S.classify(it("ICs", "", mpn="STM32F103C8T6")) == ("DIGITAL", "ibis")
    assert S.classify(it("ICs", "3.3V LDO regulator")) == ("REG", "spice") and S.classify(it("ICs", "Dual op-amp")) == ("OPAMP", "spice")
    assert S.classify(it("Connectors")) == (None, None)


# ---------------------------------------------------------------- whole run: Altium + KiCad
def _impl_groups(schlib):
    """[(45, 46, [47...], 48)] records with their indices, checking each OwnerIndex points where it should."""
    _, syms = AL.read_schlib(schlib)
    recs = syms[0]["records"]
    i44 = next(i for i, r in enumerate(recs) if r.get("RECORD") == "44")
    groups, cur = [], None
    for i, r in enumerate(recs[i44 + 1:], i44 + 1):
        rt = r.get("RECORD")
        if rt == "45":
            assert int(r["OWNERINDEX"]) == i44, (i, r)
            cur = {"45": (i, r), "47": []}
            groups.append(cur)
        elif rt == "46":
            assert int(r["OWNERINDEX"]) == cur["45"][0]
            cur["46"] = i
        elif rt == "47":
            assert int(r["OWNERINDEX"]) == cur["46"]
            cur["47"].append(r)
        elif rt == "48":
            assert int(r["OWNERINDEX"]) == cur["45"][0]
    return groups


def test_run_attach_altium_and_kicad():
    with tempfile.TemporaryDirectory() as d:
        plan, items, rep = _plan(d)
        out = plan.parent
        assert {p["part"]: p["pins"] for p in rep["parts"]} == {"RES-0603-10k": "verified", "LED-RED-0603": "verify", "TPS1234DSGR": "verify",
                                                                "EEPART1": "verified", "DSPART1": None, "NOWHERE1": None}, rep["parts"]
        assert not rep.get("errors") and rep["hosts"] == {}, rep           # ACME has no vendor entry: no network
        # Altium: footprint link kept, SIM link added with its pin map, model file beside the SchLib
        g = _impl_groups(out / "models/SchLib/EEPART1.SchLib")
        assert [x["45"][1]["MODELTYPE"] for x in g] == ["PCBLIB", "SIM"], g
        sim = g[1]["45"][1]
        assert sim["MODELNAME"] == "EEPART1" and sim["MODELDATAFILE0"] == "EEPART1.ckt" and sim["ISCURRENT"] == "T"
        assert {r["DESINTF"]: r["DESIMP0"] for r in g[1]["47"]} == {"1": "2", "2": "3", "3": "1"}   # G S D -> nodes D=1 G=2 S=3
        assert S.parse_spice((out / "models/SchLib/EEPART1.ckt").read_text())["subckts"][0]["name"] == "EEPART1"
        assert (out / "models/SchLib/LED_RED_0603.mdl").exists() and (out / "models/SchLib/TPS1234.ckt").exists()
        g = _impl_groups(out / "models/SchLib/Resistor.SchLib")       # generic symbol: no footprint link, one SIM link
        assert [x["45"][1]["MODELNAME"] for x in g] == ["RESISTOR"] and "MODELDATAFILE0" not in g[0]["45"][1]
        assert all(r.get("ISTRIVIAL") == "T" for r in g[0]["47"]) and len(g[0]["47"]) == 2
        hdr, syms = AL.read_schlib(out / "models/SchLib/EEPART1.SchLib")
        assert int(hdr["WEIGHT"]) == len(syms[0]["records"]) + 1           # header record count follows the new records
        assert len([r for r in syms[0]["records"] if r["RECORD"] == "2"]) == 3
        # unverified pin maps hold the part (verify); verified ones stay ready
        assert items["EEPART1"]["status"] == "ready" and items["RES-0603-10k"]["status"] == "ready", items["EEPART1"].get("hold_reasons")
        for n in ("LED-RED-0603", "TPS1234DSGR"):
            assert items[n]["status"] == "held" and any(h.startswith("verify: sim model") for h in items[n]["hold_reasons"]), items[n]
        # re-run: links replaced, not duplicated; verify note not repeated
        rep2 = S.run(plan, [VENDOR], attach=True, http=TL.FakeHTTP(), log=lambda *a: None)
        assert [x["45"][1]["MODELTYPE"] for x in _impl_groups(out / "models/SchLib/EEPART1.SchLib")] == ["PCBLIB", "SIM"]
        items = {i["name"]: i for i in json.loads(plan.read_text())}
        assert sum(v.startswith("sim model") for v in items["TPS1234DSGR"]["verify"]) == 1
        # KiCad: Sim.* fields in the .kicad_sym, model files in <lib>.sim/
        kx = Path(d) / "kicad"
        r = X.export(plan, kx, "mylib", include_held=True, http=rep["_http"], log=lambda *a: None)
        assert not r["errors"], r["errors"]
        syms = {s[1]: {p[1]: p[2] for p in sexpr.find(s, "property")} for s in sexpr.find(sexpr.parse((kx / "mylib.kicad_sym").read_text()), "symbol")}
        e = syms["EEPART1"]
        assert (e["Sim.Device"], e["Sim.Name"], e["Sim.Library"], e["Sim.Pins"]) == ("SUBCKT", "EEPART1", "mylib.sim/EEPART1.lib", "1=G 2=S 3=D"), e
        assert (kx / "mylib.sim/EEPART1.lib").exists()
        r_ = syms["RES-0603-10k"]
        assert r_["Sim.Device"] == "R" and r_["Sim.Params"] == "r=10k" and "Sim.Library" not in r_
        led = syms["LED-RED-0603"]
        assert (led["Sim.Device"], led["Sim.Name"], led["Sim.Pins"]) == ("D", "LED_RED_0603", "1=K 2=A"), led
        assert "Sim.Device" not in syms["DSPART1"] if "DSPART1" in syms else True
        notes = next(p for p in r["parts"] if p["part"] == "TPS1234DSGR")["notes"]
        assert any("pin map to verify" in n for n in notes), notes
        # without --attach nothing is written into the libraries; the model attached above stays in the plan, so the
        # KiCad export keeps matching the .SchLib's SIM link (it used to drop the Sim fields while Altium kept the link)
        before = (out / items["EEPART1"]["symbol"]["file"]).read_bytes()
        rep3 = S.run(plan, [VENDOR], attach=False, http=TL.FakeHTTP(), log=lambda *a: None)
        items = {i["name"]: i for i in json.loads(plan.read_text())}
        assert (out / items["EEPART1"]["symbol"]["file"]).read_bytes() == before
        assert items["EEPART1"]["sim_model"]["attached"] and S.kicad_export_fields(items["EEPART1"], out, kx / "x", "x", [])
        md = (out / "sim_models.md").read_text()
        assert "EEPART1" in md and "Altium 365" in md


def test_mismatch_never_attached_and_shared_symbol():
    def mutate(items):                                 # two transistors on one symbol; the model has 4 pins
        items["EEPART1"]["description"] = "N-channel MOSFET"
    with tempfile.TemporaryDirectory() as d:
        mdir = Path(d) / "m"
        mdir.mkdir()
        (mdir / "EEPART1.sub").write_text(".subckt EEPART1 D G S B\nM1 D G S B NM\n.model NM NMOS\n.ends\n")
        plan, items, rep = _plan(d, models=[mdir], mutate=mutate)
        p = _part(rep, "EEPART1")
        assert p["pins"] == "mismatch" and p["attached"] == [] and any("not attached" in n for n in p["notes"]), p
        assert [x["45"][1]["MODELTYPE"] for x in _impl_groups(plan.parent / "models/SchLib/EEPART1.SchLib")] == ["PCBLIB"]
        assert items["EEPART1"]["status"] == "ready"
    with tempfile.TemporaryDirectory() as d:          # a part-specific model on a shared symbol: not added
        items, http = TL.run(d)
        plan = Path(d) / "out" / "plan.json"
        its = json.loads(plan.read_text())
        twin = copy.deepcopy(next(i for i in its if i["name"] == "EEPART1"))
        twin["name"] = "EEPART2"
        its.append(twin)
        plan.write_text(json.dumps(its))
        rep = S.run(plan, [VENDOR], attach=True, http=http, log=lambda *a: None)
        p = _part(rep, "EEPART1")
        assert p["attached"] == ["kicad"] and any("shared with EEPART2" in n for n in p["notes"]), p


def test_ibis_for_digital_part():
    def mutate(items):
        items["TPS1234DSGR"]["description"] = "USB to UART bridge"
    with tempfile.TemporaryDirectory() as d:
        mdir = Path(d) / "m"
        mdir.mkdir()
        shutil.copy(FIX / "bridge.ibs", mdir)
        shutil.copy(VENDOR / "tps1234.cir", mdir)     # a SPICE model as well: IBIS is preferred for a digital part
        plan, items, rep = _plan(d, models=[mdir], mutate=mutate)
        p = _part(rep, "TPS1234DSGR")
        assert (p["kind"], p["format"], p["name"], p["pins"]) == ("DIGITAL", "ibis", "TPS1234", "verified"), p
        assert any("KiCad: IBIS devices are per pin" in n for n in p["notes"])
        g = _impl_groups(plan.parent / "models/SchLib/TPS1234DSGR.SchLib")
        si = g[-1]["45"][1]
        assert (si["MODELTYPE"], si["MODELNAME"], si["MODELDATAFILE0"]) == ("SI", "TPS1234", "bridge.ibs"), si
        assert (plan.parent / "models/SchLib/bridge.ibs").exists() and items["TPS1234DSGR"]["status"] == "ready"
        assert items["TPS1234DSGR"]["sim_model"]["kicad"] == {}


def test_models_folder_zip_and_suffix_match():
    with tempfile.TemporaryDirectory() as d:
        z = Path(d) / "vendor_models.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.write(VENDOR / "EEPART1.lib", "models/EEPART1.lib")
            zf.writestr("readme.txt", "not a model")
        (Path(d) / "BSS138.lib").write_text(".subckt BSS138 D G S\n.ends\n")
        f = S.ModelFolder([d])
        assert [e["file"].endswith("models/EEPART1.lib") for _, e, _ in f.find("EEPART1")] == [True]
        hits = f.find("BSS138LT1G")
        assert hits and "minus an ordering suffix" in hits[0][2], hits
        assert not f.find("XYZ")


# ---------------------------------------------------------------- network sources (fake HTTP)
class NetHTTP:
    offline = False

    def __init__(self, routes, binary=None, blocked=False):
        self.routes, self.binary, self.calls, self.blocked = routes, binary or {}, [], blocked

    def get(self, url, **kw):
        self.calls.append(url)
        if url in self.routes:
            return Response(200, self.routes[url])
        if url.endswith("/robots.txt") and not self.blocked:
            return Response(404, "")
        return Response(0, "", error="ProxyError: proxy CONNECT refused (403)")

    def post(self, url, body, **kw):
        return self.get(url)

    def get_bytes(self, url):
        self.calls.append(url)
        return (self.binary[url], "") if url in self.binary else (None, "HTTP 404")


def test_vendor_pattern_robots_and_pages():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BSS138.txt", "* nexperia-style model (HAND-WRITTEN)\n.subckt BSS138 D G S\nM1 D G S S NM\n.model NM NMOS\n.ends\n")
    url = "https://assets.nexperia.com/documents/spice-model/BSS138.txt"
    item = {"name": "BSS138BK", "folder": "Components/Transistors", "description": "N-channel MOSFET", "footprint": {},
            "symbol": {}, "parameters": [{"name": "Manufacturer", "value": "Nexperia"}, {"name": "Manufacturer Part Number", "value": "BSS138,215"}]}
    http = NetHTTP({}, {url: buf.getvalue()})
    rec = S.find_one(item, ".", None, S.Net(http), sym_pins=[("1", "G"), ("2", "S"), ("3", "D")])
    assert rec["found"] and rec["name"] == "BSS138" and rec["pin_map"]["status"] == "verified" and rec["source"] == f"vendor:{url}", rec
    assert any("nexperia.com/search" in p for p in rec["pages"]) and "by pin name" == rec["pin_map"]["method"]
    # robots.txt disallows: not fetched
    http = NetHTTP({"https://assets.nexperia.com/robots.txt": "User-agent: *\nDisallow: /documents/\n"}, {url: buf.getvalue()})
    net = S.Net(http)
    rec = S.find_one(item, ".", None, net, sym_pins=[("1", "G"), ("2", "S"), ("3", "D")])
    assert not rec["found"] and url not in http.calls and net.hosts["assets.nexperia.com"] == "robots.txt disallows", (rec, http.calls)
    # blocked host (this sandbox): reported as unreachable, the page links remain for a person
    net = S.Net(NetHTTP({}, blocked=True))
    rec = S.find_one(item, ".", None, net, sym_pins=None)
    assert not rec["found"] and net.hosts["assets.nexperia.com"].startswith("unreachable") and rec["pages"]
    # no network at all
    net = S.Net(NetHTTP({}), enabled=False)
    assert not S.find_one(item, ".", None, net)["found"] and not net.hosts


def test_ibis_list_and_nexar_documents():
    html = ('<ul><li><a href="https://www.st.com/ibis">STMicroelectronics IBIS models</a></li>'
            '<li><a href="/models/acme.html">ACME Inc.</a></li><li><a href="x">Other</a></li></ul>')
    assert S.ibis_list_links(html, "STMicroelectronics") == ["https://www.st.com/ibis"]
    assert S.ibis_list_links(html, "ACME Inc") == ["https://ibis.org/models/acme.html"]
    data = {"data": {"supSearchMpn": {"results": [{"part": {"mpn": "X", "documentCollections": [
        {"name": "Datasheets", "documents": [{"name": "X datasheet", "url": "https://a/x.pdf"}]},
        {"name": "Models", "documents": [{"name": "SPICE model", "url": "https://a/x.lib"}, {"name": "3D STEP model", "url": "https://a/x.step"}]}]}}]}}}
    assert S.nexar_docs(data) == [("Models", "SPICE model", "https://a/x.lib")]
    try:
        from graphql import build_schema, parse, validate
    except ImportError:
        print("skip schema check (pip install graphql-core)"); return
    schema = build_schema(Path("tests/nexar/schema.graphql").read_text(), assume_valid_sdl=True)
    assert not validate(schema, parse(S.NEXAR_QUERY))
    # the release input really has no simulation-model files (what the report says)
    inp = schema.get_type("DesReleaseComponentInput").fields
    assert not [k for k in inp if "sim" in k.lower() or "model" in k.lower()] and "SIMULATION_MODEL" in schema.get_type("DesContentTypeKind").values


def test_nexar_source_used_when_configured():
    item = {"name": "X1", "folder": "Components/ICs", "description": "LDO regulator", "footprint": {}, "symbol": {},
            "parameters": [{"name": "Manufacturer", "value": "ACME"}, {"name": "Manufacturer Part Number", "value": "X1"}]}
    data = {"data": {"supSearchMpn": {"results": [{"part": {"mpn": "X1", "documentCollections": [
        {"name": "Simulation Models", "documents": [{"name": "PSpice", "url": "https://docs.example/x1.lib"}]}]}}]}}}
    http = NetHTTP({"https://api.nexar.com/graphql": json.dumps(data)},
                   {"https://docs.example/x1.lib": b".subckt X1 VIN GND VOUT\n.ends\n"})
    with mock.patch.dict("os.environ", {"NEXAR_SUPPLY_TOKEN": "t"}):
        rec = S.find_one(item, ".", None, S.Net(http), sym_pins=[("1", "GND"), ("2", "VOUT"), ("3", "VIN")])
    assert rec["found"] and rec["source"] == "nexar:https://docs.example/x1.lib" and rec["pin_map"]["status"] == "verified", rec


# ---------------------------------------------------------------- CLI, MCP, Workspace, altium365 push
def test_cli_mcp_and_push_note():
    assert cli.COMMANDS["sim-models"][0] == "ecadlib.sim_models"
    tool = next(t for t in M.TOOLS if t["name"] == "sim_models")
    assert tool["inputSchema"]["required"] == ["bom"]
    with tempfile.TemporaryDirectory() as d:
        plan, items, _ = _plan(d)
        assert S.main([str(plan), "--models", str(VENDOR), "--no-net", "--attach", "--cache", str(Path(d) / "c")]) == 0
        its = json.loads(plan.read_text())
        for i in its:
            for k in ("symbol", "footprint"):
                if i[k].get("file"):
                    i[k]["file"] = str(plan.parent / i[k]["file"])
        res = {r["name"]: r for r in A365.push(its, None, "https://ws", dry_run=True)}
        assert "not uploaded" in res["EEPART1"]["sim_model"] and res["EEPART1"]["action"] == "release", res["EEPART1"]


def test_workspace_sim_models():
    from ecadlib.app import Workspace
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(Path(d) / "ws", [], offline=True)
        bom = ws.root / "uploads" / "bom.csv"
        bom.parent.mkdir(parents=True, exist_ok=True)
        bom.write_text(TL.BOM)
        (Path(d) / "lib").mkdir()
        _, http = TL.run(Path(d) / "lib")
        lib_dir = ws._librarian_dir(bom)
        shutil.copytree(Path(d) / "lib" / "out", lib_dir, dirs_exist_ok=True)
        shutil.copytree(VENDOR, ws.root / "sim_models")
        r = ws.sim_models("uploads/bom.csv")
        assert {p["part"]: p["pins"] for p in r["parts"]}["EEPART1"] == "verified" and r["reports"][0].endswith("sim_models.md"), r
        try:
            ws.sim_models("uploads/bom.csv", models=["../../etc"])
            raise AssertionError("path outside the workspace accepted")
        except ValueError:
            pass



# ---------------------------------------------------------------- review regressions
def test_builtin_zero_esr_with_esl_reaches_node_2():
    item = {"name": "C1", "parameters": [{"name": "Value", "value": "100n"}, {"name": "ESR", "value": "0"}, {"name": "ESL", "value": "1n"}]}
    m, _ = S.builtin(item, "C")
    assert "L1 n1 2 1n" in m["text"], m["text"]                      # was 'L1 n1 n2': port 2 left floating


def test_sim_rules_section_is_accepted():
    from ecadlib import rules as RU
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "ecadlib_rules.json"
        f.write_text(json.dumps({"sim": {"capacitor_esr": "10m"}}))
        rules = RU.load(str(f))                                       # was RulesError: [sim] is not a known section
        item = {"name": "C2", "parameters": [{"name": "Value", "value": "100n"}]}
        m, _ = S.builtin(item, "C", rules)
        assert m["model_type"] == "subckt" and "R1 n1 2 10m" in m["text"], m
        assert S.builtin(item, "C", RU.load(None, start=d + "/none"))[0]["model_type"] in ("ideal", "subckt")


def test_same_model_file_name_for_two_parts_not_overwritten():
    with tempfile.TemporaryDirectory() as m:
        for sub, src in (("a", "EEPART1.lib"), ("b", "led_red.mod")):
            (Path(m) / sub).mkdir()
            (Path(m) / sub / "model.lib").write_text((VENDOR / src).read_text())
        with tempfile.TemporaryDirectory() as d:
            plan, items, _ = _plan(d, models=(m,))
            files = set()
            for n in ("EEPART1", "LED-RED-0603"):
                sm = items[n]["sim_model"]
                assert sm["name"] in (plan.parent / sm["file"]).read_text(), (n, sm["file"])
                files.add(sm["file"])
            assert len(files) == 2, files


def test_report_only_rerun_keeps_attached_model_and_hold():
    with tempfile.TemporaryDirectory() as d:
        plan, items, rep = _plan(d)
        held = [v for v in items["TPS1234DSGR"]["verify"] if v.startswith("sim model")]
        assert held and items["TPS1234DSGR"]["sim_model"]["attached"]
        S.run(plan, [str(VENDOR)], attach=False, http=rep["_http"], log=lambda *a: None)
        it = {i["name"]: i for i in json.loads(plan.read_text())}["TPS1234DSGR"]
        assert [v for v in it["verify"] if v.startswith("sim model")] == held, it["verify"]
        assert it["sim_model"]["attached"], it["sim_model"]           # KiCad export still writes its Sim.* fields

if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_sim_models"]))
