"""2026-10-26 fixes (binread): Altium binary readers, footprint_check geometry, nexar sign-in, ecadlib status.
No network, no pytest: plain test functions (tests.run_all) with tests.fixtures_lite for tmp_path / monkeypatch."""
import json, math

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-BR-6: ecadlib status names a damaged plan
@lite
def test_status_names_a_plan_with_wrong_typed_fields(tmp_path):
    from ecadlib import ws_status as WS
    cases = {"approvals": {"name": "R1", "status": "ready", "approvals": 5},
             "change": {"name": "R1", "status": "ready", "change": "x"},
             "name": {"name": ["R1"], "status": "ready"},
             "hold_reasons": {"name": "R1", "status": "held", "hold_reasons": 3},
             "status": {"name": "R1", "status": ["ready"]},
             "held change name": {"name": ["R1"], "status": "ready", "change": {"state": "held"}}}
    for label, item in cases.items():
        root = tmp_path / label.replace(" ", "_")
        (root / "librarian" / "b").mkdir(parents=True)
        (root / "librarian" / "b" / "plan.json").write_text(json.dumps([item, {"name": "R2", "status": "ready"}]))
        s = WS.status(root)                    # was: TypeError / AttributeError out of `ecadlib status`
        WS.render(s)
        assert isinstance(s["problems"], list)
    # a damaged plan is named (and not counted); a good plan next to it still is
    root = tmp_path / "mixed"
    for b, items in (("bad", [{"name": "R1", "status": "held", "hold_reasons": 3}]),
                     ("good", [{"name": "R2", "status": "ready"}])):
        (root / "librarian" / b).mkdir(parents=True)
        (root / "librarian" / b / "plan.json").write_text(json.dumps(items))
    s = WS.status(root)
    assert any("librarian/bad/plan.json" in p for p in s["problems"]), s["problems"]
    assert [p["plan"] for p in s["plans"]] == ["librarian/good/plan.json"]
    assert s["totals"]["ready"] == 1 and "librarian/bad/plan.json" in WS.render(s)


# ---------------------------------------------------------------- R-BR-5: a stray local request does not end sign-in
def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_nexar_signin_ignores_requests_with_the_wrong_state():
    import threading, urllib.error, urllib.request
    from ecadlib import nexar_signin as N
    redirect = f"http://127.0.0.1:{_free_port()}/login"
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # localhost only, no network
    codes = []

    def browser():
        def go():
            for q in ("code=junk&state=attacker", "x=1", "error=access_denied&state=other", "code=REAL&state=GOOD"):
                try:
                    codes.append(direct.open(f"{redirect}?{q}", timeout=5).status)
                except urllib.error.HTTPError as e:
                    codes.append(e.code)
        threading.Thread(target=go, daemon=True).start()
    # was: SigninError (the first request, a hostile page's, was taken and the real redirect refused)
    assert N.wait_for_code(redirect, "GOOD", timeout=10, ready=browser) == "REAL"
    assert codes[:3] == [400, 400, 400], codes


def test_nexar_signin_times_out_naming_the_wrong_state():
    import threading, urllib.error, urllib.request
    from ecadlib import nexar_signin as N
    redirect = f"http://127.0.0.1:{_free_port()}/login"
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def browser():
        def go():
            try:
                direct.open(f"{redirect}?code=x&state=forged", timeout=5).read()
            except urllib.error.HTTPError:
                pass
        threading.Thread(target=go, daemon=True).start()
    try:
        N.wait_for_code(redirect, "GOOD", timeout=1.5, ready=browser)
    except N.SigninError as e:
        assert "state" in str(e), e
    else:
        raise AssertionError("a forged state was accepted")


# ---------------------------------------------------------------- R-BR-4: silk arcs measured along the real arc
_ARC_BASE = '''(module "T" (layer F.Cu)
  (fp_line (start -2 -2) (end 2 -2) (layer F.CrtYd) (width 0.05))
  (fp_line (start 2 -2) (end 2 2) (layer F.CrtYd) (width 0.05))
  (fp_line (start 2 2) (end -2 2) (layer F.CrtYd) (width 0.05))
  (fp_line (start -2 2) (end -2 -2) (layer F.CrtYd) (width 0.05))
  (fp_line (start -1.5 -1.5) (end 1.5 -1.5) (layer F.Fab) (width 0.1))
  %s
  (pad 1 smd rect (at %s) (size 0.4 0.4) (layers F.Cu F.Paste F.Mask))
  (pad 2 smd rect (at 0 1.8) (size 0.4 0.4) (layers F.Cu F.Paste F.Mask))
)'''


