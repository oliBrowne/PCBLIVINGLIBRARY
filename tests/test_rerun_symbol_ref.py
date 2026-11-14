"""A second librarian run in the same folder keeps each part's own shared symbol (2026-11-14 PC run: a resumed
tantalum went back to 'Capacitor', a resumed ferrite bead to 'Inductor').
Plain functions: python3 -m tests.run_all test_rerun_symbol_ref"""
from pathlib import Path

from ecadlib import librarian as L
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None
BOM = ("Designator,Qty,Value,Footprint,Manufacturer,MPN,Description\n"
       "C1,1,100nF,C_0603_1608Metric,Murata,GRM188R71H104KA93D,\n"
       "C2,1,10uF,,KEMET,T491A106K016AT,\n"
       "FB1,1,600R,L_0603_1608Metric,Murata,BLM18PG601SN1D,Ferrite bead 600R 0603\n"
       "L1,1,10uH,L_0805_2012Metric,Murata,LQM21PN100MC0D,Inductor 10uH 0805\n"
       "L2,1,,,Murata,BLM18PG221SN1D,\n"                          # held (no value): resumed as held
       "C6,1,,CP_EIA-3528-21_Kemet-B,KEMET,T491B226K016AT,\n")


@lite
def test_second_run_keeps_polarized_and_ferrite_symbols(tmp_path):
    d = Path(tmp_path)
    (d / "bom.csv").write_text(BOM, encoding="utf-8")
    runs = []
    for _ in range(2):
        items = L.run(d / "bom.csv", out=d / "out", sources=["standard"], offline=True, cache=d / "cache", rules={}, log=QUIET)
        runs.append({it["name"]: (it["status"], it["symbol"]["ref"], it["symbol"].get("file")) for it in items})
    assert runs[0] == runs[1], (runs[0], runs[1])
    refs = {ref for _, ref, _ in runs[1].values()}
    assert {L.POLARIZED_SYMBOL, L.FERRITE_SYMBOL, "Capacitor", "Inductor"} <= refs, runs[1]
    for status, ref, f in runs[1].values():
        assert f is None or Path(f).stem == ref, (ref, f)


def test_footprint_by_series_names_one_land_or_none():
    from ecadlib.librarian import KiCadLibrary
    k = KiCadLibrary.__new__(KiCadLibrary)
    k.footprints = {n: [lib] for lib, n in (
        ("Connector_Molex", "Molex_PicoBlade_53261-0471_1x04-1MP_P1.25mm_Horizontal"),
        ("Connector_Molex", "Molex_PicoBlade_53261-0271_1x02-1MP_P1.25mm_Horizontal"),
        ("Connector_USB", "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"),
        ("Inductor_SMD", "L_Bourns-SRN4018"), ("Inductor_SMD", "L_Bourns-SRN4018_HandSolder"),
        ("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm"), ("Crystal", "Crystal_SMD_3225-4Pin_3.2x2.5mm"),
        ("Inductor_SMD", "L_Bourns_SRP1245A"), ("Inductor_SMD", "L_Bourns-SRP1245A"))}
    S = k.footprint_by_series
    assert S("0532610471") == "Connector_Molex:Molex_PicoBlade_53261-0471_1x04-1MP_P1.25mm_Horizontal"
    assert S("USB4105-GF-A") == "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"
    assert S("SRN4018-100M") == "Inductor_SMD:L_Bourns-SRN4018"                  # not its hand-solder variant
    for m in ("0532610371", "USB4105", "SRN4028-100M", "LM358DR", "ABM8-16.000MHZ-B2-T", "3225", "SRP1245A-100M", "", None):
        assert S(m) is None, (m, S(m))          # another code, too little, no such series, or two names fit


def test_part_search_keeps_the_case_text_as_written():
    from ecadlib import part_lookup as PL
    s = PL.specs_from_pairs([("Supplier Device Package", "8-SOIC"), ("Package / Case", '8-SOIC (0.154", 3.90mm Width)')])
    assert s["package_text"] == '8-SOIC (0.154", 3.90mm Width)'
    s = PL.specs_from_pairs([("Package / Case", "1812 (4532 Metric)")])
    assert s["package"] == "1812" and s["package_text"] == "1812 (4532 Metric)"
    s = PL.specs_from_pairs([("Case Code - mm", "1005")])                   # Mouser's metric code: not a case text
    assert "package_text" not in s
