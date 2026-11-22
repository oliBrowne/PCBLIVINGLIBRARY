"""Port pins written PORT[BIT] (Cypress PSoC: "(GPIO) P2[0]") name the same pin as PORT.BIT ("P2.0") (2026-11-22).
Plain functions: python3 -m tests.run_all test_port_bit_names_1122"""
from ecadlib import crosscheck as C, pin_drawing as PD


def test_bracket_bit_is_the_dotted_bit():
    assert C.names_agree("P2.0", "(GPIO) P2[0]")
    assert C.names_agree("P0.4", "P0[4]")
    assert C.names_agree("P3[7]", "P3.7")
    assert PD.same_pin("ic", "2", "P2.0", "(GPIO) P2[0]")


def test_another_bit_or_port_still_differs():
    assert not C.names_agree("P2.0", "P2[1]")
    assert not C.names_agree("P2.0", "P3[0]")
    assert not C.names_agree("P1.2", "P12[0]")
    assert not PD.same_pin("ic", "3", "P2.0", "(GPIO) P2[1]")


def test_a_port_pin_with_its_other_function():
    """CY8C4245 pin 44: the read's symbol "P1.7/VREF", the pinout drawing "(GPIO) P1[7]"."""
    assert C.names_agree("P1.7/VREF", "(GPIO) P1[7]")
    assert not C.names_agree("P1.6/VREF", "(GPIO) P1[7]")
    assert not C.names_agree("VREF/SDA", "P1[7]")
