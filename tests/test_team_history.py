"""Past triages (read-only, any signed-in user) and uploads that never overwrite each other.
Offline; a real HTTP server on an ephemeral port, as in tests/test_team.py.
Run:  python3 -m tests.test_team_history"""
import json, os, re, tempfile, threading
from pathlib import Path
from urllib.parse import quote

from ecadlib import mcp_server as M
from ecadlib.app import Workspace
from ecadlib.ui import server as S
from tests.test_team import Client, server, wait_job

UP = re.compile(r"uploads/\d{8}-\d{6}-[0-9a-f]{6}/([^/]+)")


def bom(ref):
    return f"Designator,Value,Footprint\n{ref},10k,0603\n".encode()


def test_uploads_get_their_own_folder_and_stay_inside():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, providers=["digikey"], offline=True)
        a, b = ws.save_upload("BOM.csv", bom("R1")), ws.save_upload("BOM.csv", bom("R2"))
        assert a != b and UP.fullmatch(a).group(1) == UP.fullmatch(b).group(1) == "BOM.csv", (a, b)
        assert (ws.root / a).read_bytes() == bom("R1") and (ws.root / b).read_bytes() == bom("R2")
        for evil in ("../../x.csv", "..\\..\\y.csv", "/etc/passwd", "..", "a/../../../z.csv"):
            p = ws.save_upload(evil, bom("R3"))
            assert UP.fullmatch(p) and (ws.root / p).resolve().parent.parent == ws.root / "uploads", (evil, p)
        files = sorted(f.relative_to(ws.root).parts[0] for f in ws.root.rglob("*") if f.is_file())
        assert set(files) == {"uploads"} and len(files) == 7, files            # nothing landed anywhere else
        assert {a, b} <= set(ws.info()["boms"])
        ra, rb = ws.triage(a), ws.triage(b)
        assert ra["reports"] != rb["reports"], (ra["reports"], rb["reports"])
        assert [r["designator"] for r in ws.triage_result(ra["id"])["rows"]] == ["R1"]
        assert [r["designator"] for r in ws.triage_result(rb["id"])["rows"]] == ["R2"]
        assert ws._librarian_dir(ws.inside(a)) != ws._librarian_dir(ws.inside(b))   # librarian output apart too


def test_concurrent_same_name_uploads_from_several_users_all_survive():
    with server() as (ws, team, port):
        users = ("lib", "apr", "ap2", "ada")
        clients = {u: Client(port) for u in users}
        for u, c in clients.items():
            assert c.login(u)[0] == 200
        got, errors, start = [], [], threading.Barrier(len(users))

        def work(u, c):
            try:
                start.wait(10)
                for n in range(3):
                    data = bom(f"{u.upper()}{n}")
                    st, j = c.call("POST", "/api/upload", data, {"X-Filename": quote("BOM rev A.csv")})
                    assert st == 200, j
                    res = wait_job(c, j["job"])
                    assert res["state"] == "done", res
                    got.append((u, data, res["result"]))
            except Exception as e:                                             # noqa: BLE001 - asserted below
                errors.append(f"{u}: {e!r}")
        ts = [threading.Thread(target=work, args=(u, c)) for u, c in clients.items()]
        [t.start() for t in ts]
        [t.join(120) for t in ts]
        assert not errors, errors
        assert len(got) == 12 and len({r["bom"] for _, _, r in got}) == 12
        assert len({r["reports"][1] for _, _, r in got}) == 12                    # triage reports apart too
        for u, data, r in got:
            assert UP.fullmatch(r["bom"]).group(1) == "BOM_rev_A.csv" and r["name"] == "BOM_rev_A.csv"
            assert (ws.root / r["bom"]).read_bytes() == data, r["bom"]            # nobody overwrote anybody
            assert r["user"] == u and [x["designator"] for x in r["rows"]] == [data.decode().split("\n")[1].split(",")[0]]
        assert len([b for b in ws.info()["boms"] if b.endswith("/BOM_rev_A.csv")]) == 12
        st, h = clients["lib"].call("GET", "/api/triage/history")
        assert st == 200 and h["total"] == 12 and {x["user"] for x in h["triages"]} == set(users), h


