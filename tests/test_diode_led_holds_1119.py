"""Two-pin diodes and LEDs the 1000-part run held although a free source names them (2026-11-19, hold group F).
Run:  python3 -m tests.test_diode_led_holds_1119

- DigiKey writes axial cases 'DO-204AL, DO-41, Axial'; the word 'Axial' made the case text unused (1N4733ATR,
  BZX55C5V1-TAP, P6KE6.8A). An outline named axial by its JEDEC name keeps its case; 'Axial' alone still names none.
- DO-15 (DO-204AC), Diodes Inc.'s PowerDI-123 and Nexperia's SOD-882 are KiCad lands the standard table now has
  (pads and courtyard checked against kicad-footprints, tests/fixtures/stdparts_kicad_numbers.json.gz).
- A TVS that conducts both ways (SMBJ24CA, DigiKey 'Bidirectional Channels: 1') gets a two-way symbol, pins A1 / A2.
- KiCad symbols named '<Maker>_<MPN>' (LED:Inolux_IN-PI554FCH) are this part's own when the maker is the part's."""
from ecadlib import librarian as L, part_lookup as S, stdparts as SP
from ecadlib.search_land import dims_text

DO41 = "D_DO-41_SOD81_P10.16mm_Horizontal"
DO15 = "D_DO-15_P12.70mm_Horizontal"


def _fp(case, kind="D", mpn="X"):
    return SP.footprint_for(["", SP.case_text(case)], kind, mpn)[0]


def test_an_axial_outline_keeps_its_case_text():
    assert SP.case_text("DO-204AL, DO-41, Axial") == "DO-204AL, DO-41"
    assert _fp("DO-204AL, DO-41, Axial") == DO41
    assert _fp("DO-204AH, DO-35, Axial") == "D_DO-35_SOD27_P7.62mm_Horizontal"
    assert _fp("DO-204AC, DO-15, Axial") == DO15
    assert SP.case_text("Axial") == ""                         # names no outline: nothing to go on
    assert SP.case_text("Radial") == ""
    assert SP.case_text("0207, Axial") == ""                   # a resistor's body code is not an axial diode outline


def test_do201aa_do27_is_not_kicads_do201ad():
    """onsemi's 1N5822RLG: DigiKey 'DO-201AA, DO-27, Axial'; KiCad's 1N5822 is Vishay's DO-201AD land (1.6 mm holes,
    KiCad's DO-27 land has 1.2 mm): the case is read but names neither, so the part stays held for a look."""
    assert SP.case_text("DO-201AA, DO-27, Axial") == "DO-201AA, DO-27"
    assert _fp("DO-201AA, DO-27, Axial") is None


def test_do15_powerdi123_and_sod882_lands():
    for text in ("DO-15", "DO-204AC", "DO-204AC (DO-15)"):
        assert SP.resolve([text], "D", "P6KE6.8A")["footprint"] == DO15, text
    for text in ("POWERDI®123", "PowerDI™ 123", "PowerDI-123", "POWERDI 123"):
        assert SP.resolve([text], "D", "DFLS240L-7")["footprint"] == "D_PowerDI-123", text
    assert SP.resolve(["SOD-882"], "D", "PESD5V0S1BL")["footprint"] == "D_SOD-882"
    for text in ("PowerDI-5", "PowerDI-123 flat leads", "POWERDI®123"):     # another PowerDI, another lead form, no diode
        kind = "U" if text == "POWERDI®123" else "D"
        assert SP.resolve([text], kind, "X")["footprint"] is None, text
    assert SP.resolve(["DO-15 vertical"], "D", "X")["footprint"] is None     # standing: not the laid-flat land


