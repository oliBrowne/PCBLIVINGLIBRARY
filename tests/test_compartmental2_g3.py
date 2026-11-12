"""Compartmental testing round 2, group 3 (2026-11-12): what happens after a part is built - change control,
approvals, the ledger, library writing (librarian, KiCad import) and the Altium files. Each component is called
directly with small hand-made inputs."""
import json
from pathlib import Path

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- #30 parity reads the workbook the DbLib names
@lite
def test_parity_reads_the_workbook_altium_reads_not_the_csv_mirror(tmp_path):
    import openpyxl
    from ecadlib import altium_dblib_sync as S, parity as P
    from tests.test_dblib_sync import _base_items, _models, _plan
    _models(tmp_path)
    lib = tmp_path / "lib"
    S.sync(_plan(tmp_path, _base_items()), lib)
    wb = openpyxl.load_workbook(lib / "ecadlib.xlsx")
    ws = wb["Resistors"]
    h = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[h.index("Part Number")].value == "RES-0603-10k":
            row[h.index("Value")].value = "4.7k"                         # edited in the workbook Altium reads
    wb.save(lib / "ecadlib.xlsx")
    assert "10k" in (lib / "ecadlib.csv").read_text(encoding="utf-8-sig")  # the mirror is stale
    assert P.altium_db_rows(lib)["RES-0603-10k"]["Value"] == "4.7k"


# ---------------------------------------------------------------- #31 names that differ only in case
def _named_items(d, extra_name):
    import copy
    from tests.test_dblib_sync import _base_items, _models
    _models(d)
    items = _base_items()[:2]
    dup = copy.deepcopy(items[1])
    dup["name"] = extra_name
    items.append(dup)
    for k, it in enumerate(items):
        it["parameters"] += [{"name": "Manufacturer Part Number", "value": f"MPN-{k}"}, {"name": "Manufacturer", "value": "ACME"}]
    return items


@lite
def test_release_check_holds_a_second_part_whose_name_differs_only_in_case(tmp_path):
    from ecadlib import release_check as RC
    for other in ("res-0603-10K", "RES-0603-10k"):          # Altium Part Numbers ignore case; dblib-sync refuses it
        items = _named_items(tmp_path / other, other)
        rows = RC.apply(items, tmp_path / other)
        assert [r["status"] for r in rows] == ["ready", "ready", "held"], rows
        assert any("RES-0603-10k" in h and "case" in h for h in items[2]["hold_reasons"]), items[2]["hold_reasons"]
        RC.apply(items, tmp_path / other, allow=[other])            # a name clash cannot be released anyway
        assert items[2]["status"] == "held" and items[0]["status"] == "ready", items


# ---------------------------------------------------------------- #27 the Value in another part type's unit
def test_a_value_in_another_part_types_unit_is_held():
    from ecadlib import release_check as RC

    def it(folder, value, mpn=""):
        return {"name": "X", "folder": f"Components/{folder}", "comment": "=Value",
                "parameters": [{"name": "Value", "value": value}, {"name": "Manufacturer Part Number", "value": mpn}]}
    bad = [("Resistors", "100nF", ""), ("Resistors", "4.7uH", ""), ("Capacitors", "10kΩ", ""), ("Capacitors", "4.7uH", ""),
           ("Inductors", "100nF", ""), ("ICs", "100nF", "RC0603FR-0710KL"),             # a Yageo resistor MPN
           ("Misc", "10 ohm", "GRM188R71C104KA01D"), ("Misc", "1uF", "LQG15HS4N7S02D")]
    for folder, value, mpn in bad:
        msg = RC.value_kind_problem(it(folder, value, mpn))
        assert msg and value in msg, (folder, value, mpn, msg)
    good = [("Resistors", "10k", ""), ("Resistors", "4R7", ""), ("Resistors", "0R", ""), ("Resistors", "10K 1%", ""),
            ("Capacitors", "100nF 50V X7R", ""), ("Capacitors", "100n", ""), ("Inductors", "600R@100MHz", "BLM18PG601SN1D"),
            ("Inductors", "4.7uH", ""), ("ICs", "3.3V", "AMS1117-3.3"), ("ICs", "LM358", ""), ("Resistors", "", ""),
            ("Capacitors", "1uF/50V", "GRM188R61H105KA93D"), ("Resistors", "10k", "RC0603FR-0710KL"), ("Misc", "100nF", "")]
    for folder, value, mpn in good:
        assert RC.value_kind_problem(it(folder, value, mpn)) is None, (folder, value, mpn)


