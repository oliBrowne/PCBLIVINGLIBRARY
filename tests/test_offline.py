"""Offline self-tests (no network). Run:  python3 -m tests.test_offline

Exercises header mapping, value parsing, the IPC math, and the EasyEDA/JLC parsers via a
seeded HTTP cache. The seeded payloads are SYNTHETIC, shaped after the field paths that
easyeda2kicad 1.0.1 reads (result.dataStr.head.c_para, result.packageDetail.dataStr.shape,
'SVGNODE~{json}' with attrs.c_etype == 'outline3D'); they are not captured live responses."""
import json, tempfile
from pathlib import Path

from ecadlib import altium365 as A, bom_triage as T, footprint_gen as F, providers as P
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

    def find_component(self, ws, name):
        return self.existing.get(name)

    def upload(self, path):
        self.calls.append(("upload", Path(path).name)); return f"id-{Path(path).name}"

    def release(self, inp):
        self.calls.append(("release", inp))

    def update_params(self, cid, params, note):
        self.calls.append(("update", cid, params)); return cid + "-r2"


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


def seed(http, method, url, body, payload):
    f = http.dir / f"{http._key(method, url, body)}.json"
    f.write_text(json.dumps({"url": url, "status": 200, "text": json.dumps(payload)}))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
