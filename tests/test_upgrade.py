"""Updates never break a library (ecadlib/migrate.py, ecadlib/rebuild.py).

* Workspaces made by older ecadlib (tests/fixtures/upgrade/*.tar.gz, built by tests/upgrade_fixtures.py with the code of
  v0.2.0, v0.3.0 and two later merge commits) open with today's code, rebuild the same parts, keep their approvals, and
  their queue / watch state still work.
* Files from a NEWER ecadlib are refused with one clear line, never misread.
* `ecadlib migrate` checks / migrates (backups first; --dry-run and --check write nothing).
* library.lock.json + `ecadlib rebuild --check` rebuild offline and report drift."""
import contextlib, hashlib, io, json, os, shutil, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import __version__, approvals as AP, librarian as L, migrate as MG, rebuild as RB, rules as RU
from ecadlib.app import Workspace
from tests import upgrade_fixtures as UF
from tests.e2e_support import seed_cache

LABELS = ("v0.2.0", "v0.3.0", "r5-agent", "r7-stdparts")
AGENT = ("r5-agent", "r7-stdparts")


def _tree(root):
    """{relative path: sha256} of every file under root."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


@contextlib.contextmanager
def _fixture(label):
    with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"ECADLIB_USER": ""}):
        os.environ.pop("ECADLIB_CONFIG", None)
        ws = UF.extract(label, d)
        seed_cache(ws / "http_cache")
        MG._CHECKED.clear()
        yield ws, json.loads((ws / "manifest.json").read_text())


def _fresh_build(d):
    """Today's code on the same BOM in an empty workspace: what an upgraded workspace must end up with."""
    root = Path(d) / "fresh"
    (root / "uploads").mkdir(parents=True)
    (root / "uploads" / "board.csv").write_text(UF.BOM, encoding="utf-8")
    (root / "ecadlib.toml").write_text(UF.RULES, encoding="utf-8")
    seed_cache(root / "http_cache")
    Workspace(root, offline=True).librarian_run("uploads/board.csv")
    return {i["name"]: i for i in json.loads((root / "librarian" / "board" / "plan.json").read_text())}


def test_fixtures_are_from_older_versions():
    for label in LABELS:
        with _fixture(label) as (ws, man):
            assert man["commit"] and man["ecadlib"] in ("0.2.0", "0.3.0"), man
            assert (ws / "librarian" / "board" / "plan.json").is_file()
            assert not (ws / "librarian" / "board" / MG.STAMP).exists(), "made before versioning: no stamp"
            assert not (ws / "librarian" / "board" / RB.LOCK).exists()
            if label in AGENT:
                assert len(list((ws / "agent" / "queue" / "items").glob("*.json"))) == 2


