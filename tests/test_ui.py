"""Offline tests for the front ends: Workspace actions, the web UI server, the MCP server and the CLI.
Run:  python3 -m tests.test_ui"""
import contextlib, http.client, io, json, os, re, subprocess, sys, tempfile, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from ecadlib import mcp_server as M
from ecadlib.app import Workspace
from ecadlib.ui.server import make_handler

SPEC = lambda n: json.loads(Path(f"ecadlib/package_specs/{n}.json").read_text())
ALTIUM_DEFAULT = ('"Comment","Description","Designator","Footprint","LibRef","Quantity"\n'
                  '"10k","Resistor","R1, R2","RESC1608X55N","Res1","2"\n'
                  '"CH340C","USB UART","U1","SOIC-16","CH340C","1"\n')
KICAD_DNP = ('"Reference","Value","Footprint","Qty","DNP","MPN"\n'
             '"C1,C2","100n","Capacitor_SMD:C_0402_1005Metric","2","","CL05B104KO5NNNC"\n'
             '"J1","Conn","Connector:Foo","1","DNP",""\n')


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def expect(exc, fn, *a, contains=""):
    try:
        quiet(fn, *a)
    except exc as e:
        assert contains in str(e), (contains, str(e))
        return
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


def test_workspace_flow():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, ["examples/kicad_lib"], providers=["digikey"])
        rel = ws.save_upload("../my bom.csv", Path("examples/sample_bom.csv").read_bytes())
        assert re.fullmatch(r"uploads/\d{8}-\d{6}-[0-9a-f]{6}/my_bom\.csv", rel), rel
        seen = []
        t = quiet(ws.triage, rel, progress=lambda a, b, lab: seen.append((a, b)))
        assert len(t["rows"]) == 14 and sum(t["counts"].values()) == 14 and t["bom"] == rel, t["counts"]
        assert seen[0] == (0, 14) and seen[-1] == (14, 14) and [r["line"] for r in t["rows"]] == list(range(1, 15))
        before = t["counts"].get("in_library", 0)

        pv = ws.preview_footprint(SPEC("R_0603_1608Metric"))
        assert pv["svg"].startswith("<svg") and len(pv["report"]["pads"]) == 2 and pv["exists"] is False
        r = ws.approve_footprint(SPEC("R_0603_1608Metric"))
        assert r["files"] == ["footprints/ecadlib.pretty/R_0603_1608Metric.kicad_mod",
                              "altium_models/PcbLib/R_0603_1608Metric.PcbLib"] and not r["warnings"], r
        assert (Path(d) / r["files"][1]).read_bytes()[:8] == bytes.fromhex("D0CF11E0A1B11AE1")   # OLE2 compound file
        expect(FileExistsError, ws.approve_footprint, SPEC("R_0603_1608Metric"), contains="already in the workspace")
        ws.approve_footprint(SPEC("R_0603_1608Metric"), None, True)                              # overwrite allowed
        assert ws.preview_footprint(SPEC("R_0603_1608Metric"))["exists"] is True
        ws.approve_footprint(SPEC("C_0402_1005Metric"))
        assert quiet(ws.triage, rel)["counts"].get("in_library", 0) > before    # approved footprints found on re-check

        m = quiet(ws.build_altium_models, rel)
        assert {"Resistor", "Capacitor"} <= set(m["symbols"]) and "R_0603_1608Metric" in m["footprints"], m
        p = ws.altium365_plan(rel)
        ready = {i["name"] for i in p["items"] if i["status"] == "ready"}
        assert {"RES-0603-10k", "CAP-0402-100nF"} <= ready and p["total"] == 14, ready
        dry = ws.altium365_push(rel, "", dry_run=True)
        assert {x["action"] for x in dry["results"]} == {"release", "skip"} and (Path(d) / dry["report"]).exists()
        env = os.environ.pop("NEXAR_TOKEN", None)
        try:
            expect(ValueError, ws.altium365_push, rel, "https://ws", False, contains="NEXAR_TOKEN")
        finally:
            if env:
                os.environ["NEXAR_TOKEN"] = env
        assert any(f.endswith(".DbLib") for f in quiet(ws.export_dblib, rel)["files"])
        info = ws.info()
        assert "R_0603_1608Metric" in info["altium_models"]["PcbLib"] and rel in info["boms"]


