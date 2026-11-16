"""Fixes from the 2026-11-14 100-part real BOM run on the PC (RESULT_100.md).
Plain functions: python3 -m tests.run_all test_set3_fixes"""
from ecadlib import bom_triage as BT, httpcache as H, kicad_import as K, outline_docs as OD, providers as P, sexpr
from ecadlib import stdparts as SP
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- 1. Mouser's 30 calls a minute
def test_mouser_calls_are_spaced_by_its_allowance():
    assert H.HOST_INTERVAL["api.mouser.com"] >= 2.0                 # 30 a minute


class _Http:
    def __init__(self, statuses):
        self.statuses, self.calls = list(statuses), 0

    def post(self, url, body, **kw):
        self.calls += 1
        st = self.statuses.pop(0)
        return H.Response(st, '{"Errors": [], "SearchResults": {"Parts": []}}', error=None if st == 200 else f"HTTP {st}")


def _mouser(http, monkeypatch, waits):
    from ecadlib import part_lookup as PL, userconfig as UC
    monkeypatch.setattr(UC, "secret", lambda k: "KEY")
    m = PL.MouserSourcing.__new__(PL.MouserSourcing)
    m.http, m.name = http, "mouser"
    monkeypatch.setattr(PL.MouserSourcing, "configured", lambda self: True)
    import time
    monkeypatch.setattr(time, "sleep", lambda s: waits.append(s))
    return m


@lite
def test_mouser_refusal_waits_a_minute_once_then_stops_waiting(monkeypatch):
    waits = []
    http = _Http([403, 200])
    m = _mouser(http, monkeypatch, waits)
    assert m._post(m.URL, {}).status == 200 and http.calls == 2 and waits == [m.RATE_WAIT]
    http.statuses, waits[:] = [403, 403, 403], []
    assert m._post(m.URL, {}).status == 403 and waits == [m.RATE_WAIT]     # still refused: a cap, not the minute
    assert m._post(m.URL, {}).status == 403 and waits == [m.RATE_WAIT]     # no more waiting this run


# ---------------------------------------------------------------- 2. a blocked JLCPCB search
class _Prov:
    def __init__(self, name, res):
        self.name, self.res = name, res

    def lookup(self, work):
        return self.res


def test_one_unreachable_source_does_not_unverify_a_part_another_found():
    from ecadlib.kicad_index import KiCadIndex
    ln = {"designator": "U1", "qty": "1", "value": "", "mpn": "LM358DR", "manufacturer": "TI", "footprint": ""}
    found = P.SourceResult("digikey", found=True, mpn="LM358DR", manufacturer="Texas Instruments")
    down = P.SourceResult("jlcpcb_search", error="HTTP 403")
    row = BT.triage([ln], KiCadIndex([]), [_Prov("digikey", found), _Prov("jlcpcb_search", down)], None)[0]
    assert row["status"] == "needs_footprint_generation", row["status"]
    row = BT.triage([ln], KiCadIndex([]), [_Prov("jlcpcb_search", down)], None)[0]
    assert row["status"] == "unverified (source unreachable)"                # nobody answered: still unverified
    row = BT.triage([dict(ln, lcsc="C7950")], KiCadIndex([]), [_Prov("jlcpcb_search", down)], None)[0]
    assert row["status"] == "unverified (source unreachable)"                # the BOM's own LCSC number is no answer


# ---------------------------------------------------------------- 3. Maxim's package table
DS = ["DS3231 Extremely Accurate I2C-Integrated RTC/TCXO/Crystal\nOrdering Information\nDS3231SN# -40C to +85C 16 SO DS3231SN\n",
      "PACKAGE INFORMATION\nFor the latest package outline information and land patterns (footprints), go to "
      "www.maximintegrated.com/packages.\nPACKAGE TYPE PACKAGE CODE OUTLINE NO. LAND PATTERN NO.\n16 SO W16#2 21-0042 90-0107\n"
      "REVISION HISTORY"]


