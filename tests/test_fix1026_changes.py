"""2026-10-26 fixes "changes": (1) a pass / warn change and a first release (baseline) are recorded only when the version
passes the release check, as `ecadlib part approve` requires; (2) web UI actions made while a librarian run holds the
library lock are never lost and never hang (decisions recorded at once, file rewrites queued until the lock frees).
Offline.  python3 -m pytest -q tests/test_fix1026_changes.py   (or python -m tests.run_all test_fix1026_changes)"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path

from tests.fixtures_lite import lite

REPO = Path(__file__).resolve().parents[1]
_DEMO = {}


def _run(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "ECADLIB_")) and k not in ("NEXAR_TOKEN",)}
    env.update(ECADLIB_HOME=str(Path(cwd) / "home"), NO_COLOR="1", ECADLIB_USER="Tester <tester@example.com>",
               PYTHONPATH=os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")]))
    return subprocess.run([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


def _demo():
    """A fresh copy of the offline demo workspace -> (work dir, library folder)."""
    if "dir" not in _DEMO:
        d = Path(tempfile.mkdtemp(prefix="ecadlib-fix-demo-"))
        r = _run(d, "demo", "-o", "ws")
        assert r.returncode == 0, r.stdout + r.stderr
        _DEMO["dir"] = d
    w = Path(tempfile.mkdtemp(prefix="ecadlib-fix-"))
    shutil.copytree(_DEMO["dir"] / "ws", w / "ws")
    return w, w / "ws/librarian/demo_bom_demo"


def _ledger(lib):
    return [json.loads(x) for x in (lib / "library.ledger.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def _items(plan):
    return {i["name"]: i for i in json.loads(Path(plan).read_text(encoding="utf-8"))}


RES = ("RES-0603-10k", "RES-0603-4.7k")


# ------------------------------------------------------------------ 1: pass / warn releases run the release check
def test_fix_changes_1a_a_warn_change_that_fails_parity_is_held_not_released():
    """upkeep Q2: a geometry-minor hand edit of the shared R_0603 .kicad_mod (pad 0.8 -> 0.82 mm) was recorded as r2
    (warn) for both resistors; parity then failed (KiCad 0.82, Altium 0.8) and every publisher dropped both released
    parts. Now the edit is held for approval and the released r1 keeps shipping."""
    w, lib = _demo()
    k = lib / "models/KiCad/R_0603_1608Metric.kicad_mod"
    released = k.read_bytes()
    t = k.read_text(encoding="utf-8")
    assert "(size 0.8 0.95)" in t
    k.write_text(t.replace("(size 0.8 0.95)", "(size 0.82 0.95)"), encoding="utf-8")
    n = len(_ledger(lib))
    r = _run(w, "kicad-export", lib / "plan.json", "--out", "kx")
    assert r.returncode == 1 and "a change is held for approval" in r.stdout, r.stdout + r.stderr   # 1: held changes
    new = _ledger(lib)[n:]
    assert not [e for e in new if e["event"] == "release" and e.get("part") in RES], [(e["event"], e.get("part")) for e in new]
    assert {e.get("part") for e in new if e["event"] == "hold"} >= set(RES), [(e["event"], e.get("part")) for e in new]
    assert k.read_bytes() == released                      # the released file is back on disk
    items = _items(lib / "plan.json")
    for p in RES:
        assert (items[p].get("change") or {}).get("state") == "held", items[p].get("change")
        assert items[p]["status"] == "ready", (p, items[p]["status"], items[p].get("hold_reasons"))
    prop = json.loads((lib / ".ecadlib/proposed" / f"{RES[0]}.json").read_text(encoding="utf-8"))
    assert any("release check" in w_ for w_ in prop.get("why") or []), prop.get("why")
    assert "skipped  RES-0603" not in r.stdout + r.stderr, r.stdout
    pub = [e.get("part") for e in _ledger(lib)[n:] if e["event"] == "publish"]
    assert set(RES) <= set(pub), pub                        # both released parts still go out
    r = _run(w, "altium-pkg", lib / "plan.json", "--out", "apkg")
    assert "4 added" in r.stdout, r.stdout + r.stderr


def test_fix_changes_1b_a_warn_change_that_passes_the_check_is_still_released():
    """No false hold: a cosmetic-only / warn edit that passes the release check is recorded as before."""
    w, lib = _demo()
    k = lib / "models/KiCad/R_0603_1608Metric.kicad_mod"
    t = k.read_text(encoding="utf-8")
    assert "(thickness 0.06)" in t
    k.write_text(t.replace("(size 0.4 0.4) (thickness 0.06)", "(size 0.5 0.5) (thickness 0.07)", 1), encoding="utf-8")
    n = len(_ledger(lib))
    r = _run(w, "kicad-export", lib / "plan.json", "--out", "kx")
    assert r.returncode == 0, r.stdout + r.stderr
    new = _ledger(lib)[n:]
    rel = {e.get("part") for e in new if e["event"] == "release"}
    assert set(RES) <= rel, [(e["event"], e.get("part"), e.get("decision")) for e in new]
    assert not [e for e in new if e["event"] == "hold"], [(e["event"], e.get("part")) for e in new]


def test_fix_changes_1c_baseline_runs_the_release_check_not_the_plan_status():
    """upkeep Q1: a held part whose status was set to "ready" by hand got `release r1 baseline` from the first
    publisher. Baseline now runs the release check, so it stays unreleased."""
    w, lib = _demo()
    plan = lib / "plan.json"
    items = json.loads(plan.read_text(encoding="utf-8"))
    for i in items:
        if i["name"] in ("AO3401A", "CH340C"):
            assert i["status"] == "held"
            i["status"] = "ready"
    plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
    n = len(_ledger(lib))
    r = _run(w, "kicad-export", plan, "--out", "kx")
    assert r.returncode == 0, r.stdout + r.stderr
    new = _ledger(lib)[n:]
    assert not [e for e in new if e["event"] == "release"], [(e["event"], e.get("part"), e.get("decision")) for e in new]
    assert "skipped  CH340C" in r.stdout and "skipped  AO3401A" in r.stdout, r.stdout     # and nothing shipped


@lite
def test_fix_changes_1d_baseline_still_records_a_part_that_passes(tmp_path, monkeypatch):
    """A part that passes the release check still gets its revision 1 (no false hold from the new check)."""
    from ecadlib import change_gate as CG
    from tests.ui_fixture import build
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    with contextlib.redirect_stdout(io.StringIO()):
        ws, bom = build(tmp_path / "ws")
    plan = ws.root / "librarian" / "sample_bom" / "plan.json"
    ready = [n for n, i in _items(plan).items() if i["status"] == "ready"]
    assert ready
    from ecadlib import ledger as LG
    heads = {n: LG.Ledger(plan.parent).head(n) for n in ready}
    assert all(heads.values()), heads                    # the librarian's own baseline recorded them


# ------------------------------------------------------------------ R-PUB-1 / R-PUB-2: edited recipe / STEP beside a released part
C_RECIPE = "models/PcbLib/C_0402_1005Metric.PcbLib.ecadbuild"


def _move_pad1(lib):
    """Move pad 1 in CAP-0402-100nF's Altium build recipe (unshared footprint) -> (released bytes, edited pad line)."""
    rp = lib / C_RECIPE
    released = rp.read_bytes()
    t = rp.read_text(encoding="utf-8")
    p1 = next(x for x in t.splitlines() if x.startswith("PAD\t1\t"))
    f = p1.split("\t")
    f[2] = str(int(f[2]) - 300000)
    new = "\t".join(f)
    rp.write_text(t.replace(p1, new, 1), encoding="utf-8")
    return released, new


