"""Change control (#105), round 6: a body style (De Morgan view) or unit that is new is compared with body style 1 of
the unit, a display-mode count change is at least a warn, and a hidden pin's net (Altium HiddenNetName) decides
whether stacked power pins are one net."""
import copy, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, change_diff as CD, change_gate as CG, fingerprint as FP, sexpr
from tests.test_change_control_round5 import _altium
from tests.fixtures_lite import clean_all, scratch


def texts(ch):
    return [c["text"] for c in ch]


def _pin(num, name, x, y, a=0, t="passive", hide=False):
    return (f'(pin {t} line (at {x} {y} {a}) (length 2.54){" hide" if hide else ""} (name "{name}" (effects (font '
            f'(size 1.27 1.27)))) (number "{num}" (effects (font (size 1.27 1.27)))))')


def _node(views):
    """views: {(unit, style): [pin strings]} -> a KiCad symbol node."""
    body = "".join(f'(symbol "Q_{u}_{s}" ' + " ".join(ps) + ")" for (u, s), ps in views.items())
    return sexpr.parse(f'(symbol "Q" (property "Reference" "Q" (at 0 0 0)) (property "Value" "Q" (at 0 0 0)) {body})')


OK = [_pin(1, "G", -5.08, 0, 0, "input"), _pin(2, "S", 2.54, -5.08, 90), _pin(3, "D", 2.54, 5.08, 270)]
SHORT = [_pin(1, "G", -5.08, 0, 0, "input"), _pin(2, "S", 2.54, -5.08, 90), _pin(3, "D", 2.54, -5.08, 90)]


def _fp(views):
    return FP.of_kicad(_node(views), part="Q")


def _altium_too(views):
    """The same symbol as the librarian releases it: converted to Altium and read back."""
    from ecadlib import kicad_import as K
    conv = K.convert_symbol([_node(views)], "Q", None, [], None, [])
    with tempfile.TemporaryDirectory() as d:
        _, ent = AL.read_schlib(AL.write_schlib(Path(d) / "Q.SchLib", [conv]))
        return FP.build("Q", FP.altium_symbol(ent[0])[0], tool="altium")


def test_a_new_body_style_that_joins_pins_is_held():
    """R6-1: a hand edit that only added a De Morgan view with D on S's end passed as "display modes 1 -> 2": the
    stacks of a view that did not exist were never compared. A new view is compared with body style 1 of its unit."""
    for read in (_fp, _altium_too):
        a, b = read({(1, 1): OK}), read({(1, 1): OK, (1, 2): SHORT})
        ch = CD.diff(a, b)
        assert CD.worst(ch) == "electrical" and CG.decide(ch, CG.POLICY)["action"] == "hold", texts(ch)
        assert "pin 3 (D) (body style 2, a new one) now shares a pin end with pin 2 (S): they connect" in texts(ch), texts(ch)
        assert not any(c.get("drawing") for c in ch if c["class"] == "electrical")


def test_a_new_body_style_drawn_like_the_first_warns():
    """A view placed schematics can switch to is at least a warn, whatever the policy; with the same connections and
    signals as body style 1 it is nothing more. Removing it warns as well."""
    a, b = _fp({(1, 1): OK}), _fp({(1, 1): OK, (1, 2): OK})
    ch = CD.diff(a, b)
    assert CD.worst(ch) == "geometry-minor" and "symbol display modes (body styles) 1 -> 2" in texts(ch), texts(ch)
    assert CG.decide(ch, CG.normalize_policy({"geometry_minor": "pass"}))["action"] == "warn"
    assert CG.decide(CD.diff(b, a), CG.POLICY)["action"] == "warn"
    assert CD.diff(b, _fp({(1, 1): OK, (1, 2): OK})) == []


def test_a_pin_that_is_another_signal_in_a_new_body_style_is_held():
    alt = [_pin(1, "G", -5.08, 0, 0, "input"), _pin(2, "D", 2.54, -5.08, 90), _pin(3, "S", 2.54, 5.08, 270)]
    ch = CD.diff(_fp({(1, 1): OK}), _fp({(1, 1): OK, (1, 2): alt}))
    assert CD.worst(ch) == "electrical", texts(ch)
    assert any(t.startswith("pin 2 in body style 2 (a new body style) is D (passive), in body style 1 S (passive)")
               for t in texts(ch)), texts(ch)


