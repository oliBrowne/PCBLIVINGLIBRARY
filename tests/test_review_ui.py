"""Regression tests for the independent review of the UI / MCP / footprint code (items U1-U14 in
reviews/2026-10-13_independent_review.md). Run:  python3 -m tests.test_review_ui"""
import io, json, os, subprocess, sys, tempfile, time
from pathlib import Path

import jsonschema

from ecadlib import app as APP, footprint_check as C, footprint_gen as F, mcp_server as M, sexpr
from ecadlib.app import Workspace, footprint_svg
from tests.test_ui import SPEC, serve, wait_job

EVIL = '</title></rect><image href=x onerror=alert(document.domain) /><rect><title>'


def test_u1_svg_escapes_pad_numbers():
    rep = {"courtyard": (2, 2), "pads": [{"num": EVIL, "x": 0, "y": 0, "w": 1, "h": 1}]}
    svg = footprint_svg(rep)
    assert "<image" not in svg and "&lt;/title&gt;" in svg, svg
    spec = SPEC("SOT-223-3_TabPin2")
    spec["tab"]["number"] = EVIL
    try:
        F.validate(spec)
        raise AssertionError("schema accepted markup as a pad number")
    except jsonschema.ValidationError:
        pass
    with tempfile.TemporaryDirectory() as d:                   # and the UI endpoint refuses it
        srv, call = serve(Workspace(d, providers=["digikey"]))
        try:
            st, r = call("POST", "/api/footprint/preview", {"spec": spec})
            assert st == 400 and "isn't valid" in r["error"], (st, r)
        finally:
            srv.shutdown()


def _mcp(lines):
    out = io.BytesIO()
    with tempfile.TemporaryDirectory() as d:
        M.serve(Workspace(d, providers=["digikey"]), io.BytesIO(b"\n".join(lines) + b"\n"), out)
    return [json.loads(l) for l in out.getvalue().decode().splitlines()]


