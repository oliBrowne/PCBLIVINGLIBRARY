"""Library hub (ecadlib.hub): every part once across your libraries and ecadlib's own, a cell per tool, last changed
and by whom, history, fixes; and its web routes in local and team mode."""
import json, os, re, shutil, subprocess, tempfile, threading, urllib.error, urllib.request
from pathlib import Path

from ecadlib import hub as H
from ecadlib.app import Workspace
from ecadlib.firstrun import build_demo

_DEMO = {}


def _demo():
    """The offline sample library (ecadlib demo), built once and copied per test."""
    if "dir" not in _DEMO:
        d = Path(tempfile.mkdtemp(prefix="hubdemo_"))
        build_demo(Workspace(d / "ws", offline=True), "both", log=lambda *a, **k: None)
        _DEMO["dir"] = d / "ws"
    t = Path(tempfile.mkdtemp(prefix="hub_"))
    shutil.copytree(_DEMO["dir"], t / "ws")
    return t


def _mylib(t):
    """A user library made from the demo's output: KiCad .kicad_sym + .pretty, Altium .SchLib + .PcbLib."""
    ws = t / "ws"
    lib = t / "mylib"
    kdir = next((ws / "kicad").iterdir())
    lib.mkdir()
    (lib / "kicad").mkdir()
    shutil.copytree(next(kdir.glob("*.pretty")), lib / "kicad" / "my.pretty")
    sym = next(kdir.glob("*.kicad_sym"))
    (lib / "kicad" / "my.kicad_sym").write_text(sym.read_text(encoding="utf-8").replace(f"{sym.stem}:", "my:"), encoding="utf-8")
    (lib / "altium").mkdir()
    models = next((ws / "librarian").iterdir()) / "models"
    for f in list((models / "PcbLib").glob("*.PcbLib")) + list((models / "SchLib").glob("*.SchLib")):
        shutil.copy(f, lib / "altium" / f.name)
    return lib


def _age(path, days):
    t = os.path.getmtime(path) - days * 86400
    os.utime(path, (t, t))


def _row(res, name):
    return next(r for r in res["rows"] if r["name"] == name)


def _fresh():
    H._BUILT.clear()


def test_ecadlib_parts_show_altium_and_kicad():
    t = _demo()
    ws = Workspace(t / "ws", offline=True)
    _fresh()
    res = ws.hub()
    assert res["total"] == 7, [r["name"] for r in res["rows"]]
    r = _row(res, "AO3401A")
    assert r["origin"] == "ecadlib" and r["status"] == "ready"
    assert r["cells"]["altium"]["state"] == "same" and r["cells"]["kicad"]["state"] == "same", r["cells"]
    assert "altium365" not in r["cells"]
    held = _row(res, "CH340C")
    assert held["status"] == "held" and "held" in held["flags"] and "kicad" not in held["cells"]
    tools = {x["tool"]: x for x in res["tools"]}
    import importlib.util
    assert tools["kicad"]["reader"] and tools["fusion"]["reader"] == bool(importlib.util.find_spec("ecadlib.eagle_export"))
    assert tools["easyeda"]["reader"] == bool(importlib.util.find_spec("ecadlib.easyeda_pro_export"))
    assert tools["fusion"]["parts"] == 0 and tools["easyeda"]["parts"] == 0
    assert res["summary"]["held"] == 1 and res["summary"]["parts"] == 7


