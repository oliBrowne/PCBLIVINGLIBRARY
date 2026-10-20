"""LCSC C2975360 (SOSET 16M 20PF 10PPM crystal), added from the Altium menu on 2026-10-20: its EasyEDA symbol names the
pins "1" and "2" and shows the names, so both names landed on top of each other in the narrow body ("12"), and the
part came out as U? because EasyEDA's prefix is U?. The symbol here is SYNTHETIC, drawn in the shape of that one:
two 200 mil pins pointing into a 100 mil wide crystal body. A crystal is a two-pin passive: no pin names or numbers
shown (Oliver's rule, as the standard R / C / L symbols), and designator Y?."""
import copy, json, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, easyeda_convert as EC, kicad_import as K, sexpr, symbol_layout as SL
from tests import test_librarian as TL


def _pin(num, name, x, d):
    return (f"P~show~0~{num}~{x}~300~0~gge{num}~0^^{x}~300^^M {x} 300 h {d}~#880000^^"
            f"1~{x}~300~0~{name}~start~~~#0000FF^^1~{x}~300~0~{num}~end~~~#0000FF^^0~{x}~300^^0~M 0 0")


XTAL = {"head": {"x": 400, "y": 300, "c_para": {"pre": "U?", "name": "16M 20PF 10PPM", "Manufacturer": "SOSET",
                                                 "Manufacturer Part": "16M 20PF 10PPM"}},
        "shape": [_pin(1, "1", 380, 15), _pin(2, "2", 420, -15),
                  "R~397~292~~~6~16~#880000~1~0~none~gge3~0",
                  "PL~395 292 395 308~#880000~1~0~none~gge4~0", "PL~405 292 405 308~#880000~1~0~none~gge5~0"]}


def _sym(text):
    return K.convert_symbol([K._find_symbol(sexpr.parse(text), "X")], "X", None, [], None, [])


def test_pin_names_that_repeat_the_number_are_hidden():
    sym = _sym(EC.symbol_to_kicad(XTAL, "X"))
    pins = [pr[1] for pr in sym["prims"] if pr[0] == "pin"]
    assert [p["name"] for p in pins] == ["1", "2"]
    assert not any(p.get("show_name") for p in pins), pins
    assert not [t for t in SL.text_problems(sym) if "name" in t], SL.text_problems(sym)


def test_real_pin_names_stay_shown():
    sym = _sym(EC.symbol_to_kicad(json.loads(json.dumps(TL.EE["result"]["dataStr"])), "X"))
    pins = [pr[1] for pr in sym["prims"] if pr[0] == "pin"]
    assert all(p["show_name"] and p["show_num"] for p in pins), pins           # G / S / D


def test_librarian_crystal_from_the_menu_has_no_pin_text_and_is_a_y():
    ee = copy.deepcopy(TL.EE)
    r = ee["result"]
    r["dataStr"] = copy.deepcopy(XTAL)
    r["title"] = "16M 20PF 10PPM"
    r["lcsc"]["number"] = "C2975360"
    r["dataStr"]["head"]["c_para"]["package"] = r["packageDetail"]["title"]
    bom = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
           "U1,1,16M 20PF 10PPM,,SOSET,16M 20PF 10PPM,C2975360,Crystals 16MHz 20pF,\n")   # the menu writes U1
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, http=TL.FakeHTTP({"C2975360": ee}), bom=bom, sources=["easyeda"])
        it = next(iter(items.values()))
        assert it["folder"].endswith("Crystals"), it["folder"]
        f = next((Path(d) / "out").rglob("*.SchLib"))
        _, syms = AL.read_schlib(f)
    recs = syms[0]["records"]
    pins = [r for r in recs if r.get("RECORD") == "2"]
    assert len(pins) == 2
    for p in pins:
        assert not p["conglomerate"] & 0x10, ("pin number shown", p)
        assert not p["conglomerate"] & 0x08, ("pin name shown", p)
        assert not p["conglomerate"] & 0x04, ("pin hidden: it would not connect", p)
    des = next(r for r in recs if r.get("RECORD") == "34")
    assert des["TEXT"] == "Y?", des


def test_crystal_with_named_pins_keeps_x_and_shows_no_pin_text():
    """The 12M 20PF crystal of the same day: designator X?, pins named OSC1 / OSC2 ran into each other and the leads."""
    from ecadlib import librarian as L
    x = copy.deepcopy(XTAL)
    x["head"]["c_para"]["pre"] = "X?"
    x["shape"][:2] = [_pin(1, "OSC1", 380, 15), _pin(2, "OSC2", 420, -15)]
    sym = _sym(EC.symbol_to_kicad(x, "X"))
    assert SL.text_problems(sym)                                    # as drawn: the names collide
    sym = L.quiet_two_pin(L.category_designator(sym, "Components/Crystals"))
    assert sym["designator"] == "X?" and not SL.text_problems(sym), (sym["designator"], SL.text_problems(sym))
    assert all(not pr[1]["show_name"] and not pr[1]["show_num"] for pr in sym["prims"] if pr[0] == "pin")


def test_category_designator_only_replaces_a_generic_u():
    from ecadlib import librarian as L
    assert L.category_designator({"designator": "U?"}, "Components/Transistors")["designator"] == "Q?"
    assert L.category_designator({"designator": "U?"}, "ICs")["designator"] == "U?"
    assert L.category_designator({"designator": "D?"}, "Crystals")["designator"] == "D?"


def test_three_pin_symbols_keep_their_pin_text():
    from ecadlib import librarian as L
    sym = _sym(EC.symbol_to_kicad(json.loads(json.dumps(TL.EE["result"]["dataStr"])), "X"))
    assert L.quiet_two_pin(sym) == sym