def test_old_workspaces_open_and_rebuild_the_same_parts():
    with tempfile.TemporaryDirectory() as d:
        fresh = _fresh_build(d)
    for label in LABELS:
        with _fixture(label) as (ws, man):
            lib = ws / "librarian" / "board"
            old_plan = {i["name"]: i for i in json.loads((lib / "plan.json").read_text())}
            old_state_bytes = (lib / "librarian_state.json").read_bytes()
            rows = {(Path(r["path"]).name, r["kind"]): r for r in MG.scan(ws)}
            assert rows[("librarian_state.json", "librarian_state")]["state"] == "old", (label, rows)
            assert rows[("plan.json", "plan")]["state"] == "ok"
            assert not [r for r in rows.values() if r["state"] in ("new", "error")], (label, rows)
            # in memory: migrated, nothing written
            st = MG.read(lib / "librarian_state.json")
            assert st["EEPART1"]["footprint"]["hash"] and st["EEPART1"]["footprint"]["name"] == "EEPART1"
            assert (lib / "librarian_state.json").read_bytes() == old_state_bytes
            old_state = json.loads(old_state_bytes)
            done_before = {n: (lib / it["symbol"]["file"]).read_bytes() for n, it in old_plan.items()   # what the
                           if L._done(old_state.get(n) or {}, lib) and it["status"] != "held"}         # librarian skips
            # (a held part an older version built is built again, so the release check sees today's models and fields)
            # today's code opens the workspace and runs the librarian on it
            w = Workspace(ws, offline=True)
            assert RU.load(None, start=ws)["footprint"]["density"] == ("N" if man["rules"] else RU.DEFAULTS["footprint"]["density"])
            w.librarian_run("uploads/board.csv")
            backups = list((lib / MG.BACKUP).glob("librarian_state-v1-*/librarian_state.json"))
            assert len(backups) == 1 and backups[0].read_bytes() == old_state_bytes, label
            assert json.loads((lib / MG.STAMP).read_text())["kinds"] == {"approvals_audit": 1, "librarian_state": 2, "plan": 1} \
                if (lib / "approvals_audit.jsonl").exists() else True
            assert MG.folder_version(lib, "librarian_state")[0] == 2
            new = {i["name"]: i for i in json.loads((lib / "plan.json").read_text())}
            assert set(new) == set(old_plan) == set(fresh), label
            for n, it in new.items():             # the same parts as a fresh build with today's code; a part the old
                assert it["status"] == fresh[n]["status"], (label, n, it["status"])    # version finished keeps its sources
                want = old_plan[n]["source"] if n in done_before else fresh[n]["source"]
                assert it["source"] == want, (label, n, it["source"], want)
            for n, data in done_before.items():   # finished parts were kept, not rebuilt: files untouched
                assert (lib / new[n]["symbol"]["file"]).read_bytes() == data, (label, n)
            if "approved" in man["made"]:         # a sign-off made by the old version still counts
                ee = new["EEPART1"]
                assert ee.get("approvals") and ee["approvals"][0]["who"].startswith("Ann Lee"), ee.get("approvals")
                ev = AP.evaluate(ee, AP.normalize_policy({"required": 1}), lib)
                assert ev["approved_by"] == ["Ann Lee <ann@example.com>"] and not ev["stale"], (label, ev)
            lock = RB.read_lock(lib)
            assert set(lock["parts"]) == set(new) and lock["ecadlib"] == __version__
            for n, p in lock["parts"].items():  # parts kept from the old run: inputs were not recorded then
                if n in done_before:
                    assert p["inputs"] is None and p["built_with"] is None, (label, n)
                elif p["footprint"] and p["footprint"]["source"].startswith("kicad:"):
                    assert any(e["url"].startswith("https://gitlab.com/") for e in p["inputs"]), (label, n)
            if label in AGENT:
                _agent_still_works(ws, man, w)


def _agent_still_works(ws, man, w):
    from ecadlib import agent_queue as Q, agent_watch as W
    q = Q.Queue(ws)
    ids = {it["id"]: it for it in q.list()}
    assert set(man["made"]["queue"].values()) <= set(ids)
    rid = man["made"]["queue"]["review"]
    assert ids[rid]["status"] == "review" and ids[man["made"]["queue"]["failed"]]["status"] == "failed"
    r = Q.decide(w, rid, "approve", "Bob Ray <bob@example.com>")
    assert r["item"]["status"] == "approved" and r["approval"]["recorded"], r["approval"]
    plan = {i["name"]: i for i in json.loads((ws / "librarian" / "board" / "plan.json").read_text())}
    assert any(a["who"].startswith("Bob Ray") for a in plan["LM358DR"]["approvals"])
    Q.decide(w, man["made"]["queue"]["failed"], "retry", "Bob Ray <bob@example.com>")
    assert MG.folder_version(ws / "agent" / "queue", "queue")[0] == 1 and (ws / "agent" / "queue" / MG.STAMP).is_file()
    state = W.load_state(ws)                       # the watch state from the old version still loads ...
    assert state["sources"], state
    out = io.StringIO()
    with contextlib.redirect_stdout(out):          # ... and a new scan of the unchanged BOM finds nothing new
        W.main(["--workspace", str(ws), "--source", "bom_file:uploads/board.csv", "--json"])
    rep = json.loads(out.getvalue())
    assert not [c for c in rep.get("changes", []) if c.get("kind") in ("added", "new")], rep.get("changes")


