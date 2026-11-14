"""Fixes from the 2026-11-14 DigiKey / Mouser key run on 30 real parts (RESULT_WITH_KEYS.md).
Plain functions: python3 -m tests.run_all test_keys_run_fixes"""
from pathlib import Path

from ecadlib import librarian as L, stdparts as SP
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def _run(tmp_path, text, sources=("standard",)):
    d = Path(tmp_path)
    (d / "bom.csv").write_text(text, encoding="utf-8")
    items = L.run(d / "bom.csv", out=d / "out", sources=list(sources), offline=True, cache=d / "cache", rules={}, log=QUIET)
    return {it["name"]: it for it in items}


def test_is_ferrite():
    assert SP.is_ferrite("FB1", "", "")
    assert SP.is_ferrite("L1", "", "BLM18PG221SN1D") and SP.is_ferrite("L3", "", "MPZ1608S221ATA00")
    assert SP.is_ferrite("L2", "Ferrite bead 600R 0603", "") and SP.is_ferrite("L2", "FERRITE BEAD 220 OHM 0603 1LN", "X")
    for args in (("L1", "", "SRN4018-100M"), ("L1", "Inductor 10uH 0805", "LQM21PN100MC0D"), ("L1, FB1", "", ""),
                 ("L1", "Power inductor", "XAL4020-102MEB"), ("", "", "")):
        assert not SP.is_ferrite(*args), args
    text, name = SP.symbol_text("FerriteBead")
    assert name == "FerriteBead" and text.count("(pin passive") == 2


@lite
def test_ferrite_bead_gets_its_own_symbol_and_an_inductor_keeps_its_own(tmp_path):
    got = _run(tmp_path, "Designator,Qty,Value,Footprint,Manufacturer,MPN,Description\n"
                         "L1,1,10uH,L_0805_2012Metric,Murata,LQM21PN100MC0D,Inductor 10uH 0805\n"
                         "FB1,1,600R,L_0603_1608Metric,Murata,BLM18PG601SN1D,Ferrite bead 600R 0603\n"
                         "L2,1,220R,L_0603_1608Metric,Murata,BLM18PG221SN1D,\n")
    by = {next(p["value"] for p in it["parameters"] if p["name"] == "Manufacturer Part Number"): it for it in got.values()}
    ind = by["LQM21PN100MC0D"]
    assert ind["symbol"]["ref"] == "Inductor" and ind["source"]["symbol"] == "standard:L", ind["source"]
    for mpn in ("BLM18PG601SN1D", "BLM18PG221SN1D"):
        fb = by[mpn]
        assert fb["symbol"]["ref"] == L.FERRITE_SYMBOL and fb["source"]["symbol"] == "standard:FerriteBead", (mpn, fb["source"])
        assert fb["symbol"]["file"] != ind["symbol"]["file"]


def test_description_source_names_lcsc_only_for_lcsc_text():
    st = {"found": {}}
    row = {"src_description": "RES 10K OHM 1% 1/10W 0603", "src_description_from": "digikey", "lcsc": "C98220",
           "src_manufacturer": "YAGEO", "src_manufacturer_from": "digikey"}
    L.Run._finish_found(st, row)
    assert st["found"]["description"][1] == "digikey part data", st["found"]
    assert "C98220" not in st["found"]["manufacturer"][1], st["found"]
    st = {"found": {}}
    L.Run._finish_found(st, dict(row, src_description_from="jlcpcb_search"))
    assert st["found"]["description"][1] == "LCSC/JLCPCB part data C98220", st["found"]


def test_symbol_family_text_naming_packages_waits_for_the_part_search():
    st = {"found": {"kicad_description": ("Low-Power, Dual Operational Amplifiers, DIP-8/SOIC-8/TO-99-8", "KiCad symbol X")}}
    L.Run._finish_found(st, {"src_description": "IC OPAMP GP 2 CIRCUIT 8SOIC", "src_description_from": "digikey"})
    assert st["found"]["description"][0] == "IC OPAMP GP 2 CIRCUIT 8SOIC", st["found"]
    st = {"found": {"kicad_description": ("Low-Power, Dual Operational Amplifiers, DIP-8/SOIC-8/TO-99-8", "KiCad symbol X")}}
    L.Run._finish_found(st, {})                                     # no part search text: the symbol's is still used
    assert st["found"]["description"][1] == "KiCad symbol X", st["found"]


