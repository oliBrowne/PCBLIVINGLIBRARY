"""Offline self-tests (no network). Run:  python3 -m tests.test_offline

Exercises header mapping, value parsing, the IPC math, and the EasyEDA/JLC parsers via a
seeded HTTP cache. The seeded payloads are SYNTHETIC, shaped after the field paths that
easyeda2kicad 1.0.1 reads (result.dataStr.head.c_para, result.packageDetail.dataStr.shape,
'SVGNODE~{json}' with attrs.c_etype == 'outline3D'); they are not captured live responses."""
import io, json, logging, random, struct, tempfile, uuid
from pathlib import Path

from ecadlib import altium365 as A, altium_lib as AL, bom_triage as T, cfb, footprint_gen as F, providers as P
from ecadlib.httpcache import CachedHTTP
from ecadlib.kicad_index import KiCadIndex, parse_value


def test_headers():
    m = T.map_headers(["Ref Des", "QTY.", "Manufacturer Part Number", "Mfg", "LCSC Part #", "Package / Case", "comment"])
    assert m == {"designator": "Ref Des", "qty": "QTY.", "mpn": "Manufacturer Part Number", "manufacturer": "Mfg",
                 "lcsc": "LCSC Part #", "footprint": "Package / Case", "value": "comment"}, m
    m = T.map_headers(["Reference", "Quantity", "MPN", "Manufacturer", "JLCPCB Part"])
    assert m["mpn"] == "MPN" and m["lcsc"] == "JLCPCB Part" and m["manufacturer"] == "Manufacturer", m


def test_values():
    assert parse_value("10k") == 1e4 and parse_value("4k7") == 4700 and parse_value("10kΩ") == 1e4
    assert abs(parse_value("0.1uF") - parse_value("100nF")) < 1e-15 and parse_value("AMS1117") is None