def test_the_new_lands_are_kicads():
    from ecadlib import footprint_bench as FB
    from tests import test_stdparts as T
    ref = T._ref()
    rows, s = SP.bench(lambda n, lib: ref.get(n), [DO15, "D_PowerDI-123", "D_SOD-882"])
    assert s["both_ok"] == 3, rows
    pads = {p[0]: p[1:] for p in FB.numbers(SP.build("D_PowerDI-123")[0])["pads"]}
    assert pads["1"][2] > pads["2"][2]                         # pad 1 is the big cathode tab (2.40 x 1.50 mm)
    d15 = {p[0]: p[1:] for p in FB.numbers(SP.build(DO15)[0])["pads"]}
    assert round(d15["2"][0] - d15["1"][0], 2) == 12.7 and d15["1"][4] == 1.2


def test_tvs_direction():
    assert SP.symbol_kind("D", "SMBJ24CA", "D_SMB", "TVS DIODE 24VWM")[0] == "D_TVS_Bidirectional"
    assert SP.symbol_kind("D", "SMBJ24A", "D_SMB", "TVS DIODE 24VWM")[0] == "D_TVS"
    assert SP.symbol_kind("D", "SMBJ24A", "D_SMB", "TVS DIODE bidirectional")[0] is None     # number and text disagree
    assert SP.symbol_kind("D", "SMBJ24CA", "D_SMB", "TVS DIODE unidirectional")[0] is None
    assert SP.symbol_kind("D", "PESD5V0S1BL,315", "D_SOD-882", "TVS DIODE")[0] is None      # not said: held
    assert SP.symbol_kind("D", "PESD5V0S1BL,315", "D_SOD-882", "TVS DIODE bidirectional")[0] == "D_TVS_Bidirectional"
    assert SP.symbol_kind("D", "ESDALC6V1-1U2", "D_0201_0603Metric", "TVS DIODE unidirectional")[0] == "D_TVS"
    text, name = SP.symbol_text("D_TVS_Bidirectional", "SMBJ24CA")
    assert name == "D_TVS_Bidirectional" and '"A1"' in text and '"A2"' in text and '"K"' not in text


def test_digikeys_channel_count_gives_the_direction():
    d = S.specs_from_pairs([("Bidirectional Channels", "1"), ("Package / Case", "SOD-882"), ("Mounting Type", "Surface Mount")])
    assert d["dims"]["tvs_bi"] == "1"
    assert L.tvs_direction({"src_dims": dims_text(d["dims"])}) == "bidirectional"
    assert L.tvs_direction({"src_dims": "tvs_uni=1"}) == "unidirectional"
    assert L.tvs_direction({"src_dims": "tvs_uni=4"}) == ""         # four channels: an array, not a two-pin diode
    assert L.tvs_direction({"src_dims": "tvs_uni=1 | tvs_bi=1"}) == ""
    assert L.tvs_direction({}) == ""


def _lib(symbols):
    k = L.KiCadLibrary.__new__(L.KiCadLibrary)
    k.symbols = {n: [lib] for lib, n in symbols}
    k.footprints = {}
    k._build_norm()
    return k


def test_a_symbol_named_after_its_maker_and_the_mpn():
    lib = _lib([("LED", "Inolux_IN-PI554FCH"), ("Device", "Crystal_GND2"), ("Connector_Generic", "Conn_2Rows-05Pins")])
    ref, how = lib.find_symbol("IN-PI554FCH")
    assert ref == "LED:Inolux_IN-PI554FCH" and "(check the maker)" in how, (ref, how)
    ok = L.Run.maker_named({"manufacturer": ""}, {"src_manufacturer": "Inolux"}, how)
    assert "check" not in ok and "Inolux" in ok
    assert "(check the maker)" in L.Run.maker_named({"manufacturer": "Worldsemi"}, {"src_manufacturer": "Inolux"}, how)
    assert "(check the maker)" in L.Run.maker_named({}, {}, how)              # no maker named: a person looks
    assert lib.find_symbol("GND2") == (None, None)                            # too short to be a part number
    assert lib.find_symbol("2Rows-05Pins")[0] is None or "check" in lib.find_symbol("2Rows-05Pins")[1]


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main([__name__.split(".")[-1]]))