def test_fix_changes_rpub1_a_field_change_does_not_carry_an_edited_recipe_out():
    """R-PUB-1: the side-file check ran only for parts that were otherwise unchanged. A description edit (pass) plus a
    moved pad in the .ecadbuild recorded r2 with the edited recipe, and altium-pkg shipped the moved pad."""
    w, lib = _demo()
    released, new = _move_pad1(lib)
    plan = json.loads((lib / "plan.json").read_text(encoding="utf-8"))
    it = next(i for i in plan if i["name"] == "CAP-0402-100nF")
    it["description"] = (it.get("description") or "") + " (edited)"
    (lib / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    r = _run(w, "altium-pkg", lib / "plan.json", "--out", "apkg")
    assert new not in (w / "apkg/ecadlib_build.txt").read_text(encoding="utf-8"), r.stdout + r.stderr
    assert (lib / C_RECIPE).read_bytes() == released
    ov = [e for e in _ledger(lib) if e["event"] == "overwritten" and e.get("part") == "CAP-0402-100nF"]
    assert ov, [(e["event"], e.get("part")) for e in _ledger(lib)]
    rel = [e for e in _ledger(lib) if e["event"] == "release" and e.get("part") == "CAP-0402-100nF"]
    assert rel[-1]["revision"] == 2 and rel[-1]["files"]["footprint"]["ecadbuild"] == __import__("hashlib").sha256(released).hexdigest()


def test_fix_changes_rpub2_a_resaved_pcblib_does_not_hide_an_edited_recipe():
    """R-PUB-2: the side files were compared only when the .PcbLib's bytes were the head's. Re-saved (same content,
    other bytes) with a moved pad in its .ecadbuild, altium-pkg shipped the moved pad."""
    w, lib = _demo()
    pl = lib / "models/PcbLib/C_0402_1005Metric.PcbLib"
    pl.write_bytes(pl.read_bytes() + b"\0" * 512)
    released, new = _move_pad1(lib)
    r = _run(w, "altium-pkg", lib / "plan.json", "--out", "apkg")
    assert new not in (w / "apkg/ecadlib_build.txt").read_text(encoding="utf-8"), r.stdout + r.stderr
    assert (lib / C_RECIPE).read_bytes() == released
    assert [e for e in _ledger(lib) if e["event"] == "overwritten" and e.get("part") == "CAP-0402-100nF"]


def test_fix_changes_rpub_no_false_fix_after_librarian_refresh():
    """No `overwritten` line from the side check after a librarian --refresh and a publisher (nothing was edited)."""
    w, lib = _demo()
    n = len(_ledger(lib))
    out = ""
    for how in (("librarian", "ws/uploads/demo/demo_bom.csv", "-o", lib, "--offline", "--sources", "standard", "--refresh"),
                ("rebuild", lib), ("altium-pkg", lib / "plan.json", "--out", "apkg"), ("kicad-export", lib / "plan.json",
                                                                                     "--out", "kx")):
        r = _run(w, *how)
        out += r.stdout + r.stderr
    assert "Traceback" not in out, out
    assert not [e for e in _ledger(lib)[n:] if e["event"] in ("overwritten", "hold")], out


# ------------------------------------------------------------------ R-PUB-5: PCM identifier truncation and churn
def test_fix_changes_rpub5_long_names_keep_distinct_identifiers():
    from ecadlib import kicad_pcm as KP
    a, b = "A" * 85 + "one", "A" * 85 + "two"
    ia, ib = KP.default_identifier(a), KP.default_identifier(b)
    assert ia != ib and KP.ID_RE.match(ia) and KP.ID_RE.match(ib), (ia, ib)
    n1, n2 = "部" * 60 + "品", "部" * 60 + "抵"     # long non-ASCII names: the hash is never cut off
    assert KP.default_identifier(n1) != KP.default_identifier(n2)
    assert KP.ID_RE.match(KP.default_identifier(n1))
    assert KP.default_identifier("mylib") == "local.ecadlib.mylib"           # short ASCII names unchanged


def test_fix_changes_rpub5_an_existing_package_keeps_its_identifier():
    """A package built before (kicad_pcm.json in the out folder, same library) keeps its identifier when the default
    formula changed (Bibliothèque was local.ecadlib.Biblioth-que before upkeep-05): an upgrade must not install a
    second KiCad package and move the 3D paths."""
    from ecadlib import kicad_pcm as KP
    lib = "Bibliothèque"
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        (out / "kicad_pcm.json").write_text(json.dumps({"identifier": "local.ecadlib.Biblioth-que", "library": lib}),
                                            encoding="utf-8")
        assert KP.reuse_identifier(out, lib) == "local.ecadlib.Biblioth-que"
        assert KP.reuse_identifier(out, "Other") is None             # another library's record: not reused
        (out / "kicad_pcm.json").write_text("{garbled", encoding="utf-8")
        assert KP.reuse_identifier(out, lib) is None


# ------------------------------------------------------------------ 2: web UI actions during a librarian run
ANN = "Ann Lee <ann@example.com>"


class _BusyLibrary:
    """The ui_fixture workspace served over HTTP, with a real librarian run (POST /api/librarian/run, every part redone)
    stopped at its first part look-up: it holds the plan's library lock until release() (as a long online run does)."""

    def __init__(self, tmp_path, monkeypatch):
        from ecadlib import librarian as L
        from tests.test_ui import serve
        from tests.ui_fixture import build
        monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("ECADLIB_LOCK_WAIT", "60")
        with contextlib.redirect_stdout(io.StringIO()):
            self.ws, self.bom = build(tmp_path / "ws")
        self.plan = self.ws.root / "librarian" / "sample_bom" / "plan.json"
        self.started, self.go = threading.Event(), threading.Event()
        real = L.Run.resolve
        me = self

        def resolve(self_, *a, **k):
            me.started.set()
            me.go.wait(60)
            return real(self_, *a, **k)
        monkeypatch.setattr(L.Run, "resolve", resolve)
        self.srv, self.call = serve(self.ws)

    def start(self):
        st, j = self.call("POST", "/api/librarian/run", {"bom": self.bom, "refresh": True})
        assert st == 200, j
        self.run_job = j["job"]
        assert self.started.wait(30), "the librarian run did not start"
        from ecadlib import ledger as LG
        got = None
        try:                                          # it holds the library lock now
            got = LG.lock(self.plan.parent, wait=0.2)
        except LG.LedgerError:
            pass
        if got is not None:
            LG.unlock(got)
            raise AssertionError("the librarian run does not hold the library lock")

    def timed(self, method, path, body, limit=15):
        t0 = time.time()
        st, j = self.call(method, path, body)
        took = time.time() - t0
        assert took < limit, f"{path} took {took:.1f} s while the librarian ran (the UI must not hang)"
        return st, j

    def release(self):
        from tests.test_ui import wait_job
        self.go.set()
        j = wait_job(self.call, self.run_job, timeout=120)
        assert j["state"] == "done", j
        return j

    def close(self):
        self.go.set()
        self.srv.RequestHandlerClass.job_runner.wait(30)
        self.srv.shutdown()
        self.srv.server_close()


def _plan_items(plan):
    return {i["name"]: i for i in json.loads(Path(plan).read_text(encoding="utf-8"))}


@lite
def test_fix_changes_2a_release_anyway_during_a_librarian_run_is_recorded_at_once_and_kept(tmp_path, monkeypatch):
    """Release anyway while a librarian run holds the library lock: answered at once (no 5 s refusal, no 600 s wait),
    recorded in plan.json, and the run's own plan write keeps it (the part ends ready, released in the ledger)."""
    b = _BusyLibrary(tmp_path, monkeypatch)
    try:
        b.start()
        st, j = b.timed("POST", "/api/release-check", {"bom": b.bom, "allow": ["AO3401A"], "user": ANN})
        assert st == 200, j
        assert _plan_items(b.plan)["AO3401A"].get("allow"), "the release anyway was not recorded"
        assert j.get("pending"), j                    # it says the release is recorded when the run finishes
        b.release()
        it = _plan_items(b.plan)["AO3401A"]
        assert it.get("allow") and it["status"] == "ready", (it.get("allow"), it["status"], it.get("hold_reasons"))
        from ecadlib import ledger as LG
        assert LG.Ledger(b.plan.parent).head("AO3401A"), "no released revision after the run"
    finally:
        b.close()


@lite
def test_fix_changes_2b_sign_off_reject_during_a_librarian_run_is_kept(tmp_path, monkeypatch):
    b = _BusyLibrary(tmp_path, monkeypatch)
    try:
        b.start()
        st, j = b.timed("POST", "/api/approvals/decide", {"bom": b.bom, "part": "CAP-0402-100nF", "decision": "reject",
                                                          "user": ANN, "comment": "wrong dielectric"})
        assert st == 200, j
        b.release()
        it = _plan_items(b.plan)["CAP-0402-100nF"]
        assert [a["decision"] for a in it.get("approvals") or []] == ["reject"], it.get("approvals")
        assert it["status"] != "ready", it["status"]
    finally:
        b.close()


@lite
def test_fix_changes_2c_revert_during_a_librarian_run_is_queued_and_applied_when_it_ends(tmp_path, monkeypatch):
    """A revert (rewrites library files) made while the run holds the library lock is accepted as a job that waits
    for the run, says so, and is applied once the run ends: not refused, not lost, no hang."""
    from ecadlib import change_gate as CG, ledger as LG
    from tests.test_ui import wait_job
    b = _BusyLibrary(tmp_path, monkeypatch)
    try:
        items = json.loads(b.plan.read_text(encoding="utf-8"))
        cap = next(i for i in items if i["name"] == "CAP-0402-100nF")
        cap["description"] = (cap.get("description") or "") + " (edited)"
        b.plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            CG.publish_guard(b.plan, items, "kicad")          # r2 of CAP-0402-100nF
        assert LG.Ledger(b.plan.parent).head("CAP-0402-100nF")["revision"] == 2
        b.start()
        # the run builds CAP-0402-100nF from the BOM again (its description back: r3); the queued revert to r2 is
        # applied after it, as r4
        st, j = b.timed("POST", "/api/change/revert", {"bom": b.bom, "part": "CAP-0402-100nF", "revision": 2,
                                                       "user": ANN, "comment": "back to the edited r2"})
        assert st == 200 and j.get("queued") and j.get("job"), j
        assert "waiting for the librarian run to finish" in j.get("waiting", ""), j
        time.sleep(1.0)
        st, q = b.call("GET", f"/api/job?id={j['job']}")
        assert q["state"] == "running" and "waiting for the librarian run" in q["label"], q   # still waiting
        b.release()
        done = wait_job(b.call, j["job"], timeout=60)
        assert done["state"] == "done", (done["state"], done.get("error"))
        ev = [e for e in LG.Ledger(b.plan.parent).history("CAP-0402-100nF") if e.get("event") == "revert"]
        assert ev, [e.get("event") for e in LG.Ledger(b.plan.parent).history("CAP-0402-100nF")]
        assert _plan_items(b.plan)["CAP-0402-100nF"]["description"].endswith("(edited)")
    finally:
        b.close()


@lite
def test_fix_changes_2d_a_queued_action_that_fails_reports_its_error(tmp_path, monkeypatch):
    """The queued job reports a refusal (here: no such revision) like any other UI job, once the run ends."""
    from tests.test_ui import wait_job
    b = _BusyLibrary(tmp_path, monkeypatch)
    try:
        b.start()
        st, j = b.timed("POST", "/api/change/revert", {"bom": b.bom, "part": "CAP-0402-100nF", "revision": 9,
                                                       "user": ANN, "comment": "x"})
        assert st == 200 and j.get("queued"), j
        b.release()
        done = wait_job(b.call, j["job"], timeout=60)
        assert done["state"] == "error" and done["error"], done
    finally:
        b.close()


@lite
def test_fix_changes_2e_a_command_line_run_holding_the_lock_queues_and_keeps_ui_actions(tmp_path, monkeypatch):
    """A fake command-line writer (another process in real life: no UI job) holds the library lock, then writes its old
    copy of plan.json through librarian.save_plan, as change control's writers do. Meanwhile the UI records a release
    anyway (kept by that write) and a revert (queued, applied once the lock is free). Nothing deadlocks."""
    from ecadlib import change_gate as CG, ledger as LG, librarian as L
    from tests.test_ui import serve, wait_job
    from tests.ui_fixture import build
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ECADLIB_LOCK_WAIT", "60")
    with contextlib.redirect_stdout(io.StringIO()):
        ws, bom = build(tmp_path / "ws")
    plan = ws.root / "librarian" / "sample_bom" / "plan.json"
    items = json.loads(plan.read_text(encoding="utf-8"))
    cap = next(i for i in items if i["name"] == "CAP-0402-100nF")
    cap["description"] = (cap.get("description") or "") + " (edited)"
    plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
    with contextlib.redirect_stderr(io.StringIO()):
        CG.publish_guard(plan, items, "kicad")                # r2
    holding, go, errors = threading.Event(), threading.Event(), []

    def writer():
        try:
            with LG.locked(plan.parent):
                old = json.loads(plan.read_text(encoding="utf-8"))
                holding.set()
                go.wait(30)
                L.save_plan(plan, old)                       # its old copy: decisions recorded meanwhile are kept
        except Exception as e:                               # pragma: no cover - reported below
            errors.append(e)
    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert holding.wait(10)
    srv, call = serve(ws)
    try:
        t0 = time.time()
        st, j = call("POST", "/api/release-check", {"bom": bom, "allow": ["AO3401A"], "user": ANN})
        assert st == 200 and j.get("pending"), (st, j)
        st, q = call("POST", "/api/change/revert", {"bom": bom, "part": "CAP-0402-100nF", "revision": 1, "user": ANN,
                                                    "comment": "back"})
        assert st == 200 and q.get("queued") and WAITING_TEXT in q["waiting"], (st, q)
        assert time.time() - t0 < 15, "the UI waited for the writer"
        time.sleep(0.5)
        assert call("GET", f"/api/job?id={q['job']}")[1]["state"] == "running"
        go.set()
        t.join(20)
        assert not t.is_alive() and not errors, errors
        assert _plan_items(plan)["AO3401A"].get("allow"), "the writer's plan write dropped the release anyway"
        done = wait_job(call, q["job"], timeout=30)
        assert done["state"] == "done", (done["state"], done.get("error"))
        assert LG.Ledger(plan.parent).head("CAP-0402-100nF")["revision"] == 3
        assert [e for e in LG.Ledger(plan.parent).history("CAP-0402-100nF") if e.get("event") == "revert"]
    finally:
        go.set()
        srv.RequestHandlerClass.job_runner.wait(30)
        srv.shutdown()
        srv.server_close()


WAITING_TEXT = "waiting for the librarian run to finish"
