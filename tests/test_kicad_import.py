"""Offline tests for ecadlib.kicad_import and the through-hole / arc / 3D-body records in altium_lib.
Run:  python3 -m tests.test_kicad_import

Fixtures (tests/fixtures/kicad, CC-BY-SA 4.0 KiCad libraries, see NOTICE): the HRO TYPE-C-31-M-12 footprint
(slotted and non-plated holes) and AP1117-15 + its derived AMS1117-3.3 in one symbol library. The other KiCad
snippets below are written for these tests. REF_* records are from AltiumSharp TestData (Altium-saved files)."""
import base64, json, math, struct, tempfile, uuid, zlib
from pathlib import Path

from ecadlib import altium365 as A, altium_lib as AL, kicad_import as K, sexpr
from ecadlib.httpcache import CachedHTTP

FIX = Path(__file__).parent / "fixtures" / "kicad"
unz = lambda s: zlib.decompress(base64.b64decode(s))
# PAD_TH_SLOT.PcbLib pad 1: 202-byte main block + 596-byte size/shape block (its 55-byte AD24 full-stack tail cut)
REF_SLOT_PAD = unz("eNrz4mH4DwcMUODgxM/QIMWGSfMyMzAxMcGUMULpBW2MDCxgEqQAhMGGzGGAK2RkQAB+BmZGmLT68f7n5w+Ve7fbqjY+9jwz6Z8Ah32Q+wrXyfF6"
                   "T6d1rOgAqQe6qx6EGRilkExBMRLuaHpjUKDQGzPhAwxMB9Q5GUYBPJXgB4T0AwAZMk29")
# ARCS_TEST.PcbLib second arc, and BODY_3D.PcbLib's extruded body (record payloads without the type byte)
REF_ARC = bytes.fromhex("010c00ffffffffffffffffffff0000000000000000b0710b0000000000008056400000000000806640a0860100000000000000000100000100000000")
REF_BODY = unz("eNp1ks1uozAUhVHVZZ+hyr4C2YRMwsKLi30pbgxGtokgm64rdZ5gPNIs+jh9iT5Zy1+ZJJO5C9vnu0c614b0LvhcKhjq/SYIDttnBR0aViIvoJIc"
               "FPUVlMhWfi8rwYi3TVZr1fUCWxZS31RSV5MiHgw3aLVqXA8ZiTY/X169tLaAGjOwKFgOyqLncJCuK1A+Fo6RwWQdVELn+SnTBzSg1IwoGWGmRVcb"
               "/YR8yrieObi4VtqsBdslMd2QZGS6Bt4n95RGhBAvBVZO5rK/snfYusbgcuB9C007zXLGujNm5RHbf8i5x2gH43wrMuSeFD4Mqy+1QCUF+wW4BbrN"
               "8hAo2YVJEich8BjC9TZOSZ6uMc3S35M94gXyvW1KFie7DaHx7gedO1hmy2NPZPyK8zkW0TzworsLvQw8zjvz9cjbK6y7wo6XTBxPQ8bVdTWyb0v/"
               "VKYRKKJSVmfWvw1oj/OPENwGU/15iz6m/R4u9LxH8B8NX5EP3Gw=")