def test_newer_files_are_refused_with_one_clear_line():
    with _fixture("r5-agent") as (ws, man):
        lib = ws / "librarian" / "board"
        (lib / MG.STAMP).write_text(json.dumps({"schema_version": 1, "ecadlib": "9.9.0", "kinds": {"plan": 7}}))
        before = _tree(ws)
        for call in (lambda: Workspace(ws, offline=True), lambda: MG.read(lib / "plan.json"),
                     lambda: L.run(ws / "uploads" / "board.csv", out=lib, offline=True, cache=ws / "http_cache", log=lambda *a: None)):
            MG._CHECKED.clear()
            try:
                call()
                raise AssertionError("a newer plan was read")
            except MG.SchemaTooNew as e:
                msg = str(e)
                assert "made by ecadlib 9.9.0" in msg and "plan schema 7" in msg and "upgrade ecadlib" in msg, msg
                assert "\n" not in msg
        assert _tree(ws) == before, "nothing was written"
        with contextlib.redirect_stdout(io.StringIO()):
            assert MG.main([str(ws), "--check"]) == 1
        (lib / MG.STAMP).unlink()
        # the stamp file itself in a newer format
        (ws / "agent" / "queue" / MG.STAMP).write_text(json.dumps({"schema_version": 3, "ecadlib": "2.0.0"}))
        from ecadlib import agent_queue as Q
        try:
            Q.Queue(ws)
            raise AssertionError("a newer queue was opened")
        except MG.SchemaTooNew as e:
            assert "ecadlib 2.0.0" in str(e) and "upgrade ecadlib" in str(e)
        (ws / "agent" / "queue" / MG.STAMP).write_text(json.dumps({"schema_version": 1, "ecadlib": "2.0.0", "kinds": {"queue": 2}}))
        try:
            Q.Queue(ws)
            raise AssertionError("a newer queue was opened")
        except MG.SchemaTooNew as e:
            assert "queue schema 2" in str(e)
        # in-file versions: rules, the approvals policy, the lock
        (ws / "ecadlib.toml").write_text('schema_version = 4\necadlib = "1.2.0"\n' + UF.RULES)
        try:
            RU.load(ws / "ecadlib.toml")
            raise AssertionError("newer rules were read")
        except RU.RulesError as e:
            assert "made by ecadlib 1.2.0" in str(e) and "rules schema 4" in str(e)
        (ws / "ecadlib.toml").write_text("schema_version = 1\n" + UF.RULES)
        assert RU.load(ws / "ecadlib.toml")["footprint"]["density"] == "N"     # the current version is fine
        (lib / "approvals.json").write_text(json.dumps({"schema_version": 3, "required": 1}))
        try:
            AP.load_policy(lib)
            raise AssertionError("a newer policy was read")
        except MG.SchemaTooNew as e:
            assert "approvals_policy schema 3" in str(e)
        (lib / "approvals.json").write_text(json.dumps({"schema_version": 1, "required": 1}))
        assert AP.load_policy(lib)["required"] == 1
        (lib / RB.LOCK).write_text(json.dumps({"schema_version": 2, "ecadlib": "5.0", "parts": {}}))
        try:
            RB.read_lock(lib)
            raise AssertionError("a newer lock was read")
        except MG.SchemaTooNew as e:
            assert "lock schema 2" in str(e)
        # a kind only a newer ecadlib knows is left alone, not an error
        (lib / MG.STAMP).write_text(json.dumps({"schema_version": 1, "ecadlib": "3.0", "kinds": {"plan": 1, "hologram": 4}}))
        assert MG.pending(lib, ["plan"]) == []
        assert any(r["state"] == "unknown" and r["kind"] == "hologram" for r in MG.scan(ws))