def _silk(arc, pad1):
    from ecadlib import footprint_check as FC
    return [i for i in FC.check(_ARC_BASE % (arc, pad1)) if i[1] == "silk" and "width" not in i[2]]


def test_kicad5_silk_arc_over_a_pad_is_found():
    # KiCad 5: (start centre) (end arc start) (angle): a half circle r = 1 from (1, 0) through (0, 1) to (-1, 0)
    # (clockwise on screen, y down); pad 1 at (0, 1) sits on it. Was read as a line from the centre to (1, 0).
    k5 = "(fp_arc (start 0 0) (end 1 0) (angle 180) (layer F.SilkS) (width 0.12))"
    assert any(i[0] == "error" for i in _silk(k5, "0 1")), _silk(k5, "0 1")
    # the other half (through (0, -1)) is 0.8 mm clear of a pad at (0, 1) less its half size: no finding
    k5b = "(fp_arc (start 0 0) (end 1 0) (angle -180) (layer F.SilkS) (width 0.12))"
    assert not _silk(k5b, "0 1"), _silk(k5b, "0 1")


def test_kicad6_silk_arc_is_not_two_chords():
    # a half circle r = 1 through (0, -1): pad 1 at 45 degrees on the arc. The chords start-mid-end lie 0.29 mm inside.
    a = "(fp_arc (start 1 0) (mid 0 -1) (end -1 0) (layer F.SilkS) (width 0.1))"
    c = math.cos(math.pi / 4)
    assert any(i[0] == "error" for i in _silk(a, f"{c + 0.1:.3f} {-c - 0.1:.3f}")), _silk(a, f"{c + 0.1:.3f} {-c - 0.1:.3f}")


def test_arc_courtyard_box_is_the_arc():
    from ecadlib import footprint_check as FC, sexpr
    t = sexpr.parse('(footprint "T" (fp_arc (start 1 0) (mid 0 -1) (end -1 0) (stroke (width 0.05)) (layer "F.CrtYd")))')
    bb = FC._bbox(FC._graphics(t, "F.CrtYd")[0])
    assert abs(bb[1] + 1) < 0.01 and abs(bb[0] + 1) < 1e-9 and abs(bb[2] - 1) < 1e-9, bb
    t5 = sexpr.parse('(module "T" (fp_arc (start 0 0) (end 1 0) (angle 90) (layer F.CrtYd) (width 0.05)))')
    bb = FC._bbox(FC._graphics(t5, "F.CrtYd")[0])     # quarter circle (1, 0) -> (0, 1), clockwise on screen
    assert abs(bb[0]) < 0.01 and abs(bb[1]) < 0.01 and abs(bb[2] - 1) < 0.01 and abs(bb[3] - 1) < 0.01, bb


# ---------------------------------------------------------------- R-BR-2: custom pads measured as KiCad draws them
def _custom(pad1, p2, size2="0.5 0.5"):
    return f'''(footprint "T" (layer "F.Cu")
  (fp_rect (start -4 -4) (end 4 4) (stroke (width 0.05)) (fill none) (layer "F.CrtYd"))
  {pad1}
  (pad "2" smd rect (at {p2}) (size {size2}) (layers "F.Cu" "F.Paste" "F.Mask"))
)'''


_ROT = '''(pad "1" smd custom (at 0 0 {a}) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask")
    (options (clearance outline) (anchor rect))
    (primitives (gr_poly (pts (xy 0 -0.25) (xy 3 -0.25) (xy 3 0.25) (xy 0 0.25)) (width 0) (fill yes))))'''


def _cu_errors(text):
    from ecadlib import footprint_check as FC
    return [i for i in FC.check(text) if i[0] == "error" and i[1] == "copper"]