TEST_FP = """(footprint "TEST_FP" (layer "F.Cu") (descr "test | footprint")
  (pad "1" smd rect (at 1 2 90) (size 1 0.5) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" thru_hole oval (at -2 0 90) (size 1.2 2.4) (drill oval 0.6 1.8) (layers "*.Cu" "*.Mask"))
  (pad "3" thru_hole roundrect (at 3 0 45) (size 1.7 1.7) (drill 1) (roundrect_rratio 0.25) (layers "*.Cu" "*.Mask"))
  (pad "4" smd custom (at 0 -3) (size 0.5 0.5) (layers "F.Cu" "F.Mask") (options (clearance outline) (anchor rect))
    (primitives (gr_poly (pts (xy -0.25 -0.25) (xy 1.75 -0.25) (xy 1.75 0.25) (xy -0.25 0.25)) (width 0) (fill yes))))
  (pad "" np_thru_hole circle (at 0 3) (size 1.5 1.5) (drill 1.2) (layers "*.Cu" "*.Mask"))
  (fp_arc (start -1 0) (mid 0 -1) (end 1 0) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
  (fp_circle (center 0 0) (end 0.25 0) (stroke (width 0.1) (type solid)) (fill solid) (layer "F.SilkS"))
  (fp_rect (start -4 -4) (end 4 4) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))
  (fp_poly (pts (xy -3 -3) (xy 3 -3) (xy 3 3) (xy -3 3) (arc (start -3 3) (mid -3.5 0) (end -3 -3)))
    (stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab"))
  (fp_text user "${REFERENCE}" (at 0 0) (layer "F.Fab"))
  (model "${KICAD9_3DMODEL_DIR}/Test.3dshapes/TEST_FP.wrl" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
)"""

# two units, a De Morgan body, KiCad 7 bare `hide` and KiCad 9 `(hide yes)`, a stacked hidden pin, an overbar name
TEST_SYM = """(kicad_symbol_lib (version 20231120) (generator "kicad_symbol_editor")
  (symbol "DUAL" (pin_names (offset 1.016) hide) (in_bom yes) (on_board yes)
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "DUAL" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "Package_SO:SOIC-8" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Description" "dual gate" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "MPN" "DUAL-1" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (symbol "DUAL_0_1"
      (rectangle (start -5.08 5.08) (end 5.08 -5.08) (stroke (width 0.254) (type default)) (fill (type background)))
      (pin power_in line (at 0 7.62 270) (length 2.54) hide (name "VCC" (effects (font (size 1.27 1.27)))) (number "8" (effects (font (size 1.27 1.27)))))
      (pin power_in line (at 0 -7.62 90) (length 2.54) (hide yes) (name "GND" (effects (font (size 1.27 1.27)))) (number "4" (effects (font (size 1.27 1.27))))))
    (symbol "DUAL_1_1"
      (arc (start -2.54 0) (mid 0 2.54) (end 2.54 0) (stroke (width 0) (type default)) (fill (type none)))
      (circle (center 0 0) (radius 0.635) (stroke (width 0) (type default)) (fill (type outline)))
      (polyline (pts (xy -1.27 -1.27) (xy 1.27 -1.27) (xy 0 1.27) (xy -1.27 -1.27)) (stroke (width 0) (type default)) (fill (type background)))
      (text "&" (at 0 -2.54 0) (effects (font (size 1.27 1.27))))
      (pin input line (at -7.62 0 0) (length 2.54) (name "~{RST}" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      (pin passive line (at -7.62 0 0) (length 2.54) hide (name "RST" (effects (font (size 1.27 1.27)))) (number "5" (effects (font (size 1.27 1.27))))))
    (symbol "DUAL_1_2" (rectangle (start -1 -1) (end 1 1) (stroke (width 0) (type default)) (fill (type none))))
    (symbol "DUAL_2_1"
      (pin output line (at 7.62 0 180) (length 2.54) (name "Y" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27))))))))"""


def pads_of(path):
    return [r for r in AL.read_pcblib(path)[0]["records"] if r["type"] == "pad"]


def test_tht_slot_pad_matches_altium_bytes():
    m, ss = REF_SLOT_PAD[:202], REF_SLOT_PAD[202:]
    x, y, w, h = struct.unpack_from("<4i", m, 13)
    p = {"designator": "1", "x": x, "y": y, "w": w, "h": h, "shape": "rect", "layer": AL.MULTI, "hole": struct.unpack_from("<i", m, 45)[0],
         "hole_shape": "slot", "slot": struct.unpack_from("<i", ss, 263)[0], "plated": True}
    rec = AL.pad_rec(p, uuid.UUID(bytes_le=m[126:142]), uuid.UUID(bytes_le=m[142:158]))
    assert rec.endswith(AL.block(m) + AL.block(ss)), "slotted pad differs from the Altium-saved record"
    assert m[0] == AL.MULTI and m[60] == 1 and m[185] == 1 and ss[262] == 2       # multi-layer, plated, THT marker, slot


