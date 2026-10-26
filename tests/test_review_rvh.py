"""Regression tests for the round-6 review of the core pipeline modules (rvH). Run:  python3 -m tests.test_review_rvh"""
import ast, json, os, subprocess, sys, tempfile, threading
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
CORE = ["bom_triage", "providers", "librarian", "release_check", "resolve", "part_lookup", "lifecycle", "upstream", "upkeep",
        "approvals", "dedupe", "datasheet", "httpcache", "rules", "rules_infer", "sheetsafe", "cli", "app", "fsutil"]


# ---------------------------------------------------------------- text files are UTF-8 whatever the locale
def _unencoded_text_io(src):
    out = []
    for n in ast.walk(ast.parse(src)):
        if not isinstance(n, ast.Call) or any(k.arg in ("encoding", None) for k in n.keywords):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            out.append(n.lineno)
        elif isinstance(f, ast.Name) and f.id == "open":
            mode = next((a.value for a in n.args[1:2] if isinstance(a, ast.Constant)), None)
            mode = next((k.value.value for k in n.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)), mode) or "r"
            if "b" not in mode:
                out.append(n.lineno)
    return out


def test_core_modules_read_and_write_text_as_utf8():
    """Windows' default encoding is cp1252: a BOM value '10kΩ' crashed the triage report, and a 'µ' written in cp1252
    could not be read back by the UTF-8 readers (pandas, json)."""
    bad = {m: _unencoded_text_io((ROOT / "ecadlib" / f"{m}.py").read_text(encoding="utf-8")) for m in CORE}
    bad = {m: v for m, v in bad.items() if v}
    assert not bad, f"text I/O without encoding= (locale-dependent): {bad}"


