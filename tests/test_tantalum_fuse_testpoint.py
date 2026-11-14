"""Tantalum case lands, and fuse / test point / plated mounting hole symbols (2026-11-14).
Plain functions: python3 -m tests.run_all test_tantalum_fuse_testpoint"""
from pathlib import Path

from ecadlib import librarian as L, release_check as RC, rules as RU, stdparts as SP
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def test_tantalum_land_by_case():
    T = SP.tantalum_land
    assert T(["", "10uF"], "T491A106K016AT")[0] == "CP_EIA-3216-18_Kemet-A"
    assert T(["CP_Tantalum_Case-B_EIA-3528-21_Reflow"], "TAJB226K016RNJ")[0] == "CP_EIA-3528-21_Kemet-B"
    assert T(["Capacitor_Tantalum_SMD:CP_EIA-7343-31_Kemet-D"], "")[0] == "CP_EIA-7343-31_Kemet-D"
    assert T(["Case C"], "")[0] == "CP_EIA-6032-28_Kemet-C"
    assert T(["6032-28"], "")[0] == "CP_EIA-6032-28_Kemet-C"
    for texts, mpn in ((["CP_Tantalum_Case-B_EIA-3528-21_Reflow"], "T491A106K016AT"),   # two cases named
                       (["C_1206_3216Metric"], "T491A106K016AT"),                       # a chip land, not a case
                       (["CP_EIA-3528-21_Kemet-B_HandSolder"], ""),                     # a land variant
                       (["CP_EIA-3528-12_Kemet-T"], ""),                                # a case not in the table
                       (["Case B"], "T491A106K016AT"), ([""], ""), ([""], "EEE-1HA100SP"), (["3528"], "")):
        assert T(texts, mpn)[0] is None, (texts, mpn, T(texts, mpn))


def test_tantalum_land_only_for_a_polarized_capacitor():
    for kind in ("R", "L", "D", "LED", "U", None):
        assert SP.resolve(["CP_EIA-3528-21_Kemet-B"], kind, "", "")["footprint"] is None, kind
    r = SP.resolve(["CP_EIA-3528-21_Kemet-B"], "C", "", "")             # a C on a polarized land is polarized
    assert r["footprint"] == "CP_EIA-3528-21_Kemet-B" and r["symbol"] == "CP", r
    r = SP.resolve(["", "", "", "10uF"], "C", "T491A106K016AT", "")
    assert r["footprint"] == "CP_EIA-3216-18_Kemet-A" and r["symbol"] == "CP", r
    assert SP.resolve(["CP_EIA-3528-21_Kemet-B"], "CP", "", "aluminium electrolytic")["footprint"] is None


def test_tantalum_table_lands_have_pin1_plus_left():
    for name in SP.TANT_LAND.values():
        e = SP.packages()[name]
        pads = {p["number"]: p for p in e["pad_table"]["pads"]}
        assert e["polarized"] and e["lib"] == "Capacitor_Tantalum_SMD" and set(pads) == {"1", "2"}
        assert pads["1"]["x"] < 0 < pads["2"]["x"]                    # KiCad: pad 1 (+) on the left, as C_Polarized pin 1


def test_passive_kind_reads_a_tantalum_part_number():
    item = {"folder": "Components/Capacitors", "footprint": {"ref": "X", "kicad": ""}, "description": "",
            "parameters": [{"name": "Manufacturer Part Number", "value": "T491A106K016AT"}]}
    assert L.passive_kind(item, {}) == "CP"
    item["parameters"][0]["value"] = "GRM188R71H104KA93D"
    assert L.passive_kind(item, {}) == "C"
    assert L.passive_kind(item, {"footprint": "CP_EIA-3528-21_Kemet-B"}) == "CP"


def _run(tmp_path, text, sources=("standard",)):
    d = Path(tmp_path)
    (d / "bom.csv").write_text(text, encoding="utf-8")
    items = L.run(d / "bom.csv", out=d / "out", sources=list(sources), offline=True, cache=d / "cache", rules={}, log=QUIET)
    return {it["name"]: it for it in items}, d / "out"


