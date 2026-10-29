"""2026-10-29 superpowers bug hunt (cli): end-to-end command line and MCP server use, hostile BOMs.
Plain functions: python -m tests.run_all test_hunt1029_cli"""
import contextlib, io, json
from pathlib import Path

from tests.fixtures_lite import lite


def _held_plan(d):
    d.mkdir(parents=True, exist_ok=True)
    items = [{"name": "X1", "folder": "Components/Crystals", "description": "X1 desc", "comment": "=Value",
              "parameters": [], "cleared": [], "status": "held",
              "symbol": {"ref": None, "file": None}, "footprint": {"ref": "C_0402_1005Metric", "file": None},
              "hold_reasons": ["files: no symbol model (.SchLib)"]}]
    (d / "plan.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    return d / "plan.json"


# ---------------------------------------------------------------- cli-01: every part held -> no DbLib written
@lite
def test_dblib_sync_with_every_part_held_does_not_claim_a_dblib_it_never_wrote(tmp_path):
    # `ecadlib dblib-sync plan --to lib` with no ready part wrote nothing (the folder was not even made), yet printed
    # "written: lib/ecadlib.DbLib  open this in Altium" and exited 0
    from ecadlib import altium_dblib_sync as S
    plan = _held_plan(tmp_path / "p")
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = S.main([str(plan), "--to", str(tmp_path / "lib")])
        except SystemExit as e:
            code = e.code
    text = out.getvalue() + err.getvalue()
    assert not (tmp_path / "lib" / "ecadlib.DbLib").exists()
    assert "open this in Altium" not in text, text
    assert "nothing written" in text, text
    assert code not in (0, None), (code, text)


@lite
def test_export_altium_dblib_with_every_part_held_answers_instead_of_errno(tmp_path):
    # MCP export_altium_dblib / the web UI's DbLib export listed altium/<BOM>/ after the sync: with every part held
    # the folder was never made and the answer was "[Errno 2] No such file or directory: <full path>"
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "w", offline=True)
    bom = ws.root / "uploads" / "b.csv"
    bom.write_text("Designator,Qty,Value,Manufacturer,MPN\nY1,1,16MHz,Acme,X1\n", encoding="utf-8")
    _held_plan(ws._librarian_dir(bom))
    r = ws.export_dblib("uploads/b.csv")
    assert r["files"] == [] and r["held"] == ["X1"] and r["written"] == 0, r


