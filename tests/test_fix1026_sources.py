"""2026-10-26 second review of PR #196 (sources): BOM size headers, datasheet SSRF, maker names, KiCad land choice,
LCSC numbers in links. Plain functions: python -m tests.run_all test_fix1026_sources"""
import contextlib, io

from ecadlib import bom_triage as T
from tests.fixtures_lite import lite


def _read(f, text):
    f.write_bytes(text.encode("utf-8"))
    with contextlib.redirect_stderr(io.StringIO()):
        return T.read_bom(f)


# ---------------------------------------------------------------- R-SRC-1: a header naming metric and imperial
@lite
def test_a_header_naming_metric_and_imp_is_not_metric_only(tmp_path):
    # 'Case (Metric / Imp.)' was read as metric-only ('imp' was no imperial word), so a 0603 resistor became an 0201
    for h in ("Case (Metric / Imp.)", "Case (metric/imp)", "Package (Imp/Metric)", "Size (metric, US)",
              "Case Code Metric / Imperial", "Footprint (Inch/MM)", "Case (mm / mil)", "Case Code (EIA/mm)"):
        assert not T.metric_header(T._hnorm(h)), h
        assert T.mixed_header(T._hnorm(h)), h
    for h in ("Case Code (mm)", "Metric size", "Package", "Case Code (in)", "Package (EIA)", "Case Code in mm"):
        assert not T.mixed_header(T._hnorm(h)), h
    assert T.metric_header(T._hnorm("Case Code in mm"))                   # 'in' there is no inch


@lite
def test_a_code_valid_in_both_systems_under_a_mixed_header_is_held_not_converted(tmp_path):
    for hdr in ("Case (Metric / Imp.)", "Package (Imp/Metric)", "Case (Imperial/Metric)"):
        lines, m = _read(tmp_path / "imp.csv", f"Designator,Qty,MPN,Manufacturer,{hdr}\n"
                                               "R1,1,RC0603FR-0710KL,Yageo,0603\nC1,1,XYZ,Acme,1608\n"
                                               "C2,1,XYZ2,Acme,0805\nU1,1,LDO,Acme,SOT-23-5\nR2,1,XYZ3,Acme,0402\n"
                                               "C3,1,XYZ4,Acme,0603 mm\n")
        assert m["footprint"] == hdr, m
        by = {l["designator"]: l for l in lines}
        assert by["R1"].get("footprint", "") not in ("0201",), (hdr, by["R1"])     # never the wrong size
        assert not by["R1"].get("footprint") and "0603" in by["R1"].get("bom_note", ""), (hdr, by["R1"])
        assert not by["R2"].get("footprint") and "0402" in by["R2"].get("bom_note", ""), (hdr, by["R2"])
        assert by["C1"]["footprint"] == "0603", (hdr, by["C1"])      # 1608 is no inch code: metric, so an 0603
        assert by["C2"]["footprint"] == "0805", (hdr, by["C2"])      # 0805 is no metric code: an inch 0805
        assert by["U1"]["footprint"] == "SOT-23-5", (hdr, by["U1"])
        assert by["C3"]["footprint"] == "0201", (hdr, by["C3"])      # the cell itself says mm


@lite
def test_plain_and_inch_headers_keep_their_0603(tmp_path):
    for hdr in ("Package", "Case Code", "Package (EIA)", "Case (inch)"):
        lines, _ = _read(tmp_path / "p.csv", f"Designator,Qty,MPN,{hdr}\nR1,1,RC0603FR-0710KL,0603\n")
        assert lines[0]["footprint"] == "0603", (hdr, lines)


@lite
def test_an_inch_column_wins_over_a_mixed_one(tmp_path):
    for hdr, row in (("Case (Metric / Imp.),Case Code (in)", "0603,0603"), ("Case Code (in),Case (Metric / Imp.)", "0603,0603")):
        lines, m = _read(tmp_path / "b.csv", f"Designator,Qty,MPN,{hdr}\nR1,1,RC0603FR-0710KL,{row}\n")
        assert m["footprint"] == "Case Code (in)", (hdr, m)
        assert lines[0]["footprint"] == "0603", (hdr, lines)


# ---------------------------------------------------------------- R-SRC-2: numeric IPv4 forms in a datasheet link
@lite
def test_unsafe_download_refuses_every_numeric_form_of_a_local_address(monkeypatch):
    from ecadlib import datasheet as D
    monkeypatch.setattr(D, "_resolve", lambda host: (_ for _ in ()).throw(AssertionError(f"looked up {host}")))
    for host in ("2852039166", "0xa9fea9fe", "0XA9.0XFE.0XA9.0XFE", "127.1", "0177.0.0.1", "0", "0x7f000001",
                 "127.0.0.1.", "169.254.169.254", "0251.0376.0251.0376", "017700000001", "10.1", "0x0a.1"):
        for public in (False, True):
            assert D.unsafe_download(f"http://{host}/x.pdf", public=public), (host, public)
    # a redirect to one is refused too (each hop is checked with the same rule)
    assert D.redirect_refused("https://ds.example.com/a.pdf", "http://2852039166/latest/meta-data/", public=False)
    assert D.redirect_refused("https://ds.example.com/a.pdf", "http://127.1:8080/", public=False)
    # a BOM link to a datasheet server on your own network, written as a dotted quad, stays allowed; public sites too
    for url in ("http://10.1.2.3/ds/x.pdf", "http://192.168.1.20/x.pdf", "https://www.ti.com/lit/ds/symlink/lm358.pdf",
                "http://93.184.216.34/x.pdf", "https://cdn.0x10c.example/x.pdf", "https://1password.example/x.pdf"):
        assert D.unsafe_download(url, public=False) is None, url


