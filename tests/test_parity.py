"""ecadlib.parity: the KiCad and the Altium library of a part read back the same. Run:
    python3 -m tests.run_all test_parity
Conventions (units, y axis, rotation), generator round trips, deliberately broken files it must catch (pads, drills,
paste, mask, courtyard, exposed-pad windows, 3D, pins, fields, database rows), the release-check hold, the testpack in
export mode, kicad_export's 3D model fix, the survey of every generator footprint, Workspace and MCP."""
import copy, csv, json, sqlite3, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, footprint_gen as F, kicad_import as K, parity as P, release_check as RC, rules as RU, sexpr

SPECS = Path(__file__).resolve().parents[1] / "ecadlib" / "package_specs"
_PACK = {}


def _spec(name, adv=False):
    return json.loads(((SPECS / "advanced") if adv else SPECS).joinpath(f"{name}.json").read_text())


def _write(d, fp, name="x.PcbLib"):
    f = Path(d) / name
    AL.write_pcblib(f, [fp])
    return AL.read_pcblib(f)[0]


def _errors(diffs):
    return [d for d in diffs if d["level"] == "error"]


def _pack():
    if not _PACK:
        from ecadlib import testpack as TP
        d = Path(tempfile.mkdtemp(prefix="ecadlib-parity-"))
        import atexit, shutil
        atexit.register(shutil.rmtree, d, True)
        s = TP.build(d / "pack", log=lambda *a, **k: None)
        _PACK.update(dir=d / "pack", summary=s)
    return _PACK


# ---------------------------------------------------------------- conventions (one place)
def test_units_axis_and_rotation_conventions():
    assert P._a2k(AL.mm(1.27), AL.mm(-2.5)) == (AL.to_mm(AL.mm(1.27)), AL.to_mm(AL.mm(2.5)))     # y flips, mm
    assert abs(P._mm(10000) - 0.0254) < 1e-12                                                   # 1 mil = 10000 units
    assert P.canon_pad(1, 2, 90) == P.canon_pad(2, 1, 0) == P.canon_pad(1, 2, 270) == (2, 1, 0.0)
    assert P.canon_pad(1, 2, 180) == (1, 2, 0.0) and P.canon_pad(1, 2, 120) == (2, 1, 30.0)
    assert P.canon_pad(1.5, 1.5, 33, round_=True) == (1.5, 1.5, 0.0) and P.canon_pad(1.5, 1.5, 33)[2] == 33.0   # circle: none
    assert P.canon_pad(1, 3, 33, round_=True) == (1, 3, 33.0)                                   # oval keeps it
    assert P._angle_diff(179.995, 0.0) < 0.01


def test_generator_footprints_match_both_ways():
    """footprint_gen -> altium_lib (approval) and -> kicad_import (librarian): SMD, EP paste windows, THT, slots,
    NPTH, polygon pads, BGA."""
    cases = [_spec("SOIC-8_3.9x4.9mm_P1.27mm"), _spec("DFN-8-1EP_3x3mm_P0.5mm_EP1.66x2.38mm")]
    cases += [_spec(p.stem, True) for p in sorted((SPECS / "advanced").glob("*.json"))][:41:5]
    with tempfile.TemporaryDirectory() as d:
        for spec in cases:
            text, _ = F.build(spec)
            for fp in (AL.footprint_from_spec(spec)[0], K.convert_footprint(text, spec["name"], "t")[0]):
                diffs = P.footprint_pair(text, _write(d, fp))
                assert not _errors(diffs), (spec["name"], [x["msg"] for x in diffs])