def _ws_with_held_library(tmp_path):
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "w", offline=True)
    (ws.root / "uploads" / "b.csv").write_text("Designator,Qty,Value,Footprint,Manufacturer,MPN\n"
                                               "R1,1,10k,R_0603_1608Metric,UNI-ROYAL,0603WAF1002T5E\n", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = ws.librarian_run("uploads/b.csv", sources=["standard"], ai=False, offline=True)
    assert r["held"] >= 1 and not r.get("ready"), r       # no datasheet link offline: held
    return ws


# ---------------------------------------------------------------- cli-02: tool_packages with every part held
@lite
def test_tool_packages_include_held_returns_the_kicad_zip_when_altium_has_nothing_released(tmp_path):
    # MCP tool_packages include_held=true wrote the KiCad zip with the held parts, then the Altium side (release-checked
    # parts only) raised "nothing to package: all 1 part(s) are held" and the whole answer was that error: the zip
    # written on disk was never named
    ws = _ws_with_held_library(tmp_path)
    r = ws.tool_packages("uploads/b.csv", include_held=True)
    assert r["kicad"]["zip"] and (ws.root / r["kicad"]["zip"]).is_file(), r
    assert r["kicad"]["footprints"] >= 1, r
    assert r["altium"]["libpkg"] is None and any("held" in p for p in r["altium"]["problems"]), r


# ---------------------------------------------------------------- cli-03: a BOM named like a Windows device
@lite
def test_default_librarian_folder_for_a_bom_named_aux_is_no_windows_device_name(tmp_path, monkeypatch):
    # aux.csv (the auxiliary board's BOM), con.xlsx, "rev2..csv": the librarian's default -o was <ws>/librarian/aux,
    # a folder Windows cannot make (AUX is a device), and "rev2." whose trailing dot Windows drops (another folder)
    from ecadlib import firstrun as FR, bom_triage as T
    from ecadlib.fsutil import DEVICE_NAMES
    monkeypatch.setenv("ECADLIB_WORKSPACE", str(tmp_path / "ws"))
    for bom in ("aux.csv", "CON.xlsx", "Com1.csv", "rev2..csv", "board .csv"):
        for got in (FR.default_out("librarian", bom), T._lib_out(bom)):
            leaf = Path(got).name
            assert leaf.split(".")[0].rstrip(" ").upper() not in DEVICE_NAMES, (bom, got)
            assert not leaf.endswith((".", " ")), (bom, got)
        tri = Path(FR.default_out("triage", bom)).name
        assert not tri.endswith((".", " ")), (bom, tri)


# ---------------------------------------------------------------- cli-04: "next:" commands that break when pasted
def _next_commands(text):
    lines = text.splitlines()
    i = next(k for k, l in enumerate(lines) if l.strip() == "next:")
    return [l.split("  # ")[0].strip() for l in lines[i + 1:] if l.startswith("  ecadlib ")]


@lite
def test_next_commands_quote_a_bom_or_folder_name_with_spaces_and_brackets(tmp_path, monkeypatch):
    # triage of "My Board (rev 2).csv" suggested `ecadlib librarian My Board (rev 2).csv -o ...`: pasted into a shell
    # that is a syntax error (bash) or three arguments; the librarian's next commands had the same problem for -o
    import shlex, types
    from ecadlib import bom_triage as T, librarian as L, term as TM
    monkeypatch.setenv("ECADLIB_WORKSPACE", str(tmp_path / "my ws"))
    buf = io.StringIO()
    a = types.SimpleNamespace(bom="My Board (rev 2).csv", out=str(tmp_path / "reports" / "t"), offline=True)
    T.print_result([], {}, a, t=TM.Term(buf, styled=False, width=200))
    buf2 = io.StringIO()
    L.print_result([{"name": "X1", "status": "held", "hold_reasons": []}], tmp_path / "my lib (a)", t=TM.Term(buf2, styled=False, width=300))
    for text in (buf.getvalue(), buf2.getvalue()):
        cmds = _next_commands(text)
        assert cmds, text
        for c in cmds:
            words = shlex.split(c)
            assert not any(w in ("(rev", "2).csv", "(a)/plan.json") or w.startswith("(") for w in words), (c, words)
    assert "My Board (rev 2).csv" in shlex.split(_next_commands(buf.getvalue())[0]), buf.getvalue()


@lite
def test_review_fix_commands_quote_a_part_number_with_brackets(tmp_path):
    # `ecadlib review` printed "to fix: ecadlib release-check plan.json --allow SM04B-SRSS-TB(LF)(SN)" (a part of the
    # bundled sample BOM): bash refuses it ("syntax error near unexpected token `('")
    import shlex
    from ecadlib import review_cli as R
    item = {"name": "SM04B-SRSS-TB(LF)(SN)", "hold_reasons": ["fields: no datasheet link"]}
    plan = tmp_path / "my lib" / "plan.json"
    cmds = R.fix_commands(plan, item, [("symbol", {"id": "kicad:Connector:Conn_01x04"})])
    assert cmds, cmds
    for c in cmds:
        words = shlex.split(c)
        assert str(plan) in words and "SM04B-SRSS-TB(LF)(SN)" in words, (c, words)
        assert "'SM04B-SRSS-TB(LF)(SN)'" in c or '"SM04B-SRSS-TB(LF)(SN)"' in c, c     # quoted: bash reads ( as syntax


# ---------------------------------------------------------------- cli-05: fp-edit --part spelled another way
def _datasheet_answer(base, name):
    import re
    d = base / "datasheet"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.+-]", "_", name)
    (d / f"{safe}.json").write_text(json.dumps({"name": name, "footprint": "PKG-4", "pins": [{"num": "1", "name": "A"}]}))
    (d / "PKG-4.json").write_text(json.dumps({"name": "PKG-4", "package_type": "custom"}))
    (base / "plan.json").write_text(json.dumps([{"name": name, "status": "held"}]))
    return base / "plan.json"


