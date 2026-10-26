"""2026-10-26 fixes, second review (R-FX11-2..6): save_rules gates, v1 approvals, live sign-off policy, menu refusal.
Run:  python3 -m tests.test_fix1026_fx11"""
import json
from pathlib import Path

from tests.fixtures_lite import lite


# ---- R-FX11-2: parameters.required compared by canonical name (the MCP setup flow saves)
def _mcp_ws(root, rules_text=None):
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    if rules_text is not None:
        (root / "ecadlib.toml").write_text(rules_text, encoding="utf-8")
    (root / "librarian" / "b").mkdir(parents=True, exist_ok=True)
    ws = Workspace(str(root), [], None, True)
    ws.mcp_options = M.Options(confine=True)
    return ws


def _call(ws, name, args):
    from ecadlib import mcp_server as M
    return M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": name, "arguments": args}})["result"]


def _save(ws, text):
    r = _call(ws, "save_rules", {"toml": text, "overwrite": True})
    return ("REFUSED " + r["content"][0]["text"]) if r["isError"] else "saved"


def _parts_lib(root, datasheet):
    lib = root / "mylib"
    lib.mkdir()
    rows = ["Part Number,Library Ref,Footprint Ref,Description,Manufacturer,MPN,Datasheet"]
    for i, (mpn, mf) in enumerate([("LM358DR", "TI"), ("NE555DR", "TI"), ("AO3401A", "AOS"), ("BAT54", "Nexperia"),
                                   ("SS34", "Diodes")]):
        rows.append(f"P{i},{mpn},SOIC-8,desc {i},{mf},{mpn}," + (f"https://x/{mpn}.pdf" if datasheet else ""))
    (lib / "parts.csv").write_text("\n".join(rows) + "\n")
    return lib


@lite
def test_fx11_2_required_under_the_house_name_saves(tmp_path):
    ws = _mcp_ws(tmp_path)
    text = ('[parameters]\nrequired = ["Manufacturer", "MPN", "Datasheet", "Description"]\n'
            '[parameters.names]\n"Manufacturer Part Number" = "MPN"\n')
    assert _save(ws, text) == "saved"


@lite
def test_fx11_2_dropping_a_required_parameter_is_still_refused(tmp_path):
    ws = _mcp_ws(tmp_path)
    r = _save(ws, '[parameters]\nrequired = ["Manufacturer", "Datasheet", "Description"]\n')
    assert r.startswith("REFUSED") and "parameters.required" in r, r
    # a house name that is not the required parameter's does not stand in for it
    r = _save(ws, '[parameters]\nrequired = ["Manufacturer", "MPN", "Datasheet", "Description"]\n')
    assert r.startswith("REFUSED") and "parameters.required" in r, r


@lite
def test_fx11_2_propose_then_save_over_mcp(tmp_path):
    for datasheet in (True, False):
        root = tmp_path / ("ds" if datasheet else "nods")
        root.mkdir()
        lib = _parts_lib(root, datasheet)
        ws = _mcp_ws(root)
        p = _call(ws, "propose_rules", {"paths": [str(lib)]})
        assert not p["isError"], p
        d = json.loads(p["content"][0]["text"])
        assert '"Manufacturer Part Number" = "MPN"' in d["toml"], d["toml"]
        assert _save(ws, d["toml"]) == "saved", (datasheet, d["toml"])
        if not datasheet:                    # the library has no datasheet links: Datasheet is kept, and said so
            assert any("Datasheet" in w and "kept" in w for w in d["warnings"]), d["warnings"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
