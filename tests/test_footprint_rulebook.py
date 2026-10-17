"""Offline tests for the footprint rule book (ecadlib.footprint_rules, docs/footprint-rulebook.md). Run:
    python3 -m tests.test_footprint_rulebook

Three kinds of evidence:
  golden    24 footprints from KiCad's official library (tests/fixtures/kicad_golden, CC-BY-SA 4.0; one per family)
            pass with no errors and only the warnings listed in GOLDEN_WARNINGS
  mutants   each golden footprint broken one way at a time; the rule for that defect (and only a known set of
            others) must fire, at the right level
  ours      every footprint ecadlib generates (package specs at densities A/B/C, the standard table) has no
            rule-book errors, and its warnings stay inside OURS_WARNINGS
Set ECADLIB_KICAD_FOOTPRINTS to a kicad-footprints checkout to also run the whole official library (test_corpus)."""
import json, os, re, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, footprint_check as FC, footprint_gen as F, footprint_rules as R, sexpr

GOLD = Path(__file__).parent / "fixtures" / "kicad_golden"
DOC = Path(__file__).parent.parent / "docs" / "footprint-rulebook.md"
SPECS = Path(__file__).parent.parent / "ecadlib" / "package_specs"

# KiCad's own deviations from its rules (real, and harmless): kept visible, not silenced in the checker
GOLDEN_WARNINGS = {
    "SOT-223-3_TabPin2": {"padshape"},                  # 0.375 mm corner radius on 1.5 mm pads (cap is 0.25 mm)
    "CP_Radial_D5.0mm_P2.00mm": {"silk", "courtyard"},  # silk 0.18 mm from the pad; circular courtyard off-grid
    "D_DO-41_SOD81_P10.16mm_Horizontal": {"silk"},
}
# what ecadlib's own footprints may still be warned about, and why (docs/footprint-rulebook.md, "Known deviations")
OURS_WARNINGS = {
    "model",      # the 3D model is attached at export (kicad_export / the PcbLib body), not in the raw generator output
    "copper",     # fine-pitch and 0201/01005 lands below a 0.2 mm mask web: the fab's minimum decides (KiCad's too)
    "silk",       # 0.1 mm silk clearance on tiny parts, as KiCad does
    "padshape",   # SMD pin-header lands are sharp rectangles, as in KiCad's library (bench-matched)
    "pin1",       # Pin1Right headers, EP numbering after the last pin
    "origin",     # TO-252 / TO-263: origin on the lead row centre, as KiCad's library
    "name",       # tiny exposed pads the survey cannot tell from the leads
    "vias",       # the custom-pad example puts paste over a 0.3 mm via on purpose
    "margin",     # small WLCSP courtyard as KiCad's (0.25 mm; KLC's newer rule asks 1.0 mm)
}


def gold(name):
    return (GOLD / f"{name}.kicad_mod").read_text()


def rules_of(issues, level=None):
    return {r for l, r, _ in issues if level is None or l == level}


def run(text, name=None, **kw):
    name = name or R._name(sexpr.parse(text))
    return R.check(text, filename=f"{name}.kicad_mod", **kw)


def rename(text, new):
    """Rename a footprint everywhere its name appears (footprint name, Value, 3D model file)."""
    old = R._name(sexpr.parse(text))
    return text.replace(f'"{old}"', f'"{new}"').replace(f"/{old}.", f"/{new}.")


def expect(text, rule, level="error", name=None, **kw):
    issues = run(text, name, **kw)
    hit = [i for i in issues if i[1] == rule and i[0] == level]
    assert hit, f"expected a {level} '{rule}', got {issues}"
    return hit


# ------------------------------------------------------------------ golden
def test_golden_examples_pass():
    files = sorted(GOLD.glob("*.kicad_mod"))
    assert len(files) == 24
    for f in files:
        issues = R.check(f.read_text(), filename=f.name)
        errs = [i for i in issues if i[0] == "error"]
        assert not errs, (f.name, errs)
        extra = rules_of(issues) - GOLDEN_WARNINGS.get(f.stem, set())
        assert not extra, (f.name, [i for i in issues if i[1] in extra])


