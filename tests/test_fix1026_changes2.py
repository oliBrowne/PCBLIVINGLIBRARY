"""2026-10-26 fixes "changes", round 2 (second reviewer's runtime stress test, race.md): a reject holds until an
approve takes it back (R-RC-3); a released revision's fingerprint and files come from the same bytes (R-RC-1);
kicad-export ships the bytes the change gate checked (R-RC-2); CLI decisions wait briefly for plan.lock (R-RC-4).
Offline.  python3 -m pytest -q tests/test_fix1026_changes2.py   (or python -m tests.run_all test_fix1026_changes2)"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path

from tests.fixtures_lite import lite

REPO = Path(__file__).resolve().parents[1]
_DEMO = {}
ANN = "Ann Lee <ann@example.com>"


def _run(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "ECADLIB_")) and k not in ("NEXAR_TOKEN",)}
    env.update(ECADLIB_HOME=str(Path(cwd) / "home"), NO_COLOR="1", ECADLIB_USER="Tester <tester@example.com>",
               PYTHONPATH=os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")]))
    return subprocess.run([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


def _demo():
    """A fresh copy of the offline demo workspace -> (work dir, library folder)."""
    if "dir" not in _DEMO:
        d = Path(tempfile.mkdtemp(prefix="ecadlib-fix2-demo-"))
        r = _run(d, "demo", "-o", "ws")
        assert r.returncode == 0, r.stdout + r.stderr
        _DEMO["dir"] = d
    w = Path(tempfile.mkdtemp(prefix="ecadlib-fix2-"))
    shutil.copytree(_DEMO["dir"] / "ws", w / "ws")
    return w, w / "ws/librarian/demo_bom_demo"


def _items(plan):
    return {i["name"]: i for i in json.loads(Path(plan).read_text(encoding="utf-8"))}


def _ledger(lib):
    return [json.loads(x) for x in (lib / "library.ledger.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


# ------------------------------------------------------------------ R-RC-3: a reject holds until an approve
def test_fix2_rrc3_a_reject_is_not_lifted_by_a_content_change():
    """Unit: a reject recorded against one version still holds after the part's content changed (style apply, part
    revert, a re-run); only an approve by that person takes it back. An approve of an old version still goes stale."""
    from ecadlib import approvals as AP
    with tempfile.TemporaryDirectory() as d:
        it = {"name": "P1", "status": "ready", "description": "Resistor 10k", "parameters": [],
              "symbol": {"ref": "R", "file": None}, "footprint": {"ref": "R_0603", "file": None}}
        AP.decide(it, "reject", ANN, comment="wrong value", base=d)
        assert AP.evaluate(it, None, d)["required"] and not AP.evaluate(it, None, d)["met"]
        it["description"] = "Resistor 10k 1% (restyled)"               # the content changes after the reject
        ev = AP.evaluate(it, None, d)
        assert ev["required"] and not ev["met"] and ev["rejected_by"], ev
        assert AP.refresh_status(it, None, d) == "awaiting_approval"
        AP.decide(it, "approve", ANN, comment="fixed", base=d)          # the same person takes it back
        assert AP.evaluate(it, None, d)["met"] and AP.refresh_status(it, None, d) == "ready"
        bob = "Bob Roe <bob@example.com>"                               # an approve of an old version is stale
        pol = AP.normalize_policy({"required": 1})
        it2 = dict(it, approvals=[])
        AP.decide(it2, "approve", bob, base=d, policy=pol)
        it2["description"] = "changed again"
        ev2 = AP.evaluate(it2, pol, d)
        assert ev2["stale"] == [bob] and not ev2["met"], ev2


def test_fix2_rrc3_cli_reject_survives_a_content_change_and_kicad_export():
    """Repro A of race.md without the house style: `ecadlib approve P reject`, the part's description changes (as a
    style apply writes it), `release-check` and kicad-export: the part stays held and is not exported."""
    w, lib = _demo()
    plan = lib / "plan.json"
    r = _run(w, "approve", plan, "reject", "--part", "RES-0603-10k", "--user", ANN, "-m", "wrong tolerance")
    assert r.returncode == 0, r.stdout + r.stderr
    items = json.loads(plan.read_text(encoding="utf-8"))
    it = next(i for i in items if i["name"] == "RES-0603-10k")
    it["description"] = (it.get("description") or "") + " (house style)"
    plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
    r = _run(w, "release-check", plan)
    assert _items(plan)["RES-0603-10k"]["status"] != "ready", (r.stdout, _items(plan)["RES-0603-10k"]["status"])
    r = _run(w, "kicad-export", plan, "--out", "kx")
    sym = (w / "kx" / "ecadlib.kicad_sym").read_text(encoding="utf-8")
    assert '"RES-0603-10k"' not in sym, r.stdout