def test_migrate_cli_check_dry_run_and_backup():
    with _fixture("v0.3.0") as (ws, man):
        lib = ws / "librarian" / "board"
        before = _tree(ws)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert MG.main([str(ws), "--check"]) == 1                 # something to migrate
            assert MG.main([str(ws), "--dry-run"]) == 0
        assert "librarian_state" in out.getvalue() and "would be migrated" in out.getvalue()
        assert _tree(ws) == before, "--check / --dry-run wrote something"
        with contextlib.redirect_stdout(io.StringIO()):
            assert MG.main([str(ws)]) == 0
        after = _tree(ws)
        assert after["librarian/board/librarian_state.json"] != before["librarian/board/librarian_state.json"]
        bk = [k for k in after if k.startswith("librarian/board/.ecadlib_backup/")]
        assert len(bk) == 1 and after[bk[0]] == before["librarian/board/librarian_state.json"]
        assert {k for k in after if k not in before} == {bk[0], "librarian/board/" + MG.STAMP}
        st = json.loads((lib / "librarian_state.json").read_text())
        assert st["EEPART1"]["footprint"]["hash"]
        with contextlib.redirect_stdout(io.StringIO()):
            assert MG.main([str(ws), "--check"]) == 0                 # now up to date
            assert MG.main([str(ws)]) == 0
        assert _tree(ws) == after, "a second migrate changes nothing"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            MG.main([str(ws), "--json"])
        assert all(r["state"] == "ok" for r in json.loads(out.getvalue()))


def test_state_migration_is_safe_on_new_data_and_keeps_unchecked_shares():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "models" / "KiCad").mkdir(parents=True)
        (Path(d) / "models" / "KiCad" / "A.kicad_mod").write_text('(footprint "A" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))')
        data = {"P1": {"footprint": {"file": "models/PcbLib/A.PcbLib", "kicad_mod": "models/KiCad/A.kicad_mod", "source": "kicad:X:A"}},
                "P2": {"footprint": {"file": "models/PcbLib/A.PcbLib", "kicad_mod": "models/KiCad/A.kicad_mod",
                                     "notes": ["shared with P1"]}},
                "P3": {"footprint": {"file": "models/PcbLib/B.PcbLib", "name": "B", "hash": "h-already"}},
                "P4": {"symbol": None, "footprint": None}}
        once = MG.migrate_data("librarian_state", 1, json.loads(json.dumps(data)), d)
        assert once["P1"]["footprint"]["hash"] and once["P1"]["footprint"]["name"] == "A"
        assert "hash" not in once["P2"]["footprint"], "a name-only share stays unchecked so the librarian re-checks it"
        assert once["P3"]["footprint"]["hash"] == "h-already"
        assert MG.migrate_data("librarian_state", 1, json.loads(json.dumps(once)), d) == once     # idempotent
        assert MG.migrate_data("librarian_state", 2, once, d) is once
        try:
            MG.migrate_data("librarian_state", 3, once, d)
            raise AssertionError
        except MG.SchemaTooNew:
            pass


def _build(d, name="lib", bom_text=UF.BOM, epoch=None):
    d = Path(d)
    (d / "bom.csv").write_text(bom_text, encoding="utf-8")
    if not (d / "cache").exists():
        seed_cache(d / "cache")
    env = {"SOURCE_DATE_EPOCH": str(epoch)} if epoch else {}
    with mock.patch.dict(os.environ, env):
        L.run(d / "bom.csv", out=d / name, offline=True, cache=d / "cache", rules={}, log=lambda *a: None)
    return d / name