def test_arc_and_body_records_match_altium_bytes():
    cx, cy, r, a0, a1, w = struct.unpack_from("<3i2di", REF_ARC, 13)
    assert AL.arc_rec(cx, cy, r, a0, a1, w, 1) == b"\x01" + AL.block(REF_ARC)
    pl = struct.unpack_from("<I", REF_BODY, 18)[0]
    bp = AL.parse_params(REF_BODY[22:22 + pl - 1].decode())
    pts = [struct.unpack_from("<2d", REF_BODY, 26 + pl + 16 * k) for k in range(4)]
    txt = AL.body_params(57, AL.mm(2.54), bp["MODELID"], int(bp["MODEL.CHECKSUM"]))
    assert AL.body_rec([(int(x), int(y)) for x, y in pts], txt, 57) == b"\x0c" + AL.block(REF_BODY)
    assert AL.step_checksum(b"ISO") == (ord("I") + ord("S") * 1 + ord("O") * 2)


def test_usb_c_fixture_tht_roundtrip():
    notes = []
    fp, t = K.convert_footprint((FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text(), "USB_C_Receptacle_16P", "KiCad x", notes)
    K.add_body(fp, t, None, None, False, notes)                     # no network: height from DEFAULT_HEIGHT
    assert fp["height_mm"] == 3.3 and any("default table" in n for n in notes), notes
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "u.PcbLib", [fp])
        pads = pads_of(Path(d) / "u.PcbLib")
        sh = [p for p in pads if p["designator"] == "SH"]
        npth = [p for p in pads if p["designator"] == ""]
        assert len(pads) == 22 and len(sh) == 4 and len(npth) == 2
        big = [p for p in sh if p["h"] == AL.mm(2.1)]
        assert len(big) == 2 and all(p["layer"] == AL.MULTI and p["plated"] and p["hole"] == AL.mm(0.6) and p["hole_shape"] == 2
                                     and p["slot"] == AL.mm(1.7) and p["hole_rot"] == 90.0 and p["w"] == AL.mm(1) for p in big), big
        assert all(not p["plated"] and p["hole"] == AL.mm(0.65) == p["w"] and p["hole_shape"] == 0 for p in npth)
        a9 = next(p for p in pads if p["designator"] == "A9")
        assert (a9["x"], a9["y"], a9["shape"], a9["corner"], a9["layer"]) == (AL.mm(2.45), AL.mm(4.045), 9, 50, AL.TOP)
        lib = __import__("pyaltiumlib").read(str(Path(d) / "u.PcbLib"))
        recs = lib.Parts[0].Records
        ps = [r for r in recs if type(r).__name__ == "PcbPad" and r.designator == "SH"]
        assert len(ps) == 4 and str(ps[0].hole_shape) .lower().endswith("slot") and abs(float(ps[0].hole_slot_length) - 1.7 / 0.0254) < 0.01
        assert any(type(r).__name__ == "PcbComponentBody" for r in recs)


