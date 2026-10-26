"""The standard source never builds a land other than the one a footprint text names (free stress test 2026-10-26:
all 15,468 kicad-footprints names through stdparts.resolve; 149 names outside the checked table built a land that
differs from KiCad's footprint of that name). Three ways in:
  * a KiCad footprint name the table does not hold ('R_Array_Convex_4x0603', 'LED_RGB_1210', 'SO-8_3.9x4.9mm_P1.27mm',
    'TO-92_Wide') fell through to the chip size code or the package family inside it;
  * a size code inside a part number ('L_Bourns_SRR1210A', 'Molex ... 43045-1210', 'Speaker_CUI_CMR-1206S-67',
    'L_Coilcraft_XxL4020', 'Crystal_SMD_0603-2Pin_6.0x3.5mm', 'L_Wuerth_MAPI-2010') read as a chip size;
  * 'TO-220-3_Horizontal_TabDown': the horizontal word between underscores was not seen (\\b), so the vertical land."""
from ecadlib import stdparts as SP


def fp(text, kind=None):
    return SP.resolve([text], kind, "")["footprint"]


def test_a_kicad_name_outside_the_table_is_not_built_as_another_land():
    for text, kind in (("R_Array_Convex_4x0603", "R"), ("R_Array_Concave_2x0603", "R"), ("LED_RGB_1210", "LED"),
                       ("LED_1206_3216Metric_ReverseMount_Hole1.8x2.4mm", "LED"), ("Anaren_0805_2012Metric-6", None),
                       ("R_Shunt_Vishay_WSK2512_6332Metric_T1.19mm", "R"), ("SO-8_3.9x4.9mm_P1.27mm", "U"),
                       ("Texas_PW0020A_TSSOP-20_4.4x6.5mm_P0.65mm", "U"), ("L_Coilcraft_0805HQ_2012Metric", "L"),
                       ("TO-92_Wide", "Q"), ("TO-92_Inline_Horizontal1", "Q"), ("D_SMB_Modified", "D"),
                       ("Package_TO_SOT_THT:TO-92_Wide", "Q")):
        r = SP.resolve([text], kind, "")
        assert r["footprint"] is None and "not in the checked standard table" in r["fp_how"], (text, r)


def test_the_table_names_and_plain_package_names_still_resolve():
    assert fp("R_0603_1608Metric", "R") == "R_0603_1608Metric"
    assert fp("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "U") == "SOIC-8_3.9x4.9mm_P1.27mm"
    assert fp("TO-92", "Q") == "TO-92_Inline"                         # the package's name, not KiCad's TO-92 land
    assert fp("SO-8 3.9x4.9mm", "U") == "SOIC-8_3.9x4.9mm_P1.27mm"
    assert fp("0603", "R") == "R_0603_1608Metric" and fp("C0805", "C") == "C_0805_2012Metric"


def test_a_size_code_inside_a_part_number_is_no_chip_size():
    for text in ("SRR1210A", "Bourns SDR1806", "XxL4020", "Molex 43045-1210", "55560-0201", "CMR-1206S-67",
                 "CL535-0402-2-51", "FV1206-1", "0603USB", "1812CAN", "MAPI-2010", "WE-GF-1210", "CST2010",
                 "WSK2512", "0603-2Pin", "4x0603", "2x1206 array"):
        assert SP.chip_size(text) is None, (text, SP.chip_size(text))
    for text, want in (("0603", "0603"), ("C0805", "0805"), ("R_0603_1608Metric", "0603"), ("RES 0603 RoHS", "0603"),
                       ("0603 (1608)", "0603"), ("LED0805", "0805"), ("CAP-0402", "0402"), ("1608Metric", "0603"),
                       ("RESC1608X55N", "0603"), ("SMD 1206", "1206"), ("Fuse_1206_3216Metric", "1206"),
                       ("0603, 10k, 1%", "0603")):
        assert SP.chip_size(text) == want, (text, SP.chip_size(text), want)


def test_horizontal_or_wide_tht_packages_are_not_the_vertical_land():
    for text in ("TO-220-3_Horizontal_TabDown", "Heatsink_Aavid-TV5G_TO220_Horizontal", "TO-92 wide",
                 "TO-92 staggered", "TO-92_W4.0mm"):
        assert fp(text, "Q") is None, (text, SP.resolve([text], "Q", ""))
    assert fp("TO-220", "Q") == "TO-220-3_Vertical" and fp("TO-92", "Q") == "TO-92_Inline"


def test_free_text_footprint_fields_with_a_part_numbers_size_or_a_multi_pad_word_build_no_chip():
    """The same names typed with spaces, as a BOM's footprint column has them: a size code glued into a part number
    anywhere in a footprint field means the field names that part's own land; words for multi-pad parts or other
    lands; RF connectors whose names start with a diode package's (SMA, SMB)."""
    for text, kind in (("L Coilcraft 0805HQ 2012Metric", "L"), ("R Shunt Vishay WSK2512 6332Metric", "R"),
                       ("Anaren 0805 2012Metric-6", "C"), ("LED RGB 1210", "LED"), ("R Array Convex 4x0603", "R"),
                       ("L Taiyo-Yuden BK Array 1206 3216Metric", "L"), ("MouseBite-Slot-Jumper 0201", "R"),
                       ("LED 1206 3216Metric ReverseMount", "LED"), ("L Abracon ASPIAIG-F4020", "L"),
                       ("SMA Amphenol 132134 Vertical", "D"), ("SMB Jack Vertical", "D"), ("D SMB Modified", "D")):
        assert fp(text, kind) is None, (text, SP.resolve([text], kind, ""))
    # a description keeps its own size next to an MPN that carries it (only footprint fields are strict)
    assert SP.resolve(["0603"], "R", "", "RES SMD 10K 1% 0603 CRCW060310K0FKEA")["footprint"] == "R_0603_1608Metric"
    assert SP.resolve([], "R", "", "RES SMD 10K 1% 0603 CRCW060310K0FKEA")["footprint"] in ("R_0603_1608Metric", None)
    assert SP.chip_size("SRR1210A, 1210") == "1210" and SP.chip_size("SRR1210A, 1210", strict=True) is None
    assert fp("SMA", "D") == "D_SMA" and fp("DO-214AC", "D") == "D_SMA" and fp("LED 0805", "LED") == "LED_0805_2012Metric"
    assert fp("0603 jumper", "R") == "R_0603_1608Metric"                # a 0 ohm link is a two-pad chip


def test_a_value_after_the_size_code_is_no_part_number():
    """Self-review of #195: 'RES-0603-10k', 'C0805-10uF' (LCSC/JLC-style names) give the code then the value; a dash
    and a value is not a part number's suffix ('FV1206-1', '0603-2Pin' still are)."""
    for text, kind, want in (("RES-0603-10k", "R", "R_0603_1608Metric"), ("CAP-0402-100nF", "C", "C_0402_1005Metric"),
                             ("0603-1%", "R", "R_0603_1608Metric"), ("R0603-10K", "R", "R_0603_1608Metric"),
                             ("C0805-10uF", "C", "C_0805_2012Metric"), ("R0603-4R7", "R", "R_0603_1608Metric"),
                             ("L0805-2.2uH", "L", "L_0805_2012Metric"), ("C0603-0.1uF", "C", "C_0603_1608Metric")):
        assert fp(text, kind) == want, (text, SP.resolve([text], kind, ""))
    for text in ("FV1206-1", "0603-2Pin", "0805-12"):
        assert SP.chip_size(text) is None, text
