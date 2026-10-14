"""Offline tests for ecadlib.rules (ecadlib.toml: load, merge, validate) and for the tools following it.
Run:  python3 -m tests.test_rules

Without a rules file every output must be byte-identical to before; with one, a full librarian run (the fake
KiCad library and EasyEDA payloads from tests/test_librarian) must rename, map and lay out parts as it says."""
import contextlib, io, json, os, tempfile
from pathlib import Path
from unittest import mock

import pandas as pd

from ecadlib import (altium365 as A, altium_dblib_sync as S, altium_export as X, altium_lib as AL, approvals as AP,
                     bom_triage as T, kicad_export as KE, release_check as RC, rules as RU, upstream as U)
from tests import test_librarian as TL

ROOT = Path(__file__).parent.parent
SAMPLE = ROOT / "examples" / "sample_bom.csv"

HOUSE = """
[naming]
value_style = "rkm"
footprint = "ipc7351"
[naming.by_category]
Resistors = "RES_{package}_{value}_{tolerance}"
LED = "LED_{package}_{value}"
[parameters]
required = ["Manufacturer", "MPN", "Datasheet", "RoHS"]
[parameters.names]
"Manufacturer Part Number" = "MPN"
"ComponentLink2URL" = "Datasheet"
[parameters.defaults]
RoHS = "Yes"
[folders]
root = "Library"
[folders.map]
R = "Library/Passives/Resistors"
[layers]
courtyard = "Mechanical1"
assembly = "Mechanical2"
body3d = "Mechanical3"
[sources]
order = ["kicad", "datasheet"]
[release.approvals]
required = 1
"""


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


def bad(text, *needles):
    try:
        RU.validate(RU.parse_text(text), "t.toml")
    except RU.RulesError as e:
        assert all(n in str(e) for n in needles), (needles, str(e))
        return
    raise AssertionError(f"accepted: {text!r}")


def test_defaults_and_load():
    with tempfile.TemporaryDirectory() as d:
        r = RU.load(None, start=d)
        assert r["_file"] == "" and RU.effective(r) == RU.DEFAULTS
        (Path(d) / "a" / "b").mkdir(parents=True)
        (Path(d) / "ecadlib.toml").write_text(HOUSE)
        r = RU.load(None, start=Path(d) / "a" / "b")                    # found in a parent
        assert r["_file"].endswith("ecadlib.toml") and r["naming"]["footprint"] == "ipc7351"
        assert r["naming"]["component"] == "{mpn}" and r["parameters"]["supplier_slots"] == 2   # merged over the defaults
        assert RU.param_name("Manufacturer Part Number", r) == "MPN" and RU.canonical_param("MPN", r) == "Manufacturer Part Number"
        assert RU.folder_for("Resistors", r) == "Library/Passives/Resistors" and RU.folder_for("ICs", r) == "Library/ICs"
        assert RU.layers(r) == {"courtyard": 57, "assembly": 58, "body3d": 59, "silkscreen": 33, "designator": 33}
        assert RU.layers(RU.DEFAULTS) is None and RU.ipc_density(RU.DEFAULTS) is None
        assert RU.load({"footprint": {"density": "A"}})["footprint"]["density"] == "M"   # A/B/C accepted as M/N/L
        (Path(d) / "j").mkdir()
        (Path(d) / "j" / "ecadlib_rules.json").write_text(json.dumps({"footprint": {"density": "L"}}))   # Python 3.10 form
        assert RU.load(None, start=Path(d) / "j")["footprint"]["density"] == "L"
        try:
            RU.load(Path(d) / "missing.toml")
            raise AssertionError("missing explicit file accepted")
        except RU.RulesError as e:
            assert "not found" in str(e)


def test_validation_names_the_bad_key():
    bad("[nameing]\ncomponent='x'", "[nameing]", "known section")
    bad("[naming]\ncomponant='{mpn}'", "naming.componant", "did you mean naming.component")
    bad("[naming]\ncomponent='{mpn}_{colour}'", "naming.component", "{colour}")
    bad("[naming.by_category]\nWidgets='{mpn}'", "naming.by_category.Widgets")
    bad("[naming]\nfootprint='fancy'", "naming.footprint", "ipc7351")
    bad("[naming]\nvalue_style='roman'", "naming.value_style", "rkm")
    bad("[footprint]\ndensity='X'", "footprint.density", "L, N or M")
    bad("[layers]\ncourtyard='Mechanical40'", "layers.courtyard", "1 to 16")
    bad("[layers]\nassembly='Top Fab'", "layers.assembly", "not an Altium layer")
    bad("[sources]\norder=['kicad','octopart']", "sources.order", "octopart")
    bad("[parameters]\nrequired='MPN'", "parameters.required", "list")
    bad("[parameters]\nsupplier_slots=12", "parameters.supplier_slots")
    bad("[bom.columns]\npartnumber='PN'", "bom.columns.partnumber")
    bad("[release.approvals]\nrequired=-1", "release.approvals")
    bad("naming = 3", "[naming]", "table")
    try:
        RU.parse_text("[naming\n")
        raise AssertionError("bad TOML accepted")
    except RU.RulesError as e:
        assert "not valid TOML" in str(e)
    RU.validate(RU.parse_text(HOUSE))