def test_maxim_package_table_row_names_the_outline_documents():
    got = OD.refs(DS, "DS3231SN#", '16-SOIC (0.295", 7.50mm Width)')
    assert [g[0] for g in got] == ["21-0042", "90-0107"], got
    links = OD.links("21-0042", "maxim", DS)
    assert links[0].startswith("https://pdfserv.maximintegrated.com/") and \
        "https://www.analog.com/media/en/package-pcb-resources/package/pkg_pdf/soic/21-0042.pdf" in links
    assert OD.links("21-0664", "maxim") == ["https://pdfserv.maximintegrated.com/package_dwgs/21-0664.PDF"]
    other = [DS[0], DS[1].replace("16 SO W16#2", "16 TSSOP U16+1")]               # another package's row: not ours
    assert OD.refs(other, "DS3231SN#", '16-SOIC (0.295", 7.50mm Width)') == []
    two = [DS[0] + "DS3231SN# 16 SO W16#2\n", DS[1].replace("16 SO W16#2 21-0042 90-0107",
                                                           "8 SO S8+2 21-0041 90-0096\n16 SO S16+1 21-0041 90-0097\n"
                                                           "16 SO W16#2 21-0042 90-0107")]
    assert [g[0] for g in OD.refs(two, "DS3231SN#", '16-SOIC (0.295", 7.50mm Width)')] == ["21-0042", "90-0107"]
    unsure = [DS[0], two[1]]                  # two 16 SO rows and no ordering line naming W16 or S16: neither
    assert OD.refs(unsure, "DS3231SN#", '16-SOIC (0.295", 7.50mm Width)') == []
    no_header = [DS[0], "PACKAGE INFORMATION\nPACKAGE TYPE PACKAGE CODE\n16 SO W16#2 21-0042 90-0107\n"]   # bare numbers
    assert OD.refs(no_header, "DS3231SN#", '16-SOIC (0.295", 7.50mm Width)') == []


# ---------------------------------------------------------------- 4. KiCad 9 stacked pins
def test_stacked_pin_numbers_become_one_pin_per_pad():
    assert K.stacked_numbers("[1,15,38,39]") == ["1", "15", "38", "39"]
    assert K.stacked_numbers("[1-4,7]") == ["1", "2", "3", "4", "7"]
    for n in ("1", "A1", "[]", "[1-]", "[4-1]", "", None):
        assert K.stacked_numbers(n) is None, n
    sym = sexpr.parse('(symbol "M" (symbol "M_1_1" (pin power_in line (at 0 0 0) (length 2.54) (name "GND") '
                      '(number "[1,15,38,39]")) (pin input line (at 0 2.54 0) (length 2.54) (name "EN") (number "3"))))')
    K.expand_stacked(sym)
    pins = [p for p in sexpr.find(sexpr.first(sym, "symbol"), "pin")]
    nums = [sexpr.first(p, "number")[1] for p in pins]
    assert nums == ["1", "15", "38", "39", "3"], nums
    assert [p[1] for p in pins] == ["power_in", "passive", "passive", "passive", "input"]


# ---------------------------------------------------------------- 5. free matches
def _lib(symbols=()):
    from ecadlib.librarian import KiCadLibrary
    from ecadlib.kicad_index import norm_mpn
    k = KiCadLibrary.__new__(KiCadLibrary)
    k.symbols = {n: [lib] for lib, n in symbols}
    k._norm = {}
    for lib, n in symbols:
        k._norm.setdefault(norm_mpn(n), []).append(f"{lib}:{n}")
    k._wild = []
    return k


def test_packing_code_after_a_grade_letter_still_finds_the_symbol_for_a_look():
    k = _lib([("Transistor_BJT", "BC847"), ("74xx", "74HC595")])
    ref, how = k.find_symbol("BC847C,215")
    assert ref == "Transistor_BJT:BC847" and "check" in how and "',215'" in how, how
    ref, how = k.find_symbol("74HC595D,118")
    assert ref == "74xx:74HC595" and "check" in how, how
    assert k.find_symbol("BC847XYZ,215")[0] is None


