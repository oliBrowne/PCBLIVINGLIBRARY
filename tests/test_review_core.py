"""Regression tests for the core-module review fixes (offline). Run:  python3 -m tests.test_review_core"""
import json, os, tempfile, time
from pathlib import Path

from ecadlib import bom_triage as T, footprint_gen as F, providers as P, sexpr
from ecadlib.httpcache import CachedHTTP, Response
from ecadlib.kicad_index import KiCadIndex, Symbol, parse_value


def _index(*syms):
    idx = KiCadIndex([])
    idx.symbols = list(syms)
    return idx


def test_sexpr_unescapes_backslash():
    for s in ('x\\y', 'x\\', 'say "hi"', 'a\\"b'):
        assert sexpr.parse(f"(a {F.q(s)})")[1] == s, s
    assert sexpr.parse('(a "l1\\nl2")')[1] == "l1\nl2"


def test_dim_one_sided_limit():
    assert F.dim({"nom": 1.0, "min": 0.9}) == (0.9, 1.0, 1.0)
    assert F.dim({"nom": 1.0, "max": 1.2, "tol": 0.1}) == (0.9, 1.0, 1.2)
    assert F.dim({"nom": 1.0, "tol": 0.1}) == (0.9, 1.0, 1.1)


def test_ep_gap_checks_top_bottom_rows():
    spec = json.loads(Path("examples/packages/QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm.json").read_text())
    _, rep = F.build(spec)
    assert "warning" not in rep, rep.get("warning")
    spec["exposed_pad"] = {"length": {"nom": 3.0}, "width": {"nom": 4.2}}      # tall EP overlaps top/bottom pads
    _, rep = F.build(spec)
    assert "EP to pad gap" in rep.get("warning", ""), rep.get("warning")


def test_values_units_and_zero():
    assert parse_value("0R") == 0.0 and parse_value("4k7Ω") == 4700 and parse_value("1Meg") == 1e6
    assert parse_value("1m") == 1e-3 and parse_value("4.7µF") == parse_value("4.7μF") == parse_value("4u7F")
    assert parse_value("74HC595") is None and parse_value("1N4148") is None
    assert parse_value("10uF", True)[1] == "F" and parse_value("10uH", True)[1] == "H"
    assert parse_value("10kΩ", True)[1] == parse_value("10 Ohms", True)[1] == "R"


def test_match_zero_ohm_and_no_false_positives():
    fp = "Resistor_SMD:R_0603_1608Metric"
    assert _index(Symbol("l", "R0", {"Value": "0R", "Footprint": fp})).match(value="0", package="0603")[1] == "value+package"
    ic = Symbol("l", "74HC165", {"Value": "74HC165", "Footprint": "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"})
    assert _index(ic).match(mpn="SN74HC595DR", value="74HC595", package="SOIC16") == (None, None)
    ind = Symbol("l", "L10u", {"Value": "10uH", "Footprint": "Inductor_SMD:L_0805_2012Metric"})
    assert _index(ind).match(value="10uF", package="0805") == (None, None)
    assert _index(ind).match(value="10u", package="0805")[0] is ind                # unit unstated -> still matches


def test_headers_distributor_pn_is_not_mpn():
    assert "mpn" not in T.map_headers(["Designator", "Quantity", "Value", "Footprint", "Digi-Key Part Number"])
    assert "mpn" not in T.map_headers(["Ref", "Qty", "Value", "Supplier Part Number", "Manufacturer"])
    m = T.map_headers(["Ref", "Qty", "Mouser Part Number", "Manufacturer Part Number"])
    assert m["mpn"] == "Manufacturer Part Number", m


def test_read_bom_encodings_and_wrapped_designators():
    d = Path(tempfile.mkdtemp())
    (d / "a.csv").write_bytes("Designator,Qty,Value,MPN\nC1,1,1µF,GRM188R61E105KA12D\n".encode("cp1252"))
    lines, _ = T.read_bom(d / "a.csv")
    assert lines[0]["value"] == "1µF", lines
    (d / "b.csv").write_bytes("﻿Designator,Qty,Value,MPN\nC1,1,1uF,X\n".encode("utf-8"))
    lines, m = T.read_bom(d / "b.csv")
    assert m["designator"] == "Designator" and lines[0]["value"] == "1uF", (m, lines)
    (d / "c.csv").write_text('Designator,Qty,Value,MPN\n"R1,R2,R3",5,10k,RC0603FR-0710KL\n"R4,R5",,,\n,,,\n'
                             'TP1,1,,\nTP2,,,\n')
    lines, _ = T.read_bom(d / "c.csv")
    assert [l["designator"] for l in lines] == ["R1,R2,R3, R4,R5", "TP1", "TP2"], lines


def test_write_reports_empty_bom():
    d = Path(tempfile.mkdtemp())
    T.write_reports([], {}, [], CachedHTTP(d / "c"), d / "out", "empty.csv")
    assert (d / "out.md").exists() and (d / "out.csv").read_text().startswith("line,status")


def test_jlc_unexpected_json_shape_is_error_not_crash():
    for body in ("[]", '{"data": {"componentPageInfo": {"list": ["x"]}}}', '{"data": "busy"}'):
        class H:
            def post(self, *a, **k):
                return Response(200, body)
        r = P.JLCPCBSearch(H()).lookup({"mpn": "X"})
        assert r.error.startswith("unparseable") and r.found is None, (body, r)


def test_offline_serves_stale_cache():
    h = CachedHTTP(tempfile.mkdtemp(), offline=True)
    f = h.dir / f"{h._key('GET', 'http://x/y', None)}.json"
    f.write_text(json.dumps({"url": "http://x/y", "status": 200, "text": "hi"}))
    os.utime(f, (time.time() - 30 * 86400,) * 2)
    r = h.get("http://x/y")
    assert r.ok and r.text == "hi" and r.from_cache


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
