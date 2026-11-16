"""Fixes from the 2026-11-14 held-out 30-part test on the PC (RESULT_SET2.md).
Plain functions: python3 -m tests.run_all test_set2_fixes"""
from ecadlib import bom_triage as BT, providers as P, stdparts as SP


class _Prov:
    name = "digikey"

    def __init__(self, res):
        self.res = res

    def lookup(self, work):
        return self.res


def test_concave_chip_case_of_a_two_terminal_part_is_its_plain_land():
    """DigiKey writes Littelfuse 1206L050YR's case as '1206 (3216 Metric), Concave' (its end caps' shape): a PTC fuse
    has one 1206 land; an array that says 'Concave' still has many pads and stays unused."""
    C = SP.case_text
    assert C("1206 (3216 Metric), Concave", "PTC RESET FUSE 6V 500MA 1206") == "1206 (3216 Metric)"
    assert C("1206 (3216 Metric), Concave") == ""                       # no description: nothing says what it is
    for desc in ("RES ARRAY 4 RES 10K OHM 1206", "RES NETWORK 4 RES 10K", "CAP ARRAY 4 x 0.1UF 0612", "RES ARR 2 RES 0606",
                 "Resistor Arrays", "4 Resistors 10k 1206", "RES 10K 4R CONCAVE", "RES 10K OHM 1% 1206", "PTC FUSE ARRAY"):
        assert C("1206 (3216 Metric), Concave", desc) == "", desc
    for t in ("1206 (3216 Metric), Concave, Reverse Geometry", "1206 (3216 Metric), Concave Pad", "1206, Concave"):
        assert C(t, "PTC RESET FUSE 6V 500MA 1206") == "", t
    assert C("1206 (3216 Metric), Convex, Long Side Terminals", "PTC RESET FUSE 6V 500MA 1206") == ""


def test_triage_keeps_a_concave_fuse_case_from_the_part_search():
    from ecadlib.kicad_index import KiCadIndex
    r = P.SourceResult("digikey", found=True, mpn="1206L050YR", manufacturer="Littelfuse",
                       package="1206 (3216 Metric), Concave", extra={"description": "PTC RESET FUSE 6V 500MA 1206"})
    ln = {"designator": "F1", "qty": "1", "value": "", "mpn": "1206L050YR", "manufacturer": "Littelfuse", "footprint": ""}
    row = BT.triage([ln], KiCadIndex([]), [_Prov(r)], None)[0]
    assert row["src_package"] == "1206 (3216 Metric)" and row["src_package_from"] == "digikey", row


def test_sot23_case_names():
    N = SP.names_only
    assert N("TO-236-3, SC-59, SOT-23-3", "SOT-23", group=True) == (True, True)    # DigiKey's SOT-23 family group
    assert N("TO-236-3, SOT-323", "SOT-23", group=True) == (True, False)           # names another land
    assert N("NPN SC-59", "SOT-23") == (True, False) and N("NPN SC-70", "SOT-23") == (True, False)
    assert N("TRANS NPN 40V 0.2A SOT23-3", "SOT-23") == (True, True) and N("NPN 40V", "SOT-23") == (False, False)
    assert SP.footprint_for(["", "TO-236-3, SC-59, SOT-23-3"], None, "")[0] is None   # not read as SOT-23 elsewhere


def _lib(symbols=(), footprints=()):
    from ecadlib.librarian import KiCadLibrary
    from ecadlib.kicad_index import norm_mpn
    k = KiCadLibrary.__new__(KiCadLibrary)
    k.footprints = {n: [lib] for lib, n in footprints}
    k.symbols = {n: [lib] for lib, n in symbols}
    k._norm = {}
    for lib, n in symbols:
        k._norm.setdefault(norm_mpn(n), []).append(f"{lib}:{n}")
    k._wild = []
    return k


def test_onsemi_case_and_reel_suffix_finds_the_symbol_but_asks_for_the_case():
    k = _lib(symbols=[("Transistor_BJT", "MMBT3904"), ("Transistor_BJT", "BC847")])
    ref, how = k.find_symbol("MMBT3904LT1G")
    assert ref == "Transistor_BJT:MMBT3904" and "(check the case)" in how, how
    ref, how = k.find_symbol("BC847BWT1G")                # SC-70: the case decides, never the name
    assert ref is None or "(check the case)" in how, how
    assert k.find_symbol("MMBT3904XYZ")[0] is None


def _agrees(case, mpn="MMBT3904LT1G", bom_fp="", bom_desc="", item_desc="", several="", hint="Package_TO_SOT_SMD:SOT-23"):
    from ecadlib import librarian as L
    st = {"hint_fp": hint, "tried": []}
    item = {"footprint": {"kicad": ""}, "description": item_desc}
    ln = {"footprint": bom_fp, "description": bom_desc, "value": ""}
    row = {"src_package": case, "src_package_from": "digikey", "src_several": several}
    return L.Run.case_agrees(None, st, item, ln, row, None, mpn, "Transistor_BJT:MMBT3904")


