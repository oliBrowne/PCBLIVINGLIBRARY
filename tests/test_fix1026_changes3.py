"""2026-10-26 fixes "changes", round 3 (fixes3.md): a tool writer that did not rebuild a file never releases an edited
recipe beside it (R-FX3-1); a release re-checks the recipe and 3D models it copied (R-FX3-2); dblib-sync / altium-pkg
ship the released recipe, not one saved after the gate (R-FX3-3); a reject by someone no longer in the sign-off
policy can be lifted (R-FX3-4).
Offline.  python3 -m pytest -q tests/test_fix1026_changes3.py   (or python -m tests.run_all test_fix1026_changes3)"""
import contextlib, copy, io, json, os
from pathlib import Path

from tests.fixtures_lite import lite
from tests.test_fix1026_changes2 import _demo, _ledger, _run

CAP = "CAP-0402-100nF"
ANN = "Ann Lee <ann@example.com>"
BOB = "Bob Roe <bob@example.com>"


def _recipe(lib):
    return lib / "models/PcbLib/C_0402_1005Metric.PcbLib.ecadbuild"


def _moved_pad(lib):
    """Move PAD 1 in the C_0402 recipe on disk (the .PcbLib is not touched) -> (edited recipe text, the moved line)."""
    rp = _recipe(lib)
    t = rp.read_text(encoding="utf-8")
    p1 = next(l for l in t.splitlines() if l.startswith("PAD\t1\t"))
    f = p1.split("\t")
    f[2] = str(int(f[2]) - 300000)
    new = "\t".join(f)
    return t.replace(p1, new, 1), new


def _head_recipe(lib, part=CAP):
    from ecadlib import ledger as LG
    lg = LG.Ledger(lib)
    return lg.get_bytes(lg.head(part)["files"]["footprint"]["ecadbuild"]).decode()


# ------------------------------------------------------------------ R-FX3-1
def test_fix3_rfx31_style_apply_does_not_release_an_edited_recipe_beside_a_resaved_pcblib():
    """ride3.py style: the .PcbLib re-saved (other bytes, same footprint), PAD 1 moved in its recipe, then a style
    apply's cosmetic change. The released revision must not take the edited recipe: it is put back (kept in the blob
    store with an `overwritten` line), and the r2 that style apply records has the released recipe."""
    _rfx31(*_demo())


def _rfx31(w, lib):
    from ecadlib import change_gate as CG
    plan = lib / "plan.json"
    released = _recipe(lib).read_bytes()                       # bytes: on Windows the recipe has "\r\n"
    pl = lib / "models/PcbLib/C_0402_1005Metric.PcbLib"
    pl.write_bytes(pl.read_bytes() + b"\0" * 512)
    edited, new = _moved_pad(lib)
    _recipe(lib).write_text(edited, encoding="utf-8")
    items = json.loads(plan.read_text(encoding="utf-8"))
    after = copy.deepcopy(items)
    cap = next(i for i in after if i["name"] == CAP)
    cap["description"] = (cap.get("description") or "") + " (house style)"
    g = CG.Guard(lib, "style-apply")
    g.begin(items)
    g.restyle(items, after)
    g.close()
    assert new not in _head_recipe(lib), "the edited recipe was released by style apply"
    assert _head_recipe(lib).encode("utf-8") == released
    assert _recipe(lib).read_bytes() == released
    ev = [e for e in _ledger(lib) if e.get("part") == CAP]
    assert any(e["event"] == "overwritten" and e.get("writer") == "style-apply" for e in ev), ev