# ---------------------------------------------------------------- R-SRC-3: acquired / legacy maker names
ACQUIRED = [("Freescale", "NXP USA Inc."), ("Freescale Semiconductor", "NXP Semiconductors"),
            ("National Semiconductor", "Texas Instruments"), ("Linear Tech", "Analog Devices Inc."),
            ("Linear Technology", "Analog Devices Inc."), ("LT", "Analog Devices"), ("Maxim", "Analog Devices Inc."),
            ("Dallas Semiconductor", "Analog Devices Inc."), ("Micrel", "Microchip Technology"),
            ("Atmel", "Microchip Technology"), ("Microsemi", "Microchip Technology"),
            ("Silicon Storage Technology", "Microchip Technology"), ("Avago", "Broadcom Limited"),
            ("Avago Technologies", "Broadcom"), ("International Rectifier", "Infineon Technologies"),
            ("Fairchild", "onsemi"), ("Fairchild Semiconductor", "ON Semiconductor"), ("Intersil", "Renesas Electronics America Inc"),
            ("Cypress", "Infineon Technologies"), ("Spansion", "Infineon Technologies"), ("Zetex", "Diodes Incorporated"),
            ("Exar", "MaxLinear, Inc."), ("Xilinx", "AMD"), ("Altera", "Intel"), ("Same Sky", "CUI Devices"),
            ("Seiko Epson", "EPSON"), ("Nanjing Qinheng", "WCH"), ("TE", "TE Connectivity AMP Connectors"),
            ("Tyco Electronics", "TE Connectivity"), ("WE", "Würth Elektronik"), ("GD", "GigaDevice Semiconductor (HK) Limited"),
            ("Epcos", "TDK Corporation"), ("IXYS", "Littelfuse Inc."), ("ST Micro", "STMicroelectronics"),
            ("Murata Manufacturing", "Murata Electronics North America"), ("Siliconix", "Vishay Siliconix")]
NOT_SAME = [("ST", "Stackpole Electronics"), ("TI", "Analog Devices Inc."), ("NXP", "Nexperia"), ("Nexperia", "NXP USA Inc."),
            ("Freescale", "Nexperia"), ("National Semiconductor", "Analog Devices"), ("Avago", "Microchip"),
            ("Linear Integrated Systems", "Analog Devices Inc."),
            ("Microchip", "Micro Commercial Co"), ("TE", "Texas Instruments"), ("WE", "Winbond Electronics"),
            ("GD", "Good-Ark"), ("Exar", "Microchip")]


def test_acquired_makers_match_both_ways_and_others_do_not():
    from ecadlib.providers import same_maker as S
    for a, b in ACQUIRED:
        assert S(a, b) and S(b, a), (a, b)
    for a, b in NOT_SAME:
        assert not S(a, b) and not S(b, a), (a, b)


def test_digikey_lifecycle_finds_a_part_listed_under_its_makers_new_name():
    from ecadlib import lifecycle as L
    for mpn, bom, dk in (("MC9S08QG8CPBE", "Freescale", "NXP USA Inc."), ("LM317T", "National Semiconductor", "Texas Instruments"),
                         ("LT1763CS8", "Linear Tech", "Analog Devices Inc."), ("MIC5205", "Micrel", "Microchip Technology"),
                         ("S25FL", "Spansion", "Infineon Technologies"), ("ACPL", "Avago", "Broadcom Limited"),
                         ("XR21V1410", "Exar", "MaxLinear, Inc."), ("CH340G", "Nanjing Qinheng", "WCH")):
        d = {"Product": {"ManufacturerProductNumber": mpn, "Manufacturer": {"Name": dk}, "ProductStatus": {"Status": "Obsolete"}}}
        r = L.parse_digikey(d, {"mpn": mpn, "manufacturer": bom}, L.LifecycleResult("digikey", mpn=mpn))
        assert r.found and r.status == "obsolete", (bom, dk, r)
    d = {"Product": {"ManufacturerProductNumber": "LM358", "Manufacturer": {"Name": "Analog Devices Inc."},
                     "ProductStatus": {"Status": "Obsolete"}}}
    r = L.parse_digikey(d, {"mpn": "LM358", "manufacturer": "TI"}, L.LifecycleResult("digikey", mpn="LM358"))
    assert r.found is False, r


