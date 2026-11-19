"""Name-only KiCad matches that ecadlib can check itself, so they release with no person's look (2026-11-17,
1000-part pass 1 on main 534cb9e: 126 parts held only because a KiCad symbol or footprint was matched by name).
Plain functions: python3 -m tests.run_all test_name_match_autocheck"""
from ecadlib import librarian as L, sexpr


def _chain(name, pins, footprint="", filters=""):
    """A KiCad symbol (its chain, as K.load_symbol gives it) with named pins [(number, name)]."""
    txt = "\n".join(f'(pin passive line (at -7.62 {2.54 * i:g} 0) (length 2.54) (name "{nm}") (number "{n}"))'
                    for i, (n, nm) in enumerate(pins))
    text = f"""(kicad_symbol_lib (version 20241209)
  (symbol "{name}" (in_bom yes) (on_board yes)
    (property "Reference" "D" (at 0 0 0))
    (property "Value" "{name}" (at 0 0 0))
    (property "Footprint" "{footprint}" (at 0 0 0))
    (property "ki_fp_filters" "{filters}" (at 0 0 0))
    (symbol "{name}_1_1" {txt})))"""
    return [s for s in sexpr.find(sexpr.parse(text), "symbol") if s[1] == name]


DIODE = [("1", "K"), ("2", "A")]
FET = [("1", "G"), ("2", "S"), ("3", "D")]
BJT = [("1", "B"), ("2", "E"), ("3", "C")]
PAIR = [("1", "A"), ("2", "K"), ("3", "K")]


class _Run(L.Run):
    """Only what variant_agrees needs; the datasheet PDF is never there (the cloud reaches no datasheet host)."""
    def __init__(self):
        self.pdf_asked = []

    def datasheet_pdf(self, item, ln, row, st, mpn):
        self.pdf_asked.append(mpn)
        return None, None

    def kicad(self):                                  # (no KiCad library here: no land names to compare pitches with)
        k = L.KiCadLibrary.__new__(L.KiCadLibrary)
        k.footprints = {}
        return k


def _variant(mpn, sym_ref, pins, hint, case, several="", src_mpn=None, bom_fp="", filters=""):
    run = _Run()
    st = {"hint_fp": hint, "tried": []}
    item = {"footprint": {"kicad": ""}, "description": ""}
    ln = {"footprint": bom_fp, "description": "", "value": ""}
    row = {"src_package": case, "src_package_from": "digikey", "src_several": several,
           "src_mpn": mpn if src_mpn is None else src_mpn}
    chain = _chain(sym_ref.split(":")[-1], pins, hint, filters)
    ok = run.variant_agrees(st, item, ln, row, None, mpn, sym_ref, chain)
    return ok, st["tried"], run.pdf_asked


SOT23 = "TO-236-3, SC-59, SOT-23-3"
SOT23_LAND = "Package_TO_SOT_SMD:SOT-23"


def test_rest_after_a_kicad_name_keeps_the_mpn_as_written():
    R = L.rest_after_name
    assert R("BAT54S-7-F", "BAT54S") == "-7-F"
    assert R("BC847C,215", "BC847") == "C"                # NXP's packing code ',215' is not part of the variant
    assert R("BC817-40,215", "BC817") == "-40"
    assert R("TIP120G", "TIP120") == "G"
    assert R("1N4148W-E3-08", "1N4148W") == "-E3-08"
    assert R("LM358DR", "LM358") == "DR"
    assert R("LM358", "LM358") == ""
    assert R("XYZ123", "LM358") is None                   # not that name at all


def test_ordering_codes_of_discretes_that_change_neither_die_nor_pins():
    O = L.discrete_ordering_code
    for rest, lib in (("-7-F", "Diode"), ("-7", "Transistor_FET"), ("-13-F", "Transistor_FET"), ("-F", "Diode"),
                      ("G", "Transistor_BJT"), ("BU", "Transistor_BJT"), ("TA", "Transistor_FET"),
                      ("T3G", "Diode"), ("T1G", "Diode"), ("-E3/54", "Diode"), ("-E3-08", "Diode"), ("-GS08", "Diode"),
                      ("C", "Transistor_BJT"), ("-40", "Transistor_BJT"), ("B", "Transistor_BJT"), ("A", "Transistor_BJT"),
                      ("CG", "Transistor_BJT"), ("-25-7-F", "Transistor_BJT")):
        assert O(rest, lib), (rest, lib)
    for rest, lib in (("", "Diode"),                    # nothing to check: not this rule's
                      ("S", "Diode"), ("C", "Diode"), ("A", "Diode"),   # BAT54 + S / C / A: other pinouts (pairs)
                      ("W", "Transistor_BJT"), ("T", "Transistor_BJT"), # BC847W: SC-70, MMBT3904T: SOT-523
                      ("BK", "Transistor_FET"),                         # BSS138BK: another Nexperia part
                      ("K-7", "Transistor_FET"),                        # 2N7002K: the ESD part
                      ("BS", "Transistor_BJT"), ("BW", "Transistor_BJT"), ("C-7-F", "Diode"),
                      ("-7-F", "Amplifier_Operational"), ("DR", "Amplifier_Operational"), ("G", "Regulator_Linear"),
                      ("-7-F", "Power_Protection"), ("1G", "Diode"), ("LT1G", "Transistor_BJT"), ("-E3-08X", "Diode"),
                      ("-08", "Diode"), ("GG", "Transistor_BJT")):
        assert not O(rest, lib), (rest, lib)


