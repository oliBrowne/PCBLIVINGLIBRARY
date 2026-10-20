"""Minor problems an overnight run in real Altium AD26 found, each reproduced offline with its cause fixed:

1. DbLib part name 'IND--10uH': an inductor whose footprint names no package left an empty token between two dashes.
2. MPNs with a space squashed in the part name: 'FC-135 32.768KA-AG0' became 'FC-13532.768KA-AG0'.
3. `ecadlib project` listing a crystal as 'Y1, Y1', Qty 2: the same component counted twice (its sheet listed twice in
   the .PrjPcb, or a copy of it with the same UniqueId on a second sheet).
4. dblib-sync --prune left a pruned footprint's STEP folder (PcbLib/ecadlib_3d/<stem>_<k>/) behind."""
import json, tempfile
from pathlib import Path

from ecadlib import altium_dblib_sync as S, altium_export as X, altium_lib as AL, altium_project as AP
from tests import test_altium_project as TAP, test_dblib_sync as TDS


# ---------------------------------------------------------------- 1+2. part names
def _names(lines, rules=None):
    return {k: list(v["Part Number"]) for k, v in X.to_rows(lines, rules=rules).items()}


def test_empty_package_token_drops_out_of_the_part_name():
    n = _names([{"designator": "L1", "value": "10uH", "mpn": "X", "footprint": ""},
                {"designator": "R1", "value": "10k", "mpn": "Y", "footprint": "Resistor_SMD:R_0603_1608Metric"}])
    assert n["Inductors"] == ["IND-10uH"], n                    # was IND--10uH
    assert n["Resistors"] == ["RES-0603-10k"], n                # a package keeps its place


def test_space_in_mpn_is_kept_as_a_separator():
    xtal = {"designator": "Y1", "value": "32.768kHz", "mpn": "FC-135 32.768KA-AG0", "manufacturer": "Epson",
            "footprint": "Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm"}
    rows = X.to_rows([xtal])["Crystals"].iloc[0]
    assert rows["Part Number"] == "FC-135_32.768KA-AG0", rows["Part Number"]        # was FC-13532.768KA-AG0
    assert rows["Manufacturer Part Number"] == "FC-135 32.768KA-AG0"                 # the MPN itself is untouched
    assert AL.file_stem(rows["Part Number"]) == rows["Part Number"]
    # two parts sharing a synthetic name get the MPN appended the same way
    a = {"designator": "C1", "value": "100nF", "mpn": "GRM 155", "footprint": "Capacitor_SMD:C_0402_1005Metric"}
    b = dict(a, designator="C2", mpn="CL05 B104")
    assert _names([a, b])["Capacitors"] == ["CAP-0402-100nF", "CAP-0402-100nF-CL05_B104"]
    # house naming rules: the MPN token keeps its separator too
    from ecadlib import rules as RU
    r = RU.load({"naming": {"component": "{mpn}", "by_category": {"Crystals": "{mpn}"}}})
    assert _names([xtal], r)["Crystals"] == ["FC-135_32.768KA-AG0"]


# ---------------------------------------------------------------- 3. a designator counted twice
def _xtal_sheet(path, uid="XTALAAAA"):
    return TAP.write_schdoc(path, [
        TAP.comp("U1", "ESP32", "MCUAAAAA", TAP.mpn("ESP32-C3", "Espressif"), ["QFN32"]),
        TAP.comp("Y1", "Crystal", uid, TAP.mpn("FC-135 32.768KA-AG0", "Epson"), ["XTAL 3215"])])


def _groups(prj):
    p = AP.load_project(prj)
    return {g["mpn"]: (g["designator"], g["qty"]) for g in AP.bom_groups(p)}, p["warnings"]


def test_sheet_listed_twice_is_read_once():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _xtal_sheet(d / "WiFi.SchDoc")
        (d / "WiFi.PrjPcb").write_text("[Design]\nVersion=1.0\n\n[Document1]\nDocumentPath=WiFi.SchDoc\n\n"
                                       "[Document2]\nDocumentPath=.\\wifi.schdoc\n\n")
        g, w = _groups(d / "WiFi.PrjPcb")
        assert g["FC-135 32.768KA-AG0"] == ("Y1", "1") and g["ESP32-C3"] == ("U1", "1"), g
        assert not any("duplicate designator" in x for x in w), w


def test_same_component_on_two_sheets_counted_once():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _xtal_sheet(d / "WiFi.SchDoc")
        TAP.write_schdoc(d / "Clock.SchDoc", [TAP.comp("Y1", "Crystal", "XTALAAAA", TAP.mpn("FC-135 32.768KA-AG0", "Epson"),
                                                       ["XTAL 3215"])])
        (d / "WiFi.PrjPcb").write_text("[Design]\nVersion=1.0\n\n[Document1]\nDocumentPath=WiFi.SchDoc\n\n"
                                       "[Document2]\nDocumentPath=Clock.SchDoc\n\n")
        g, w = _groups(d / "WiFi.PrjPcb")
        assert g["FC-135 32.768KA-AG0"] == ("Y1", "1"), g                           # was ('Y1, Y1', '2')
        assert any("Y1 is on WiFi.SchDoc and Clock.SchDoc with the same UniqueId" in x for x in w), w
        # two different components that share a designator are still two parts, with the warning
        TAP.write_schdoc(d / "Clock.SchDoc", [TAP.comp("Y1", "Crystal", "XTALBBBB", TAP.mpn("FC-135 32.768KA-AG0", "Epson"),
                                                       ["XTAL 3215"])])
        g, w = _groups(d / "WiFi.PrjPcb")
        assert g["FC-135 32.768KA-AG0"] == ("Y1, Y1", "2") and any("duplicate designator Y1" in x for x in w), (g, w)