def test_rotated_pad_slot_custom_and_arcs():
    notes = []
    fp, t = K.convert_footprint(TEST_FP, notes=notes)
    K.add_body(fp, t, None, 2.0, False, notes)
    assert "|" not in fp["description"]
    p = {q["designator"]: q for q in fp["pads"]}
    assert (p["1"]["rot"], p["1"]["y"], p["1"]["paste"]) == (90.0, -2.0, None)
    assert (p["2"]["rot"], p["2"]["w"], p["2"]["h"], p["2"]["hole_rot"], p["2"]["slot"]) == (0.0, 2.4, 1.2, 0.0, 1.8)   # baked 90 deg
    assert p["3"]["rot"] == 45.0 and p["3"]["corner"] == 50 and p["3"]["plated"]
    assert (p["4"]["w"], p["4"]["h"], p["4"]["x"], p["4"]["y"], p["4"]["shape"], p["4"]["mask"]) == (0.5, 0.5, 0.0, 3.0, "rect", 0.0)
    shape = [(-0.25, 3.25), (1.75, 3.25), (1.75, 2.75), (-0.25, 2.75)]          # custom pad: anchor + its polygon
    assert [r for r in fp["regions"] if r[1] in (AL.TOP, AL.TOP_SOLDER)] == [(shape, AL.TOP), (shape, AL.TOP_SOLDER)]
    assert p[""]["w"] == 1.2 and not p[""]["plated"]
    arcs = {round(a[2], 4): a for a in fp["arcs"]}
    cx, cy, r, a0, a1, w, layer = arcs[1.0]                         # (-1,0)->(0,1)->(1,0) in y-up is clockwise
    assert (round(cx, 9), round(cy, 9), round(a0, 6), round(a1, 6), layer) == (0, 0, 0, 180, AL.TOP_OVERLAY)
    assert arcs[0.15][3:6] == (0.0, 360.0, 0.3)                     # filled 0.25 mm circle + 0.1 stroke
    body = fp["bodies"][0]
    assert body["height"] == 2.0 and min(x for x, _ in body["pts"]) == -3.5   # fab outline incl. the poly arc
    assert any("custom shape" in n for n in notes) and any("fp_text" in n for n in notes) and any("caller" in n for n in notes)
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "t.PcbLib", [fp])
        got = AL.read_pcblib(Path(d) / "t.PcbLib")[0]
        rp = {q["designator"]: q for q in got["records"] if q["type"] == "pad"}
        assert rp["1"]["rot"] == 90.0 and rp["3"]["rot"] == 45.0 and rp["2"]["hole_shape"] == 2 and rp["3"]["hole_shape"] == 0
        ra = [q for q in got["records"] if q["type"] == "arc"]
        assert any(q["r"] == AL.mm(1) and (q["a0"], q["a1"]) == (0.0, 180.0) for q in ra)
        bd = [q for q in got["records"] if q["type"] == "body"][0]
        bp = AL.parse_params(bd["params"])
        assert got["records"][-1] is bd and bp["MODEL.MODELTYPE"] == "0" and bp["OVERALLHEIGHT"] == "78.7402mil"
        assert bp["V7_LAYER"] == "MECHANICAL13" and got["params"]["HEIGHT"] == "78.7402mil"
        lib = __import__("pyaltiumlib").read(str(Path(d) / "t.PcbLib"))
        kinds = [type(r).__name__ for r in lib.Parts[0].Records]
        assert kinds.count("PcbPad") == 5 and kinds.count("PcbArc") == 2 and kinds[-1] == "PcbComponentBody", kinds
        pa = [r for r in lib.Parts[0].Records if type(r).__name__ == "PcbArc"]
        assert any(abs(float(a.radius) - 1 / 0.0254) < 0.01 for a in pa)