def test_lock_records_inputs_and_is_stable():
    with tempfile.TemporaryDirectory() as d:
        lib = _build(d)
        lock = RB.read_lock(lib)
        assert lock["bom"] == {"file": "../bom.csv", "sha256": RB.file_sha(Path(d) / "bom.csv")}
        assert lock["settings"]["cache"] == "../cache" and lock["settings"]["rules_sha256"] == RB.rules_hash(RU.load({}))
        lm = lock["parts"]["LM358DR"]
        assert lm["built_with"] == {"ecadlib": __version__, "rules_sha256": lock["settings"]["rules_sha256"]}
        urls = {e["url"] for e in lm["inputs"]}
        assert any("Amplifier_Operational.kicad_symdir/LM358.kicad_sym" in u for u in urls), urls
        assert any("SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod" in u for u in urls), urls
        assert not any("/repository/tree" in u for u in urls)
        ee = lock["parts"]["EEPART1"]
        assert [e["url"] for e in ee["inputs"]] and all("easyeda.com" in e["url"] for e in ee["inputs"])
        for e in lm["inputs"]:                     # the recorded hash is the cached response's
            f = Path(d) / "cache" / f"{L.CachedHTTP(Path(d) / 'cache', offline=True)._key('GET', e['url'], None)}.json"
            assert RB.sha(json.loads(f.read_text())["text"]) == e["sha256"]
        assert lm["footprint"]["sha256"] == RB.file_sha(lib / lm["footprint"]["file"])
        assert lm["footprint"]["kicad_mod"]["sha256"] == RB.file_sha(lib / lm["footprint"]["kicad_mod"]["file"])
        assert lock["parts"]["NOWHERE1"]["symbol"] is None and lock["parts"]["NOWHERE1"]["footprint"] is None
        text, stamp = (lib / RB.LOCK).read_bytes(), (lib / MG.STAMP).read_bytes()
        L.run(Path(d) / "bom.csv", out=lib, offline=True, cache=Path(d) / "cache", rules={}, log=lambda *a: None)
        assert (lib / RB.LOCK).read_bytes() == text and (lib / MG.STAMP).read_bytes() == stamp, "a re-run changes nothing"
        assert json.loads(stamp)["kinds"] == {"librarian_state": 2, "plan": 1}