def test_case_and_reel_suffix_is_released_only_when_code_symbol_and_case_name_one_land():
    G = "TO-236-3, SC-59, SOT-23-3"
    assert _agrees(G) and _agrees(G, mpn="MMBT3904LT3G") and _agrees(G, item_desc="TRANS NPN 40V 0.2A SOT23-3")
    assert not _agrees(G, mpn="MMBT3904WT1G")            # W = SC-70: not the symbol's SOT-23
    assert not _agrees(G, mpn="MMBT3904XT1G")            # a code onsemi's table does not know
    assert not _agrees("SC-70, SOT-323") and not _agrees("TO-236-3, SOT-323") and not _agrees("")
    assert not _agrees(G, several="yes")
    for bom in ({"bom_fp": "SOT-323_SC-70"}, {"bom_fp": "SC-70"}, {"bom_desc": "TRANS NPN 40V SC-70"},
                {"bom_desc": "NPN SC-59"}, {"bom_desc": "NPN SOT-416"}, {"item_desc": "NPN SOT-323"}):
        assert not _agrees(G, **bom), bom
    assert not _agrees(G, hint="")                        # the symbol names no land


def test_series_with_an_ordering_code_or_a_pin_count_in_the_name():
    k = _lib(footprints=[("Connector_JST", "JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal"),
                         ("Connector_JST", "JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical"),
                         ("Connector_JST", "JST_PH_S2B-PH-SM4-TB_1x02-1MP_P2.00mm_Horizontal"),
                         ("Crystal", "Crystal_SMD_Abracon_ABM3-2Pin_5.0x3.2mm"),
                         ("Crystal", "Crystal_SMD_Abracon_ABM3-2Pin_5.0x3.2mm_HandSoldering"),
                         ("Crystal", "Crystal_SMD_3215-2Pin_3.2x1.5mm"), ("Crystal", "Crystal_SMD_G8-2Pin_3.2x1.5mm"),
                         ("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm")])
    S = k.footprint_by_series
    assert S("S2B-PH-K-S(LF)(SN)") == "Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal"
    assert S("B2B-PH-K-S(LF)(SN)") == "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical"
    assert S("ABM3-16.000MHZ-D2Y-T") == "Crystal:Crystal_SMD_Abracon_ABM3-2Pin_5.0x3.2mm"
    for m in ("S3B-PH-K-S", "ABM3B-16.000MHZ", "ABM3", "3215-32.768K", "G8-32K", "SOIC-8-X", "S2B-PH-KS"):
        assert S(m) is None, (m, S(m))


def test_series_exact_name_wins_over_a_shorter_series_with_an_ordering_code():
    k = _lib(footprints=[("Connector_JST", "JST_ZH_B10B-ZR-SM4-TF_1x10-1MP_P1.50mm_Vertical"),
                         ("Connector_JST", "JST_ZH_B10B-ZR_1x10_P1.50mm_Vertical")])
    assert k.footprint_by_series("B10B-ZR-SM4-TF(LF)(SN)") == "Connector_JST:JST_ZH_B10B-ZR-SM4-TF_1x10-1MP_P1.50mm_Vertical"
    assert k.footprint_by_series("B10B-ZR(LF)(SN)") == "Connector_JST:JST_ZH_B10B-ZR_1x10_P1.50mm_Vertical"


def test_plain_pin_header_land_from_the_description():
    H = SP.header_land
    assert H("CONN HEADER VERT 2POS 2.54MM") == "PinHeader_1x02_P2.54mm_Vertical"
    assert H("CONN HEADER VERT 6POS 2.54MM 1 ROW") == "PinHeader_1x06_P2.54mm_Vertical"
    for t in ("CONN HEADER VERT 10POS 2.54MM", "CONN HEADER R/A 2POS 2.54MM", "CONN HEADER SMD 2POS 2.54MM",
              "CONN RCPT 2POS 0.1 GOLD PCB", "CONN HEADER VERT 2POS 2MM", "CONN HEADER VERT 2POS 2.50MM",
              "CONN HEADER VERT 10POS 2.54MM SHROUDED", "CONN HEADER VERT 2POS 2.54MM LOCKING", "", None,
              "CONN HEADER VERT 2POS 2.54MM PRESS-FIT", "CONN SHUNT HEADER VERT 2POS 2.54MM", "HEADER VERT 2POS POSTS 2.10\"",
              "CONN HEADER VERT 2POS 2.54MM SOLDER CUP"):
        assert H(t) is None, t


def test_two_pad_crystal_land_gets_the_crystal_symbol():
    M = SP.mechanical_symbol
    assert M("kicad:Crystal:Crystal_SMD_Abracon_ABM3-2Pin_5.0x3.2mm", "Y1", {"1", "2"})[0] == "Crystal"
    assert M("kicad:Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", "Y1", {"1", "2", "3", "4"})[0] is None
    assert M("kicad:Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", "U1", {"1", "2"})[0] is None
    assert M("kicad:Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", "X1, XTAL2", {"1", "2"})[0] == "Crystal"
    assert M("kicad:Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", "Y1", {"1", "2", "3"})[0] is None
    text, name = SP.symbol_text("Crystal")
    assert name == "Crystal" and text.count("(pin passive") == 2 and "(pin_numbers hide)" in text
