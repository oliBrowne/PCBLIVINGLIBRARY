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