def test_rotated_custom_pad_copper_is_turned_with_the_pad():
    # turned 90 degrees, the copper runs along -y (x stays -0.25..0.25)
    assert _cu_errors(_custom(_ROT.format(a=90), "0 -2")), "a pad on the turned copper is no overlap"
    # 1.0 mm clear of the real copper: was "pads 1 and 2 overlap by 0.375 mm", a hard (false) error
    assert not _cu_errors(_custom(_ROT.format(a=90), "1.5 0")), _cu_errors(_custom(_ROT.format(a=90), "1.5 0"))
    # 180 and 270 degrees, and an odd angle
    assert _cu_errors(_custom(_ROT.format(a=180), "-2 0")) and not _cu_errors(_custom(_ROT.format(a=180), "2 0"))
    assert _cu_errors(_custom(_ROT.format(a=270), "0 2")) and not _cu_errors(_custom(_ROT.format(a=270), "0 -1.5"))
    c = 2 / math.sqrt(2)                                          # 45 degrees: the copper runs to (2.1, -2.1)
    assert _cu_errors(_custom(_ROT.format(a=45), f"{c:.3f} {-c:.3f}"))
    assert not _cu_errors(_custom(_ROT.format(a=45), f"{c:.3f} {c:.3f}")), _cu_errors(_custom(_ROT.format(a=45), f"{c:.3f} {c:.3f}"))


def test_rotated_custom_pad_box():
    from ecadlib import footprint_check as FC, sexpr
    p = FC._pad_rects(sexpr.parse(_custom(_ROT.format(a=90), "3 3")))[0]
    assert abs(p["x"]) < 1e-6 and abs(p["y"] + 1.375) < 1e-6 and abs(p["w"] - 0.5) < 1e-6 and abs(p["h"] - 3.25) < 1e-6, p


def test_custom_pad_circle_line_arc_and_stroke_are_copper():
    circle = '''(pad "1" smd custom (at 0 0) (size 0.3 0.3) (layers "F.Cu" "F.Paste" "F.Mask")
    (options (clearance outline) (anchor circle))
    (primitives (gr_circle (center 0 0) (end 1 0) (width 0) (fill yes))))'''
    assert _cu_errors(_custom(circle, "0.7 0", "0.3 0.3")), "pad 2 inside the circle's copper"
    assert not _cu_errors(_custom(circle, "1.6 0", "0.3 0.3"))
    line = '''(pad "1" smd custom (at 0 0) (size 0.3 0.3) (layers "F.Cu" "F.Paste" "F.Mask")
    (options (clearance outline) (anchor rect))
    (primitives (gr_line (start 0 0) (end 2 0) (width 0.6))))'''
    assert _cu_errors(_custom(line, "2 0.4", "0.3 0.3")), "the line's 0.3 mm half width reaches pad 2"
    assert not _cu_errors(_custom(line, "2 0.9", "0.3 0.3"))
    stroked = '''(pad "1" smd custom (at 0 0) (size 0.3 0.3) (layers "F.Cu" "F.Paste" "F.Mask")
    (options (clearance outline) (anchor rect))
    (primitives (gr_poly (pts (xy -1 -1) (xy 1 -1) (xy 1 1) (xy -1 1)) (width 0.4) (fill yes))))'''
    assert _cu_errors(_custom(stroked, "1.3 0", "0.3 0.3")), "the polygon's stroke reaches 1.2 mm"
    assert not _cu_errors(_custom(stroked, "1.6 0", "0.3 0.3"))
    arc = '''(pad "1" smd custom (at 0 0) (size 0.3 0.3) (layers "F.Cu" "F.Paste" "F.Mask")
    (options (clearance outline) (anchor rect))
    (primitives (gr_arc (start 1 0) (mid 0 -1) (end -1 0) (width 0.2))))'''
    assert _cu_errors(_custom(arc, "0 -1", "0.3 0.3")), "pad 2 on the arc's copper"
    assert not _cu_errors(_custom(arc, "0 1.4", "0.3 0.3"))


def test_custom_pad_silk_measured_to_the_real_copper():
    from ecadlib import footprint_check as FC
    t = _custom(_ROT.format(a=90), "3 3").replace(
        '(pad "2"', '(fp_line (start -1 -2) (end 1 -2) (stroke (width 0.12)) (layer "F.SilkS"))\n  (pad "2"')
    assert any(i[1] == "silk" and i[0] == "error" for i in FC.check(t)), FC.check(t)


