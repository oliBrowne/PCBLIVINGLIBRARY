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


# ------------------------------------------------------------------ R-RC-1: fingerprint and files from the same bytes
@lite
def test_fix2_rrc1_an_edit_saved_while_a_release_is_recorded_is_not_released_unclassified(tmp_path, monkeypatch):
    """A cosmetic change of CAP-0402-100nF (its description) is released by a publisher's settle; between the
    fingerprint (classified cosmetic) and the file snapshot, a hand edit makes its pads 0.98 mm (geometry major, held
    by policy). That file was recorded as the released revision with classes {cosmetic: 1}. Now the release sees the
    files moved, classifies them again and holds the pad edit; the released .kicad_mod stays the original."""
    import hashlib, re
    from ecadlib import change_gate as CG, ledger as LG
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    w, lib = _demo()
    plan = lib / "plan.json"
    f = lib / "models" / "KiCad" / "C_0402_1005Metric.kicad_mod"
    orig = f.read_text(encoding="utf-8")
    edit = re.sub(r"\(size [\d.]+ 0\.62\)", "(size 0.98 0.62)", orig)
    assert edit != orig
    items = json.loads(plan.read_text(encoding="utf-8"))
    cap = next(i for i in items if i["name"] == "CAP-0402-100nF")
    cap["description"] = (cap.get("description") or "") + " (restyled)"
    plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
    real, done = LG.Ledger.snapshot_files, []

    def snapshot_files(self, item, base=None):
        if item.get("name") == "CAP-0402-100nF" and not done:      # the person saves their edit right now
            done.append(1)
            f.write_text(edit, encoding="utf-8")
        return real(self, item, base)
    monkeypatch.setattr(LG.Ledger, "snapshot_files", snapshot_files)
    with contextlib.redirect_stderr(io.StringIO()):
        CG.publish_guard(plan, items, "kicad")
    assert done, "the snapshot was never taken"
    edit_sha = hashlib.sha256(edit.encode("utf-8")).hexdigest()
    for e in _ledger(lib):
        km = ((e.get("files") or {}).get("kicad_mod") or {}).get("sha256")
        if e.get("event") == "release" and e.get("part") == "CAP-0402-100nF" and km == edit_sha:
            assert any(c.startswith("geometry") for c in e.get("classes") or {}), \
                f"the 0.98 mm pads were released as {e.get('classes')} (unclassified)"
    head = LG.Ledger(lib).head("CAP-0402-100nF")
    assert head["files"]["kicad_mod"]["sha256"] != edit_sha, head.get("classes")


# ------------------------------------------------------------------ R-RC-2: kicad-export ships what the gate checked
@lite
def test_fix2_rrc2_kicad_export_ships_the_released_footprint_not_an_edit_saved_meanwhile(tmp_path, monkeypatch):
    """A hand edit of the CAP-0402 .kicad_mod (pads 0.40 mm) saved after publish_guard settled the library and the
    export checked the part again was copied into the export and recorded as the released revision. The export now ships the
    released revision's bytes and says the file changed while it ran."""
    import re
    from ecadlib import kicad_export as KX, release_check as RC
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    w, lib = _demo()
    plan = lib / "plan.json"
    f = lib / "models" / "KiCad" / "C_0402_1005Metric.kicad_mod"
    orig = f.read_text(encoding="utf-8")
    edit = re.sub(r"\(size [\d.]+ 0\.62\)", "(size 0.40 0.62)", orig)
    assert edit != orig
    real = RC.publish_problems

    def checked(it, *a, **k):
        out = real(it, *a, **k)
        if it.get("name") == "CAP-0402-100nF":       # checked; the person saves now (an editor takes no lock)
            f.write_text(edit, encoding="utf-8")
        return out
    monkeypatch.setattr(RC, "publish_problems", checked)
    rep = KX.export(plan, w / "kx", "ecadlib", log=lambda *a, **k: None)
    shipped = (w / "kx" / "ecadlib.pretty" / "C_0402_1005Metric.kicad_mod").read_text(encoding="utf-8")
    assert "(size 0.40 0.62)" not in shipped, "the hand edit saved during the export was shipped"
    assert any("changed while" in x for x in rep["warnings"]), rep["warnings"]


# ------------------------------------------------------------------ R-RC-4: CLI decisions wait briefly for plan.lock
@lite
def test_fix2_rrc4_cli_approve_waits_for_a_short_plan_write_instead_of_refusing(tmp_path, monkeypatch):
    """`ecadlib approve PLAN reject` refused at once ("decide again in a minute") whenever another command held
    plan.lock, even for the fraction of a second a plan write takes: under load most decisions were refused. It now
    waits a few seconds (bounded: a holder that never lets go still gets a refusal, no hang)."""
    from ecadlib import approvals as AP
    from ecadlib.fsutil import plan_lock
    from tests.ui_fixture import build
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    with contextlib.redirect_stdout(io.StringIO()):
        ws, bom = build(tmp_path / "ws")
    plan = ws.root / "librarian" / "sample_bom" / "plan.json"
    held, done = threading.Event(), threading.Event()

    def writer():                                    # another command's short plan write (1 s)
        with plan_lock(ws.root, wait=True):
            held.set()
            time.sleep(1.0)
        done.set()
    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert held.wait(10)
    t0 = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        AP.main([str(plan), "reject", "--part", "CAP-0402-100nF", "--user", ANN, "-m", "wrong dielectric"])
    assert time.time() - t0 < 20
    assert [a["decision"] for a in _items(plan)["CAP-0402-100nF"].get("approvals") or []] == ["reject"]
    t.join(10)
    monkeypatch.setattr(AP, "DECISION_WAIT", 0.5)    # a holder that never lets go: refused after the bounded wait
    stop = threading.Event()

    def stuck():
        with plan_lock(ws.root, wait=True):
            held.set()
            stop.wait(20)
    held.clear()
    t2 = threading.Thread(target=stuck, daemon=True)
    t2.start()
    assert held.wait(10)
    try:
        t0 = time.time()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                AP.main([str(plan), "approve", "--part", "CAP-0402-100nF", "--user", ANN])
            raise AssertionError("not refused")
        except SystemExit as e:
            assert "decide again" in str(e.code), e.code
        assert time.time() - t0 < 10
    finally:
        stop.set()
        t2.join(10)