# ---------------------------------------------------------------- R-SRC-5: _pick counts other makers by company
def test_a_name_that_only_starts_like_another_is_not_that_maker():
    from ecadlib.providers import same_maker as S
    for a, b in (("Analog Devices", "Analog Power"), ("Analog Devices", "Analogix Semiconductor"), ("ADI", "Analog Power"),
                 ("Micron", "Micronas"), ("General Electric", "General Semiconductor"), ("Vishay", "Vishay Precision Group"),
                 ("Vishay Dale", "VPG"), ("HUAXIN", "HUAXINAN"), ("American", "American Technical Ceramics"),
                 ("Linear Tech", "Linear Integrated Systems"), ("AMS", "ams OSRAM")):
        assert not S(a, b) and not S(b, a), (a, b)
    for a, b in (("Analog Devices", "Analog Devices Global"), ("Murata", "Murata Power Solutions"),
                 ("TE Connectivity", "TE Connectivity AMP Connectors"), ("Vishay", "Vishay Siliconix"), ("Würth", "Würth Elektronik"),
                 ("MDD", "MDD Microdiode"), ("Silicon Labs", "Silicon Laboratories Inc"), ("Analog Devices", "ADI"),
                 ("Linear Technology", "ADI"), ("Samsung", "Samsung Electro-Mechanics"), ("Wakefield", "Wakefield-Vette"),
                 ("OSRAM", "ams OSRAM"), ("OSRAM Opto Semiconductors", "ams-OSRAM USA INC.")):
        assert S(a, b) and S(b, a), (a, b)


def test_pick_counts_other_makers_by_company_not_by_name():
    from ecadlib import lifecycle as L
    items = [{"mpn": "X1", "m": "TE Connectivity"}, {"mpn": "X1", "m": "TE Connectivity AMP Connectors"}]
    got = L._pick(items, {"mpn": "X1", "manufacturer": "Foo Corp"}, lambda i: i["mpn"], lambda i: i["m"])
    assert got is items[0], got                              # one other company under two names: as one maker
    items = [{"mpn": "X1", "m": "Texas Instruments"}, {"mpn": "X1", "m": "STMicroelectronics"}]
    assert L._pick(items, {"mpn": "X1", "manufacturer": "Foo Corp"}, lambda i: i["mpn"], lambda i: i["m"]) is None


# ---------------------------------------------------------------- R-SRC-4: KiCad land choice
def _index(fps):
    from ecadlib.kicad_index import KiCadIndex
    idx = KiCadIndex([])
    idx.footprints = {k: {"path": "", "has_3d": True} for k in fps}
    return idx


STOCK = ["Resistor_SMD:R_0603_1608Metric", "Resistor_SMD:R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",
         "Resistor_SMD:R_Array_Concave_2x0603", "Resistor_SMD:R_Array_Concave_4x0603", "Resistor_SMD:R_Array_Convex_4x0603",
         "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric_Pad1.08x0.95mm_HandSolder",
         "Inductor_SMD:L_0603_1608Metric", "Inductor_SMD:L_0603_1608Metric_Pad1.05x0.95mm_HandSolder",
         "LED_SMD:LED_0603_1608Metric", "LED_SMD:LED_0603_1608Metric_Pad1.05x0.95mm_HandSolder",
         "LED_SMD:LED_0603_1608Metric_Castellated",
         "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
         "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm_ThermalVias"]


def test_a_hand_solder_variant_is_no_ambiguity():
    for fps in (STOCK, STOCK[::-1]):
        idx = _index(fps)
        assert idx.find_footprint_for_package("0603", "R", hint="0603 ") == "Resistor_SMD:R_0603_1608Metric"
        assert idx.find_footprint_for_package("0603", "C", hint="0603 CAP CER 100NF") == "Capacitor_SMD:C_0603_1608Metric"
        assert idx.find_footprint_for_package("0603", "L", hint="0603") == "Inductor_SMD:L_0603_1608Metric"
        assert idx.find_footprint_for_package("0603", "LED", hint="0603 green") == "LED_SMD:LED_0603_1608Metric"
        # a thermal-vias copy of one exposed-pad land is the same land
        assert idx.find_footprint_for_package("QFN16", "U", hint="QFN-16 3x3mm exposed pad") == \
            "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm"
    # only the hand-solder land indexed: it is the one
    only = ["Resistor_SMD:R_0603_1608Metric_Pad0.98x0.95mm_HandSolder"]
    assert _index(only).find_footprint_for_package("0603", "R", hint="0603") == only[0]
    # a resistor array is asked for by name, never handed to a plain 0603 resistor
    assert _index(STOCK).find_footprint_for_package("0603", "R", hint="0603 RES ARRAY 4x 10k") is None


def test_real_land_ambiguity_still_holds():
    qfn = ["lib:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm", "lib:QFN-16-1EP_3x3mm_P0.5mm_EP1.45x1.45mm"]
    assert _index(qfn).find_footprint_for_package("QFN16", "U", hint="QFN-16 3x3mm exposed pad") is None
