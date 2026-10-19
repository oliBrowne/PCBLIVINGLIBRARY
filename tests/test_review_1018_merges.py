"""Regression tests for what testing the PRs merged on 2026-10-17/18 (#81-#103) found by running them. Run:
    python3 -m tests.run_all test_review_1018_merges"""
import csv, json

from ecadlib import altium365 as A365, altium_export as AE, altium_menu as AM, fp_share as FS, librarian as L
from ecadlib import migrate as MG, timing as TI, ws_status as WS
from tests.fixtures_lite import lite


# ------------------------------------------------------------------ #84: A365 names
def test_a365_name_match_prefers_the_exact_item_and_skips_a_sibling_part():
    """components(name eq "LM358") also returns a colleague's LM358-N: updating it overwrote that part's MPN."""
    sib, own = {"id": "cmp-colleague", "name": "LM358-N"}, {"id": "cmp-own", "name": "LM358"}
    assert A365.pick_named([sib], "LM358") is None
    assert A365.pick_named([sib, own], "LM358") is own
    assert A365.pick_named([{"id": "r", "name": "AMS1117-3.3-1"}], "AMS1117-3.3")["id"] == "r"     # a revision name
    assert A365.pick_named([{"id": "r", "name": "Capacitor-A.2"}], "Capacitor")["id"] == "r"
    assert A365.pick_named([{"id": "s", "name": "CMP-017-000002-1"}], "AMS1117-3.3") is None
    assert A365.pick_named([own], "") is None


# ------------------------------------------------------------------ #85: run timing
def _status_line(folder):
    lr = TI.last_run(folder)
    s = WS.status(folder)
    s["last_run"] = dict(lr, plan="plan.json")
    return lr, WS.render(s)


@lite
def test_a_failed_push_is_not_reported_as_released(tmp_path):
    TI.record(tmp_path, "triage", 1000, 1001, new_run=True)
    TI.record(tmp_path, "release_check", 1001, 1002)
    TI.record(tmp_path, "push", 1003, 1004, released=0, errors=1)
    lr, text = _status_line(tmp_path)
    assert lr["push_errors"] == 1, lr
    assert "BOM to push, 1 failed" in text and "BOM to released" not in text, text


@lite
def test_an_interrupted_run_keeps_the_last_finished_run_and_its_review(tmp_path):
    TI.record(tmp_path, "triage", 1000, 1001, new_run=True)
    TI.record(tmp_path, "build", 1001, 1003)
    TI.record(tmp_path, "release_check", 1003, 1004)
    TI.record(tmp_path, "push", 1010, 1011, released=3, errors=0)
    TI.record(tmp_path, "triage", 2000, 2001, new_run=True)          # killed right after triage: no plan of its own
    assert TI.mark_review(tmp_path, "unallow") is not None             # a later decision still counts
    lr = TI.last_run(tmp_path)
    assert set(lr["steps"]) == {"triage", "build", "release_check", "push", "review"}, lr
    assert lr["push_errors"] == 0


@lite
def test_total_time_never_goes_negative_when_the_clock_steps_back(tmp_path):
    TI.record(tmp_path, "triage", 5000, 1400, new_run=True)
    assert TI.last_run(tmp_path)["total_seconds"] == 0.0


