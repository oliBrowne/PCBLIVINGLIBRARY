"""The site's crash-report endpoint (api/crash-report.js) with a fake GitHub, and the contract between it and the
client: what ecadlib sends for a real crash passes the endpoint's checks. Needs node (skipped without it).
Run:  python3 -m tests.test_crash_report_endpoint"""
import json, shutil, subprocess
from pathlib import Path

from tests.fixtures_lite import lite
from ecadlib import bugreport as B, userconfig as UC

ROOT = Path(__file__).resolve().parent.parent


def test_endpoint_checks():
    node = shutil.which("node")
    if not node:
        print("      (node not installed: skipped)")
        return
    r = subprocess.run([node, str(ROOT / "tests" / "crash_report_endpoint_check.js")], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@lite
def test_what_the_client_sends_passes_the_endpoint(tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node:
        print("      (node not installed: skipped)")
        return
    monkeypatch.setenv("ECADLIB_HOME", tmp_path)
    for where in ("triage", "web UI: /api/status", "web UI job: librarian", "MCP server", "Altium / KiCad menu"):
        try:
            UC.check_name("not a name ä")
        except ValueError as e:
            B.record(where, e)
        p = B.payload(B.latest())
        js = ("const ep=require(process.argv[1]);let d='';process.stdin.on('data',c=>d+=c).on('end',()=>{"
              "const bad=ep.validate(JSON.parse(d));if(bad){console.log(bad);process.exit(1)}})")
        r = subprocess.run([node, "-e", js, str(ROOT / "api" / "crash-report.js")], input=json.dumps(p),
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (where, p, r.stdout, r.stderr)
        assert p["frames"] and p["frames"][-1]["file"] == "ecadlib/userconfig.py"
