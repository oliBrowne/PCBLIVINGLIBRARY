"""2026-10-29 independent injection review: each test reproduces a confirmed bug on main c7a276d."""
import json

from ecadlib.app import Workspace
from ecadlib import mcp_server as M
from tests.fixtures_lite import lite
from tests.test_altium_project import write_schdoc, comp, mpn


def _confined_ws(tmp_path):
    outside = tmp_path / "outside_private"
    outside.mkdir()
    write_schdoc(outside / "Secret.SchDoc", [comp("U9", "SecretChip", "SECRETAA", mpn("TOPSECRET-MPN-123", "AcmeCorp"),
                                                  ["QFN-48"], desc="Unreleased product controller")])
    ws = Workspace(str(tmp_path / "ws"), [], None, True)
    ws.mcp_options = M.Options(confine=True)             # `ecadlib mcp --http` default
    return ws


def _call(ws, name, args):
    r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r["result"]["content"][0]["text"]


def _upload_project(ws):
    prj = "[Design]\nVersion=1.0\n[Document1]\nDocumentPath=..\\..\\..\\outside_private\\Secret.SchDoc\n"
    return json.loads(_call(ws, "upload_bom", {"filename": "Board.PrjPcb", "content": prj}))["bom"]


@lite
def test_project_bom_does_not_read_schematics_outside_the_workspace(tmp_path):
    ws = _confined_ws(tmp_path)
    out = _call(ws, "project_bom", {"project": _upload_project(ws)})
    assert "TOPSECRET" not in out and "AcmeCorp" not in out


@lite
def test_where_used_does_not_read_schematics_outside_the_workspace(tmp_path):
    ws = _confined_ws(tmp_path)
    out = _call(ws, "where_used", {"projects": [_upload_project(ws)]})
    assert "TOPSECRET" not in out and "U9" not in out


_SYM = ('(kicad_symbol_lib (version 20231120) (generator x)\n'
        ' (symbol "U1" (property "Reference" "U" (at 0 0 0)) (property "Value" "U1" (at 0 0 0))\n'
        '  (symbol "U1_1_1"\n'
        '   (pin input line (at -5.08 0 0) (length 2.54) (name "EN{sep}ABLE") (number "1"))\n'
        '   (pin output line (at 5.08 0 180) (length 2.54) (name "OUT") (number "2")))))\n')


@lite
def test_altium_recipe_pin_name_with_any_line_break_stays_one_record(tmp_path):
    import warnings
    from ecadlib import sexpr, kicad_import as K, altium_lib as AL, altium_native as AN
    for i, sep in enumerate(("\x0c", "\x0b", "\x1c", "\x85", "\u2028", "\u2029")):   # str.splitlines() breaks on all
        d = tmp_path / str(i)
        d.mkdir()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sym = K.convert_symbol([K._find_symbol(sexpr.parse(_SYM.format(sep=sep)), "U1")], "U1")
            AL.write_schlib(d / "U1.SchLib", [sym])
        pins = AN._recipe_items(d / ("U1.SchLib" + AN.EXT))["U1"]["pins"]
        assert len(pins) == 2, repr(sep)
        lines = [ln for ln in AN.build_text(d).split("\n") if ln.startswith("PIN")]
        assert len({len(ln.split("\t")) for ln in lines}) == 1, repr(sep)


@lite
def test_altium_recipe_pad_number_with_a_form_feed_stays_one_record(tmp_path):
    import warnings
    from ecadlib import kicad_import as K, altium_lib as AL, altium_native as AN
    mod = ('(footprint "X" (version 20221018) (generator x) (layer "F.Cu")\n'
           ' (pad "1\x0cA" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
           ' (pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))\n')
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fp, _ = K.convert_footprint(mod, "X", "test", [], "")
        AL.write_pcblib(tmp_path / "X.PcbLib", [fp])
    assert len(AN._recipe_items(tmp_path / ("X.PcbLib" + AN.EXT))["X"]["pads"]) == 2
    assert not [ln for ln in AN.build_text(tmp_path).split("\n") if ln.startswith("A\t")]


@lite
def test_freeze_keeps_board_footprint_names_inside_the_pretty_folder(tmp_path):
    from pathlib import Path
    from ecadlib import project_check as PC
    pcb = ('(kicad_pcb (version 20240108) (generator "pcbnew")\n'
           '  (footprint "Lib:../../../ESCAPED" (layer "F.Cu") (at 10 10)\n'
           '    (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu")))\n'
           '  (footprint "Lib:CON" (layer "F.Cu") (at 20 10)\n'
           '    (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu"))))\n')
    proj = tmp_path / "work" / "received" / "proj"
    proj.mkdir(parents=True)
    (proj / "board.kicad_pro").write_text("{}")
    (proj / "board.kicad_pcb").write_text(pcb)
    res = PC.freeze_kicad(proj / "board.kicad_pro", rewrite=False)
    mods = [Path(f) for f in res["files"] if str(f).endswith(".kicad_mod")]
    assert len(mods) == 2
    for f in mods:
        assert f.resolve().parent == (proj / "board-frozen.pretty").resolve(), f
    assert sorted(f.stem for f in mods) == [".._.._.._ESCAPED", "CON_"]
    assert not (tmp_path / "work" / "ESCAPED.kicad_mod").exists()


_FP = ('(footprint "{n}" (layer "F.Cu")\n  (pad "1" smd rect (at -{x} 0) (size 1 1) (layers "F.Cu"))\n'
       '  (pad "2" smd rect (at {x} 0) (size 1 1) (layers "F.Cu")))')


@lite
def test_visual_check_proofs_of_two_nopb_parts_do_not_share_a_folder(tmp_path):
    from ecadlib import visual_check as VC
    ws = Workspace(str(tmp_path))
    pl = tmp_path / "librarian" / "b"
    pl.mkdir(parents=True)
    names = ("LM2596S-5.0/NOPB", "LMR14020SDDA/NOPB")
    items = []
    for i, m in enumerate(names):
        (pl / f"fp{i}.kicad_mod").write_text(_FP.format(n=f"FP{i}", x=1 + 2 * i))
        items.append({"name": m, "footprint": {"kicad_mod": f"fp{i}.kicad_mod"}, "parameters": []})
    (pl / "plan.json").write_text(json.dumps(items))
    for m in names:
        ws.visual_check(m, render_only=True)
    proofs = sorted((tmp_path / "agent" / "proofs").rglob("proof.json"))
    assert sorted(json.loads(p.read_text())["part"] for p in proofs) == sorted(names)
    assert VC._slug("CON") != "CON"                      # a Windows device name is no folder
