"""Regression tests for the full review of the KiCad area (sexpr, kicad_export / kicad_import, kicad_pcm, the pcbnew
action plugin). Run:  python3 -m tests.test_review_full_kicad"""
import json, pathlib, sys, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import kicad_export as X, kicad_import as K, kicad_pcm as KP, sexpr

FIX = Path(__file__).parent / "fixtures" / "kicad"


def _tmp():
    return Path(tempfile.mkdtemp(prefix="ecadlib-review-kicad-"))


def test_symbol_text_angle_is_tenths_of_a_degree():
    # KiCad's symbol parser: "LIB_TEXT is really decidegrees" - (at x y 900) is vertical text, not 180 degrees
    sym = sexpr.parse('(symbol "A" (symbol "A_1_1" (text "V" (at 1.27 2.54 900) (effects (font (size 1.27 1.27))))'
                      ' (text "H" (at 0 0 0) (effects (font (size 1.27 1.27))))'
                      ' (pin passive line (at 0 0 0) (length 2.54) (name "a") (number "1"))))')
    texts = {p[3]: p[4] for p in K.convert_symbol([sym])["prims"] if p[0] == "text"}
    assert texts == {"V": 1, "H": 0}, texts


def _k7(text):
    k7 = KP.footprint_kicad7(text)
    t = sexpr.parse(k7, keep_quotes=True)
    return k7, t