@lite
def test_tantalums_build_on_their_case_with_their_own_polarized_symbol(tmp_path):
    """A plain capacitor first, tantalums after: each tantalum is built on its case's land, and with the polarized
    symbol, never with the plain capacitor's shared one (it was: the first C wrote Capacitor.SchLib for all)."""
    got, out = _run(tmp_path, "Designator,Qty,Value,Footprint,MPN,Description\n"
                              "C1,1,100nF,C_0603_1608Metric,GRM188R71H104KA93D,\n"
                              "C2,1,10uF,,T491A106K016AT,\n"
                              "C3,1,22uF,CP_Tantalum_Case-B_EIA-3528-21_Reflow,TAJB226K016RNJ,\n"
                              "C4,1,10uF,C_1206_3216Metric,T491A106K016AT2,\n"
                              "C5,1,10uF,CP_Tantalum_Case-B_EIA-3528-21_Reflow,T491A106K016AS,\n")
    by_mpn = {next(p["value"] for p in it["parameters"] if p["name"] == "Manufacturer Part Number"): it
              for it in got.values()}
    plain = by_mpn["GRM188R71H104KA93D"]
    assert plain["status"] == "ready" and plain["symbol"]["ref"] == "Capacitor"
    for mpn, land in (("T491A106K016AT", "CP_EIA-3216-18_Kemet-A"), ("TAJB226K016RNJ", "CP_EIA-3528-21_Kemet-B")):
        it = next(i for i in got.values() if any(p["value"] == mpn for p in i["parameters"])
                  and i["source"]["footprint"] and land in i["source"]["footprint"])
        assert it["status"] == "ready", (mpn, it["hold_reasons"])
        assert it["symbol"]["ref"] == L.POLARIZED_SYMBOL and it["source"]["symbol"] == "standard:C_Polarized", it["symbol"]
        assert it["symbol"]["file"] != plain["symbol"]["file"]
    held = [i for i in got.values() if i["status"] == "held"]
    assert len(held) == 2, [(i["name"], i["hold_reasons"]) for i in got.values()]    # C4 chip land, C5 two cases
    assert all(not i["source"]["footprint"] for i in held), [i["source"] for i in held]


def test_mechanical_symbol():
    M = SP.mechanical_symbol
    assert M("kicad:TestPoint:TestPoint_Pad_D1.0mm", "TP1", {"1"})[0] == "TestPoint"
    assert M("kicad:MountingHole:MountingHole_3.2mm_M3_Pad_Via", "H1, H2", {"1"})[0] == "MountingHole_Pad"
    assert M("kicad:MountingHole:MountingHole_3.2mm_M3", "H1", set())[0] is None        # pinless: not built
    assert M("standard:Fuse:Fuse_1206_3216Metric", "F1", {"1", "2"}, "500mA 0466.500NR")[0] == "Fuse"
    assert M("standard:Fuse:Fuse_1812_4532Metric", "F2", {"1", "2"}, "MF-MSMF050-2")[0] == "Polyfuse"
    assert M("standard:Fuse:Fuse_1206_3216Metric", "F3", {"1", "2"}, "PTC resettable 500mA")[0] == "Polyfuse"
    for args in (("standard:Fuse:Fuse_1206_3216Metric", "R1", {"1", "2"}),       # not a fuse's designator
                 ("standard:Fuse:Fuse_1206_3216Metric", "F1, R1", {"1", "2"}),
                 ("kicad:TestPoint:TestPoint_2Pads_Pitch2.54mm_Drill0.8mm", "TP1", {"1", "2"}),
                 ("kicad:TestPoint:TestPoint_Pad_D1.0mm", "J1", {"1"}),
                 ("easyeda:C123", "TP1", {"1"}), ("local:Mine:TP", "TP1", {"1"}),
                 ("kicad:Connector:Conn_01x01", "TP1", {"1"}), ("standard:Fuse:Fuse_1206_3216Metric", "", {"1", "2"})):
        assert M(*args)[0] is None, args
    for key in ("Fuse", "Polyfuse", "TestPoint", "MountingHole_Pad"):           # each symbol's pins are what it claims
        text, name = SP.symbol_text(key)
        assert name == key and text.count("(pin passive") == {"TestPoint": 1, "MountingHole_Pad": 1}.get(key, 2), key


@lite
def test_fuse_on_a_kicad_fuse_land_gets_the_fuse_symbol(tmp_path):
    got, _ = _run(tmp_path, "Designator,Qty,Value,Footprint,Manufacturer,MPN,Description\n"
                            "F1,1,500mA,Fuse_1206_3216Metric,Littelfuse,0466.500NR,\n"
                            "F2,1,1.1A,Fuse_1206_3216Metric,Bourns,MF-NSMF110-2,PTC resettable fuse 1.1A 1206\n"
                            "R1,1,10k,Fuse_1206_3216Metric,Yageo,X1,\n")
    f1, f2 = got["0466.500NR"], got["MF-NSMF110-2"]
    assert f1["status"] == "ready" and f1["source"]["symbol"] == "standard:Fuse", (f1["source"], f1["hold_reasons"])
    assert f1["description"] == "Fuse 500mA 1206", f1["description"]
    assert f2["status"] == "ready" and f2["source"]["symbol"] == "standard:Polyfuse", (f2["source"], f2["hold_reasons"])
    r1 = next(i for n, i in got.items() if n not in ("0466.500NR", "MF-NSMF110-2"))
    assert r1["source"]["symbol"] != "standard:Fuse"


def test_no_maker_part_needed_for_a_test_pad_or_mounting_hole():
    base = {"folder": "Components/Misc", "description": "Test point",
            "parameters": [{"name": "Manufacturer", "value": ""}, {"name": "Manufacturer Part Number", "value": ""}],
            "symbol": {}, "footprint": {}, "status": "ready"}
    rules = RU.resolve({}, ".")
    fails, _, _ = RC.check_item(dict(base), ".", rules)
    assert any("Manufacturer Part Number is empty" in m for _, m in fails)
    fails, _, _ = RC.check_item(dict(base, no_bom_part="test point (TestPoint_Pad_D1.0mm)"), ".", rules)
    assert not any("Manufacturer" in m for _, m in fails), fails