def test_a_reel_code_on_a_sot23_mosfet_is_released_on_its_case_without_the_pdf():
    ok, tried, pdf = _variant("BSS138-7-F", "Transistor_FET:BSS138", FET, SOT23_LAND, SOT23)
    assert ok and not pdf, tried
    assert any("ordering code" in t and "-7-F" in t for t in tried), tried


def test_a_gain_group_and_packing_code_on_a_bjt_is_released():
    ok, tried, _ = _variant("BC847C,215", "Transistor_BJT:BC847", BJT, SOT23_LAND, SOT23)
    assert ok, tried
    ok, _, _ = _variant("2N3904BU", "Transistor_BJT:2N3904", BJT, "Package_TO_SOT_THT:TO-92_Inline",
                        "TO-226-3, TO-92-3 (TO-226AA)")
    assert ok


def test_the_case_must_still_name_the_symbols_land():
    for case in ("SC-70, SOT-323", "TO-236-3, SOT-323", "", "SOT-23-6"):
        ok, _, _ = _variant("BSS138-7-F", "Transistor_FET:BSS138", FET, SOT23_LAND, case)
        assert not ok, case
    ok, _, _ = _variant("BSS138-7-F", "Transistor_FET:BSS138", FET, SOT23_LAND, SOT23, bom_fp="SOT-323_SC-70")
    assert not ok                                       # the BOM names another package
    ok, _, _ = _variant("BSS138-7-F", "Transistor_FET:BSS138", FET, SOT23_LAND, SOT23, several="yes")
    assert not ok                                       # several makers: a clone's case is not this part's
    ok, _, _ = _variant("BSS138-7-F", "Transistor_FET:BSS138", FET, SOT23_LAND, SOT23, src_mpn="BSS138")
    assert not ok                                       # the search found another MPN


def test_an_unknown_suffix_still_waits_for_the_pin_table():
    ok, tried, pdf = _variant("BSS138BK,215", "Transistor_FET:BSS138", FET, SOT23_LAND, SOT23)
    assert not ok and pdf == ["BSS138BK,215"], tried     # BK: the free pin-table check is tried, then a person
    ok, _, _ = _variant("BAT54-7-F", "Diode:BAT54", PAIR, SOT23_LAND, SOT23)
    assert ok                                            # a reel code on the KiCad name itself (BAT54S-7-F alike)


def test_a_two_pin_diode_on_a_diode_land_is_released_on_its_case():
    ok, tried, pdf = _variant("1N4148W-7-F", "Diode:1N4148W", DIODE, "Diode_SMD:D_SOD-123", "SOD-123")
    assert ok and not pdf, tried
    ok, tried, _ = _variant("MBRA340T3G", "Diode:MBRA340", DIODE, "Diode_SMD:D_SMA", "DO-214AC, SMA")
    assert ok, tried
    ok, tried, _ = _variant("SS14XYZ", "Diode:SS14", DIODE, "Diode_SMD:D_SMA", "DO-214AC, SMA")
    assert ok, tried                                     # any suffix: two pins, cathode is pad 1 on every diode land
    ok, _, _ = _variant("1N4148W-7-F", "Diode:1N4148W", DIODE, "Diode_SMD:D_SOD-123", "SOD-323")
    assert not ok                                        # another case
    ok, _, _ = _variant("XYZ1-A", "Diode:XYZ1", [("1", "A"), ("2", "K")], "Diode_SMD:D_SOD-123", "SOD-123")
    assert not ok                                        # anode on pin 1: not the diode land's numbering
    ok, _, _ = _variant("LEDX-7", "LED:LEDX", DIODE, "LED_SMD:LED_0603_1608Metric", "0603 (1608 Metric)")
    assert not ok                                        # not a diode land