def test_dumps_round_trip_and_cli():
    full = RU.dumps(RU.DEFAULTS, only={f"{s}.{k}" for s in RU.DEFAULTS for k in RU.DEFAULTS[s]})
    assert RU.merge(RU.DEFAULTS, RU.parse_text(full)) == RU.DEFAULTS
    r = RU.load(RU.parse_text(HOUSE))
    again = RU.load(RU.parse_text(RU.dumps(r, notes={"naming.footprint": ["from a test"]})))
    assert RU.effective(again) == RU.effective(r)
    assert "# from a test" in RU.dumps(r, notes={"naming.footprint": ["from a test"]})
    from ecadlib import cli
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "ecadlib.toml"
        f.write_text("[footprint]\ndensity = 'Q'\n")
        out = io.StringIO()
        with contextlib.redirect_stderr(out), contextlib.redirect_stdout(io.StringIO()):
            assert cli.main(["rules", "check", str(f)]) == 2
        assert "footprint.density" in out.getvalue()
        f.write_text(HOUSE)
        with contextlib.redirect_stdout(io.StringIO()) as o:
            assert cli.main(["rules", "check", str(f)]) == 0
        assert '"footprint": "ipc7351"' in o.getvalue()
        with contextlib.redirect_stdout(io.StringIO()) as o:
            assert cli.main(["rules", "show"]) == 0
        assert RU.merge(RU.DEFAULTS, RU.parse_text(o.getvalue())) == RU.DEFAULTS


def test_naming_templates_and_values():
    r = RU.load(RU.parse_text(HOUSE))
    f = RU.line_fields({"value": "10k", "description": "Resistor 10k 1% 0603", "mpn": "RC0603FR-0710KL"}, "Resistors", "0603", r)
    assert f["value"] == "10K" and f["tolerance"] == "1%" and RU.component_name(f, r) == "RES_0603_10K_1%"
    f = RU.line_fields({"value": "4.7k", "mpn": "X"}, "Resistors", "0402", r)
    assert RU.component_name(f, r) == "RES_0402_4K7"                     # no tolerance: the field and its _ drop out
    f = RU.line_fields({"value": "", "mpn": "RC1"}, "Resistors", "0402", r)
    assert RU.component_name(f, r) == "RC1"                              # no value at all: named like any part
    caps = RU.load({"naming": {"by_category": {"C": "C{package}_{value}_{voltage}_{dielectric}"}, "value_style": "rkm"}})
    f = RU.line_fields({"value": "0.1uF", "description": "100nF 16V X7R"}, "Capacitors", "0402", caps)
    assert RU.component_name(f, caps) == "C0402_100N_16V_X7R", RU.component_name(f, caps)
    assert [RU.fmt_value("4.7uF", "Capacitors", s) for s in ("rkm", "rkm_lower", "si", "si_unit")] == ["4U7", "4u7", "4.7u", "4.7uF"]
    assert RU.fmt_value("100", "Resistors", "rkm") == "100R" and RU.fmt_value("2.2M", "Resistors", "rkm") == "2M2"
    assert RU.fmt_value("whatever", "Resistors", "rkm") == "whatever" and RU.fmt_value("10k", "ICs", "rkm") == "10k"
    assert RU.ipc7351_name("R_0603_1608Metric") == "RESC1608X55N" and RU.ipc7351_name("SOIC-8_3.9x4.9mm_P1.27mm") == "SOIC127P600X175-8N"
    assert RU.ipc7351_name("QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm", density="M") == "QFN50P300X300X100-17M"
    assert RU.ipc7351_name("USB_C_Receptacle_HRO_TYPE-C-31-M-12") is None
    assert RU.footprint_name("USB_C_Receptacle_HRO_TYPE-C-31-M-12", r) == "USB_C_Receptacle_HRO_TYPE-C-31-M-12"   # unknown: kept
    t = RU.load({"naming": {"footprint": "{ref}{package}"}})
    assert RU.footprint_name("R_0603_1608Metric", t) == "R0603" and RU.footprint_name("SOIC-8_3.9x4.9mm_P1.27mm", t).startswith("SOIC")