def test_stacked_pins_in_a_new_unit_are_a_new_connection():
    one = [_pin(1, "A", -5.08, 0, 0, "input"), _pin(2, "B", -5.08, -2.54, 0, "input"), _pin(3, "C", 5.08, 0, 180, "output")]
    two = {(1, 1): [_pin(1, "A", -5.08, 0, 0, "input"), _pin(3, "C", 5.08, 0, 180, "output")],
           (2, 1): [_pin(2, "B", -5.08, 0, 0, "input"), _pin(4, "X", -5.08, 0, 0, "input")]}
    ch = CD.diff(_fp({(1, 1): one + [_pin(4, "X", -5.08, -5.08, 0, "input")]}), _fp(two))
    assert CD.worst(ch) == "electrical", texts(ch)
    assert any("(a new unit) now shares a pin end with" in t for t in texts(ch)), texts(ch)


def test_released_differs_sees_a_de_morgan_view_added_upstream():
    """kicad-export reuses a KiCad source only when it is the released revision: one that gained a De Morgan view
    (with D on S, or drawn like body style 1) is not."""
    from ecadlib import kicad_export as KX, kicad_import as K
    with tempfile.TemporaryDirectory() as d:
        conv = K.convert_symbol([_node({(1, 1): OK})], "Q", None, [], None, [])
        AL.write_schlib(Path(d) / "Q.SchLib", [conv])
        it = {"name": "Q", "symbol": {"ref": "Q", "file": "Q.SchLib"}, "parameters": []}
        assert KX.released_differs(it, Path(d), _node({(1, 1): OK})) == []
        bad = KX.released_differs(it, Path(d), _node({(1, 1): OK, (1, 2): SHORT}))
        assert any("now shares a pin end with pin 2 (S)" in c["text"] for c in bad), bad
        assert KX.released_differs(it, Path(d), _node({(1, 1): OK, (1, 2): OK}))


def test_hidden_pins_named_alike_on_two_nets_are_not_one_net():
    """R6-2: Altium hidden pins both named GND, tied to AGND and DGND by HiddenNetName, are two nets: stacking them is
    a real new connection (not a drawing change kicad-export may ship), and pulling them apart is held."""
    def p(n, net, y):
        return {"num": n, "name": "GND", "type": "power", "hidden": True, "net": net}, "bottom", y, -40
    base = [({"num": "1", "name": "IN", "type": "input"}, "left", 0, -10)]
    def fp(pins):
        s = {"name": "Q", "description": "q", "designator": "U?", "comment": "*", "footprint": None,
             "prims": [("rect", 0, 0, 40, -40)] + [("pin", q, side, x, y, 30) for q, side, x, y in pins],
             "label_at": ((0, 2), (0, -48))}
        d = scratch("ecadlib-r6-")
        AL.write_schlib(d / "Q.SchLib", [s])
        return FP.of_item({"name": "Q", "symbol": {"ref": "Q", "file": str(d / "Q.SchLib")}, "parameters": []}, d)
    apart = fp(base + [p("4", "AGND", 10), p("5", "DGND", 30)])
    joined = fp(base + [p("4", "AGND", 10), p("5", "DGND", 10)])
    assert {n: q.get("net") for n, q in apart["symbol"]["pins"].items()} == {"1": None, "4": "AGND", "5": "DGND"}
    ch = CD.diff(apart, joined)
    assert CD.worst(ch) == "electrical" and not any(c.get("drawing") for c in ch if c["class"] == "electrical"), ch
    ch = CD.diff(joined, apart)
    assert CD.worst(ch) == "electrical" and CG.decide(ch, CG.POLICY)["action"] == "hold", texts(ch)
    same = fp(base + [p("4", "GND", 10), p("5", "GND", 10)])        # one net: a split is the house-rules warn
    ch = CD.diff(same, fp(base + [p("4", "GND", 10), p("5", "GND", 30)]))
    assert CD.worst(ch) == "geometry-minor", texts(ch)


clean_all(globals())        # R-G2-8: the scratch folders a test makes go when it ends
