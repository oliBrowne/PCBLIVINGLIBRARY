"""Regression tests for the independent review, items P1-P11 (offline). Run:  python3 -m tests.test_independent_review"""
import json, tempfile
from pathlib import Path

from ecadlib import altium365 as A, altium_export as X, altium_lib as AL, bom_triage as T
from ecadlib.kicad_index import KiCadIndex, Symbol, package_of


def _write(text, name="bom.csv", raw=None):
    f = Path(tempfile.mkdtemp()) / name
    f.write_bytes(raw if raw is not None else text.encode())
    return f


def _index(syms=(), fps=()):
    idx = KiCadIndex([])
    idx.symbols, idx.footprints = list(syms), {k: {"path": "", "has_3d": False} for k in fps}
    return idx


def test_p1_non_cp1252_names():
    fp = AL.footprint_from_spec(json.loads(Path("ecadlib/package_specs/R_0603_1608Metric.json").read_text()))[0]
    fp["name"] = "R_1kΩ_0603"
    sym = dict(AL.resistor_symbol(), name="Ω-sensor", footprint="R_1kΩ_0603")
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "a.PcbLib", [fp])
        AL.write_schlib(Path(d) / "a.SchLib", [sym])
        got = AL.read_pcblib(Path(d) / "a.PcbLib")
        assert [f["name"] for f in got] == ["R_1kOhm_0603"] and any(r["type"] == "pad" for r in got[0]["records"]), got
        _, syms = AL.read_schlib(Path(d) / "a.SchLib")
        assert len(syms) == 1 and syms[0]["records"], syms
    for bad in ("R_☃", "电阻"):
        try:
            AL.lib_name(bad)
        except ValueError as e:
            assert "rename" in str(e)
        else:
            raise AssertionError(bad)


def test_p2_mfr_column_is_manufacturer():
    lines, mapping = T.read_bom(_write("Designator,Qty,Mfr,Mfr Part #\nU1,1,Texas Instruments,LM358DR\n"))
    assert mapping["manufacturer"] == "Mfr" and mapping["mpn"] == "Mfr Part #", mapping
    assert lines[0]["mpn"] == "LM358DR"


def test_p3_value_match_respects_ref_class():
    idx = _index([Symbol("lib", "L_10u", {"Value": "10u", "Footprint": "Inductor_SMD:L_0805_2012Metric", "Reference": "L"}),
                  Symbol("lib", "C_10u", {"Value": "10u", "Footprint": "Capacitor_SMD:C_0805_2012Metric", "Reference": "C"})])
    s, how = idx.match(value="10u", package="0805", ref_class="C")
    assert s.name == "C_10u" and how == "value+package", s
    idx.symbols = idx.symbols[:1]
    assert idx.match(value="10u", package="0805", ref_class="C") == (None, None)
    rows = T.triage([{"designator": "C7", "value": "10u", "footprint": "0805"}], idx, [], None)
    assert rows[0]["status"] != "in_library", rows


def test_p4_mpn_with_slash():
    lines = [{"designator": "U1", "mpn": "LM358/NOPB", "footprint": "SOIC-8_3.9x4.9mm_P1.27mm"}]
    row = X.to_rows(lines)["ICs"].iloc[0]
    assert row["Library Ref"] == "LM358/NOPB" and row["Library Path"] == "SchLib\\LM358_NOPB.SchLib", row
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sym.json").write_text(json.dumps({"name": "LM358/NOPB", "pins": [
            {"num": "1", "name": "OUT", "side": "right"}, {"num": "4", "name": "V-", "side": "left"}]}))
        AL.main(["schlib", f"{d}/sym.json", "-o", f"{d}/SchLib"])
        (Path(d) / "PcbLib").mkdir()
        (Path(d) / "PcbLib" / "SOIC-8_3.9x4.9mm_P1.27mm.PcbLib").write_bytes(b"x")
        it = A.plan(lines, models=d)[0]
        assert it["status"] == "ready" and it["symbol"]["file"].endswith("LM358_NOPB.SchLib"), it


class Stateful:
    """Fake workspace that remembers releases and parameter updates."""
    def __init__(self):
        self.comps, self.calls = {}, []
    def release_defs(self, ws): return {}
    def find(self, ws, name): return self.comps.get(name)
    def upload(self, p): self.calls.append(("upload", Path(p).name)); return "f" + str(len(self.calls))
    def release(self, inp):
        self.calls.append(("release", inp))
        self.comps[inp["componentItemName"]] = {"id": "c" + inp["componentItemName"],
                                                "details": {"parameters": [dict(p) for p in inp["parameters"]]}}
    def update_params(self, cid, params, note, replace=False):
        self.calls.append(("update", cid, params, replace))
        c = next(c for c in self.comps.values() if c["id"] == cid)
        c["details"]["parameters"] = list(params) if replace else c["details"]["parameters"] + list(params)
        return cid