def test_ipc_soic8():
    spec = json.loads(Path("examples/packages/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    _, rep = F.build(spec)
    p1 = rep["pads"][0]
    assert abs(p1["w"] - 1.95) < 0.05 and abs(p1["h"] - 0.6) < 0.05 and abs(p1["x"] + 2.475) < 0.02, p1


def test_providers_seeded_cache():
    with tempfile.TemporaryDirectory() as d:
        http = CachedHTTP(d, offline=True)
        svg = json.dumps({"attrs": {"c_etype": "outline3D", "uuid": "abc123"}})
        ee = {"success": True, "result": {"title": "FAKE-PART", "dataStr": {"head": {"c_para": {
            "Manufacturer Part": "FAKE-PART", "Manufacturer": "ACME", "package": "SOT-23"}}, "shape": ["R~..."]},
            "packageDetail": {"title": "SOT-23-3_L2.9-W1.3", "dataStr": {"shape": ["PAD~...", "SVGNODE~" + svg]}}}}
        jlc = {"data": {"componentPageInfo": {"list": [{"componentCode": "C999999", "componentModelEn": "FAKE-PART",
                                                        "componentBrandEn": "ACME", "componentSpecificationEn": "SOT-23"}]}}}
        seed(http, "GET", P.EasyEDA.URL.format("C999999"), None, ee)
        seed(http, "POST", P.JLCPCBSearch.URL, json.dumps({"keyword": "FAKE-PART", "currentPage": 1, "pageSize": 10}), jlc)
        provs = [P.DigiKey(http), P.JLCPCBSearch(http), P.EasyEDA(http)]
        rows = T.triage([{"mpn": "FAKE-PART", "designator": "Q9"}], KiCadIndex([]), provs, http)
        r = rows[0]
        assert r["status"] == "available_easyeda" and r["src_3d"] == "yes" and r["lcsc"] == "C999999 (via MPN)", r
        assert "jlcpcb: MPN -> C999999" in r["notes"] and "not configured: digikey" in r["notes"], r


class FakeNexar:
    """Records calls instead of hitting api.nexar.com / files.nexar.com."""
    def __init__(self, existing):
        self.existing, self.calls = existing, []

    def release_defs(self, ws):
        return {"componentScheme": {"id": "cs"}, "symbolLifeCycle": {"id": "sl"}}

    def find(self, ws, name):                 # existing: name -> id, or name -> {"id", "details": {"parameters"}}
        e = self.existing.get(name)
        return {"id": e, "details": {"parameters": []}} if isinstance(e, str) else e

    def upload(self, path):
        self.calls.append(("upload", Path(path).name)); return f"id-{Path(path).name}"

    def release(self, inp):
        self.calls.append(("release", inp))

    def update_params(self, cid, params, note, replace=False):
        self.calls.append(("update", cid, params, replace))
        for e in self.existing.values():
            if isinstance(e, dict) and e["id"] == cid:
                e["details"]["parameters"] = list(params) if replace else e["details"]["parameters"] + list(params)
        return cid + "-r2"


def test_altium365_plan_and_push():
    lines = [{"designator": "U1", "mpn": "CH340C", "manufacturer": "WCH", "lcsc": "C84681", "footprint": "SOIC-16",
              "description": "USB UART"},
             {"designator": "R1", "mpn": "0603WAF1002T5E", "value": "10k", "footprint": "R_0603_1608Metric",
              "description": "Resistor 10k 0603"}]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "SchLib").mkdir(); (Path(d) / "PcbLib").mkdir()
        (Path(d) / "SchLib" / "CH340C.SchLib").write_bytes(b"x"); (Path(d) / "PcbLib" / "SOIC-16.PcbLib").write_bytes(b"x")
        items = A.plan(lines, models=d)
        by = {i["name"]: i for i in items}
        assert by["CH340C"]["status"] == "ready" and by["CH340C"]["folder"] == "Components/ICs", by["CH340C"]
        res = next(i for i in items if i["folder"] == "Components/Resistors")
        assert res["status"] == "needs_altium_models" and res["symbol"]["ref"] == "Resistor", res
        assert {"name": "Supplier Part Number 1", "value": "C84681"} in by["CH340C"]["parameters"]
        fake = FakeNexar({res["name"]: "cmp-1"})
        out = {r["name"]: r for r in A.push(items, fake, "https://ws")}
        assert out["CH340C"]["action"] == "release" and out[res["name"]]["action"] == "update_parameters", out
        rel = next(c[1] for c in fake.calls if c[0] == "release")
        assert rel["symbolFiles"] == [{"fileId": "id-CH340C.SchLib", "relativePath": "Released\\CH340C.SchLib"}], rel
        assert rel["componentRevisionNamingSchemeNodeId"] == "cs" and rel["symbolLifeCycleDefinitionNodeId"] == "sl"
        dry = A.push(items, None, "https://ws", dry_run=True)
        assert {r["action"] for r in dry} == {"release", "skip"}, dry


def test_altium365_ops_match_nexar_schema():
    """Every query/mutation, and a release input built by plan(), validates against Nexar's schema."""
    try:
        from graphql import build_schema, parse, validate
        from graphql.execution.values import get_variable_values
    except ImportError:
        print("skip test_altium365_ops_match_nexar_schema (pip install graphql-core)"); return
    schema = build_schema(Path("tests/nexar/schema.graphql").read_text(), assume_valid_sdl=True)
    for op in (A.Q_WORKSPACES, A.Q_DEFS, A.Q_FIND, A.M_RELEASE, A.M_UPDATE_PARAMS):
        assert not validate(schema, parse(op)), op
    item = A.plan([{"designator": "U1", "mpn": "CH340C", "footprint": "SOIC-16"}])[0]
    inp = A.release_input(item, "https://ws", {}, ([("a.SchLib", "f1")], [("b.PcbLib", "f2")]))
    var_defs = parse(A.M_RELEASE).definitions[0].variable_definitions
    res = get_variable_values(schema, var_defs, {"input": inp})
    assert not isinstance(res, list), [e.message for e in res]


def test_cfb_writer_reads_back_cleanly():
    """Random storage trees (mini + regular streams, empty streams, >109 FAT sectors -> DIFAT) read back
    byte-exact with olefile in strict mode, with no logged warnings, and every directory is a valid BST."""
    try:
        import olefile
    except ImportError:
        print("skip test_cfb_writer_reads_back_cleanly (pip install olefile)"); return
    rnd = random.Random(7)
    log = io.StringIO(); h = logging.StreamHandler(log)
    lg = logging.getLogger("olefile"); old_level = lg.level
    lg.addHandler(h); lg.setLevel(logging.WARNING)    # olefile mutes its logger (level 51) by default

    def mk(depth):
        t = {}
        for i in range(rnd.randint(1, 9)):
            k = rnd.choice(["Data", "Header", "Parameters", f"S{i}", f"long_name_{i}_" + "x" * rnd.randint(0, 14)])
            if k.upper() not in {x.upper() for x in t}:
                t[k] = mk(depth + 1) if depth < 2 and rnd.random() < 0.3 else bytes(rnd.getrandbits(8) for _ in range(
                    rnd.choice([0, 1, 64, 65, 4095, 4096, 5000, rnd.randint(0, 3000)])))
        return t

    def walk(t, pre=""):
        for k, v in t.items():
            yield from walk(v, pre + k + "/") if isinstance(v, dict) else [(pre + k, v)]
    trees = [mk(0) for _ in range(40)] + [{"big": b"\x5a" * 7_500_000, "small": b"abc"},
                                          {"\U0001F600" * 15: b"x", "\uffff": b"y", "\U00010000": b"z"}]
    try:
        _check_trees(olefile, trees, walk)
    finally:
        lg.removeHandler(h); lg.setLevel(old_level)
    assert log.getvalue() == "", log.getvalue()
    for bad in ({"": b""}, {"\U0001F600" * 16: b""}):
        try:
            cfb.to_bytes(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted illegal name {list(bad)}")


def _check_trees(olefile, trees, walk):
    for t in trees:
        o = olefile.OleFileIO(io.BytesIO(cfb.to_bytes(t)), raise_defects=olefile.DEFECT_INCORRECT)
        assert all(o.openstream(p).read() == v for p, v in walk(t))
        for d in o.direntries:
            if d is not None and d.entry_type in (1, 5):     # in-order walk of each child tree must be sorted
                seq, stack, i = [], [], d.sid_child
                while stack or i != cfb.NOSTREAM:
                    while i != cfb.NOSTREAM:
                        stack.append(i); i = o.direntries[i].sid_left
                    i = stack.pop(); seq.append(cfb._key(o.direntries[i].name)); i = o.direntries[i].sid_right
                assert seq == sorted(seq) and len(seq) == len(d.kids), d.name


# Bytes Altium Designer wrote (AltiumSharp TestData, Apache-2.0: QFN-24 pad "2" and a Top Overlay track)
ALTIUM_PAD = bytes.fromhex(
    "020000000132010000000005000000047c267c300100000000ca000000010c00ffffffffffffffffffff0196f4ff6b8104005ecd0100"
    "bc9a03005ecd0100bc9a03005ecd0100bc9a03000000000001010100000000008056400100000000000000a08601000400a0860100"
    "400d0300400d030000000000409c0000000000000000000101010000000000000000000001000001000000409c0000008785b3db7b"
    "57c2449ae515f1d8db0355ec3be13f0a809446bead96d0f4dbfa5200000000ffffff7fffffff7f00011a000000000000000000000001"
    "030000000000000000000000000000000000000000")
ALTIUM_TRACK = bytes.fromhex("31000000210c00ffffffffffffffffffff95c9f5ff6b360a0095c9f5ffae500c00943301000000000000"
                             "0000000600030100000000")


def test_altium_units_and_records_match_altium_bytes():
    assert AL.mm(0.0254) == 10000 and AL.mm(1) == 393701 and AL.mm(-2.54) == -1000000
    x1, y1, x2, y2, w = struct.unpack_from("<5i", ALTIUM_TRACK, 17)
    assert AL.track_rec(x1, y1, x2, y2, w, AL.TOP_OVERLAY) == b"\x04" + ALTIUM_TRACK
    main = ALTIUM_PAD[29:29 + 202]
    x, y, w, h = struct.unpack_from("<4i", main, 13)
    p = {"designator": "2", "x": x, "y": y, "w": w, "h": h, "shape": "round", "rot": 90.0, "paste": None}
    mine = AL.pad_rec(p, uuid.UUID(bytes_le=main[126:142]), uuid.UUID(bytes_le=main[142:158]))[1:]
    diff = [i - 29 for i in range(len(mine)) if mine[i] != ALTIUM_PAD[i]]
    assert len(mine) == len(ALTIUM_PAD) and diff == [103], diff     # 103 = paste-mask cache-valid flag (Altium revalidates)


def _missing(mod, test):
    """True (after reporting a skip) when an optional dev dependency isn't installed; under pytest, skips."""
    import importlib.util
    if importlib.util.find_spec(mod):
        return False
    try:
        import pytest
    except ImportError:
        print(f"skip {test} (pip install {mod})"); return True
    pytest.skip(f"{mod} not installed")


def test_pcblib_writer_roundtrip():
    if _missing("olefile", "test_pcblib_writer_roundtrip"):
        return
    specs = ["SOIC-8_3.9x4.9mm_P1.27mm", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm"]
    fps = [AL.footprint_from_spec(json.loads(Path(f"examples/packages/{n}.json").read_text()))[0] for n in specs]
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "two.PcbLib", fps)
        got = {f["name"]: f for f in AL.read_pcblib(Path(d) / "two.PcbLib")}
    q = got[specs[1]]
    assert q["storage"] == specs[1][:31] and q["params"]["PATTERN"] == specs[1] and q["params"]["HEIGHT"] == "31.4961mil", q["params"]
    pads = [r for r in q["records"] if r["type"] == "pad"]
    _, rep = F.build(json.loads(Path(f"examples/packages/{specs[1]}.json").read_text()))
    p1, r1 = pads[0], rep["pads"][0]
    assert len(pads) == 33 and (p1["x"], p1["y"], p1["w"], p1["h"]) == (AL.mm(r1["x"]), AL.mm(-r1["y"]), AL.mm(r1["w"]), AL.mm(r1["h"]))
    assert p1["shape"] == 9 and p1["corner"] == 50 and p1["paste_mode"] == 1
    ep = pads[-1]
    assert ep["designator"] == "33" and ep["shape"] == 2 and ep["paste_mode"] == 2 and ep["paste"] < -ep["w"] // 2
    layers = {(r["type"], r["layer"]) for r in q["records"]}
    assert {("fill", AL.TOP_PASTE), ("track", AL.TOP_OVERLAY), ("track", AL.MECH13), ("track", AL.MECH15), ("region", AL.TOP_OVERLAY)} <= layers
    assert q["header_count"] == len(q["records"])


def test_schlib_writer_roundtrip():
    if _missing("olefile", "test_schlib_writer_roundtrip"):
        return
    sym = AL.symbol_from_json(json.loads(Path("examples/symbols/CH340C.json").read_text()))
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d) / "x.SchLib", [sym, AL.resistor_symbol(), AL.capacitor_symbol()])
        hdr, syms = AL.read_schlib(Path(d) / "x.SchLib")
    assert hdr["COMPCOUNT"] == "3" and [s["name"] for s in syms] == ["CH340C", "Resistor", "Capacitor"]
    pins = [r for r in syms[0]["records"] if r["RECORD"] == "2"]
    assert len(pins) == 16 and {p["num"]: p["name"] for p in pins}["5"] == "UD+"
    assert all(p["x"] % 10 == 0 and p["y"] % 10 == 0 for p in pins)              # on the 100 mil grid
    impl = next(r for r in syms[0]["records"] if r["RECORD"] == "45")
    assert impl["MODELNAME"] == "SOIC-16_3.9x9.9mm_P1.27mm" and impl["MODELTYPE"] == "PCBLIB"
    # a pin record Altium wrote (AltiumSharp TestData PE42442 pin 1) re-encodes byte for byte
    real = bytes.fromhex("26000001" "020000000001000000000000000104" "3a1e00ceffd8ff00000000" "03474e44" "0131" "00" "037c267c" "00")
    assert AL.pin_rec("1", "GND", "passive", 2, -50, -40, 30) == real
    for bad in (lambda: AL.schlib_bytes([AL.ic_symbol("Storage", [])]),
                lambda: AL.ic_symbol("X", [{"num": "1", "name": "A", "side": "Left"}]),
                lambda: AL.pcblib_bytes([dict(name="Library", pads=[], tracks=[], fills=[], regions=[])])):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("reserved name / unknown pin side accepted")