def test_your_library_copy_behind_and_different():
    t = _demo()
    lib = _mylib(t)
    f = lib / "kicad" / "my.pretty" / "SOT-23.kicad_mod"
    f.write_text(f.read_text(encoding="utf-8").replace("(size 1.475 0.6)", "(size 1.6 0.6)", 1), encoding="utf-8")
    _age(f, 30)
    _age(lib / "kicad" / "my.kicad_sym", 30)
    ws = Workspace(t / "ws", [str(lib)], offline=True)
    _fresh()
    res = ws.hub()
    r = _row(res, "AO3401A")
    assert r["origin"] == "both"
    assert r["cells"]["kicad"]["state"] == "behind", r["cells"]["kicad"]
    assert "behind" in r["cells"]["kicad"]["note"] and "duplicate" not in r["flags"]
    assert _row(res, "AMS1117-3.3")["cells"]["kicad"]["state"] == "same"
    p = ws.hub_part(r["key"])
    mine = next(c for c in p["copies"] if c["tool"] == "kicad" and c["origin"] == "library")
    assert mine["state"] == "behind" and mine["behind"]["words"] in ("4 weeks", "30 days", "1 months") or mine["behind"]["days"] >= 29
    assert mine["diffs"][0]["what"] == "footprint" and "pad 1 is 1.6 x 0.6 mm" in mine["diffs"][0]["text"]
    assert len(p["pictures"]) == 2 and all(x["svg"].startswith("<svg") for x in p["pictures"])
    assert p["symbol"] and p["symbol"]["pins"] == 3 and "<svg" in p["symbol"]["svg"]
    # ecadlib's own KiCad copy matches, so there is no "Make the KiCad version" (your files are never rewritten)
    assert "kicad" not in {x["action"] for x in p["fixes"]}
    assert p["plan"]["status"] == "ready" and p["plan"]["source"]["footprint"].startswith("standard:")
    # filters: in Altium and KiCad but behind; lacks Altium 365
    assert [x["name"] for x in ws.hub(states=["behind"])["rows"]] == ["AO3401A"]
    assert ws.hub(have=["kicad"], lack=["kicad"])["matched"] == 0
    assert ws.hub(lack=["kicad"])["matched"] == 3          # CH340C (held) and the generic Altium symbols
    assert ws.hub(q="alpha omega")["matched"] == 1
    # a fresh copy that differs is "different", not behind
    _age(f, -31)
    _fresh()
    assert _row(ws.hub(), "AO3401A")["cells"]["kicad"]["state"] == "different"


def test_history_records_changes_with_what_changed():
    t = _demo()
    lib = _mylib(t)
    ws = Workspace(t / "ws", [str(lib)], offline=True)
    _fresh()
    ws.hub()
    assert not H.read_history(ws.root), "the first look records nothing"
    f = lib / "kicad" / "my.pretty" / "SOT-23.kicad_mod"
    f.write_text(f.read_text(encoding="utf-8").replace("(size 1.475 0.6)", "(size 1.6 0.6)", 1), encoding="utf-8")
    _fresh()
    ws.hub()
    ev = [e for e in H.read_history(ws.root) if e.get("what") == "changed"]
    assert len(ev) == 1 and ev[0]["tool"] == "kicad" and ev[0]["detail"] == "footprint pads", ev
    # without the library, nothing is "removed"; with it again, nothing is "added"
    _fresh()
    Workspace(t / "ws", offline=True).hub()
    _fresh()
    ws.hub()
    assert [e["what"] for e in H.read_history(ws.root)] == ["changed"]
    # a removed symbol is
    sym = lib / "altium" / "AO3401A.SchLib"
    sym.unlink()
    _fresh()
    ws.hub()
    assert any(e["what"] == "removed" and e.get("name") == "AO3401A" for e in H.read_history(ws.root))
    p = ws.hub_part("AO3401A")
    assert any(e["what"] == "changed" for e in p["history"])


def test_who_from_git_and_from_web_actions():
    t = _demo()
    lib = _mylib(t)
    env = dict(os.environ, GIT_AUTHOR_NAME="Ann Lee", GIT_AUTHOR_EMAIL="ann@example.com", GIT_COMMITTER_NAME="Ann Lee",
               GIT_COMMITTER_EMAIL="ann@example.com")
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "library"]):
        subprocess.run(["git", "-C", str(lib), *cmd], check=True, env=env, capture_output=True)
    H._GIT.clear()
    _fresh()
    ws = Workspace(t / "ws", [str(lib)], offline=True)
    r = ws.hub_part("AO3401A")
    mine = [c for c in r["copies"] if c["origin"] == "library"]
    assert mine and all(c["updated_by"] == "Ann Lee <ann@example.com>" and c["updated_how"] == "git commit" for c in mine), mine
    # an uncommitted change: the file's date, and nobody named
    f = lib / "altium" / "AO3401A.SchLib"
    with open(f, "ab") as fh:
        fh.write(b"\0")
    _fresh()
    c = next(c for c in ws.hub_part("AO3401A")["copies"] if c["tool"] == "altium" and c["origin"] == "library")
    assert c["updated_how"] == "file saved" and c["updated_by"] is None, c
    # a web-app action just before a change names who did it
    w = Workspace(t / "ws", offline=True)
    plan = next((w.root / "librarian").glob("*/plan.json"))
    bom = H.Scan(w, []).plan_bom(plan)
    H.note_action(w.root, "/api/librarian/run", {"bom": bom}, "Bo Ng <bo@example.com>")
    H.note_action(w.root, "/api/altium365/push", {"bom": bom, "dry_run": True}, "Bo")     # a dry run changes nothing
    for f in plan.parent.glob("models/*/*"):
        os.utime(f, None)
    _fresh()
    c = next(c for c in w.hub_part("AO3401A")["copies"] if c["tool"] == "altium")
    assert c["updated_by"] == "Bo Ng <bo@example.com>" and "filled the library in the web app" in c["updated_how"], c
    assert [e["what"] for e in H.read_history(w.root) if e.get("kind") == "action"] == ["filled the library"]


