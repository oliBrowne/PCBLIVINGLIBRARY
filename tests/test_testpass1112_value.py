"""Free test pass 2026-11-12: a BOM with no Value column (a Digi-Key / Mouser style export: designator, quantity,
maker, MPN, description) gave every resistor / capacitor / inductor an empty Value. They share the generic
Resistor / Capacitor / Inductor symbol whose Comment is '=Value', so the placed part showed no value in Altium,
KiCad showed the part number instead, parity said the two differ, and the part was still released 'ready'.
Now the value is taken from the description when it names exactly one value in the part's own unit, and a part
that still has no value is held."""
import contextlib
import io
import json
from pathlib import Path

from ecadlib import cli
from ecadlib import release_check as RC
from tests.fixtures_lite import lite

HEAD = "Designator,Quantity,Manufacturer,Manufacturer Part Number,Description,Footprint\n"


def _librarian(tmp_path, rows):
    bom = tmp_path / "bom.csv"
    bom.write_text(HEAD + "".join(r + "\n" for r in rows), encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        cli.main(["librarian", str(bom), "-o", str(tmp_path / "lib"), "--offline", "--sources", "standard",
                  "--providers", "", "--cache", str(tmp_path / "c")])
    plan = json.loads((tmp_path / "lib" / "plan.json").read_text(encoding="utf-8"))
    return {_par(it).get("Manufacturer Part Number"): it for it in plan}     # by MPN: a valued passive is named
                                                                              # RES-0603-10K, as with a Value column


def _par(item):
    return {p["name"]: p["value"] for p in item["parameters"]}


def _value(item):
    return _par(item).get("Value", "")


@lite
def test_value_comes_from_the_description_when_the_bom_has_no_value_column(tmp_path):
    got = _librarian(tmp_path, [
        "R1,1,Yageo,RC0603FR-0710KL,RES 10K OHM 1% 1/10W 0603,0603",
        "C1,1,Murata,GRM155R71C104KA88D,CAP CER 0.1UF 16V X7R 0402,0402",
        "L1,1,Murata,LQM21PN4R7MC0D,FIXED IND 4.7UH 800MA 0805,0805",
    ])
    assert _value(got["RC0603FR-0710KL"]) == "10K", got["RC0603FR-0710KL"]
    assert _value(got["GRM155R71C104KA88D"]) == "0.1UF", got["GRM155R71C104KA88D"]
    assert _value(got["LQM21PN4R7MC0D"]) == "4.7UH", got["LQM21PN4R7MC0D"]
    for it in got.values():
        assert it["status"] == "ready", (it["name"], it.get("hold_reasons"))


@lite
def test_a_passive_whose_description_names_no_value_or_two_is_held(tmp_path):
    got = _librarian(tmp_path, [
        "R1,1,Yageo,RC0603FR-0710KL,RES 0603,0603",                 # no value at all
        "R2,1,Yageo,RC0603FR-074K7L,RES 4.7K or 10K 0603,0603",     # two values: no guess
        "C1,1,Murata,GRM155R71C104KA88D,CAP CER 16V X7R 0402,0402",   # 16V is not a capacitance
    ])
    for name in ("RC0603FR-0710KL", "RC0603FR-074K7L", "GRM155R71C104KA88D"):
        it = got[name]
        assert _value(it) == "", it
        assert it["status"] == "held", (name, it["status"])
        assert any("Value is empty" in h for h in it.get("hold_reasons") or []), it.get("hold_reasons")


def test_release_check_holds_an_empty_value_behind_an_equals_value_comment():
    item = {"name": "R1", "comment": "=Value", "parameters": [{"name": "Value", "value": ""}]}
    assert RC.value_link_empty(item)
    item["parameters"][0]["value"] = "10k"
    assert not RC.value_link_empty(item)
    assert not RC.value_link_empty({"name": "U1", "comment": "LM358DR", "parameters": []})
    assert not RC.value_link_empty({"name": "U1", "comment": "=Part Number", "parameters": []})   # a DbLib column
    assert RC.value_link_empty({"name": "U1", "comment": "=Tolerance", "parameters": [{"name": "Tolerance", "value": " "}]})


def test_an_mpn_only_line_takes_the_value_from_the_part_search_text():
    from ecadlib import altium_export as AX
    lines = [{"designator": "R1", "mpn": "RC0603FR-0710KL", "description": "", "footprint": "0603", "qty": "1"}]
    import pandas as pd
    tri = pd.DataFrame([{"mpn": "RC0603FR-0710KL", "src_description": "10kΩ ±1% 100mW 0603 Chip Resistor",
                         "status": "found", "line": "0"}])
    df = AX.to_rows(lines, tri)["Resistors"]
    assert list(df["Value"]) == ["10kΩ"], df.to_dict("records")
