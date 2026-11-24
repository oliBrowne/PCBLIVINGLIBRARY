"""Planted-error hunt 2026-11-23: a line whose own sources disagree about the package, the value or the maker is held,
never built from whichever source was read first. Each case here is one a planted slip got through on main.
Plain functions, no pytest needed: python3 -m tests.run_all test_source_clash_1123"""
from ecadlib import librarian as L


def _st(fp_source):
    return {"footprint": {"source": fp_source}}


def _item(mpn="", value="", folder="", description=""):
    pars = [{"name": "Manufacturer Part Number", "value": mpn}]
    if value:
        pars.append({"name": "Value", "value": value})
    return {"parameters": pars, "folder": folder, "description": description}


def _pkg(fp_source, ln=None, row=None, kind=None, mpn=""):
    return L.package_clash(_st(fp_source), _item(mpn), ln or {}, row or {}, kind)


# ------------------------------------------------------------------ package: IC and discrete outlines

def test_part_search_package_naming_another_outline_holds():
    # TPS2051BDBVR: DBV is SOT-23-5; a part search answer of SC-70-5 built SOT-353 with no hold
    msg = _pkg("standard:Package_TO_SOT_SMD:SOT-23-5",
               row={"src_package": "5-TSSOP, SC-70-5, SOT-353", "src_description": "IC POWER SW SOT-23-5"})
    assert msg and "SOT-353" in msg


def test_part_search_package_against_its_own_description_holds_a_diode():
    # BAS16LT1G: package SOD-123 but the description says SOT23
    assert _pkg("standard:Diode_SMD:D_SOD-123", row={"src_package": "SOD-123",
                                                     "src_description": "DIODE STANDARD 100V 200MA SOT23"}, kind="D")
    # B340A: SMB package, SMA description; SMBJ12A: SMA package, DO214AA (SMB) description
    assert _pkg("standard:Diode_SMD:D_SMB", row={"src_package": "DO-214AA, SMB",
                                                 "src_description": "DIODE SCHOTTKY 40V 3A SMA"}, kind="D")
    assert _pkg("standard:Diode_SMD:D_SMA", row={"src_package": "DO-214AC, SMA",
                                                 "src_description": "TVS DIODE 12VWM 19.9VC DO214AA"}, kind="D")


def test_bom_description_naming_another_outline_holds():
    assert _pkg("kicad:Package_TO_SOT_SMD:SOT-23-5", ln={"description": "Schmitt inverter SOT-23-6"})
    assert _pkg("standard:Package_TO_SOT_SMD:SOT-23-5", ln={"description": "Op amp SC-70-5"})
    assert _pkg("kicad:Diode_SMD:D_SMA", ln={"description": "Schottky 40V 3A SMB"}, kind="D")


def test_family_and_pin_count_in_words_hold():
    # '8-TSSOP' names no body, but it is not a SOIC
    assert _pkg("standard:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", ln={"description": "Dual JFET op amp, 8-TSSOP"})
    # a 5.3 mm wide SOIC-8 is not the 3.9 mm one
    assert _pkg("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", row={"src_package": "8-SOIC (0.209\", 5.30mm Width)"})
    # 16 pins is not 8
    assert _pkg("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", row={"src_package": "16-SOIC (0.295\", 7.50mm Width)"})
    assert _pkg("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", row={"src_package": "8-VSSOP, 8-MSOP (0.118\", 3.00mm Width)"})


def test_agreeing_texts_do_not_hold():
    ok = [("kicad:Package_TO_SOT_SMD:SOT-23-5", {"src_package": "SC-74A, SOT-753", "src_description": "IC REG LINEAR 3.3V 200MA SOT23-5"}, None),
          ("kicad:Package_TO_SOT_SMD:TSOT-23-5", {"src_package": "SOT-23-5 Thin, TSOT-23-5"}, None),
          ("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", {"src_package": "8-SOIC (0.154\", 3.90mm Width)", "src_description": "IC OP AMP 2CH 8-SOIC"}, None),
          ("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", {"src_package": "8-SO"}, None),
          ("standard:Package_TO_SOT_SMD:SOT-23", {"src_package": "TO-236-3, SC-59, SOT-23-3", "src_description": "DIODE STANDARD 100V 200MA SOT23"}, "D"),
          ("standard:Diode_SMD:D_SMA", {"src_package": "DO-214AC, SMA", "src_description": "DIODE SCHOTTKY 40V 3A SMA"}, "D"),
          ("kicad:Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm", {"src_package": "20-TSSOP (0.173\", 4.40mm Width)"}, None),
          ("kicad:Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm", {"src_package": "16-SOIC (0.295\", 7.50mm Width)"}, None),
          ("easyeda:C12345", {"src_package": "SOT-23-6"}, None)]       # an LCSC land names no outline: not compared
    for src, row, kind in ok:
        assert _pkg(src, row=row, kind=kind) is None, (src, row)


