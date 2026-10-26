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


# ---------------------------------------------------------------- R-FL-4: a value written another way keeps the name
def _bom(*rows):
    return [dict(zip(("designator", "value", "footprint", "manufacturer", "mpn", "description"), r)) for r in rows]


R10K = ("R1,R2", "10k", "R_0603_1608Metric", "Yageo", "RC0603FR-0710KL", "Resistor 10k 1% 0603")
C100N = ("C1", "100nF", "C_0603_1608Metric", "Murata", "GRM188R71C104KA01D", "Capacitor 100nF X7R 0603")


def _plan_names(lines, prior_items=None):
    from ecadlib import altium365 as A, altium_export as AX
    items = A.plan(lines, prior=AX.prior_names(prior_items) if prior_items is not None else None)
    return {i["name"]: next((p["value"] for p in i["parameters"] if p["name"] == "Manufacturer Part Number"), "")
            for i in items}, items


def test_a_value_written_another_way_in_a_new_bom_keeps_the_released_name():
    first, items = _plan_names(_bom(R10K, C100N))
    assert sorted(first) == ["CAP-0603-100nF", "RES-0603-10k"], first
    again, _ = _plan_names(_bom(("R1,R2", "10K0") + R10K[2:], ("C1", "0.1uF") + C100N[2:]), items)
    assert again == first, again                          # same MPN, maker and land: the released names stay
    for v in ("10.0k", "10K", "10000"):                   # 10K: never a name that differs only in case either
        respelt, _ = _plan_names(_bom(("R1,R2", v) + R10K[2:]), items)
        assert respelt == {"RES-0603-10k": "RC0603FR-0710KL"}, (v, respelt)


def test_a_real_change_or_another_part_still_follows_the_naming_rules():
    _, items = _plan_names(_bom(R10K))
    other_value, _ = _plan_names(_bom(("R1,R2", "1k") + R10K[2:]), items)          # the value itself changed
    assert other_value == {"RES-0603-1k": "RC0603FR-0710KL"}, other_value
    other_land, _ = _plan_names(_bom(("R1,R2", "10K0", "R_0402_1005Metric") + R10K[3:]), items)
    assert other_land == {"RES-0402-10K0": "RC0603FR-0710KL"}, other_land
    other_maker, _ = _plan_names(_bom(("R1,R2", "10K0", "R_0603_1608Metric", "Vishay") + R10K[4:]), items)
    assert other_maker == {"RES-0603-10K0": "RC0603FR-0710KL"}, other_maker
    same, _ = _plan_names(_bom(R10K), items)                                        # unchanged: as before
    assert same == {"RES-0603-10k": "RC0603FR-0710KL"}, same


def test_a_respelt_line_never_takes_a_name_another_line_of_the_bom_holds():
    """The released RES-0603-10k is respelt 10K0 while a new MPN-less 10k 0603 line comes first: the released part
    keeps its name and the new line takes the suffixed one (SYS-1), never two parts under one name."""
    _, items = _plan_names(_bom(R10K))
    got, _ = _plan_names(_bom(("R7", "10k", "R_0603_1608Metric", "", "", "Resistor 10k 0603"),
                              ("R1,R2", "10K0") + R10K[2:]), items)
    assert got.get("RES-0603-10k") == "RC0603FR-0710KL" and len(got) == 2, got
