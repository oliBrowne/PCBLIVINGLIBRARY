"""KiCad 9 writes a private field as (property private "name" "value" ...), `private` a bare atom. KiCad 7 / 8 refuse
a library holding one, so the KiCad 8-format export drops the atom (the field stays, public); fields are keyed by
their name, not by the atom. Checked with the real kicad-cli: KiCad 9.0.9 reads the KiCad 9 input, KiCad 7.0.11
loads the converted file (and refused it with the atom left in)."""
from ecadlib import kicad_export as X, sexpr


def test_private_property_dropped_for_kicad8():
    k9 = '''(symbol "R" (pin_numbers (hide yes)) (exclude_from_sim no) (in_bom yes) (on_board yes)
 (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
 (property "Value" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
 (property private "KLC_S3.3" "The rectangle is not a symbol body" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
 (symbol "R_1_1" (pin passive line (at 0 3.81 270) (length 1.27) (name "~" (effects (font (size 1.27 1.27))))
  (number "1" (effects (font (size 1.27 1.27))))))
 (embedded_fonts no))'''
    node = sexpr.parse(k9, keep_quotes=True)
    out = X.sym_kicad8(node)
    props = [p for p in sexpr.find(out, "property")]
    assert [str(p[1]) for p in props] == ["Reference", "Value", "KLC_S3.3"], [p[:3] for p in props]
    assert "private" not in sexpr.dumps(out)
    flat = X.flatten([node], "R2")                                    # keyed by the name, not by the atom
    assert [str(p[1]) for p in sexpr.find(flat, "property")] == ["Reference", "Value", "KLC_S3.3"]
    assert list(sexpr.find(X.sym_kicad8(flat), "property"))[2][2] == "The rectangle is not a symbol body"
    quoted = sexpr.parse('(symbol "Q" (property "private" "kept" (at 0 0 0)))', keep_quotes=True)
    assert [p[1:3] for p in sexpr.find(X.sym_kicad8(quoted), "property")] == [["private", "kept"]]   # a field so named