def test_plan_ready_with_generated_models():
    lines = [{"designator": "U1", "mpn": "CH340C", "footprint": "SOIC-16_3.9x9.9mm_P1.27mm"}]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "SchLib").mkdir(); (Path(d) / "PcbLib").mkdir()
        AL.main(["schlib", "examples/symbols/CH340C.json", "-o", f"{d}/SchLib"])
        AL.main(["pcblib", "examples/packages/SOIC-16_3.9x9.9mm_P1.27mm.json", "-o", f"{d}/PcbLib"])
        assert A.plan(lines, models=d)[0]["status"] == "ready"
        try:
            import pyaltiumlib                        # independent reader, if installed
        except ImportError:
            return
        pcb = pyaltiumlib.read(f"{d}/PcbLib/SOIC-16_3.9x9.9mm_P1.27mm.PcbLib")
        sch = pyaltiumlib.read(f"{d}/SchLib/CH340C.SchLib")
        assert len([r for r in pcb.Parts[0].Records if type(r).__name__ == "PcbPad"]) == 16
        assert len([r for r in sch.Parts[0].Records if type(r).__name__ == "SchPin"]) == 16


def seed(http, method, url, body, payload):
    f = http.dir / f"{http._key(method, url, body)}.json"
    f.write_text(json.dumps({"url": url, "status": 200, "text": json.dumps(payload)}))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
