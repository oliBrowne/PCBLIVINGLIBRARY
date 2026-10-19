"""Change control workflow (#105 review): approvals bound to the reviewed proposal, the two-person rule counting people
(not spellings) and covering reverts, a person's choice going through the release check, the library lock, a lost
ledger failing closed, the Altium project check reading pulled revisions, and a renamed part carrying a released MPN.

Each test is a regression for a finding of the #105 tester / critic review (ids in the docstrings)."""
import contextlib, io, json, subprocess, sys, tempfile, threading
from pathlib import Path
from unittest import mock

from ecadlib import altium_lib as AL, change_cli as CC, change_gate as CG, cliutil, fingerprint as FP
from ecadlib import ledger as LG, project_check as PJ, stdparts as SP
from ecadlib.approvals import same_person
from tests.test_change_control import ANN, BOB, ROOT, demo, item, items_of, write_symbol

SGD = [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")]
DSG = [("1", "D", "passive"), ("2", "S", "input"), ("3", "G", "passive")]


def raises(fn, *a, contains="", exc=CG.ChangeError, **k):
    try:
        fn(*a, **k)
    except exc as e:
        assert contains in str(e), e
        return e
    raise AssertionError(f"{fn.__name__} did not refuse")


def pins(plan, lib, name="AO3401A"):
    return {n: p["name"] for n, p in FP.of_item(item(items_of(plan), name), lib)["symbol"]["pins"].items()}


def held_hand_edit(plan, lib, pin_list, name="AO3401A"):
    """A hand edit of the part's symbol, found and held by a publisher -> the review digest of the held change."""
    items = items_of(plan)
    write_symbol(lib, item(items, name), pin_list)
    CG.publish_guard(plan, items, "kicad")
    return CG.review_digest(LG.Ledger(lib), name)


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = CC.main([str(a) for a in args])
    return rc, buf.getvalue() + err.getvalue()


def to_0402(lib, it):
    """Point a resistor at a new 0402 footprint (both tools' files written)."""
    text, _ = SP.build("R_0402_1005Metric")
    AL.write_pcblib(lib / "models" / "PcbLib" / "R_0402_1005Metric.PcbLib", [AL.footprint_from_kicad(text)])
    (lib / "models" / "KiCad" / "R_0402_1005Metric.kicad_mod").write_text(text, encoding="utf-8")
    it["footprint"] = {"ref": "R_0402_1005Metric", "file": "models/PcbLib/R_0402_1005Metric.PcbLib",
                       "kicad_mod": "models/KiCad/R_0402_1005Metric.kicad_mod"}


# ---------------------------------------------------------------- M2: an approval names the change reviewed
def test_approval_is_bound_to_the_reviewed_proposal():
    """M2 (s_stale1): Ann reviews an S-G-D proposal; another run replaces it with D-S-G before she approves. Her
    approval names the digest she saw, so it is refused and nothing is released; the new digest releases D-S-G."""
    plan, lib = demo()
    seen = held_hand_edit(plan, lib, SGD)
    rc, out = run_cli("show", lib, "AO3401A")
    assert rc == 0 and f"digest {seen}" in out and f"--digest {seen[:12]}" in out, out
    now = held_hand_edit(plan, lib, DSG)                      # proposed again while Ann was looking
    assert now != seen
    raises(CC.approve, plan, ["AO3401A"], ANN, digests={"AO3401A": seen[:12]}, contains="not the one you reviewed")
    assert LG.Ledger(lib).head("AO3401A")["revision"] == 1 and pins(plan, lib) == {"1": "G", "2": "S", "3": "D"}
    rc, out = run_cli("approve", lib, "--part", "AO3401A", "--user", ANN)
    assert rc == 2 and "--digest is required" in out, out
    rc, out = run_cli("approve", lib, "--part", "AO3401A", "--digest", now[:4], "--user", ANN)
    assert rc == 2 and "at least 6" in out, out
    rc, out = run_cli("approve", lib, "--part", "AO3401A", "--digest", now[:8], "--user", ANN, "-m", "D-S-G checked")
    assert rc == 0 and "-> r2" in out, out
    head = LG.Ledger(lib).head("AO3401A")
    assert head["decision"] == "approved" and pins(plan, lib) == {"1": "D", "2": "S", "3": "G"}
    approve = [e for e in LG.Ledger(lib).history("AO3401A") if e["event"] == "approve"][-1]
    assert approve["review"] == now and approve["digest"] == head["digest"], approve


def test_status_json_and_the_web_card_carry_the_digest():
    plan, lib = demo()
    d = held_hand_edit(plan, lib, SGD)
    rc, out = run_cli("status", lib, "--json")
    assert rc == 1 and json.loads(out)["held"][0]["digest"] == d, out
    assert item(items_of(plan), "AO3401A")["change"]["digest"] == d
    from ecadlib import partview as PV
    card = next(p for p in PV.cards(lib, items_of(plan), pictures=False)["parts"] if p["part"] == "AO3401A")
    assert card["change"]["digest"] == d, card["change"]


# ---------------------------------------------------------------- H5: the two-person rule counts people
def test_one_person_under_two_spellings_is_one_approver():
    """H5 (s_2p): "Ann Lee <ann@x>" then "Ann Lee", "Ann Lee" then "ann  lee", ann@ then ann+2@ were two people."""
    for a, b in [("Ann Lee <ann@example.com>", "Ann Lee"), ("Ann Lee", "ann  lee"), ("ann@example.com", "Ann <ann+2@example.com>"),
                 ("ANN LEE <Ann@Example.com>", "ann@example.com"), ("Ann\xa0Lee", "Ann Lee"), ("Ann Lee (ann@example.com)", "ann@example.com")]:
        assert same_person(a, b) and same_person(b, a), (a, b)
    for a, b in [(ANN, BOB), ("Ann Lee", "Ann Leeds"), ("ann@example.com", "anne@example.com"), ("", "")]:
        assert not same_person(a, b), (a, b)
    plan, lib = demo()
    (lib / "ecadlib.toml").write_text("[change]\napprovers = 2\n", encoding="utf-8")
    d = held_hand_edit(plan, lib, SGD)
    [r] = CC.approve(plan, ["AO3401A"], ANN, digests={"AO3401A": d})
    assert r["result"] == "approval recorded" and r["approvals"] == 1 and r["needed"] == 2, r
    for other in ("Ann Lee", "ann  lee", "Ann Lee <ann+2@example.com>", "ANN@example.com"):
        raises(CC.approve, plan, ["AO3401A"], other, digests={"AO3401A": d}, contains="approved this change already")
    assert LG.Ledger(lib).head("AO3401A")["revision"] == 1
    [r] = CC.approve(plan, ["AO3401A"], BOB, digests={"AO3401A": d})
    assert r["result"] == "released" and r["revision"] == 2, r


def test_a_group_counts_only_approvals_of_its_current_proposals():
    """Parts sharing a file are approved together; when one of their proposals changes, an approval of the old group
    no longer counts for it (before, only the first part's approvals were counted)."""
    plan, lib = demo()
    (lib / "ecadlib.toml").write_text("[change]\napprovers = 2\n", encoding="utf-8")
    items = items_of(plan)
    f = lib / item(items, "RES-0603-10k")["footprint"]["file"]
    km = lib / item(items, "RES-0603-10k")["footprint"]["kicad_mod"]
    text = SP.build("R_0603_1608Metric", "C")[0]             # the denser land pattern, both tools' files

    def rewrite():
        AL.write_pcblib(f, [AL.footprint_from_kicad(text)])
        km.write_text(text, encoding="utf-8")
    rewrite()
    CG.publish_guard(plan, items, "kicad")
    lg = LG.Ledger(lib)
    g1 = CG.review_digest(lg, "RES-0603-10k")
    assert len(CG.proposal_group(lg, "RES-0603-10k")) == 2 and g1 == CG.review_digest(lg, "RES-0603-4.7k")
    CC.approve(plan, ["RES-0603-10k"], ANN, digests={"RES-0603-10k": g1})
    items = items_of(plan)                    # the same footprint again, and the 4.7k gets a new description
    rewrite()
    item(items, "RES-0603-4.7k")["description"] = "Resistor 4.7k 1% 0603 (new)"
    CG.publish_guard(plan, items, "kicad")
    g2 = CG.review_digest(LG.Ledger(lib), "RES-0603-10k")
    assert g2 != g1
    rows = CC.approve(plan, ["RES-0603-10k"], BOB, digests={"RES-0603-10k": g2})
    assert all(r["result"] == "approval recorded" for r in rows) and LG.Ledger(lib).head("RES-0603-10k")["revision"] == 1, rows
    rows = CC.approve(plan, ["RES-0603-4.7k"], ANN, digests={"RES-0603-4.7k": g2})
    assert {r["result"] for r in rows} == {"released"}, rows


# ---------------------------------------------------------------- H6: a revert is a release
def test_revert_under_the_two_person_rule_waits_for_a_second_person():
    """H6 (s_revert2): with approvers = 2 one person reverted r2 to r1 alone."""
    plan, lib = demo()
    (lib / "ecadlib.toml").write_text("[change]\napprovers = 2\n", encoding="utf-8")
    d = held_hand_edit(plan, lib, SGD)
    CC.approve(plan, ["AO3401A"], ANN, digests={"AO3401A": d})
    CC.approve(plan, ["AO3401A"], BOB, digests={"AO3401A": d})
    assert pins(plan, lib) == {"1": "S", "2": "G", "3": "D"}
    [r] = CC.revert(plan, "AO3401A@1", "Mallory <m@example.com>", "undo")
    assert r["result"] == "approval recorded" and r["needed"] == 2, r
    lg = LG.Ledger(lib)
    assert lg.head("AO3401A")["revision"] == 2 and pins(plan, lib) == {"1": "S", "2": "G", "3": "D"}, "nothing released yet"
    prop = lg.proposal("AO3401A")
    assert prop["writer"] == "revert" and prop["revert_to"] == 1 and [a["who"] for a in prop["approvals"]] == ["Mallory <m@example.com>"]
    rc, out = run_cli("show", lib, "AO3401A")
    assert "held revert to r1" in out, out
    d = CG.review_digest(lg, "AO3401A")
    raises(CC.approve, plan, ["AO3401A"], "m@example.com", digests={"AO3401A": d}, contains="already")
    [r] = CC.approve(plan, ["AO3401A"], ANN, digests={"AO3401A": d})
    head = LG.Ledger(lib).head("AO3401A")
    assert r["result"] == "released" and head["revision"] == 3 and head["event"] == "revert", head
    assert head["approved_by"] == ["Mallory <m@example.com>", ANN] and pins(plan, lib) == {"1": "G", "2": "S", "3": "D"}


def test_revert_with_one_approver_still_releases_at_once():
    plan, lib = demo()
    d = held_hand_edit(plan, lib, SGD)
    CC.approve(plan, ["AO3401A"], ANN, digests={"AO3401A": d})
    [r] = CC.revert(plan, "AO3401A@1", BOB)
    assert r["result"] == "reverted" and r["revision"] == 3 and LG.Ledger(lib).head("AO3401A")["event"] == "revert"


# ---------------------------------------------------------------- M4: a person's choice goes through the release check
def _strip_steps(lib):
    """The ledger as an ecadlib before 3D snapshots wrote it (heads without "steps")."""
    p = lib / LG.LEDGER
    lines = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        e = json.loads(raw)
        for r in (e.get("files") or {}).values():
            r.pop("steps", None)
        lines.append(json.dumps(e, sort_keys=True, ensure_ascii=False))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _model_files(lib):
    return {str(p.relative_to(lib)): p.read_bytes() for p in (lib / "models").rglob("*") if p.is_file()}


def test_a_chosen_footprint_that_fails_the_release_check_is_not_released():
    """M4 (s_resolve): `resolve choose` (approvers = 1) recorded `release r2 approved` for a SOT-23-5 footprint on a
    3-pin symbol, which fails the release check; and a held choice left a changed .step on disk."""
    from ecadlib import resolve as R
    for approvers in (1, 2):
        plan, lib = demo()
        if approvers == 2:
            (lib / "ecadlib.toml").write_text("[change]\napprovers = 2\n", encoding="utf-8")
        _strip_steps(lib)
        before = _model_files(lib)
        src = R.Sources(cache=Path(tempfile.mkdtemp()), offline=True)
        res = R.choose(plan, "AO3401A", "footprint", "spec:SOT-23-5", src=src, user=ANN)
        lg = LG.Ledger(lib)
        assert lg.head("AO3401A")["revision"] == 1, (approvers, res["change"])
        assert {k for k in before if before[k] != _model_files(lib).get(k)} == set(), approvers
        prop = lg.proposal("AO3401A")
        if approvers == 1:
            assert prop["approvals"] == [] and "release check" in prop["refused"]["reason"], prop.get("refused")
            assert any("not applied" in n for n in res["notes"]), res["notes"]
            assert [e["event"] for e in lg.history("AO3401A")][-2:] == ["hold", "refused"]
        else:
            assert [a["who"] for a in prop["approvals"]] == [ANN]
            d = CG.review_digest(lg, "AO3401A")
            raises(CC.approve, plan, ["AO3401A"], BOB, digests={"AO3401A": d}, contains="fails the release check")
            assert {k for k in before if before[k] != _model_files(lib).get(k)} == set(), "the .step went back too"


# ---------------------------------------------------------------- M3: the library lock
def test_the_library_lock_is_reentrant_and_keeps_other_threads_out():
    d = Path(tempfile.mkdtemp())
    got = {}
    with LG.locked(d):
        with LG.locked(d):                     # the same thread again: fine
            pass
        t = threading.Thread(target=lambda: got.update(e=raises(LG.lock, d, wait=0.2, exc=LG.LedgerError,
                                                                 contains="another ecadlib run")))
        t.start()
        t.join()
    assert got["e"].hint and "ECADLIB_LOCK_WAIT" in got["e"].hint
    key = LG.lock(d, wait=1)                   # free again
    LG.unlock(key)


def test_parallel_publishers_record_one_revision():
    """M3 (s_conc): four kicad-exports at once wrote four `release r2` lines for one cosmetic change."""
    plan, lib = demo()
    items = items_of(plan)
    item(items, "KT-0603R")["description"] = "LED red 0603 v2"
    write_symbol(lib, item(items, "AO3401A"), SGD)
    CG.write_plan(plan, items)
    code = ("import json, sys; from ecadlib import change_gate as CG; p = sys.argv[1]; "
            "CG.publish_guard(p, json.load(open(p, encoding='utf-8')), 'kicad')")
    ps = [subprocess.Popen([sys.executable, "-c", code, str(plan)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
          for _ in range(4)]
    for p in ps:
        _, err = p.communicate(timeout=300)
        assert p.returncode == 0, err.decode(errors="replace")
    lg = LG.Ledger(lib)
    revs = [(e["part"], e["revision"]) for e in lg.lines() if e["event"] in LG.RELEASES]
    assert len(revs) == len(set(revs)), revs
    assert [e["revision"] for e in lg.releases("KT-0603R")] == [1, 2]
    assert sum(e["event"] == "hold" for e in lg.history("AO3401A")) == 1 and lg.head("AO3401A")["revision"] == 1


# ---------------------------------------------------------------- H8: a lost ledger fails closed
def test_a_deleted_or_cut_ledger_is_never_silently_adopted():
    """H8 (s_ledger L1, L2, L5): a deleted, emptied or cut ledger made the disk (with a pin swap) revision 1."""
    for how in ("deleted", "emptied", "cut"):
        plan, lib = demo()
        p = lib / LG.LEDGER
        if how == "deleted":
            p.unlink()
        elif how == "emptied":
            p.write_text("", encoding="utf-8")
        else:
            p.write_text("".join(l + "\n" for l in p.read_text(encoding="utf-8").splitlines() if '"AO3401A"' not in l),
                         encoding="utf-8")
        items = items_of(plan)
        write_symbol(lib, item(items, "AO3401A"), SGD)
        e = raises(CG.publish_guard, plan, items, "kicad", exc=LG.LedgerError, contains="AO3401A")
        assert "part adopt" in e.hint and "git checkout" in e.hint, e.hint
        assert not LG.Ledger(lib).head("AO3401A"), how
        f = cliutil.friendly(e)
        assert f and f[2] == 2 and "nothing was changed" in f[0], f
    # recovery: a person checks the files and adopts them
    rc, out = run_cli("adopt", lib, "--part", "AO3401A", "--user", ANN)
    assert rc == 2 and "needs a reason" in out, out
    rc, out = run_cli("adopt", lib, "--part", "KT-0603R", "-m", "x", "--user", ANN)
    assert rc == 2 and "nothing to adopt" in out, out
    rc, out = run_cli("adopt", lib, "--all", "-m", "checked the pinout against the datasheet", "--user", ANN)
    assert rc == 2 and "pins swapped" in out and "--accept-changes" in out, out     # R2-3: the swap is shown
    rc, out = run_cli("adopt", lib, "--all", "-m", "checked the pinout against the datasheet", "--user", ANN,
                      "--accept-changes")
    assert rc == 0 and "AO3401A" in out and "signed as Ann Lee" in out, out    # R3-1: the stored version, the swap held
    head = LG.Ledger(lib).head("AO3401A")
    assert head["decision"] == "adopted" and head["by"] == ANN and "datasheet" in head["reason"], head
    status, _ = CG.publish_guard(plan, items_of(plan), "kicad")
    assert status["AO3401A"] is None


def test_a_new_library_and_new_parts_are_still_baselined():
    plan, lib = demo()
    items = items_of(plan)
    new = json.loads(json.dumps(item(items, "KT-0603R")))
    new["name"] = "KT-0603G"
    for prm in new["parameters"]:
        if prm["name"] == "Manufacturer Part Number":
            prm["value"] = "KT-0603G"
    items.append(new)
    status, _ = CG.publish_guard(plan, items, "kicad")
    assert status["KT-0603G"] is None and LG.Ledger(lib).head("KT-0603G")["decision"] == "baseline"


def test_a_broken_ledger_line_is_a_plain_error():
    """L3: a corrupt ledger line was reported as an internal error."""
    plan, lib = demo()
    p = lib / LG.LEDGER
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1][:40]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    e = raises(LG.Ledger(lib).lines, exc=LG.LedgerError, contains=":2: a broken ledger line")
    msg, hint, code = cliutil.friendly(e)
    assert code == 2 and "git diff" in hint, hint


# ---------------------------------------------------------------- C5: a renamed part with a released MPN
def test_a_renamed_part_with_a_released_mpn_is_compared_and_held():
    """C5 (s_librarian): a BOM footprint change 0603 -> 0402 renames RES-0603-10k to RES-0402-10k with the same MPN;
    the new part was released at once as r1 and the old one left without a word."""
    plan, lib = demo()
    old = items_of(plan)
    items = json.loads(json.dumps(old))
    it = item(items, "RES-0603-10k")
    it["name"] = "RES-0402-10k"
    to_0402(lib, it)
    g = CG.Guard(lib, "librarian")
    g.begin(old)
    g.settle(items)
    rows = g.baseline(items)
    assert g.retire(["RES-0603-10k"], "no longer in the plan (bom.csv)") == ["RES-0603-10k"]
    assert g.retire(["RES-0603-10k"]) == [], "retired once"
    [r] = [r for r in rows if r["part"] == "RES-0402-10k"]
    assert r["action"] == "hold" and r["twin"] == {"part": "RES-0603-10k", "revision": 1}, r
    assert "R_0603_1608Metric to R_0402_1005Metric" in r["summary"], r
    assert it["status"] == "held" and it["change"]["twin"]["part"] == "RES-0603-10k", it.get("change")
    CG.write_plan(plan, items)
    lg = LG.Ledger(lib)
    assert lg.head("RES-0402-10k") is None and lg.history("RES-0603-10k")[-1]["event"] == "retired"
    status, _ = CG.publish_guard(plan, items_of(plan), "kicad")     # still held, not a "lost history"
    assert status["RES-0402-10k"].startswith("not released")
    rc, out = run_cli("show", lib, "RES-0402-10k")
    assert "MPN of released RES-0603-10k r1" in out, out
    d = CG.review_digest(LG.Ledger(lib), "RES-0402-10k")
    [r] = CC.approve(plan, ["RES-0402-10k"], ANN, digests={"RES-0402-10k": d}, comment="0402 on the new board")
    head = LG.Ledger(lib).head("RES-0402-10k")
    assert r["result"] == "released" and head["revision"] == 1 and head["decision"] == "approved", head
    assert "MPN of released RES-0603-10k" in head["reason"] and item(items_of(plan), "RES-0402-10k")["status"] == "ready"


def test_a_rename_without_a_change_is_released_as_usual():
    plan, lib = demo()
    old = items_of(plan)
    items = json.loads(json.dumps(old))
    item(items, "RES-0603-10k")["name"] = "R-10K"
    g = CG.Guard(lib, "librarian")
    g.begin(old)
    rows = g.baseline(items)
    assert [(r["part"], r["action"]) for r in rows] == [("R-10K", "baseline")], rows


# ---------------------------------------------------------------- N3: approving over a newer hand edit
def test_an_approval_over_a_newer_edit_warns_and_keeps_it():
    plan, lib = demo()
    d = held_hand_edit(plan, lib, SGD)
    write_symbol(lib, item(items_of(plan), "AO3401A"), DSG)     # edited again by hand, no writer ran
    edited = (lib / item(items_of(plan), "AO3401A")["symbol"]["file"]).read_bytes()
    [r] = CC.approve(plan, ["AO3401A"], ANN, digests={"AO3401A": d})
    assert r["result"] == "released" and "newer edit" in r["warning"], r
    lg = LG.Ledger(lib)
    over = [e for e in lg.history("AO3401A") if e["event"] == "overwritten"]
    assert len(over) == 1 and lg.get_bytes(over[0]["files"]["symbol"]["sha256"]) == edited
    assert pins(plan, lib) == {"1": "S", "2": "G", "3": "D"}


# ---------------------------------------------------------------- N2: Altium project check
class FakeNexar:
    def __init__(self, latest):
        self.latest = latest

    def find(self, ws, name):
        return {"id": name, "name": name, "revision": {"revisionId": self.latest[name]}} if name in self.latest else None


def test_altium_check_reads_pulled_revisions_and_fails_on_unlisted_changes():
    """N2 (critic g_a365pull + g_altproj): changes ecadlib recorded from `a365 pull` were "not listed" and the check
    passed (exit 0); TPS7A02-33 was looked up as TPS7A02."""
    from ecadlib import altium_project as AP
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "RES-0603-10k")
    lg = LG.Ledger(lib)
    lg.append({"event": "publish", "part": "RES-0603-10k", "target": "altium365:https://ws", "revision": 1,
               "detail": {"action": "release", "a365_revision": "{AAAA-1}"}})
    it["a365"] = {"revision": "AAAA-2"}                 # a365 pull brings the workspace's revision 2: 0402 now
    to_0402(lib, it)
    # `a365 pull` is parked (its writer left WRITERS), but ledgers it wrote keep these lines: record one the way it did
    with mock.patch.dict(CG.WRITERS, {"a365-pull": {"mode": "observe", "module": "ecadlib.a365_sync", "what": "a365 pull"}}):
        g = CG.Guard(lib, "a365-pull")
        [row] = g.settle(items)
    assert row["revision"] == 2 and LG.Ledger(lib).head("RES-0603-10k")["a365_revision"] == "AAAA-2", row
    comps = [{"designator": "R1", "design_item_id": "RES-0603-10k", "vault_guid": "v", "item_guid": "i", "revision_guid": "{AAAA-1}"},
             {"designator": "U5", "design_item_id": "TPS7A02-33", "vault_guid": "v", "item_guid": "k", "revision_guid": "X1"},
             {"designator": "C9", "design_item_id": "CMP-003-000000-1", "vault_guid": "v", "item_guid": "c", "revision_guid": "C1"}]
    nx = FakeNexar({"RES-0603-10k": "AAAA-2", "TPS7A02": "T9", "TPS7A02-33": "X2", "CMP-003-000000": "C1"})
    with mock.patch.object(AP, "load_project", lambda p, rules=None: {"path": p, "components": comps}):
        res = PJ.check_altium("board.PrjPcb", nx, "https://ws", lib)
    rows = {r["id"]: r for r in res["rows"]}
    assert rows["RES-0603-10k"]["why"] == "ledger r1 -> r2" and rows["RES-0603-10k"]["action"] == "hold", rows["RES-0603-10k"]
    assert rows["TPS7A02-33"]["state"] == "out of date" and rows["TPS7A02-33"]["latest"] == "x2", rows
    assert rows["TPS7A02-33"]["action"] == "hold" and "not listed" in rows["TPS7A02-33"]["why"]
    assert rows["CMP-003-000000"]["state"] == "same" and res["held"] == 2, rows
    (lib / "ecadlib.toml").write_text('[change]\nunlisted = "warn"\n', encoding="utf-8")
    with mock.patch.object(AP, "load_project", lambda p, rules=None: {"path": p, "components": comps}):
        res = PJ.check_altium("board.PrjPcb", nx, "https://ws", lib)
    assert {r["id"]: r["action"] for r in res["rows"]}["TPS7A02-33"] == "warn" and res["held"] == 1
    assert PJ._base_id("TPS7A02-33") == "TPS7A02-33" and PJ._base_id("CMP-003-000000-1") == "CMP-003-000000"
    raises(CG.normalize_policy, {"unlisted": "maybe"}, exc=ValueError, contains="unlisted")


# ---------------------------------------------------------------- L2: a board without a schematic
def test_project_scan_reads_a_board_without_a_schematic():
    plan, lib = demo()
    proj = Path(tempfile.mkdtemp()) / "board"
    proj.mkdir()
    (proj / "board.kicad_pro").write_text("{}", encoding="utf-8")
    (proj / "board.kicad_pcb").write_text(
        '(kicad_pcb (version 20240108)\n'
        ' (footprint "Resistor_SMD:R_0603_1608Metric" (layer "F.Cu") (property "Reference" "R1")'
        ' (property "MPN" "0603WAF1002T5E"))\n'
        ' (footprint "Resistor_SMD:R_0603_1608Metric" (layer "B.Cu") (property "Reference" "R7")'
        ' (property "Manufacturer Part Number" "0603WAF1002T5E"))\n'
        ' (footprint "MountingHole:MH" (layer "F.Cu") (property "Reference" "H1")))\n', encoding="utf-8")
    res = PJ.scan(proj / "board.kicad_pro", lib)
    assert res["parts"]["RES-0603-10k"] == {"revision": 1, "designators": ["R1", "R7"]}, res


if __name__ == "__main__":
    from tests.run_all import main
    sys.exit(main(["test_change_control_workflow"]))