def test_pcm_kicad7_footprint_drops_kicad8_only_syntax():
    # an official KiCad footprint (uuid on every item, (remove_unused_layers no) on THT pads) through kicad_export
    t8, _ = X.footprint_tree((FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text(encoding="utf-8"), "USB_C", "lib", [])
    k8 = sexpr.dumps(t8) + "\n"
    assert "(uuid" in k8 and "(remove_unused_layers no)" in k8
    k7, t = _k7(k8)
    assert sexpr.first(t, "version")[1] == "20221018"
    for tok in ("(uuid", "(remove_unused_layers no)", "(keep_end_layers no)", "(fill no)", "(hide yes)", "(unlocked yes)"):
        assert tok not in k7, tok                     # KiCad 7's pcb parser has no such tokens / values
    assert sexpr.pads(sexpr.parse(k8)) == sexpr.pads(sexpr.parse(k7))
    poly = '(fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (stroke (width 0.1) (type solid)) (fill no) (layer "F.SilkS") (uuid "u3"))'
    pad = '(pad "1" thru_hole circle (at 0 0) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask") (remove_unused_layers yes) (uuid "u4"))'
    model = '(model "x.step" (hide yes) (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))'
    k7, t = _k7(f'(footprint "X" (version 20240108) (generator "pcbnew") (generator_version "8.0") (layer "F.Cu") {poly} {pad} {model})')
    assert "(hide yes)" not in k7 and "hide" in sexpr.first(t, "model")
    assert sexpr.first(sexpr.first(t, "fp_poly"), "fill") == ["fill", "none"]
    assert sexpr.first(sexpr.first(t, "pad"), "remove_unused_layers") == ["remove_unused_layers"]


def test_pcm_kicad7_hidden_value_keeps_at_first():
    # KiCad 7's parseFP_TEXT reads the text and then requires (at ...): "(fp_text value "X" hide (at ...))" fails to load
    k8 = ('(footprint "X" (version 20240108) (generator "ecadlib") (layer "F.Cu")\n'
          ' (property "Reference" "REF**" (at 0 -2 0) (layer "F.SilkS") (uuid "u1") (effects (font (size 1 1) (thickness 0.15))))\n'
          ' (property "Value" "X" (at 0 2 0) (unlocked yes) (layer "F.Fab") (hide yes) (uuid "u2") (effects (font (size 1 1) (thickness 0.15))))\n'
          ' (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))')
    k7, t = _k7(k8)
    val = next(f for f in sexpr.find(t, "fp_text") if f[1] == "value")
    ref = next(f for f in sexpr.find(t, "fp_text") if f[1] == "reference")
    assert val[3][0] == "at" and "hide" in val and ref[3][0] == "at" and "hide" not in ref, (val, ref)
    assert "(uuid" not in k7 and "unlocked" not in k7


def test_pcm_kicad7_symbol_description_is_ki_description():
    # KiCad 7 has four mandatory fields and reads a symbol's description only from ki_description
    k8 = ('(kicad_symbol_lib (version 20231120) (generator "ecadlib") (generator_version "8.0")\n'
          '  (symbol "A" (exclude_from_sim no) (property "Description" "10k resistor" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))))')
    t = sexpr.parse(KP.symbol_kicad7(k8))
    props = {p[1]: p[2] for s in sexpr.find(t, "symbol") for p in sexpr.find(s, "property")}
    assert props == {"ki_description": "10k resistor"}, props


def test_kicad_files_read_and_written_as_utf8():
    # KiCad files are UTF-8; Path.read_text()/write_text() without an encoding use the locale's (cp1252 on Windows),
    # where "10kΩ" raises UnicodeEncodeError and kicad_pcm (which reads UTF-8) chokes on the result
    from tests import test_kicad_export as TX
    d = _tmp()
    plan, _, http = TX._plan(d)
    items = json.loads(plan.read_text(encoding="utf-8"))
    items[0]["description"] = "10kΩ ±1% µ"
    plan.write_text(json.dumps(items), encoding="utf-8")
    bad = []
    orig_w, orig_r = pathlib.Path.write_text, pathlib.Path.read_text

    def check(self, kw):
        caller = sys._getframe(2).f_code.co_filename
        if kw.get("encoding") is None and Path(caller).name in ("kicad_export.py", "kicad_import.py"):
            bad.append((Path(caller).name, sys._getframe(2).f_lineno, self.name))

    def w(self, data, *a, **kw):
        check(self, kw)
        return orig_w(self, data, *a, **kw)

    def r(self, *a, **kw):
        check(self, kw)
        return orig_r(self, *a, **kw)
    with mock.patch.object(pathlib.Path, "write_text", w), mock.patch.object(pathlib.Path, "read_text", r):
        rep = X.export(plan, d / "kicad", "lib", http=http, log=lambda *a, **k: None)
    assert not bad, bad
    assert "10kΩ ±1% µ" in (d / "kicad" / "lib.kicad_sym").read_text(encoding="utf-8"), rep["errors"]


def test_action_plugin_finds_part_named_by_the_library_rules():
    # the plan names parts by [naming] (RES-0603-10k, case = "upper" ...): the MPN typed in KiCad is not the part name
    from tests.test_tool_packages import _plugin_module
    mod, _ = _plugin_module()
    d = _tmp()
    (d / "ecadlib").mkdir()
    (d / "ecadlib" / "kicad_export.json").write_text(json.dumps(
        {"parts": [{"part": "RES-0603-10k", "footprint": "R_0603_1608Metric"}], "skipped": [{"part": "LM358DR", "why": "held: pins"}]}))
    (d / "ecadlib-work" / "out").mkdir(parents=True)
    (d / "ecadlib-work" / "out" / "plan.json").write_text(json.dumps(
        [{"name": "RES-0603-10k", "parameters": [{"name": "Manufacturer Part Number", "value": "RC0603FR-0710KL"}]},
         {"name": "LM358DR", "parameters": [{"name": "Manufacturer Part Number", "value": "LM358DR"}]}]))
    assert mod.footprint_for(str(d), "RC0603FR-0710KL") == ("R_0603_1608Metric", None)
    assert mod.footprint_for(str(d), "rc0603fr-0710kl") == ("R_0603_1608Metric", None)
    assert "held" in mod.footprint_for(str(d), "LM358DR")[1]
    assert mod.footprint_for(str(d), "NOPE")[0] is None


if __name__ == "__main__":
    from tests.run_all import main
    sys.exit(main(["test_review_full_kicad"]))
