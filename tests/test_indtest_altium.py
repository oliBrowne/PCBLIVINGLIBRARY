"""Regression tests for the Altium bugs of the 2026-10-20 independent testing pass (T-ids in the comments). Run:
    python3 -m tests.test_indtest_altium"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

from ecadlib import altium365 as A3, altium_dblib_sync as S, altium_lib as AL
from tests.fixtures_lite import lite, released_anyway

ROOT = Path(__file__).parent.parent


def _models(d):
    (d / "models/SchLib").mkdir(parents=True, exist_ok=True)
    (d / "models/PcbLib").mkdir(parents=True, exist_ok=True)
    AL.write_schlib(d / "models/SchLib/Capacitor.SchLib", [AL.capacitor_symbol()])
    AL.write_schlib(d / "models/SchLib/Resistor.SchLib", [AL.resistor_symbol()])
    for n in ("R_0603_1608Metric", "C_0402_1005Metric"):
        spec = json.loads((ROOT / "ecadlib/package_specs" / f"{n}.json").read_text())
        AL.write_pcblib(d / f"models/PcbLib/{n}.PcbLib", [AL.footprint_from_spec(spec)[0]])


def _item(name, cat, sym, fp, **params):
    return {"name": name, "folder": f"Components/{cat}", "description": f"{name} desc", "comment": "=Value",
            "parameters": [{"name": k.replace("_", " "), "value": v} for k, v in params.items()], "status": "ready",
            "symbol": {"ref": sym, "file": f"models/SchLib/{sym}.SchLib"},
            "footprint": {"ref": fp, "file": f"models/PcbLib/{fp}.PcbLib"}, "hold_reasons": []}


def _wrong_0402(d):
    """The mistake: the 0603 resistor's footprint file copied over the 0402 capacitor's."""
    shutil.copyfile(d / "models/PcbLib/R_0603_1608Metric.PcbLib", d / "models/PcbLib/C_0402_1005Metric.PcbLib")


class _Nexar:
    """Just enough of the Nexar client for a first release: records uploads and releases."""
    def __init__(self):
        self.calls = []

    def release_defs(self, ws):
        return {}

    def find(self, ws, name):
        return None

    def find_model(self, ws, kind, name, folder):
        return None

    def folder_id(self, ws, path):
        return f"folder:{path}"

    def upload(self, p):
        self.calls.append(("upload", Path(p).name))
        return "file"

    def release(self, inp):
        self.calls.append(("release", inp["componentItemName"]))


# T003: altium365 push read back the model files before any upload, like dblib-sync
@lite
def test_push_refuses_a_footprint_file_holding_another_footprint(tmp_path):
    d = tmp_path
    _models(d)
    _wrong_0402(d)
    items = released_anyway([_item("CAP-0402-100nF", "Capacitors", "Capacitor", "C_0402_1005Metric", Value="100nF")], d)
    for it in items:
        for k in ("symbol", "footprint"):
            it[k]["file"] = str(d / it[k]["file"])
    nx = _Nexar()
    res = A3.push(items, nx, "https://acme.365.altium.com")
    assert res[0]["action"] == "error" and "holds ['R_0603_1608Metric'], not 'C_0402_1005Metric'" in res[0]["detail"], res
    assert nx.calls == [], nx.calls                       # nothing uploaded, nothing released
    dry = A3.push(items, None, "https://acme.365.altium.com", dry_run=True)
    assert dry[0]["action"] == "error", dry


# T004: a part dblib-sync refuses (read-back) gets no released revision 1; the fixed file is the first release
@lite
def test_refused_first_sync_releases_nothing_and_the_fix_syncs(tmp_path, monkeypatch):
    from ecadlib import ledger as LG
    d = tmp_path
    monkeypatch.setenv("ECADLIB_HOME", d / "home")
    _models(d)
    good = (d / "models/PcbLib/C_0402_1005Metric.PcbLib").read_bytes()
    _wrong_0402(d)
    items = released_anyway([_item("CAP-0402-100nF", "Capacitors", "Capacitor", "C_0402_1005Metric", Value="100nF")], d)
    (d / "plan.json").write_text(json.dumps(items, indent=1))
    r = S.sync(d / "plan.json", d / "lib")["results"][0]
    assert r["action"] == "error" and "holds ['R_0603_1608Metric']" in r["detail"], r
    assert not LG.Ledger(d).head("CAP-0402-100nF")       # no released revision for the refused file
    (d / "models/PcbLib/C_0402_1005Metric.PcbLib").write_bytes(good)      # the fix
    (d / "plan.json").write_text(json.dumps(items, indent=1))
    r = S.sync(d / "plan.json", d / "lib")["results"][0]
    assert r["action"] == "added", r
    assert (d / "models/PcbLib/C_0402_1005Metric.PcbLib").read_bytes() == good
    assert LG.Ledger(d).head("CAP-0402-100nF")["revision"] == 1


def _exit_code(fn):
    try:
        fn()
    except SystemExit as e:
        return e.code
    return 0


# T006: dblib-sync --prune with a plan.json of {} or [] refuses (exit 2) instead of deleting the whole library
@lite
def test_prune_with_a_damaged_plan_keeps_the_library(tmp_path, monkeypatch):
    import contextlib, io
    d = tmp_path
    monkeypatch.setenv("ECADLIB_HOME", d / "home")
    _models(d)
    items = released_anyway([_item("CAP-0402-100nF", "Capacitors", "Capacitor", "C_0402_1005Metric", Value="100nF"),
                             _item("RES-0603-10k", "Resistors", "Resistor", "R_0603_1608Metric", Value="10k")], d)
    (d / "plan.json").write_text(json.dumps(items, indent=1))
    assert {r["action"] for r in S.sync(d / "plan.json", d / "lib")["results"]} == {"added"}
    files = sorted(p.relative_to(d / "lib").as_posix() for p in (d / "lib").rglob("*"))
    for bad, says in (("{}", "not a librarian plan"), ("[]", "has no parts")):
        (d / "plan.json").write_text(bad)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = _exit_code(lambda: S.main([str(d / "plan.json"), "--to", str(d / "lib"), "--prune"]))
        assert code == 2 and says in err.getvalue() and err.getvalue().count("\n") == 1, (bad, code, err.getvalue())
        assert sorted(p.relative_to(d / "lib").as_posix() for p in (d / "lib").rglob("*")) == files, bad


# T006: kicad-dblib refuses a {} plan and a [] plan that would empty its database
@lite
def test_kicad_dblib_refuses_a_damaged_plan(tmp_path):
    from ecadlib import kicad_dblib as KD
    (tmp_path / "out").mkdir()
    (tmp_path / "out/ecadlib.sqlite").write_bytes(b"db")
    for bad, says in (("{}", "not a librarian plan"), ("[]", "has no parts")):
        (tmp_path / "plan.json").write_text(bad)
        try:
            KD.build(tmp_path / "plan.json", tmp_path / "out")
            raise AssertionError(f"{bad}: built")
        except ValueError as e:
            assert says in str(e), e
        assert (tmp_path / "out/ecadlib.sqlite").read_bytes() == b"db"


_HOUSE = {"parameters": {"names": {"Supplier Part Number 1": "LCSC PN", "Manufacturer": "Mfr"}}}


# T038: after adopting house names, dblib-sync leaves no stale value under ecadlib's old column name
@lite
def test_sync_after_adopting_house_names_has_no_stale_column(tmp_path, monkeypatch):
    from ecadlib import rules as RU
    d = tmp_path
    monkeypatch.setenv("ECADLIB_HOME", d / "home")
    _models(d)
    items = released_anyway([_item("CAP-0402-100nF", "Capacitors", "Capacitor", "C_0402_1005Metric", Value="100nF",
                                   Manufacturer="Samsung", Supplier_Part_Number_1="C1525")], d)
    (d / "plan.json").write_text(json.dumps(items, indent=1))
    S.sync(d / "plan.json", d / "lib")
    house = RU.resolve(json.loads(json.dumps(_HOUSE)), d)
    S.sync(d / "plan.json", d / "lib", rules=house)
    items[0]["parameters"][2]["value"] = "C347222"            # re-sourced
    (d / "plan.json").write_text(json.dumps(items, indent=1))
    S.sync(d / "plan.json", d / "lib", rules=house)
    row = S.read_tables(d / "lib/ecadlib.xlsx")["Capacitors"][1][0]
    assert row["LCSC PN"] == "C347222" and row["Mfr"] == "Samsung", row
    assert not row.get("Supplier Part Number 1") and not row.get("Manufacturer"), row


# T038: the A365 parameter merge drops ecadlib's old name of a field the push now sends under its house name
def test_a365_merge_drops_the_old_name_of_a_house_named_field():
    from ecadlib import rules as RU
    house = RU.resolve(json.loads(json.dumps(_HOUSE)), ".")
    existing = {"details": {"parameters": [{"name": "Supplier Part Number 1", "value": "C1525"},
                                           {"name": "LCSC PN", "value": "C1525"}, {"name": "Notes", "value": "hand"}]}}
    item = {"parameters": [{"name": "LCSC PN", "value": "C347222"}]}
    assert A3.merged_params(existing, item, house) == [{"name": "LCSC PN", "value": "C347222"},
                                                       {"name": "Notes", "value": "hand"}]
    assert {p["name"] for p in A3.merged_params(existing, item)} == {"Supplier Part Number 1", "LCSC PN", "Notes"}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
