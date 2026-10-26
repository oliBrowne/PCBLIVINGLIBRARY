"""Fixes 2026-10-26 (fx20): review R-FX20-1..3. Each test failed on 5d9ab0a and passes with its fix."""
from tests.fixtures_lite import fresh_home, lite


# --- R-FX20-1: a KiCad x stands for nothing only where an ordering letter goes --------------------------------------

def _lib(**symbols):
    from ecadlib.librarian import KiCadLibrary
    lib = KiCadLibrary.__new__(KiCadLibrary)
    lib.symbols, lib.footprints = {n: [l] for n, l in symbols.items()}, {}
    lib._build_norm()
    return lib


def test_a_blank_x_in_the_middle_of_a_name_does_not_make_another_part_fit():
    lib = _lib(VTL5C="Isolator", VTL5Cx2="Isolator")
    ref, how = lib.find_symbol("VTL5C2")             # the single Vactrol, not the dual VTL5Cx/2
    assert ref == "Isolator:VTL5C" and "check" in how, (ref, how)
    assert lib.find_symbol("VTL5C42")[0] == "Isolator:VTL5Cx2"   # a filled x still fits
    lib = _lib(SMAJxxA="Diode")
    assert lib.find_symbol("SMAJ5A") == (None, None)  # one of two x slots left blank mid-name: no fit
    assert lib.find_symbol("SMAJ5.0A")[0] == "Diode:SMAJxxA"


def test_a_blank_x_next_to_a_dash_or_slash_or_at_the_end_still_fits():
    lib = _lib(**{"MCP6L01x-xOT": "Amplifier_Operational", "MCP6L01Rx-xOT": "Amplifier_Operational"})
    for mpn, want in (("MCP6L01R-E/OT", "MCP6L01Rx-xOT"), ("MCP6L01-E/OT", "MCP6L01x-xOT"),
                      ("MCP6L01RT-E/OT", "MCP6L01Rx-xOT")):
        ref, how = lib.find_symbol(mpn)
        assert ref == f"Amplifier_Operational:{want}" and "wildcard" in how, (mpn, ref, how)
    lib = _lib(TPS7A20xx="Regulator_Linear")
    assert lib.find_symbol("TPS7A201")[0] == "Regulator_Linear:TPS7A20xx"   # blank at the end
