"""2026-10-26 fixes "gate2" (reviews/2026-10-26_review_196/gate2.md): an edit saved while a writer runs is not the
writer's own rewrite (R-G2-1); a pad-to-pad gap cut below its margin is a major change (R-G2-2); a cut or merged ledger
fails closed (R-G2-3); CRLF line ends alone change nothing (R-G2-4); a recipe's STEP body rotation / dz / height is
checked (R-G2-5); pad-level paste / clearance / thermal settings of a .kicad_mod are fingerprinted (R-G2-6); part revert
checks the policy's users (R-G2-7).
Offline.  python3 -m pytest -q tests/test_fix1026_gate2.py   (or python -m tests.run_all test_fix1026_gate2)"""
import contextlib, io, json, re
from pathlib import Path

from tests.fixtures_lite import lite
from tests.test_fix1026_changes2 import _demo, _ledger
from tests.test_fix1026_changes3 import CAP, _head_recipe, _moved_pad, _recipe

ANN = "Ann Lee <ann@example.com>"


def _pcblib(lib):
    return lib / "models/PcbLib/C_0402_1005Metric.PcbLib"


def _silk_resave(lib, into=None):
    """The C_0402 .PcbLib with its silkscreen 0.12 -> 0.15 mm (cosmetic) and its recipe, written by altium_lib into
    the library (into=None) or into another folder (a person's save, copied in later as plain bytes)."""
    from ecadlib import altium_lib as AL, stdparts as SP
    txt = SP.build("C_0402_1005Metric")[0]
    txt = re.sub(r"\(stroke \(width 0\.12\)", "(stroke (width 0.15)", re.sub(r"\(width 0\.12\)", "(width 0.15)", txt))
    dst = Path(into) / _pcblib(lib).name if into else _pcblib(lib)
    dst.parent.mkdir(parents=True, exist_ok=True)
    AL.write_pcblib(dst, [AL.footprint_from_kicad(txt)])
    return dst


# ------------------------------------------------------------------ R-G2-1
def _race(writer):
    """g7_rewrote_race.py: a writer begins, builds nothing for CAP; meanwhile a person saves a silkscreen edit of the
    .PcbLib and moves PAD 1 in its recipe; the writer settles. The moved pad must not be released."""
    from ecadlib import change_gate as CG, ledger as LG
    w, lib = _demo()
    items = json.loads((lib / "plan.json").read_text(encoding="utf-8"))
    released = _recipe(lib).read_bytes()
    saved = _silk_resave(lib, into=w / "altium")               # what the person's Altium will save
    g = CG.Guard(lib, writer)
    g.begin(items)
    _pcblib(lib).write_bytes(saved.read_bytes())               # an edit saved while the writer works (another program)
    _recipe(lib).write_bytes(Path(str(saved) + ".ecadbuild").read_bytes())
    edited, moved = _moved_pad(lib)                            # ... with PAD 1 moved in the recipe
    _recipe(lib).write_text(edited, encoding="utf-8")
    rows = g.settle(items)
    g.close()
    head = LG.Ledger(lib).head(CAP)
    assert moved not in _head_recipe(lib), ("the moved pad was released", rows, head)
    assert head["revision"] == 1, head
    assert _recipe(lib).read_bytes() == released               # the released recipe is back
    assert [r for r in rows if r["part"] == CAP and r["action"] == "hold"], rows


def test_gate2_rg21_an_edit_saved_while_the_librarian_runs_is_not_its_own_rewrite():
    _race("librarian")


def test_gate2_rg21_an_edit_saved_while_resolve_runs_is_not_its_own_rewrite():
    _race("resolve")


def test_gate2_rg21_the_writers_own_rebuild_still_releases():
    """No false hold: the writer itself rewrites the .PcbLib (through altium_lib, which writes the matching recipe):
    released at the policy's action, no `overwritten` line."""
    from ecadlib import change_gate as CG, ledger as LG
    w, lib = _demo()
    items = json.loads((lib / "plan.json").read_text(encoding="utf-8"))
    g = CG.Guard(lib, "librarian")
    g.begin(items)
    _silk_resave(lib)                                          # the writer's own write this time
    mine = _recipe(lib).read_bytes()
    rows = g.settle(items)
    g.close()
    assert LG.Ledger(lib).head(CAP)["revision"] == 2, rows
    assert _recipe(lib).read_bytes() == mine
    assert not [e for e in _ledger(lib) if e.get("event") == "overwritten"]
