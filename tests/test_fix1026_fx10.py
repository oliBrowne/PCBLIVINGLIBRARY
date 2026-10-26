"""Fixes 2026-10-26 (fx10): second review of the R-RU / R-FX4 / R-BF fixes (R-FX10-1..5, 7). Each test failed on
9b3c729 and passes with its fix."""
from tests.fixtures_lite import lite
from tests.test_fix1026_bomflow import HDR, SRR, _holds, _run


def _device_l():
    """KiCad's Device:R symbol renamed Device:L (the recorded library has no inductor symbol)."""
    from tests import e2e_support as E
    import re
    text = (E.KICAD / "kicad-symbols" / "Device.kicad_symdir" / "R.kicad_sym").read_text(encoding="utf-8")
    return re.sub(r'\(symbol "R(_\d_\d)?"', lambda m: f'(symbol "L{m.group(1) or ""}"', text)


# --- R-FX10-1: a maker / own land the index lacks is held, never built as the plain chip ---------------------------

_OWN = [("L1", "10uH", "Inductor_SMD:L_Bourns_SRR1210", "Bourns", "SRR1210A-100M"),        # a typo'd maker land
        ("L2", "22uH", "L_Bournz_SRR1210A", "Bourns", "SRR1210A-220M"),
        ("R9", "0.01", "R_Shnt_Vishay_WSK2512", "Vishay", "WSK2512R0100FEA"),             # 4-terminal Kelvin shunt
        ("L3", "1uH", "L_0603_1608Metric_typo", "Murata", "LQM18PN1R0MFRL")]               # a suffix KiCad lacks
_EXTRA = [("Inductor_SMD:L_1210_3225Metric", None), ("Inductor_SMD:L_0603_1608Metric", None),
          ("Resistor_SMD:R_2512_6332Metric", None), ("Inductor_SMD:L_Bourns_SRR1210A", SRR)]


@lite
def test_a_maker_land_the_index_lacks_is_held_not_built_as_the_plain_chip(tmp_path):
    for sources in (("kicad", "standard"), ("standard",)):
        csv = HDR + "".join(f"{d},1,{v},{fp},{mk},{mpn},part,https://example.com/a.pdf\n" for d, v, fp, mk, mpn in _OWN)
        sub = tmp_path / "_".join(sources)
        sub.mkdir()
        items = _run(sub, csv, sources, extra_fp=_EXTRA, extra_sym=[("Device:L", _device_l())])
        assert len(items) == len(_OWN), list(items)
        for it in items.values():
            src = str(it["source"].get("footprint") or "")
            assert it.get("status") != "ready", (sources, it["name"], it["footprint"], src)
            assert not src.endswith(("L_1210_3225Metric", "R_2512_6332Metric", "L_0603_1608Metric")), (sources, src)


@lite
def test_a_bare_chip_land_name_is_still_built_by_its_size(tmp_path):
    csv = HDR + ("R1,1,10k,R_0603_1608,Yageo,RC0603FR-0710KL,part,https://example.com/a.pdf\n"
                 "R2,1,1k,Resistor_SMD:R_0603_1608Metric,Yageo,RC0603FR-071KL,part,https://example.com/a.pdf\n")
    items = _run(tmp_path, csv, ("kicad", "standard"))
    for it in items.values():
        assert it.get("status") == "ready", (it["name"], _holds(it))
        assert str(it["source"]["footprint"]).endswith("R_0603_1608Metric"), it["source"]


# --- R-FX10-2: one package written two ways is one land; two packages stay two ---------------------------------------

_SAME = [("Package_QFP:LQFP-48_7x7mm_P0.5mm", "48-LQFP"), ("Package_QFP:LQFP-48_7x7mm_P0.5mm", "QFP-48"),
         ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SO-8"), ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SO8"),
         ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "8-SOIC"), ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOP-8"),
         ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SO8N"),
         ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL"),     # EasyEDA's name
         ("Package_TO_SOT_SMD:SOT-23", "TO-236"), ("Package_TO_SOT_SMD:SOT-23", "TO-236AB"),
         ("Diode_SMD:D_SMA", "DO-214AC"), ("Diode_SMD:D_SMB", "DO-214AA"),
         ("Package_DFN_QFN:QFN-20-1EP_4x4mm_P0.5mm_EP2.5x2.5mm", "QFN-20"),         # a bare leadless name: pad unsaid
         ("Package_SO:HTSSOP-16-1EP_4.4x5mm_P0.65mm_EP3.4x5mm", "HTSSOP-16"),      # HTSSOP always has the pad
         ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8"), ("Resistor_SMD:R_0603_1608Metric", "0603"),
         ("Package_TO_SOT_SMD:SOT-23", "SOT23")]