@lite
def test_fp_edit_keys_the_edit_by_the_plans_part_name(tmp_path):
    # fp-edit found the answer file by the --part spelling's file name (ABC_DEF_12.json for ABC/DEF:12, and any case
    # on Windows), then saved the edit under that spelling: the librarian looks the edit up by the plan's name, so the
    # rename the person made was never applied (and `show` said "run the librarian again to build the part")
    from ecadlib import fp_edit as FE
    for name, typed in (("ABC/DEF:12", "ABC_DEF_12"), ("CH340C", "ch340c")):
        base = tmp_path / typed
        plan = _datasheet_answer(base, name)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            FE.main([str(plan), "--part", typed, "rename", "1=2"])
        edits = FE.load(base)
        assert FE.for_part(edits, name), (name, edits)
        assert typed == name or typed not in edits, (name, edits)


# ---------------------------------------------------------------- cli-06: approve_footprint named like a device
@lite
def test_approve_footprint_named_con_writes_no_windows_device_file(tmp_path):
    # MCP approve_footprint / the web UI with a spec named CON wrote footprints/ecadlib.pretty/CON.kicad_mod and
    # altium_models/PcbLib/CON.PcbLib: on Windows that opens the console device, not a file
    from ecadlib.app import Workspace
    from ecadlib.fsutil import DEVICE_NAMES
    ws = Workspace(tmp_path / "w", offline=True)
    spec = dict(next(iter(ws.example_specs().values())))
    for nm in ("CON", "nul", "COM1.x"):
        r = ws.approve_footprint(dict(spec, name=nm))
        for f in r["files"]:
            stem = Path(f).name.split(".")[0]
            assert stem.upper() not in DEVICE_NAMES, (nm, f)
            assert (ws.root / f).is_file(), f
        assert ws.preview_footprint(dict(spec, name=nm))["exists"], nm     # found again under the name it was saved as


# ---------------------------------------------------------------- PR #212 review: CON_ file, CON drawing inside
@lite
def test_approve_footprint_named_con_names_the_drawing_inside_as_the_file(tmp_path):
    # the files were CON_.kicad_mod and CON_.PcbLib, but the footprint inside both was still CON: the index finds
    # ecadlib:CON_, the plan asks Altium for drawing CON_ in CON_.PcbLib, and that drawing is not there
    from ecadlib import altium_lib as AL, sexpr
    from ecadlib.app import Workspace
    from ecadlib.kicad_index import KiCadIndex
    ws = Workspace(tmp_path / "w", offline=True)
    spec = dict(next(iter(ws.example_specs().values())))
    for nm in ("CON", "my part"):
        r = ws.approve_footprint(dict(spec, name=nm))
        kmod = next(ws.root / f for f in r["files"] if f.endswith(".kicad_mod"))
        inner = sexpr.parse(kmod.read_text(encoding="utf-8"))[1]
        assert inner == kmod.stem == r["name"], (nm, inner, kmod.stem, r["name"])
        pcb = next(ws.root / f for f in r["files"] if f.endswith(".PcbLib"))
        assert [fp["name"] for fp in AL.read_pcblib(pcb)] == [pcb.stem] == [r["name"]], (nm, pcb)
        assert f"{kmod.parent.stem}:{r['name']}" in KiCadIndex([kmod.parent.parent]).footprints


# ---------------------------------------------------------------- review of the cli hunt: hints still unquoted
def _marked(mod, name="shell_word"):
    from unittest import mock
    return mock.patch.object(mod, name, lambda s: f"<{s}>")