def test_tvs_direction_from_the_series_numbering():
    U = SP.tvs_unidirectional
    for m in ("SMBJ5.0A", "P6KE6.8A-E3/54", "SMAJ15A-13-F", "SMCJ24A-TR", "1.5KE18A"):
        assert U(m), m
    for m in ("SMBJ5.0CA", "SMBJ5.0C", "1.5KE18CA", "SMBJ5.0", "ESD5V0", "PESD5V0S1BL,315", ""):
        assert not U(m), m
    assert U("X1", "TVS DIODE UNIDIRECTIONAL 5V") and not U("SMBJ5.0A", "TVS DIODE BIDIRECTIONAL")
    assert not U("SMBJ5.0CA", "TVS Diode UNIDIRECTIONAL") and not U("SA612A", "TVS") and not U("SM6T100A", "TVS")
    assert SP.symbol_kind("D", "SMBJ5.0A", "D_SMB", "TVS DIODE 5VWM 9.2VC SMB")[0] == "D_TVS"
    assert SP.symbol_kind("D", "SMBJ5.0CA", "D_SMB", "TVS DIODE 5VWM 9.2VC SMB")[0] is None


def test_generic_symbols_for_connector_lands_with_mounting_pads_usb_and_terminal_blocks():
    M = SP.mechanical_symbol
    assert M("kicad:Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", "J1",
             {"1", "2", "3", "4", "MP"})[0] == "Conn_01x04_MountingPin"
    assert M("kicad:Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", "J1", {"1", "2", "3", "4"})[0] is None
    assert M("kicad:Connector_USB:USB_Micro-B_Molex-105017-0001", "J2", {"1", "2", "3", "4", "5", "SH"})[0] == "USB_Micro-B_Receptacle"
    assert M("kicad:Connector_USB:USB_Micro-B_Molex-105017-0001", "J2", {"1", "2", "3", "4", "5", "6"})[0] is None
    assert M("kicad:Connector_USB:USB_Micro-B_Molex-105017-0001", "U2", {"1", "2", "3", "4", "5", "SH"})[0] is None
    assert M("kicad:TerminalBlock_TE-Connectivity:TerminalBlock_TE_282834-2_1x02_P2.54mm_Horizontal", "J3",
             {"1", "2"})[0] == "Conn_01x02"
    text, name = SP.symbol_text("Conn_01x04_MountingPin")
    assert name == "Conn_01x04_MountingPin" and '(number "MP"' in text and text.count("(pin passive") == 5
    text, name = SP.symbol_text("USB_Micro-B_Receptacle")
    for n, pin in (("1", "VBUS"), ("2", "D-"), ("3", "D+"), ("4", "ID"), ("5", "GND"), ("SH", "Shield")):
        assert f'(name "{pin}"' in text and f'(number "{n}"' in text, n


# ---------------------------------------------------------------- 6. another maker's KiCad part
def _other(case="", ours="onsemi", theirs="Vishay", several=""):
    from ecadlib import librarian as L
    st = {"sym_maker": theirs, "tried": []}
    row = {"src_package": case, "src_package_from": "digikey", "src_several": several}
    return L.Run.other_makers_land(st, {"manufacturer": ours}, row, None, "SS34", "Diode_SMD:D_SMA")


def test_another_makers_kicad_land_is_held_unless_the_case_confirms_it():
    assert "DO-214AB, SMC" in _other("DO-214AB, SMC")                       # onsemi's SS34 is an SMC
    assert "no case text" in _other("")                                      # nothing to confirm it
    assert _other("DO-214AC, SMA") == ""                                     # the case is KiCad's land
    assert _other("", ours="Vishay") == "" and _other("", ours="Vishay Semiconductor Diodes Division") == ""
    assert _other("", ours="") == "" and _other("", theirs="") == ""         # no maker named: taken as KiCad's
    assert "compare" in _other("DO-214AC, SMA", several="yes")
