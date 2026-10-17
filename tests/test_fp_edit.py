"""Same-net pads from the pin table, the stacked-pad rule, and `ecadlib fp-edit` (hand edits of a held pad table).
Run:  python3 -m tests.test_fp_edit"""
import contextlib, io, json, tempfile
from pathlib import Path

from ecadlib import datasheet as D, footprint_adv as FA, fp_edit as FE
from ecadlib.eval import gate as G


def _spec(pads, **kw):
    return dict({"name": "T", "package_type": "custom",
                 "pad_table": {"unit": "mm", "y_axis": "down", "view": "top", "origin": "datum", "pads": pads}}, **kw)


def _errors(spec):
    return [p["message"] for p in FA.check_pad_table(spec) if p["level"] == "error"]


def test_same_net_from_pins_joins_names_not_nc():
    pins = [{"num": n, "name": nm} for n, nm in (("1", "V_{IN}"), ("2", "V_{IN}"), ("26", "V_{IN}"), ("3", "NC"), ("4", "NC"),
                                                  ("5", "DNC"), ("6", "DNC"), ("7", "GND"), ("8", ""), ("9", ""), ("10", "10"), ("11", "10"))]
    assert D.same_net_from_pins(pins) == [["1", "2", "26"]]
    assert D.same_net_from_pins(pins, [["7", "12"]]) == [["1", "2", "26"], ["7", "12"]]
    assert D.same_net_from_pins(pins, [["26", "30"]]) == [["1", "2", "26", "30"]]       # groups sharing a pad merge


def test_overlapping_pads_pass_only_on_one_net():
    pads = [{"number": "1", "x": 0, "y": 0, "w": 1, "h": 1}, {"number": "2", "x": 0.5, "y": 0, "w": 1, "h": 1},
            {"number": "3", "x": 3, "y": 0, "w": 1, "h": 1}]
    assert any("overlap" in e for e in _errors(_spec(pads)))
    assert not any("overlap" in e for e in _errors(_spec(pads, same_net=[["1", "2"]])))


def test_stacked_pad_of_another_size_is_fine_a_copy_is_not():
    ep = [{"number": "1", "x": -2, "y": 0, "w": 0.5, "h": 0.5}, {"number": "2", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
          {"number": "2", "x": 0, "y": 0, "w": 0.8, "h": 0.8}]
    assert _errors(_spec(ep)) == []
    assert any("listed twice" in e for e in _errors(_spec(ep[:2] + [dict(ep[1])])))


def test_pad_1_is_still_required():
    assert any("pad numbered 1" in e for e in _errors(_spec([{"number": "T", "x": 0, "y": 0, "w": 1, "h": 1},
                                                             {"number": "R", "x": 3, "y": 0, "w": 1, "h": 1}])))


def test_apply_renames_pads_and_pins():
    res = {"spec": _spec([{"number": "T", "x": 0, "y": 0, "w": 1, "h": 1}, {"number": "R", "x": 3, "y": 0, "w": 1, "h": 1}]),
           "symbol": {"pins": [{"num": "T", "name": "TIP"}, {"num": "R", "name": "RING"}]}}
    out, notes = FE.apply({"rename": {"T": "1", "R": "2"}, "same_net": [["1", "2"]], "by": "ob"}, res)
    assert [p["number"] for p in out["spec"]["pad_table"]["pads"]] == ["1", "2"]
    assert [p["num"] for p in out["symbol"]["pins"]] == ["1", "2"]
    assert out["spec"]["same_net"] == [["1", "2"]] and len(notes) == 2 and "ob" in notes[0]
    assert res["spec"]["pad_table"]["pads"][0]["number"] == "T"                           # the input is not changed
    assert _errors(out["spec"]) == []


def test_fp_edit_cli_builds_a_held_connector_on_the_next_run():
    with tempfile.TemporaryDirectory() as d:
        ids = ["connector-NLJ2MDXX-V"]
        rep = G.run(out=d, ids=ids)
        assert not rep["cases"][0]["pass"]
        plan = Path(d) / "lib" / "plan.json"
        with contextlib.redirect_stdout(io.StringIO()) as o:
            assert FE.main([str(plan), "--part", "NLJ2MDXX-V", "show"]) == 1
        assert "no pad numbered 1" in o.getvalue()
        with contextlib.redirect_stdout(io.StringIO()) as o:
            assert FE.main([str(plan), "--part", "NLJ2MDXX-V", "rename", "1+=1", "1-=2"]) == 0
        assert "passes the check" in o.getvalue()
        assert json.loads((plan.parent / FE.FILE).read_text())["NLJ2MDXX-V"]["rename"] == {"1+": "1", "1-": "2"}
        rep = G.run(out=d, ids=ids)                                                     # the librarian again
        c = rep["cases"][0]
        assert any(t.startswith("footprint edited: pads renamed by hand") for t in c["tried"])
        assert c["source"] == "datasheet"                                               # built (scored against KiCad's 1+/1-)
        with contextlib.redirect_stdout(io.StringIO()):
            assert FE.main([str(plan), "--part", "NLJ2MDXX-V", "undo"]) == 1
        assert "NLJ2MDXX-V" not in json.loads((plan.parent / FE.FILE).read_text())


def test_held_part_says_why_and_what_to_do():
    from ecadlib import messages as M
    with tempfile.TemporaryDirectory() as d:
        G.run(out=d, ids=["connector-NSJ12HF-1"])
        it = json.loads((Path(d) / "lib" / "plan.json").read_text())[0]
        codes = {e["code"]: e for e in M.explain_all(it["hold_reasons"])}
        assert set(codes) == {"padtable.no_pin1", "padtable.overlap", "padtable.symbol_waits"}, it["hold_reasons"]
        assert "G, R1" in codes["padtable.no_pin1"]["text"] and "fp-edit" in codes["padtable.no_pin1"]["fix"]
        assert "same-net G,S2" in codes["padtable.overlap"]["fix"]


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_fp_edit"]))