# ------------------------------------------------------------------ #92: NOTICE.txt
@lite
def test_a_notice_you_wrote_is_never_overwritten(tmp_path):
    mine = "Copyright 2026 Acme Corp. Internal library.\n"
    (tmp_path / "NOTICE.txt").write_text(mine, encoding="utf-8")
    items = [{"source": {"footprint": "kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}}]
    assert L.write_notice(tmp_path, items) is None
    assert (tmp_path / "NOTICE.txt").read_text(encoding="utf-8") == mine
    (tmp_path / "NOTICE.txt").unlink()
    assert L.write_notice(tmp_path, items) is not None                # ecadlib's own notice is still written


# ------------------------------------------------------------------ #81: DbLib tables
def test_an_ic_with_integrated_mosfets_stays_in_the_ics_table():
    assert AE.kind_of(["3-17V 3A step-down converter with integrated MOSFETs"], ["VQFN-16-1EP_3x3mm_P0.5mm"]) != "Transistors"
    assert AE.kind_of(["Dual H-bridge motor driver, integrated MOSFETs"], ["HTSSOP-16"]) != "Transistors"
    assert AE.kind_of(["N-Channel SOT-23 MOSFETs ROHS"], ["SOT-23"]) == "Transistors"


@lite
def test_a_wrong_lcsc_hint_is_not_a_bad_mpn(tmp_path):
    """EasyEDA is asked by LCSC number only: its "no such part" says nothing about the MPN."""
    t = tmp_path / "triage.csv"
    cols = ["mpn", "status", "src", "notes", "local_symbol", "local_footprint", "src_mpn", "src_description"]
    def write(**row):
        with t.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, cols)
            w.writeheader()
            w.writerow({**{c: "" for c in cols}, "mpn": "TPS62162DSGR", "status": "needs_footprint_generation", **row})
    item = {"status": "held"}
    write(src="easyeda")
    assert not AM.not_found(t, "TPS62162DSGR", item)
    write(src="nexar")
    assert AM.not_found(t, "TPS62162DSGR", item)


# ------------------------------------------------------------------ #97: hashes recorded before it
@lite
def test_upgrading_recomputes_footprint_hashes_so_a_rerun_shares(tmp_path):
    mod = tmp_path / "footprints" / "R_0603.kicad_mod"
    mod.parent.mkdir()
    mod.write_text('(footprint "R_0603" (pad "1" smd roundrect (at -0.8 0) (size 0.8 0.95) (layers "F.Cu"))'
                   ' (pad "2" smd roundrect (at 0.8 0) (size 0.8 0.95) (layers "F.Cu")))', encoding="utf-8")
    state = {"RES-0603-10k": {"footprint": {"name": "R_0603", "hash": "old-algorithm", "kicad_mod": "footprints/R_0603.kicad_mod"}},
             "GONE": {"footprint": {"name": "X", "hash": "old", "kicad_mod": "footprints/missing.kicad_mod"}}}
    out = MG.migrate_data("librarian_state", 2, json.loads(json.dumps(state)), tmp_path)
    assert out["RES-0603-10k"]["footprint"]["hash"] == FS.digest(mod.read_text(encoding="utf-8"))
    assert "hash" not in out["GONE"]["footprint"]
    assert MG.KINDS["librarian_state"].current == 3


# ------------------------------------------------------------------ #89 + #94: MCP
def test_an_agent_cannot_change_where_the_stored_key_is_sent():
    """save_rules could point [ai] base_url at any server; the next AI step sent the stored key there."""
    import tempfile
    from pathlib import Path
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    from tests.test_mcp_clients import _call, _text
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        ws.mcp_options = M.Options()
        for toml in ("[ai]\nprovider = 'openai-compatible'\nbase_url = 'http://127.0.0.1:18999/v1'\n",
                     "[ai]\napi_key_env = 'ANTHROPIC_API_KEY'\n", "[datasheet]\nbase_url = 'https://evil.example/v1'\n"):
            r = _call(ws, "save_rules", {"toml": toml, "overwrite": True})["result"]
            assert r["isError"] and "cannot change" in _text({"result": r}), (toml, r)
        assert not Path(d, "ecadlib.toml").exists()
        r = _call(ws, "save_rules", {"toml": "[style]\nprofile = 'styles/house.json'\n", "overwrite": True})["result"]
        assert not r.get("isError"), r                                   # other settings still save


def test_mcp_jobs_are_one_list_under_concurrent_first_calls():
    import tempfile, threading
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        seen, go = [], threading.Event()

        def first():
            go.wait()
            seen.append(M._jobs(ws))
        ts = [threading.Thread(target=first) for _ in range(16)]
        for t in ts:
            t.start()
        go.set()
        for t in ts:
            t.join()
        assert len({id(j) for j in seen}) == 1


def test_confined_altium_menu_takes_a_windows_folder():
    """win_dir is the folder on the Altium PC, only written into the menu's text: never a path opened here."""
    from ecadlib import mcp_server as M
    assert "win_dir" not in M.PATH_ARGS


# ------------------------------------------------------------------ #86, #102: datasheet reads and the benchmark
def test_a_sot223_pad_table_keeps_its_body_along_the_pin_row():
    """#102 turned a pad-table body when the pads spread further across than along: SOT-223's 6.5 mm body came out
    3.5 mm wide along its pins and the courtyard was narrower than the body."""
    from ecadlib import footprint_bench as B, footprint_gen as F
    pads = [{"number": n, "x": x, "y": 3.15, "w": 0.95, "h": 2.0} for n, x in ((1, -2.3), (2, 0), (3, 2.3))]
    spec = {"name": "SOT-223-3_TabPin2", "package_type": "custom", "pin_count": 3, "pitch": 2.3, "height_max": 1.8,
            "body_length": {"nom": 6.5}, "body_width": {"nom": 3.5},
            "pad_table": {"unit": "mm", "y_axis": "down", "view": "top", "origin": "center",
                          "pads": pads + [{"number": 2, "x": 0, "y": -3.15, "w": 3.25, "h": 2.0}]}}
    txt, _ = F.build(spec, "B")
    cy = B.numbers(txt)["courtyard"]
    xs = [v for v in (cy if isinstance(cy, (list, tuple)) else cy.values()) if isinstance(v, (int, float))]
    assert max(abs(v) for v in xs) >= 3.25, cy                         # covers the 6.5 mm body along x


def test_an_erratum_does_not_hide_a_wrong_pin_it_does_not_list():
    from ecadlib.eval import runner as R, score as S
    ref = [("1", "VDD"), ("2", "OUTPUT"), ("3", "GND")]
    er = R.load_errata("kicad-v1")["AH1806-W"]
    for mine, want in (([("1", "VDD"), ("2", "GND"), ("3", "OUTPUT")], True),
                       ([("1", "GND"), ("2", "GND"), ("3", "OUTPUT")], False)):
        sc = {"fields": S.score_symbol(mine, ref)}
        sc["errors"] = [f"{k}: {f['error']}" for k, f in sc["fields"].items() if not f["ok"]]
        sc["pass"] = S.verdict(sc["fields"])
        R.apply_errata(sc, mine, er, ref)
        assert sc["fields"]["symbol_names"]["ok"] is want, (mine, sc)


def test_a_land_narrower_than_the_leads_is_not_counted_as_the_makers():
    from ecadlib import footprint_bench as B, footprint_gen as F
    from ecadlib.eval import score as S
    spec = {"name": "SO-14", "package_type": "gullwing", "pin_count": 14, "pitch": 1.27,
            "body_length": {"min": 8.55, "max": 8.75}, "body_width": {"min": 3.8, "max": 4.0},
            "lead_span": {"min": 5.8, "max": 6.2}, "terminal_length": {"min": 0.4, "max": 1.27},
            "terminal_width": {"min": 0.31, "max": 0.51}, "height_max": 1.75}
    good = dict(spec, land={"pad_length": 1.55, "pad_width": 0.6, "pad_center": 2.7})
    bad = dict(spec, land={"pad_length": 0.8, "pad_width": 0.3, "pad_center": 2.6})
    assert S.maker_land(B.numbers(F.build(good, "B")[0]), good)["followed"]
    assert not S.maker_land(B.numbers(F.build(bad, "B")[0]), bad)["followed"]


@lite
def test_fp_edit_reads_a_parts_latest_spec(tmp_path):
    from ecadlib import datasheet as D, fp_edit as E
    real = D.report
    D.report = lambda res: "x"
    try:
        def res(part, w):
            return {"spec": {"name": "SOT-23", "package_type": "gullwing", "terminal_width": w},
                    "symbol": {"name": part, "footprint": "SOT-23", "pins": [{"num": "1", "name": "A"}]}, "sidecar": {}}
        for p, w in (("PARTA", 0.4), ("PARTB", 0.5), ("PARTB", 0.4)):
            D.write(res(p, w), tmp_path / "datasheet")
        assert E.read(tmp_path, "PARTB")["spec"]["terminal_width"] == 0.4
    finally:
        D.report = real


def test_other_json_given_as_an_object_is_read():
    from ecadlib import datasheet as D
    D.from_wire({"package": {"name": "X", "package_type": "gullwing", "dims": [], "other_json": {"tab": None}}})


# ------------------------------------------------------------------ AD26 recheck: a library file open in Altium
@lite
def test_a_locked_library_file_is_reported_and_the_sync_goes_on(tmp_path):
    """On Windows a PcbLib open in Altium can't be replaced: dblib-sync stopped with a bare "[Errno 13] Permission
    denied" after it had already replaced other files."""
    import os
    from ecadlib import altium_dblib_sync as S
    from tests.test_review_1018_ad26_recheck import _item, _models
    _models(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([_item("R1", "Resistors")]))
    real = os.replace

    def locked(a, b):
        if str(b).endswith(".PcbLib"):
            raise PermissionError(13, "Permission denied", str(b))
        return real(a, b)
    os.replace = locked
    try:
        out = S.sync(plan, tmp_path / "lib")
    finally:
        os.replace = real
    text = json.dumps(out)
    assert "is open in Altium or another program" in text, text
    assert not list((tmp_path / "lib").rglob("*.tmp")), "no temp file left behind"
    assert list((tmp_path / "lib").rglob("Resistor.SchLib")), "the other files still sync"


def test_the_dialog_headline_says_not_found_for_a_bad_part_number():
    from ecadlib import altium_forms as AF
    src = AF.__file__ and open(AF.__file__, encoding="utf-8").read()
    i = src.index("If St = 'error' Then")
    assert "EcadlibDialog.Big.Caption := Answer(Job, 'big')" in src[i:i + 300]


@lite
def test_an_exclusively_locked_library_file_is_reported_too(tmp_path):
    """AD26 recheck after #112: a file locked with no sharing failed already at the read, with a bare Errno 13."""
    from pathlib import Path
    from ecadlib import altium_dblib_sync as S
    from tests.test_review_1018_ad26_recheck import _item, _models
    _models(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([_item("R1", "Resistors")]))
    S.sync(plan, tmp_path / "lib")                                    # the files exist, then one is locked
    lib = str(tmp_path / "lib")
    real = Path.read_bytes

    def locked(self):
        if str(self).startswith(lib) and self.suffix == ".PcbLib":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)
    Path.read_bytes = locked
    try:
        out = S.sync(plan, tmp_path / "lib")
    finally:
        Path.read_bytes = real
    text = json.dumps(out)
    assert "is open in Altium or another program" in text and "Errno" not in text, text
