"""KiCad symbols the name matcher missed (hold audit 2026-11-18): about 40 ICs of the 1000-part run were held with
"no symbol named like ..." although KiCad draws them.
Run:  python3 -m tests.test_symbol_names_1118

Every near match found here says "(check ...)": the librarian releases it only when the part search's case is the
symbol's land and the datasheet's pin table agrees (variant_agrees), else a person looks."""
from ecadlib import librarian as L


def _lib(symbols):
    k = L.KiCadLibrary.__new__(L.KiCadLibrary)
    k.symbols = {n: [lib] for lib, n in symbols}
    k.footprints = {}
    k._build_norm()
    return k


LIB = _lib([("Amplifier_Operational", "TL072"), ("Amplifier_Operational", "MCP6002-xSN"), ("Amplifier_Operational", "MCP6002-xMS"),
            ("MCU_Microchip_PIC18", "PIC18F46K22-xPT"), ("Amplifier_Operational", "OPA2333xxD"),
            ("Amplifier_Operational", "OPA2333xxDGK"), ("Amplifier_Current", "INA181"), ("Interface_UART", "MAX3232"),
            ("Interface_USB", "CP2104"), ("74xx", "74HC595"), ("74xx", "74HC14"), ("Regulator_Switching", "TPS5430DDA"),
            ("Regulator_Linear", "LM78"), ("Sensor_Current", "ACS712xLCTR-05B"), ("Transistor_BJT", "BC847"),
            ("Regulator_Switching", "MC33063AD"), ("Switch", "SW_DIP_x04"), ("Memory_EEPROM", "M24C02-WMN"), ("Regulator_Linear", "LM1117")])
F = LIB.find_symbol


def test_a_wildcard_after_a_dash_is_a_wildcard():
    assert F("MCP6002-I/SN") == ("Amplifier_Operational:MCP6002-xSN", "name with KiCad x wildcards")
    assert F("PIC18F46K22-I/PT") == ("MCU_Microchip_PIC18:PIC18F46K22-xPT", "name with KiCad x wildcards")
    assert F("MCP6002-I/P") == (None, None)                               # no symbol for that package
    assert F("SW_DIP_E04")[0] is None                                     # 'SW_DIP_x04' is a count, not an MPN wildcard


def test_a_base_name_and_a_longer_ordering_code_is_a_near_match_to_check():
    for mpn, ref in (("TL072CDR", "Amplifier_Operational:TL072"), ("MAX3232ESE+", "Interface_UART:MAX3232"),
                     ("INA181A1IDBVR", "Amplifier_Current:INA181"), ("CP2104-F03-GMR", "Interface_USB:CP2104"),
                     ("BC847CLT1G", "Transistor_BJT:BC847"), ("M24C02-WMN6TP", "Memory_EEPROM:M24C02-WMN"), ("MC33063ADR2G", "Regulator_Switching:MC33063AD")):
        got, how = F(mpn)
        assert got == ref and "(check the " in how, (mpn, got, how)


def test_a_number_that_goes_on_is_another_part():
    assert F("TL0721CDR") == (None, None)            # TL072 + '1CDR': the number goes on
    assert F("LM7805CT") == (None, None)             # a four-character name is too short to take a long code
    assert F("TL072ABCDEFGH") == (None, None)        # eight characters: not an ordering code
    assert F("CP2104-MINI-1-N4") == (None, None)     # two more dashes: a module or kit named after the chip


def test_a_wildcard_name_and_a_reel_or_ordering_code():
    got, how = F("OPA2333AIDR")
    assert got == "Amplifier_Operational:OPA2333xxD" and "x wildcards" in how and "packing suffix 'R'" in how \
        and "check" not in how, (got, how)                                 # TI's R after the package letter: a reel
    got, how = F("ACS712ELCTR-05B-T")
    assert got == "Sensor_Current:ACS712xLCTR-05B" and "(check the variant)" in how, (got, how)
    got, how = F("MCP6002T-I/SN")                                          # Microchip's T (reel) before the dash
    assert got == "Amplifier_Operational:MCP6002-xSN" and "x wildcards" in how, (got, how)


def test_a_logic_makers_prefix_is_left_off():
    for mpn in ("SN74HC595DR", "MC74HC595ADR2G", "CD74HC14M96"):
        got, how = F(mpn)
        assert got in ("74xx:74HC595", "74xx:74HC14") and "(check the variant)" in how and "prefix" in how, (mpn, got, how)
    assert F("SN74HC595")[1] and "(check the variant)" in F("SN74HC595")[1]      # never an 'exact name'
    assert F("SNX74HC595DR") == (None, None)


def test_what_matched_before_still_matches_the_same_way():
    assert F("TL072") == ("Amplifier_Operational:TL072", "exact name")
    assert F("TPS5430DDAR") == ("Regulator_Switching:TPS5430DDA", "name + packing suffix 'R' (tape and reel or lead-free, same part)")
    got, how = F("TL072-Q1")
    assert got == "Amplifier_Operational:TL072" and "ordering suffix 'Q1' (check the variant)" in how, how


# ---------------------------------------------------------------- KiCad's generic symbol for its own land
def test_the_generic_symbols_named_like_a_usb_land():
    names = ["USB_A", "USB_A_Stacked", "USB_C_Plug", "USB_C_Receptacle", "USB_C_Receptacle_USB2.0_16P",
             "USB_C_Receptacle_USB2.0_14P", "USB_C_Receptacle_PowerOnly_6P", "USB_B_Micro", "Micro_SD_Card", "HDMI_A"]
    N = L.land_symbol_names
    assert N("USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", names) == \
        ["USB_C_Receptacle", "USB_C_Receptacle_PowerOnly_6P", "USB_C_Receptacle_USB2.0_14P", "USB_C_Receptacle_USB2.0_16P"]
    assert N("USB_A_Molex_67643_Horizontal", names) == ["USB_A", "USB_A_Stacked"]
    assert N("USB_Micro-B_Amphenol_10103594-0001LF_Horizontal", names) == []      # KiCad names it USB_B_Micro: no guess
    assert N("USB", names) == [] and N("", names) == []


def test_a_land_symbol_needs_the_filter_and_the_exact_pads():
    P = L.land_symbol_fits
    pads = {"A1", "A4", "A5", "A6", "A7", "A8", "A9", "A12", "B1", "B4", "B5", "B6", "B7", "B8", "B9", "B12", "S1"}
    land = "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"
    assert P("USB*C*Receptacle*", pads, pads, land)
    assert not P("USB*C*Receptacle*", pads - {"S1"}, pads, land)          # the 14P / power-only symbol: other pins
    assert not P("USB*C*Plug*", pads, pads, land) and not P("", pads, pads, land)


def test_one_contact_is_said_by_the_description_or_by_kicads_land_name():
    C = L.one_contact
    assert C("SWITCH TACTILE SPST-NO 0.05A 12V", "SW_Push_1P1T_NO_CK_KMR2")
    assert C("", "SW_SPST_B3U-1000P") and C(None, "SW_SPST_PTS645")
    assert not C("", "SW_Push_1P1T_NO_CK_KMR2") and not C("SWITCH SLIDE SPDT 300MA 6V", "SW_SPDT_PCM12")
    assert not C("SWITCH SLIDE SPDT", "SW_SPST_X")       # (a DIP array of SPSTs has more than pads 1 and 2: land_symbol)


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            print("ok", k)