# ---------------------------------------------------------------- R-BR-1: altium-native compare checks every primitive
def _rich_fp():
    """A footprint with every kind of PCB primitive ecadlib writes: pads, tracks, arcs, fills, regions (copper, keep-out,
    mechanical), texts, an extruded body and an embedded STEP model."""
    from pathlib import Path
    from ecadlib import altium_lib as AL
    spec = json.loads((Path(AL.__file__).parent / "package_specs" / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    fp = AL.footprint_from_spec(spec)[0]
    return dict(fp, tracks=list(fp["tracks"]) + [(-2.0, 3.0, 2.0, 3.0, 0.12, AL.TOP_OVERLAY)],
                arcs=list(fp.get("arcs", [])) + [(0.0, 0.0, 0.5, 0.0, 360.0, 0.1, AL.TOP_OVERLAY),
                                                  (1.0, 1.0, 0.3, 45.0, 135.0, 0.1, AL.MECH13)],
                fills=list(fp.get("fills", [])) + [(-0.5, -0.5, 0.5, 0.2, AL.TOP)],
                regions=list(fp.get("regions", [])) + [([(0, 0), (1, 0), (1, 0.5), (0, 0.5)], AL.TOP),
                                                        ([(-1, -1), (-0.5, -1), (-0.5, -0.5)], AL.TOP, 31),
                                                        ([(2, 2), (3, 2), (3, 3)], AL.MECH13)],
                texts=list(fp.get("texts", [])) + [(".Designator", -1.0, 3.5, 1.0, 0.15, 0.0, AL.TOP_OVERLAY)],
                bodies=[{"pts": [(-1, -1), (1, -1), (1, 1), (-1, 1)], "height": 1.5, "standoff": 0.1},
                        {"pts": [(-2, -2), (2, -2), (2, 2), (-2, 2)], "height": 1.75,
                         "step": {"name": "SOIC-8.step", "data": b"ISO-10303-21; BODY", "rot": (0.0, 0.0, 90.0)}}])


def _built(d, fp, altium):
    """Write fp (ecadlib's file + recipe), then put 'Altium's build' (the footprint dict altium) over the file."""
    from ecadlib import altium_lib as AL, altium_native as AN
    lib = d / "X.PcbLib"
    AL.write_pcblib(lib, [fp])
    rp = lib.with_name(lib.name + AN.EXT)
    AN.record(lib, AN._sha(lib.read_bytes()))
    lib.write_bytes(AL.pcblib_bytes([altium], lib.name))
    return lib, rp


@lite
def test_native_compare_matches_ecadlibs_own_libraries(tmp_path):
    from pathlib import Path
    from ecadlib import altium_lib as AL, altium_native as AN, kicad_import as K
    fp = _rich_fp()
    lib = tmp_path / "R.PcbLib"
    AL.write_pcblib(lib, [fp])
    assert AN.compare_one(lib, lib.with_name(lib.name + AN.EXT)) == []
    # what Altium changes by itself (docs/altium-native.md): primitive order, track end order; and a region's first point
    alt = dict(fp, tracks=[(x2, y2, x1, y1, w, l) for x1, y1, x2, y2, w, l in reversed(fp["tracks"])],
               arcs=list(reversed(fp["arcs"])), regions=[(r[0][1:] + r[0][:1], *r[1:]) for r in reversed(fp["regions"])],
               texts=list(reversed(fp["texts"])))
    d = tmp_path / "alt"
    d.mkdir()
    lib2, rp2 = _built(d, fp, alt)
    assert AN.compare_one(lib2, rp2) == [], AN.compare_one(lib2, rp2)
    assert AN.lib_state(lib2, rp2) == "built"
    # every footprint the repo's package specs and KiCad fixtures make compares equal to itself
    root = Path(AL.__file__).parent.parent
    fps = [AL.footprint_from_spec(json.loads(f.read_text()))[0] for f in sorted((root / "ecadlib/package_specs").glob("*.json"))]
    for f in sorted((root / "tests/fixtures/kicad_golden").glob("*.kicad_mod")):
        fps.append(K.convert_footprint(f.read_text(encoding="utf-8"))[0])
    assert len(fps) > 40
    for i, f in enumerate(fps):
        lib = tmp_path / f"S{i}.PcbLib"
        AL.write_pcblib(lib, [f])
        probs = AN.compare_one(lib, lib.with_name(lib.name + AN.EXT))
        assert probs == [], (f["name"], probs)


@lite
def test_native_compare_finds_missing_or_changed_primitives(tmp_path):
    from ecadlib import altium_native as AN
    fp = _rich_fp()
    last = fp["regions"][-1]
    cases = {
        "a copper region gone": dict(fp, regions=fp["regions"][:-3] + fp["regions"][-2:]),
        "keep-out region no keep-out": dict(fp, regions=[(r[0], r[1]) for r in fp["regions"]]),
        "no tracks": dict(fp, tracks=[]),
        "track moved": dict(fp, tracks=[(x1 + 0.1, y1, x2 + 0.1, y2, w, l) for x1, y1, x2, y2, w, l in fp["tracks"]]),
        "track on another layer": dict(fp, tracks=fp["tracks"][:-1] + [fp["tracks"][-1][:5] + (fp["tracks"][-1][5] + 1,)]),
        "arc angle": dict(fp, arcs=fp["arcs"][:-1] + [(1.0, 1.0, 0.3, 45.0, 180.0, 0.1, fp["arcs"][-1][6])]),
        "fill gone": dict(fp, fills=fp["fills"][:-1]),
        "text changed": dict(fp, texts=fp["texts"][:-1] + [(".Comment",) + tuple(fp["texts"][-1][1:])]),
        "extruded body height": dict(fp, bodies=[dict(fp["bodies"][0], height=2.5), fp["bodies"][1]]),
        "STEP model renamed": dict(fp, bodies=[fp["bodies"][0],
                                               dict(fp["bodies"][1], step=dict(fp["bodies"][1]["step"], name="other.step"))]),
        "body gone": dict(fp, bodies=fp["bodies"][:1]),
        "Mechanical 13 region on 15": dict(fp, regions=fp["regions"][:-1] + [(last[0], last[1] + 2)]),
    }
    for label, alt in cases.items():
        d = tmp_path / label.replace(" ", "_")
        d.mkdir()
        lib, rp = _built(d, fp, alt)
        assert AN.compare_one(lib, rp), label                 # was [] for every one: only pads were compared
        assert AN.lib_state(lib, rp) == "edited", label        # was "built": recorded as Altium's build


@lite
def test_native_compare_l_shaped_custom_pad(tmp_path):
    """The reviewer's case: a KiCad custom pad (anchor + copper REGION on Top) built without its region and outlines."""
    from ecadlib import altium_native as AN, kicad_import as K
    mod = '''(footprint "L_PAD" (layer "F.Cu")
  (fp_rect (start -2 -2) (end 2 2) (stroke (width 0.05)) (fill none) (layer "F.CrtYd"))
  (fp_rect (start -1.5 -1.5) (end 1.5 1.5) (stroke (width 0.1)) (fill none) (layer "F.Fab"))
  (pad "1" smd custom (at -1 0) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask")
    (options (clearance outline) (anchor rect))
    (primitives (gr_poly (pts (xy -0.25 -0.25) (xy 1.5 -0.25) (xy 1.5 0.25) (xy -0.25 0.25)) (width 0) (fill yes))))
  (pad "2" smd rect (at 1 1) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask"))
)'''
    fp = K.convert_footprint(mod)[0]
    assert fp["regions"]
    lib, rp = _built(tmp_path, fp, dict(fp, regions=[], tracks=[], arcs=[], fills=[]))
    probs = AN.compare_one(lib, rp)
    assert any("region" in p for p in probs), probs
    assert AN.lib_state(lib, rp) == "edited"


@lite
def test_native_compare_symbol_graphics(tmp_path):
    from ecadlib import altium_lib as AL, altium_native as AN
    pins = [{"num": str(i), "name": f"P{i}", "type": "io", "side": "left" if i < 3 else "right"} for i in range(1, 5)]
    sym = AL.ic_symbol("U4", pins, "Test", footprint="SOIC8", layout=False)
    lib = tmp_path / "U.SchLib"
    AL.write_schlib(lib, [sym])
    rp = lib.with_name(lib.name + AN.EXT)
    assert AN.compare_one(lib, rp) == []
    keep = [i for i, pr in enumerate(sym["prims"]) if pr[0] != "rect"]          # Altium's build lost the body rectangle
    assert len(keep) < len(sym["prims"])
    alt = dict(sym, prims=[sym["prims"][i] for i in keep],
               **{k: [sym[k][i] for i in keep] for k in ("prim_parts", "prim_modes") if sym.get(k)})
    lib.write_bytes(AL.schlib_bytes([alt]))
    probs = AN.compare_one(lib, rp)
    assert any("rect" in p.lower() for p in probs), probs


# ---------------------------------------------------------------- R-BR-3: a small hostile .PcbLib cannot take GBs
def _soic_fp():
    from pathlib import Path
    from ecadlib import altium_lib as AL
    spec = json.loads((Path(AL.__file__).parent / "package_specs" / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    return AL.footprint_from_spec(spec)[0]


def _tree(path):
    import olefile
    tree = {}
    with olefile.OleFileIO(str(path)) as o:
        for e in o.listdir():
            node = tree
            for part in e[:-1]:
                node = node.setdefault(part, {})
            node[e[-1]] = o.openstream(e).read()
    return tree


@lite
def test_pcblib_zlib_bomb_model_is_not_unpacked_on_read(tmp_path):
    import struct, tracemalloc, zlib
    from ecadlib import altium_lib as AL, cfb
    plain = tmp_path / "plain.PcbLib"
    AL.write_pcblib(plain, [_soic_fp()])
    tree = _tree(plain)
    c = zlib.compressobj(9)
    comp = b"".join(c.compress(b"\0" * (1 << 24)) for _ in range(5)) + c.flush()        # 80 MB of zeros in ~80 kB
    models = tree["Library"].setdefault("Models", {})
    models.update(Header=struct.pack("<I", 1), Data=AL.pblock("|EMBED=TRUE|ID={X}|NAME=x.step|"), **{"0": comp})
    bomb = tmp_path / "bomb.PcbLib"
    cfb.write_cfb(bomb, tree)
    tracemalloc.start()
    try:
        got = AL.read_pcblib(bomb)                            # was: 80 MB (1 GB for the reviewer's file) unpacked here
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 20 * 2 ** 20, peak
    assert got[0]["records"] and len(got[0]["models"]) == 1
    params, _ = got[0]["models"].packed(0)
    assert params["NAME"] == "x.step"
    try:
        got[0]["models"][0]
    except ValueError as e:
        assert "3D model" in str(e) and "MB" in str(e), e
    else:
        raise AssertionError("an 80 MB model was unpacked past the cap")


@lite
def test_pcblib_stream_bigger_than_the_file_is_refused(tmp_path):
    import olefile, struct, time
    from ecadlib import altium_lib as AL
    p = tmp_path / "plain.PcbLib"
    AL.write_pcblib(p, [_soic_fp()])
    d = bytearray(p.read_bytes())
    with olefile.OleFileIO(str(p)) as o:
        sid = o._find("Library/Data")
        e = o.direntries[sid]
        assert e.size >= 4096                                  # regular sectors: its FAT entry is made to point at itself
        hdr = struct.unpack_from("<109I", d, 76)
    struct.pack_into("<I", d, 512 * (1 + hdr[0]) + 4 * e.isectStart, e.isectStart)
    first_dir = struct.unpack_from("<I", d, 48)[0]
    struct.pack_into("<Q", d, 512 * (1 + first_dir) + 128 * sid + 120, 64 << 20)        # and its size says 64 MB
    f = tmp_path / "loop.PcbLib"
    f.write_bytes(d)
    t = time.time()
    try:
        AL.read_pcblib(f)
    except ValueError as ex:
        assert "Library/Data" in str(ex) and "bigger than the file" in str(ex), ex     # was: read 64 MB of one sector
    else:
        raise AssertionError("a looping 64 MB stream was read")
    assert time.time() - t < 2


@lite
def test_real_libraries_still_read_with_their_models(tmp_path):
    from pathlib import Path
    from ecadlib import altium_lib as AL
    root = Path(AL.__file__).parent.parent
    for f in sorted(root.glob("out/altium_models/PcbLib/*.PcbLib")):
        assert AL.read_pcblib(f)[0]["records"], f
    for f in sorted(root.glob("out/altium_models/SchLib/*.SchLib")):
        assert AL.read_schlib(f)[1], f
    data = b"ISO-10303-21;\n" + bytes(range(256)) * 4000                # a 1 MB embedded STEP model
    fp = dict(_soic_fp(), bodies=[{"pts": [(-1, -1), (1, -1), (1, 1), (-1, 1)], "height": 1.5,
                                   "step": {"name": "S.step", "data": data, "rot": (0.0, 0.0, 0.0)}}])
    lib = tmp_path / "S.PcbLib"
    AL.write_pcblib(lib, [fp])
    models = AL.read_pcblib(lib)[0]["models"]
    assert len(models) == 1 and models
    [(params, got)] = list(models)
    assert got == data and params["NAME"] == "S.step"
    assert models[0][1] == data and {str(mp.get("ID")): d for mp, d in models}