def test_triage_report_with_unicode_under_a_non_utf8_locale():
    with tempfile.TemporaryDirectory() as d:
        bom = Path(d) / "bom.csv"
        bom.write_text("Designator,Value,Manufacturer Part Number\nR1,10kΩ,RC0603FR-0710KL\nC1,0.1µF,GRM188R71H104KA93D\n",
                       encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "LANG", "PYTHONUTF8", "PYTHONIOENCODING"))}
        env.update(LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT))
        r = subprocess.run([sys.executable, "-m", "ecadlib.bom_triage", str(bom), "-o", str(Path(d) / "tri"), "--offline",
                            "--providers", "", "--cache", str(Path(d) / "cache")],
                           capture_output=True, text=True, encoding="utf-8", env=env, cwd=d, timeout=120)
        assert r.returncode in (0, None), r.stderr[-2000:]
        csv_text = (Path(d) / "tri.csv").read_text(encoding="utf-8")
        assert "10kΩ" in csv_text and "0.1µF" in csv_text, csv_text
        assert "RC0603FR-0710KL" in (Path(d) / "tri.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- plan / state files are replaced atomically
def _crash_mid_write(self, data, *a, **k):
    with open(self, "w", encoding="utf-8") as f:            # the old Path.write_text: truncate, write part, die
        f.write(data[: len(data) // 3])
    raise OSError("simulated crash / full disk")


def test_write_text_atomic_keeps_the_old_file():
    from ecadlib.fsutil import write_text_atomic
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "plan.json"
        write_text_atomic(p, '[{"name": "A"}]')
        with mock.patch("ecadlib.fsutil.os.replace", side_effect=OSError("disk full")):
            try:
                write_text_atomic(p, '[{"name": "B"}]')
                assert False
            except OSError:
                pass
        assert json.loads(p.read_text(encoding="utf-8")) == [{"name": "A"}]
        assert [x.name for x in Path(d).iterdir()] == ["plan.json"], list(Path(d).iterdir())   # no temp file left


def test_approve_crash_leaves_plan_readable():
    """`ecadlib approve` used plan.write_text: a crash while writing left a truncated plan.json, losing every
    decision and breaking every later command on that plan."""
    from ecadlib import approvals as AP, release_check as RC
    item = {"name": "P1", "status": "ready", "folder": "Components/ICs", "description": "x", "comment": "*",
            "parameters": [], "symbol": {"ref": "P1", "file": None}, "footprint": {"ref": "P1", "file": None}}
    with tempfile.TemporaryDirectory() as d:
        plan = Path(d) / "plan.json"
        plan.write_text(json.dumps([item] * 1, indent=2), encoding="utf-8")
        before = plan.read_text(encoding="utf-8")
        with mock.patch.object(Path, "write_text", _crash_mid_write):
            for call in (lambda: AP.main([str(plan), "approve", "--part", "P1", "--user", "Ann <ann@x.com>"]),
                         lambda: RC.main([str(plan), "-o", str(Path(d) / "rc")])):
                try:
                    call()
                except (OSError, SystemExit):
                    pass
                json.loads(plan.read_text(encoding="utf-8"))           # still a whole JSON document
        assert json.loads(plan.read_text(encoding="utf-8"))[0]["name"] == "P1" and before


# ---------------------------------------------------------------- http cache
class _Resp:
    status_code, text, ok = 200, '{"ok": 1}', True


def test_http_cache_damaged_entry_is_a_miss():
    from ecadlib.httpcache import CachedHTTP
    with tempfile.TemporaryDirectory() as d:
        h = CachedHTTP(d, min_interval=0, offline=True)
        url = "https://example.com/x"
        (Path(d) / f"{h._key('GET', url, None)}.json").write_text('{"status": 200, "te', encoding="utf-8")   # torn write
        r = h.get(url)
        assert not r.ok and "offline" in r.error, (r.status, r.error)


def test_http_cache_concurrent_writers_of_one_url():
    """Two threads fetching the same URL shared one '<key>.tmp': the second os.replace found it gone (FileNotFoundError)
    or moved the other writer's half-written file into the cache."""
    from ecadlib import httpcache as HC
    with tempfile.TemporaryDirectory() as d:
        h = HC.CachedHTTP(d, min_interval=0)
        h.s = mock.Mock()
        h.s.request.return_value = _Resp()
        real, errors, first = os.replace, [], [True]

        def other_writer():
            try:
                g = HC.CachedHTTP(d, min_interval=0, ttl_s=0)
                g.s = mock.Mock()
                g.s.request.return_value = _Resp()
                g.get("https://example.com/same")
            except Exception as e:
                errors.append(e)

        def replace(a, b):
            if first[0]:                       # between this writer's temp write and its rename, another thread writes
                first[0] = False
                t = threading.Thread(target=other_writer)
                t.start(); t.join()
            return real(a, b)
        with mock.patch.object(HC.os, "replace", replace):
            r = h.get("https://example.com/same")
        assert r.ok and not errors, errors
        files = sorted(p.name for p in Path(d).iterdir())
        assert len(files) == 1 and files[0].endswith(".json"), files
        assert json.loads((Path(d) / files[0]).read_text(encoding="utf-8"))["text"] == '{"ok": 1}'


# ---------------------------------------------------------------- resolve: a shared model file is re-checked
def test_resolve_choose_rechecks_parts_sharing_the_file():
    """Two parts share models/PcbLib/SOT-23.PcbLib. Choosing a 10-pad header for one overwrote the shared file, but
    only the chosen part was re-checked: the other stayed `ready` with a footprint whose pads no longer match its
    3-pin symbol, and push would release it. Now the chosen footprint gets its own file (other pads: T011), so the
    other part is not touched at all; the choice fails the release check for the chosen part, so it is not applied
    there either, and the shared file keeps its released bytes."""
    from ecadlib import kicad_import as K, librarian as L, release_check as RC, resolve as R
    from tests import test_resolve as TR
    with tempfile.TemporaryDirectory() as d:
        plan, src = TR.make_plan(d)
        R.choose(plan, "FAKEFET1", "footprint", "spec:SOT-23", src=src)
        items = json.loads(plan.read_text(encoding="utf-8"))
        a = next(i for i in items if i["name"] == "FAKEFET1")
        b = json.loads(json.dumps(a))
        b.update(name="FAKEFET2", parameters=TR.PARAMS + [{"name": "Manufacturer Part Number", "value": "FAKEFET2"}])
        b["symbol"] = {"ref": "FAKEFET2", "file": None}
        st = {"name": "FAKEFET2", "symbol": None, "footprint": None}
        L.Run(Path(d), src.http, None, [], []).put_kicad_sym(st, K.load_symbol("Transistor_FET:FAKEFET", src.http)[0],
                                                             "FAKEFET2", "SOT-23", "kicad:Transistor_FET:FAKEFET")
        b["symbol"]["file"] = st["symbol"]["file"]
        items.append(b)
        RC.apply(items, Path(d))
        assert b["status"] == "ready" and b["footprint"]["file"] == a["footprint"]["file"], (b["status"], b["hold_reasons"])
        plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
        shared = Path(d) / a["footprint"]["file"]
        released = shared.read_bytes()
        r = R.choose(plan, "FAKEFET1", "footprint", "kicad:Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical", src=src)
        assert any("named SOT-23_PinHeader_2x05_P1.27mm_Vertical" in n for n in r["notes"]), r["notes"]
        items = {i["name"]: i for i in json.loads(plan.read_text(encoding="utf-8"))}
        a, b = items["FAKEFET1"], items["FAKEFET2"]
        assert "FAKEFET1: pins:" in a["change"]["refused"], a.get("change")
        assert a["change"]["state"] == "held" and a["footprint"]["file"] == b["footprint"]["file"]
        assert b["status"] == "ready" and not b.get("change"), (b["status"], b.get("change"))
        assert shared.read_bytes() == released


# ---------------------------------------------------------------- sourcing / rules / dedupe / lifecycle
def test_money_decimal_comma_below_one():
    from ecadlib.part_lookup import money
    assert money("0,125 €") == 0.125 and money("0,010 €") == 0.01          # was 125.0 and 10.0: a 1000x cost rollup
    assert money("1.234,50 €") == 1234.5 and money("1,234.50") == 1234.5 and money("12,345") == 12345.0
    assert money("0,10 €") == 0.1


def test_rkm_values_in_base_units_and_milli():
    from ecadlib import rules as RU
    assert RU.fmt_value("1.5F", "Capacitors", "rkm") == "1F5"               # was '15': a 15 F part
    assert RU.fmt_value("2.2H", "Inductors", "rkm_lower") == "2H2"
    assert RU.fmt_value("1.5mH", "Inductors", "rkm") == "1m5"               # was '1M5' (reads as mega)
    assert RU.fmt_value("4.7uF", "Capacitors", "rkm") == "4U7" and RU.fmt_value("100", "Resistors", "rkm") == "100R"


def test_dedupe_remap_bom_reads_the_bom_sheet():
    """A workbook with a cover sheet first: remap_bom read sheet 1 and wrote the cover sheet as the 'deduped BOM'."""
    import pandas as pd
    from ecadlib import dedupe as DD
    with tempfile.TemporaryDirectory() as d:
        x = Path(d) / "bom.xlsx"
        with pd.ExcelWriter(x) as w:
            pd.DataFrame({"Project": ["Widget rev B"], "Notes": ["see sheet 2"]}).to_excel(w, sheet_name="Cover", index=False)
            pd.DataFrame({"Designator": ["U1", "U2"], "Quantity": [1, 1], "LibRef": ["OLD_SYM", "KEEP"],
                          "Footprint": ["SOIC8_OLD", "QFN"]}).to_excel(w, sheet_name="BOM", index=False)
        mapping = [{"kind": "footprint", "from": "SOIC8_OLD", "to": "SOIC-8"}, {"kind": "symbol", "from": "OLD_SYM", "to": "NEW"}]
        out, n = DD.remap_bom(x, mapping, Path(d) / "deduped.csv")
        text = out.read_text(encoding="utf-8")
        assert n == 2 and "SOIC-8" in text and "NEW" in text and "Widget" not in text, (n, text)


def test_lifecycle_dblib_path_with_hash_sign():
    """'Lib #2/parts.sqlite' cut the SQLite URI at '#': the check silently saw no parts."""
    import sqlite3
    from ecadlib import lifecycle as LC
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ("Lib #2" if os.name == "nt" else "Lib #2?") / "parts.sqlite"      # Windows forbids '?' in names
        p.parent.mkdir()
        con = sqlite3.connect(p)
        con.execute('create table R ("Part Number" text, "Manufacturer Part Number" text, Manufacturer text)')
        con.execute("insert into R values ('RC0603', 'RC0603FR-0710KL', 'Yageo')")
        con.commit(); con.close()
        parts = LC.parts_from_dblib(p)
        assert [x["mpn"] for x in parts] == ["RC0603FR-0710KL"], parts



# ---------------------------------------------------------------- follow-up: plan lock, dedupe backup, approve status, refs
def _agent_ws(d):
    ws = Path(d) / "ws"
    (ws / "agent").mkdir(parents=True)
    plan = ws / "librarian" / "lib" / "plan.json"
    plan.parent.mkdir(parents=True)
    item = {"name": "P1", "status": "held", "folder": "Components/ICs", "description": "x", "comment": "*", "parameters": [],
            "symbol": {"ref": "P1", "file": None}, "footprint": {"ref": "P1", "file": None},
            "source": {"symbol": None, "footprint": None}, "librarian": {"tried": [], "review": [], "notes": []}}
    plan.write_text(json.dumps([item]), encoding="utf-8")
    return ws, plan


def test_cli_allow_survives_a_concurrent_agent_rebuild():
    """The agent's librarian holds plan.lock and writes plan.json from the copy it read at the start. `release-check
    --allow` used to write in the middle of that, and the agent's save then dropped the allow. Now the CLI waits."""
    import time
    from ecadlib import release_check as RC
    from ecadlib.fsutil import plan_lock
    with tempfile.TemporaryDirectory() as d:
        ws, plan = _agent_ws(d)
        locked, cli_started = threading.Event(), threading.Event()

        def agent():
            with plan_lock(ws):
                items = json.loads(plan.read_text(encoding="utf-8"))      # the librarian's copy
                locked.set()
                cli_started.wait(10)
                time.sleep(0.5)                                            # the rebuild takes a while
                for it in items:
                    it["rebuilt"] = True
                plan.write_text(json.dumps(items), encoding="utf-8")
        t = threading.Thread(target=agent)
        t.start()
        locked.wait(10)
        cli_started.set()
        with mock.patch("builtins.print"):
            RC.main([str(plan), "--allow", "P1", "--user", "Ann <ann@x.com>", "-o", str(Path(d) / "rc")])
        t.join(10)
        it = json.loads(plan.read_text(encoding="utf-8"))[0]
        assert it.get("rebuilt") and it.get("allow", {}).get("user") == "Ann <ann@x.com>", it


def test_workspace_plan_rewrites_take_the_plan_lock():
    from ecadlib.fsutil import plan_lock
    from ecadlib.app import Workspace
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(Path(d) / "ws")
        ws.PLAN_LOCK_WAIT = 0.3                  # a plan write waits this long for plan.lock, then is refused
        bom = ws.root / "uploads" / "b.csv"
        bom.write_text("Designator,Value,MPN\nU1,X,P1\n", encoding="utf-8")
        _, plan = _agent_ws(d)
        out = ws._librarian_dir(bom)
        out.mkdir(parents=True)
        (out / "plan.json").write_text(plan.read_text(encoding="utf-8"), encoding="utf-8")
        held, release = threading.Event(), threading.Event()

        def other():
            with plan_lock(ws.root):
                held.set()
                release.wait(10)
        t = threading.Thread(target=other)
        t.start()
        held.wait(10)
        for call in (lambda: ws.release_check("uploads/b.csv", allow=["P1"]), lambda: ws.find_duplicates("uploads/b.csv", merge=True),
                     lambda: ws.choose_part("uploads/b.csv", "P1", "footprint", "spec:SOT-23")):
            try:
                call()
                assert False, "wrote while another command holds the plan lock"
            except ValueError as e:
                assert "another ecadlib command" in str(e), e
        assert "allow" not in json.loads((out / "plan.json").read_text(encoding="utf-8"))[0]
        release.set(); t.join(10)
        r = ws.release_check("uploads/b.csv", allow=["P1"])
        assert r["parts"] == 1 and json.loads((out / "plan.json").read_text(encoding="utf-8"))[0]["allow"]
        r = ws.choose_part("uploads/b.csv", "P1", "footprint", "spec:SOT-23")    # holds the lock, re-checks inside it
        assert r["chosen"]["source"] == "spec:SOT-23", r["chosen"]


def test_dedupe_backup_keeps_the_first_original():
    from ecadlib import dedupe as DD
    with tempfile.TemporaryDirectory() as d:
        plan = Path(d) / "plan.json"
        plan.write_text("original", encoding="utf-8")
        assert DD.backup(plan).name == "plan.json.pre-dedupe"
        plan.write_text("merged once", encoding="utf-8")
        second, third = DD.backup(plan), DD.backup(plan)
        assert (Path(d) / "plan.json.pre-dedupe").read_text(encoding="utf-8") == "original"
        assert second != third and second.name.startswith("plan.json.pre-dedupe-") and third.read_text(encoding="utf-8") == "merged once"


def test_approve_status_names_the_rules_policy():
    from ecadlib import approvals as AP
    with tempfile.TemporaryDirectory() as d:
        plan = Path(d) / "plan.json"
        plan.write_text(json.dumps([{"name": "P1", "status": "ready", "folder": "x", "parameters": [],
                                     "symbol": {"ref": "P1"}, "footprint": {"ref": "P1"}}]), encoding="utf-8")
        (Path(d) / "ecadlib.toml").write_text("[release.approvals]\nrequired = 1\n", encoding="utf-8")
        out = []
        with mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            AP.main([str(plan), "status"])
        assert "[release.approvals]" in out[0] and "none" not in out[0], out
        assert "missing" in out[1], out


def test_count_refs_spaced_ranges_and_units():
    from ecadlib.bom_triage import count_refs
    assert count_refs("R1 - R4") == 4 and count_refs("R10 – R12") == 3 and count_refs("R1 -R4") == 4
    assert count_refs("U1A, U1B") == 1 and count_refs("U1A, U1B, U2A") == 2
    assert count_refs("R1, R2, R5-R8") == 6 and count_refs("R5-8") == 4 and count_refs("") == 0

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
