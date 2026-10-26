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


# ---- R-FX11-3: an approval recorded with a version-1 hash is upgraded on its first match, so it covers the .kicad_mod
def _v1_approved(d):
    from ecadlib import approvals as AP, release_check as RC
    from tests.test_release_check import make
    it = make(d)
    (d / "approvals.json").write_text(json.dumps({"required": 1}))
    RC.apply([it], d)
    pol = AP.load_policy(d)
    AP.decide(it, "approve", "Ann Lee <ann@example.com>", base=d, policy=pol)
    AP.refresh_status(it, pol, d)
    old = AP.content_hash(it, d, version=1)          # as an ecadlib before the .kicad_mod was hashed stored them
    for r in it["approvals"]:
        r["hash"] = old
    it["approval"]["checked_hash"] = old
    return it, pol


def _edit_pads(d, it):
    from tests.test_release_check import SOIC
    (d / it["footprint"]["kicad_mod"]).write_text(SOIC.replace("(size 1.965 0.58)", "(size 1.965 0.30)"))


@lite
def test_fx11_3_v1_approval_upgraded_then_kicad_mod_edit_is_stale(tmp_path):
    from ecadlib import approvals as AP
    it, pol = _v1_approved(tmp_path)
    ev = AP.evaluate(it, pol, tmp_path)
    assert ev["met"], ev                                     # the old sign-off still counts on the files it covered
    assert all(str(r["hash"]).startswith("v2:") for r in it["approvals"]), it["approvals"]
    _edit_pads(tmp_path, it)
    ev = AP.evaluate(it, pol, tmp_path)
    assert not ev["met"] and ev["stale"], ev                 # and now an edit of the .kicad_mod makes it stale
    assert AP.push_block(it, pol, tmp_path), "edited .kicad_mod published on a pre-upgrade sign-off"


@lite
def test_fx11_3_v1_release_check_snapshot_upgraded(tmp_path):
    from ecadlib import approvals as AP
    it, pol = _v1_approved(tmp_path)
    assert AP.push_block(it, pol, tmp_path) is None
    assert str(it["approval"]["checked_hash"]).startswith("v2:"), it["approval"]
    for r in it["approvals"]:                                # a fresh v2 approval: only the snapshot is old
        r["hash"] = AP.content_hash(it, tmp_path)
    _edit_pads(tmp_path, it)
    assert "changed since the release check" in (AP.push_block(it, pol, tmp_path) or ""), AP.push_block(it, pol, tmp_path)


# ---- R-FX11-4: every publisher checks the sign-off policy in force now, not only the release check's snapshot
@lite
def test_fx11_4_policy_added_after_release_check_holds_kicad_export(tmp_path):
    import contextlib, io
    from ecadlib import kicad_export as KE, release_check as RC
    from tests.test_release_check import make
    d = tmp_path
    it = make(d)
    RC.apply([it], d)
    assert it["status"] == "ready"
    (d / "approvals.json").write_text(json.dumps({"required": 1}))
    why = RC.publish_problems(it, d, writes_files=True)
    assert any(w.startswith("sign-off: awaiting approval") for w in why), why
    assert RC.publish_problems(it, d, writes_files=True, policy={}) == []      # an explicit "no policy" still wins
    (d / "plan.json").write_text(json.dumps([it], indent=2))
    with contextlib.redirect_stdout(io.StringIO()):
        rep = KE.export(str(d / "plan.json"), str(d / "kicad"), "lib", log=lambda *a, **k: None)
    assert not rep["parts"] and [s["part"] for s in rep["skipped"]] == ["PART1"], rep


@lite
def test_fx11_4_unreadable_policy_holds(tmp_path):
    from ecadlib import release_check as RC
    from tests.test_release_check import make
    it = make(tmp_path)
    RC.apply([it], tmp_path)
    (tmp_path / "approvals.json").write_text("{not json")
    why = RC.publish_problems(it, tmp_path, writes_files=True)
    assert any(w.startswith("sign-off:") and "approvals.json" in w for w in why), why


# ---- R-FX11-5: [footprint] pin1 and [layers] courtyard are gates over MCP; a one-way tightening of a gate saves
@lite
def test_fx11_5_pin1_and_courtyard_cannot_be_loosened(tmp_path):
    for label, text in (("pin1 accepts any name", '[footprint]\npin1 = ["1", "A1", "2", "3"]\n'),
                        ("courtyard on the silkscreen", '[layers]\ncourtyard = "TopOverlay"\n')):
        root = tmp_path / label.replace(" ", "_")
        root.mkdir()
        r = _save(_mcp_ws(root), text)
        assert r.startswith("REFUSED") and "save_rules cannot change" in r, (label, r)
    root = tmp_path / "fewer_pin1"
    root.mkdir()
    assert _save(_mcp_ws(root), '[footprint]\npin1 = ["1"]\n') == "saved"        # fewer names: stricter


@lite
def test_fx11_5_tightening_saves_loosening_is_refused(tmp_path):
    start = ('[release]\nparity = "warn"\nheight = "warn"\n[release.approvals]\nrequired = 1\nroles = ["librarian"]\n'
             '[change]\ngeometry_minor = "warn"\napprovers = 1\n')
    tighter = {
        "height warn->hold": start.replace('height = "warn"', 'height = "hold"'),
        "parity warn->hold": start.replace('parity = "warn"', 'parity = "hold"'),
        "approvals 1->2": start.replace("required = 1", "required = 2"),
        "approvals one more role": start.replace('["librarian"]', '["librarian", "hardware"]'),
        "change warn->hold": start.replace('geometry_minor = "warn"', 'geometry_minor = "hold"'),
        "change approvers 1->2": start.replace("approvers = 1", "approvers = 2"),
    }
    looser = {
        "parity warn->off": start.replace('parity = "warn"', 'parity = "off"'),
        "approvals 1->0": start.replace("required = 1", "required = 0"),
        "approvals role dropped": start.replace('["librarian"]', "[]"),
        "approvals removed": start.replace('[release.approvals]\nrequired = 1\nroles = ["librarian"]\n', ""),
        "approvals only --allow parts": start.replace("required = 1", 'required = 1\napplies_to = "allowed"'),
        "change warn->pass": start.replace('geometry_minor = "warn"', 'geometry_minor = "pass"'),
        "change approvers 1->... removed": start.replace("approvers = 1\n", 'electrical = "warn"\n'),
    }
    for i, (label, text) in enumerate([*tighter.items(), *looser.items()]):
        root = tmp_path / f"c{i}"
        root.mkdir()
        r = _save(_mcp_ws(root, start), text)
        if label in tighter:
            assert r == "saved", (label, r)
        else:
            assert r.startswith("REFUSED") and "save_rules cannot change" in r, (label, r)
    for i, (label, text) in enumerate((("no policy -> 2", '[release.approvals]\nrequired = 2\n'),
                                       ("height default warn -> hold", '[release]\nheight = "hold"\n'))):
        root = tmp_path / f"fresh{i}"
        root.mkdir()
        assert _save(_mcp_ws(root), text) == "saved", label
    root = tmp_path / "fresh_ws"
    root.mkdir()
    r = _save(_mcp_ws(root), '[altium365]\nworkspace = "https://mine.365.altium.com"\n')
    assert r.startswith("REFUSED"), r                # where a real push goes is not a tightening: a person sets it


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