@lite
def test_release_check_holds_a_resistor_whose_value_is_a_capacitance(tmp_path):
    from ecadlib import release_check as RC
    from tests.test_dblib_sync import _base_items, _models
    _models(tmp_path)
    items = _base_items()[:2]
    for k, i in enumerate(items):
        i["parameters"] += [{"name": "Manufacturer Part Number", "value": f"MPN-{k}"}, {"name": "Manufacturer", "value": "ACME"}]
    items[0]["parameters"][0]["value"] = "100nF"                     # the resistor
    RC.apply(items, tmp_path)
    assert items[0]["status"] == "held" and items[1]["status"] == "ready", items
    assert any(h.startswith("fields: Value '100nF'") for h in items[0]["hold_reasons"]), items[0]["hold_reasons"]


# ---------------------------------------------------------------- #29 a footprint-name clash in the KiCad export
@lite
def test_kicad_export_leaves_out_a_part_whose_footprint_name_another_footprint_has(tmp_path):
    """Two parts whose footprints are both named MYFP but differ (pad width): dblib-sync and altium-pkg leave the
    second part out with an error; kicad-export wrote its symbol with an empty Footprint. Now it is left out too."""
    import contextlib, io, re
    from ecadlib import altium_lib as AL, kicad_export as KX, release_check as RC, stdparts as SP
    d = tmp_path / "lib"
    soic = SP.build("SOIC-8_3.9x4.9mm_P1.27mm")[0].replace('SOIC-8_3.9x4.9mm_P1.27mm"', 'MYFP"', 1)
    narrow = re.sub(r"\(size 1\.95 0\.6\)", "(size 1.95 0.45)", soic)
    assert narrow != soic
    items = []
    for name, text in (("PART_A", soic), ("PART_B", narrow)):
        sub = d / name
        for k in ("SchLib", "PcbLib", "KiCad"):
            (sub / k).mkdir(parents=True)
        (sub / "KiCad" / "MYFP.kicad_mod").write_text(text)
        AL.write_pcblib(sub / "PcbLib" / "MYFP.PcbLib", [AL.footprint_from_kicad(text, height_mm=1.75)])
        AL.write_schlib(sub / "SchLib" / f"{name}.SchLib", [AL.ic_symbol(
            name, [{"num": str(i), "name": f"P{i}", "type": "passive", "side": "left"} for i in range(1, 9)], "x",
            footprint="MYFP")])
        items.append({"name": name, "folder": "Components/ICs", "description": "x", "comment": "=Value",
                      "parameters": [{"name": "Value", "value": name}, {"name": "Manufacturer", "value": "ACME"},
                                     {"name": "Manufacturer Part Number", "value": name}],
                      "symbol": {"ref": name, "file": f"{name}/SchLib/{name}.SchLib"},
                      "footprint": {"ref": "MYFP", "file": f"{name}/PcbLib/MYFP.PcbLib", "kicad_mod": f"{name}/KiCad/MYFP.kicad_mod"},
                      "status": "ready", "source": {"symbol": "standard:generic", "footprint": "standard:generic"},
                      "verify": [], "hold_reasons": []})
    RC.apply(items, d)
    (d / "plan.json").write_text(json.dumps(items))
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = KX.main([str(d / "plan.json"), "-o", str(tmp_path / "kc"), "--name", "fpx", "--offline"])
    assert rc == 1 and "PART_B" in err.getvalue(), err.getvalue()
    txt = (tmp_path / "kc" / "fpx.kicad_sym").read_text()
    assert '(symbol "PART_A"' in txt and '(symbol "PART_B"' not in txt, txt[:400]
    assert '(property "Footprint" ""' not in txt


# ---------------------------------------------------------------- #43 a missing plan leaves nothing behind
@lite
def test_publishers_on_a_missing_plan_write_nothing(tmp_path, monkeypatch):
    import contextlib, io, os
    from ecadlib import cli
    monkeypatch.chdir(tmp_path)
    for argv in (["release-check", "nope/plan.json"], ["kicad-export", "nope/plan.json", "-o", "kc", "--name", "x"],
                 ["kicad-dblib", "nope/plan.json"], ["altium-pkg", "nope/plan.json"],
                 ["dblib-sync", "nope/plan.json", "--to", "sy"]):
        for with_folder in (False, True):
            if with_folder:
                (tmp_path / "nope").mkdir()
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                try:
                    rc = cli.main(argv)
                except SystemExit as e:
                    rc = e.code
            assert rc not in (0, None), (argv, rc)
            assert "nope" in err.getvalue() and "Traceback" not in err.getvalue(), (argv, err.getvalue())
            left = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
            assert left == (["nope"] if with_folder else []), (argv, with_folder, left)
            if with_folder:
                os.rmdir(tmp_path / "nope")
