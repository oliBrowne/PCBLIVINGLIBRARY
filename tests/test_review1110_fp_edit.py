"""Full review 2026-11-10, fp-edit: a rename on a footprint that was not read as a pad table (SOIC, QFN, QFP, BGA)
printed "nothing to edit here" but had already saved the edit; the next librarian run renamed the SYMBOL's pins and left
the pads alone (VIN moved to number 2 while pad 1 stayed pad 1): a crossed pin map no count check can see. And a rename
onto a number another symbol pin already has gave two pins one number."""
import contextlib, io, json, tempfile
from pathlib import Path

from ecadlib import fp_edit as FE
from tests.test_fp_edit import _spec

GULL = {"spec": {"name": "SOIC-8", "package_type": "gullwing", "pin_count": 8},
        "symbol": {"name": "X", "pins": [{"num": "1", "name": "VIN"}, {"num": "2", "name": "GND"}, {"num": "3", "name": "EN"}]}}


def test_a_rename_without_a_pad_table_changes_nothing():
    out, notes = FE.apply({"rename": {"1": "2", "2": "1"}, "by": "me", "at": "now"}, GULL)
    assert out["symbol"]["pins"] == GULL["symbol"]["pins"], out["symbol"]["pins"]
    assert len(notes) == 1 and "rename skipped" in notes[0] and "not read as a pad table" in notes[0], notes
    assert not any("pads renamed by hand" in n for n in notes)


def test_a_rename_onto_a_number_another_symbol_pin_has_is_refused():
    res = {"spec": _spec([{"number": "T", "x": 0, "y": 0, "w": 1, "h": 1}, {"number": "R", "x": 3, "y": 0, "w": 1, "h": 1}]),
           "symbol": {"pins": [{"num": "T", "name": "TIP"}, {"num": "R", "name": "RING"}, {"num": "1", "name": "OTHER"}]}}
    out, notes = FE.apply({"rename": {"T": "1"}}, res)
    assert [p["num"] for p in out["symbol"]["pins"]] == ["T", "R", "1"], out["symbol"]["pins"]
    assert [p["number"] for p in out["spec"]["pad_table"]["pads"]] == ["T", "R"]
    assert any("rename skipped" in n and "symbol pin" in n for n in notes), notes
    # the usual use still works: the pads take the numbers the symbol's pins already have (no pin named T there)
    res["symbol"]["pins"] = [{"num": "1", "name": "TIP"}, {"num": "2", "name": "RING"}]
    out, notes = FE.apply({"rename": {"T": "1", "R": "2"}}, res)
    assert [p["number"] for p in out["spec"]["pad_table"]["pads"]] == ["1", "2"]
    assert [p["num"] for p in out["symbol"]["pins"]] == ["1", "2"] and "pads renamed by hand" in notes[0]
    # a swap of two pads that both have pins is still one consistent rename
    res = {"spec": _spec([{"number": "1", "x": 0, "y": 0, "w": 1, "h": 1}, {"number": "2", "x": 3, "y": 0, "w": 1, "h": 1}]),
           "symbol": {"pins": [{"num": "1", "name": "A"}, {"num": "2", "name": "K"}]}}
    out, notes = FE.apply({"rename": {"1": "2", "2": "1"}}, res)
    assert [(p["num"], p["name"]) for p in out["symbol"]["pins"]] == [("2", "A"), ("1", "K")]
    assert [p["number"] for p in out["spec"]["pad_table"]["pads"]] == ["2", "1"]


def test_the_cli_saves_no_edit_for_a_footprint_without_a_pad_table():
    with tempfile.TemporaryDirectory() as d:
        plan = Path(d) / "plan.json"
        plan.write_text("[]", encoding="utf-8")
        (Path(d) / "datasheet").mkdir()
        (Path(d) / "datasheet" / "SOIC-8.json").write_text(json.dumps(GULL["spec"]), encoding="utf-8")   # as datasheet.write
        (Path(d) / "datasheet" / "PART1.symbol.json").write_text(
            json.dumps(dict(GULL["symbol"], name="PART1", footprint="SOIC-8")), encoding="utf-8")
        assert not FE.read(plan, "PART1")["spec"].get("pad_table")
        for cmd in (["rename", "1=2", "2=1"], ["same-net", "1,2"], ["hole", "0.8"]):
            with contextlib.redirect_stdout(io.StringIO()) as o, contextlib.redirect_stderr(io.StringIO()) as e:
                rc = FE.main([str(plan), "--part", "PART1", *cmd])
            assert rc == 1 and "not read as a pad table" in o.getvalue() + e.getvalue(), (cmd, rc, o.getvalue(), e.getvalue())
            assert "nothing saved" in o.getvalue() + e.getvalue()
            assert FE.load(d) == {}, (cmd, FE.load(d))