# ---------------------------------------------------------------- 4. --prune and the STEP folder
STEP = b"ISO-10303-21;"


def _models_with_step(d):
    TDS._models(d)
    spec = json.loads((TDS.ROOT / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    fp = AL.footprint_from_spec(spec)[0]
    fp["bodies"] = [{"pts": [(-0.8, -0.4), (0.8, -0.4), (0.8, 0.4), (-0.8, 0.4)], "height": 0.5,
                     "step": {"name": "R_0603.step", "data": STEP, "rot": (0.0, 0.0, 0.0)}}]
    AL.write_pcblib(d / "models/PcbLib/R_0603_1608Metric.PcbLib", [fp])


def test_prune_removes_the_step_folder_ecadlib_wrote():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); _models_with_step(d)
        lib = d / "lib"
        S.sync(TDS._plan(d, TDS._base_items()), lib)
        step = lib / "PcbLib/ecadlib_3d/R_0603_1608Metric_0/R_0603.step"
        assert step.read_bytes() == STEP
        state = json.loads((lib / S.STATE).read_text())
        assert "PcbLib/ecadlib_3d/R_0603_1608Metric_0/R_0603.step" in state["files"], state
        # a folder the user made under ecadlib_3d, and a file the user put in the generated one: never deleted
        (lib / "PcbLib/ecadlib_3d/Mine").mkdir()
        (lib / "PcbLib/ecadlib_3d/Mine/mine.step").write_bytes(b"user")
        only_cap = [i for i in TDS._base_items() if i["name"] != "RES-0603-10k"]
        out = S.sync(TDS._plan(d, only_cap), lib, prune=True, dry_run=True)
        assert step.exists() and any(r["name"].endswith("R_0603.step") and r["kind"] == "3d" for r in out["results"])
        out = S.sync(TDS._plan(d, only_cap), lib, prune=True)
        assert not (lib / "PcbLib/R_0603_1608Metric.PcbLib").exists()
        assert not (lib / "PcbLib/ecadlib_3d/R_0603_1608Metric_0").exists(), "STEP folder left behind"
        assert (lib / "PcbLib/ecadlib_3d/Mine/mine.step").read_bytes() == b"user"
        assert any(r["name"] == "PcbLib/ecadlib_3d/R_0603_1608Metric_0/R_0603.step" and r["action"] == "removed"
                   for r in out["results"]), out["results"]
        # svn added the file and its folders, so they are `svn delete`d too; ecadlib_3d keeps the user's folder
        assert {"PcbLib/ecadlib_3d/R_0603_1608Metric_0/R_0603.step",
                "PcbLib/ecadlib_3d/R_0603_1608Metric_0"} <= set(out["removed"]), out["removed"]
        assert "PcbLib/ecadlib_3d" not in out["removed"], out["removed"]
        assert not any("ecadlib_3d" in f for f in json.loads((lib / S.STATE).read_text())["files"])


def test_prune_keeps_a_step_folder_holding_user_files_or_not_managed():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); _models_with_step(d)
        lib = d / "lib"
        S.sync(TDS._plan(d, TDS._base_items()), lib)
        gen = lib / "PcbLib/ecadlib_3d/R_0603_1608Metric_0"
        (gen / "notes.txt").write_text("mine")
        only_cap = [i for i in TDS._base_items() if i["name"] != "RES-0603-10k"]
        S.sync(TDS._plan(d, only_cap), lib, prune=True)
        assert not (gen / "R_0603.step").exists() and (gen / "notes.txt").read_text() == "mine"
    with tempfile.TemporaryDirectory() as t:                    # a state from before STEP files were tracked: kept
        d = Path(t); _models_with_step(d)
        lib = d / "lib"
        S.sync(TDS._plan(d, TDS._base_items()), lib)
        st = json.loads((lib / S.STATE).read_text())
        st["files"] = [f for f in st["files"] if "ecadlib_3d" not in f]
        (lib / S.STATE).write_text(json.dumps(st))
        only_cap = [i for i in TDS._base_items() if i["name"] != "RES-0603-10k"]
        S.sync(TDS._plan(d, only_cap), lib, prune=True)
        assert not (lib / "PcbLib/R_0603_1608Metric.PcbLib").exists()
        assert (lib / "PcbLib/ecadlib_3d/R_0603_1608Metric_0/R_0603.step").exists()


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_altium_ad26_overnight."]))
