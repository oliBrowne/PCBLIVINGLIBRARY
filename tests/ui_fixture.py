"""A small, offline workspace for driving the web UI in a real browser (tests/test_ui_browser.py) and for
screenshots: the sample BOM uploaded and triaged, the librarian run on the local example library (some parts
ready, some held), two review-queue items with pictures, and (team mode) one user per role.

    python3 -m tests.ui_fixture DIR [--team] [--port 0]     # build DIR and serve it until Ctrl+C"""
import base64, contextlib, io, json, shutil, sys, threading, zlib
from pathlib import Path

from ecadlib import agent_queue as AQ, team as TM
from ecadlib.app import Workspace

BOM = Path(__file__).resolve().parent.parent / "examples" / "sample_bom.csv"
LIB = str(Path(__file__).resolve().parent.parent / "examples" / "kicad_lib")
PW = "correct horse battery"                   # every fixture user's password (10+ characters)
USERS = [("ada", "admin", "Ada Admin"), ("lena", "librarian", "Lena Librarian"), ("abe", "approver", "Abe Approver"),
         ("vic", "viewer", "Vic Viewer")]


def png(w=120, h=80, rgb=(200, 116, 58)):
    """A plain one-colour PNG (no Pillow needed)."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        return len(d).to_bytes(4, "big") + t + d + zlib.crc32(t + d).to_bytes(4, "big")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def build(root, team=False, librarian=True, queue=True):
    """Builds the workspace in root (an empty or missing folder). Returns (Workspace, bom path)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    ws = Workspace(root, [LIB], providers=["digikey"], offline=True)
    up = ws.root / "uploads"
    up.mkdir(exist_ok=True)
    shutil.copy(BOM, up / "sample_bom.csv")
    bom = "uploads/sample_bom.csv"
    with contextlib.redirect_stdout(io.StringIO()):
        ws.triage(bom, user="lena" if team else None)
        if librarian:
            ws.librarian_run(bom, sources=["kicad", "standard"])
    if queue:
        q = AQ.Queue(ws.root)
        pics = ws.root / "agent" / "pictures"
        pics.mkdir(parents=True, exist_ok=True)
        (pics / "before.png").write_bytes(png(rgb=(140, 150, 160)))
        (pics / "after.png").write_bytes(png())
        for mpn, mfr, verdict in (("CH340C", "WCH", "pass"), ("AO3401A", "Alpha & Omega Semiconductor", "doubt")):
            it = q.upsert({"kind": "new_part", "source": {"type": "bom", "name": "sample_bom.csv"}, "mpn": mpn,
                           "manufacturer": mfr, "line": {"Designator": "U9", "MPN": mpn}})
            q.transition(it["id"], "building", "agent")
            q.transition(it["id"], "proving", "agent", build={"bom": bom, "part": mpn, "user": "agent", "hash": "h1",
                                                               "built": "2026-10-16T10:00:00+00:00"})
            q.transition(it["id"], "review", "agent", proof={"verdict": verdict, "reason": "pin count and pitch match page 3",
                                                             "findings": []},
                         pictures={"before": ["agent/pictures/before.png"], "after": ["agent/pictures/after.png"]})
    if team:
        st = TM.UserStore(ws.root)
        for name, role, full in USERS:
            st.add(name, role, PW, full, f"{name}@example.com")
    return ws, bom


def main(argv=None):
    import argparse
    from ecadlib.ui.server import make_server
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--team", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--empty", action="store_true", help="no BOM, no plan, no queue: a first run")
    a = ap.parse_args(argv)
    if a.empty:
        Path(a.dir).mkdir(parents=True, exist_ok=True)
        ws = Workspace(a.dir, [LIB], providers=["digikey"], offline=True)
        if a.team:
            st = TM.UserStore(ws.root)
            for name, role, full in USERS:
                st.add(name, role, PW, full, f"{name}@example.com")
    else:
        ws, _ = build(a.dir, a.team)
    team = TM.Team(ws.root) if a.team else None
    srv = make_server(ws, "127.0.0.1", a.port, team)
    print(json.dumps({"url": f"http://127.0.0.1:{srv.server_address[1]}/"}), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