def test_part_show_and_adopt_hints_quote_the_plan_and_part():
    # `ecadlib part show` / `part adopt` printed `ecadlib part approve <plan> --part SM04B-SRSS-TB(LF)(SN)` unquoted:
    # pasted into a shell, the brackets (and a folder with a space) break the command
    from ecadlib import change_cli as CC
    from tests.test_change_control import ANN, demo, item, items_of, write_symbol
    from tests.test_change_control_workflow import SGD, held_hand_edit, run_cli
    from tests.test_change_control_round2 import _cut_part, _lose_ledger, _two_approvers
    plan, lib = demo()
    held_hand_edit(plan, lib, SGD)
    with _marked(CC, "q"):
        rc, out = run_cli("show", lib, "AO3401A")
    assert rc == 0 and "part approve <" in out and "--part <AO3401A>" in out, out
    plan, lib = demo()
    _two_approvers(lib)
    _cut_part(lib, "AO3401A")
    write_symbol(lib, item(items_of(plan), "AO3401A"), SGD)
    with _marked(CC, "q"):
        rc, out = run_cli("adopt", lib, "--part", "AO3401A", "-m", "checked", "--user", ANN, "--accept-changes")
    assert "part show <" in out and "<AO3401A>" in out, (rc, out)
    plan, lib = demo()
    _lose_ledger(lib)
    write_symbol(lib, item(items_of(plan), "AO3401A"), SGD)
    with _marked(CC, "q"):
        rc, out = run_cli("adopt", lib, "--all", "-m", "checked", "--user", ANN)
    assert rc == 1 and "part adopt <" in out and "--part <AO3401A>" in out, (rc, out)


def test_lost_ledger_hint_quotes_the_library_and_part():
    from ecadlib import change_gate as CG, ledger as LG
    from tests.test_change_control import demo, item, items_of, write_symbol
    from tests.test_change_control_workflow import SGD
    plan, lib = demo()
    (lib / LG.LEDGER).unlink()
    items = items_of(plan)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    try:
        with _marked(CG):
            CG.publish_guard(plan, items, "kicad")
        raise AssertionError("a lost ledger must refuse")
    except LG.LedgerError as e:
        assert "part adopt <" in e.hint and "--part <" in e.hint, e.hint


def test_resolve_held_change_notes_quote_the_plan_and_part():
    # `resolve choose` on a released part: "it waits as a held change (ecadlib part show <plan> <part>)" and
    # "ecadlib part approve <plan> --part <part>" were printed unquoted
    from ecadlib import resolve as R
    from tests.fixtures_lite import scratch
    from tests.test_change_control import ANN, demo
    for approvers in (1, 2):
        plan, lib = demo()
        if approvers == 2:
            (lib / "ecadlib.toml").write_text("[change]\napprovers = 2\n", encoding="utf-8")
        with _marked(R):
            res = R.choose(plan, "AO3401A", "footprint", "spec:SOT-23-5", src=R.Sources(cache=scratch(), offline=True),
                           user=ANN)
        notes = " ".join(res["notes"])
        assert ("part show <" if approvers == 1 else "part approve <") in notes and "<AO3401A>" in notes, notes


def test_shell_word_quotes_cmd_specials_on_windows():
    # on Windows shell_word used subprocess.list2cmdline, which quotes only blanks: a folder R&D or a part
    # SM04B-SRSS-TB(LF)(SN) stayed bare, and cmd.exe ran `ecadlib review R` and then a command named D\plan.json
    from ecadlib.cliutil import shell_word
    assert shell_word("R&D\\board\\plan.json", nt=True) == '"R&D\\board\\plan.json"'
    assert shell_word("SM04B-SRSS-TB(LF)(SN)", nt=True) == '"SM04B-SRSS-TB(LF)(SN)"'
    assert shell_word("a|b<c>d^e", nt=True) == '"a|b<c>d^e"'
    assert shell_word("C:\\My Parts\\", nt=True) == '"C:\\My Parts\\\\"'      # a closing \" would escape the quote
    assert shell_word("C:\\libs\\plan.json", nt=True) == "C:\\libs\\plan.json"
    assert shell_word("My Board (rev 2).csv", nt=False) == "'My Board (rev 2).csv'"