def test_part_search_datasheet_link_reaches_held_parts():
    st = {"found": {}}
    L.Run._finish_found(st, {"src_datasheet": "https://www.ti.com/lit/ds/symlink/lm358.pdf", "src_datasheet_from": "digikey"})
    assert st["found"]["datasheet"] == ("https://www.ti.com/lit/ds/symlink/lm358.pdf", "digikey part data"), st["found"]
    st = {"found": {"datasheet": ("https://kicad.example/ds.pdf", "KiCad symbol X")}}       # the symbol's own link first
    L.Run._finish_found(st, {"src_datasheet": "https://other.example/ds.pdf", "src_datasheet_from": "mouser"})
    assert st["found"]["datasheet"][1] == "KiCad symbol X"
    st = {"found": {}}
    L.Run._finish_found(st, {"src_datasheet": "see web", "src_datasheet_from": "digikey"})   # not a link
    assert "datasheet" not in st["found"]


def test_distributor_package_text_names_the_ic_land():
    F = SP.footprint_for
    assert F(["", '8-SOIC (0.154", 3.90mm Width)'], "U", "LM358DR")[0] == "SOIC-8_3.9x4.9mm_P1.27mm"
    assert F(["", '14-TSSOP (0.173", 4.40mm Width)'], "U", "")[0] == "TSSOP-14_4.4x5mm_P0.65mm"
    assert F(["", '16-SOIC (0.154", 3.90mm Width)'], "U", "")[0] == "SOIC-16_3.9x9.9mm_P1.27mm"
    for t in ('8-SOIC (0.209", 5.30mm Width)',            # two KiCad bodies fit: held
              '8-SOIC (0.154", 3.90mm Width) Exposed Pad',  # pad size unknown
              '8-WDFN Exposed Pad', '48-LQFP', '8-DIP (0.300", 7.62mm)'):
        assert F(["", t], "U", "")[0] is None, t
    # the BOM's own land and the part search's case disagree: nothing built
    r = SP.resolve(["", "SOIC-14_3.9x8.7mm_P1.27mm", "", "", '8-SOIC (0.154", 3.90mm Width)'], "U", "LM358DR", "")
    assert r["footprint"] in (None, "SOIC-14_3.9x8.7mm_P1.27mm"), r


def test_single_row_connector_symbol_only_on_a_plain_numbered_row():
    M = SP.mechanical_symbol
    assert M("kicad:Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", "J1", {"1", "2"})[0] == "Conn_01x02"
    assert M("kicad:Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", "P3, J4", {"1", "2", "3", "4"})[0] == "Conn_01x04"
    # a '-1MP' land's mounting pads are the symbol's MP pin (2026-11-14 100-part run, test_set3_fixes)
    assert M("kicad:Connector_Molex:Molex_PicoBlade_53261-0471_1x04-1MP_P1.25mm_Horizontal", "J2",
             {"1", "2", "3", "4", "MP"})[0] == "Conn_01x04_MountingPin"
    for args in (("kicad:Connector_Molex:Molex_PicoBlade_53261-0471_1x04-1MP_P1.25mm_Horizontal", "J2", {"1", "2", "3", "4"}),
                 ("kicad:Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", "JP1", {"1", "2"}),     # a jumper
                 ("kicad:Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", "J1", {"1", "2", "3"}),
                 ("kicad:Connector_PinHeader_2.54mm:PinHeader_2x04_P2.54mm_Vertical", "J1", {str(i) for i in range(1, 9)}),
                 ("easyeda:C123", "J1", {"1", "2"}), ("kicad:Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", "", {"1", "2"})):
        assert M(*args)[0] is None, args
    text, name = SP.symbol_text("Conn_01x03")
    assert name == "Conn_01x03" and text.count("(pin passive") == 3
    for i in (1, 2, 3):
        assert f'(number "{i}"' in text


@lite
def test_part_search_case_text_datasheet_and_description_end_to_end(tmp_path, monkeypatch):
    """A part search (DigiKey here) answers for the exact MPN: its case text builds the IC land and the PTC's 1812 land,
    its description and datasheet link reach the part, and the source label names DigiKey only."""
    from ecadlib import bom_triage as T
    real = T.triage
    said = {"LM358DR": ('8-SOIC (0.154", 3.90mm Width)', "IC OPAMP GP 2 CIRCUIT 8SOIC", "https://www.ti.com/lit/ds/lm358.pdf"),
            "MF-MSMF050-2": ("1812 (4532 Metric)", "PTC RESET FUSE 15V 500MA 1812", "https://www.bourns.com/docs/mf-msmf.pdf")}

    def fake(lines, *a, **k):
        rows = real(lines, *a, **k)
        for r in rows:
            if r["mpn"] in said:
                pkg, desc, url = said[r["mpn"]]
                r.update(src_package=pkg, src_package_from="digikey", src_description=desc, src_description_from="digikey",
                         src_datasheet=url, src_datasheet_from="digikey", lcsc="C7950 (via MPN)")
        return rows
    monkeypatch.setattr(T, "triage", fake)
    got = _run(tmp_path, "Designator,Qty,Value,Footprint,Manufacturer,MPN,Description\n"
                         "U1,1,,,Texas Instruments,LM358DR,\n"
                         "F2,1,500mA,,Bourns,MF-MSMF050-2,\n")
    u1 = next(i for i in got.values() if "LM358DR" in str(i["parameters"]))
    assert "SOIC-8_3.9x4.9mm" in str(u1["source"]["footprint"]), u1["source"]
    assert u1["description"] == "IC OPAMP GP 2 CIRCUIT 8SOIC", u1["description"]
    par = {p["name"]: p["value"] for p in u1["parameters"]}
    assert par.get("ComponentLink2URL") == "https://www.ti.com/lit/ds/lm358.pdf", par
    assert "C7950" not in str(u1.get("field_sources")), u1.get("field_sources")
    f2 = next(i for i in got.values() if "MF-MSMF050-2" in str(i["parameters"]))
    assert "Fuse_1812" in str(f2["source"]["footprint"]) and f2["source"]["symbol"] == "standard:Polyfuse", f2["source"]


