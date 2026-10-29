"""2026-10-29 review (symbols / values / naming): passive ratings and values read from BOM text.
Run:  python3 -m tests.test_review1029b_symbols"""
from tests.fixtures_lite import lite  # noqa: F401  (the suite's convention; these tests need no temp folder)


def test_line_fields_reads_rkm_voltage_and_power_whole():
    """line_fields read the IEC 60062 rating '6V3' as '6V' and '0W5' as '0W': a 6.3 V cap was named 6 V."""
    from ecadlib import rules as RU
    f = RU.line_fields({"value": "10uF", "description": "CAP CER 10UF 6V3 X5R 0603"}, "Capacitors", "0603")
    assert f["voltage"] == "6V3", f["voltage"]
    f = RU.line_fields({"value": "100nF", "description": "100nF 2V5"}, "Capacitors", "0402")
    assert f["voltage"] == "2V5", f["voltage"]
    f = RU.line_fields({"value": "0R1", "description": "RES 0R1 1% 0W5 1206"}, "Resistors", "1206")
    assert f["power"] == "0W5", f["power"]
    # the decimal-point and plain spellings are unchanged
    f = RU.line_fields({"value": "10uF", "description": "10uF 6.3V 25VDC 1/10W"}, "Capacitors", "0603")
    assert (f["voltage"], f["power"]) == ("6.3V", "1/10W")
    f = RU.line_fields({"value": "10uF", "description": "10uF 25VDC X7R"}, "Capacitors", "0603")
    assert f["voltage"] == "25V"


def test_line_fields_reads_npo_spelt_with_letter_o():
    """'NPO' (letter O, as Yageo / many BOMs write NP0) was no dielectric, while 'COG' already read as C0G."""
    from ecadlib import rules as RU
    f = RU.line_fields({"value": "100pF", "description": "CAP CER 100PF 50V NPO 0402"}, "Capacitors", "0402")
    assert f["dielectric"] == "NP0", f["dielectric"]
    f = RU.line_fields({"value": "1nF", "description": "CAP 1nF 50V COG"}, "Capacitors", "0402")
    assert f["dielectric"] == "C0G"


def test_name_mining_takes_rkm_voltage_and_letter_o_dielectrics_as_fields():
    """ecadlib init mined '6V3' / 'NPO' / 'COG' in part names as literal text, so every new part got that rating."""
    from ecadlib import rules_infer as I
    t = I.name_template({"name": "CAP_0603_10uF_6V3_X5R", "category": "Capacitors", "params": {}}, [])
    assert t == "{cat3}_{package}_{value}_{voltage}_{dielectric}", t
    for d in ("NPO", "COG"):
        t = I.name_template({"name": f"CAP_0402_100pF_50V_{d}", "category": "Capacitors", "params": {}}, [])
        assert t == "{cat3}_{package}_{value}_{voltage}_{dielectric}", t


def test_fmt_value_restyles_upper_case_capacitor_and_inductor_values():
    """fmt_value left '100N' / '0.1UF' / '4U7' / '10UH' as written: it could not read back its own rkm style."""
    from ecadlib import rules as RU
    assert RU.fmt_value("100N", "Capacitors", "si_unit") == "100nF"
    assert RU.fmt_value("0.1UF", "Capacitors", "rkm_lower") == "100n"
    assert RU.fmt_value("4U7", "Capacitors", "si_unit") == "4.7uF"
    assert RU.fmt_value("22PF", "Capacitors", "si") == "22p"
    assert RU.fmt_value("10UH", "Inductors", "si_unit") == "10uH"
    for raw in ("100nF", "4.7uF", "1.5nF", "22pF"):      # every style reads back what rkm wrote (100N, 4U7, 1N5, 22P)
        rkm = RU.fmt_value(raw, "Capacitors", "rkm")
        assert RU.fmt_value(rkm, "Capacitors", "si_unit") == RU.fmt_value(raw, "Capacitors", "si_unit"), (raw, rkm)
    assert RU.fmt_value("10K", "Resistors", "rkm_lower") == "10k"   # resistors as before
    assert RU.fmt_value("10M", "Resistors", "si") == "10M"          # mega, never milli


if __name__ == "__main__":
    import sys
    fails = 0
    for n, fn in list(globals().items()):
        if n.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", n)
            except AssertionError as e:
                fails += 1
                print("FAIL", n, e)
    sys.exit(1 if fails else 0)