def test_kicad_fix_and_build_bom():
    t = _demo()
    shutil.rmtree(t / "ws" / "kicad")
    ws = Workspace(t / "ws", offline=True)
    _fresh()
    r = _row(ws.hub(), "AO3401A")
    assert "kicad" not in r["cells"]
    fixes = {f["action"] for f in ws.hub_part(r["key"])["fixes"]}
    assert "kicad" in fixes and "a365_preview" in fixes
    out = ws.hub_fix(r["key"], "kicad", "Ann <ann@example.com>")
    assert out["result"]["folder"].startswith("kicad/")
    _fresh()
    assert _row(ws.hub(), "AO3401A")["cells"]["kicad"]["state"] == "same"
    assert any(e.get("what") == "made the KiCad version" and e.get("who") == "Ann <ann@example.com>" for e in H.read_history(ws.root))
    try:
        ws.hub_fix(r["key"], "kicad")
        raise AssertionError("a fix that is not on offer must be refused")
    except ValueError as e:
        assert "not on offer" in str(e)
    held = _row(ws.hub(), "CH340C")
    assert [f["action"] for f in ws.hub_part(held["key"])["fixes"]] == ["open_plan"]
    dry = ws.hub_fix(r["key"], "a365_preview")
    assert dry["result"]["dry_run"] and [x["name"] for x in dry["result"]["results"]] == ["AO3401A"]
    # a part only in your library: a one-line BOM to build ecadlib's own copy
    lib = t / "lib2" / "my.pretty"
    lib.mkdir(parents=True)
    sym = (t / "lib2" / "parts.kicad_sym")
    sym.write_text('(kicad_symbol_lib (version 20231120) (generator test)\n  (symbol "LM358" (property "Reference" "U" (at 0 0 0))'
                   ' (property "Value" "LM358" (at 0 0 0)) (property "Footprint" "my:SOIC-8" (at 0 0 0))'
                   ' (property "MPN" "LM358DR" (at 0 0 0)) (property "Manufacturer" "TI" (at 0 0 0))))\n', encoding="utf-8")
    ws2 = Workspace(t / "ws", [str(t / "lib2")], offline=True)
    _fresh()
    row = _row(ws2.hub(), "LM358")
    assert row["origin"] == "library" and row["cells"]["kicad"]["state"] == "present"
    assert [f["action"] for f in ws2.hub_part(row["key"])["fixes"]] == ["build", "copy:altium"]
    bom = ws2.hub_build_bom(row["key"])
    text = ws2.inside(bom).read_text(encoding="utf-8")
    assert "LM358DR" in text and "SOIC-8" in text and text.splitlines()[1].startswith("U1,")


def test_other_tool_reader_fills_its_column():
    t = _demo()
    lib = t / "fus"
    lib.mkdir()
    (lib / "parts.lbr").write_text(json.dumps([{"name": "AO3401A", "mpn": "AO3401A", "prefix": "Q",
                                                "pads": [["1", -1.1, 0.95, 1.4, 0.6, 0, False], ["2", -1.1, -0.95, 1.4, 0.6, 0, False],
                                                         ["3", 1.1, 0, 1.4, 0.6, 0, False]],
                                                "pins": [["1", "G"], ["2", "S"], ["3", "D"]]}]), encoding="utf-8")
    _age(lib / "parts.lbr", 1)                     # older than ecadlib's copies, which are then the newest to compare with
    saved = dict(H._READERS)
    try:
        H.register_tool("fusion", "Fusion", (".lbr",), lambda p: json.loads(Path(p).read_text(encoding="utf-8")))
        ws = Workspace(t / "ws", [str(lib)], offline=True)
        _fresh()
        res = ws.hub()
        assert next(x for x in res["tools"] if x["tool"] == "fusion")["reader"]
        cell = _row(res, "AO3401A")["cells"]["fusion"]
        assert cell["state"] == "different" and "footprint" in cell["note"], cell
    finally:
        H._READERS.clear()
        H._READERS.update(saved)