def test_rebuild_check_passes_then_reports_drift():
    with tempfile.TemporaryDirectory() as d:
        lib = _build(d)
        before = _tree(lib)
        rep = RB.check(lib, log=lambda *a: None)
        assert rep["ok"], RB.render(rep)
        c = rep["counts"]
        assert c["DRIFT"] == 0 and c["identical"] >= 8 and c["identical"] + c["normalised"] == len(rep["rows"]), c
        assert _tree(lib) == before, "--check changes nothing in the library"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert RB.main([str(lib), "--check"]) == 0
        assert "OK: the library rebuilds from its lock" in out.getvalue()
        # an input changed under the lock: the cached KiCad footprint now has a wider pad
        lock = RB.read_lock(lib)
        e = next(x for x in lock["parts"]["LM358DR"]["inputs"] if x["url"].endswith(".kicad_mod"))
        http = L.CachedHTTP(Path(d) / "cache", offline=True)
        f = Path(d) / "cache" / f"{http._key('GET', e['url'], None)}.json"
        j = json.loads(f.read_text())
        assert "(size 1.95 0.6)" in j["text"]
        j["text"] = j["text"].replace("(size 1.95 0.6)", "(size 2.25 0.6)")
        f.write_text(json.dumps(j))
        rep = RB.check(lib, log=lambda *a: None)
        assert not rep["ok"]
        assert any(x["part"] == "LM358DR" and x["what"] == "input" and "changed" in x["detail"] for x in rep["inputs"])
        assert any(r["part"] == "LM358DR" and r["result"] == "DRIFT" and r["file"].endswith(".PcbLib") for r in rep["rows"]), rep["rows"]
        assert not any(r["part"] == "RES-0603-10k" and r["result"] == "DRIFT" for r in rep["rows"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert RB.main([str(lib), "--check", "--json"]) == 1
        assert json.loads(out.getvalue())["counts"]["DRIFT"] >= 1
        # the BOM changed: reported as an input drift
        (Path(d) / "bom.csv").write_text(UF.BOM.replace("10k", "22k"))
        rep = RB.check(lib, log=lambda *a: None)
        assert any(x["what"] == "bom" for x in rep["inputs"]) and not rep["ok"]
        (Path(d) / "bom.csv").unlink()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert RB.main([str(lib), "--check"]) == 2


def test_rebuild_check_reuses_cached_datasheet_answers_and_never_calls_a_model():
    from tests import e2e_support as E
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        seed_cache(d / "cache")
        url = E.seed_datasheet(d / "cache", "DSPART16")
        (d / "bom.csv").write_text("Designator,Qty,Value,Footprint,Manufacturer,Mfr Part #,LCSC,Description,Datasheet\n"
                                   f"U3,1,DSPART16,,ACME,DSPART16,,Buck converter VQFN-16,{url}\n", encoding="utf-8")
        with E.FakeAnthropic() as fa, mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-e2e-test", "ANTHROPIC_BASE_URL": fa.url}):
            L.run(d / "bom.csv", out=d / "lib", offline=True, cache=d / "cache", rules={}, log=lambda *a: None)
            assert ("datasheet", "DSPART16") in fa.kinds, fa.kinds
        p = RB.read_lock(d / "lib")["parts"]["DSPART16"]
        assert p["footprint"]["source"] == f"datasheet:{url}", p
        ds = p["datasheet"]
        assert ds["url"] == url and len(ds["pdf_sha256"]) == 64 and ds["model"] and ds["provider"] == "anthropic", ds
        with E.FakeAnthropic() as fa2, mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ANTHROPIC_BASE_URL": fa2.url}):
            rep = RB.check(d / "lib", log=lambda *a: None)
            assert fa2.calls == [], "a rebuild never calls a model"
        assert rep["ok"], RB.render(rep)
        assert any(r["part"] == "DSPART16" and r["file"].endswith(".PcbLib") for r in rep["rows"])


def test_pcblib_dates_are_the_only_wall_clock_value():
    with tempfile.TemporaryDirectory() as d:
        a = _build(d, "a", epoch=1791936000)
        b = _build(d, "b", epoch=1791936000 + 86400 * 40 + 3601)
        fa, fb = a / "models" / "PcbLib" / "EEPART1.PcbLib", b / "models" / "PcbLib" / "EEPART1.PcbLib"
        assert fa.read_bytes() != fb.read_bytes()
        assert RB.norm_sha(fa) == RB.norm_sha(fb)
        for f in sorted(a.rglob("*")):             # everything else ecadlib writes for the library is byte-identical
            rel = f.relative_to(a)
            if f.is_file() and f.suffix not in (".PcbLib", ".md", ".json") and ".ecadlib" not in str(rel) \
                    and f.name != "timing.jsonl":   # how long each step took: wall clock by design (ecadlib.timing)
                assert f.read_bytes() == (b / rel).read_bytes(), rel
        la, lb = RB.read_lock(a), RB.read_lock(b)
        assert [r for r in RB.compare(la, lb) if r["result"] == "DRIFT"] == []
        assert {r["result"] for r in RB.compare(la, lb)} == {"identical", "normalised"}


def test_recorder_restores_the_client_and_never_breaks_a_fetch():
    class Resp:
        def __init__(self, status, text):
            self.status, self.text = status, text

    class Fake:
        def get(self, url, **kw):
            return Resp(200, "body " + url) if "ok" in url else Resp(404, "")

        def post(self, url, body, **kw):
            return Resp(200, "posted")

    h = Fake()
    rec = RB.Recorder(h)
    with rec.part("P"):
        h.get("https://x/ok?token=SECRET")
        h.get("https://x/missing")
        h.post("https://x/q", '{"a": 1}')
        h.get("https://gitlab.com/api/v4/projects/x/repository/tree?ref=master")
    h.get("https://x/ok-outside")
    got = rec.take("P")
    assert [e["url"] for e in got] == ["https://x/ok?token=REDACTED", "https://x/q"], got
    assert got[1]["body_sha256"] == RB.sha('{"a": 1}') and got[0]["sha256"] == RB.sha("body https://x/ok?token=SECRET")
    rec.close()
    assert "get" not in h.__dict__ and "post" not in h.__dict__
    h.lam = None
    g = type("G", (), {})()
    g.get = lambda url, **kw: Resp(200, "x")                   # an instance attribute is put back as it was
    orig = g.get
    RB.Recorder(g).close()
    assert g.get is orig


def test_lock_datasheet_entry_from_the_extraction_sidecar():
    from ecadlib import datasheet as D
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "datasheet").mkdir()
        (Path(d) / "datasheet" / "X.extraction.json").write_text(json.dumps(
            {"datasheet": "https://e/x.pdf", "model": "claude-opus-4-1", "pdf": {"url": "https://e/x.pdf", "sha256": "ab" * 32},
             "ai": {"provider": "anthropic", "model": "claude-opus-4-1"}}))
        e = RB._datasheet(d, "https://e/x.pdf")
        assert e == {"url": "https://e/x.pdf", "pdf_sha256": "ab" * 32, "model": "claude-opus-4-1", "provider": "anthropic",
                     "schema": D.schema_version(), "extraction": "datasheet/X.extraction.json"}
        assert RB._datasheet(d, "https://other/y.pdf")["pdf_sha256"] is None


def test_doctor_reports_old_and_new_formats():
    from ecadlib import doctor as DR
    with _fixture("v0.3.0") as (ws, man):
        c = DR.check_formats(ws)
        assert c["level"] == "WARN" and "librarian_state" in c["detail"] and "ecadlib migrate" in c["hint"], c
        with contextlib.redirect_stdout(io.StringIO()):
            MG.main([str(ws)])
        assert DR.check_formats(ws)["level"] == "OK"
        (ws / "librarian" / "board" / MG.STAMP).write_text(json.dumps({"schema_version": 1, "ecadlib": "8.0", "kinds": {"plan": 3}}))
        c = DR.check_formats(ws)
        assert c["level"] == "FAIL" and "8.0" in c["detail"] and "upgrade ecadlib" in c["hint"], c
        assert any(x["check"] == "file formats" for x in DR.run(str(ws))["checks"])


def test_workspace_and_mcp_tools():
    from ecadlib import mcp_server as M
    assert {"file_formats", "rebuild_check"} <= {t["name"] for t in M.TOOLS}
    with _fixture("v0.3.0") as (ws, man):
        w = Workspace(ws, offline=True)
        r = M.call_tool(w, "file_formats", {})
        assert r["old"] == 1 and not r["migrated"] and r["rows"][0]["path"] and not r["rows"][0]["path"].startswith("/"), r
        try:
            M.call_tool(w, "rebuild_check", {"bom": "uploads/board.csv"})
            raise AssertionError("no lock yet")
        except ValueError as e:
            assert "library.lock.json" in str(e)
        assert M.call_tool(w, "file_formats", {"migrate": True})["migrated"]
        assert M.call_tool(w, "file_formats", {})["old"] == 0
        w.librarian_run("uploads/board.csv")
        rep = M.call_tool(w, "rebuild_check", {"bom": "uploads/board.csv"})
        # EEPART1 (ready, kept from 0.3.0) rebuilds with today's EasyEDA converter, which places REF** / value above /
        # below the courtyard and fp_tidy tidies it: its footprint files drift, nothing else does
        assert rep["lock"] == "librarian/board/library.lock.json", RB.render(rep)
        drift = {(r["part"], Path(r["file"]).suffix) for r in rep["rows"] if r["result"] == "DRIFT"}
        assert drift <= {("EEPART1", ".PcbLib"), ("EEPART1", ".kicad_mod")}, RB.render(rep)
        assert {d["part"] for d in rep["inputs"] if d["what"] == "inputs"} == {"EEPART1"}   # kept from 0.3.0: not recorded


def test_cli_lists_migrate_and_rebuild():
    from ecadlib import cli
    assert cli.COMMANDS["migrate"][0] == "ecadlib.migrate" and cli.COMMANDS["rebuild"][0] == "ecadlib.rebuild"
    with tempfile.TemporaryDirectory() as d:
        lib = _build(d)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert cli.main(["rebuild", str(lib), "--check"]) == 0
            assert cli.main(["migrate", str(d), "--check"]) == 0
        assert "rebuilds from its lock" in out.getvalue() and "0 would be migrated" in out.getvalue()


if __name__ == "__main__":
    import sys
    fails = 0
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            try:
                f()
                print(f"ok   {n}")
            except Exception as e:
                fails += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {n}: {e}")
    sys.exit(1 if fails else 0)
