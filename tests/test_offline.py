"""Offline self-tests (no network). Run:  python3 -m tests.test_offline

Exercises header mapping, value parsing, the IPC math, and the EasyEDA/JLC parsers via a
seeded HTTP cache. The seeded payloads are SYNTHETIC, shaped after the field paths that
easyeda2kicad 1.0.1 reads (result.dataStr.head.c_para, result.packageDetail.dataStr.shape,
'SVGNODE~{json}' with attrs.c_etype == 'outline3D'); they are not captured live responses."""
import json, tempfile
from pathlib import Path

from ecadlib import bom_triage as T, footprint_gen as F, providers as P
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


def test_workspace_and_mcp():
    from ecadlib.app import Workspace
    from ecadlib import mcp_server as M
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, ["examples/kicad_lib"], providers=["digikey"])
        rel = ws.save_upload("../my bom.csv", Path("examples/sample_bom.csv").read_bytes())
        assert rel == "uploads/my_bom.csv", rel
        t = ws.triage(rel)
        assert len(t["rows"]) == 14 and sum(t["counts"].values()) == 14, t["counts"]
        before = t["counts"].get("in_library", 0)
        spec = json.loads(Path("examples/packages/R_0603_1608Metric.json").read_text())
        pv = ws.preview_footprint(spec)
        assert pv["svg"].startswith("<svg") and len(pv["report"]["pads"]) == 2
        ws.approve_footprint(spec)
        ws.approve_footprint(json.loads(Path("examples/packages/C_0402_1005Metric.json").read_text()))
        assert ws.triage(rel)["counts"].get("in_library", 0) > before   # approved footprints are found on re-triage
        try:
            ws.inside("../../etc/passwd")
            raise AssertionError("path escape not refused")
        except ValueError:
            pass
        r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert "triage_bom" in [x["name"] for x in r["result"]["tools"]]
        r = M.handle(ws, {"id": 2, "method": "tools/call", "params": {"name": "preview_footprint", "arguments": {"spec": spec}}})
        assert r["result"]["isError"] is False, r
        assert M.handle(ws, {"method": "notifications/initialized"}) is None


def seed(http, method, url, body, payload):
    f = http.dir / f"{http._key(method, url, body)}.json"
    f.write_text(json.dumps({"url": url, "status": 200, "text": json.dumps(payload)}))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