def test_embedded_step_model():
    step = ("ISO-10303-21;\r\nDATA;\r\n#1=CARTESIAN_POINT('',(-1.,-0.5,0.));\r\n#2=CARTESIAN_POINT('',(1.,0.5,1.25));\r\n"
            "#3=CARTESIAN_POINT('',(0.,0.,0.1));\r\nENDSEC;\r\nEND-ISO-10303-21;\r\n")
    with tempfile.TemporaryDirectory() as d:
        http = CachedHTTP(d, offline=True)
        url = K.GITLAB.format(repo="kicad-packages3D", path="Test.3dshapes/TEST_FP.step")
        (http.dir / f"{http._key('GET', url, None)}.json").write_text(json.dumps({"url": url, "status": 200, "text": step}))
        notes = []
        fp, t = K.convert_footprint(TEST_FP, notes=notes)
        K.add_body(fp, t, http, None, True, notes)
        assert fp["height_mm"] == 1.25 and fp["bodies"][0]["pts"] == [(-1, -0.5), (1, -0.5), (1, 0.5), (-1, 0.5)], fp["bodies"]
        AL.write_pcblib(Path(d) / "s.PcbLib", [fp])
        got = AL.read_pcblib(Path(d) / "s.PcbLib")[0]
        (mp, data), = got["models"]
        bp = AL.parse_params([q for q in got["records"] if q["type"] == "body"][0]["params"])
        assert data == step.encode() and int(mp["CHECKSUM"]) == AL.step_checksum(data) and mp["ID"] == bp["MODELID"]
        assert bp["MODEL.EMBED"] == "TRUE" and bp["MODEL.NAME"] == mp["NAME"] == "TEST_FP.step" and bp["MODEL.MODELTYPE"] == "1"
        assert int(bp["MODEL.CHECKSUM"]) == int(mp["CHECKSUM"]) & 0xFFFFFFFF


def test_derived_symbol_and_symdir():
    ref = f"{FIX}/Regulator_Linear_AMS1117.kicad_sym:AMS1117-3.3"
    chain, _ = K.load_symbol(ref, None)
    assert [s[1] for s in chain] == ["AMS1117-3.3", "AP1117-15"]
    notes = []
    sym = K.convert_symbol(chain, footprint="SOT-223-3_TabPin2", notes=notes)
    pins = {pr[1]["num"]: pr for pr in sym["prims"] if pr[0] == "pin"}
    params = dict(sym["params"])
    assert sorted(pins) == ["1", "2", "3"] and params["Value"] == "AMS1117-3.3" and params["KiCad Footprint"].endswith("SOT-223-3_TabPin2")
    assert "3.3V" in sym["description"] and sym["designator"] == "U?"
    with tempfile.TemporaryDirectory() as d:                       # KiCad 9 symdir: one symbol per file, parent beside it
        lib = sexpr.parse((FIX / "Regulator_Linear_AMS1117.kicad_sym").read_text())
        src = (FIX / "Regulator_Linear_AMS1117.kicad_sym").read_text()
        for s in sexpr.find(lib, "symbol"):
            body = src[src.index(f'(symbol "{s[1]}"'):]
            depth, end = 0, 0
            for i, c in enumerate(body):
                depth += c == "("; depth -= c == ")"
                if depth == 0:
                    end = i + 1; break
            Path(d, f"{s[1]}.kicad_sym").write_text(f'(kicad_symbol_lib (version 20241209)\n{body[:end]}\n)')
        chain2, _ = K.load_symbol(f"{d}/AMS1117-3.3.kicad_sym", None)
        assert [s[1] for s in chain2] == ["AMS1117-3.3", "AP1117-15"]
        AL.write_schlib(Path(d) / "a.SchLib", [sym])
        _, syms = AL.read_schlib(Path(d) / "a.SchLib")
        rp = {r["num"]: r for r in syms[0]["records"] if r["RECORD"] == "2"}
        kp = sexpr.first(next(x for x in sexpr.find(chain[-1], "symbol") if x[1].endswith("_1_1")), "pin")
        n = sexpr.first(kp, "number")[1]
        x, y, ang = (float(v) for v in sexpr.first(kp, "at")[1:4])
        L = float(sexpr.first(kp, "length")[1])
        bx, by = x + L * math.cos(math.radians(ang)), y + L * math.sin(math.radians(ang))
        assert (rp[n]["x"], rp[n]["y"]) == (round(bx / 0.254), round(by / 0.254)) and rp[n]["orient"] == {0: 2, 90: 3, 180: 0, 270: 1}[int(ang)]
        names = {r.get("NAME"): r.get("TEXT") for r in syms[0]["records"] if r["RECORD"] == "41"}
        assert names["Value"] == "AMS1117-3.3" and names["Datasheet"].startswith("http")
        assert len([r for r in __import__("pyaltiumlib").read(str(Path(d) / "a.SchLib")).Parts[0].Records if type(r).__name__ == "SchPin"]) == 3


