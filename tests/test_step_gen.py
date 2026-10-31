"""Offline tests for ecadlib.step_gen (package-shaped STEP models) and its use in kicad_import / librarian.
Run:  python3 -m tests.test_step_gen

Every package spec in ecadlib/package_specs and a list of footprint names are generated and checked with
step_gen.check (references resolve, closed shells with consistently oriented edges, planar faces, positive
volume, colours), the bounding box is compared with the spec dimensions computed here, the height with
release_check.HEIGHTS, and each lead with the pad of the same number in footprint_gen's footprint."""
import json, re, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, footprint_gen as F, kicad_import as K, release_check as RC, step_gen as S

SPEC_FILES = sorted(S.SPECS.glob("*.json"))
NAMES = ["R_0201_0603Metric", "C_1210_3225Metric", "L_1206_3216Metric", "LED_1206_3216Metric", "CP_EIA-7343-31_Kemet-D",
         "D_SMC", "D_SOD-523", "SOT-23-6", "SOT-89-3", "SOT-323_SC-70", "SOT-363_SC-70-6", "SOIC-8_5.3x5.3mm_P1.27mm",
         "TSSOP-20_4.4x6.5mm_P0.65mm", "SSOP-28_5.3x10.2mm_P0.65mm", "MSOP-10_3x3mm_P0.5mm", "QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm",
         "DFN-6-1EP_2x2mm_P0.65mm_EP1x1.6mm", "TQFP-44_10x10mm_P0.8mm", "Package_QFP:LQFP-64_10x10mm_P0.5mm",
         "PinHeader_1x04_P2.54mm_Vertical", "PinHeader_2x05_P1.27mm_Vertical", "CP_Radial_D5.0mm_P2.00mm",
         "C_Radial_D5.0mm_H7.0mm_P2.00mm", "R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", "D_DO-41_SOD81_P10.16mm_Horizontal"]
nom = lambda d: F.dim(d)[1]


