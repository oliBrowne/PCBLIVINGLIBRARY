"""Regression tests for review findings in altium_export.py / altium365.py (offline). Run:
    python3 -m tests.test_review_altium"""
import gc, json, os, tempfile, warnings
from pathlib import Path

import pandas as pd

from ecadlib import altium365 as A, altium_export as X


def cat_of(designator):
    return next(iter(X.to_rows([{"designator": designator, "mpn": "M"}])))


def test_category_uses_whole_prefix():
    got = {d: cat_of(d) for d in ("LED1", "led2", "CN1", "IC1", "SW1", "TP1", "FB1", "U1,U2", "R1", "C3")}
    assert got == {"LED1": "Diodes", "led2": "Diodes", "CN1": "Connectors", "IC1": "ICs", "SW1": "Misc", "TP1": "Misc",
                   "FB1": "Misc", "U1,U2": "ICs", "R1": "Resistors", "C3": "Capacitors"}, got


def test_part_number_collisions_keep_every_part():
    caps = X.to_rows([{"designator": "C1", "mpn": "CL05B104KO5NNNC", "value": "100nF", "footprint": "C_0402"},
                      {"designator": "C2", "mpn": "GRM155R71H104KE14D", "value": "100nF", "footprint": "C_0402"},
                      {"designator": "C3", "mpn": "CL05B104KO5NNNC", "value": "100nF", "footprint": "C_0402"}])["Capacitors"]
    assert caps["Part Number"].tolist() == ["CAP-0402-100nF", "CAP-0402-100nF-GRM155R71H104KE14D"], caps
    res = X.to_rows([{"designator": "R1", "mpn": "A", "footprint": "R_0603"},           # no value
                     {"designator": "R2", "mpn": "B", "footprint": "R_0603"}])["Resistors"]
    assert res["Part Number"].tolist() == ["A", "B"], res
    ics = X.to_rows([{"designator": "U1", "value": "LM358"}, {"designator": "U2", "value": "NE555"}])["ICs"]   # no MPN
    assert ics["Part Number"].tolist() == ["LM358", "NE555"] and ics["Library Ref"].tolist() == ["LM358", "NE555"], ics


def test_triage_row_without_mpn_is_not_matched():
    tri = pd.DataFrame([{"mpn": "", "local_footprint": "ecadlib:SOT-23"}])
    row = X.to_rows([{"designator": "U1", "value": "LM358", "footprint": "SOIC-8"}], tri)["ICs"].iloc[0]
    assert row["Footprint Ref"] == "SOIC-8", row


def test_library_path_matches_altium_lib_layout():
    t = X.to_rows([{"designator": "U1", "mpn": "CH340C"}, {"designator": "R1", "mpn": "A", "value": "10k"}])
    assert t["ICs"].iloc[0]["Library Path"] == "SchLib\\CH340C.SchLib", t["ICs"].iloc[0]
    assert t["Resistors"].iloc[0]["Library Path"] == "SchLib\\Resistor.SchLib", t["Resistors"].iloc[0]


def test_export_closes_sqlite():
    with warnings.catch_warnings(record=True) as w, tempfile.TemporaryDirectory() as d:
        warnings.simplefilter("always")
        X.main(["examples/sample_bom.csv", "-o", d]); gc.collect()
        leaks = [str(x.message) for x in w if issubclass(x.category, ResourceWarning) and "sqlite" in str(x.message)]
    assert not leaks, leaks


class Resp:
    def __init__(self, body): self.body = body
    def raise_for_status(self): pass
    def json(self): return self.body


class UpdateFails:
    """HTTP 200 whose desUpdateComponentRevisionParameters payload carries errors."""
    def post(self, url, json=None, **kw):
        if "desUpdateComponentRevisionParameters" in json["query"]:
            return Resp({"data": {"desUpdateComponentRevisionParameters": {"componentId": "c1", "errors": [{"message": "denied"}]}}})
        if "components(" in json["query"]:
            return Resp({"data": {"desLibrary": {"components": {"nodes": [{"id": "c1", "name": "X"}]}}}})
        return Resp({"data": {}})


ITEM = {"name": "X", "folder": "Components/ICs", "description": "", "comment": "", "parameters": [{"name": "Value", "value": "1"}],
        "symbol": {"ref": "X", "file": None}, "footprint": {"ref": "X", "file": None}, "status": "needs_altium_models"}


def test_update_payload_errors_are_errors():
    out = A.push([ITEM], A.Nexar("t", UpdateFails()), "https://ws")
    assert out[0]["action"] == "error" and "denied" in out[0]["detail"], out


def test_push_exits_nonzero_when_a_part_fails():
    class Bad(A.Nexar):
        def __init__(self, token): pass
        def release_defs(self, ws): return {}
        def find(self, ws, name): raise A.NexarError("boom")
    real, old = A.Nexar, os.environ.get("NEXAR_TOKEN")
    A.Nexar, os.environ["NEXAR_TOKEN"] = Bad, "t"
    try:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "p.json").write_text(json.dumps([ITEM]))
            try:
                A.main(["push", f"{d}/p.json", "--workspace", "https://ws", "-o", f"{d}/r.json"])
                raise AssertionError("push with a failed part exited 0")
            except SystemExit as e:
                assert e.code and "1 of 1" in str(e.code), e.code
            assert json.loads((Path(d) / "r.json").read_text())[0]["action"] == "error"
    finally:
        A.Nexar = real
        if old is None: os.environ.pop("NEXAR_TOKEN", None)
        else: os.environ["NEXAR_TOKEN"] = old


def test_plan_names_unique():
    items = A.plan([{"designator": "D1", "mpn": "BAT54"}, {"designator": "U7", "mpn": "BAT54"}])
    assert [i["name"] for i in items] == ["BAT54"], items


def test_update_op_matches_nexar_schema():
    try:
        from graphql import build_schema, parse, validate
    except ImportError:
        print("skip test_update_op_matches_nexar_schema (pip install graphql-core)"); return
    schema = build_schema(Path("tests/nexar/schema.graphql").read_text(), assume_valid_sdl=True)
    assert not validate(schema, parse(A.M_UPDATE_PARAMS))


def test_plan_paths_survive_cwd_change():
    """plan.json stores model paths relative to itself, so push can run from any folder."""
    import subprocess, sys
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "sub" / "plan.json"
        subprocess.run([sys.executable, "-m", "ecadlib.altium365", "plan", "examples/sample_bom.csv", "--models",
                        "out/altium_models", "-o", str(out)], cwd=root, check=True, capture_output=True)
        files = [i[k]["file"] for i in json.loads(out.read_text()) for k in ("symbol", "footprint") if i[k]["file"]]
        assert files and all(not os.path.isabs(f) and (out.parent / f).exists() for f in files), files


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
