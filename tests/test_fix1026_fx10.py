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


# --- R-FX10-4: an unquoted '3,3V' / '1,5A' / '0,5W' value is one value, a '100,50V' row is still a shifted row -----

@lite
def test_decimal_comma_values_in_volts_amps_watts_are_one_value(tmp_path):
    from tests.test_fix1026_bomflow import _read
    lines = _read(tmp_path, "Designator,Qty,Value,Footprint,Manufacturer,MPN,Description\n"
                            "D1,1,3,3V,SOD-123,Nexperia,BZT52-C3V3,Zener\n"
                            "D2,1,5,1V,SOD-123,Nexperia,BZT52-C5V1,Zener\n"
                            "F1,1,1,5A,1206,Littelfuse,0466001.NR,Fuse\n"
                            "R4,1,0,5W,R_2512,Vishay,CRCW25121R00FKEG,Res\n"
                            "R5,1,0,25W,R_1206,Vishay,CRCW12061R00FKEA,Res\n")
    got = {ln["designator"]: ln for ln in lines}
    for des, v, mpn in (("D1", "3,3V", "BZT52-C3V3"), ("D2", "5,1V", "BZT52-C5V1"), ("F1", "1,5A", "0466001.NR"),
                        ("R4", "0,5W", "CRCW25121R00FKEG"), ("R5", "0,25W", "CRCW12061R00FKEA")):
        ln = got[des]
        assert ln.get("value") == v and ln.get("mpn") == mpn and not ln.get("bom_hold"), ln


@lite
def test_a_rating_after_a_whole_value_is_still_a_shifted_row(tmp_path):
    from tests.test_fix1026_bomflow import _read
    lines = _read(tmp_path, "Designator,Qty,Value,Description,Footprint,Manufacturer,MPN\n"
                            "C1,1,100,50V, X7R,C_0603_1608Metric,Samsung,CL10B101KB8NNNC\n"
                            "C2,1,10,16V, X5R,C_0603_1608Metric,Samsung,CL10A106KO8NNNC\n"
                            "C3,1,1,16V, X5R,C_0603_1608Metric,Samsung,CL10A105KO8NNNC\n"
                            "R1,1,10,5W, wirewound,R_2512,Vishay,WSR51R000FEA\n")
    for ln in lines:
        assert ln.get("bom_hold") and "," not in str(ln.get("value")), ln


# --- R-FX10-5: a passive alternate whose MPN codes the inherited chip size keeps the land ---------------------------

def test_a_passive_alternate_of_the_inherited_chip_size_is_not_held():
    from ecadlib import librarian as L
    fp = {"footprint": {"source": "kicad:Resistor_SMD:R_0603_1608Metric", "file": "x"}, "kind": "R"}
    for land, mpn in (("R_0603_1608Metric", "RC0603FR-0710KL"), ("Resistor_SMD:R_0603_1608Metric", "ERJ-3EKF1002V"),
                      ("0603", "CRCW060310K0FKEA")):
        v, r = L.alternate_footprint({"altium_mpn_source": "Alternate", "mpn": mpn, "footprint": land}, fp)
        assert not v and r and "0603" in r, (land, mpn, v, r)
    cap = {"footprint": {"source": "kicad:Capacitor_SMD:C_0402_1005Metric", "file": "x"}, "kind": "C"}
    v, r = L.alternate_footprint({"altium_mpn_source": "Alternate", "mpn": "GRM155R71C104KA88D",
                                  "footprint": "C_0402_1005Metric"}, cap)
    assert not v and r, (v, r)


def test_a_passive_alternate_of_another_size_or_kind_or_land_is_still_held():
    from ecadlib import librarian as L
    fp = {"footprint": {"source": "kicad:Resistor_SMD:R_0603_1608Metric", "file": "x"}, "kind": "R"}
    for land, mpn in (("R_0603_1608Metric", "RC0402FR-0710KL"),          # another size
                      ("R_0603_1608Metric", "GRM188R71H104KA93D"),       # a capacitor's MPN on a resistor land
                      ("R_0603_1608Metric_Pad0.98x0.95mm_HandSolder", "RC0603FR-0710KL"),   # another land
                      ("R_Array_Convex_4x0603", "RC0603FR-0710KL"),
                      ("R_0603_1608Metric", "ACME-10K-X")):              # no size in the MPN: nothing confirms it
        v, r = L.alternate_footprint({"altium_mpn_source": "Alternate", "mpn": mpn, "footprint": land}, fp)
        assert v and not r, (land, mpn, v, r)


# --- R-FX10-7 / R-BF-9: two parts never get names that differ only by case (10m / 10M) ------------------------------

def test_milli_and_mega_parts_get_names_apart_in_any_case():
    from ecadlib import altium_export as AX, rules as RU
    lines = [{"designator": "R1", "value": "10m", "footprint": "R_2512_6332Metric", "description": "Shunt 10 milliohm"},
             {"designator": "R2", "value": "10M", "footprint": "R_2512_6332Metric", "description": "Resistor 10 megaohm"},
             {"designator": "R3", "value": "2.2m", "footprint": "R_0603_1608Metric", "description": "Shunt"},
             {"designator": "R4", "value": "2.2M", "footprint": "R_0603_1608Metric", "description": "Resistor"},
             {"designator": "R5", "value": "10M", "footprint": "R_2512_6332Metric", "description": "Resistor 10 megaohm"}]
    for rules in (None, RU.load({"naming": {"case": "upper"}}), RU.load({"naming": {"case": "lower"}})):
        names = [n for df in AX.to_rows(lines, rules=rules).values() for n in df["Part Number"]]
        assert len(names) == 4, (rules and rules["naming"]["case"], names)       # R5 is R2's part
        assert len({n.casefold() for n in names}) == 4, (rules and rules["naming"]["case"], names)