def _expected_xy(spec):
    """Half extents (x, y) of the model from the spec, worked out independently of step_gen."""
    t = spec["package_type"]
    if t in ("chip", "molded"):
        return nom(spec["body_length"]) / 2, nom(spec["body_width"]) / 2
    if t == "qfn":
        return nom(spec["body_length"]) / 2, nom(spec["body_width"]) / 2
    if t == "qfp":
        return nom(spec["lead_span"]) / 2, nom(spec.get("lead_span_y") or spec["lead_span"]) / 2
    b, D = nom(spec["terminal_width"]), nom(spec["body_length"])
    ys = [abs(p["offset"]) + b / 2 for p in spec.get("pin_positions", [])]
    if spec.get("tab"):
        ys.append(abs(spec["tab"].get("offset", 0)) + nom(spec["tab"]["width"]) / 2)
    if not spec.get("pin_positions"):
        ys.append((spec["pin_count"] // 2 - 1) * spec["pitch"] / 2 + b / 2)
    return nom(spec["lead_span"]) / 2, max([D / 2] + ys)


def _ok(text, name):
    r = S.check(text)
    assert not r["errors"], (name, r["errors"][:5])
    assert r["solids"] >= 2 and not r["unstyled"], (name, r["solids"], r["unstyled"])
    assert all(p["volume"] > 0 for p in r["per_solid"]), name
    return r


def test_every_package_spec():
    assert len(SPEC_FILES) >= 20
    for f in SPEC_FILES:
        spec = json.loads(f.read_text())
        text, info = S.generate(f)
        r = _ok(text, spec["name"])
        x0, y0, z0, x1, y1, z1 = r["bbox"]
        hx, hy = _expected_xy(spec)
        assert abs(x1 - hx) < 1e-4 and abs(x0 + hx) < 1e-4, (spec["name"], r["bbox"], hx)
        assert abs(y1 - hy) < 1e-4 and abs(y0 + hy) < 1e-4, (spec["name"], r["bbox"], hy)
        assert abs(z0) < 1e-9 and abs(z1 - info["height"]) < 1e-6, (spec["name"], r["bbox"])
        if spec.get("height_max"):
            assert info["height"] == spec["height_max"]
        lo, hi, rx = RC.height_range(spec["name"])
        assert rx and lo <= z1 <= hi, (spec["name"], z1, lo, hi)
        assert K.step_extent(text, {"scale": [1, 1, 1], "rotate": [0, 0, 0], "offset": [0, 0, 0]}) == r["bbox"]


def test_footprint_names():
    for n in NAMES:
        text, info = S.generate(n)
        r = _ok(text, n)
        lo, hi, rx = RC.height_range(n.split(":")[-1])
        assert lo <= r["bbox"][5] <= hi, (n, r["bbox"], lo, hi)
    assert S.spec_from_name("Foo_Bar") is None
    try:
        S.generate("Foo_Bar")
        raise AssertionError("unknown family accepted")
    except ValueError:
        pass
    assert S.generate("CP_EIA-3216-18_Kemet-A")[1]["height"] == 1.8              # height from the EIA suffix
    assert S.generate("SOIC-8_3.9x4.9mm_P1.27mm", 1.5)[1]["bbox"][5] == 1.5       # caller height wins


EASYEDA = ["SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL", "SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL", "SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR",
           "SOT-23-5_L3.0-W1.7-P0.95-LS2.8-BR", "TSSOP-20_L6.5-W4.4-P0.65-LS6.4-BL", "ESOP-8_L4.9-W3.9-P1.27-LS6.0-BL-EP",
           "LQFP-48_L7.0-W7.0-P0.50-LS9.0-BL", "QFN-32_L5.0-W5.0-P0.50-BL-EP3.4", "DFN-8_L3.0-W3.0-P0.65-BL-EP1.6x2.4",
           "R0603", "C0402", "LED0603-RD", "R1206"]


def test_easyeda_footprint_names():
    """EasyEDA / LCSC footprint names (the 'Add part from MPN' path): dimensions read from the name, family height,
    and the leads sit on the pads of the real EasyEDA footprints in tests/fixtures/easyeda*."""
    from ecadlib import easyeda_convert as EC
    for n in EASYEDA:
        text, info = S.generate(n)
        r = _ok(text, n)
        x0, y0, _, x1, y1, z1 = r["bbox"]
        m = S.EASYEDA.match(n)
        if m and m.group(6):                                   # rows along x: lead span along y, L along x (not QFP: leads)
            assert abs(y1 - y0 - float(m.group(6))) < 1e-6 and ("QFP" in n or abs(x1 - x0 - float(m.group(3))) < 0.5), (n, r["bbox"])
        assert z1 == S.default_height(S.spec_from_name(n)) > 0, (n, z1)
    assert S.generate("SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL")[1]["height"] == 1.75 and S.generate("R0603")[1]["height"] == 0.45
    assert S.spec_from_name("R0603")["body_length"]["nom"] == 1.6 and S.spec_from_name("C0805")["body_width"]["nom"] == 1.2
    assert S.spec_from_name("R9999") is None and S.spec_from_name("SOT-89-3_L4.5-W2.5-P1.50-LS4.2-BR") is None
    seen = set()
    for f in sorted(Path(__file__).parent.glob("fixtures/easyeda*/*.json")):
        pkg = (json.loads(f.read_text()).get("result") or {}).get("packageDetail") or {}
        name = pkg.get("title") or ""
        if not S.EASYEDA.match(name) or name in seen:
            continue
        seen.add(name)
        fp, t = K.convert_footprint(EC.footprint_to_kicad(pkg.get("dataStr") or {}, name))
        boxes = _solid_boxes(S.build(S.spec_from_name(name))[0])
        for p in fp["pads"]:
            b = boxes[f"pin {p['designator']}"]
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            assert abs(cx - p["x"]) < 1e-6 and abs(cy - p["y"]) < p["h"] / 2 + 0.6, (name, p["designator"], (p["x"], p["y"]), b)
        notes = []                                             # kicad_import: a generated STEP, not the extruded box
        K.add_body(fp, t, None, None, False, notes, gen_step=True)
        assert fp["bodies"][0].get("step") and "generated STEP (gullwing" in notes[-1], notes
    assert {"SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL", "SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR"} <= seen, seen


def test_easyeda_names_that_are_not_two_gullwing_rows():
    """LCSC's DIP-8 (through-hole), TO-252-2 (two leads and a tab on the other side) and SOT-223-4 names have the same
    L-W-P-LS shape as an SOIC; they made an SMD gull-wing model with a lead row where the tab is. No model now.
    TSOT-23-5 / SC-70-6 read like SOT-23-5 / SOT-363 and have a family height."""
    for n in ("DIP-8_L9.8-W6.6-P2.54-LS7.6-BL", "TO-252-2_L6.5-W6.1-P4.58-LS9.9-BR", "TO-263-2_L10.0-W9.1-P5.08-LS15.3-BR",
              "SOT-223-4_L6.5-W3.5-P2.30-LS7.0-BR"):
        assert S.spec_from_name(n) is None, n
    for n, h in (("TSOT-23-5_L2.9-W1.6-P0.95-LS2.8-BR", 1.0), ("SC-70-6_L2.0-W1.3-P0.65-LS2.1-BL", 0.95)):
        text, info = S.generate(n)
        _ok(text, n)
        x0, y0, _, x1, y1, z1 = info["bbox"]
        m = S.EASYEDA.match(n)
        assert abs(y1 - y0 - float(m.group(6))) < 1e-6 and abs(x1 - x0 - float(m.group(3))) < 1e-6 and z1 == h, (n, info["bbox"])
        assert sum(1 for s in S.build(S.spec_from_name(n))[0] if re.fullmatch(r"pin \d+", s.name)) == int(m.group(2))


def _centres(solids, rx=r"(pin|terminal|tab) \S+"):
    return {s.name: ((min(v[0] for v in s.verts) + max(v[0] for v in s.verts)) / 2, (min(v[1] for v in s.verts) + max(v[1] for v in s.verts)) / 2)
            for s in solids if re.fullmatch(rx, s.name)}


def test_model_turned_to_the_pads():
    """2N7002 (LCSC C8545) has the same footprint name as C2002, SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR, but its pads are drawn
    with the pin row along y: 1 (1.0, -0.95), 2 (1.0, 0.95), 3 (-1.0, 0). The model came out turned 90 degrees from
    the name alone (x +-1.45, y +-1.2, leads across the pads); with the pads it is turned so each lead sits on its pad."""
    from ecadlib import easyeda_convert as EC
    n = "SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR"
    pads = [{"designator": "1", "x": 1.0, "y": -0.95, "w": 0.9, "h": 0.6, "rot": 0.0},
            {"designator": "2", "x": 1.0, "y": 0.95, "w": 0.9, "h": 0.6, "rot": 0.0},
            {"designator": "3", "x": -1.0, "y": 0.0, "w": 0.9, "h": 0.6, "rot": 0.0}]
    text, info = S.generate(n, pads=pads)
    x0, y0, _, x1, y1, _ = info["bbox"]
    assert info["rotation"] == 180 and abs(x1 - x0 - 2.4) < 1e-6 and abs(y1 - y0 - 2.9) < 1e-6, info   # body length along the pin row (y)
    c = _centres(S.build(dict(S.spec_from_name(n), rotation=info["rotation"]))[0])
    assert len([k for k in c if k.startswith("pin")]) == 3
    for p in pads:
        cx, cy = c[f"pin {p['designator']}"]
        assert abs(cy - p["y"]) < 1e-6 and abs(cx - p["x"]) < 0.6 and cx * p["x"] > 0, (p, c)
    assert S.generate(n)[1]["rotation"] == 90                     # no pads: the name's convention (C2002: pin rows along x)
    # through kicad_import.add_body: C2002's real footprint, and the same turned to C8545's layout
    pkg = json.loads((Path(__file__).parent / "fixtures/easyeda/C2002.json").read_text())["result"]["packageDetail"]
    for turn in (False, True):
        fp, t = K.convert_footprint(EC.footprint_to_kicad(pkg["dataStr"], n))
        if turn:
            for p in fp["pads"]:
                p["x"], p["y"], p["w"], p["h"] = -p["y"], p["x"], p["h"], p["w"]
        K.add_body(fp, t, None, None, False, [], gen_step=True)
        x0, y0, _, x1, y1, _ = S.check(fp["bodies"][0]["step"]["data"].decode())["bbox"]
        rows_x = len({round(p["y"], 3) for p in fp["pads"]}) == 2               # pin row axis from the pads
        assert (x1 - x0 > y1 - y0) == rows_x and abs(min(x1 - x0, y1 - y0) - 2.4) < 1e-6, (turn, (x0, y0, x1, y1))
    # a model that already sits on its pads keeps its rotation (every package spec with its own footprint_gen pads,
    # the BGAs' balls on their lands too): 0, never None, which would pass with no lead checked (reviewer 2026-10-31)
    for f in SPEC_FILES + sorted((S.SPECS / "advanced").glob("*.json")):
        spec = json.loads(f.read_text())
        fp, _ = K.convert_footprint(F.build(spec)[0])
        assert S.fit_rotation(spec, fp["pads"]) == 0, spec["name"]
    bga = json.loads((S.SPECS / "advanced" / "BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD.json").read_text())
    fp, _ = K.convert_footprint(F.build(bga)[0])
    off = [dict(p, x=p["x"] + 1.0) for p in fp["pads"]]                 # every ball one pitch off its land
    assert S.fit_rotation(bga, off, checked=True) == S.NO_FIT
    turned = [dict(p, x=-p["y"], y=p["x"]) for p in fp["pads"]]         # the footprint drawn a quarter turn round
    assert S.fit_rotation(bga, turned) == 90
    assert S.fit_rotation(S.spec_from_name(n), [dict(p, designator="9") for p in pads]) is None           # no pad numbers: no guess


def test_easyeda_names_without_pin_count_sot223_sod():
    """LCSC names step_gen had no model for (AO3401A, AMS1117, 1N4148W): SOT-23 without a pin count is SOT-23-3, SOT-223(-3)
    is 3 leads in one row and the tab opposite, SOD-123F is a flat-lead SOD-123, plain SOD-123 is KiCad's D_SOD-123."""
    for fam, k in (("SOT-23", 3), ("SOT-323", 3), ("SOT-353", 5), ("SOT-363", 6), ("SOT-563", 6)):
        n = f"{fam}_L2.0-W1.25-P0.65-LS2.1-BR"
        s = S.spec_from_name(n)
        assert s and s["pin_count"] == k and s["name"] == n, (n, s)
        assert sum(1 for x in S.build(s, 1.0)[0] if re.fullmatch(r"pin \d+", x.name)) == k, n
    n = "SOT-23_L2.9-W1.3-P1.90-LS2.4-BR"
    assert {k: v for k, v in S.spec_from_name(n).items() if k != "name"} == \
        {k: v for k, v in S.spec_from_name("SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR").items() if k != "name"}
    text, info = S.generate(n)
    _ok(text, n)
    x0, y0, _, x1, y1, z1 = info["bbox"]
    assert abs(x1 - x0 - 2.9) < 1e-6 and abs(y1 - y0 - 2.4) < 1e-6 and z1 == 1.1, info["bbox"]
    assert S.spec_from_name("SOT-999_L2.0-W1.25-P0.65-LS2.1-BR") is None and S.spec_from_name("SOT-223-4_L6.5-W3.5-P2.30-LS7.0-BR") is None
    for n, W in (("SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR", 3.4), ("SOT-223_L6.5-W3.5-P2.30-LS7.0-BR", 3.5)):
        text, info = S.generate(n)
        _ok(text, n)
        x0, y0, _, x1, y1, z1 = info["bbox"]
        assert abs(x1 - x0 - 6.5) < 1e-6 and abs(y1 - y0 - 7.0) < 1e-6 and z1 == 1.8, (n, info["bbox"])
        c = _centres(S.build(S.spec_from_name(n))[0])
        pins, tabs = [c[f"pin {i}"] for i in (1, 2, 3)], [v for k, v in c.items() if k.startswith("tab")]
        assert len(c) == 4 and len(tabs) == 1 and len({round(y, 6) for _, y in pins}) == 1, c           # 3 leads in one row + tab
        assert [round(x, 6) + 0.0 for x, _ in pins] == [-2.3, 0.0, 2.3] and pins[0][1] < 0 < tabs[0][1] and abs(tabs[0][0]) < 1e-6, c
        body = next(s for s in S.build(S.spec_from_name(n))[0] if s.name == "body")
        assert abs(max(v[1] for v in body.verts) - W / 2) < 1e-6
    for n, LS, h in (("SOD-123F_L2.7-W1.6-LS3.8-RD", 3.8, 1.0), ("SOD-123", 3.7, 1.17)):
        s = S.spec_from_name(n)
        text, info = S.generate(n)
        _ok(text, n)
        x0, y0, _, x1, y1, z1 = info["bbox"]
        assert s["pin_count"] == 2 and abs(x1 - x0 - LS) < 1e-6 and abs(y1 - y0 - 1.6) < 1e-6 and z1 == h, (n, info["bbox"])
        c = _centres(S.build(s)[0])
        assert sorted(c) == ["pin 1", "pin 2"] and c["pin 1"][0] < 0 < c["pin 2"][0] and abs(c["pin 1"][1]) < 1e-9, c
    assert S.spec_from_name("SOD-123F_L2.7-W1.6-LS3.8-RD")["lead_type"] == "flat"
    assert S.spec_from_name("SOD-999F_L2.7-W1.6-LS3.8-RD") is None and S.spec_from_name("SOD-123F_L2.7-W1.6-LS2.5-RD") is None
    # with EasyEDA-style pads (SOT-223: leads along x at the bottom, tab pad 4 on top) the leads sit on their pads
    pads = [{"designator": str(i + 1), "x": (i - 1) * 2.3, "y": -3.15, "w": 1.0, "h": 2.0, "rot": 0} for i in range(3)] + \
           [{"designator": "4", "x": 0.0, "y": 3.15, "w": 3.6, "h": 2.0, "rot": 0}]
    assert S.generate("SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR", pads=pads)[1]["rotation"] == 90
    flipped = [dict(p, x=-p["x"], y=-p["y"]) for p in pads]
    assert S.generate("SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR", pads=flipped)[1]["rotation"] == 270


def _solid_boxes(solids):
    return {s.name: (min(v[0] for v in s.verts), min(v[1] for v in s.verts), max(v[0] for v in s.verts), max(v[1] for v in s.verts))
            for s in solids}


def test_leads_sit_on_their_pads():
    """KiCad placement: each lead / terminal overlaps the footprint pad with the same number (Altium coords, y up)."""
    for f in SPEC_FILES:
        spec = json.loads(f.read_text())
        fp, _ = K.convert_footprint(F.build(spec)[0])
        boxes = _solid_boxes(S.build(spec)[0])
        for p in fp["pads"]:
            if not p["designator"] or spec.get("tab") and p["designator"] == str(spec["tab"]["number"]) and p["w"] > 2:
                continue
            b = boxes.get(f"pin {p['designator']}") or boxes.get(f"terminal {p['designator']}")
            if b is None and spec.get("exposed_pad"):
                b = boxes["exposed pad"]
            assert b, (spec["name"], p["designator"], sorted(boxes))
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            assert abs(cx - p["x"]) < p["w"] / 2 + 0.6 and abs(cy - p["y"]) < p["h"] / 2 + 1e-6 or \
                abs(cy - p["y"]) < p["h"] / 2 + 0.6 and abs(cx - p["x"]) < p["w"] / 2 + 1e-6, (spec["name"], p, b)
        if spec["package_type"] in ("gullwing", "qfp", "qfn") and spec.get("pin_count", 0) > 2:
            b = boxes["pin 1"]
            assert b[0] < 0 and (b[1] + b[3]) / 2 > -1e-9, (spec["name"], b)           # pin 1 left, upper half
    # through-hole: pin 1 at the footprint origin
    for n in ("PinHeader_2x05_P2.54mm_Vertical", "CP_Radial_D5.0mm_P2.00mm", "R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"):
        b = _solid_boxes(S.build(S.spec_from_name(n))[0])
        p1 = b["pin 1"]
        assert p1[0] <= 0 <= p1[2] and p1[1] <= 0 <= p1[3] and p1[0] > -0.7 and p1[1] > -0.7, (n, p1)
    hdr = _solid_boxes(S.build(S.spec_from_name("PinHeader_2x05_P2.54mm_Vertical"))[0])
    assert abs((hdr["pin 2"][0] + hdr["pin 2"][2]) / 2 - 2.54) < 1e-6 and abs((hdr["pin 3"][1] + hdr["pin 3"][3]) / 2 + 2.54) < 1e-6


def test_checker_finds_broken_shells():
    text, _ = S.generate("R_0603_1608Metric")
    shell = re.search(r"#(\d+)=CLOSED_SHELL\('',\((#\d+),(#\d+),", text)
    assert S.check(text.replace(f"({shell.group(2)},{shell.group(3)},", f"({shell.group(3)},", 1))["errors"]       # a face missing
    face = re.search(r"#(\d+)=ADVANCED_FACE\((.*?),\.T\.\);", text)
    bad = S.check(text.replace(face.group(0), face.group(0).replace(",.T.);", ",.F.);")))["errors"]
    assert any("wrong way" in e for e in bad), bad                                                              # flipped face
    oe = re.search(r"ORIENTED_EDGE\('',\*,\*,#\d+,\.T\.\)", text).group(0)
    bad = S.check(text.replace(oe, oe.replace(".T.", ".F."), 1))["errors"]
    assert any("not connected" in e or "same direction" in e for e in bad), bad                                  # reversed edge
    assert any("missing" in e for e in S.check(text.replace("#1)", "#999999)", 1))["errors"])                     # dangling ref
    assert S.check("ISO-10303-21;\nDATA;\n#1=FOO(;\nENDSEC;")["errors"]
    cp = re.search(r"VERTEX_POINT\('',#(\d+)\)", text).group(1)                                                   # a vertex's point
    vp = re.search(rf"^#{cp}=CARTESIAN_POINT\('',\(([-0-9.]+),", text, re.M)
    moved = text.replace(vp.group(0), f"#{cp}=CARTESIAN_POINT('',({float(vp.group(1)) + 0.3},", 1)
    assert any("plane" in e or "LINE" in e for e in S.check(moved)["errors"])                                    # vertex moved


def _soic_fp():
    spec = json.loads((S.SPECS / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    return K.convert_footprint(F.build(spec)[0], "MY_SOIC8")


def test_add_body_embeds_generated_step():
    with tempfile.TemporaryDirectory() as d:
        notes = []
        fp, t = _soic_fp()
        K.add_body(fp, t, None, None, False, notes, gen_step=True)
        (body,) = fp["bodies"]
        assert body["step"]["name"] == "MY_SOIC8.step" and fp["height_mm"] == 1.75 == body["height"], (body["height"], notes)
        assert body["pts"] == [(-3.0, -2.45), (3.0, -2.45), (3.0, 2.45), (-3.0, 2.45)] and "generated STEP" in notes[-1], notes
        AL.write_pcblib(Path(d) / "g.PcbLib", [fp])
        got = AL.read_pcblib(Path(d) / "g.PcbLib")[0]
        (mp, data), = got["models"]
        assert data == body["step"]["data"] and int(mp["CHECKSUM"]) == AL.step_checksum(data)
        assert not S.check(data.decode())["errors"]
        lib = __import__("pyaltiumlib").read(str(Path(d) / "g.PcbLib"))
        assert any(type(r).__name__ == "PcbComponentBody" for r in lib.Parts[0].Records)
        # caller height with a source label beats the table and drives the model
        fp, t = _soic_fp(); notes = []
        K.add_body(fp, t, None, 1.5, False, notes, gen_step=True, height_src="datasheet height_max")
        assert fp["height_mm"] == 1.5 and S.check(fp["bodies"][0]["step"]["data"].decode())["bbox"][5] == 1.5
        assert "datasheet height_max" in notes[-1], notes
        # existing behaviour stays selectable: no gen_step -> extruded box
        fp, t = _soic_fp(); notes = []
        K.add_body(fp, t, None, None, False, notes)
        assert "step" not in fp["bodies"][0] and "extruded body" in notes[-1]
        # an explicit spec (datasheet reader output) is used even when the name is unknown
        spec = dict(json.loads((S.SPECS / "SOT-23.json").read_text()), name="XYZ123")
        fp, t = K.convert_footprint(F.build(spec)[0]); notes = []
        K.add_body(fp, t, None, 1.0, False, notes, gen_step=True, spec=spec, height_src="caller")
        assert fp["bodies"][0]["step"] and fp["height_mm"] == 1.0
        # unknown family: the box is the last resort
        fp, t = K.convert_footprint(F.build(spec)[0]); notes = []
        K.add_body(fp, t, None, 1.0, False, notes, gen_step=True)
        assert "step" not in fp["bodies"][0] and any("no generated STEP" in n for n in notes), notes


def test_librarian_embeds_generated_models():
    import copy
    from unittest import mock
    from ecadlib import datasheet as D
    from tests import test_librarian as TL
    ext = copy.deepcopy(TL.TD.GOOD)
    with tempfile.TemporaryDirectory() as d, mock.patch.object(D, "call", lambda *a, **k: (ext, TL.TD.message(ext))):
        items, _ = TL.run(d, api_key="sk-test")
        st = json.loads((Path(d) / "out" / "librarian_state.json").read_text())
        for n in ("DSPART1", "RES-0603-10k", "TPS1234DSGR"):
            f = st[n]["footprint"]
            (mp, data), = AL.read_pcblib(Path(d) / "out" / f["file"])[0]["models"]
            assert any(x.startswith("3D: generated STEP") for x in f["notes"]) and not S.check(data.decode())["errors"], (n, f["notes"])
            assert items[n]["release_check"]["height"] and not any(r.startswith("height") for r in items[n]["hold_reasons"]), items[n]
        assert any("datasheet height_max" in x for x in st["DSPART1"]["footprint"]["notes"])
        f = st["EEPART1"]["footprint"]                                                           # EasyEDA SOT-23-3_L2.9-W1.3-...
        assert any(x.startswith("3D: generated STEP (gullwing") for x in f["notes"]), f["notes"]
    with tempfile.TemporaryDirectory() as d:                                                     # --box: old behaviour
        TL.run(d, gen_step=False)
        st = json.loads((Path(d) / "out" / "librarian_state.json").read_text())
        assert not any(x.startswith("3D: generated") for v in st.values() if v.get("footprint") for x in v["footprint"]["notes"])


def test_cli():
    from ecadlib import cli
    assert "step-gen" in cli.COMMANDS
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x" / "q.step"
        assert S.main([str(S.SPECS / "QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm.json"), "-o", str(out)]) == 0
        assert S.main(["SOT-23-5", "-o", str(Path(d) / "s.step"), "--height", "1.2"]) == 0
        assert S.main(["--check", str(out), str(Path(d) / "s.step")]) == 0
        (Path(d) / "bad.step").write_text(out.read_text().replace("CLOSED_SHELL('',(#", "CLOSED_SHELL('',(", 1).replace("(,", "(", 1))
        assert S.main(["--check", str(Path(d) / "bad.step")]) == 1
        assert S.main(["Foo_Bar", "-o", str(Path(d) / "f.step")]) == 2
        assert cli.main(["step-gen", "R_0402_1005Metric", "-o", str(Path(d) / "r.step")]) == 0
        assert out.read_text().startswith("ISO-10303-21;") and "AUTOMOTIVE_DESIGN" in out.read_text()


def test_zero_size_name_is_a_value_error():
    """A degenerate footprint name (zero-size body) must raise ValueError so kicad_import falls back to a box."""
    for n in ("R_0000_0000Metric", "CP_Radial_D0mm_P0mm", "PinHeader_0x00_P2.54mm_Vertical"):
        try:
            S.generate(n)
        except ValueError:
            continue
        raise AssertionError(n)


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); print("ok", k)