# ------------------------------------------------------------------ package: tantalum case

def test_tantalum_case_against_its_part_number_holds():
    # T491B106K016AT is case B; the BOM's footprint named case A and case A was built
    assert _pkg("kicad:Capacitor_Tantalum_SMD:CP_EIA-3216-18_Kemet-A", ln={"footprint": "CP_EIA-3216-18_Kemet-A"},
                mpn="T491B106K016AT", kind="C")


def test_tantalum_case_against_its_description_holds():
    # built case C (6032) by its code, the description says case B; built case B, the part search's size is 1206 (A)
    assert _pkg("kicad:Capacitor_Tantalum_SMD:CP_EIA-6032-28_Kemet-C", ln={"description": "CAP TANT 10UF 25V case B"},
                mpn="T491C106K025AT", kind="C")
    assert _pkg("kicad:Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B",
                row={"src_package": "1206 (3216 Metric)", "src_description": "CAP TANT 22UF 10% 10V 1206"},
                mpn="T491B226K010AT", kind="C")


def test_tantalum_agreeing_texts_do_not_hold():
    assert _pkg("kicad:Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B",
                row={"src_package": "1411 (3528 Metric)", "src_description": "CAP TANT 10UF 10% 16V 1411"},
                mpn="T491B106K016AT", kind="C") is None
    assert _pkg("kicad:Capacitor_Tantalum_SMD:CP_EIA-7343-31_Kemet-D",
                row={"src_package": "2917 (7343 Metric)", "src_description": "CAP TANT 100UF 10% 10V 2917"},
                mpn="TAJD107K010RNJ", kind="C") is None


# ------------------------------------------------------------------ package: chip size from the part search

def test_chip_size_in_the_part_search_text_holds():
    # CL21B105KBFNNNE is an 0805; the part search says 1206
    assert _pkg("kicad:Capacitor_SMD:C_0805_2012Metric",
                row={"src_package": "1206 (3216 Metric)", "src_description": "CAP CER 1UF 50V X7R 1206"}, kind="C")
    assert _pkg("kicad:Capacitor_SMD:C_0805_2012Metric",
                row={"src_package": "0805 (2012 Metric)", "src_description": "CAP CER 1UF 50V X7R 0805"}, kind="C") is None


# ------------------------------------------------------------------ value

def _val(value, folder, ln=None, row=None, mpn=""):
    return L.value_clash(_item(mpn, value, folder), ln or {}, row or {})


def test_value_against_the_part_search_description_holds():
    assert _val("22uH", "Components/Inductors", row={"src_description": "FIXED IND 2.2UH 600MA 425 MOHM"})
    assert _val("60R", "Components/Inductors", row={"src_description": "FERRITE BEAD 600 OHM 0603 1LN"})
    assert _val("10nF", "Components/Capacitors", row={"src_description": "CAP CER 1000PF 50V X7R 0402"})
    assert _val("22kΩ", "Components/Resistors", row={"src_description": "RES SMD 2.2K OHM 1% 1/10W 0603"})
    assert _val("10uF", "Components/Capacitors", row={"src_description": "CAP TANT 1UF 10% 16V 1411"})


def test_agreeing_values_do_not_hold():
    for value, folder, desc in [("10nF", "Components/Capacitors", "CAP CER 10000PF 50V X7R 0402"),
                                ("2.2UH", "Components/Inductors", "FIXED IND 2.2UH 600MA 425 MOHM"),
                                ("600 OHM", "Components/Inductors", "FERRITE BEAD 600 OHM 0603 1LN"),
                                ("0Ω", "Components/Resistors", "RES 0 OHM JUMPER 1/10W 0603"),
                                ("47kΩ", "Components/Resistors", "RES 47K OHM 1% 1/16W 0402"),
                                ("100nF", "Components/Capacitors", "CAP CER 0.1UF 16V X7R 0402"),
                                ("10uH", "Components/Inductors", "FIXED IND 10UH 2A 49 MOHM SMD"),
                                ("10uF", "Components/Capacitors", "Multilayer Ceramic Capacitors MLCC - SMD/SMT"),
                                ("", "Components/Resistors", "RES 10K OHM 1% 1/10W 0603")]:
        assert _val(value, folder, row={"src_description": desc}) is None, (value, desc)