def test_hidden_power_pins_and_units():
    notes = []
    with tempfile.TemporaryDirectory() as d:
        Path(d, "t.kicad_sym").write_text(TEST_SYM)
        chain, _ = K.load_symbol(f"{d}/t.kicad_sym:DUAL", None)
        sym = K.convert_symbol(chain, "DUAL-1", extra=[("MPN", "DUAL-1")], notes=notes)
        assert sym["parts"] == 2 and sym["footprint"] == "SOIC-8" and any("De Morgan" in n for n in notes)
        assert "Datasheet" not in dict(sym["params"])                # '~' = none
        AL.write_schlib(Path(d) / "d.SchLib", [sym])
        hdr, syms = AL.read_schlib(Path(d) / "d.SchLib")
        recs = syms[0]["records"]
        assert hdr["PARTCOUNT0"] == "3" and recs[0]["PARTCOUNT"] == "3"
        pins = [r for r in recs if r["RECORD"] == "2"]
        by = lambda num: [r for r in pins if r["num"] == num]
        assert sorted(r["part"] for r in by("8")) == [1, 2] and [r["part"] for r in by("1")] == [1] and [r["part"] for r in by("2")] == [2]
        vcc = by("8")[0]
        assert vcc["conglomerate"] & 0x04 and (vcc["x"], vcc["y"], vcc["orient"], vcc["length"]) == (0, 20, 1, 10)
        nets = [(recs[int(r["OWNERINDEX"])]["num"], r["TEXT"]) for r in recs if r.get("NAME") == "HiddenNetName"]
        assert sorted(set(nets)) == [("4", "GND"), ("8", "VCC")], nets
        rst, stacked = by("1")[0], by("5")[0]
        assert rst["name"] == "R\\S\\T\\" and (rst["x"], rst["y"], rst["orient"]) == (-20, 0, 2) and not rst["conglomerate"] & 0x08   # pin_names hide
        assert rst["conglomerate"] & 0x10 and not stacked["conglomerate"] & 0x1C and (stacked["x"], stacked["y"]) == (-20, 0)
        y = by("2")[0]
        assert (y["x"], y["orient"], y["etype"]) == (20, 0, AL.ETYPE["output"])
        kinds = {r["RECORD"] for r in recs}
        assert {"12", "8", "7", "4", "14"} <= kinds, kinds
        arc = next(r for r in recs if r["RECORD"] == "12")
        assert (arc.get("STARTANGLE", "0"), arc["ENDANGLE"], arc["RADIUS"]) == ("0", "180.000", "10")
        lib = __import__("pyaltiumlib").read(str(Path(d) / "d.SchLib"))
        assert len([r for r in lib.Parts[0].Records if type(r).__name__ == "SchPin"]) == 7


def test_pin_pad_check_and_plan_with_two_model_dirs():
    fp, _ = K.convert_footprint((FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text())
    sym = {"prims": [("pin", {"num": n}, "left", 0, 0, 10) for n in ("A1", "SH", "X9")]}
    nopad, nopin = K.pin_pad_check(sym, fp)
    assert nopad == ["X9"] and "B12" in nopin and "" not in nopin
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d, "a"), Path(d, "b")
        (a / "SchLib").mkdir(parents=True); (b / "PcbLib").mkdir(parents=True)
        (a / "SchLib" / "X1.SchLib").write_bytes(b"x"); (b / "PcbLib" / "SOT-23.PcbLib").write_bytes(b"x")
        items = A.plan([{"designator": "Q1", "mpn": "X1", "footprint": "SOT-23"}], None, [a, b])
        assert items[0]["status"] == "ready" and items[0]["footprint"]["file"].startswith(str(b))
        assert A.plan([{"designator": "Q1", "mpn": "X1", "footprint": "SOT-23"}], None, a)[0]["status"] == "needs_altium_models"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