def test_viewer_lists_and_opens_past_triages_but_not_live_jobs():
    with server() as (ws, team, port):
        lib, vic, anon = Client(port), Client(port), Client(port)
        lib.login("lib"), vic.login("vic")
        st, j = lib.call("POST", "/api/upload", Path("examples/sample_bom.csv").read_bytes(), {"X-Filename": "board.csv"})
        assert st == 200, j
        assert vic.call("GET", f"/api/job?id={j['job']}")[0] == 404            # a running job stays its owner's
        done = wait_job(lib, j["job"])["result"]
        st, h = vic.call("GET", "/api/triage/history")
        assert st == 200 and h["total"] == 1, h
        it = h["triages"][0]
        assert it["id"] == done["id"] and it["user"] == "lib" and it["name"] == "board.csv" and it["lines"] == 14, it
        assert it["bom"] == done["bom"] and it["bom_exists"] and sum(it["counts"].values()) == 14, it
        st, r = vic.call("GET", "/api/triage/result?id=" + quote(it["id"]))
        assert st == 200 and r["rows"] == done["rows"] and r["counts"] == done["counts"] and r["mapping"], r.keys()
        assert all(x["status"] for x in r["rows"]) and r["finished"] and r["user"] == "lib"
        st, md = vic.call("GET", "/api/file?path=" + quote(r["reports"][0]))
        assert st == 200 and md.startswith(b"# BOM triage")
        for bad in ("nope", "../team_users", "../../etc/passwd", "", "%2e%2e%2fteam_users"):
            st, e = vic.call("GET", "/api/triage/result?id=" + quote(bad))
            assert st == 404 and "no triage" in e["error"], (bad, st, e)
        assert anon.call("GET", "/api/triage/history")[0] == 401
        assert anon.call("GET", "/api/triage/result?id=" + quote(it["id"]))[0] == 401
        assert vic.call("POST", "/api/triage", {"bom": done["bom"]})[0] == 403  # re-running still needs librarian
        assert S.ROUTES[("GET", "/api/triage/history")] == S.ROUTES[("GET", "/api/triage/result")] == "viewer"


def test_history_local_mode_legacy_reports_and_links():
    from tests.test_ui import serve
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
        ws = Workspace(d, ["examples/kicad_lib"], providers=["digikey"], offline=True)
        (ws.root / "reports" / "triage_old.csv").write_text("line,designator,status\n1,R1,in_library\n2,U1,needs_footprint_generation\n")
        (ws.root / "reports" / "triage_old.md").write_text("# BOM triage - `old.csv`\n")
        os.utime(ws.root / "reports" / "triage_old.csv", (1.7e9, 1.7e9))       # finished in 2023
        Path(outside, "x.json").write_text(json.dumps({"bom": "secret", "rows": [{"secret": 1}]}))
        os.symlink(Path(outside, "x.json"), ws.root / "reports" / "triage_evil.json")   # links are never followed
        t = ws.triage(ws.save_upload("new.csv", bom("C1")))
        srv, call = serve(ws)
        try:
            st, h = call("GET", "/api/triage/history")                         # local mode: no sign-in
            assert st == 200 and [x["id"] for x in h["triages"]][0] == t["id"], h
            ids = {x["id"]: x for x in h["triages"]}
            assert set(ids) == {t["id"], "old"}, ids
            old = ids["old"]
            assert old["bom"] is None and not old["bom_exists"] and old["lines"] == 2 and old["counts"]["in_library"] == 1, old
            assert old["reports"] == ["reports/triage_old.md", "reports/triage_old.csv"]
            st, r = call("GET", "/api/triage/result?id=old")
            assert st == 200 and [x["line"] for x in r["rows"]] == [1, 2] and r["rows"][1]["status"] == "needs_footprint_generation"
            assert call("GET", "/api/triage/result?id=evil")[0] == 404
            st, page = call("GET", "/")
            assert b"Past triages" in page and b"/api/triage/history" in page and b"/api/triage/result" in page
        finally:
            srv.shutdown()
        # MCP: the same read-only view
        assert "triage_history" in {x["name"] for x in M.TOOLS}
        assert M.call_tool(ws, "triage_history", {})["total"] == 2
        assert M.call_tool(ws, "triage_history", {"id": t["id"]})["rows"][0]["designator"] == "C1"


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_team_history"]))