def test_u2_mcp_survives_bad_lines():
    ping = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode()
    bad = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": {"x": 1}}}).encode(),
           b"[" * 100000 + b"]" * 100000,                         # nested deeper than Python's parser allows
           b'{"jsonrpc": "2.0", "id": 2, "method": "ping", "x": "\xff\xfe"}',   # invalid UTF-8
           b"not json"]
    out = _mcp(bad + [ping])
    assert out[-1] == {"jsonrpc": "2.0", "id": 9, "result": {}}, out[-1]
    assert out[0]["error"]["code"] == -32602 and out[1]["error"]["code"] == -32700, out[:2]
    assert out[2]["result"] == {} and out[3]["error"]["code"] == -32700, out[2:4]
    # UTF-8 both ways whatever the locale: a non-ASCII tool error comes back intact
    call = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "preview_footprint", "arguments": {"spec": {"name": "Ω"}}}}).encode()
    r = _mcp([call])[0]["result"]
    assert r["isError"] is True and r["content"][0]["text"], r
    p = subprocess.run([sys.executable, "-m", "ecadlib", "mcp", "--workspace", tempfile.mkdtemp()],
                       input=b"\xff\xfe garbage\n" + ping + b"\n", capture_output=True, timeout=60,
                       env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert p.returncode == 0 and json.loads(p.stdout.splitlines()[-1])["id"] == 9, p


def _err(spec):
    try:
        F.build(spec)
    except ValueError as e:
        return str(e)
    raise AssertionError("no error")


def test_u3_pin_counts_are_checked():
    spec = SPEC("SOIC-8_3.9x4.9mm_P1.27mm")
    spec["pin_count"] = 5
    assert "odd" in _err(spec)
    spec = SPEC("QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm")
    spec["pins_left_right"], spec["pins_top_bottom"] = 5, 4
    assert "pins_left_right" in _err(spec)
    spec = SPEC("SOT-23")
    spec["pin_count"] = 4
    assert "pin_count is 4 but the layout gives 3" in _err(spec)


def test_u4_lead_types():
    spec = SPEC("R_0603_1608Metric")
    for lt in ("concave", "convex"):
        spec["lead_type"] = lt
        try:
            F.validate(spec)
            raise AssertionError(f"schema still allows {lt}")
        except jsonschema.ValidationError:
            pass
    for lt in ("molded", "melf", "flat"):
        spec["lead_type"] = lt
        F.validate(spec)
        assert F.build(spec)[1]["ipc_family"] == lt
    spec = SPEC("SOIC-8_3.9x4.9mm_P1.27mm")
    spec["lead_type"] = "molded"
    assert "doesn't apply" in _err(spec)


def test_u5_clockwise_numbering_is_caught():
    txt, rep = F.build(SPEC("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm"))
    assert not C.check(txt)
    # mirror left-right: pin 1 moves to the top-right, numbering runs clockwise
    flipped = "\n".join(_mirror_x(l) if l.lstrip().startswith("(pad ") else l for l in txt.splitlines())
    found = {(lvl, rule) for lvl, rule, _ in C.check(flipped)}
    assert ("error", "pin1") in found, C.check(flipped)
    # renumbering two neighbours makes the order double back
    swapped = txt.replace('(pad "2" ', '(pad "X" ').replace('(pad "3" ', '(pad "2" ').replace('(pad "X" ', '(pad "3" ')
    assert any("doubles back" in m for _, _, m in C.check(swapped)), C.check(swapped)


def _mirror_x(line):
    import re
    return re.sub(r"\(at (-?[\d.]+) ", lambda m: f"(at {-float(m.group(1))} ", line)


def test_u6_triage_does_not_block_other_actions():
    slow = APP.T.triage

    def slow_triage(*a, **k):
        time.sleep(0.3)
        return slow(*a, **k)
    with tempfile.TemporaryDirectory() as d:
        APP.T.triage = slow_triage
        srv, call = serve(Workspace(d, providers=["digikey"]))
        try:
            st, j = call("POST", "/api/upload", Path("examples/sample_bom.csv").read_bytes(), {"X-Filename": "b.csv"})
            t0 = time.time()
            st, r = call("POST", "/api/footprint/approve", {"spec": SPEC("R_0603_1608Metric")})
            took = time.time() - t0
            assert st == 200 and took < 2.0, (st, took)             # 14 lines x 0.3 s would be > 4 s
            assert call("GET", f"/api/job?id={j['job']}")[1]["state"] == "running"
            assert wait_job_long(call, j["job"])["state"] == "done"
        finally:
            APP.T.triage = slow
            srv.shutdown()


def wait_job_long(call, jid):
    for _ in range(400):
        st, j = call("GET", f"/api/job?id={jid}")
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_u7_names_and_validation():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, providers=["digikey"])
        spec = SPEC("SOIC-8_3.9x4.9mm_P1.27mm")
        spec["name"] = "SOIC-8/TI_D"
        try:
            ws.approve_footprint(spec)
            raise AssertionError("path separator accepted")
        except ValueError as e:
            assert "SOIC-8/TI_D" in str(e), e                      # refused by the schema's name pattern
        assert not list((ws.gen_dir).glob("TI_D*"))
        try:
            ws.safe_name("SOIC-8/TI_D", strict=True)                # and by the workspace itself
            raise AssertionError("path separator accepted")
        except ValueError as e:
            assert "SOIC-8_TI_D" in str(e), e
        assert ws.safe_name("../../etc/BOM v2.csv") == "BOM_v2.csv"     # uploads keep just the base name
    bad = SPEC("R_0603_1608Metric")
    bad["body_length"] = {"min": "1.5", "max": 1.7}
    try:
        F.validate(bad)                                            # jsonschema is a runtime dependency now
        raise AssertionError("validation skipped")
    except jsonschema.ValidationError:
        pass
    toml = Path("pyproject.toml").read_text()
    assert '"jsonschema"' in toml.split("[project.optional-dependencies]")[0], "jsonschema must be a runtime dep"


def test_u8_example_specs_ship_with_the_package():
    toml = Path("pyproject.toml").read_text()
    assert "package_specs/*.json" in toml
    with tempfile.TemporaryDirectory() as d:
        specs = Workspace(d, providers=["digikey"]).example_specs()
    assert {"R_0603_1608Metric", "SOT-23", "LQFP-48_7x7mm_P0.5mm"} <= set(specs), sorted(specs)
    assert Path(APP.__file__).parent.joinpath("package_specs", "SOT-23.json").exists()