# ------------------------------------------------------------------ maker

def test_bom_maker_the_part_search_does_not_list_holds():
    assert L.maker_clash({"manufacturer": "Microchip"}, {"src_makers": "ROHM Semiconductor; Texas Instruments"})
    assert L.maker_clash({"manufacturer": "Texas Instruments"}, {"src_makers": "Analog Devices Inc."})


def test_bom_maker_among_the_listed_makers_does_not_hold():
    for bom, row in [("Texas Instruments", {"src_makers": "ROHM Semiconductor; Texas Instruments"}),
                     ("TI", {"src_makers": "Texas Instruments"}),
                     ("Maxim", {"src_makers": "Analog Devices Inc./Maxim Integrated"}),
                     ("Yageo", {"src_makers": "YAGEO"}),
                     ("", {"src_makers": "Texas Instruments"}),          # no BOM maker: nothing to disagree with
                     ("N/A", {"src_makers": "Texas Instruments"}),
                     ("Vishay", {"src_makers": ""})]:                     # no maker listed: nothing to compare
        assert L.maker_clash({"manufacturer": bom}, row) is None, (bom, row)


def test_bom_maker_against_the_maker_a_part_number_code_names_holds():
    # an RC0402FR-07..L is a Yageo code; a Murata GRM code on a "Samsung" line; the MPN typed in the maker column
    assert L.maker_clash({"manufacturer": "Vishay", "mpn": "RC0402FR-0710KL"}, {})
    assert L.maker_clash({"manufacturer": "Samsung", "mpn": "GRM188R71H104KA93D"}, {})
    assert L.maker_clash({"manufacturer": "GRM155R71H103KA88D", "mpn": "GRM155R71H103KA88D"}, {})
    for bom, mpn in [("Yageo", "RC0402FR-0710KL"), ("YAGEO Corporation", "CC0603KRX7R9BB104"),
                     ("Murata Electronics", "GRM188R71H104KA93D"), ("AVX", "TAJB106K016RNJ"),
                     ("Kyocera AVX", "08055C104KAT2A"), ("Panasonic", "ERJ-3EKF1002V"), ("Vishay Dale", "CRCW060310K0FKEA"),
                     ("KEMET", "T491A106K016AT"), ("TDK Corporation", "C1608X7R1H104K080AA"),
                     ("Stackpole Electronics Inc", "RMCF0603FT10K0"), ("", "RC0402FR-0710KL")]:
        assert L.maker_clash({"manufacturer": bom, "mpn": mpn}, {}) is None, (bom, mpn)


