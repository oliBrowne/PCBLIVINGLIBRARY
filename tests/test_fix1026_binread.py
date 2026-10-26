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