def test_fix3_rfx31_a_writer_that_rebuilt_the_file_keeps_its_own_recipe():
    """A writer that rewrote the .PcbLib itself since begin() wrote its recipe with it: the same footprint in other
    bytes and a new recipe is not called a hand edit (no `overwritten` line, the recipe stays)."""
    from ecadlib import change_gate as CG
    w, lib = _demo()
    plan = lib / "plan.json"
    items = json.loads(plan.read_text(encoding="utf-8"))
    g = CG.Guard(lib, "librarian")
    g.begin(items)
    pl = lib / "models/PcbLib/C_0402_1005Metric.PcbLib"
    pl.write_bytes(pl.read_bytes() + b"\0" * 512)              # the writer's own rebuild: other bytes
    rp = _recipe(lib)
    mine = rp.read_text(encoding="utf-8") + "\n"               # ... and its recipe, written anew
    rp.write_text(mine, encoding="utf-8")
    from ecadlib import altium_native as AN                    # a writer's own write is announced (R-G2-1): bytes
    AN.note_written(pl)                                        # that only changed since begin() are no sign
    g.settle(items)
    g.close()
    assert not [e for e in _ledger(lib) if e.get("event") == "overwritten"], _ledger(lib)
    assert rp.read_text(encoding="utf-8") == mine


# ------------------------------------------------------------------ R-FX3-2
@lite
def test_fix3_rfx32_a_recipe_saved_while_a_release_is_recorded_is_not_released(tmp_path, monkeypatch):
    """repro_side_snapshot_race.py: a description-only (pass) change is being released; the recipe is saved with PAD 1
    moved between the side-file check and the copy into the blob store. The released revision must not carry the
    edited recipe: the release is classified again and the released recipe goes back first."""
    _rfx32(*_demo(), monkeypatch)


def _rfx32(w, lib, monkeypatch):
    from ecadlib import change_gate as CG, ledger as LG
    plan = lib / "plan.json"
    released = _recipe(lib).read_bytes()                       # bytes: on Windows the recipe has "\r\n"
    edited, new = _moved_pad(lib)
    items = json.loads(plan.read_text(encoding="utf-8"))
    cap = next(i for i in items if i["name"] == CAP)
    cap["description"] = (cap.get("description") or "") + " (restyled)"
    plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
    real, done = LG.Ledger.snapshot_files, []

    def snap(self, item, base=None):
        if item.get("name") == CAP and not done:
            done.append(1)
            _recipe(lib).write_text(edited, encoding="utf-8")      # the person saves the recipe now
        return real(self, item, base)
    monkeypatch.setattr(LG.Ledger, "snapshot_files", snap)
    with contextlib.redirect_stderr(io.StringIO()):
        CG.publish_guard(plan, items, "altium")
    assert done
    head = LG.Ledger(lib).head(CAP)
    assert head["revision"] == 2 and "(restyled)" in (head["item"].get("description") or ""), head
    assert new not in _head_recipe(lib), "the recipe saved during the release was recorded as released"
    assert _head_recipe(lib).encode("utf-8") == released
    assert _recipe(lib).read_bytes() == released               # put back, the edit kept in the blob store


# ------------------------------------------------------------------ Windows newlines (CI on Windows)
_WIN = {}


def _windows_text(monkeypatch=None):
    """Path.write_text as on Windows: text mode writes "\r\n" for each "\n" (unless newline= is given). Returns the
    folder of a sitecustomize.py that does the same in a child ecadlib process."""
    import pathlib, tempfile
    if "site" not in _WIN:
        d = Path(tempfile.mkdtemp(prefix="ecadlib-wintext-"))
        (d / "sitecustomize.py").write_text(
            "import pathlib\n_w = pathlib.Path.write_text\n"
            "def write_text(self, data, encoding=None, errors=None, newline=None):\n"
            "    return _w(self, data, encoding=encoding, errors=errors, newline='\\r\\n' if newline is None else newline)\n"
            "pathlib.Path.write_text = write_text\n", encoding="utf-8")
        _WIN["site"] = d
    if monkeypatch is not None:
        real = pathlib.Path.write_text

        def write_text(self, data, encoding=None, errors=None, newline=None):
            return real(self, data, encoding=encoding, errors=errors, newline="\r\n" if newline is None else newline)
        monkeypatch.setattr(pathlib.Path, "write_text", write_text)
    return _WIN["site"]


