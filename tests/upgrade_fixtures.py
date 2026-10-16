"""Sample workspaces made by older ecadlib versions, for tests/test_upgrade.py.

    python3 -m tests.upgrade_fixtures build OLD_CHECKOUT LABEL     (run by hand; writes tests/fixtures/upgrade/LABEL.tar.gz)

OLD_CHECKOUT is a `git worktree add --detach DIR <commit>` of this repository at an older commit. The workspace is made by
THAT code (it runs in a subprocess with PYTHONPATH=OLD_CHECKOUT), offline, from the same recorded inputs the end-to-end
tests use (tests/fixtures/e2e: real KiCad library files, a synthetic EasyEDA part), through the old Workspace API the web
UI and MCP server call:
  * uploads/board.csv -> librarian/<bom>/ (plan.json, librarian_state.json, models/, reports), via librarian_run
  * versions with approvals: EEPART1 approved by "Ann Lee" (plan + approvals_audit.jsonl)
  * versions with rules: ecadlib.toml ([footprint] density, [release] approvals table)
  * versions with the library agent: a watch scan of the BOM (agent/watch_state.json), two queue items (one in review
    with a build that points at the plan, one failed), agent/status.json
The HTTP cache is left out of the archive (tests seed it again with e2e_support.seed_cache). manifest.json inside says
which commit and version made it."""
import io, json, os, subprocess, sys, tarfile, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = Path(__file__).resolve().parent / "fixtures" / "upgrade"
BOM = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr Part #,LCSC,Description\n"
       '"R1, R2",2,10k,R_0603_1608Metric,Yageo,RC0603FR-0710KL,,Resistor 10k 1% 0603\n'
       "C1,1,100nF,C_0603_1608Metric,Murata,GRM188R71C104KA01D,,Capacitor 100nF X7R 0603\n"
       "U2,1,LM358DR,SOIC-8_3.9x4.9mm_P1.27mm,Texas Instruments,LM358DR,,Dual op amp SOIC-8\n"
       "Q1,1,EEPART1,,ACME,EEPART1,C2002,EasyEDA-only part\n"
       "U9,1,NOWHERE1,,,NOWHERE1,,\n")
RULES = '[footprint]\ndensity = "N"\n\n[release.approvals]\nrequired = 0\n'
USER = "Ann Lee <ann@example.com>"

# runs under the OLD code
OLD = r'''
import json, sys
from pathlib import Path
import ecadlib
from ecadlib.app import Workspace
root = Path(sys.argv[1])
ws = Workspace(root, offline=True)
did = {"version": ecadlib.__version__}
rep = ws.librarian_run("uploads/board.csv")
did["librarian"] = {"parts": rep.get("parts") if isinstance(rep, dict) else None}
if hasattr(ws, "approve_part"):
    ws.approve_part("uploads/board.csv", "EEPART1", "approve", sys.argv[2])
    did["approved"] = ["EEPART1"]
try:
    from ecadlib import agent_queue as Q, agent_watch as W, agent as A
except ImportError:
    Q = None
if Q is not None:
    W.main(["--workspace", str(root), "--source", "bom_file:uploads/board.csv", "--json"])
    q = Q.Queue(root)
    it = q.upsert({"mpn": "LM358DR", "manufacturer": "Texas Instruments", "origin": "manual",
                   "change": {"kind": "manual", "note": "upgrade fixture"}}, user="ecadlib-agent")
    for to in ("building", "proving"):
        q.transition(it["id"], to, user="ecadlib-agent")
    q.transition(it["id"], "review", user="ecadlib-agent",
                 build={"bom": "uploads/board.csv", "part": "LM358DR", "user": "ecadlib-agent", "sources": {"symbol": "kicad"}})
    bad = q.upsert({"mpn": "NOWHERE1", "manufacturer": "", "origin": "manual", "change": {"kind": "manual"}}, user="ecadlib-agent")
    q.transition(bad["id"], "failed", user="ecadlib-agent", note="no source has this part")
    A.write_status(root, state="idle")
    did["queue"] = {"review": it["id"], "failed": bad["id"]}
print("RESULT " + json.dumps(did))
'''


def build(old_checkout, label, out_dir=DIR):
    old_checkout = Path(old_checkout).resolve()
    commit = subprocess.run(["git", "-C", str(old_checkout), "rev-parse", "--short", "HEAD"], capture_output=True,
                            text=True, check=True).stdout.strip()
    sys.path.insert(0, str(ROOT))
    from tests.e2e_support import seed_cache
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "ws"
        (root / "uploads").mkdir(parents=True)
        (root / "uploads" / "board.csv").write_text(BOM, encoding="utf-8")
        has_rules = (old_checkout / "ecadlib" / "rules.py").is_file()
        if has_rules:
            (root / "ecadlib.toml").write_text(RULES, encoding="utf-8")
        seed_cache(root / "http_cache")
        env = dict(os.environ, PYTHONPATH=str(old_checkout), ECADLIB_USER=USER)
        env.pop("ECADLIB_CONFIG", None)
        p = subprocess.run([sys.executable, "-c", OLD, str(root), USER], cwd=d, env=env, capture_output=True, text=True)
        if p.returncode:
            raise SystemExit(f"old code failed:\n{p.stdout[-3000:]}\n{p.stderr[-3000:]}")
        did = json.loads(next(l for l in p.stdout.splitlines() if l.startswith("RESULT "))[7:])
        manifest = {"label": label, "commit": commit, "ecadlib": did.pop("version"), "made": did, "bom": "uploads/board.csv",
                    "user": USER, "rules": has_rules}
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / f"{label}.tar.gz"
        with tarfile.open(f, "w:gz") as t:
            def add(path, arc):
                ti = t.gettarinfo(str(path), arc)
                ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = 1791936000, 0, 0, "", ""
                if path.is_file():
                    with open(path, "rb") as fh:
                        t.addfile(ti, fh)
                else:
                    t.addfile(ti)
            for p_ in sorted(root.rglob("*")):
                rel = p_.relative_to(root)
                if rel.parts[0] == "http_cache" or p_.suffix == ".tmp" or p_.name.endswith(".lock"):
                    continue
                add(p_, f"ws/{rel.as_posix()}")
            data = (json.dumps(manifest, indent=1) + "\n").encode()
            ti = tarfile.TarInfo("ws/manifest.json")
            ti.size, ti.mtime = len(data), 1791936000
            t.addfile(ti, io.BytesIO(data))
    return f, manifest


def extract(label, dest):
    """Unpack a fixture into dest (-> the workspace folder, dest/ws). Paths are checked (archive = data)."""
    dest = Path(dest)
    with tarfile.open(DIR / f"{label}.tar.gz") as t:
        for m in t.getmembers():
            p = (dest / m.name).resolve()
            if dest.resolve() not in p.parents or not (m.isfile() or m.isdir()):
                raise ValueError(f"bad member {m.name}")
        t.extractall(dest, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return dest / "ws"


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "build":
        f, m = build(sys.argv[2], sys.argv[3])
        print(f"{f} ({f.stat().st_size} bytes): {json.dumps(m)}")
    else:
        print(__doc__)
