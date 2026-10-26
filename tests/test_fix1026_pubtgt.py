"""Fixes 2026-10-26 (pubtgt): publish targets shared between plan folders (a DbLib folder, an Altium 365 workspace).
Change control is per plan folder, so a target two plans publish to must never let one plan change what the other
released. Plain functions: python -m tests.run_all test_fix1026_pubtgt. Altium 365 / Nexar is always a fake here."""
import json
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------- R-FL-1: one DbLib folder, two plan folders
def _plan_folder(d, items, land=None):
    """A plan folder d with Resistor.SchLib and R_0603_1608Metric.PcbLib (land: changes to the 0603 spec, as a newer
    upstream footprint would have), the items released anyway; -> plan.json."""
    from ecadlib import altium_lib as AL
    from tests.test_dblib_sync import _plan
    (d / "models" / "SchLib").mkdir(parents=True)
    (d / "models" / "PcbLib").mkdir(parents=True)
    AL.write_schlib(d / "models/SchLib/Resistor.SchLib", [AL.resistor_symbol()])
    spec = json.loads((ROOT / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    spec.update(land or {})
    AL.write_pcblib(d / "models/PcbLib/R_0603_1608Metric.PcbLib", [AL.footprint_from_spec(spec)[0]])
    return _plan(d, items)


def _res(name, mpn, maker="Yageo", value=None):
    from tests.test_dblib_sync import _item
    return _item(name, "Resistors", "Resistor", "R_0603_1608Metric", Value=value or name.rsplit("-", 1)[-1],
                 Manufacturer=maker, Manufacturer_Part_Number=mpn)


def _rows(lib):
    from ecadlib import altium_dblib_sync as S
    return {r["Part Number"]: r for _, (_, rs) in S.read_tables(lib / "ecadlib.xlsx").items() for r in rs}


def _acts(out):
    return {r["name"]: r["action"] for r in out["results"] if r.get("kind") is None}


@lite
def test_dblib_second_plan_does_not_replace_a_footprint_another_plans_rows_use(tmp_path):
    """Board A and board B (two plan folders) sync into one DbLib folder. B's build of R_0603_1608Metric has bigger
    pads: replacing the file gave A's released RES-0603-10k B's land with no hold (A's ledger still said r1 went
    there). The file another plan's rows use is not replaced: B's part is an error naming the conflict."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")], land={"terminal_length": {"min": 0.3, "max": 0.6}})
    assert _acts(S.sync(a, lib)) == {"RES-0603-10k": "added"}
    before = (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes()
    out = S.sync(b, lib)
    res = next(r for r in out["results"] if r["name"] == "RES-0603-1k")
    assert res["action"] == "error" and "RES-0603-10k" in res["detail"] and "another plan" in res["detail"], res
    assert (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes() == before
    assert set(_rows(lib)) == {"RES-0603-10k"}
    # B with the same land as A's (same bytes): shared, both rows there
    b2 = _plan_folder(tmp_path / "B2", [_res("RES-0603-1k", "RC0603FR-071KL")])
    out = S.sync(b2, lib)
    assert _acts(out)["RES-0603-1k"] == "added", out["results"]
    assert set(_rows(lib)) == {"RES-0603-10k", "RES-0603-1k"}
    # now A's land changes (approved in A): B2's released RES-0603-1k uses the file, so A cannot replace it either
    a2 = _plan_folder(tmp_path / "A2", [_res("RES-0603-10k", "RC0603FR-0710KL")], land={"terminal_length": {"min": 0.3, "max": 0.6}})
    import shutil
    shutil.rmtree(tmp_path / "A"); shutil.move(tmp_path / "A2", tmp_path / "A")
    res = next(r for r in S.sync(a, lib)["results"] if r["name"] == "RES-0603-10k")
    assert res["action"] == "error" and "RES-0603-1k" in res["detail"], res
    assert (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes() == before


@lite
def test_dblib_one_plan_still_updates_its_own_shared_footprint(tmp_path):
    """No false refusal: within one plan folder, an approved land change rewrites the footprint every row of that plan
    uses, also a row of a part the plan has dropped since (kept, not pruned)."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL"), _res("RES-0603-1k", "RC0603FR-071KL")])
    S.sync(a, lib)
    import shutil
    shutil.rmtree(tmp_path / "A")
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")], land={"terminal_length": {"min": 0.3, "max": 0.6}})
    before = (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes()
    out = S.sync(a, lib)
    assert _acts(out)["RES-0603-10k"] == "updated", out["results"]
    assert (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes() != before


@lite
def test_dblib_same_part_name_with_another_mpn_from_another_plan_is_held(tmp_path):
    """A third BOM gives the generated name RES-0603-1k to a Vishay part: its sync rewrote the Yageo row of the plan
    that released it (Manufacturer, Manufacturer Part Number) in place. An identity change of another plan's row is
    not applied; the same plan's own approved change still is."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")])
    S.sync(b, lib)
    c = _plan_folder(tmp_path / "C", [_res("RES-0603-1k", "CRCW06031K00FKEA", maker="Vishay")])
    out = S.sync(c, lib)
    res = next(r for r in out["results"] if r["name"] == "RES-0603-1k")
    assert res["action"] == "held" and "identity change" in res["detail"], res
    row = _rows(lib)["RES-0603-1k"]
    assert (row["Manufacturer"], row["Manufacturer Part Number"]) == ("Yageo", "RC0603FR-071KL"), row
    # B's own correction (its change control signed it off): applied
    import shutil
    shutil.rmtree(tmp_path / "B")
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-07L1KL")])
    assert _acts(S.sync(b, lib))["RES-0603-1k"] == "updated"
    assert _rows(lib)["RES-0603-1k"]["Manufacturer Part Number"] == "RC0603FR-07L1KL"


@lite
def test_dblib_prune_keeps_rows_another_plan_wrote(tmp_path):
    """--prune from plan B deleted plan A's rows (they are "not in B's plan"): only rows this plan wrote are pruned."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")])
    S.sync(a, lib)
    S.sync(b, lib)
    out = S.sync(b, lib, prune=True)
    assert set(_rows(lib)) == {"RES-0603-10k", "RES-0603-1k"}, out["results"]
    r = next(r for r in out["results"] if r["name"] == "RES-0603-10k")
    assert r["action"] == "not_in_plan" and "another plan" in r["detail"], r
