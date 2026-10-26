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