# ---------------------------------------------------------------- broken files it must catch
def test_broken_pcblib_is_caught_field_by_field():
    spec = _spec("DFN-8-1EP_3x3mm_P0.5mm_EP1.66x2.38mm")
    text, _ = F.build(spec)
    fp, _ = AL.footprint_from_spec(spec)
    breaks = {
        "size/rotation": lambda f: f["pads"][1].update(w=f["pads"][1]["w"] + 0.05),
        "position": lambda f: f["pads"][2].update(x=f["pads"][2]["x"] + 0.01),
        "paste": lambda f: f["pads"][3].update(paste="none"),
        "solder mask": lambda f: f["pads"][4].update(mask="none"),
        "mask margin": lambda f: f["pads"][4].update(mask=0.05),
        "corner radius": lambda f: f["pads"][0].update(corner=10),
        "shape": lambda f: f["pads"][0].update(shape="rect"),
        "paste/mask windows": lambda f: f.update(fills=f["fills"][:-1]),
        "extent": lambda f: f.update(tracks=[t for t in f["tracks"] if t[5] != AL.MECH15][:len(f["tracks"])]
                                     + [(t[0] - 0.1, *t[1:]) for t in f["tracks"] if t[5] == AL.MECH15]),
        "missing": lambda f: f.update(pads=f["pads"][:-1]),
        "presence": lambda f: f.update(tracks=[t for t in f["tracks"] if t[5] != AL.MECH15]),
    }
    with tempfile.TemporaryDirectory() as d:
        assert not _errors(P.footprint_pair(text, _write(d, fp)))
        for field, brk in breaks.items():
            bad = copy.deepcopy(fp)
            brk(bad)
            got = _errors(P.footprint_pair(text, _write(d, bad)))
            assert any(x["field"] == field for x in got), (field, [x["msg"] for x in got])
        bad = copy.deepcopy(fp)                  # the exposed pad's paste windows: one window gone shows on the EP too
        bad["fills"] = bad["fills"][:-1]
        got = _errors(P.footprint_pair(text, _write(d, bad)))
        assert any(x["what"] == "exposed pad" and "3 windows" in x["msg"] for x in got), [x["msg"] for x in got]
        # a readable per-field message
        bad = copy.deepcopy(fp)
        bad["pads"][1]["w"] += 0.05
        msg = _errors(P.footprint_pair(text, _write(d, bad)))[0]["msg"]
        assert msg.startswith("pad 2 size/rotation: KiCad (") and "/ Altium (" in msg and "mm" in msg, msg


def test_broken_through_hole_drill_and_slot_are_caught():
    for p in sorted((SPECS / "advanced").glob("*.json")):
        spec = _spec(p.stem, True)
        fp, _ = AL.footprint_from_spec(spec)
        if any(q.get("hole_shape") == "slot" for q in fp["pads"]):
            break
    text, _ = F.build(spec)
    slot = next(i for i, p in enumerate(fp["pads"]) if p.get("hole_shape") == "slot")
    tht = next(i for i, p in enumerate(fp["pads"]) if p.get("hole") and p.get("plated", True))
    with tempfile.TemporaryDirectory() as d:
        assert not _errors(P.footprint_pair(text, _write(d, fp))), spec["name"]
        for field, brk in (("drill", lambda f: f["pads"][tht].update(hole=f["pads"][tht]["hole"] + 0.1)),
                           ("slot direction", lambda f: f["pads"][slot].update(hole_rot=(f["pads"][slot]["hole_rot"] + 90) % 180)),
                           ("side", lambda f: f["pads"][tht].update(plated=False))):
            bad = copy.deepcopy(fp)
            brk(bad)
            got = _errors(P.footprint_pair(text, _write(d, bad)))
            assert any(x["field"] == field for x in got), (field, [x["msg"] for x in got])


def test_broken_kicad_side_and_3d_presence():
    spec = _spec("SOIC-8_3.9x4.9mm_P1.27mm")
    text, _ = F.build(spec)
    with tempfile.TemporaryDirectory() as d:
        afp = _write(d, AL.footprint_from_spec(spec)[0])
        t = sexpr.parse(text, keep_quotes=True)
        sexpr.first(next(sexpr.find(t, "pad")), "at")[1] = "-9"
        got = _errors(P.footprint_pair(sexpr.dumps(t), afp))
        assert [x["field"] for x in got] == ["position"], [x["msg"] for x in got]
        with_model = text.rstrip()[:-1] + '\t(model "${KICAD8_3DMODEL_DIR}/x.3dshapes/x.wrl")\n)\n'
        got = _errors(P.footprint_pair(with_model, afp))          # KiCad has a model, Altium has no body
        assert [x["what"] for x in got] == ["3D model"], [x["msg"] for x in got]
        got = _errors(P.footprint_pair(text, afp, export=True))     # export mode: Altium none, KiCad none -> fine
        assert not got