def test_part_or_series_number_in_a_footprint_name_is_not_a_pin_count():
    """FC-135 (an Epson crystal), KCSC02-105 (a display), ESP-07 (a module): the number names the part, not 135 pins."""
    from ecadlib import footprint_rules as FR
    for n in ("FC-135", "FC-135_3.2x1.5mm", "KCSC02-105", "ESP-07", "WE-PD-1050", "ESP32-S3-WROOM-1"):
        assert FR.parse_name(n).get("pins") is None, (n, FR.parse_name(n))
    for n, pins in (("SOIC-8_3.9x4.9mm_P1.27mm", 8), ("HTSSOP-20-1EP_4.4x6.5mm_P0.65mm", 20), ("UFQFPN-28", 28),
                    ("LFCSP-VQ-24-1EP_4x4mm_P0.5mm", 24), ("DFN-S-8-1EP_6x5mm_P1.27mm", 8), ("TSOP-I-24_12.4x6mm_P0.5mm", 24),
                    ("DSUB-15_Pins_EdgeMount_P2.77mm", 15), ("SolderJumper-2_P1.3mm_Bridged2Bar_Pad1.0x1.5mm", 2),
                    ("MSOP-12-16_3x4mm_P0.5mm", 12), ("WFDFPN-8-1EP_3x2mm_P0.5mm", 8), ("SOT-23-5", 5)):
        assert FR.parse_name(n).get("pins") == pins, (n, FR.parse_name(n))


def test_review_case_text_with_extra_pads_or_leads_is_not_used():
    C = SP.case_text
    for t in ("0603 (1608 Metric), 3 PC Pad", "1206 (3216 Metric), 4 PC Pad", "1206 (3216 Metric), 4 Leads",
              "1206 (3216 Metric), Convex, Long Side Terminals", '8-SOIC (0.154", 3.90mm Width), 7 Leads', "Radial", ""):
        assert C(t) == "", t
    assert C('8-SOIC (0.154", 3.90mm Width)') and C("1812 (4532 Metric)")
    F = SP.footprint_for
    assert F(["", '8-SOIC (0.154", 3.90mm Width), 7 Leads'], "U", "LNK304DG")[0] is None
    assert F(["", '8-SSOP (0.118", 3.00mm Width)'], "U", "")[0] is None            # 3 mm wide: not SSOP-8_5.3x3mm


def test_review_ferrite_core_inductor_is_not_a_bead():
    assert not SP.is_ferrite("L1", "Power inductor 10uH shielded ferrite core", "SRN3225-100M")
    assert SP.is_ferrite("L1", "Ferrite Chip 600 Ohm", "") and SP.is_ferrite("L1", "FERRITE BEAD 220 OHM 0603", "")


def test_review_case_text_only_when_the_bom_is_silent_and_one_maker():
    st, item = {"hint_fp": ""}, {"footprint": {"kicad": ""}, "description": ""}
    row = {"src_package": '8-SOIC (0.154", 3.90mm Width)'}
    R = L.Run.case_from_search
    assert R(st, item, {"footprint": "", "description": ""}, row)
    assert R(st, item, {"footprint": "", "description": "Dual op amp DIP-8"}, row) == ""
    assert R(st, item, {"footprint": "", "description": "LDO SOT-23-3"}, row) == ""
    assert R(st, item, {"footprint": "SOIC-14_3.9x8.7mm_P1.27mm", "description": ""}, row) == ""
    assert R(st, item, {"footprint": "", "description": ""}, dict(row, src_several="yes")) == ""
    s = {"found": {}}
    L.Run._finish_found(s, {"src_datasheet": "https://x.example/a.pdf", "src_several": "yes"})
    assert "datasheet" not in s["found"]
    s = {"found": {}}
    L.Run._finish_found(s, {"src_datasheet": "https://x.example/a.pdf"})               # an older row: no source named
    assert s["found"]["datasheet"] == ("https://x.example/a.pdf", "the part search")
