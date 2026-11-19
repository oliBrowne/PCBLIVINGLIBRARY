"""Transistors and SOT diodes held in the 1000-part run (2026-11-19), released by their own datasheet's text.
Run:  python3 -m tests.test_transistor_holds_1119

The runs (x, y, text, size) are as pypdf places them on page 1 of onsemi's BAT54LT1G and Infineon's BSS84P sheets."""
from ecadlib import pin_text as PT
from ecadlib import stdparts as SP

ONSEMI_DIODE = [(413, 589, "3", 8.0), (396, 579, "CATHODE", 8.0), (495, 589, "1", 8.0), (480, 579, "ANODE", 8.0),
                (434, 673, "1", 7.0), (453, 663, "2", 7.0), (474, 696, "3", 7.0)]     # BAS16LT1G p1: symbol + package
ONSEMI_P1 = "BAT54L Schottky Barrier Diodes ... SOT-23 (TO-236) CASE 318 STYLE 8 (Note: Microdot may be in either location)"
ONSEMI_OUTLINE = ("CASE 318 ISSUE AU STYLE 7: PIN 1. EMITTER 2. BASE 3. COLLECTOR STYLE 8: PIN 1. ANODE 2. NO CONNECTION "
                  "3. CATHODE STYLE 9: PIN 1. ANODE 2. ANODE 3. CATHODE STYLE 10: PIN 1. DRAIN 2. SOURCE 3. GATE")
INFINEON_FET = [(459, 476, "Gate", 8.0), (459, 467, "pin1", 8.0), (515, 498, "Drain", 8.0), (515, 489, "pin 3", 8.0),
                (513, 455, "Source", 8.0), (513, 445, "pin 2", 8.0), (53, 189, "Gate source voltage", 12.0)]


def test_a_single_diode_is_read_from_anode_and_cathode_beside_their_numbers():
    assert PT.diode_orders({1: "BAT54L"}, [(1, ONSEMI_DIODE)]) == ["ANK"]


def test_a_single_diode_is_read_from_the_style_its_front_page_names():
    assert PT.diode_orders({1: ONSEMI_P1, 5: ONSEMI_OUTLINE}) == ["ANK"]
    assert PT.diode_orders({1: ONSEMI_P1, 5: ONSEMI_OUTLINE}, [(1, ONSEMI_DIODE)]) == ["ANK"]     # both agree


def test_a_style_catalogue_alone_settles_nothing():
    assert PT.diode_orders({5: ONSEMI_OUTLINE}) == []                       # no front-page pointer
    other_case = ONSEMI_OUTLINE.replace("CASE 318", "CASE 419")             # the style table of another case only
    assert PT.diode_orders({1: ONSEMI_P1, 5: other_case}) == []
    two = ONSEMI_P1 + " BAT54LT3G CASE 318 STYLE 9"
    assert PT.diode_orders({1: two, 5: ONSEMI_OUTLINE}) == []               # two styles named up front
    pair = ONSEMI_P1.replace("STYLE 8", "STYLE 9")                          # a common-cathode pair's style
    assert PT.diode_orders({1: pair, 5: ONSEMI_OUTLINE}) == []


def test_two_anodes_or_disagreeing_sources_settle_nothing():
    pair = ONSEMI_DIODE + [(574, 579, "ANODE", 8.0), (586, 589, "2", 8.0)]  # a dual drawn too: two anodes
    assert PT.diode_orders({1: "x"}, [(1, pair)]) == []
    swapped = [(413, 589, "1", 8.0), (396, 579, "CATHODE", 8.0), (495, 589, "3", 8.0), (480, 579, "ANODE", 8.0),
               (453, 663, "2", 7.0)]
    assert PT.diode_orders({1: ONSEMI_P1, 5: ONSEMI_OUTLINE}, [(1, swapped)]) == ["ANK", "KNA"]   # two orders: none


def test_words_without_a_third_lead_beside_them_settle_nothing():
    sod = [(413, 589, "1", 8.0), (396, 579, "CATHODE", 8.0), (495, 589, "2", 8.0), (480, 579, "ANODE", 8.0)]
    assert PT.diode_orders({1: "x"}, [(1, sod)]) == []                      # a 2-pin variant's symbol (SOD-123)
    apart = [(1, ONSEMI_DIODE[:2] + ONSEMI_DIODE[4:]), (7, ONSEMI_DIODE[2:4])]
    assert PT.diode_orders({1: "x"}, apart) == []                           # anode and cathode on two pages
    band = [(100, 100, "CATHODE", 8.0), (100, 109, "Pin 1", 8.0), (200, 100, "ANODE", 8.0), (200, 109, "3", 8.0),
            (150, 150, "2", 8.0)]
    assert PT.diode_orders({1: "x"}, [(1, band)]) == []                     # a 'Pin 1' marker is no diode pin number