def test_a_symbol_without_a_footprint_uses_its_filters_for_the_free_pin_table_check():
    pins = [(str(i), f"P{i}") for i in range(1, 9)]
    ok, tried, pdf = _variant("LM358DR", "Amplifier_Operational:LM358", pins, "", '8-SOIC (0.154", 3.90mm Width)',
                              filters="SOIC*3.9x4.9mm*P1.27mm* DIP*W7.62mm*")
    assert not ok and pdf == ["LM358DR"], tried          # IC: case agrees with KiCad's filters, the pin table decides
    # KiCad's filters leave the SOIC out (74HC14: DIP only), or there are none: since 2026-11-19 the pin table still
    # decides (test_review_holds_1119); with no PDF the part stays held
    ok, tried, pdf = _variant("LM358DR", "Amplifier_Operational:LM358", pins, "", '8-SOIC (0.154", 3.90mm Width)',
                              filters="DIP*W7.62mm*")
    assert not ok and pdf == ["LM358DR"], tried
    ok, _, pdf = _variant("LM358DR", "Amplifier_Operational:LM358", pins, "", '8-SOIC (0.154", 3.90mm Width)')
    assert not ok and pdf == ["LM358DR"]
    ok, _, pdf = _variant("LM358DR", "Amplifier_Operational:LM358", pins, "", "")
    assert not ok and not pdf                            # no case: no land to check it on


def _lib(footprints):
    k = L.KiCadLibrary.__new__(L.KiCadLibrary)
    k.footprints = {n: [lib] for lib, n in footprints}
    return k


def test_a_footprint_named_after_the_whole_mpn_is_that_parts_land():
    E = L.land_named_after
    assert E("Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", "B2B-XH-A(LF)(SN)", None)
    assert E("Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", "SM04B-SRSS-TB(LF)(SN)", None)
    assert E("Connector_Molex:Molex_PicoBlade_53261-0471_1x04-1MP_P1.25mm_Horizontal", "0532610471", None)
    assert E("TerminalBlock_TE-Connectivity:TerminalBlock_TE_1-282834-0_1x10_P2.54mm_Horizontal", "1-282834-0", None)
    assert E("Connector_USB:USB_Micro-B_Amphenol_10118194-0001LF_Horizontal", "10118194-0001LF", None)
    assert E("Connector_FFC-FPC:Hirose_FH12-24S-0.5SH_1x24-1MP_P0.50mm_Horizontal", "FH12-24S-0.5SH(55)", None)
    assert E("Connector_USB:USB_Micro-B_Wuerth_629105150521", "629105150521", None)
    # a series name, an ordering code after it, a wildcard, or the name only inside a longer one: still a person's look
    assert not E("Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", "B2B-PH-K-S(LF)(SN)", None)
    assert not E("TerminalBlock_TE-Connectivity:TerminalBlock_TE_1-282834-2_1x12_P2.54mm_Horizontal", "282834-2", None)
    assert E("Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", "USB4105-GF-A", None)  # 11-19
    assert not E("Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", "USB4105-12-A", None)
    assert not E("Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", "USB4105-GF-B", None)
    assert not E("Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", "USB4105-GFX-A", None)
    assert E("Connector_USB:USB_Micro-B_Molex-105017-0001", "1050170001", None)   # (maker name first: 2026-11-19)
    assert not E("Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical", "B2B-XH-AM(LF)(SN)", None)
    assert not E("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8", None)


def test_an_inductor_or_crystal_series_land_with_only_a_value_after_the_series():
    E = L.land_named_after
    assert E("Inductor_SMD:L_Bourns_SRR1260", "SRR1260-100M", "L")
    assert E("Inductor_SMD:L_Bourns-SRN4018", "SRN4018-4R7M", "L")
    assert E("Inductor_SMD:L_Coilcraft_LPS4018", "LPS4018-103MRB", "L")
    X = "Crystal:Crystal_SMD_Abracon_ABM3-2Pin_5.0x3.2mm"
    assert E(X, "ABM3-16.000MHZ-B2-T", None, case="2-SMD, No Lead")
    assert not E(X, "ABM3-16.000MHZ-B2-T", None)                              # no case: its pad count unconfirmed
    assert not E(X, "ABM3-16.000MHZ-B2-T", None, case="4-SMD, No Lead")
    assert not E(X, "ABM3B-16.000MHZ-B2-T", None, case="2-SMD, No Lead")      # ABM3B: another series
    assert not E(X, "ABM3-16.000MHZ-B2-T", "L", case="2-SMD, No Lead")        # an inductor line on a crystal land
    assert not E("Inductor_SMD:L_Bourns_SRR1260", "SRR1260-100M", None)        # not an inductor line
    assert not E("Inductor_SMD:L_Bourns_SRR1260", "SRR1260A-100M", "L")        # another series
    assert not E("Inductor_SMD:L_Bourns_SRR1260", "SRR1260-XYZ", "L")          # not a value code