def test_fusion_and_easyeda_columns_from_their_exporters():
    """With eagle-export and easyeda-export in this ecadlib, their .lbr / .elibz fill the Fusion and EasyEDA columns."""
    import importlib.util
    if not (importlib.util.find_spec("ecadlib.eagle_export") and importlib.util.find_spec("ecadlib.easyeda_pro_export")):
        return                                     # exporters not in this build
    t = _demo()
    ws = Workspace(t / "ws", offline=True)
    _fresh()
    acts = [f["action"] for f in ws.hub_part("AO3401A")["fixes"]]
    assert "tool:fusion" in acts and "tool:easyeda" in acts, acts
    assert ws.hub_fix("AO3401A", "tool:fusion")["result"]["file"].endswith(".lbr")
    assert ws.hub_fix("AO3401A", "tool:easyeda")["result"]["file"].endswith(".elibz")
    _fresh()
    res = ws.hub()
    for name in ("AO3401A", "RES-0603-10k"):
        cells = _row(res, name)["cells"]
        assert cells["fusion"]["state"] == "same" and cells["easyeda"]["state"] == "same", (name, cells)
    tools = {x["tool"]: x for x in res["tools"]}
    assert tools["fusion"]["reader"] and tools["fusion"]["parts"] == 6 and tools["easyeda"]["parts"] == 6


def test_compare_rules():
    pads = [("1", 0.0, 0.0, 1.0, 0.5, 0.0, False), ("2", 2.0, 0.0, 1.0, 0.5, 0.0, False)]
    assert H.pad_diff(pads, pads) == ""
    assert H.pad_diff(pads, pads[:1]) == "2 pads, the other has 1"
    assert "pad 2 is at" in H.pad_diff(pads, [pads[0], ("2", 2.5, 0.0, 1.0, 0.5, 0.0, False)])
    # KiCad's "~" pin names and numbers used as names don't count as differences
    assert H.pin_diff([("1", ""), ("2", "")], [("1", "1"), ("2", "2")]) == ""
    assert H.pin_diff([("1", "A"), ("2", "K")], [("1", "K"), ("2", "A")]) == "pin 1 is A here, K in the other"
    c = lambda tool, upd, value="", pads=None: dict(H._copy(tool, "R1", "library", None, value=value, pads=pads), updated=upd)
    cs = [c("altium", "2026-10-01T00:00:00+00:00", "10k"), c("kicad", "2026-09-01T00:00:00+00:00", "4.7k"),
          c("altium365", "2026-10-02T00:00:00+00:00")]
    cells = H.compare(cs, 14)
    assert cells["kicad"]["state"] == "behind" and cells["kicad"]["note"].startswith("4 weeks behind Altium"), cells
    assert cells["altium"]["state"] == "same" and cells["altium365"]["state"] == "present"
    assert H.part_key("LM1117MPX-3.3/NOPB", "x") == H.part_key("LM1117MPX-3.3/NOPB/TR", "y")
    assert H.part_key("", "Resistor") == "name:RESISTOR"


def _serve(ws, team=None):
    from ecadlib.ui import server as S
    srv = S.make_server(ws, port=0, team=team)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _projects(t, ws_dir):
    """A KiCad project whose schematic uses AO3401A and Resistor, an Altium project, and [hub] projects naming both."""
    b = t / "boards"
    (b / "kb").mkdir(parents=True)
    (b / "ab").mkdir()
    (b / "kb" / "kb.kicad_pro").write_text("{}")
    (b / "kb" / "kb.kicad_sch").write_text('(kicad_sch (version 20231120)\n (symbol (lib_id "my:AO3401A") (property "Reference" "Q1"))\n'
                                           ' (symbol (lib_id "old:Resistor") (property "Reference" "R1"))\n'
                                           ' (symbol (lib_id "power:GND") (property "Reference" "#PWR01")))\n')
    (b / "ab" / "ab.PrjPcb").write_text("[Design]\r\nVersion=1.0\r\n\r\n[Document1]\r\nDocumentPath=ab.SchDoc\r\n\r\n"
                                        "[Parameter1]\r\nName=Board\r\nValue=ab\r\n")
    Path(ws_dir).mkdir(parents=True, exist_ok=True)
    (Path(ws_dir) / "ecadlib.toml").write_text(f'[hub]\nprojects = ["{b.as_posix()}"]\n')
    return b