def test_only_one_plain_diode_gets_the_three_lead_symbol():
    K = SP.single_diode_kind
    assert K("DIODE SCHOTTKY 30V 200MA SOT23-3") == "D_Schottky" and K("DIODE STANDARD 100V 200MA SOT23") == "D"
    assert K("DIODE ZENER 5.1V 250MW SOT23") == "D_Zener"
    for d in ("DIODE ARRAY SCHOTTKY 30V SOT23", "DIODE ARR GP 100V 200MA SOT23", "TVS DIODE 5.25VWM SOT23-3",
              "DIODE SCHOTTKY 30V 200MA SOT23 COMMON CATHODE", "LED RED 0603", "DIODE BRIDGE 600V", "MOSFET N-CH 30V", ""):
        assert K(d) is None, d
    text, name = SP.symbol_text("DIODE3:D_Schottky:ANK", "BAT54LT1G")
    flat = " ".join(text.split())
    assert name == "D_Schottky_ANK", name
    assert '(name "A"' in flat and '(number "1"' in flat and "pin no_connect" in flat and '(number "3"' in flat


def test_a_pin_word_beside_a_terminal_is_its_number():
    assert [sorted(t.splitlines()) for t in PT.terminal_lines(INFINEON_FET)] == [["D 3", "G 1", "S 2"]]


def test_a_single_so8_mosfet_has_one_order_and_its_alternatives():
    want, others = SP.so8_fet_orders()
    assert want == "SSSGDDDD" and want not in others and len(others) == 7 and "DDDDSSSG" in others
    text, name = SP.symbol_text("PMOS:SSSGDDDD", "SI4401BDY")
    flat = " ".join(text.split())
    assert name == "Q_PMOS_SSSGDDDD" and flat.count("(pin ") == 8 and flat.count(") hide (name") == 5, flat
    figs = [(1, "S 1\nS 2\nS 3\nG 4\nD 5\nD 6\nD 7\nD 8")]
    ok = [o for o in [want] + others
          if PT.check({1: "Si4401BDY P-Channel 40 V MOSFET"}, list(zip(map(str, range(1, 9)), o)), figs, pin_count=8)
          ["status"] == "agreed"]
    assert ok == [want], ok


def test_an_infineon_reel_code_names_the_part_with_its_base():
    from ecadlib import datasheet as D
    assert D.mentions_part(["BSS84P SIPMOS Small-Signal-Transistor PG-SOT-23 BSS84P H6327:3000pcs/r."], "BSS84PH6327XTSA2")
    assert not D.mentions_part(["BSS84P SIPMOS Small-Signal-Transistor PG-SOT-23 BSS84P H6433:10000pcs/r."],
                               "BSS84PH6327XTSA2")                       # another reel code only
    assert not D.mentions_part(["BSS138 H6327 SIPMOS Small-Signal-Transistor PG-SOT-23 tape and reel"],
                               "BSS84PH6327XTSA2")                       # another part on the same reel code
    assert not D.mentions_part(["BSS84PW SIPMOS Small-Signal-Transistor SOT-323 H6327: 3000 pcs/reel"],
                               "BSS84PH6327XTSA2")                       # a sibling (BSS84PW) is not BSS84P


def test_a_makers_policy_is_not_a_datasheet():
    from ecadlib import datasheet as D
    assert D.not_a_datasheet(["  AOS Green Policy  \nAs a responsible global citizen"], D.NEVER_DATASHEET)
    assert D.not_a_datasheet(["AON7408 30V N-Channel MOSFET\nGeneral Description"], D.NEVER_DATASHEET) is None


def test_the_aos_datasheet_url_keeps_the_part_numbers_case():
    from ecadlib import datasheet as D
    assert D.maker_urls("AON7408", "Alpha & Omega Semiconductor Inc.") == [
        ("https://www.aosmd.com/res/datasheets/AON7408.pdf", "AOS datasheet URL pattern (AON7408)")]
    assert D.maker_urls("TPS26600PWPR", "Texas Instruments")[0][0] == "https://www.ti.com/lit/ds/symlink/tps26600pwpr.pdf"


def test_ir_tape_and_reel_before_pbf_is_the_kicad_lead_free_part():
    from ecadlib.librarian import KiCadLibrary
    lib = KiCadLibrary.__new__(KiCadLibrary)
    lib._norm = {"IRF7341PBF": ["Transistor_FET:IRF7341PBF"]}
    lib._wild = []
    ref, how = KiCadLibrary.find_symbol(lib, "IRF7341TRPBF")
    assert ref == "Transistor_FET:IRF7341PBF" and "tape and reel" in how, (ref, how)


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            print("ok", k)
