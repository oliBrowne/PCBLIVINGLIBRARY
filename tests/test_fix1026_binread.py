"""2026-10-26 fixes (binread): Altium binary readers, footprint_check geometry, nexar sign-in, ecadlib status.
No network, no pytest: plain test functions (tests.run_all) with tests.fixtures_lite for tmp_path / monkeypatch."""
import json, math

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-BR-6: ecadlib status names a damaged plan
@lite
def test_status_names_a_plan_with_wrong_typed_fields(tmp_path):
    from ecadlib import ws_status as WS
    cases = {"approvals": {"name": "R1", "status": "ready", "approvals": 5},
             "change": {"name": "R1", "status": "ready", "change": "x"},
             "name": {"name": ["R1"], "status": "ready"},
             "hold_reasons": {"name": "R1", "status": "held", "hold_reasons": 3},
             "status": {"name": "R1", "status": ["ready"]},
             "held change name": {"name": ["R1"], "status": "ready", "change": {"state": "held"}}}
    for label, item in cases.items():
        root = tmp_path / label.replace(" ", "_")
        (root / "librarian" / "b").mkdir(parents=True)
        (root / "librarian" / "b" / "plan.json").write_text(json.dumps([item, {"name": "R2", "status": "ready"}]))
        s = WS.status(root)                    # was: TypeError / AttributeError out of `ecadlib status`
        WS.render(s)
        assert isinstance(s["problems"], list)
    # a damaged plan is named (and not counted); a good plan next to it still is
    root = tmp_path / "mixed"
    for b, items in (("bad", [{"name": "R1", "status": "held", "hold_reasons": 3}]),
                     ("good", [{"name": "R2", "status": "ready"}])):
        (root / "librarian" / b).mkdir(parents=True)
        (root / "librarian" / b / "plan.json").write_text(json.dumps(items))
    s = WS.status(root)
    assert any("librarian/bad/plan.json" in p for p in s["problems"]), s["problems"]
    assert [p["plan"] for p in s["plans"]] == ["librarian/good/plan.json"]
    assert s["totals"]["ready"] == 1 and "librarian/bad/plan.json" in WS.render(s)
