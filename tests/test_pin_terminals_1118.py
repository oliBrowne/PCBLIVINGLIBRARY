"""A transistor's pin drawing read free from the PDF's placed text: each terminal's name with its pin number beside it.
Run:  python3 -m tests.test_pin_terminals_1118

onsemi draws COLLECTOR / 3, 1 / BASE, 2 / EMITTER around the schematic symbol; Vishay 1 / G, 2 / S, D / 3. The runs
below are (x, y, text, size) as pypdf places them on page 1 of BC847CLT1G, NTR4003NT1G and SI2302CDS (2026-11-18)."""
from ecadlib import pin_text as PT

ONSEMI_BJT = [(303, 38, "1", 8.0), (434, 588, "1", 7.0), (454, 577, "2", 7.0), (471, 611, "3", 7.0),
              (450, 708, "COLLECTOR", 8.0), (468, 698, "3", 8.0), (419, 680, "1", 8.0), (412, 670, "BASE", 8.0),
              (471, 653, "2", 8.0), (459, 643, "EMITTER", 8.0), (424, 452, "1", 8.0), (108, 225, "C", 8.0)]
ONSEMI_FET = [(408, 484, "1", 8.0), (456, 527, "3", 8.0), (470, 445, "2", 8.0), (455, 596, "2", 8.0),
              (453, 397, "3", 8.0), (436, 607, "1", 8.0), (479, 633, "3", 8.0), (445, 389, "Drain", 8.0),
              (433, 329, "1", 8.0), (427, 321, "Gate", 8.0), (473, 329, "2", 8.0), (462, 321, "Source", 8.0)]
VISHAY_FET = [(141, 672, "S", 8.0), (165, 672, "3", 8.0), (193, 672, "3", 8.0), (177, 574, "1", 7.0),
              (177, 565, "G", 7.0), (221, 599, "2", 7.0), (221, 591, "S", 7.0), (136, 650, "D", 7.0),
              (137, 641, "3", 7.0), (476, 513, "G", 7.0), (520, 567, "D", 7.0), (521, 465, "S", 7.0)]


def lines(runs):
    return [sorted(t.splitlines()) for t in PT.terminal_lines(runs)]


def test_terminal_words_take_the_number_beside_them():
    assert lines(ONSEMI_BJT) == [["B 1", "C 3", "E 2"]]
    assert lines(ONSEMI_FET) == [["D 3", "G 1", "S 2"]]


def test_terminal_letters_take_the_number_beside_them():
    assert lines(VISHAY_FET) == [["D 3", "G 1", "S 2"]]


def test_letters_with_no_numbers_are_no_drawing():
    # Diodes Inc. draws D, G, S on the schematic with no pin numbers: nothing to read
    assert lines([(320, 402, "D", 11.4), (320, 327, "S", 11.4), (274, 356, "G", 11.4), (147, 53, "1", 8.0),
                  (184, 53, "2", 8.0), (222, 53, "3", 8.0)]) == []


def test_a_number_between_two_names_is_not_guessed():
    runs = [(427, 321, "Gate", 8.0), (447, 321, "Source", 8.0), (440, 329, "1", 8.0), (473, 329, "2", 8.0),
            (445, 389, "Drain", 8.0), (453, 397, "3", 8.0)]
    assert lines(runs) == []


def test_an_incomplete_or_repeated_set_is_no_drawing():
    assert lines(ONSEMI_FET[:9] + ONSEMI_FET[10:]) == []                    # Gate's number missing
    twice = ONSEMI_FET + [(133, 229, "1", 8.0), (127, 221, "Gate", 8.0), (173, 229, "3", 8.0), (162, 221, "Source", 8.0),
                          (145, 289, "Drain", 8.0), (153, 297, "2", 8.0)]
    assert len(lines(twice)) == 2                                           # two drawings apart: each read on its own
    assert lines([(100, 100, "b", 8.0), (100, 109, "1", 8.0), (130, 100, "e", 8.0), (130, 109, "2", 8.0),
                  (160, 100, "c", 8.0), (160, 109, "3", 8.0)]) == []        # a mechanical drawing's dimension letters


def test_the_check_agrees_with_the_drawing_and_with_no_other_order():
    figs = [(1, t) for t in PT.terminal_lines(ONSEMI_FET)]
    ok = PT.check({1: "NTR4003N Small Signal MOSFET"}, [("1", "G"), ("2", "S"), ("3", "D")], figs)
    assert ok["status"] == "agreed" and ok["where"] == "pin drawing", ok
    bad = PT.check({1: "NTR4003N Small Signal MOSFET"}, [("1", "G"), ("2", "D"), ("3", "S")], figs)
    assert bad["status"] != "agreed", bad
    assert PT.pin_orders({1: "x"}, "GSD", figs) == ["GSD"]
    two = figs + [(2, "G 1\nD 2\nS 3")]                                     # another package drawn otherwise: no verdict
    assert PT.pin_orders({1: "x"}, "GSD", two) == []


def test_a_single_transistor_is_told_from_the_part_searchs_description():
    from ecadlib import stdparts as SP
    T = SP.transistor_type
    assert T("MOSFET N-CH 30V 500MA SOT23-3") == "NMOS" and T("MOSFET P-CH 12V 7.1A SOT23-3") == "PMOS"
    assert T("TRANS NPN 45V 0.1A SOT23-3") == "NPN" and T("TRANS PNP 60V 0.6A SOT23-3") == "PNP"
    for d in ("MOSFET 2N-CH 55V 4.7A 8SO", "TRANS PREBIAS NPN 50V SOT23", "TRANS 2NPN 40V 200MA SOT-363",
              "TRANS NPN DARL 30V 0.5A SOT23", "MOSFET N/P-CH 20V", "JFET N-CH 25V SOT23", "DIODE SCHOTTKY 30V", ""):
        assert T(d) is None, d
    text, name = SP.symbol_text("NMOS:SGD", "X1")                          # any order the datasheet gives can be drawn
    assert name == "Q_NMOS_SGD" and '(name "S"' in text.replace("\n", " "), name


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            print("ok", k)