def test_no_rules_file_means_identical_outputs():
    lines, _ = T.read_bom(SAMPLE)
    assert lines == T.read_bom(SAMPLE, RU.bom_columns(RU.DEFAULTS))[0]
    a, b = X.to_rows(lines), X.to_rows(lines, None, RU.load({}))
    assert list(a) == list(b) and all(a[k].equals(b[k]) for k in a)
    assert json.dumps(A.plan(lines)) == json.dumps(A.plan(lines, rules=RU.load({})))
    spec = json.loads((ROOT / "ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1791936000"}):
        fp, _ = AL.footprint_from_spec(spec, RU.density_for(spec, RU.DEFAULTS))
        (Path(d) / "x").mkdir()
        p1, p2 = AL.write_pcblib(Path(d) / "a.PcbLib", [fp]), AL.write_pcblib(Path(d) / "x" / "a.PcbLib", [fp], RU.layers(RU.DEFAULTS))
        assert p1.read_bytes() == p2.read_bytes()
        # the whole librarian: no file vs. an explicit defaults dict -> byte-identical plan and reports
        outs = []
        for k, rules in (("none", None), ("defaults", RU.load({}))):
            (Path(d) / k).mkdir()
            TL.run(Path(d) / k, rules=rules, sources=["kicad", "easyeda", "datasheet"])
            outs.append({f: (Path(d) / k / "out" / f).read_text().replace(str(Path(d) / k), "<run>")
                         for f in ("plan.json", "review.md", "release_check.csv", "release_check.md", "triage.csv")})
        assert outs[0] == outs[1]


def test_librarian_follows_a_house_rules_file():
    """A full librarian run with a non-default ecadlib.toml next to its output folder: names, footprint names, folders,
    default parameters, sources order, layers and required fields all follow the file."""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "ecadlib.toml").write_text(HOUSE)                     # found from <d>/out: no argument needed
        bom = TL.BOM.replace("Resistor 10k 0603", "Resistor 10k 1% 0603")
        items, http = TL.run(d, bom=bom)
        out = Path(d) / "out"
        r1 = items["RES_0603_10K_1%"]
        assert r1["folder"] == "Library/Passives/Resistors" and r1["footprint"]["ref"] == "RESC1608X55N", r1
        assert r1["footprint"]["kicad"] == "R_0603_1608Metric" and r1["footprint"]["file"] == "models/PcbLib/RESC1608X55N.PcbLib"
        assert r1["source"]["footprint"] == "kicad:Resistor_SMD:R_0603_1608Metric", r1["source"]
        assert {"name": "RoHS", "value": "Yes"} in r1["parameters"]
        assert items["TPS1234DSGR"]["folder"] == "Library/ICs" and items["TPS1234DSGR"]["footprint"]["ref"] == "SOIC127P600X175-8N"
        assert r1["release_check"]["result"] == "pass", r1["release_check"]   # RoHS + MPN (house name) present
        fp = AL.read_pcblib(out / r1["footprint"]["file"])[0]
        layers = {r["layer"] for r in fp["records"] if r["type"] in ("track", "region", "arc")}
        assert 57 in layers and 58 in layers and AL.MECH15 not in layers and AL.MECH13 not in layers, layers
        bodies = [AL.parse_params(r["params"]) for r in fp["records"] if r["type"] == "body"]
        assert bodies and all(b["V7_LAYER"] == "MECHANICAL3" for b in bodies) and \
            {r["layer"] for r in fp["records"] if r["type"] == "body"} == {59}
        q1 = items["EEPART1"]                                              # [sources] order has no easyeda
        assert not q1["symbol"].get("file") and not any(t.startswith("easyeda") for t in q1["librarian"]["tried"]), q1["librarian"]
        assert "LED-RED-0603" in items                                   # LED key = LED designators; D1 keeps {mpn}
        # required fields: house names and an extra one
        it = dict(r1, parameters=[p for p in r1["parameters"] if p["name"] != "RoHS"])
        fails, _, _ = RC.check_item(it, out, RU.load(Path(d) / "ecadlib.toml"))
        assert ("fields", "RoHS is empty") in fails, fails
        it = dict(r1, parameters=[p for p in r1["parameters"] if p["name"] != "Manufacturer Part Number"])
        assert ("fields", "MPN is empty") in RC.check_item(it, out, RU.load(Path(d) / "ecadlib.toml"))[0]
        # approvals: [release.approvals] passes through when there is no approvals.json
        assert AP.load_policy(out)["required"] == 1
        assert all(i["status"] in ("awaiting_approval", "held") for i in items.values()), {n: i["status"] for n, i in items.items()}
        # push sends house parameter names
        plan = json.loads((out / "plan.json").read_text())
        res = A.push([dict(p, status="ready") for p in plan if p["name"] == "RES_0603_10K_1%"], None, "https://w", dry_run=True,
                     rules=RU.load(Path(d) / "ecadlib.toml"), policy={})
        names = [p["name"] for p in res[0]["detail"]["input"]["parameters"]]
        assert "MPN" in names and "Manufacturer Part Number" not in names and "Datasheet" in names, names