def test_broken_symbol_pins_and_mapping_are_caught():
    kt = sexpr.parse('''(symbol "U" (property "Reference" "U") (symbol "U_1_1"
        (pin input line (at 0 0 0) (length 2.54) (name "~{RST}") (number "1"))
        (pin power_in line (at 0 2.54 0) (length 2.54) (name "VCC") (number "2"))
        (pin passive line (at 0 2.54 0) (length 2.54) hide (name "VCC") (number "3"))
        (pin output line (at 0 5 0) (length 2.54) (name "Q") (number "4"))))''')
    k = P.kicad_symbol(kt)
    pins = [{"num": "1", "name": "~{RST}", "type": "input"}, {"num": "2", "name": "VCC", "type": "power_in"},
            {"num": "3", "name": "VCC", "type": "power_in"}, {"num": "4", "name": "Q", "type": "output"}]
    with tempfile.TemporaryDirectory() as d:
        def alt(ps):
            f = Path(d) / "u.SchLib"
            AL.write_schlib(f, [AL.ic_symbol("U", [dict(p, side="left") for p in ps], layout=False)])
            return P.altium_symbol(AL.read_schlib(f)[1][0])
        a = alt(pins)
        assert a["pins"]["1"]["name"] == "R\\S\\T\\"
        assert not _errors(P.compare_symbols(k, a)), P.compare_symbols(k, a)   # overline + stacked passive = power
        for field, ps in (("name", [dict(pins[0], name="RST")] + pins[1:]), ("electrical type", pins[:3] + [dict(pins[3], type="io")]),
                          ("missing", pins[:3]), ("pin count", pins + [{"num": "5", "name": "X", "type": "input"}])):
            got = _errors(P.compare_symbols(k, alt(ps)))
            assert any(x["field"] == field for x in got), (field, [x["msg"] for x in got])
    got = P.compare_pin_pad({"1": 0, "2": 0}, {"1", "2"}, {"1": 0, "2": 0}, {"1"})
    assert [x["msg"] for x in got] == ["pin 2 pad: KiCad pad / Altium no pad (pin -> pad mapping differs)"], got


# ---------------------------------------------------------------- release check holds the part
def test_release_check_holds_a_part_whose_files_differ_with_a_per_field_diff():
    import shutil
    pk = _pack()
    with tempfile.TemporaryDirectory() as d:
        lib = Path(d) / "library"
        shutil.copytree(pk["dir"] / "library", lib)
        items = json.loads((lib / "plan.json").read_text())
        it = next(i for i in items if i["name"] == "LM358DR")
        assert it["release_check"]["parity"] == "match", it["release_check"]
        kmod = lib / it["footprint"]["kicad_mod"]
        kmod.write_text(kmod.read_text().replace("(size 1.95 0.6)", "(size 1.95 0.65)", 1))
        fails, warns, info = RC.check_item(it, lib, RU.DEFAULTS)
        par = [m for r, m in fails if r == "parity"]
        assert par == ["pad 1 size/rotation: KiCad (1.95, 0.65, 0) / Altium (1.95, 0.6, 0) (w, h mm, rotation deg)"], fails
        assert info["parity"] == "mismatch"
        rows = RC.apply(items, lib, rules=RU.DEFAULTS)
        assert it["status"] == "held" and any(h.startswith("parity: pad 1 size") for h in it["hold_reasons"]), it["hold_reasons"]
        warn = RU.load({"release": {"parity": "warn"}})
        fails, warns, _ = RC.check_item(it, lib, warn)
        assert not [r for r, _ in fails if r == "parity"] and any(w.startswith("parity: pad 1 size") for w in warns), warns
        fails, warns, info = RC.check_item(it, lib, RU.load({"release": {"parity": "off"}}))
        assert "parity" not in info and not any("parity" in w for w in warns)
        RC.apply(items, lib, allow=["LM358DR"], rules=RU.DEFAULTS)             # --allow can release it, as other rules
        assert it["status"] == "ready" and it["allow"]["failures"]
    try:
        RU.load({"release": {"parity": "sometimes"}})
        raise AssertionError("bad value accepted")
    except RU.RulesError as e:
        assert "release.parity" in str(e)
    assert RU.DEFAULTS["release"]["parity"] == "hold"


# ---------------------------------------------------------------- the testpack in export mode, databases
def test_testpack_kicad_export_and_altium_dblib_match():
    pk = _pack()
    d, s = pk["dir"], pk["summary"]
    assert s["parity"]["match"] == 6 and not s["parity"]["mismatch"] and s["parity"]["database_rows"] == {"kicad": 6, "altium": 6}, s["parity"]
    rep = P.check_plan(d / "library" / "plan.json", d / "kicad", "testpack", d / "altium")
    assert rep["ok"] and rep["counts"]["match"] == 6, [(p["part"], p["diffs"]) for p in rep["parts"]]
    usb = next(p for p in rep["parts"] if p["part"] == "TYPE-C-31-M-12")
    assert {"pyaltiumlib", "pin -> pad", "fields (DbLib row)"} <= set(usb["checked"]) and "footprint (22 pads)" in usb["checked"]
    assert (d / "parity.md").is_file() and "**6 parts match, 0 differ.**" in (d / "parity.md").read_text()