def test_tool_view_lists_one_tools_library():
    t = _demo()
    lib = _mylib(t)
    ws = Workspace(t / "ws", [str(lib)], offline=True)
    _fresh()
    res = ws.hub(tool="kicad")
    kicad = next(x for x in res["tools"] if x["tool"] == "kicad")
    assert res["matched"] == kicad["parts"] and all("kicad" in r["cells"] for r in res["rows"])
    libs = {re.split(r"[\\/]", l["library"])[-1]: l for l in kicad["libraries"]}   # absolute paths are native on Windows
    assert "my.kicad_sym" in libs and libs["my.kicad_sym"]["origin"] == "library" and kicad["updated"], libs
    one = ws.hub(tool="kicad", library=libs["my.kicad_sym"]["library"])
    assert one["matched"] == libs["my.kicad_sym"]["parts"] and all(
        any(p["library"].endswith("my.kicad_sym") for p in r["places"]["kicad"]) for r in one["rows"])
    try:
        ws.hub(tool="orcad")
        raise AssertionError("unknown tool must be refused")
    except ValueError as e:
        assert "unknown tool" in str(e)


def test_copy_altium_to_kicad_into_a_project():
    t = _demo()
    lib = _mylib(t)
    alt = t / "altonly"
    shutil.copytree(lib / "altium", alt)
    b = _projects(t, t / "ws2")
    ws = Workspace(t / "ws2", [str(alt)], offline=True)
    _fresh()
    res = ws.hub()
    assert all("kicad" not in r["cells"] for r in res["rows"])
    kicad = next(x for x in res["tools"] if x["tool"] == "kicad")
    assert kicad["can_copy"] == res["total"] and kicad["missing"] == res["total"]
    prj = {p["name"]: p for p in res["project_list"]}
    assert prj["kb"]["tool"] == "kicad" and prj["kb"]["parts"] == 2 and prj["kb"]["can_copy"] == 2, prj
    assert [r["name"] for r in ws.hub(project=prj["kb"]["path"])["rows"]] == ["AO3401A", "Resistor"]
    part = ws.hub_part("AO3401A")
    assert "copy:kicad" in {f["action"] for f in part["fixes"]}
    try:
        ws.hub_copy(["AO3401A"], "kicad", prj["ab"]["path"])
        raise AssertionError("an Altium project can't take KiCad copies")
    except ValueError as e:
        assert "not a KiCad project" in str(e)
    try:
        ws.hub_copy(["AO3401A"], "kicad", str(t / "elsewhere.kicad_pro"))
        raise AssertionError("only projects under [hub] projects")
    except ValueError as e:
        assert "[hub] projects" in str(e)
    before = {f: f.read_bytes() for f in alt.iterdir()}
    out = ws.hub_copy([r["key"] for r in res["rows"]], "kicad", prj["kb"]["path"], "Ann <ann@example.com>")
    assert len(out["copied"]) == res["total"] and not out["skipped"], out["skipped"]
    assert "kb's sym-lib-table" in out["project"]
    assert {f: f.read_bytes() for f in alt.iterdir()} == before                 # the user's library is only read
    table = (b / "kb" / "sym-lib-table").read_text()
    assert '(name "ecadlib_hub")' in table and "hub/library/kicad/ecadlib_hub.kicad_sym" in table
    assert "ecadlib_hub.pretty" in (b / "kb" / "fp-lib-table").read_text()
    _fresh()
    res = ws.hub()
    r = _row(res, "AO3401A")
    assert r["cells"]["kicad"]["state"] == "same", r["cells"]                  # the converted copy matches the Altium one
    assert r["places"]["kicad"][0]["library"] == "hub/library/kicad/ecadlib_hub.kicad_sym" and r["places"]["kicad"][0]["origin"] == "ecadlib"
    assert r["cells"]["kicad"]["updated_by"] == "Ann <ann@example.com>"
    assert ws.hub_copy(["AO3401A"], "kicad")["skipped"][0]["why"] == "already in KiCad"
    assert any(e.get("what") == "copied to KiCad from Altium" and e.get("key") == "AO3401A" for e in H.read_history(ws.root))


