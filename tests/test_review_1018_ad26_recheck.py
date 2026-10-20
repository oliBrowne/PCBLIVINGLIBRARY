"""Review 2026-10-18, AD26 recheck: Unicode pin names in a SchLib, DbLib table names ACE renames, a non-ASCII
package name in the install scripts. Run:  python3 -m tests.test_review_1018_ad26_recheck"""
import json, warnings
from pathlib import Path

from ecadlib import altium_dblib_sync as S, altium_export as X, altium_lib as AL, altium_native as AN, altium_pkg as PKG
from tests.fixtures_lite import lite, released_anyway

HERE = Path(__file__).resolve().parent.parent


def _sym(names):
    pins = [{"num": str(i), "name": n, "type": "io", "side": "left"} for i, n in enumerate(names, 1)]
    return AL.ic_symbol("UNICODE_PINS", pins, "pins", layout=False)


def _soic8():
    spec = json.loads((HERE / "ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    return AL.footprint_from_spec(spec)[0]


# ------------------------------------------------------------------ 1. Unicode pin names
@lite
def test_pin_name_cp1252_cannot_hold_warns_and_compare_reports_best_fit(tmp_path):
    """'Φ0' / 'ϕ2' were written as '?0' / '?2' without a word, and after Altium rebuilt the library compare said
    "name 'F0' != 'Φ0'". The binary pin record has no UTF-8 copy of the name: the writer warns, naming the pin and the
    characters, and compare lists Altium's best-fit substitute as a known difference, not a problem."""
    names = ["Φ0", "ϕ2", "µA ±"]
    lib = tmp_path / "SchLib" / "UNICODE_PINS.SchLib"
    lib.parent.mkdir()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        AL.write_schlib(lib, [_sym(names)])
    msgs = [str(x.message) for x in w if issubclass(x.category, AL.PinNameWarning)]
    assert len(msgs) == 2, msgs                                           # 'µA ±' fits cp1252
    assert msgs[0].startswith("pin 1: name 'Φ0'") and "'Φ'" in msgs[0] and "'?0'" in msgs[0], msgs
    assert msgs[1].startswith("pin 2: name 'ϕ2'") and "'ϕ'" in msgs[1], msgs
    got = [r["name"] for r in AL.read_schlib(lib)[1][0]["records"] if r.get("RECORD") == "2"]
    assert got == ["?0", "?2", "µA ±"], got
    assert AL.not_cp1252("ΦΦxϕ") == "Φϕ" and AL.not_cp1252("µA ±?") == ""

    cmp = AN.compare(tmp_path)                                            # as ecadlib wrote it
    assert cmp["problems"] == [] and len(cmp["known"]) == 2, cmp
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lib.write_bytes(AL.schlib_bytes([_sym(["F0", "?2", "µA ±"])]))     # as Altium saved it
    cmp = AN.compare(tmp_path)
    assert cmp["problems"] == [] and cmp["checked"] == 1, cmp
    assert any("pin 1: name 'F0' for 'Φ0'" in k for k in cmp["known"]) and any("pin 2" in k for k in cmp["known"]), cmp
    assert AN.main(["compare", str(tmp_path)]) == 0
    # a character cp1252 can hold that differs is still a problem
    lib.write_bytes(AL.schlib_bytes([_sym(["F1", "?2", "µA +"])]))
    probs = AN.compare(tmp_path)["problems"]
    assert len(probs) == 2 and any("pin 1" in p for p in probs) and any("pin 3" in p for p in probs), probs
    assert not AN.best_fit_name("Φ0", "F00") and not AN.best_fit_name("A0", "B0")


# ------------------------------------------------------------------ 2. DbLib table names ACE renames
def test_sheet_names_are_the_names_ace_exposes():
    assert S.table_of({"folder": "Components/Driver's ICs"}) == "Drivers ICs"
    assert S.table_of({"folder": "Components/Res. Networks"}) == "Res_ Networks"
    assert S.table_of({"folder": "Components/Capacitors"}) == "Capacitors"
    assert S.table_of({"folder": "Components/'"}) == "Misc"
    for a in ("Driver's ICs$", "Drivers ICs$", "drivers ics", "DRIVER'S ICS"):
        assert S.table_key(a) == S.table_key("Drivers ICs"), a
    for a in ("Res. Networks$", "Res# Networks$", "res_ networks"):
        assert S.table_key(a) == S.table_key("Res_ Networks"), a
    assert S.table_key("Resistors") != S.table_key("Res_ Networks")


def _models(d):
    (d / "models" / "SchLib").mkdir(parents=True)
    (d / "models" / "PcbLib").mkdir(parents=True)
    AL.write_schlib(d / "models/SchLib/Resistor.SchLib", [AL.resistor_symbol()])
    spec = json.loads((HERE / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    AL.write_pcblib(d / "models/PcbLib/R_0603_1608Metric.PcbLib", [AL.footprint_from_spec(spec)[0]])


def _item(name, cat):
    return {"name": name, "folder": f"Components/{cat}", "description": "d", "comment": "=Value", "parameters": [],
            "status": "ready", "symbol": {"ref": "Resistor", "file": "models/SchLib/Resistor.SchLib"},
            "footprint": {"ref": "R_0603_1608Metric", "file": "models/PcbLib/R_0603_1608Metric.PcbLib"}}


def _plan(d, items):
    released_anyway(items, d)                  # the sync checks every part again: these were released anyway
    (d / "plan.json").write_text(json.dumps(items))
    return d / "plan.json"


def _table_names(ini):
    return [ln.split("=", 1)[1] for ln in ini.splitlines() if ln.startswith("TableName=")]


def _sections(ini, prefix):
    return [ln for ln in ini.splitlines() if ln.startswith(f"[{prefix}") and ln[len(prefix) + 1:-1].isdigit()]


@lite
def test_dblib_sync_writes_ace_names_and_resync_does_not_duplicate(tmp_path):
    """Sheets "Driver's ICs" / "Res. Networks" load in ACE as "Drivers ICs$" / "Res# Networks$", so the DbLib's
    TableName entries failed; after Altium re-saved the DbLib with its names, a re-sync added the old two again."""
    d = tmp_path
    _models(d)
    plan = _plan(d, [_item("R1", "Resistors"), _item("U1", "Driver's ICs"), _item("RN1", "Res. Networks")])
    S.sync(plan, d / "lib")
    lib = d / "lib" / "ecadlib.DbLib"
    assert set(S.read_tables(d / "lib" / "ecadlib.xlsx")) == {"Resistors", "Drivers ICs", "Res_ Networks"}
    ini = lib.read_bytes().decode()
    assert _table_names(ini) == ["Resistors$", "Drivers ICs$", "Res_ Networks$"], _table_names(ini)
    assert "FieldName=Drivers ICs$.Part Number|TableNameOnly=Drivers ICs$|" in ini and "Driver's" not in ini

    # what an older ecadlib left: sheets with the apostrophe / dot, and a DbLib Altium re-saved with ACE's names
    # beside the stale entries a re-sync appended (Table4/5 with their field maps)
    tabs = S.read_tables(d / "lib" / "ecadlib.xlsx")
    S.write_xlsx(d / "lib" / "ecadlib.xlsx", {{"Drivers ICs": "Driver's ICs", "Res_ Networks": "Res. Networks"}.get(t, t): v
                                             for t, v in tabs.items()})
    ini = ini.replace("Res_ Networks$", "Res# Networks$")
    ini = ini.replace("FieldName=Drivers ICs$.Value|TableNameOnly=Drivers ICs$|FieldNameOnly=Value|FieldType=1|ParameterName=Value|",
                      "FieldName=Drivers ICs$.Value|TableNameOnly=Drivers ICs$|FieldNameOnly=Value|FieldType=1|ParameterName=MyValue|")
    assert "ParameterName=MyValue" in ini
    n_fm = len(_sections(ini, "FieldMap"))
    stale = "".join(f"[Table{i}]\r\nSchemaName=\r\nTableName={t}\r\nEnabled=True\r\nUserWhere=0\r\nUserWhereText=\r\n"
                    for i, t in ((4, "Driver's ICs$"), (5, "Res. Networks$")))
    stale_fm = "".join("".join(X.fieldmap_section(n_fm + 1 + 2 * k + j, t, c)[0] + "\r\n" + X.fieldmap_section(0, t, c)[1] + "\r\n"
                               for j, c in enumerate(("Part Number", "Value")))
                       for k, t in enumerate(("Driver's ICs$", "Res. Networks$")))
    head, _, fms = ini.partition("[FieldMap1]")
    lib.write_bytes((head + stale + "[FieldMap1]" + fms + stale_fm).encode())
    assert len(_table_names(lib.read_bytes().decode())) == 5

    out = S.sync(plan, d / "lib")
    ini = lib.read_bytes().decode()
    # Altium's 'Res# Networks$' is the same table: renamed to the sheet's name, the stale twins dropped
    assert _table_names(ini) == ["Resistors$", "Drivers ICs$", "Res_ Networks$"], _table_names(ini)
    assert _sections(ini, "Table") == ["[Table1]", "[Table2]", "[Table3]"], _sections(ini, "Table")
    fms = _sections(ini, "FieldMap")
    assert fms == [f"[FieldMap{i}]" for i in range(1, len(fms) + 1)] and len(fms) == n_fm, (len(fms), n_fm)
    assert "ParameterName=MyValue" in ini, "the field maps Altium kept stay as they are"
    assert "Driver's" not in ini and "Res. Networks" not in ini
    assert set(S.read_tables(d / "lib" / "ecadlib.xlsx")) == {"Resistors", "Drivers ICs", "Res_ Networks"}
    assert {r["name"]: r["action"] for r in out["results"]} == {"R1": "up_to_date", "U1": "up_to_date", "RN1": "up_to_date"}, out
    S.sync(plan, d / "lib")
    again = lib.read_bytes().decode()
    assert again == ini, "a second re-sync changes nothing"


def test_dblib_update_renames_a_stale_only_table_and_keeps_case():
    cols = ["Part Number", "Value"]
    links = dict(conn_str="x", db_type="Microsoft Excel", db_path="ecadlib.xlsx", relative=True)
    old = S.dblib_update("", {"Driver's ICs": cols, "passives": cols}, links)          # what an older ecadlib wrote
    assert _table_names(old) == ["Driver's ICs$", "passives$"]
    new = S.dblib_update(old, {"Drivers ICs": cols, "Passives": cols}, links)
    assert _table_names(new) == ["Drivers ICs$", "passives$"], _table_names(new)     # renamed; case left alone
    assert "FieldName=Drivers ICs$.Value|TableNameOnly=Drivers ICs$|FieldNameOnly=Value|" in new
    assert len(_sections(new, "FieldMap")) == 4 and "LastFocusedTable=Drivers ICs$" in new
    assert S.dblib_update(new, {"Drivers ICs": cols, "Passives": cols}, links) == new


# ------------------------------------------------------------------ 3. non-ASCII package name
@lite
def test_install_scripts_are_cp1252(tmp_path):
    """Altium reads the .PrjScr and the .pas as ANSI: in UTF-8 'Bibliothèque' became 'BibliothÃ¨que'."""
    src = tmp_path / "src"
    (src / "PcbLib").mkdir(parents=True)
    AL.write_pcblib(src / "PcbLib" / "SOIC8.PcbLib", [_soic8()])
    rep = PKG.build(src, tmp_path / "pkg", "Bibliothèque", log=lambda *a: None, unchecked=True)   # no library
    pkg = tmp_path / "pkg"
    prj = (pkg / "Bibliothèque_Install.PrjScr").read_bytes()
    assert b"DocumentPath=Biblioth\xe8que_Install.pas\r\n" in prj, prj
    pas = (pkg / "Bibliothèque_Install.pas").read_bytes()
    assert b"'Biblioth\xe8que.LibPkg'" in pas and "Ã".encode("cp1252") not in pas
    assert pas.decode("cp1252") and rep["script"] == "Bibliothèque_Install.PrjScr"

    bad = tmp_path / "bad"
    try:
        PKG.build(src, bad, "Библиотека", log=lambda *a: None, unchecked=True)
    except ValueError as e:
        assert "Windows-1252" in str(e) and "--name" in str(e), e
    else:
        raise AssertionError("a name cp1252 can't hold must stop the package")
    assert not bad.exists(), "nothing written"
    assert PKG.main([str(src), "-o", str(bad), "--name", "中文"]) == 2
    try:
        PKG.script_bytes("x_Install.pas", "Begin\n  F('→');\nEnd;")
    except ValueError as e:
        assert "x_Install.pas line 2" in str(e), e
    else:
        raise AssertionError("text cp1252 can't hold must not be written as '?'")