def test_export_mode_catches_missing_3d_file_symbol_and_database_rows():
    import shutil
    pk = _pack()
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "pack"
        shutil.copytree(pk["dir"], d)
        (d / "kicad" / "testpack.3dshapes" / "SOT-23.step").unlink()
        sym = d / "kicad" / "testpack.kicad_sym"
        sym.write_text(sym.read_text().replace('(name "+IN_A"', '(name "IN_A"', 1))
        rows = list(csv.DictReader(open(d / "altium" / "testpack.csv", encoding="utf-8-sig")))
        with open(d / "altium" / "testpack.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows([r for r in rows if r["Part Number"] != "61300511121"])
        con = sqlite3.connect(d / "kicad" / "testpack.sqlite")
        con.execute('UPDATE "Resistors" SET "MPN" = ? WHERE "Part ID" = ?', ("RC0603FR-07100KL", "RES-0603-10k"))
        con.commit(); con.close()
        rep = P.check_plan(d / "library" / "plan.json", d / "kicad", "testpack", d / "altium")
        bad = {p["part"]: [x["msg"] for x in p["diffs"] if x["level"] == "error"] for p in rep["parts"] if p["result"] == "mismatch"}
        assert any(m.startswith("3D model presence: KiCad file missing") for m in bad["MMBT3904LT1G"]), bad
        assert bad["LM358DR"] == ["pin 3 name: KiCad IN_A / Altium +IN_A"], bad
        db = [x["msg"] for x in rep["database"]]
        assert "part 61300511121 database row: KiCad present / Altium missing" in db, db
        assert "part RES-0603-10k MPN: KiCad RC0603FR-07100KL / Altium RC0603FR-0710KL" in db, db
        assert not rep["ok"]
        from ecadlib import parity
        assert parity.main([str(d / "library" / "plan.json"), "--kicad", str(d / "kicad"), "--altium", str(d / "altium"),
                            "-o", str(Path(t) / "rep"), "--no-pyaltiumlib"]) == 1
        assert "pin 3 name is IN_A in KiCad and +IN_A in Altium" in (Path(t) / "rep.md").read_text()


def test_kicad_export_writes_the_altium_3d_model_regression():
    """Before: a footprint without a KiCad model (every generated one) had no 3D model in the KiCad export while its
    Altium footprint carried a STEP; now the embedded STEP (or the extruded body as a STEP) goes to <lib>.3dshapes."""
    from ecadlib import step_gen as SG
    pk = _pack()
    d = pk["dir"] / "kicad"
    for fp in ("SOT-23", "SOIC-8_3.9x4.9mm_P1.27mm", "USB_C_Receptacle_HRO_TYPE-C-31-M-12"):
        t = sexpr.parse((d / "testpack.pretty" / f"{fp}.kicad_mod").read_text())
        m = sexpr.first(t, "model")
        assert m and m[1] == f"${{KIPRJMOD}}/testpack.3dshapes/{fp}.step", fp
        step = (d / "testpack.3dshapes" / f"{fp}.step").read_text()
        assert not SG.check(step)["errors"], fp
    emb = AL.read_pcblib(pk["dir"] / "library" / "models" / "PcbLib" / "SOT-23.PcbLib")[0]["models"][0][1]
    assert (d / "testpack.3dshapes" / "SOT-23.step").read_bytes() == emb           # the very same file
    usb = SG.check((d / "testpack.3dshapes" / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.step").read_text())
    assert abs(usb["bbox"][5] - 3.3) < 0.01, usb["bbox"]                            # the extruded body's height
    rep = json.loads((d / "kicad_export.json").read_text())
    assert rep["models"]["USB_C_Receptacle_HRO_TYPE-C-31-M-12.step"].startswith("extruded body"), rep["models"]


# ---------------------------------------------------------------- converter fixes the KiCad library survey found
def _mod(body):
    return '(footprint "T" (version 20240108) (generator "t") (layer "F.Cu")\n' + body + "\n)"


def _roundtrip(text, d):
    """.kicad_mod -> librarian (kicad_import) -> .PcbLib -> both readers, and the .PcbLib -> kicad-export text."""
    from ecadlib import kicad_export as X
    fp, _ = K.convert_footprint(text, "T", "t")
    a = _write(d, fp)
    back = X.footprint_from_pcblib(Path(d) / "x.PcbLib", "T", [])
    return a, back, P.footprint_pair(text, a), P.compare_footprints(P.kicad_footprint(back), P.altium_footprint(a))


def test_through_hole_drill_offset_is_carried_regression():
    """Before: kicad_import dropped (drill (offset ..)) with a note, so the copper sat on the hole in Altium; now the
    offset goes to the pad's hole-offset field (read back by our reader) and kicad-export writes it again."""
    text = _mod('(pad "1" thru_hole rect (at 2 1 90) (size 3 1.6) (drill 1 (offset 0.6 0.1)) (layers "*.Cu" "*.Mask"))\n'
                '(pad "2" thru_hole oval (at -2 1) (size 3 1.6) (drill oval 1.6 0.8 (offset -0.5 0)) (layers "*.Cu" "*.Mask"))')
    with tempfile.TemporaryDirectory() as d:
        a, back, diffs, exp = _roundtrip(text, d)
        assert not _errors(diffs) and not _errors(exp), [x["msg"] for x in diffs + exp]
        pads = {p["designator"]: p for p in a["records"] if p["type"] == "pad"}
        assert pads["1"]["offset"] != (0, 0) and pads["2"]["offset"] != (0, 0)
        assert "(offset" in back
        k = P.kicad_footprint(text)                     # the copper centre is what moved: (at) + offset turned by 90
        assert P._fmt(k["pads"][0]["offset"]) == P._fmt((0.1, -0.6)), k["pads"][0]["offset"]
        a0 = copy.deepcopy(a)                           # and a dropped offset is a mismatch
        for r in a0["records"]:
            r.pop("offset", None)
        assert any(x["field"] == "copper offset from hole" for x in P.footprint_pair(text, a0))


def test_smd_shape_offset_moves_the_copper_regression():
    """Before: an SMD pad's (drill (offset ..)) (KiCad draws the copper there) was ignored; now the pad moves."""
    text = _mod('(pad "D1" smd roundrect (at -2.54 23.9 90) (size 3.2 1.6) (drill (offset -0.8 0)) (layers "F.Cu" "F.Mask") '
                '(roundrect_rratio 0.5))')
    with tempfile.TemporaryDirectory() as d:
        a, _, diffs, exp = _roundtrip(text, d)
        assert not _errors(diffs) and not _errors(exp), [x["msg"] for x in diffs + exp]
        pad = next(p for p in a["records"] if p["type"] == "pad")
        assert abs(AL.to_mm(pad["y"]) - -(23.9 + 0.8)) < 1e-3, AL.to_mm(pad["y"])   # (-0.8, 0) turned by 90: 0.8 mm down


def test_custom_pad_shapes_and_windows_regression():
    """Custom pads whose primitive only touches the anchor (MMCX), whose outline is stroked, mask / paste windows with a
    custom or round shape on both layers (thermal-via apertures), footprint paste polygons on a custom pad's copper
    (SMA): read back the same from the .PcbLib and from its KiCad export."""
    text = _mod(
        '(pad "2" smd custom (at 2.25 -1.6) (size 3 1.4) (layers "F.Cu" "F.Mask" "F.Paste") (options (clearance outline) '
        '(anchor rect)) (primitives (gr_poly (pts (xy -1.5 -0.15) (xy -6 -0.15) (xy -6 -0.7) (xy -1.5 -0.7)) (width 0) (fill yes))))\n'
        '(pad "3" smd custom (at 0 4) (size 0.5 0.5) (layers "F.Cu" "F.Mask" "F.Paste") (options (clearance outline) (anchor circle)) '
        '(primitives (gr_poly (pts (xy -1 -0.5) (xy 1 -0.5) (xy 1 0.5) (xy -1 0.5)) (width 0.2) (fill yes))))\n'
        '(pad "" smd custom (at -1.5 -6) (size 0.7 0.7) (layers "F.Paste" "F.Mask") (options (clearance outline) (anchor circle)) '
        '(primitives (gr_poly (pts (xy 0.3 -0.5) (xy -0.3 -0.5) (xy -0.3 -0.8) (xy 0.3 -0.8)) (width 0) (fill yes))))\n'
        '(pad "" smd circle (at 3 -6) (size 0.8 0.8) (layers "F.Paste" "F.Mask"))\n'
        '(pad "4" smd rect (at 0 9) (size 6 3) (layers "F.Cu" "F.Mask"))\n'
        '(fp_poly (pts (xy -2.9 7.6) (xy -0.1 7.6) (xy -0.1 10.4) (xy -2.9 10.4)) (stroke (width 0) (type solid)) (fill yes) (layer "F.Paste"))\n'
        '(fp_poly (pts (xy 0.1 7.6) (xy 2.9 7.6) (xy 2.9 10.4) (xy 0.1 10.4)) (stroke (width 0) (type solid)) (fill yes) (layer "F.Paste"))\n'
        '(fp_rect (start 4 4) (end 5 5) (stroke (width 0) (type solid)) (fill yes) (layer "F.Mask"))')
    with tempfile.TemporaryDirectory() as d:
        a, back, diffs, exp = _roundtrip(text, d)
        assert not _errors(diffs) and not _errors(exp), [x["msg"] for x in diffs + exp]
        k, al = P.kicad_footprint(text), P.altium_footprint(a)
        assert [p["shape"] for p in al["pads"]] == ["custom", "custom", "rect"], al["pads"]
        assert sorted(w for w, _ in k["windows"]) == sorted(w for w, _ in al["windows"]) == ["mask"] * 4 + ["paste"] * 5
        assert back.count(" smd custom ") == 2 and back.count('(layers "F.Paste")') == 5, back


def test_footprint_copper_next_to_a_pad_is_not_a_pad_mismatch():
    """A copper polygon of the footprint that touches / covers a pad (solder-jumper bridge, net tie): Altium regions
    belong to no pad, so the reader may see a custom pad where KiCad has a plain one; same copper -> warn only."""
    text = _mod('(pad "1" smd circle (at -0.5 0) (size 0.5 0.5) (layers "F.Cu"))\n(pad "2" smd circle (at 0.5 0) (size 0.5 0.5) '
                '(layers "F.Cu"))\n(fp_poly (pts (xy -0.5 -0.25) (xy 0.5 -0.25) (xy 0.5 0.25) (xy -0.5 0.25)) '
                '(stroke (width 0) (type solid)) (fill yes) (layer "F.Cu"))')
    with tempfile.TemporaryDirectory() as d:
        _, _, diffs, _ = _roundtrip(text, d)
        assert not _errors(diffs) and any("the copper itself is the same" in x["msg"] for x in diffs), diffs
        k = P.kicad_footprint(text)
        k["copper"] = [(-0.5, -0.25, 0.9, 0.25)]       # different copper: still an error
        assert _errors(P.compare_footprints(k, P.altium_footprint(_write(d, K.convert_footprint(text, "T", "t")[0]))))


# ---------------------------------------------------------------- survey, workspace, MCP
def test_survey_of_generator_and_standard_footprints_finds_no_mismatch():
    rep = P.survey(quick=True)
    assert rep["by_path"]["generator"]["checked"] == rep["by_path"]["librarian"]["checked"] == 68, rep["by_path"]
    assert rep["by_path"]["standard"]["checked"] >= 24, rep["by_path"]
    assert rep["mismatched"] == 0, rep["diffs"]


def test_workspace_and_mcp_tool():
    import shutil
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    pk = _pack()
    with tempfile.TemporaryDirectory() as t:
        ws = Workspace(Path(t) / "ws", [], offline=True)
        bom = ws.root / "boms" / "pack.csv"
        bom.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(pk["dir"] / "bom.csv", bom)
        lib = ws._librarian_dir(bom)
        shutil.copytree(pk["dir"] / "library", lib, dirs_exist_ok=True)
        r = ws.parity(ws.rel(bom))
        assert r["ok"] and r["mode"] == "plan" and r["counts"]["match"] == 6 and (ws.root / r["report"]).is_file(), r
        try:
            ws.parity(ws.rel(bom), kicad=True)
            raise AssertionError("no export yet")
        except ValueError as e:
            assert "kicad_dblib" in str(e)
        assert any(x["name"] == "parity" for x in M.TOOLS)
        r2 = M.call_tool(ws, "parity", {"bom": ws.rel(bom)})
        assert r2["counts"]["match"] == 6 and not r2["mismatches"], r2
