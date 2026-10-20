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


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