def test_workspace_bom_shapes_and_bad_files():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, ["examples/kicad_lib", "out/footprints"], providers=["digikey"])
        t = quiet(ws.triage, ws.save_upload("altium.csv", ALTIUM_DEFAULT.encode()))
        assert [r["status"] for r in t["rows"]] == ["in_library", "in_library"], t["rows"]
        t = quiet(ws.triage, ws.save_upload("kicad.csv", KICAD_DNP.encode()))
        assert t["counts"].get("dnp (not fitted)") == 1, t["counts"]
        expect(ValueError, ws.save_upload, "empty.csv", b"", contains="is empty")
        bad = {"bin.csv": b"\x89PNG\r\n\x1a\n\x00\x00junk", "hdr.csv": b"Designator,MPN\n", "words.csv": b"hello world\n"}
        for name, data in bad.items():
            expect(ValueError, ws.triage, ws.save_upload(name, data))
        expect(ValueError, ws.inside, "../../etc/passwd", contains="outside workspace")
        expect(ValueError, ws.preview_footprint, {"package_type": "chip"}, contains="name")


def serve(ws):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ws))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def call(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        c.request(method, path, body=body if isinstance(body, (bytes, str, type(None))) else json.dumps(body), headers=headers or {})
        r = c.getresponse()
        data = r.read()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data
    return srv, call


def wait_job(call, jid, timeout=30.0):
    end = time.time() + timeout                            # a slow Windows runner: 10 s was too tight
    while time.time() < end:
        st, j = call("GET", f"/api/job?id={jid}")
        assert st == 200, j
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_ui_server():
    with tempfile.TemporaryDirectory() as d:
        srv, call = serve(Workspace(d, ["examples/kicad_lib"], providers=["digikey"]))
        try:
            st, page = call("GET", "/")
            assert st == 200 and b"<title>ecadlib</title>" in page
            # guards
            assert call("GET", "/api/info", headers={"Host": "evil.example:80"})[0] == 403       # DNS rebinding
            assert call("POST", "/api/triage", {}, {"Origin": "https://evil.example"})[0] == 403
            st, e = call("POST", "/api/triage", {"bom": str(Path("examples/sample_bom.csv").resolve())})
            assert st == 400 and "outside workspace" in e["error"], e
            assert call("POST", "/api/triage", "x", {"Content-Length": "-1"})[0] == 400
            assert call("POST", "/api/triage", "[1, 2]")[0] == 400                               # not a JSON object
            assert call("GET", "/api/file?path=../../etc/passwd")[0] == 400
            # upload with a non-ASCII name -> background job -> result
            st, j = call("POST", "/api/upload", Path("examples/sample_bom.csv").read_bytes(), {"X-Filename": quote("Stückliste rev B.csv")})
            assert st == 200 and "job" in j, j
            res = wait_job(call, j["job"])
            assert res["state"] == "done" and res["done"] == res["total"] == 14, res
            bom = res["result"]["bom"]
            assert bom.startswith("uploads/") and bom.endswith("/St_ckliste_rev_B.csv"), bom
            st, f = call("GET", "/api/file?path=" + quote(res["result"]["reports"][0]))
            assert st == 200 and f.startswith(b"# BOM triage")
            # a bad upload finishes as a job error, not a crash
            st, j = call("POST", "/api/upload", b"\x00\x01binary", {"X-Filename": "x.csv"})
            res = wait_job(call, j["job"])
            assert res["state"] == "error" and "binary" in res["error"], res
            assert call("POST", "/api/upload", b"", {"X-Filename": "e.csv"})[0] == 400
            # footprint approve, then 409 on a second approve, then overwrite
            body = {"spec": SPEC("SOIC-8_3.9x4.9mm_P1.27mm")}
            st, r = call("POST", "/api/footprint/approve", body)
            assert st == 200 and len(r["files"]) == 2, r
            assert call("POST", "/api/footprint/approve", body)[0] == 409
            assert call("POST", "/api/footprint/approve", dict(body, overwrite=True))[0] == 200
            st, e = call("POST", "/api/footprint/preview", {"spec": {"name": "x", "package_type": "qfn"}})
            assert st == 400 and e["error"], e
            # Altium actions
            assert call("POST", "/api/altium/models", {"bom": bom})[0] == 200
            st, p = call("POST", "/api/altium365/plan", {"bom": bom})
            assert st == 200 and p["total"] == 14, p
            st, r = call("POST", "/api/altium365/push", {"bom": bom, "dry_run": True})
            assert st == 200 and r["dry_run"] is True, r
            st, e = call("POST", "/api/altium365/push", {"bom": bom, "workspace": "https://w", "dry_run": False})
            assert st == 400 and ("NEXAR_TOKEN" in e["error"] or os.environ.get("NEXAR_TOKEN")), e
            st, r = call("POST", "/api/export/dblib", {"bom": bom})
            assert st == 200 and r["files"], r
            assert call("GET", "/api/job?id=nope")[0] == 404
        finally:
            srv.shutdown()


def test_mcp():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, ["examples/kicad_lib"], providers=["digikey"])
        call = lambda i, name, args: M.handle(ws, {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                                    "params": {"name": name, "arguments": args}})
        names = [x["name"] for x in M.handle(ws, {"id": 1, "method": "tools/list"})["result"]["tools"]]
        assert {"triage_bom", "approve_footprint", "altium365_plan", "altium365_push", "build_altium_models"} <= set(names)
        bom = str(Path("examples/sample_bom.csv").resolve())
        r = json.loads(quiet(call, 2, "triage_bom", {"bom": bom, "summary_only": True})["result"]["content"][0]["text"])
        assert "rows" not in r and len(r["needs_work"]) == sum(v for k, v in r["counts"].items() if not k.startswith(("in_library", "dnp"))), r
        assert call(3, "preview_footprint", {"spec": SPEC("R_0603_1608Metric")})["result"]["isError"] is False
        assert call(4, "approve_footprint", {"spec": SPEC("R_0603_1608Metric")})["result"]["isError"] is False
        r = call(5, "approve_footprint", {"spec": SPEC("R_0603_1608Metric")})["result"]
        assert r["isError"] and "already in the workspace" in r["content"][0]["text"], r
        r = call(6, "triage_bom", {})["result"]                     # missing argument: a tool error the model can fix
        assert r["isError"] and "missing argument(s): bom" in r["content"][0]["text"], r
        assert call(7, "rm_rf", {})["error"]["code"] == -32602                                      # unknown tool
        r = call(8, "altium365_push", {"bom": bom})["result"]                                       # dry run by default
        assert r["isError"] is False and json.loads(r["content"][0]["text"])["dry_run"] is True, r
        assert M.handle(ws, {"method": "notifications/initialized"}) is None
        assert M.handle(ws, ["not", "an", "object"])["error"]["code"] == -32600
        r = M.handle(ws, {"id": 9, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        assert r["result"]["protocolVersion"] == M.PROTOCOL, r


def test_mcp_stdio_and_cli():
    """The real entry points, as a client would start them."""
    with tempfile.TemporaryDirectory() as d:
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "triage_bom", "arguments": {
                    "bom": str(Path("examples/sample_bom.csv").resolve()), "summary_only": True}}}]
        p = subprocess.run([sys.executable, "-m", "ecadlib", "mcp", "--lib", "examples/kicad_lib", "--workspace", d,
                            "--providers", "digikey"], input="\n".join(map(json.dumps, msgs)) + "\nnot json\n",
                           capture_output=True, text=True, timeout=120)
        out = [json.loads(l) for l in p.stdout.splitlines()]                  # stdout must be pure JSON-RPC
        assert [o.get("id") for o in out] == [1, 2, None], out
        assert out[1]["result"]["isError"] is False and out[2]["error"]["code"] == -32700, out
    p = subprocess.run([sys.executable, "-m", "ecadlib", "--version"], capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.startswith("ecadlib "), p
    p = subprocess.run([sys.executable, "-m", "ecadlib", "nope"], capture_output=True, text=True)
    assert p.returncode == 2 and "unknown command" in p.stderr, p
    p = subprocess.run([sys.executable, "-m", "ecadlib", "triage", "-h"], capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.startswith("usage: ecadlib triage"), p


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
