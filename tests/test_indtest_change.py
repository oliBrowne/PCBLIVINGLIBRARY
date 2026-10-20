"""Regression tests for the 2026-10-20 independent testing pass, change-control area (T-ids from
/mnt/project-files/tests/2026-10-20_independent_test/REPORT.md). Nothing live.
    python3 -m tests.run_all test_indtest_change"""
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

from ecadlib import release_check as RC

REPO = Path(__file__).resolve().parents[1]


def _run(cwd, home, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "ECADLIB_")) and k not in ("NEXAR_TOKEN",)}
    env.update(ECADLIB_HOME=str(home), NO_COLOR="1", ECADLIB_USER="Tester <tester@example.com>",
               PYTHONPATH=os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")]))
    return subprocess.run([sys.executable, "-m", "ecadlib", *map(str, args)], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


# ------------------------------------------------------------------ T009: a reject without a policy crashed the report
def test_t009_report_takes_columns_from_every_row():
    with tempfile.TemporaryDirectory() as d:
        row = dict.fromkeys(("symbol_source", "footprint_source", "height_mm", "failures", "warnings", "allowed"), "")
        rows = [dict(row, name="A", status="ready", result="ready"),
                dict(row, name="B", status="awaiting_approval", result="ready", approval="rejected by Bob")]
        RC.write_report(rows, Path(d) / "release_check")
        csv = (Path(d) / "release_check.csv").read_text(encoding="utf-8-sig")
        assert "approval" in csv.splitlines()[0] and "rejected by Bob" in csv, csv


def test_t009_release_check_after_reject_without_policy():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        home = d / "home"
        assert _run(d, home, "demo", "-o", "ws").returncode == 0
        plan = "ws/librarian/demo_bom_demo/plan.json"
        r = _run(d, home, "approve", plan, "reject", "--part", "AO3401A", "-m", "pin 3 is the drain", "--user", "Bob <bob@x.com>")
        assert r.returncode == 0, r.stdout + r.stderr
        rc = _run(d, home, "release-check", plan)
        out = rc.stdout + rc.stderr
        assert "internal error" not in out and "not in fieldnames" not in out, out
        assert rc.returncode == 2, (rc.returncode, out)                  # CH340C has no symbol: held
        assert "AO3401A" in (d / "ws/librarian/demo_bom_demo/release_check.csv").read_text(encoding="utf-8-sig")


def _ledger(lib):
    return [json.loads(x) for x in (lib / "library.ledger.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def _demo(d):
    d = Path(d)
    assert _run(d, d / "home", "demo", "-o", "ws").returncode == 0
    return d / "ws/librarian/demo_bom_demo"


# ------------------------------------------------------------------ T002: --refresh / rebuild overwrote a hand edit unrecorded
def test_t002_refresh_and_rebuild_keep_a_hand_edit_of_a_released_part():
    with tempfile.TemporaryDirectory() as d:
        lib = _demo(d)
        kmod = lib / "models/KiCad/R_0603_1608Metric.kicad_mod"
        released = kmod.read_text(encoding="utf-8")
        assert "(size 0.8 0.95)" in released
        for size, how in (("0.9", ("librarian", "ws/uploads/demo/demo_bom.csv", "-o", lib, "--offline", "--sources",
                                   "standard", "--refresh", "RES-0603-10k")), ("1.1", ("rebuild", lib))):
            kmod.write_text(released.replace("(size 0.8 0.95)", f"(size {size} 0.95)", 1), encoding="utf-8")
            sha = hashlib.sha256(kmod.read_bytes()).hexdigest()
            n = len(_ledger(lib))
            r = _run(d, Path(d) / "home", *how)
            assert r.returncode in (0, 2), r.stdout + r.stderr
            assert "change hold  RES-0603-10k" in r.stdout, r.stdout
            assert (lib / ".ecadlib/blobs" / sha[:2] / sha).is_file(), how      # the edited bytes are kept
            assert any(e.get("event") == "hold" and e.get("part") == "RES-0603-10k" and
                       any(f"0.8 x 0.95 -> {size} x 0.95 mm" in c["text"] for c in e.get("changes") or [])
                       for e in _ledger(lib)[n:]), how
            assert "(size 0.8 0.95)" in kmod.read_text(encoding="utf-8")       # the released revision stays on disk


# ------------------------------------------------------------------ T011: resolve choose rewrote a file another released part uses
def test_t011_chosen_footprint_gets_its_own_file():
    bom = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
           "D1,1,RED,LED_0603_1608Metric,Hubei KENTO Elec,KT-0603R,C2286,LED red 0603,https://example.com/r.pdf\n"
           "D2,1,GREEN,LED_0603_1608Metric,Hubei KENTO Elec,KT-0603G,C2287,LED green 0603,https://example.com/g.pdf\n")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "bom.csv").write_text(bom, encoding="utf-8")
        assert _run(d, d / "home", "librarian", "bom.csv", "-o", "lib", "--offline", "--sources", "standard").returncode == 0
        lib = d / "lib"
        shared = lib / "models/KiCad/LED_0603_1608Metric.kicad_mod"
        before = shared.read_bytes()
        r = _run(d, d / "home", "resolve", "lib/plan.json", "--part", "KT-0603R", "--offline", "choose", "footprint",
                 "spec:LED_0805_2012Metric")
        assert r.returncode == 0 and "is another part's, with other pads" in r.stdout, r.stdout + r.stderr
        items = {i["name"]: i for i in json.loads((lib / "plan.json").read_text(encoding="utf-8"))}
        red, green = items["KT-0603R"]["footprint"], items["KT-0603G"]["footprint"]
        assert red["ref"] == "LED_0603_1608Metric_LED_0805_2012Metric" and red["bom_ref"] == "LED_0603_1608Metric", red
        assert red["file"] != green["file"] and green["kicad_mod"] == "models/KiCad/LED_0603_1608Metric.kicad_mod"
        assert shared.read_bytes() == before                                  # KT-0603G keeps its released pads
        assert [e["revision"] for e in _ledger(lib) if e.get("part") == "KT-0603G" and e["event"] == "release"] == [1]
        assert [e["revision"] for e in _ledger(lib) if e.get("part") == "KT-0603R" and e["event"] == "release"] == [1, 2]
        assert _run(d, d / "home", "librarian", "bom.csv", "-o", "lib", "--offline", "--sources", "standard").returncode == 0
        again = {i["name"]: i["footprint"] for i in json.loads((lib / "plan.json").read_text(encoding="utf-8"))}
        assert again["KT-0603R"]["file"] == red["file"] and again["KT-0603G"]["file"] == green["file"]   # a re-run keeps both


# ------------------------------------------------------------------ T042: part approve released an unreadable .kicad_mod
def test_t042_unreadable_kicad_mod_fails_the_release_check():
    with tempfile.TemporaryDirectory() as d:
        lib = _demo(d)
        items = {i["name"]: i for i in json.loads((lib / "plan.json").read_text(encoding="utf-8"))}
        kmod = lib / "models/KiCad/C_0402_1005Metric.kicad_mod"
        released = kmod.read_bytes()
        assert not RC.check_item(items["CAP-0402-100nF"], lib)[0]
        kmod.write_bytes(released[:400])                                       # cut short
        fails = RC.check_item(items["CAP-0402-100nF"], lib)[0]
        assert any(r == "files" and "KiCad footprint file does not read back" in m for r, m in fails), fails
        _run(d, Path(d) / "home", "kicad-export", lib / "plan.json", "-o", "kx", "--name", "m")    # held as a change
        held = {h["part"]: h for h in json.loads(_run(d, Path(d) / "home", "part", "status", lib, "--json").stdout)["held"]}
        ap = _run(d, Path(d) / "home", "part", "approve", lib, "--part", "CAP-0402-100nF", "--digest",
                  held["CAP-0402-100nF"]["digest"][:12], "-m", "ok", "--user", "Ann <ann@x.com>")
        assert ap.returncode == 2 and "fails the release check" in ap.stderr, (ap.returncode, ap.stdout, ap.stderr)
        assert kmod.read_bytes() == released


# ------------------------------------------------------------------ T043: a refused choice left its STEP in the released library
def test_t043_refused_choice_puts_the_released_step_back():
    with tempfile.TemporaryDirectory() as d:
        lib, home = _demo(d), Path(d) / "home"
        plan = lib / "plan.json"
        _run(d, home, "release-check", plan, "--allow", "AO3401A")                            # AO3401A released
        step = lib / "models/PcbLib/ecadlib_3d/SOT-23_0/SOT-23.step"
        assert _run(d, home, "resolve", plan, "--part", "AO3401A", "--offline", "choose", "footprint", "spec:SOT-23").returncode == 0
        released = step.read_bytes()                   # an equivalent footprint again: same part, new PcbLib bytes
        r = _run(d, home, "resolve", plan, "--part", "AO3401A", "--offline", "choose", "footprint", "spec:SOT-23-5")
        assert r.returncode == 2 and "not applied" in r.stdout, r.stdout + r.stderr
        assert step.read_bytes() == released


# ------------------------------------------------------------------ T044: kicad-export left a held hand edit's released part out
def test_t044_export_ships_the_released_revision_after_a_release_check_of_a_hand_edit():
    with tempfile.TemporaryDirectory() as d:
        lib, home = _demo(d), Path(d) / "home"
        plan = lib / "plan.json"
        kmod = lib / "models/KiCad/C_0402_1005Metric.kicad_mod"
        kmod.write_text(kmod.read_text(encoding="utf-8").replace("(size 0.56 0.62)", "(size 0.9 0.62)", 1), encoding="utf-8")
        assert "CAP-0402-100nF" in _run(d, home, "release-check", plan).stdout          # held on the edited files
        for n in (1, 2):
            kx = _run(d, home, "kicad-export", plan, "-o", f"kx{n}", "--name", "m")
            assert "the released revision r1 is exported" in kx.stdout, kx.stdout
            assert (Path(d) / f"kx{n}/m.pretty/C_0402_1005Metric.kicad_mod").is_file(), kx.stdout
        assert next(i for i in json.loads(plan.read_text(encoding="utf-8")) if i["name"] == "CAP-0402-100nF")["status"] == "ready"


# ------------------------------------------------------------------ T096: publishers held a hand edit without a word
def test_t096_publishers_say_they_held_a_hand_edit():
    with tempfile.TemporaryDirectory() as d:
        lib, home = _demo(d), Path(d) / "home"
        plan = lib / "plan.json"
        kmod = lib / "models/KiCad/C_0402_1005Metric.kicad_mod"
        released = kmod.read_text(encoding="utf-8")
        for size, cmd in (("0.9", ("dblib-sync", plan, "--to", "dbl", "--name", "m")), ("0.8", ("export-parts", plan, "-o", "p.csv")),
                          ("0.7", ("kicad-dblib", plan, "-o", "kdb"))):
            kmod.write_text(released.replace("(size 0.56 0.62)", f"(size {size} 0.62)", 1), encoding="utf-8")
            r = _run(d, home, *cmd)
            assert "change hold  CAP-0402-100nF" in r.stderr and "ecadlib part show" in r.stderr, (cmd, r.stdout, r.stderr)
            assert kmod.read_text(encoding="utf-8") == released


# ------------------------------------------------------------------ T097: review sent a change-control hold to --allow
def test_t097_review_names_part_show_for_a_change_hold():
    from ecadlib import messages as M, review_cli as RV
    why = ("change control: new part with the MPN of released RES-0603-10k r1 (pad 1 size 0.8 x 0.95 -> 0.6 x 0.6 mm); "
           "a person approves it: ecadlib part show")
    e = M.explain(why)
    assert e["code"] == "change.held" and "part approve" in e["fix"] and "release anyway" not in e["fix"].split("(")[0], e
    cmds = RV.fix_commands("lib/plan.json", {"name": "RES-0402-10k", "hold_reasons": [why]}, [])
    assert cmds == ["ecadlib part show lib/plan.json RES-0402-10k"], cmds
    assert RV.fix_commands("p.json", {"name": "X", "hold_reasons": ["verify: check it"]}, []) == ["ecadlib release-check p.json --allow X"]


# ------------------------------------------------------------------ T098: the table said "ready" for parts waiting for sign-off
def test_t098_release_check_table_says_waiting_for_sign_off():
    import io
    from ecadlib import term as TM
    row = dict.fromkeys(("symbol_source", "footprint_source", "height_mm", "failures", "warnings", "allowed"), "")
    rows = [dict(row, name="PART-A", status="awaiting_approval", result="pass"),
            dict(row, name="PART-B", status="ready", result="allowed"), dict(row, name="PART-C", status="ready", result="pass")]
    out = io.StringIO()
    RC.print_result(rows, "plan.json", Path("release_check"), TM.Term(out, styled=False, width=120))
    lines = {l.split()[-1]: l for l in out.getvalue().splitlines() if l.strip().startswith(("ready", "waiting", "released"))}
    assert "waiting for sign-off" in lines["PART-A"], out.getvalue()
    assert "released anyway" in lines["PART-B"] and "ready" in lines["PART-C"], out.getvalue()


# ------------------------------------------------------------------ T099: part revert dropped a held change without a ledger line
def test_t099_revert_records_the_held_change_it_drops():
    with tempfile.TemporaryDirectory() as d:
        lib, home = _demo(d), Path(d) / "home"
        plan = lib / "plan.json"
        kmod = lib / "models/KiCad/C_0402_1005Metric.kicad_mod"
        rules = Path(d) / "ws/ecadlib.toml"
        rules.write_text(rules.read_text(encoding="utf-8") + '\n[release]\nparity = "warn"\n', encoding="utf-8")

        def held(a, b):
            kmod.write_text(kmod.read_text(encoding="utf-8").replace(f"(size {a} 0.62)", f"(size {b} 0.62)", 1), encoding="utf-8")
            _run(d, home, "kicad-export", plan, "-o", "kx", "--name", "m")
            st = json.loads(_run(d, home, "part", "status", lib, "--json").stdout)
            return next(h["digest"] for h in st["held"] if h["part"] == "CAP-0402-100nF")
        dg = held("0.56", "0.4")
        assert _run(d, home, "part", "approve", lib, "--part", "CAP-0402-100nF", "--digest", dg[:12], "-m", "r2",
                    "--user", "Ann <ann@x.com>").returncode == 0
        dg = held("0.4", "0.3")
        n = len(_ledger(lib))
        rv = _run(d, home, "part", "revert", lib, "CAP-0402-100nF@1", "-m", "back", "--user", "Bob <bob@x.com>")
        assert rv.returncode == 0 and "was dropped" in rv.stdout, rv.stdout + rv.stderr
        assert any(e["event"] == "superseded" and e.get("digest") == dg for e in _ledger(lib)[n:]), _ledger(lib)[n:]
        assert not (lib / ".ecadlib/proposed/CAP-0402-100nF.json").exists()


# ------------------------------------------------------------------ T100: ordinary resolve mistakes were "internal error"
def test_t100_resolve_mistakes_are_one_line_errors():
    from ecadlib import cliutil as CU, resolve as R
    for call in (lambda: R._item([{"name": "AO3401A"}], "AO3401"), lambda: R.load("SOT-23", "footprint", None)):
        try:
            call()
            raise AssertionError("no error")
        except (KeyError, ValueError) as e:            # still what API callers catch
            msg, hint, code = CU.friendly(e)
            assert code == 2 and "internal" not in msg and hint, (msg, hint)
    try:
        R._item([{"name": "AO3401A"}], "AO3401")
    except KeyError as e:
        assert str(e) == "'AO3401' is not in the plan" and "did you mean AO3401A" in e.hint, (str(e), e.hint)
    with tempfile.TemporaryDirectory() as d:
        lib = _demo(d)
        r = _run(d, Path(d) / "home", "resolve", lib / "plan.json", "--part", "AO3401A", "--offline", "choose", "footprint", "SOT-23")
        assert r.returncode == 2 and "internal error" not in r.stderr and "unknown footprint candidate id" in r.stderr, r.stderr


# ------------------------------------------------------------------ T101: fp-edit recorded the login name, not who you are
def test_t101_fp_edit_records_who_you_are():
    import contextlib, io
    from ecadlib import fp_edit as FE
    from ecadlib.eval import gate as G
    with tempfile.TemporaryDirectory() as d:
        G.run(out=d, ids=["connector-NLJ2MDXX-V"])
        plan = Path(d) / "lib" / "plan.json"
        old = os.environ.get("ECADLIB_USER")
        os.environ["ECADLIB_USER"] = "Ann Lee <ann@example.com>"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                FE.main([str(plan), "--part", "NLJ2MDXX-V", "rename", "1+=1"])
            assert json.loads((plan.parent / FE.FILE).read_text())["NLJ2MDXX-V"]["by"] == "Ann Lee <ann@example.com>"
            with contextlib.redirect_stdout(io.StringIO()):
                FE.main([str(plan), "--part", "NLJ2MDXX-V", "--user", "Bob <bob@example.com>", "rename", "1-=2"])
            assert json.loads((plan.parent / FE.FILE).read_text())["NLJ2MDXX-V"]["by"] == "Bob <bob@example.com>"
        finally:
            if old is None:
                os.environ.pop("ECADLIB_USER", None)
            else:
                os.environ["ECADLIB_USER"] = old