def test_chip_land_of_another_kind_holds():
    # C15 100nF with the BOM footprint R_0402_1005Metric was built as a capacitor on a resistor land
    assert L.land_kind_clash(_st("kicad:Resistor_SMD:R_0402_1005Metric"), _item(folder="Components/Capacitors"))
    assert L.land_kind_clash(_st("kicad:Capacitor_SMD:C_0603_1608Metric"), _item(folder="Components/Resistors"))
    assert L.land_kind_clash(_st("standard:Capacitor_SMD:C_0805_2012Metric"), _item(folder="Components/Inductors"))
    for src, folder in [("kicad:Resistor_SMD:R_0402_1005Metric", "Components/Resistors"),
                        ("kicad:Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B", "Components/Capacitors"),
                        ("kicad:Inductor_SMD:L_0603_1608Metric", "Components/Inductors"),
                        ("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "Components/Resistors"),   # an array: not a chip land
                        ("kicad:Resistor_SMD:R_0603_1608Metric", "Components/ICs")]:
        assert L.land_kind_clash(_st(src), _item(folder=folder)) is None, (src, folder)


def test_kicad_msop_named_tssop_and_other_kicad_lands_compare_by_family():
    # KiCad names the 3x3 mm MSOP body TSSOP-10_3x3mm; a part search's 10-MSOP agrees, a 10-TSSOP 4.4 mm does not
    assert _pkg("kicad:Package_SO:TSSOP-10_3x3mm_P0.5mm",
                row={"src_package": "10-TFSOP, 10-MSOP (0.118\", 3.00mm Width)", "src_description": "IC ADC 16BIT 10-VSSOP"}) is None
    assert _pkg("kicad:Package_QFP:LQFP-48_7x7mm_P0.5mm", row={"src_package": "48-LQFP", "src_description": "IC MCU 48LQFP"}) is None
    assert _pkg("kicad:Package_QFP:LQFP-48_7x7mm_P0.5mm", row={"src_package": "64-LQFP"})
    assert _pkg("kicad:Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm", row={"src_package": "20-SOIC (0.295\", 7.50mm Width)"})
    # NXP's TSSOP8 is the 3 mm body: 'TSSOP' with no width names no other outline
    assert _pkg("kicad:Package_SO:TSSOP-8_3x3mm_P0.65mm", row={"src_package": "8-TSSOP, 8-MSOP (0.118\", 3.00mm Width)",
                                                               "src_description": "IC XLTR VL BIDIR 8-TSSOP"}) is None


def test_no_lead_outlines_compare_pin_count_and_a_stated_body():
    assert _pkg("kicad:Package_DFN_QFN:QFN-28-1EP_5x5mm_P0.5mm_EP3.35x3.35mm", row={"src_description": "IC USB TO UART BRIDGE QFN24"})
    assert _pkg("kicad:Package_DFN_QFN:VQFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm",
                row={"src_package": "16-VFQFN Exposed Pad", "src_description": "IC BATT CHG LI-ION 1CELL 16QFN 4x4"})
    for row in [{"src_package": "16-VFQFN Exposed Pad", "src_description": "IC BATT CHG LI-ION 1CELL 16QFN"},
                {"src_package": "48-UFQFN Exposed Pad", "src_description": "IC MCU 32BIT 256KB FLSH 48UFQFPN"}]:
        built = "kicad:Package_DFN_QFN:VQFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm" if "16" in row["src_package"] else \
            "kicad:Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.6x5.6mm"
        assert _pkg(built, row=row) is None, row
    assert _pkg("kicad:Package_DFN_QFN:DFN-8-1EP_3x3mm_P0.5mm_EP1.66x2.38mm",
                row={"src_package": "8-WFDFN Exposed Pad", "src_description": "IC REG LINEAR POS ADJ 1.1A 8-DFN"}) is None


def test_crystal_body_or_pin_count_in_the_text_holds():
    assert _pkg("kicad:Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", row={"src_description": "CRYSTAL 32.7680KHZ 12.5PF SMD 2.0x1.2mm"})
    assert _pkg("kicad:Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", row={"src_description": "CRYSTAL 16.0000MHZ 9PF SMD 2520"})
    assert _pkg("kicad:Crystal:Crystal_SMD_Abracon_ABM8G-4Pin_3.2x2.5mm", row={"src_description": "CRYSTAL 12.0000MHZ 10PF SMD 2.0x1.6"})
    assert _pkg("kicad:Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", row={"src_package": "4-SMD, No Lead"})
    for src, row in [("kicad:Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", {"src_package": "2-SMD, No Lead", "src_description": "CRYSTAL 32.7680KHZ 12.5PF SMD"}),
                     ("kicad:Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", {"src_package": "4-SMD, No Lead", "src_description": "CRYSTAL 16MHZ 3225 3.2x2.5mm"}),
                     ("kicad:Crystal:Crystal_SMD_HC49-SD", {"src_package": "HC-49/US", "src_description": "CRYSTAL 16.0000MHZ 18PF SMD"})]:
        assert _pkg(src, row=row) is None, (src, row)


def test_connector_position_count_in_the_text_holds():
    assert _pkg("kicad:Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical",
                row={"src_description": "Headers & Wire Housings XH HEADER 3POS (TOP)"})
    assert _pkg("kicad:Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", row={"src_description": "CONN HEADER VERT 5POS 2.54MM"})
    assert _pkg("kicad:Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical", ln={"description": "Header 2x5 10 pos"}) is None
    assert _pkg("kicad:Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", row={"src_description": "CONN HEADER VERT 4POS 2.54MM"}) is None
    assert _pkg("kicad:Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
                row={"src_description": "CONN HEADER SMD R/A 4POS 1MM"}) is None


def test_value_spelled_in_another_kinds_prefix_or_a_bare_number_against_the_description():
    assert _val("10k", "Components/Capacitors")                         # a capacitance is never kilo
    assert _val("100n", "Components/Resistors")                         # nor a resistance nano
    assert _val("22", "Components/Resistors", ln={"description": "Resistor 22k"})
    for value, folder, desc in [("22", "Components/Resistors", "Resistor 22 ohm"), ("103", "Components/Resistors", "RES 10K"),
                                ("4k7", "Components/Resistors", ""), ("100n", "Components/Capacitors", ""),
                                ("1M", "Components/Resistors", ""), ("10u", "Components/Inductors", "")]:
        assert _val(value, folder, ln={"description": desc}) is None, (value, desc)


def test_description_naming_another_passive_kind_holds():
    assert L.kind_clash(_item(folder="Components/Resistors"), {"description": "Capacitor 10k"})
    assert L.kind_clash(_item(folder="Components/Capacitors"), {"description": "Resistor 100nF"})
    for folder, desc in [("Components/Resistors", "Current sense resistor"), ("Components/Capacitors", "CAP CER 0.1UF"),
                         ("Components/Inductors", "Ferrite bead 600R"), ("Components/Inductors", "FIXED IND 10UH"),
                         ("Components/Resistors", "Resistor for the capacitor's bleed"),      # both named: no call
                         ("Components/ICs", "Capacitor")]:
        assert L.kind_clash(_item(folder=folder), {"description": desc}) is None, (folder, desc)


# ------------------------------------------------------------------ code review fixes

def test_tssop_width_is_the_first_size_written():
    tssop = "kicad:Package_SO:TSSOP-8_4.4x3mm_P0.65mm"
    assert not _pkg(tssop, row={"src_package": "8-TSSOP (0.173\", 4.40mm Width)"})
    assert _pkg("kicad:Package_SO:MSOP-8_3x3mm_P0.65mm", ln={"footprint": "TSSOP-8_4.4x3mm_P0.65mm"})


def test_a_text_naming_several_outlines_holds_only_when_none_is_the_land():
    assert not _pkg("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", ln={"description": "EEPROM 8-SOIC / 8-TSSOP"})
    assert _pkg("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", ln={"description": "EEPROM 8-MSOP / 8-TSSOP"})


def test_qsop_and_a_narrow_ssop_are_one_outline():
    assert not _pkg("kicad:Package_SO:QSOP-16_3.9x4.9mm_P0.635mm", row={"src_package": "16-SSOP (0.154\", 3.90mm Width)"})
    assert not _pkg("kicad:Package_SO:SSOP-16_3.9x4.9mm_P0.635mm", ln={"description": "16-QSOP"})


def test_sc74_is_the_sot23_6_outline():
    assert not _pkg("kicad:Package_TO_SOT_SMD:SOT-23-6", row={"src_package": "SC-74, SOT-457"})


def test_stand_in_makers_and_nxp_nexperia_both_ways_do_not_hold():
    for bom in ("Generic", "Any", "various"):
        assert not L.maker_clash({"manufacturer": bom, "mpn": "RC0402FR-0710KL"}, {"src_makers": "YAGEO"})
    assert not L.maker_clash({"manufacturer": "Nexperia", "mpn": "BC847"}, {"src_makers": "NXP USA Inc."})


def test_a_dip_or_dpak_footprint_on_another_outline_holds():
    assert _pkg("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", ln={"footprint": "DIP-8_W7.62mm"})
    assert _pkg("standard:Package_TO_SOT_SMD:SOT-223-3_TabPin2", ln={"footprint": "TO-252-2"})


def test_a_failing_check_holds_the_part_and_the_run_goes_on():
    notes = L._clashes(_st("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"), None, {}, {}, None)
    assert any(n and n.startswith("check: ") for n in notes)


def test_note_wording():
    assert " an 8-pin " in _pkg("kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", ln={"description": "8-TSSOP"})
    assert " an inductor" in L.kind_clash({"folder": "Capacitors", "parameters": []},
                                          {"description": "Inductor 10uH 0805"})