def test_tools_use_house_names_at_the_boundaries():
    r = RU.load(RU.parse_text(HOUSE))
    item = {"name": "P1", "folder": "Components/ICs", "description": "d", "comment": "c",
            "parameters": [{"name": "Manufacturer", "value": "ACME"}, {"name": "Manufacturer Part Number", "value": "P1"},
                           {"name": "ComponentLink2URL", "value": "https://x"}], "cleared": ["Supplier 1"],
            "symbol": {"ref": "P1"}, "footprint": {"ref": "SOT-23"}}
    row = S.plan_row(item, r)
    assert row["MPN"] == "P1" and "Manufacturer Part Number" not in row and row["Datasheet"] == "https://x" and row["Part Number"] == "P1"
    assert S.plan_row(item) == S.plan_row(item, RU.DEFAULTS)
    fields = dict(KE.plan_fields(item, "lib", "SOT-23", {}, r))
    assert fields["MPN"] == "P1" and fields["Manufacturer"] == "ACME"
    house = RU.load({"parameters": {"names": {"Manufacturer Part Number": "Mfr PN", "Manufacturer": "Mfr"}}})
    fields = dict(KE.plan_fields(item, "lib", "SOT-23", {}, house))
    assert fields["Mfr PN"] == "P1" and fields["Mfr"] == "ACME" and "MPN" not in fields
    assert KE.plan_fields(item, "lib", "SOT-23", {}) == KE.plan_fields(item, "lib", "SOT-23", {}, RU.DEFAULTS)
    node = {"name": "P1", "details": {"parameters": [{"name": "Manufacturer", "value": "ACME"}, {"name": "MPN", "value": "P1"},
                                                     {"name": "Datasheet", "value": "https://x"}]}, "folder": {"path": "Components/ICs"}}
    rows, _ = U.check_a365([item], [node], None, None, r)
    assert not [x for x in rows if x["status"] == "parameter"], rows      # house names in the workspace are not drift
    rows, _ = U.check_a365([item], [node])
    assert [x for x in rows if x["status"] == "parameter"]                 # without the rules they are
    # DbLib export with house names and 3 supplier slots
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "ecadlib.toml").write_text(HOUSE.replace("required = [", "supplier_slots = 3\nrequired = ["))
        quiet(X.main, [str(SAMPLE), "-o", str(Path(d) / "alt"), "--rules", str(Path(d) / "ecadlib.toml")])
        xl = pd.read_excel(Path(d) / "alt" / "parts.xlsx", sheet_name=None, dtype=str)
        cols = list(next(iter(xl.values())).columns)
        assert "MPN" in cols and "Supplier 3" in cols and "Manufacturer Part Number" not in cols, cols
        assert any(str(n).startswith("RES_0603_10K") for df in xl.values() for n in df["Part Number"]), [list(df["Part Number"]) for df in xl.values()]
        txt = (Path(d) / "alt" / "ecadlib_sqlite.DbLib").read_text()
        assert "FieldNameOnly=MPN|" in txt


def test_bom_columns_and_pin1():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d) / "bom.csv"
        b.write_text("Designator,Qty,Part Number,Our PN,Maker\nU1,1,LM358DR,INT-0001,TI\n")
        lines, mapping = T.read_bom(b)
        assert mapping["mpn"] == "Part Number"
        lines, mapping = T.read_bom(b, RU.bom_columns(RU.load({"bom": {"columns": {"mpn": "our pn"}}})))
        assert mapping["mpn"] == "Our PN" and lines[0]["mpn"] == "INT-0001" and "Part Number" not in mapping.values()
    assert RC._has_pin1({"A1", "A2"}) and not RC._has_pin1({"A1"}, ["1"])