def _two_resistors(d):
    lines = [{"designator": "R1", "mpn": "RC0603FR-0710KL", "value": "10k", "footprint": "R_0603_1608Metric", "lcsc": "C25804"},
             {"designator": "R2", "mpn": "RC0603FR-074K7L", "value": "4k7", "footprint": "R_0603_1608Metric"}]
    for sub, f in (("SchLib", "Resistor.SchLib"), ("PcbLib", "R_0603_1608Metric.PcbLib")):
        (Path(d) / sub).mkdir(exist_ok=True); (Path(d) / sub / f).write_bytes(b"x")
    return lines


def test_p5_push_is_idempotent_and_clears_fields():
    with tempfile.TemporaryDirectory() as d:
        items = A.plan(_two_resistors(d), models=d)
        ws = Stateful()
        assert {r["action"] for r in A.push(items, ws, "w")} == {"release"}
        ws.comps[items[0]["name"]]["details"]["parameters"].append({"name": "Added in A365", "value": "keep"})
        assert {r["action"] for r in A.push(items, ws, "w")} == {"up_to_date"}         # second push: no new revisions
        assert not [c for c in ws.calls if c[0] == "update"]
        items[0]["parameters"] = [p for p in items[0]["parameters"] if p["name"] != "Supplier Part Number 1"]
        items[0]["cleared"].append("Supplier Part Number 1")                           # LCSC number removed from the BOM
        out = A.push(items, ws, "w")
        assert out[0]["action"] == "update_parameters" and out[1]["action"] == "up_to_date", out
        upd = next(c for c in ws.calls if c[0] == "update")
        names = {p["name"] for p in upd[2]}
        assert upd[3] is True and "Supplier Part Number 1" not in names and "Added in A365" in names, upd


def test_p6_shared_models_reuse_item_names_and_uploads():
    with tempfile.TemporaryDirectory() as d:
        items = A.plan(_two_resistors(d), models=d)
        ws = Stateful()
        A.push(items, ws, "w")
        rel = [c[1] for c in ws.calls if c[0] == "release"]
        assert {r["symbolItemName"] for r in rel} == {"Resistor"} and {r["footprintItemName"] for r in rel} == {"R_0603_1608Metric"}
        assert sorted(c[1] for c in ws.calls if c[0] == "upload") == ["R_0603_1608Metric.PcbLib", "Resistor.SchLib"]
        dry = A.push(items, None, "w", dry_run=True)
        assert all(r["detail"]["input"]["symbolItemName"] == "Resistor" for r in dry)


def test_p7_package_regex_word_boundaries():
    assert package_of("Small signal Schottky diode SOD-123") == "SOD123"
    assert package_of("Plasma display driver TSSOP-20") == "TSSOP20"
    assert package_of("Diode SMA") == "SMA" and package_of("SOIC-8W_5.3x5.3mm") is None


def test_p8_footprint_needs_matching_body():
    idx = _index(fps=["lib:QFN-32-1EP_4x4mm_P0.4mm", "lib:QFN-32-1EP_5x5mm_P0.5mm", "lib:SOIC-8W_5.3x5.3mm_P1.27mm",
                      "lib:SOIC-8_3.9x4.9mm_P1.27mm"])
    assert idx.find_footprint_for_package("QFN32", "U", hint="QFN-32 5x5mm") == "lib:QFN-32-1EP_5x5mm_P0.5mm"
    assert idx.find_footprint_for_package("QFN32", "U") is None                   # two body sizes, nothing to pick by
    assert idx.find_footprint_for_package("SOIC8", "U") == "lib:SOIC-8_3.9x4.9mm_P1.27mm"


def test_p9_dblib_footprint_path_per_footprint():
    row = X.to_rows([{"designator": "U1", "mpn": "X", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}])["ICs"].iloc[0]
    assert row["Footprint Path"] == "PcbLib\\SOIC-8_3.9x4.9mm_P1.27mm.PcbLib", row


def test_p10_clean_collapses_newlines():
    assert AL.clean("line one\r\nline two | x") == "line one line two; x"


def test_p11_utf16_and_designator_ranges():
    text = "Designator\tQty\tValue\nR1-R4\t4\t10k\nR9\t1\t1k\n"
    lines, _ = T.read_bom(_write("", raw=b"\xff\xfe" + text.encode("utf-16-le")))
    assert [l["designator"] for l in lines] == ["R1-R4", "R9"], lines
    assert T.count_refs("R1, R2, R5-R8") == 6 and T.count_refs("C1-3") == 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
