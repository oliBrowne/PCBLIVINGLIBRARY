"""Fixes 2026-10-26 (sy): round 7 symbol review R-SY-1..6. Each test failed on 1d7bd60 and passes with its fix."""
from tests.fixtures_lite import lite
from tests.test_fix1026_bomflow import HDR, _holds, _run


# --- R-SY-1: a KiCad x wildcard may stand for nothing; the name with the most literal characters wins -------------

def _opamp(name, pins, fp="Package_TO_SOT_SMD:SOT-23-5"):
    """A small KiCad symbol: pins = [(number, name, type)]."""
    f = '(effects (font (size 1.27 1.27)))'
    pin = "".join(f'(pin {t} line (at {-7.62 if i < 3 else 7.62} {2.54 * (i % 3)} {0 if i < 3 else 180}) (length 2.54) '
                  f'(name "{nm}" {f}) (number "{n}" {f}))' for i, (n, nm, t) in enumerate(pins))
    return (f'(kicad_symbol_lib (version 20231120) (generator "test") (symbol "{name}" (in_bom yes) (on_board yes) '
            f'(property "Reference" "U" (at 0 7 0) {f}) (property "Value" "{name}" (at 0 -7 0) {f}) '
            f'(property "Footprint" "{fp}" (at 0 0 0) {f}) (property "Datasheet" "" (at 0 0 0) {f}) '
            f'(symbol "{name}_0_1" (rectangle (start -5.08 5.08) (end 5.08 -5.08) (stroke (width 0.254) (type default)) '
            f'(fill (type background)))) (symbol "{name}_1_1" {pin})))')


_STD = [("1", "OUT", "output"), ("2", "V-", "power_in"), ("3", "+", "input"), ("4", "-", "input"), ("5", "V+", "power_in")]
_REV = [("1", "OUT", "output"), ("2", "V+", "power_in"), ("3", "+", "input"), ("4", "-", "input"), ("5", "V-", "power_in")]


def _mcp_lib():
    from ecadlib.librarian import KiCadLibrary
    lib = KiCadLibrary.__new__(KiCadLibrary)
    lib.symbols = {n: ["Amplifier_Operational"] for n in ("MCP6L01x-xOT", "MCP6L01Rx-xOT", "MCP6L01Ux-xOT")}
    lib.footprints = {}
    lib._build_norm()
    return lib


def test_an_x_that_stands_for_a_blank_does_not_swallow_the_variant_letter():
    lib = _mcp_lib()
    for mpn, want in (("MCP6L01R-E/OT", "MCP6L01Rx-xOT"), ("MCP6L01RT-E/OT", "MCP6L01Rx-xOT"),
                      ("MCP6L01U-E/OT", "MCP6L01Ux-xOT"), ("MCP6L01T-E/OT", "MCP6L01x-xOT")):
        ref, how = lib.find_symbol(mpn)
        assert ref == f"Amplifier_Operational:{want}" and "wildcard" in how, (mpn, ref, how)
    # the other family names the MPN also fits, for the pinout comparison
    assert lib.wild_alternates("MCP6L01R-E/OT", "Amplifier_Operational:MCP6L01Rx-xOT") == ["Amplifier_Operational:MCP6L01x-xOT"]
    assert lib.wild_alternates("MCP6L01RT-E/OT", "Amplifier_Operational:MCP6L01Rx-xOT") == []


@lite
def test_an_mpn_two_wildcard_names_fit_with_other_pinouts_is_held(tmp_path):
    syms = [("Amplifier_Operational:MCP6L01x-xOT", _opamp("MCP6L01x-xOT", _STD)),
            ("Amplifier_Operational:MCP6L01Rx-xOT", _opamp("MCP6L01Rx-xOT", _REV))]
    csv = HDR + ("U1,1,MCP6L01R-E/OT,SOT-23-5,Microchip,MCP6L01R-E/OT,Op amp,https://example.com/a.pdf\n"
                 "U2,1,MCP6L01RT-E/OT,SOT-23-5,Microchip,MCP6L01RT-E/OT,Op amp,https://example.com/a.pdf\n")
    items = _run(tmp_path, csv, ("kicad",), extra_sym=syms)
    u1, u2 = sorted(items.values(), key=lambda it: "RT" in str(it["parameters"]))
    assert u1["source"]["symbol"] == "kicad:Amplifier_Operational:MCP6L01Rx-xOT", u1["source"]
    assert u1["status"] != "ready" and "check the variant" in _holds(u1), (u1["status"], _holds(u1))
    assert u2["source"]["symbol"] == "kicad:Amplifier_Operational:MCP6L01Rx-xOT"
    assert "check the variant" not in _holds(u2), _holds(u2)


@lite
def test_two_wildcard_names_with_one_pinout_are_not_held_for_it(tmp_path):
    syms = [("Amplifier_Operational:MCP6L01x-xOT", _opamp("MCP6L01x-xOT", _STD)),
            ("Amplifier_Operational:MCP6L01Rx-xOT", _opamp("MCP6L01Rx-xOT", _STD))]
    csv = HDR + "U1,1,MCP6L01R-E/OT,SOT-23-5,Microchip,MCP6L01R-E/OT,Op amp,https://example.com/a.pdf\n"
    (it,) = _run(tmp_path, csv, ("kicad",), extra_sym=syms).values()
    assert it["source"]["symbol"] == "kicad:Amplifier_Operational:MCP6L01Rx-xOT"
    assert "check the variant" not in _holds(it), _holds(it)