def test_u9_upload_symlink_not_followed():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
        ws = Workspace(d, providers=["digikey"])
        target = Path(outside) / "precious.txt"
        target.write_text("keep me")
        (ws.root / "uploads" / "bom.csv").symlink_to(target)
        (ws.root / "uploads" / "dangling.csv").symlink_to(Path(outside) / "nothing.csv")
        assert "uploads/bom.csv" not in ws.info()["boms"]           # info() still works with links present
        rel = ws.save_upload("bom.csv", b"Designator,Value\nR1,10k\n")
        assert target.read_text() == "keep me" and rel.startswith("uploads/") and rel.endswith("/bom.csv"), rel
        assert not (ws.root / rel).is_symlink()


def test_u10_checker_courtyard_rotation_and_filled_silk():
    txt, _ = F.build(SPEC("SOIC-8_3.9x4.9mm_P1.27mm"))
    lines = txt.splitlines()
    crt = next(i for i, l in enumerate(lines) if "F.CrtYd" in l)
    diag = lines[:crt] + ['  (fp_line (start -3.7 -2.7) (end 3.7 2.7) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))'] + lines[crt + 1:]
    assert ("error", "courtyard") in {(a, b) for a, b, _ in C.check("\n".join(diag))}
    # a pad rotated 45 degrees reaches its neighbour
    rot = txt.replace('(pad "2" smd roundrect (at -2.475 -0.635)', '(pad "2" smd roundrect (at -2.475 -0.635 45)')
    assert any(r == "copper" for _, r, _ in C.check(rot)), C.check(rot)
    blob = txt.rstrip()[:-1] + ('  (fp_poly (pts (xy -3.5 -2.3) (xy -1.4 -2.3) (xy -1.4 -1.4) (xy -3.5 -1.4)) '
                                '(stroke (width 0.12) (type solid)) (fill solid) (layer "F.SilkS")))\n')
    assert ("error", "silk") in {(a, b) for a, b, _ in C.check(blob)}, C.check(blob)


def test_u11_multiline_text_and_unterminated_strings():
    spec = SPEC("R_0603_1608Metric")
    spec["description"] = 'Line one\nline "two"\r\n\tthree'
    txt, _ = F.build(spec)
    descr = next(l for l in txt.splitlines() if l.lstrip().startswith("(descr"))
    assert 'Line one line \\"two\\" three' in descr and "\n" not in descr
    assert sexpr.first(sexpr.parse(txt), "descr")[1].startswith('Line one line "two" three')
    try:
        sexpr.parse('(footprint "x (layer "F.Cu"))')
        raise AssertionError("unterminated string accepted")
    except ValueError:
        pass


def test_u12_ui_refuses_non_loopback_host():
    p = subprocess.run([sys.executable, "-m", "ecadlib", "ui", "--host", "0.0.0.0", "--no-browser",
                        "--workspace", tempfile.mkdtemp()], capture_output=True, text=True, timeout=60)
    assert p.returncode != 0 and "refusing" in p.stderr, p
    from ecadlib.ui.server import _loopback
    assert _loopback("127.0.0.1") and _loopback("::1") and _loopback("localhost") and not _loopback("192.168.1.5")


def test_u13_reports_keyed_by_bom_location():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, providers=["digikey"], offline=True)
        for proj, ref in (("projA", "R1"), ("projB", "R9")):
            (ws.root / proj).mkdir()
            (ws.root / proj / "BOM.csv").write_text(f"Designator,Value,Footprint\n{ref},10k,0603\n")
        a, b = ws.triage("projA/BOM.csv"), ws.triage("projB/BOM.csv")
        assert a["reports"] != b["reports"], (a["reports"], b["reports"])
        assert ws._triage_frame(ws.inside("projA/BOM.csv"))["designator"].tolist() == ["R1"]
        up = ws.save_upload("BOM.csv", b"Designator,Value\nC1,1u\n")
        assert ws.triage(up)["reports"][0] == "reports/triage_BOM@csv.md"   # by file name: a re-upload continues it (UX-16)
        (ws.root / "uploads" / "Old.csv").write_text("Designator,Value\nC1,1u\n")               # flat upload (older workspace)
        assert ws.triage("uploads/Old.csv")["reports"][0] == "reports/triage_Old.md"


def test_u14_no_pad_one_and_markers():
    spec = SPEC("D_SOD-123")
    spec["pin_positions"] = [{"number": "K", "side": "L", "offset": 0}, {"number": "A", "side": "R", "offset": 0}]
    txt, rep = F.build(spec)                                       # no bare StopIteration
    assert "no pad numbered 1" in rep["warning"] and any(c["rule"] == "pin1" for c in rep["checks"]), rep


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
