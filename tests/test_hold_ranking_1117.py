"""Hold-ranking fixes (2026-11-17, 1000-part replay on main e9b3391): KiCad's Coilcraft inductor lands named with an
upper-case value wildcard (L_Coilcraft_XAL4020-XXX), which the series finder never matched, so XAL4020-222MEB,
MSS1048-103MLB ... were held with "no footprint" although KiCad has their series land.
Plain functions: python3 -m tests.run_all test_hold_ranking_1117"""
from ecadlib import librarian as L


def _lib(footprints=()):
    k = L.KiCadLibrary.__new__(L.KiCadLibrary)
    k.footprints = {n: [lib] for lib, n in footprints}
    k.symbols, k._norm, k._wild = {}, {}, []
    return k


COILCRAFT = [("Inductor_SMD", "L_Coilcraft_XAL4020-XXX"), ("Inductor_SMD", "L_Coilcraft_XAL4030-XXX"),
             ("Inductor_SMD", "L_Coilcraft_MSS1048-XXX"), ("Inductor_SMD", "L_Coilcraft_MSS1048T-XXX"),
             ("Inductor_SMD", "L_Coilcraft_XAL1010-XXX"), ("Button_Switch_THT", "SW_SPST_REED_CT05-XXXX-G1")]


def test_an_upper_case_value_wildcard_names_the_inductor_series():
    S = _lib(COILCRAFT).footprint_by_series
    assert S("XAL4020-222MEB", stem_ok=True) == "Inductor_SMD:L_Coilcraft_XAL4020-XXX"
    assert S("XAL4030-472MEC", stem_ok=True) == "Inductor_SMD:L_Coilcraft_XAL4030-XXX"
    assert S("MSS1048-103MLB", stem_ok=True) == "Inductor_SMD:L_Coilcraft_MSS1048-XXX"
    assert S("MSS1048T-103MLB", stem_ok=True) == "Inductor_SMD:L_Coilcraft_MSS1048T-XXX"
    assert S("XAL4020-222MEB") is None                       # not an inductor line: no series guess at all
    assert S("XAL40-222MEB", stem_ok=True) is None and S("XAL4050-222MEB", stem_ok=True) is None


def test_the_wildcard_land_is_the_parts_own_only_with_a_value_code_after_the_series():
    E = L.land_named_after
    assert E("Inductor_SMD:L_Coilcraft_XAL4020-XXX", "XAL4020-222MEB", "L")
    assert E("Inductor_SMD:L_Coilcraft_XAL6060-XXX", "XAL6060-223MEB", "L")
    assert E("Inductor_SMD:L_Coilcraft_MSS1048-XXX", "MSS1048-103MLB", "L")
    assert not E("Inductor_SMD:L_Coilcraft_MSS1048-XXX", "MSS1048T-103MLB", "L")   # MSS1048T: another series
    assert not E("Inductor_SMD:L_Coilcraft_XAL4020-XXX", "XAL4020-222MEB", None)  # not an inductor line
    assert not E("Inductor_SMD:L_Coilcraft_XAL4020-XXX", "XAL4020-ABC", "L")      # not a value code
    assert not E("Button_Switch_THT:SW_SPST_REED_CT05-XXXX-G1", "CT05-1234-G1", None)


# ---------------------------------------------------------------- DigiKey's 'Supplier Device Package'
def test_the_device_package_settles_a_case_that_names_only_the_family():
    C = L.case_or_device_land
    assert C("32-TQFP", "32-TQFP (7x7)") == "TQFP-32_7x7mm_P0.8mm"
    assert C("44-TQFP", "44-TQFP (10x10)") == "TQFP-44_10x10mm_P0.8mm"
    assert C("32-TQFP", "") is None and C("32-TQFP", "32-TQFP") is None
    assert C("SOT-23-6 Thin, TSOT-23-6", "TSOT-23-6") == "TSOT-23-6"      # the group lists the device's land
    assert C("8-SOIC (0.154\", 3.90mm Width)", "8-SO") == "SOIC-8_3.9x4.9mm_P1.27mm"
    assert C("24-VFQFN Exposed Pad", "24-QFN (4x4)") is None               # body only: the exposed pad is unknown
    assert C("8-SOIC (0.209\", 5.30mm Width)", "8-SOIC") is None           # two KiCad lands fit 5.30 mm


def test_a_device_package_naming_sc59_under_digikeys_sot23_group_is_not_sot23():
    C, G = L.case_or_device_land, "TO-236-3, SC-59, SOT-23-3"
    assert C(G, "") == C(G, "SOT-23-3 (TO-236)") == C(G, "TO-236AB") == C(G, "PG-SOT23") == "SOT-23"
    assert C(G, "SC-59") is None and C(G, "SC-59-3") is None


def test_a_dip_case_with_its_row_spacing_names_kicads_dip_land():
    assert L.case_land("28-DIP (0.300\", 7.62mm)") == "DIP-28_W7.62mm"
    assert L.case_land("8-DIP (0.300\", 7.62mm)") == "DIP-8_W7.62mm"
    assert L.case_land("40-DIP (0.600\", 15.24mm)") == "DIP-40_W15.24mm"
    assert L.case_land("28-PDIP") is None                                  # 300 or 600 mil: not said
    assert L.case_land("8-DIP (0.300\", 10.16mm)") is None                 # the two widths disagree


def test_digikeys_device_package_reaches_the_triage_row():
    from ecadlib import part_lookup as PL, providers as P, bom_triage as T
    from ecadlib.kicad_index import KiCadIndex
    specs = PL.specs_from_pairs([("Package / Case", "32-TQFP"), ("Supplier Device Package", "32-TQFP (7x7)")])
    assert specs["package_text"] == "32-TQFP" and specs["device_package"] == "32-TQFP (7x7)", specs

    class Src:
        name = "digikey"

        def lookup(self, work):
            r = P.SourceResult("digikey", found=True, mpn="ATMEGA328P-AU", package="32-TQFP")
            r.extra = {"device_package": "32-TQFP (7x7)", "description": "IC MCU 8BIT 32KB FLASH 32TQFP"}
            return r
    row = T.triage([{"designator": "U1", "mpn": "ATMEGA328P-AU"}], KiCadIndex([]), [Src()], None)[0]
    assert row["src_package"] == "32-TQFP" and row["src_package_device"] == "32-TQFP (7x7)", row
