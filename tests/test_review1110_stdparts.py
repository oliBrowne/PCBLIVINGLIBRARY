"""Full review 2026-11-10, standard parts: what a part IS can be said only in the BOM's description.
  * a multi-terminal part on a chip size ('RES ARRAY 4 RES 10K OHM 1206', a common-mode choke on 0805, a 4-terminal
    Kelvin resistor, an RGB LED) was built as the plain two-pad chip and marked ready;
  * a tantalum / polymer capacitor whose part number is not on the POLAR_CAP prefix list got the unpolarized C symbol
    and the symmetric chip land (no + mark)."""
from ecadlib import stdparts as SP
from ecadlib import librarian as L


def res(fp, kind, mpn, desc):
    return SP.resolve([fp, ""], kind, mpn, desc)


def test_a_multi_terminal_part_named_only_in_the_description_is_not_a_two_pad_chip():
    for fp, kind, mpn, desc in (
            ("1206", "R", "CAY16-103J4LF", "RES ARRAY 4 RES 10K OHM 1206"),
            ("0805", "L", "ACM2012-900-2P-T002", "CMC 90 OHM 400MA 2LN SMD common mode choke"),
            ("1206", "R", "WSK1206R0100FEA", "RES 0.01 OHM 1% 1206 4-terminal Kelvin"),
            ("0603", "C", "NFM18PC104R1C3D", "FILTER LC 0.1UF 3-terminal feedthrough capacitor"),
            ("1210", "LED", "", "LED RGB 1210"),
            ("0603", "R", "", "Resistor network 4 x 10k isolated 0603"),
            ("0606", "LED", "", "LED bi-color red/green"),
            ("1210", "LED", "", "LED 3 COLOR 1210 SMD"),
            ("0805", "L", "", "Balun 2.4 GHz 0805")):
        r = res(fp, kind, mpn, desc)
        assert r["footprint"] is None and r["symbol"] is None, (desc, r)
        assert "two-pad" in r["fp_how"], (desc, r)


def test_plain_two_pad_chips_still_resolve_whatever_the_description_adds():
    for fp, kind, desc, want in (
            ("0603", "R", "RES 10K OHM 1% 1/10W 0603", "R_0603_1608Metric"),
            ("0603", "C", "CAP CER 0.1UF 50V X7R 0603", "C_0603_1608Metric"),
            ("0805", "L", "FIXED IND 10UH 300MA 0805 RF choke", "L_0805_2012Metric"),
            ("0603", "L", "FERRITE BEAD 600 OHM 0603 1LN", "L_0603_1608Metric"),
            ("0603", "LED", "LED GREEN CLEAR 0603 SMD", "LED_0603_1608Metric"),
            ("2512", "R", "RES 0.01 OHM 1% 2W 2512 current sense", "R_2512_6332Metric")):
        r = res(fp, kind, "", desc)
        assert r["footprint"] == want and r["symbol"], (desc, r)


def test_a_capacitor_described_as_polarized_is_never_the_chip_capacitor():
    for desc in ("Tantalum capacitor 10uF 16V 1206 polarized", "CAP TANT 10UF 10% 16V 1206",
                 "CAP ALUM POLY 100UF 6.3V 1210", "CAP TANT POLY 100UF 6.3V 1210", "Aluminium electrolytic 47uF",
                 "Niobium oxide capacitor 22uF", "CAP POLARISED 10UF"):
        assert SP.says_polarized(desc), desc
        assert SP.part_kind("Components/Capacitors", "", desc) == "CP", desc
        r = res("1206", "C", "TMCMA1C106MTRF", desc)                  # not on the prefix list: the words decide
        assert r["footprint"] is None and r["symbol"] is None and "polarized" in r["fp_how"], (desc, r)
    for desc in ("CAP CER 10UF 16V X5R 1206", "Non-polarized ceramic capacitor", "Bipolar film capacitor",
                 "CAP CER 0.1UF unpolarized"):
        assert not SP.says_polarized(desc), desc


def test_the_librarian_takes_the_kind_from_the_description_too():
    def item(folder, desc, fp="1206"):
        return {"folder": folder, "footprint": {"ref": fp, "kicad": ""}, "description": desc}
    assert L.passive_kind(item("Components/Capacitors", "CAP TANT 10UF 10% 16V 1206")) == "CP"
    assert L.passive_kind(item("Components/Capacitors", "CAP CER 10UF 16V 1206")) == "C"
    assert L.passive_kind(item("Components/Capacitors", "CAP CER 10UF"), {"description": "tantalum 10uF"}) == "CP"
    # a multi-terminal part is not a generic passive: no shared R / L symbol, no chip land guessed by package
    assert L.passive_kind(item("Components/Resistors", "RES ARRAY 4 RES 10K OHM 1206")) is None
    assert L.passive_kind(item("Components/Inductors", "Common mode choke 90 ohm 0805")) is None
    assert L.passive_kind(item("Components/Resistors", "RES 10K OHM 1% 0603")) == "R"
