"""Fixes from review 196 (flows): R-FL-3 (kicad-pcm and export-parts keep a released part that is held again, as every
other output does) and R-FL-4 (a value written another way in a new BOM revision keeps the part's released name)."""
import json, zipfile
from pathlib import Path

from tests.fixtures_lite import lite
from tests.test_release_check import make


def _released_pair(d):
    """PART1 and PART2 pass the check and are released (ledger r1); plan.json lists both as ready."""
    from ecadlib import change_gate as CG, fingerprint as FP, release_check as RC
    ok, two = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
    RC.apply([ok, two], d)
    g = CG.Guard(d, "librarian")
    g.hold_lock()
    try:
        for it in (ok, two):
            g._release(it, FP.of_item(it, d), [], {"action": "pass", "why": []})
    finally:
        g.close()
    (d / "plan.json").write_text(json.dumps([ok, two]))
    return ok, two


def _hold_again(d, ok, two, status="held"):
    """PART2 held again with its files and fields as released (release-check --unallow, a stale sign-off)."""
    two = dict(two, status=status, hold_reasons=["sign-off: needs a person's approval"])
    (d / "plan.json").write_text(json.dumps([ok, two]))


def _pcm_symbols(rep):
    with zipfile.ZipFile(rep["zip"]) as z:
        text = z.read(next(n for n in z.namelist() if n.endswith(".kicad_sym"))).decode()
        return sorted(n for n in ("PART1", "PART2") if f'(symbol "{n}"' in text), \
            sorted(n for n in z.namelist() if n.endswith(".kicad_mod"))


@lite
def test_kicad_pcm_keeps_a_released_part_that_is_held_again(tmp_path):
    from ecadlib import kicad_pcm as P
    ok, two = _released_pair(tmp_path)
    quiet = lambda *a, **k: None
    kw = dict(name="mylib", offline=True, cache=str(tmp_path / "cache"), log=quiet)
    first = P.build(tmp_path / "plan.json", tmp_path / "pcm", **kw)
    assert _pcm_symbols(first)[0] == ["PART1", "PART2"]
    _hold_again(tmp_path, ok, two)
    again = P.build(tmp_path / "plan.json", tmp_path / "pcm", **kw)
    assert _pcm_symbols(again) == _pcm_symbols(first), _pcm_symbols(again)   # an update does not take it from users
    assert again["version"] == first["version"]                              # nothing changed: no new package
    from ecadlib import ledger as LG                                          # the publish lines name a folder that stays
    targets = {e.get("target") for e in LG.Ledger(tmp_path).lines() if e.get("event") == "publish"}
    assert targets and all((tmp_path / str(t).split(":", 1)[1]).is_dir() for t in targets if str(t).startswith("kicad")), targets


@lite
def test_kicad_pcm_still_leaves_out_a_held_part_never_released(tmp_path):
    from ecadlib import kicad_pcm as P
    from tests.test_release_gate import _one_emptied
    plan = _one_emptied(tmp_path)                       # no ledger: PART2 was never released
    rep = P.build(plan, tmp_path / "pcm", name="mylib", offline=True, cache=str(tmp_path / "cache"), log=lambda *a, **k: None)
    assert _pcm_symbols(rep)[0] == ["PART1"]


@lite
def test_export_parts_run_again_keeps_a_released_part_held_again(tmp_path):
    from ecadlib import parts_export as PE
    ok, two = _released_pair(tmp_path)
    plan, out = tmp_path / "plan.json", tmp_path / "parts.json"
    assert PE.main([str(plan), "-o", str(out)]) == 0
    _hold_again(tmp_path, ok, two)
    assert PE.main([str(plan), "-o", str(out)]) == 0
    parts = {r["Part ID"]: r for r in json.loads(out.read_text())["parts"]}
    assert sorted(parts) == ["PART1", "PART2"] and parts["PART2"]["Status"] == "ready", sorted(parts)


@lite
def test_export_parts_keeps_the_row_it_exported_of_a_part_held_again(tmp_path):
    from ecadlib import parts_export as PE
    ok, two = _released_pair(tmp_path)
    plan = tmp_path / "plan.json"
    for fmt in ("json", "csv", "xlsx"):
        out = tmp_path / f"parts.{fmt}"
        plan.write_text(json.dumps([ok, two]))
        assert PE.main([str(plan), "-o", str(out)]) == 0
        before = PE.previous_rows(out)
        assert sorted(before) == ["PART1", "PART2"], (fmt, sorted(before))
        for status in ("held", "awaiting_approval"):
            _hold_again(tmp_path, ok, two, status)
            said = []
            rows = {r["Part ID"]: r for r in PE.rows(plan, previous=PE.previous_rows(out), warn=said.append)}
            assert sorted(rows) == ["PART1", "PART2"], (fmt, status, sorted(rows))
            assert rows["PART2"]["Status"] == "ready" and rows["PART2"]["Description"] == "Test part", rows["PART2"]
            assert rows["PART2"]["KiCad Symbol"] == "ecadlib:PART2", rows["PART2"]
            assert any("PART2" in s and "r1" in s and "until it passes the check again" in s for s in said), said
            assert PE.main([str(plan), "-o", str(out)]) == 0                 # and on the next run, from its own file
            assert sorted(PE.previous_rows(out)) == ["PART1", "PART2"], (fmt, status)
    _hold_again(tmp_path, ok, two)
    assert sorted(r["Part ID"] for r in PE.rows(plan)) == ["PART1"]       # no table before: nothing to keep
    held = {r["Part ID"]: r for r in PE.rows(plan, include_held=True, previous=PE.previous_rows(out))}["PART2"]
    assert (held["Status"], held["Hold Reasons"]) == ("held", "sign-off: needs a person's approval")   # as before


@lite
def test_export_parts_does_not_keep_a_row_written_while_held_or_never_released(tmp_path):
    from ecadlib import parts_export as PE
    from tests.test_release_gate import _one_emptied
    plan = _one_emptied(tmp_path)                       # PART2 held, never released (no ledger)
    out = tmp_path / "parts.json"
    PE.write([dict(r, Status="ready") for r in PE.rows(plan, include_held=True)], out, "json")
    assert sorted(r["Part ID"] for r in PE.rows(plan, previous=PE.previous_rows(out))) == ["PART1"]
    ok, two = _released_pair(tmp_path / "rel")          # released, then exported while held (--include-held)
    _hold_again(tmp_path / "rel", ok, two)
    plan, out = tmp_path / "rel" / "plan.json", tmp_path / "rel" / "parts.json"
    assert PE.main([str(plan), "-o", str(out), "--include-held"]) == 0
    assert PE.previous_rows(out)["PART2"]["Status"] == "held"
    assert sorted(r["Part ID"] for r in PE.rows(plan, previous=PE.previous_rows(out))) == ["PART1"]