def test_copy_kicad_to_altium_into_a_prjpcb():
    t = _demo()
    lib = _mylib(t)
    kic = t / "kionly"
    shutil.copytree(lib / "kicad", kic)
    b = _projects(t, t / "ws3")
    ws = Workspace(t / "ws3", [str(kic)], offline=True)
    _fresh()
    res = ws.hub()
    ab = next(p for p in res["project_list"] if p["name"] == "ab")
    out = ws.hub_copy(["AO3401A"], "altium", ab["path"])
    assert [c["name"] for c in out["copied"]] == ["AO3401A"] and "added to ab.PrjPcb" in out["project"], out
    text = (b / "ab" / "ab.PrjPcb").read_text()
    assert "[Document2]" in text and "AO3401A.SchLib" in text and "SOT-23.PcbLib" in text and text.index("[Parameter1]") > text.index("[Document2]")
    assert (b / "ab" / "ab.PrjPcb.ecadlib-bak").read_text().count("[Document") == 1
    _fresh()
    r = _row(ws.hub(), "AO3401A")
    assert r["cells"]["altium"]["state"] == "same", r["cells"]
    try:
        ws.hub_copy(["AO3401A"], "fusion")
        raise AssertionError("fusion copies aren't supported")
    except ValueError as e:
        assert "isn't supported" in str(e)


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _post(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": url.split("/api")[0], **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_web_routes_local():
    t = _demo()
    shutil.rmtree(t / "ws" / "kicad")
    ws = Workspace(t / "ws", offline=True)
    _fresh()
    srv, base = _serve(ws)
    try:
        r = _get(base + "/api/hub?lack=kicad&size=2")
        assert r["matched"] == 7 and len(r["rows"]) == 2 and r["page"] == 1
        assert _get(base + "/api/hub?q=AO3401&flags=")["rows"][0]["name"] == "AO3401A"
        p = _get(base + "/api/hub/part?key=AO3401A")
        assert p["name"] == "AO3401A" and p["copies"] and "path" not in p["copies"][0]
        try:
            _get(base + "/api/hub/part?key=nope")
            raise AssertionError("unknown part must be 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
        code, body = _post(base + "/api/hub/fix", {"key": "AO3401A", "action": "kicad", "user": "Ann <ann@example.com>"})
        assert code == 200 and body["action"] == "kicad", body
        code, body = _post(base + "/api/hub/fix", {"key": "AO3401A", "action": "rm -rf"})
        assert code == 400 and "not on offer" in body["error"], (code, body)
        assert any(e.get("who") == "Ann <ann@example.com>" for e in H.read_history(ws.root))
        code, body = _post(base + "/api/hub/copy", {"keys": "AO3401A", "tool": "kicad"})
        assert code == 400 and "keys must be a list" in body["error"], body
        code, body = _post(base + "/api/hub/copy", {"keys": ["AO3401A"], "tool": "altium"})
        assert code == 200 and body["skipped"][0]["why"] == "already in Altium", body
        assert _get(base + "/api/hub?tool=altium&size=1")["rows"][0]["places"]["altium"]
        html = urllib.request.urlopen(base + "/").read().decode()
        assert 'id="hubcard"' in html and 'href="#hubcard"' in html
    finally:
        srv.shutdown()


def test_web_routes_team_roles():
    from ecadlib.ui import server as S
    from tests.test_team import Client, server
    assert S.ROUTES[("GET", "/api/hub")] == "viewer" and S.ROUTES[("POST", "/api/hub/fix")] == "librarian"
    assert S.ROUTES[("POST", "/api/hub/copy")] == "librarian"
    t = _demo()
    shutil.rmtree(t / "ws" / "kicad")
    _fresh()
    with server(Workspace(t / "ws", offline=True)) as (ws, team, port):
        anon = Client(port)
        assert anon.call("GET", "/api/hub")[0] == 401
        vic = Client(port)
        assert vic.login("vic")[0] == 200
        st, r = vic.call("GET", "/api/hub")
        assert st == 200 and r["total"] == 7
        assert vic.call("GET", "/api/hub/part?key=AO3401A")[0] == 200
        assert vic.call("POST", "/api/hub/fix", {"key": "AO3401A", "action": "kicad"})[0] == 403
        assert vic.call("POST", "/api/hub/copy", {"keys": ["AO3401A"], "tool": "kicad"})[0] == 403
        lib = Client(port)
        lib.login("lib")
        st, r = lib.call("POST", "/api/hub/fix", {"key": "AO3401A", "action": "kicad", "user": "someone else"})
        assert st == 200, r
        who = [e["who"] for e in H.read_history(ws.root) if e.get("what") == "made the KiCad version"]
        assert who == ["Lib Rarian <lib@example.com>"], who            # the session's identity, not the typed name