def _demo_windows():
    """A fresh copy of the demo built by an ecadlib whose text files have Windows line ends (recipes with \r\n)."""
    import shutil, subprocess, sys, tempfile
    from tests.test_fix1026_changes2 import REPO
    if "demo" not in _WIN:
        d = Path(tempfile.mkdtemp(prefix="ecadlib-windemo-"))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "ECADLIB_")) and k != "NEXAR_TOKEN"}
        env.update(ECADLIB_HOME=str(d / "home"), NO_COLOR="1", ECADLIB_USER="Tester <tester@example.com>",
                   PYTHONPATH=os.pathsep.join([str(_windows_text()), str(REPO), env.get("PYTHONPATH", "")]))
        r = subprocess.run([sys.executable, "-m", "ecadlib", "demo", "-o", "ws"], cwd=d, env=env, capture_output=True,
                           text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        _WIN["demo"] = d
    w = Path(tempfile.mkdtemp(prefix="ecadlib-win-"))
    shutil.copytree(_WIN["demo"] / "ws", w / "ws")
    lib = w / "ws/librarian/demo_bom_demo"
    assert b"\r\n" in _recipe(lib).read_bytes()           # the simulation took: the recipes have Windows line ends
    return w, lib


@lite
def test_fix3_rfx31_rfx32_with_windows_line_ends(tmp_path, monkeypatch):
    """Windows CI failed the two tests above (the released recipe compared as text read back with "\n" against the
    blob store's bytes with "\r\n"). The same steps on a library whose text files have Windows line ends, written
    the Windows way here too."""
    w, lib = _demo_windows()
    _windows_text(monkeypatch)
    _rfx31(w, lib)
    _rfx32(*_demo_windows(), monkeypatch)


# ------------------------------------------------------------------ R-FX3-3
@lite
def test_fix3_rfx33_dblib_sync_ships_the_released_recipe_and_pcblib_not_an_edit_saved_after_the_gate(tmp_path,
                                                                                                    monkeypatch):
    """repro_altium_midrun_edit.py dblib-sync: after publish_guard settled the library (and after dblib-sync's own
    release check of the part), PAD 1 is moved in the recipe and in the .PcbLib. dblib-sync must copy the released
    revision's .PcbLib, recipe and STEP files (from the ledger's blob store), with a note, never the edit."""
    import hashlib
    from ecadlib import altium_dblib_sync as DS, altium_lib as AL, ledger as LG, stdparts as SP
    w, lib = _demo()
    plan = lib / "plan.json"
    pcb = lib / "models/PcbLib/C_0402_1005Metric.PcbLib"
    edited, new = _moved_pad(lib)
    conv = AL.footprint_from_kicad(SP.build("C_0402_1005Metric")[0])
    conv["pads"][0]["x"] -= 0.3
    real, done = DS.RC.publish_problems, []

    def check(it, *a, **k):
        r = real(it, *a, **k)
        if it.get("name") == CAP and not done:                    # the person saves now
            done.append(1)
            AL.write_pcblib(pcb, [conv])                          # (it writes a recipe too: the edited one after)
            _recipe(lib).write_text(edited, encoding="utf-8")
        return r
    monkeypatch.setattr(DS.RC, "publish_problems", check)
    out = w / "out"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        res = DS.sync(plan, out, "dblib", "ecadlib")
    head = LG.Ledger(lib).head(CAP)
    shipped = list(out.rglob("C_0402_1005Metric.PcbLib"))
    assert shipped and all(hashlib.sha256(p.read_bytes()).hexdigest() == head["files"]["footprint"]["sha256"]
                           for p in shipped)
    recs = list(out.rglob("C_0402_1005Metric.PcbLib.ecadbuild"))
    assert recs and not any(new in p.read_text(encoding="utf-8") for p in recs)
    assert done and _recipe(lib).read_text(encoding="utf-8") == edited   # the edit is left for the next writer
    row = next(r for r in res["results"] if r.get("name") == CAP)
    assert row["action"] in ("added", "updated", "up_to_date"), row
    assert "changed while" in row["detail"], row


# ------------------------------------------------------------------ R-FX3-4
def _part():
    return {"name": "P1", "status": "ready", "description": "Resistor 10k", "parameters": [],
            "symbol": {"ref": "R", "file": None}, "footprint": {"ref": "R_0603", "file": None}}


def _try(it, dec, who, d, pol, c=""):
    from ecadlib import approvals as AP
    try:
        AP.decide(it, dec, who, comment=c, base=d, policy=pol)
        return True
    except AP.Refused:
        return False


@lite
def test_fix3_rfx34_a_reject_by_someone_who_left_the_policy_can_be_lifted(tmp_path, monkeypatch):
    """repro_stuck_reject.py A: Ann rejects, then the policy's users become {bob} and the part is restyled. Bob's
    approve, recorded after her reject, lifts it; or Ann takes her own reject back (recorded, not counted as a
    sign-off). Before either, the reject still holds."""
    from ecadlib import approvals as AP
    d = str(tmp_path)
    pol = AP.normalize_policy({"required": 1, "users": {"bob@example.com": ["librarian"]}})
    it = _part()
    AP.decide(it, "reject", ANN, comment="wrong pin 9", base=d)
    it["description"] += " (fixed)"
    assert not AP.evaluate(it, pol, d)["met"]                        # still held until someone acts
    assert _try(it, "approve", BOB, d, pol)                          # a current approver lifts it
    ev = AP.evaluate(it, pol, d)
    assert ev["met"] and ev["approved_by"] == [BOB], ev
    it2 = _part()                                                    # or Ann takes her own reject back
    AP.decide(it2, "reject", ANN, comment="wrong pin 9", base=d)
    assert _try(it2, "approve", ANN, d, pol)
    ev2 = AP.evaluate(it2, pol, d)
    assert not ev2["rejected_by"] and ev2["approved_by"] == [] and not ev2["met"], ev2   # not a sign-off
    assert not _try(_part(), "approve", ANN, d, pol)                 # without a reject to take back: still refused
    it3 = _part()                                                    # an approve recorded before the reject does not
    AP.decide(it3, "approve", BOB, base=d, policy=pol)               # lift it
    AP.decide(it3, "reject", ANN, comment="wrong pin 9", base=d)
    assert not AP.evaluate(it3, pol, d)["met"]


@lite
def test_fix3_rfx34_a_rejecter_who_released_it_anyway_can_take_the_reject_back(tmp_path, monkeypatch):
    """repro_stuck_reject.py B: with no_self_approval, Ann rejected, then released it anyway (--allow). Her approve takes
    the reject back (it is not counted as a sign-off), and Bob's approve then signs it off."""
    from ecadlib import approvals as AP
    d = str(tmp_path)
    pol = AP.normalize_policy({"required": 1})
    it = _part()
    AP.decide(it, "reject", ANN, comment="pads too small", base=d)
    it["allow"] = {"user": ANN, "reason": "release anyway"}
    assert _try(it, "approve", ANN, d, pol)
    ev = AP.evaluate(it, pol, d)
    assert not ev["rejected_by"] and not ev["met"] and ev["approved_by"] == [], ev
    assert _try(it, "approve", BOB, d, pol)
    assert AP.evaluate(it, pol, d)["met"]
    assert not _try(it, "approve", ANN, d, pol)                      # nothing left to take back: refused as before


# ------------------------------------------------------------------ follow-up: kicad-export's .PcbLib / .SchLib reads
def _kx_with_midrun_edit(edit, pcblib_only=True):
    """kicad-export of a fresh demo; `edit(lib)` runs right after the export's own release check of CAP (an editor
    saving then takes no lock). pcblib_only: the footprint is converted back from the .PcbLib (no .kicad_mod found).
    -> (report, out folder, lib folder)"""
    from ecadlib import kicad_export as X, release_check as RC
    w, lib = _demo()
    real_pp, real_km, done = RC.publish_problems, RC._kicad_mod, []

    def check(it, *a, **k):
        r = real_pp(it, *a, **k)
        if it.get("name") == CAP and not done:
            done.append(1)
            if edit:
                edit(lib)
            if pcblib_only:
                RC._kicad_mod = lambda *a, **k: None
        return r
    RC.publish_problems = check
    try:
        rep = X.export(lib / "plan.json", w / "kx", "ecadlib", log=lambda *a, **k: None)
    finally:
        RC.publish_problems, RC._kicad_mod = real_pp, real_km
    assert done
    return rep, w / "kx", lib


def test_fix4_kicad_export_converts_the_released_pcblib_not_an_edit_saved_meanwhile():
    """fp_source's converted-back path (footprint_from_pcblib) and the 3D model read: PAD 1 is moved in the .PcbLib
    after the export checked the part. The released footprint is exported (as without the edit), with a warning."""
    from ecadlib import altium_lib as AL, stdparts as SP

    def move(lib):
        conv = AL.footprint_from_kicad(SP.build("C_0402_1005Metric")[0])
        conv["pads"][0]["x"] -= 0.3
        AL.write_pcblib(lib / "models/PcbLib/C_0402_1005Metric.PcbLib", [conv])
    ref, rout, _ = _kx_with_midrun_edit(None)
    rep, out, _ = _kx_with_midrun_edit(move)
    assert "converted back" in rep["footprints"]["C_0402_1005Metric"]["from"], rep["footprints"]["C_0402_1005Metric"]
    got = (out / "ecadlib.pretty/C_0402_1005Metric.kicad_mod").read_text(encoding="utf-8")
    assert got == (rout / "ecadlib.pretty/C_0402_1005Metric.kicad_mod").read_text(encoding="utf-8")
    assert any("C_0402_1005Metric.PcbLib changed while the export ran" in w for w in rep["warnings"]), rep["warnings"]


def test_fix4_kicad_export_reads_back_the_released_schlib_not_an_edit_saved_meanwhile():
    """load_symbol's .SchLib read-back: pin 2 of the capacitor renamed (an electrical edit) after the export checked
    the part. The released symbol is exported, with a warning."""
    from ecadlib import altium_lib as AL

    def pin(lib):
        it = next(i for i in json.loads((lib / "plan.json").read_text(encoding="utf-8")) if i["name"] == CAP)
        sym = AL.capacitor_symbol(name=it["symbol"]["ref"])
        sym["prims"][3] = ("pin", {"num": "2", "name": "VBAD", "type": "input"}, "right", 3, 0, 27, False)
        AL.write_schlib(lib / it["symbol"]["file"], [sym])
    rep, out, _ = _kx_with_midrun_edit(pin, pcblib_only=False)
    assert "VBAD" not in (out / "ecadlib.kicad_sym").read_text(encoding="utf-8")
    assert any("Capacitor.SchLib changed while the export ran" in w for w in rep["warnings"]), rep["warnings"]


def test_fix4_kicad_export_ships_nothing_when_the_released_copy_is_missing():
    """The .PcbLib changed meanwhile and the ledger's copy of it is gone: an error for that footprint, the edit is not
    exported."""
    from ecadlib import altium_lib as AL, ledger as LG, stdparts as SP

    def move(lib):
        lg = LG.Ledger(lib)
        lg._blob(lg.head(CAP)["files"]["footprint"]["sha256"]).unlink()
        conv = AL.footprint_from_kicad(SP.build("C_0402_1005Metric")[0])
        conv["pads"][0]["x"] -= 0.3
        AL.write_pcblib(lib / "models/PcbLib/C_0402_1005Metric.PcbLib", [conv])
    rep, out, _ = _kx_with_midrun_edit(move)
    assert not (out / "ecadlib.pretty/C_0402_1005Metric.kicad_mod").exists()
    assert any("C_0402_1005Metric" in e and "released copy is missing" in e for e in rep["errors"]), rep["errors"]


# ------------------------------------------------------------------ R-FX7-1 (fixes7.md)
@lite
def test_fix7_rfx71_a_name_spelling_the_policy_refuses_is_no_sign_off_after_a_reject(tmp_path, monkeypatch):
    """abuse_reject.py A1: users {ann@, bob@}. Ann <ann@example.com> rejects; an approve by a bare "Ann Lee" (refused
    without the reject) must not be recorded as her take-back, and nothing it left may count as a sign-off. Ann's own
    take-back still works, and a record whose own key is not a listed user never counts."""
    from ecadlib import approvals as AP
    d = str(tmp_path)
    pol = AP.normalize_policy({"required": 1, "users": {"ann@example.com": ["qa"], "bob@example.com": ["qa"]}})
    it = _part()
    AP.decide(it, "reject", ANN, comment="wrong", base=d, policy=pol)
    assert not _try(it, "approve", "Ann Lee", d, pol)
    assert not AP.evaluate(it, pol, d)["met"]
    it2 = _part()                                                     # a record written by an older ecadlib
    AP.decide(it2, "reject", ANN, comment="wrong", base=d, policy=pol)
    it2["approvals"].append(dict(it2["approvals"][0], who="Ann Lee", key="ann lee", decision="approve", comment="",
                                 hash=AP.content_hash(it2, d)))
    ev = AP.evaluate(it2, pol, d)
    assert not ev["met"] and ev["approved_by"] == [], ev
    it3 = _part()
    AP.decide(it3, "reject", ANN, comment="wrong", base=d, policy=pol)
    assert _try(it3, "approve", ANN, d, pol)                          # her own take-back: recorded, and she is listed
    assert AP.evaluate(it3, pol, d)["met"]


# ------------------------------------------------------------------ R-FX7-2
def _a365_midrun(drop_blob=False):
    """a365_midrun2.py: RES-0603-10k's .PcbLib gets PAD 1 moved 0.3 mm after publish_guard and the push's own release
    check, before the upload. -> (push results, {uploaded file name: sha256}, head of RES-0603-10k)"""
    import hashlib, time
    from ecadlib import altium365 as A, altium_lib as AL, change_gate as CG, ledger as LG, rules as RU, stdparts as SP
    from tests.test_change_control import FakeA365, demo
    plan, lib = demo()
    items = json.loads(Path(plan).read_text(encoding="utf-8"))
    it = next(i for i in items if i["name"] == "RES-0603-10k")
    pcb = (lib / it["footprint"]["file"]).resolve()
    sent = {}

    class F(FakeA365):
        def find_model(self, ws, kind, name, folder):
            return None

        def release_defs(self, ws):                                  # after the push's own release check
            conv = AL.footprint_from_kicad(SP.build(pcb.stem)[0])
            conv["pads"][0]["x"] -= 0.3
            AL.write_pcblib(pcb, [conv])                             # the person saves now
            if drop_blob:
                lg = LG.Ledger(lib)
                lg._blob(lg.head("RES-0603-10k")["files"]["footprint"]["sha256"]).unlink()
            return super().release_defs(ws)

        def upload(self, p):
            sent[Path(p).name] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
            return super().upload(p)
    rules = RU.load(None, start=lib)
    rules["folders"]["root"] = "T"
    with contextlib.redirect_stderr(io.StringIO()), CG.publishing(Path(plan).parent):
        CG.publish_guard(Path(plan), items, "altium365", rules=rules)
        for i in items:
            for k in ("symbol", "footprint"):
                if i[k].get("file"):
                    i[k]["file"] = str((Path(plan).parent / i[k]["file"]).resolve())
        t0 = time.time()
        res = A.push(items, F(), "https://test.365.altium.com", rules=rules, folder=lib)
        A.record_push(Path(plan).parent, t0, res)
    return res, sent, LG.Ledger(lib).head("RES-0603-10k"), pcb.name


def test_fix7_rfx72_a365_push_uploads_the_released_pcblib_not_an_edit_saved_meanwhile():
    res, sent, head, name = _a365_midrun()
    row = next(r for r in res if r["name"] == "RES-0603-10k")
    assert row["action"] == "release", row
    assert sent.get(name) == head["files"]["footprint"]["sha256"], "the edited .PcbLib was uploaded"


def test_fix7_rfx72_a365_push_sends_nothing_when_the_released_copy_is_missing():
    res, sent, head, name = _a365_midrun(drop_blob=True)
    row = next(r for r in res if r["name"] == "RES-0603-10k")
    assert row["action"] == "error" and "released copy is missing" in str(row["detail"]), row


# ------------------------------------------------------------------ R-FX7-3
def _silk_edit(lib, move_recipe):
    """cosmetic_recipe.py silk: the C_0402 .PcbLib rewritten with its silkscreen 0.12 -> 0.15 mm (write_pcblib writes
    a matching recipe), then, if move_recipe, PAD 1 moved in that recipe only. -> the moved recipe line or None"""
    import re
    from ecadlib import altium_lib as AL, stdparts as SP
    txt = SP.build("C_0402_1005Metric")[0]
    txt = re.sub(r"\(stroke \(width 0\.12\)", "(stroke (width 0.15)", re.sub(r"\(width 0\.12\)", "(width 0.15)", txt))
    AL.write_pcblib(lib / "models/PcbLib/C_0402_1005Metric.PcbLib", [AL.footprint_from_kicad(txt)])
    if not move_recipe:
        return None
    edited, new = _moved_pad(lib)
    _recipe(lib).write_text(edited, encoding="utf-8")
    return new


def test_fix7_rfx73_an_edited_recipe_beside_a_changed_pcblib_is_held_not_released():
    """A real minor .PcbLib edit (silkscreen) plus PAD 1 moved in its recipe, found by a publisher's settle: the recipe
    no longer builds the .PcbLib, so the change is held (the released files go back) instead of released at warn;
    approving it as it is is refused."""
    from ecadlib import change_cli as CC, change_gate as CG, ledger as LG
    w, lib = _demo()
    plan = lib / "plan.json"
    released = _recipe(lib).read_bytes()
    new = _silk_edit(lib, True)
    items = json.loads(plan.read_text(encoding="utf-8"))
    with contextlib.redirect_stderr(io.StringIO()):
        CG.publish_guard(plan, items, "altium")
    head = LG.Ledger(lib).head(CAP)
    assert head["revision"] == 1 and new not in _head_recipe(lib), head
    assert _recipe(lib).read_bytes() == released                   # the released recipe is back
    prop = LG.Ledger(lib).proposal(CAP)
    assert prop and any("recipe" in w_ for w_ in prop["why"]), prop and prop["why"]
    try:
        CC.approve(plan, [CAP], who=ANN, comment="silk only")
        raise AssertionError("approved a change whose recipe does not build its .PcbLib")
    except CG.ChangeError as e:
        assert "recipe" in str(e), e
    assert LG.Ledger(lib).head(CAP)["revision"] == 1 and new not in _head_recipe(lib)


def test_fix7_rfx73_a_changed_pcblib_with_its_own_matching_recipe_still_releases():
    """No false hold: the same silkscreen edit written with its matching recipe (as every ecadlib .PcbLib write does)
    is released as before."""
    from ecadlib import change_gate as CG, ledger as LG
    w, lib = _demo()
    plan = lib / "plan.json"
    _silk_edit(lib, False)
    items = json.loads(plan.read_text(encoding="utf-8"))
    with contextlib.redirect_stderr(io.StringIO()):
        CG.publish_guard(plan, items, "altium")
    head = LG.Ledger(lib).head(CAP)
    assert head["revision"] == 2 and not LG.Ledger(lib).proposal(CAP), head