_DIFFER = [("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.29x3mm"),
           ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8_HandSoldering"),
           ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-14"), ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "TSSOP-8"),
           ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOP-8_5.3x5.3mm_P1.27mm"),    # EIAJ SOP-8: the body says another
           ("Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm", "SOIC-16"),
           ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8-1EP"),
           ("Package_QFP:LQFP-48_7x7mm_P0.5mm", "LQFP-64"), ("Package_QFP:LQFP-48_7x7mm_P0.5mm", "QFN-48"),
           ("Package_QFP:LQFP-48_7x7mm_P0.5mm", "LQFP-48_7x7mm_P0.5mm_ThermalVias"),
           ("Package_TO_SOT_SMD:SOT-23", "SOT-23-3"),            # KiCad's SOT-23-3 is the MO-178 body (stdparts.AMBIGUOUS)
           ("Package_TO_SOT_SMD:SOT-23", "SOT-23-5"), ("Diode_SMD:D_SMA", "DO-214AA"), ("Diode_SMD:D_SMA", "D_SMA_Handsoldering"),
           ("Package_TO_SOT_THT:TO-220-3_Vertical", "TO-220-3_Horizontal_TabDown"),
           ("Resistor_SMD:R_0603_1608Metric", "R_0402_1005Metric"), ("Package_TO_SOT_SMD:TSOT-23-5", "SOT-23-5")]


def test_lands_differ_reads_the_package_not_the_spelling():
    from ecadlib.altium_export import lands_differ
    for a, b in _SAME:
        assert not lands_differ(a, b), (a, b)
        assert not lands_differ(b, a), (b, a)
    for a, b in _DIFFER:
        assert lands_differ(a, b), (a, b)
        assert lands_differ(b, a), (b, a)


# --- R-FX10-3: KiCad's x-wildcard symbols take a dotted MPN again ---------------------------------------------------

def _wild_lib():
    from ecadlib.librarian import KiCadLibrary
    lib = KiCadLibrary.__new__(KiCadLibrary)
    lib.symbols = {n: ["Diode"] for n in ("1.5KExxA", "1.5KExxCA", "P6KExxA", "SMAJxxA", "SMAJxxCA", "1N47xxA")}
    lib.symbols.update({n: ["Regulator_Linear"] for n in ("AMS1117-3.3", "XC6206PxxxMR")})
    lib.footprints = {}
    lib._build_norm()
    return lib


def test_x_wildcard_symbols_take_a_decimal_point_in_the_mpn():
    lib = _wild_lib()
    for mpn, want in (("1.5KE6.8A", "Diode:1.5KExxA"), ("1.5KE9.1A", "Diode:1.5KExxA"), ("1.5KE6.8CA", "Diode:1.5KExxCA"),
                      ("1.5KE68A", "Diode:1.5KExxA"), ("SMAJ5.0A", "Diode:SMAJxxA"), ("SMAJ5.0CA", "Diode:SMAJxxCA"),
                      ("P6KE6.8A", "Diode:P6KExxA"), ("SMAJ50A", "Diode:SMAJxxA"), ("1N4733A", "Diode:1N47xxA"),
                      ("XC6206P332MR", "Regulator_Linear:XC6206PxxxMR")):
        ref, how = lib.find_symbol(mpn)
        assert ref == want and "wildcard" in how, (mpn, ref, how)


def test_a_dotted_mpn_still_keys_apart_from_the_undotted_one():
    from ecadlib.kicad_index import norm_mpn
    lib = _wild_lib()
    assert norm_mpn("SMAJ5.0A") != norm_mpn("SMAJ50A") and norm_mpn("1.5KE6.8A") != norm_mpn("1.5KE68A")
    for mpn in ("1.5KE100A", "SMAJ5.0.0A", "1.5KE6.8.1A"):              # more characters than the x slots take
        assert lib.find_symbol(mpn)[0] is None or "wildcard" not in lib.find_symbol(mpn)[1], (mpn, lib.find_symbol(mpn))
    assert lib.find_symbol("AMS1117-3.3")[0] == "Regulator_Linear:AMS1117-3.3"
    assert lib.find_symbol("AMS1117-33")[0] is None                       # 3.3 V is not 33
