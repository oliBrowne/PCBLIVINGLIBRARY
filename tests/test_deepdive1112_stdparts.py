"""Deep-dive testing 2026-11-12, standard parts: the description words that say 'not a two-pad chip' and 'has a +
side' missed the way distributors and makers really write them. 46 of 65 such BOM lines came out ready as a plain
chip: 'RES NET', 'RESNET 8P4R', 'quad', 'x4 bussed', '4-wire', 'CMC', 'coupled inductor', 'LED RGBW' (the W hid the
RGB), 'trimmer', 'diode pair common cathode', 'POSCAP', 'OS-CON', 'Elko', 'CAP ELECT', 'Ta cap', 'supercapacitor'."""
from ecadlib import stdparts as SP


def res(fp, kind, desc, mpn="ZZ1"):
    return SP.resolve([fp, ""], kind, mpn, desc)


MULTI = (
    ("0603", "R", "RES NET 4 ISOLATED 10K 0603"), ("1206", "R", "RESNET 10K 8P4R"), ("0612", "R", "R-PACK 4x10k"),
    ("1206", "R", "Resistor quad 10k isolated"), ("0804", "R", "Chip resistor 4R8P 10k convex"),
    ("1206", "R", "10k x4 bussed"), ("2512", "R", "Current sense resistor 4-wire 10mOhm"),
    ("1206", "R", "Sense resistor 4T 10mOhm"), ("0606", "R", "Dual resistor 10k matched pair"),
    ("0805", "R", "Trimmer potentiometer 10k SMD"), ("1206", "R", "Voltage divider 10k/10k matched"),
    ("0612", "C", "CAP ARR 4x100nF"), ("0603", "C", "X2Y capacitor 100nF"), ("0805", "C", "Capacitor quad 100nF"),
    ("0805", "C", "Dual capacitor 100nF"),
    ("1210", "L", "Coupled inductor 10uH 1:1"), ("0805", "L", "CMC 90 ohm"), ("1210", "L", "Inductor 2-in-1 10uH"),
    ("1812", "L", "Dual winding inductor"), ("0805", "L", "Ferrite bead quad"), ("0805", "L", "Bead 4 circuits 120R"),
    ("0603", "L", "Bifilar inductor"), ("0603", "L", "T-filter 600R"), ("1206", "L", "USB line filter 90R"),
    ("1210", "LED", "LED RGBW 1210"), ("1210", "LED", "LED RGBA"), ("0603", "LED", "LED red/green 0603"),
    ("0606", "LED", "LED R+G dual chip"), ("0603", "D", "Schottky diode pair common cathode"),
    ("0603", "D", "Diode quad"), ("0805", "D", "Zener dual series"),
)
POLAR = (
    "POSCAP 10uF 16V", "OS-CON 10uF", "Tantal Kondensator 10uF", "Elko 10uF 16V SMD", "CAP ELECT 10UF 16V",
    "Ta cap 10uF 16V case A", "Hybrid capacitor 10uF", "MnO2 chip capacitor 10uF", "E-CAP 10uF",
    "Supercapacitor EDLC 0.1F", "TANTCAP 10U", "Kondensator tantalowy 10uF", "Condensador de tantalio 10uF",
    "钽电容 10uF 16V", "电解电容 10uF", "CAP TANTALE 10uF",
)
OTHER_LAND = (("0603", "LED", "LED red 0603 reverse mount"), ("1206", "LED", "LED side view right angle 1206"),
              ("0603", "LED", "LED red reverse-gullwing"), ("1206", "LED", "Side-emitting LED 1206"))
PLAIN = (
    ("0603", "R", "RES 10K OHM 1% 1/10W 0603"), ("0402", "R", "Thick film resistor 4.7k 5% AEC-Q200 anti-sulfur"),
    ("2512", "R", "RES 0.01 OHM 1% 1W 2512 current sense"), ("0603", "R", "Zero ohm jumper 0603"),
    ("0603", "R", "NTC thermistor 10k 1% B3950"), ("0805", "R", "Resistor 4.7k"), ("0603", "R", "RES 4.02K 0.1% 25ppm"),
    ("0603", "C", "CAP CER 0.1UF 50V X7R 0603"), ("0805", "C", "MLCC 10uF 16V X5R"), ("1206", "C", "Cap 4.7uF 25V 10%"),
    ("0402", "C", "CAP CER 4PF 50V C0G/NP0 0402"), ("1210", "C", "Capacitor 22uF 6.3V soft termination"),
    ("0603", "L", "FERRITE BEAD 600 OHM 0603 1LN"), ("0805", "L", "Inductor 10uH 20% shielded"),
    ("0402", "L", "RF inductor 3.9nH wirewound high-Q"), ("1210", "L", "Power inductor 4.7uH 2A"),
    ("0603", "LED", "LED RED CLEAR 0603 SMD"), ("0805", "LED", "LED green 520nm 20mA"),
    ("0603", "D", "TVS DIODE 5V bidirectional 0603"), ("0402", "D", "ESD suppressor 5V 0402"),
    # a body size is two lengths, not a count; an air coil's turns are not terminals
    ("1210", "L", "Inductor 10uH 3.2 x 2.5 x 2 mm"), ("0805", "L", "Inductor 2x1.25mm 4.7uH"),
    ("0603", "L", "Air core inductor 5T 12nH"), ("0603", "C", "CAP CER 1UF 6.3V X5R 1.6 x 0.8 mm"),
)


def test_multi_terminal_parts_in_distributor_words_are_not_two_pad_chips():
    for fp, kind, desc in MULTI:
        r = res(fp, kind, desc)
        assert r["footprint"] is None and r["symbol"] is None, (desc, r)


def test_polarized_capacitors_in_other_words_get_no_chip_land():
    for desc in POLAR:
        r = res("1206", "C", desc)
        assert r["footprint"] is None and r["symbol"] is None and "polarized" in r["fp_how"], (desc, r)


def test_a_land_of_another_kind_named_in_the_description_is_not_the_plain_chip():
    for fp, kind, desc in OTHER_LAND:
        r = res(fp, kind, desc)
        assert r["footprint"] is None, (desc, r)


def test_plain_two_pad_chips_still_resolve():
    for fp, kind, desc in PLAIN:
        r = res(fp, kind, desc)
        assert r["footprint"] and r["footprint"].split("_")[1] == fp, (desc, r)
        assert r["symbol"] or kind == "D", (desc, r)          # a chip-size diode's symbol comes from the other sources


def test_a_non_polar_capacitor_is_still_a_chip():
    for desc in ("Non-polar ceramic 10uF", "CAP CER 10UF bipolar MLCC", "Polyester film capacitor 100nF",
                 "CAP FILM 0.1UF polypropylene"):
        r = res("1206", "C", desc)
        assert r["footprint"] == "C_1206_3216Metric", (desc, r)
