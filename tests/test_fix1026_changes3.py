"""2026-10-26 fixes "changes", round 3 (fixes3.md): a tool writer that did not rebuild a file never releases an edited
recipe beside it (R-FX3-1); a release re-checks the recipe and 3D models it copied (R-FX3-2); dblib-sync / altium-pkg
ship the released recipe, not one saved after the gate (R-FX3-3); a reject by someone no longer in the sign-off
policy can be lifted (R-FX3-4).
Offline.  python3 -m pytest -q tests/test_fix1026_changes3.py   (or python -m tests.run_all test_fix1026_changes3)"""
import contextlib, copy, io, json
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
    from ecadlib import change_gate as CG
    w, lib = _demo()
    plan = lib / "plan.json"
    released = _recipe(lib).read_text(encoding="utf-8")
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
    assert _head_recipe(lib) == released
    assert _recipe(lib).read_text(encoding="utf-8") == released
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
    from ecadlib import change_gate as CG, ledger as LG
    w, lib = _demo()
    plan = lib / "plan.json"
    released = _recipe(lib).read_text(encoding="utf-8")
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
    assert _head_recipe(lib) == released
    assert _recipe(lib).read_text(encoding="utf-8") == released     # put back, the edit kept in the blob store


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