def test_survey_reads_golden_facts():
    s = R.survey(gold("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm"))
    assert len(s["pins"]) == 32 and s["ep_nums"] == {"33"} and 0.5 in s["pitches"]
    s = R.survey(gold("QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm_ThermalVias"))
    assert len(s["pins"]) == 24 and s["ep_nums"] == {"25"} and len(s["vias"]) >= 4
    s = R.survey(gold("SOT-223-3_TabPin2"))
    assert s["pins"] == ["1", "2", "3"] and s["big_nums"] == set()
    s = R.survey(gold("JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal"))
    assert s["pins"] == ["1", "2", "3", "4"] and s["mount"] == ["MP"]


# ------------------------------------------------------------------ names
def test_parse_klc_names():
    p = R.parse_name("SOIC-8_3.9x4.9mm_P1.27mm")
    assert p["pins"] == 8 and p["body"] == (3.9, 4.9) and p["pitch"] == (1.27,)
    p = R.parse_name("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm")
    assert p["pins"] == 32 and p["ep"] == 1 and p["ep_size"] == (3.45, 3.45)
    p = R.parse_name("R_0603_1608Metric")
    assert p["family"] == "chip" and p["chip"] == ("0603", "1608")
    p = R.parse_name("SOT-23-5")
    assert p["family"] == "SOT-23" and p["pins"] == 5
    assert R.parse_name("SOT-23").get("pins") is None
    p = R.parse_name("MSOP-12-16_3x4mm_P0.5mm")
    assert p["pins"] == 12 and p["pins_with_gaps"] == 16
    p = R.parse_name("PinHeader_2x05_P2.54mm_Vertical")
    assert p["layout"] == (2, 5) and p["pins"] == 10
    p = R.parse_name("JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    assert p["pins"] == 4 and p["mp"] == 1
    p = R.parse_name("BGA-256_17.0x17.0mm_Layout16x16_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD")
    assert p["layout"] == (16, 16) and p["pad"] == 0.4 and p["body"] == (17.0, 17.0)
    assert R.parse_name("QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm_ThermalVias")["thermal_vias"]
    assert R.parse_name("LED-APA102-2020").get("pins") is None          # 2020 is a size code, not 2020 pins


def test_parse_ipc_names():
    """IPC-7351 names, as Altium's IPC Compliant Footprint Wizard writes them."""
    p = R.parse_name("RESC1608X55N")
    assert p["style"] == "ipc" and p["body"] == (1.6, 0.8) and p["height"] == 0.55 and p["density"] == "N"
    p = R.parse_name("SOIC127P600X175-8N")
    assert p["pitch"] == (1.27,) and p["span"] == (6.0,) and p["height"] == 1.75 and p["pins"] == 8
    p = R.parse_name("QFP50P900X900X160-48N")
    assert p["pins"] == 48 and p["pitch"] == (0.5,) and p["span"] == (9.0, 9.0)
    p = R.parse_name("QFN50P500X500X100-33N")
    assert p["pins_total"] == 33 and p["body"] == (5.0, 5.0)
    p = R.parse_name("BGA256C100P16X16_1700X1700X200")
    assert p["balls"] == 256 and p["layout"] == (16, 16) and p["pitch"] == (1.0,)
    assert R.parse_name("SOT95P280X145-5N")["pins"] == 5


def test_name_pin_count_pitch_body():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    expect(rename(t, "SOIC-14_3.9x4.9mm_P1.27mm"), "name")
    hit = expect(rename(t, "SOIC-8_3.9x4.9mm_P0.65mm"), "name")
    assert "pitch 0.65" in hit[0][2]
    expect(rename(t, "SOIC-8_5.3x5.3mm_P1.27mm"), "name", "warning")
    assert not rules_of(run(rename(t, "SOIC127P600X175-8N")), "error")
    expect(rename(t, "SOIC127P600X175-14N"), "name")
    expect(rename(t, "SOIC65P600X175-8N"), "name")


def test_name_exposed_pad_and_vias():
    t = gold("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm")
    expect(rename(t, "QFN-32-2EP_5x5mm_P0.5mm_EP3.45x3.45mm"), "name")
    expect(rename(t, "QFN-32_5x5mm_P0.5mm"), "name", "warning")              # EP present, no -1EP
    expect(rename(t, "QFN-32-1EP_5x5mm_P0.5mm_EP2.5x2.5mm"), "name", "warning")
    expect(rename(t, "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm_ThermalVias"), "name", "warning")
    assert not rules_of(run(rename(t, "QFN50P500X500X100-33N")), "error")
    expect(rename(t, "QFN50P500X500X100-32N"), "name")
    v = gold("QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm_ThermalVias")
    expect(rename(v, "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm"), "name", "warning")


def test_name_chip_codes():
    t = gold("R_0603_1608Metric")
    hit = expect(rename(t, "R_0603_1005Metric"), "name")
    assert "1608" in hit[0][2]
    expect(rename(t, "R_1206_3216Metric"), "name", "warning")                # 3216 body but a 1608 outline
    assert not rules_of(run(rename(t, "RESC1608X55N")), "error")


def test_name_connectors_and_bga():
    t = gold("PinHeader_1x04_P2.54mm_Vertical")
    expect(rename(t, "PinHeader_1x05_P2.54mm_Vertical"), "name")
    expect(rename(t, "PinHeader_1x04_P2.00mm_Vertical"), "name")
    j = gold("JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    expect(rename(j, "JST_SH_SM04B-SRSS-TB_1x04-2MP_P1.00mm_Horizontal"), "name")
    b = gold("BGA-256_17.0x17.0mm_Layout16x16_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD")
    expect(rename(b, "BGA-256_17.0x17.0mm_Layout16x15_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD"), "name")
    expect(rename(b, "BGA-256_17.0x17.0mm_Layout16x16_P0.8mm_Ball0.5mm_Pad0.4mm_NSMD"), "name")
    expect(rename(b, "BGA-256_17.0x17.0mm_Layout16x16_P1.0mm_Ball0.5mm_Pad0.3mm_NSMD"), "name")
    expect(rename(b, "BGA-255_17.0x17.0mm_Layout16x16_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD"), "name")
    assert not rules_of(run(rename(b, "BGA256C100P16X16_1700X1700X200")), "error")


# ------------------------------------------------------------------ metadata, type, origin
def test_meta():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    expect(t, "meta", name="SOIC-8")                                          # file name differs from the name
    expect(t.replace('"Value" "SOIC-8_3.9x4.9mm_P1.27mm"', '"Value" "SO8"'), "meta", "warning")
    expect(t.replace('"Reference" "REF**"', '"Reference" "U1"'), "meta", "warning")
    expect(re.sub(r'\(descr "[^"]*"\)', '(descr "")', t), "meta", "warning")
    expect(re.sub(r'\(tags "[^"]*"\)', "", t), "meta", "warning")
    expect(rename(t, "SOIC 8 narrow"), "meta", "warning")


def test_attr():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    hit = expect(t.replace("(attr smd)", ""), "attr")
    assert "pick-and-place" in hit[0][2]
    expect(t.replace("(attr smd)", "(attr smd exclude_from_pos_files)"), "attr")
    expect(t.replace("(attr smd)", "(attr smd exclude_from_bom)"), "attr", "warning")
    expect(gold("DIP-8_W7.62mm").replace("(attr through_hole)", ""), "attr", "warning")


def shift(text, dx, dy=0.0):
    """Move every point of a footprint by (dx, dy): the origin is then off the body."""
    def mv(m):
        return f"({m.group(1)} {float(m.group(2)) + dx:.4f} {float(m.group(3)) + dy:.4f}"
    return re.sub(r"\((at|start|end|mid|center|xy) (-?[\d.]+) (-?[\d.]+)", mv, text)


def test_origin():
    expect(shift(gold("SOIC-8_3.9x4.9mm_P1.27mm"), 1.0), "origin", "warning")
    assert "origin" not in rules_of(run(shift(gold("SOIC-8_3.9x4.9mm_P1.27mm"), 0.02)))
    expect(shift(gold("DIP-8_W7.62mm"), -1.0), "origin", "warning")           # THT: pin 1 is the origin


# ------------------------------------------------------------------ pads
def pad_block(text, num):
    m = re.search(r'\(pad "%s" .*?\n\t\)' % re.escape(num), text, re.S)
    return m.group(0)


def edit_pad(text, num, fn):
    b = pad_block(text, num)
    return text.replace(b, fn(b), 1)


def test_layers():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    expect(edit_pad(t, "1", lambda b: b.replace(' "F.Paste"', "")), "layers")
    hit = expect(edit_pad(t, "1", lambda b: b.replace(' "F.Mask"', "")), "layers")
    assert "solder-mask" in hit[0][2]
    expect(edit_pad(t, "1", lambda b: b.replace('"F.Cu"', '"F.Cu" "F.SilkS"')), "layers")
    expect(edit_pad(t, "1", lambda b: b.replace('"F.Cu"', '"F.Cu" "B.Cu"')), "layers")
    d = gold("DIP-8_W7.62mm")
    expect(edit_pad(d, "2", lambda b: b.replace(' "*.Mask"', "")), "layers")
    expect(edit_pad(d, "2", lambda b: b.replace('"*.Mask"', '"*.Mask" "F.Paste"')), "layers", "warning")
    q = gold("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm")                      # EP paste is in separate windows: fine
    assert "layers" not in rules_of(run(q))


def test_padshape():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    expect(t.replace("smd roundrect", "smd rect").replace("(roundrect_rratio 0.25)", ""), "padshape", "warning")
    expect(t.replace("(roundrect_rratio 0.25)", "(roundrect_rratio 0.5)"), "padshape", "warning")
    d = gold("DIP-8_W7.62mm")
    expect(edit_pad(d, "1", lambda b: re.sub(r"thru_hole \w+", "thru_hole oval", b)), "padshape", "warning")
    expect(edit_pad(d, "3", lambda b: re.sub(r"thru_hole \w+", "thru_hole rect", b)), "padshape", "warning")


def test_tht_annular_ring():
    d = gold("DIP-8_W7.62mm")
    expect(edit_pad(d, "2", lambda b: re.sub(r"\(size [\d.]+ [\d.]+\)", "(size 1 1)", b)), "tht", "warning")   # 0.1 mm
    expect(edit_pad(d, "2", lambda b: re.sub(r"\(size [\d.]+ [\d.]+\)", "(size 0.8 0.8)", b)), "tht")         # 0 mm


def test_thermal_vias():
    v = gold("QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm_ThermalVias")
    assert not rules_of(run(v))
    first_via = re.search(r'\(pad "25" thru_hole', v).start()
    bad = v[:first_via] + '(pad "1" thru_hole' + v[first_via + len('(pad "25" thru_hole'):]
    hit = expect(bad, "vias")
    assert "share a number" in hit[0][2]
    expect(re.sub(r"\(drill 0\.2\)", "(drill 0.45)", v), "vias", "warning")


# ------------------------------------------------------------------ text, 3D, courtyard, polarity, graphics
def test_text():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    ref = re.search(r'\(property "Reference".*?\n\t\)', t, re.S).group(0)
    expect(t.replace(ref, ref.replace('"F.SilkS"', '"F.Fab"')), "text", "warning")
    expect(t.replace(ref, ref.replace("(size 1 1)", "(size 0.6 0.6)")), "text", "warning")
    expect(t.replace('"${REFERENCE}"', '"%V"'), "text", "warning")
    fab = re.search(r'\(fp_text user "\$\{REFERENCE\}".*?\n\t\)', t, re.S).group(0)
    expect(t.replace(fab, fab.replace("(thickness 0.15)", "(thickness 0.4)")), "text", "warning")


def test_model():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    m = re.search(r"\(model .*?\n\t\)", t, re.S).group(0)
    expect(t.replace(m, ""), "model", "warning")
    expect(t.replace(m, ""), "model", "error", require_model=True)
    expect(t.replace("${KICAD10_3DMODEL_DIR}", "C:/Users/me/models"), "model")
    expect(t.replace(".step\"", ".wrl\""), "model", "warning")
    expect(t.replace("SOIC-8_3.9x4.9mm_P1.27mm.step", "TSSOP-8.step"), "model", "warning")
    expect(t.replace(m, m.replace("(xyz 1 1 1)", "(xyz 2 2 2)")), "model", "warning")
    expect(t.replace(m, m.replace("(rotate\n\t\t\t(xyz 0 0 0)", "(rotate\n\t\t\t(xyz 0 0 90)")), "model", "warning")
    th = gold("QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm_ThermalVias")              # _ThermalVias shares the plain model
    assert "model" not in rules_of(run(th))


def test_courtyard_margin():
    t, _ = F.build(json.loads((SPECS / "R_0603_1608Metric.json").read_text()))
    assert "margin" not in rules_of(run(t))
    assert "(fp_rect (start -1.48 -0.73) (end 1.48 0.73)" in t
    tight = t.replace("(fp_rect (start -1.48 -0.73) (end 1.48 0.73)", "(fp_rect (start -1.26 -0.52) (end 1.26 0.52)")
    expect(tight, "margin", "warning")
    b = gold("BGA-256_17.0x17.0mm_Layout16x16_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD")
    assert "margin" not in rules_of(run(b))                                    # 1.0 mm around a BGA


def test_polarity_marks():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    tri = re.search(r'\(fp_poly\s+\(pts\s+\(xy -2\.6 -2\.47\).*?\(layer "F.SilkS"\)\s*\)', t, re.S).group(0)
    no_tri = t.replace(tri, "")
    sym = re.sub(r'\(fp_line\s+\(start -2\.06 -2\.465\)\s+\(end -2\.06 -2\.56\)', '(fp_line (start -2.06 -2.465) (end -2.06 -2.56)', no_tri)
    issues = run(sym)
    msgs = [m for l, r, m in issues if r == "polarity"]
    assert any("silkscreen" in m for m in msgs), issues
    fab = re.search(r'\(fp_poly\s+\(pts\s+\(xy -0\.975 -2\.45\).*?\(layer "F.Fab"\)\s*\)', t, re.S).group(0)
    box = '(fp_rect (start -1.95 -2.45) (end 1.95 2.45) (stroke (width 0.1) (type solid)) (fill no) (layer "F.Fab"))'
    assert any("fab" in m for l, r, m in run(t.replace(fab, box)) if r == "polarity")
    assert "polarity" not in rules_of(run(gold("R_0603_1608Metric")))         # resistors have no pin 1
    d = gold("D_SOD-123")
    assert "polarity" not in rules_of(run(d))


def test_duplicate_graphics():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    line = re.search(r'\(fp_line\s+\(start -2\.06 -2\.56\).*?\n\t\)', t, re.S).group(0)
    expect(t.replace(line, line + "\n\t" + line), "graphics", "warning")


# ------------------------------------------------------------------ connectivity
def test_connect_pins_and_pads():
    t = gold("SOIC-8_3.9x4.9mm_P1.27mm")
    pins = {str(i): f"P{i}" for i in range(1, 9)}
    assert not rules_of(run(t, symbol_pins=pins))
    hit = expect(t, "connect", symbol_pins={k: v for k, v in pins.items() if k != "8"})
    assert "pads without a symbol pin: 8" in hit[0][2]
    expect(t, "connect", symbol_pins={**pins, "9": "EP"})
    q = gold("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm")
    hit = expect(q, "connect", symbol_pins={str(i): "x" for i in range(1, 33)})
    assert "float" in hit[0][2]
    hit = expect(q, "connect", symbol_pins={**{str(i): "x" for i in range(1, 33)}, "EP": "GND"})
    assert any("numbered 33" in m for _, _, m in hit)
    assert not rules_of(run(q, symbol_pins={str(i): "x" for i in range(1, 34)}), "error")
    j = gold("JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")                # MP pads need no pin
    assert "connect" not in rules_of(run(j, symbol_pins=["1", "2", "3", "4"]))


def test_pinmap_polarity():
    for name in ("D_SOD-123", "D_SMA", "LED_0603_1608Metric", "D_DO-41_SOD81_P10.16mm_Horizontal"):
        t = gold(name)
        assert "pinmap" not in rules_of(run(t, symbol_pins={"1": "K", "2": "A"})), name
        hit = expect(t, "pinmap", symbol_pins={"1": "A", "2": "K"})
        assert "backwards" in hit[0][2]
    for name in ("CP_EIA-3216-18_Kemet-A", "CP_Radial_D5.0mm_P2.00mm"):
        t = gold(name)
        assert "pinmap" not in rules_of(run(t, symbol_pins={"1": "+", "2": "-"}))
        expect(t, "pinmap", symbol_pins={"1": "-", "2": "+"})
    assert "pinmap" not in rules_of(run(gold("R_0603_1608Metric"), symbol_pins={"1": "A", "2": "K"}))


# ------------------------------------------------------------------ ecadlib's own footprints
def _ours():
    from ecadlib import footprint_adv as A, stdparts as SP
    for p in sorted(SPECS.glob("*.json")) + sorted((SPECS / "advanced").glob("*.json")):
        spec = json.loads(p.read_text())
        for d in ("A", "B", "C"):
            build = A.build if spec.get("package_type") in A.BUILDERS else F.build
            yield f"{p.stem}@{d}", build(spec, d)[0]
    for n in sorted(SP.packages())[::3]:
        yield f"std:{n}", SP.build(n)[0]


def test_ecadlib_footprints_follow_the_rulebook():
    n = 0
    for label, text in _ours():
        n += 1
        issues = run(text)
        errs = [i for i in issues if i[0] == "error"]
        assert not errs, (label, errs)
        extra = rules_of(issues) - OURS_WARNINGS
        assert not extra, (label, [i for i in issues if i[1] in extra])
    assert n > 350


def test_ours_are_clean_on_the_core_families():
    """The KiCad-generator families (chips, gull-wing, QFN, SOT) at density B: no findings at all except the 3D model
    the export attaches."""
    for name in ("R_0603_1608Metric", "C_0805_2012Metric", "LED_0603_1608Metric", "SOIC-8_3.9x4.9mm_P1.27mm",
                 "TSSOP-16_4.4x5mm_P0.65mm", "LQFP-48_7x7mm_P0.5mm", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm", "SOT-23-5",
                 "D_SOD-123", "CP_EIA-3216-18_Kemet-A", "MSOP-8_3x3mm_P0.65mm"):
        t, _ = F.build(json.loads((SPECS / f"{name}.json").read_text()), "B")
        assert rules_of(run(t)) <= {"model"}, (name, run(t))


def test_pcblib_rules():
    spec = json.loads((SPECS / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "x.PcbLib"
        AL.write_pcblib(f, [AL.footprint_from_spec(spec)[0]])
        fp = AL.read_pcblib(f)[0]
        assert not [i for i in R.check_pcblib(fp) if i[0] == "error"], R.check_pcblib(fp)
        fp["records"] = [dict(r, layer=AL.MULTI) if r["type"] == "pad" and r["designator"] == "1" else r for r in fp["records"]]
        assert any("Top Layer" in m for _, _, m in R.check_pcblib(fp))
        fp["records"] = [r for r in fp["records"] if not (r["type"] in ("track", "arc", "region") and r.get("layer") == AL.MECH15)]
        assert any("courtyard" in m for l, _, m in R.check_pcblib(fp) if l == "error")


def test_release_gate_holds_a_reversed_diode():
    from ecadlib import kicad_import as K, release_check as RC
    from tests.test_release_check import PARAMS
    kmod = gold("D_SOD-123")
    with tempfile.TemporaryDirectory() as d:
        m = Path(d) / "models"
        for s in ("SchLib", "PcbLib", "KiCad"):
            (m / s).mkdir(parents=True)
        fp, t = K.convert_footprint(kmod, "D_SOD-123")
        K.add_body(fp, t, None, 1.1)
        AL.write_pcblib(m / "PcbLib" / "D_SOD-123.PcbLib", [fp])
        (m / "KiCad" / "D_SOD-123.kicad_mod").write_text(kmod)

        def item(names):
            sym = AL.ic_symbol("D1", [{"num": k, "name": v, "type": "passive", "side": "left"} for k, v in names.items()],
                               footprint="D_SOD-123")
            AL.write_schlib(m / "SchLib" / "D1.SchLib", [sym])
            return {"name": "D1", "folder": "Diodes", "description": "diode", "comment": "D1",
                    "parameters": [dict(p) for p in PARAMS], "cleared": [], "symbol": {"ref": "D1", "file": "models/SchLib/D1.SchLib"},
                    "footprint": {"ref": "D_SOD-123", "file": "models/PcbLib/D_SOD-123.PcbLib",
                                  "kicad_mod": "models/KiCad/D_SOD-123.kicad_mod"},
                    "status": "ready", "source": {"symbol": "local:x", "footprint": "kicad:Diode_SMD:D_SOD-123"}}
        fails, warns, _ = RC.check_item(item({"1": "K", "2": "A"}), d)
        assert not [f for f in fails if f[0] == "footprint"], fails
        fails, _, _ = RC.check_item(item({"1": "A", "2": "K"}), d)
        assert any(r == "footprint" and "pinmap" in msg for r, msg in fails), fails


def test_release_gate_holds_a_misnamed_footprint():
    from tests.test_release_check import make
    t = rename(gold("SOIC-8_3.9x4.9mm_P1.27mm"), "SOIC-14_3.9x4.9mm_P1.27mm")
    from ecadlib import release_check as RC
    with tempfile.TemporaryDirectory() as d:
        it = make(d, kmod=t, fp_name="SOIC-14_3.9x4.9mm_P1.27mm")
        fails, _, _ = RC.check_item(it, d)
        assert any("name:" in m for r, m in fails if r == "footprint"), fails
        it = make(d, kmod=t, fp_name="SOIC-14_3.9x4.9mm_P1.27mm", source="kicad:Package_SO:SOIC-8")
        fails, warns, _ = RC.check_item(it, d)                                 # KiCad's own parts: reported, not held
        assert not any("name:" in m for _, m in fails) and any("name:" in w for w in warns)


# ------------------------------------------------------------------ book, CLI, corpus
def test_rulebook_doc_covers_every_rule():
    doc = DOC.read_text()
    for rule in R.RULES:
        assert f"`{rule}`" in doc, rule
    for rule in re.findall(r"^### `(\w+)`", doc, re.M):
        assert rule in R.RULES or rule in ("copper", "silk", "fab", "courtyard", "pin1", "paste", "bga"), rule


def test_cli_summary_on_the_golden_library(capsys=None):
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = R.main([str(GOLD), "--summary"])
    assert rc == 0 and "24 footprints, 0 with errors" in buf.getvalue(), buf.getvalue()
    bad = Path(tempfile.mkdtemp()) / "SOIC-8.kicad_mod"
    bad.write_text(gold("SOIC-8_3.9x4.9mm_P1.27mm").replace("(attr smd)", ""))
    with contextlib.redirect_stdout(io.StringIO()):
        assert R.main([str(bad)]) == 1


def test_corpus():
    """The whole official KiCad library (opt-in): nothing crashes, and errors stay rare on the generator-built
    families. ECADLIB_KICAD_FOOTPRINTS=/path/to/kicad-footprints"""
    root = os.environ.get("ECADLIB_KICAD_FOOTPRINTS")
    if not root:
        return
    files = sorted(Path(root).glob("*.pretty/*.kicad_mod"))
    assert files
    core = ("Resistor_SMD", "Capacitor_SMD", "Package_SO", "Package_QFP", "Package_DFN_QFN", "Package_BGA")
    n = bad = 0
    for f in files:
        issues = R.check(f.read_text(encoding="utf-8"), filename=f.name, geometry=False)
        if f.parent.stem in core:
            n += 1
            bad += any(l == "error" for l, _, _ in issues)
    assert bad <= 0.03 * n, (bad, n)


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_footprint_rulebook"]))
