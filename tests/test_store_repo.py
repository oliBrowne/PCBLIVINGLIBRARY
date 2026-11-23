"""Shared store, the store repository's side (ecadlib/store_repo.py): init, add, index, verify, withdraw, dispute,
snapshot and stats, read back through the real client (store_client.Store). Real records of tests/test_store_record
(the fake KiCad library's parts), all offline; the base of a pull request is a copy of the store folder (--old)."""
import contextlib, copy, io, json, re, shutil
from pathlib import Path

from ecadlib import fp_share as FS, store_client as SC, store_keys as SK, store_record as SR, store_repo as SR2
from tests.fixtures_lite import lite
from tests.test_store_record import AT, _ev, _kicad, _offline

TPS = ("ACME", "TPS1234DSGR")
MOVE = ("(at -2.4725 -1.905)", "(at -2.6 -1.905)")


def run(*argv):
    """main(argv) -> (exit code, printed text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = SR2.main([str(a) for a in argv])
    return code, buf.getvalue()


def rec_of(name="TPS1234DSGR", by="alice", at=AT, **kw):
    items, base = _kicad()
    it = items[name]
    it.setdefault("field_sources", {})["Description"] = "KiCad library"    # a source gave it: shared (not the BOM's)
    rec = SR.from_item(it, base, by=by, at=at)
    rec.update(kw)
    return rec


def rehash(rec):
    """Hashes and evidence made to match the record's (edited) drawing and pins, so validate passes."""
    rec["footprint"]["hash"] = FS.digest(rec["footprint"]["kicad_mod"])
    for e in rec["evidence"]:
        e["footprint_hash"], e["pins_hash"] = rec["footprint"]["hash"], SR.pins_hash(rec)
    return rec


def moved(rec):
    out = copy.deepcopy(rec)
    assert MOVE[0] in out["footprint"]["kicad_mod"]
    out["footprint"]["kicad_mod"] = out["footprint"]["kicad_mod"].replace(*MOVE, 1)
    return rehash(out)


def put(path, rec):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rec), encoding="utf-8")
    return path


def store(tmp_path, *recs, maintainers=()):
    """A store seeded by its maintainer (add --maintainer: also codes, several builds in one file are allowed)."""
    root = Path(tmp_path) / "store"
    SR2.init(root, name="test", maintainers=maintainers, at=AT)
    for i, r in enumerate(recs):
        code, out = run("add", root, put(Path(tmp_path) / "in" / f"{i}.json", r), "--at", AT, "--maintainer")
        assert code == 0, out
    return root


def rec_path(root, maker, mpn):
    k = SK.key(maker, mpn)
    return Path(root) / "parts" / SK.shard(k) / f"{SK.key_id(k)}.json"


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


# ---------------------------------------------------------------- init
@lite
def test_init_writes_the_layout_and_never_overwrites(tmp_path, monkeypatch):
    root = Path(tmp_path) / "s"
    code, out = run("init", root, "--name", "parts", "--maintainer", "oli", "--maintainer", "bot", "--at", AT)
    assert code == 0, out
    meta = json.loads((root / "store.json").read_text())
    assert meta["schema"] == 1 and meta["name"] == "parts" and meta["shards"] == 256 and meta["maintainers"] == ["oli",
                                                                                                                  "bot"]
    assert meta["min_ecadlib"] == SR2.__version__ and meta["updated"] == AT
    assert json.loads((root / "withdrawn.json").read_text()) == [] and (root / "parts").is_dir()
    for f in ("README.md", "LICENSE", "NOTICE.md", "CONTRIBUTING.md", ".github/workflows/verify.yml",
              ".github/workflows/nightly.yml", ".github/workflows/snapshot.yml", ".github/ISSUE_TEMPLATE/bad-part.yml",
              ".github/PULL_REQUEST_TEMPLATE.md"):
        assert (root / f).is_file(), f
    (root / "README.md").write_text("mine")
    before = tree(root)
    assert run("init", root, "--name", "other")[0] == 0 and tree(root) == before
    assert run("init", root, "--force")[0] == 0 and (root / "README.md").read_text() != "mine"
    assert SC.Store(str(root)).usable()
    assert run("verify", root)[0] == 0                       # an empty store passes


# ---------------------------------------------------------------- add, index, the real client
@lite
def test_add_then_the_client_finds_it(tmp_path, monkeypatch):
    _offline(monkeypatch)
    rec = rec_of(also=["TPS1234DSGT"])
    root = store(tmp_path, rec)
    f = rec_path(root, *TPS)
    assert f.is_file() and json.loads(f.read_text())["rev"] == 1
    st = SC.Store(str(root))
    h = st.lookup("Acme", "tps1234dsgr")
    assert h and h.level == "checked" and h.status == "active" and h.rev == 1 and st.trouble == [], st.trouble
    assert h.sha256 == SK.digest(json.loads(f.read_text())) and SC.decide(h) == "second-opinion"
    assert SK.shard("TPS1234DSGT") != SK.shard("TPS1234DSGR")      # the also code is in another shard ...
    h = SC.Store(str(root)).lookup("ACME", "TPS1234DSGT")           # ... and still found there
    assert h and h.key == SK.key(*TPS) and "TPS1234DSGT" in h.note
    h = SC.Store(str(root)).lookup("", "TPS1234DSGT")
    assert h and "without a maker" in h.note
    assert SC.Store(str(root)).lookup("Other Co", "TPS1234DSGT") is None
    # idempotent: a second index (and a second add of the same record) changes no byte
    before = tree(root)
    assert run("index", root)[0] == 0 and tree(root) == before
    assert run("add", root, Path(tmp_path) / "in" / "0.json")[0] == 0 and tree(root) == before
    assert run("index", root, "--check")[0] == 0
    assert run("verify", root, "--all")[0] == 0


@lite
def test_add_takes_a_share_folder(tmp_path, monkeypatch):
    _offline(monkeypatch)
    rec = rec_of()
    k = rec["key"]["key"]
    put(Path(tmp_path) / "share" / "parts" / SK.shard(k) / f"{SK.key_id(k)}.json", rec)
    root = store(tmp_path)
    code, out = run("add", root, Path(tmp_path) / "share")
    assert code == 0, out
    assert SC.Store(str(root)).lookup(*TPS)


@lite
def test_second_contributor_confirms(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path, rec_of(by="alice"), rec_of(by="bob", at="2026-11-25T08:00:00Z"))
    rec = json.loads(rec_path(root, *TPS).read_text())
    assert rec["rev"] == 1 and [e["by"] for e in rec["evidence"]] == ["alice", "bob"]   # evidence only: same rev
    h = SC.Store(str(root)).lookup(*TPS)
    assert h.level == "confirmed" and SC.decide(h) == "use"
    assert run("verify", root, "--all")[0] == 0


@lite
def test_trusted_list_limits_whose_evidence_counts(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path, rec_of(by="alice"), rec_of(by="bob", at="2026-11-25T08:00:00Z"))
    meta = json.loads((root / "store.json").read_text())
    (root / "store.json").write_text(json.dumps(dict(meta, trusted=["alice"])))
    assert run("index", root)[0] == 0
    assert SC.Store(str(root)).lookup(*TPS).level == "checked"


@lite
def test_maintainer_seed_with_two_models(tmp_path, monkeypatch):
    _offline(monkeypatch)
    rec = rec_of(by="oli")
    rec["evidence"] = [_ev(rec, "oli", "opus"), _ev(rec, "oli", "haiku", at="2026-11-24T11:00:00Z")]
    assert SR.validate(rec) == []
    plain = store(Path(tmp_path) / "a", rec)
    assert SC.Store(str(plain)).lookup(*TPS).level == "checked"
    seeded = store(Path(tmp_path) / "b", rec, maintainers=["oli"])
    assert SC.Store(str(seeded)).lookup(*TPS).level == "confirmed"


@lite
def test_conflict_refused_and_disputed_only_on_request(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path, rec_of(by="alice"))
    before = tree(root)
    other = put(Path(tmp_path) / "in" / "bob.json", moved(rec_of(by="bob")))
    code, out = run("add", root, other)
    assert code == 1 and "pads" in out and "conflict" in out.lower(), out
    assert tree(root) == before                                       # nothing written
    assert run("add", root, other, "--dispute")[0] == 2               # disputing is a maintainer's
    assert tree(root) == before
    code, out = run("add", root, other, "--dispute", "--maintainer", "--at", "2026-11-26T00:00:00Z")
    assert code == 1
    rec = json.loads(rec_path(root, *TPS).read_text())
    assert rec["status"] == "disputed" and rec["rev"] == 1 and len(rec["evidence"]) == 1
    h = SC.Store(str(root)).lookup(*TPS)
    assert h.status == "disputed" and SC.decide(h, "checked") == "second-opinion"
    log = json.loads((root / "disputes.json").read_text())
    assert log[0]["key"] == SK.key(*TPS) and "pads" in log[0]["reason"]
    assert run("verify", root, "--all")[0] == 0                       # a disputed store is still a consistent store


@lite
def test_add_refuses_an_invalid_record(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path)
    bad = rec_of()
    bad["footprint"]["kicad_mod"] = bad["footprint"]["kicad_mod"].replace(*MOVE, 1)      # pads moved, hash not
    before = tree(root)
    code, out = run("add", root, put(Path(tmp_path) / "bad.json", bad))
    assert code == 1 and "hash" in out and tree(root) == before


# ---------------------------------------------------------------- verify
def _pr(tmp_path, *recs, maintainers=()):
    """(base store, the pull request's copy of it)."""
    base = store(Path(tmp_path) / "b", *recs, maintainers=maintainers)
    pr = Path(tmp_path) / "pr"
    shutil.copytree(base, pr)
    return base, pr


def _verify(pr, base, *more):
    return run("verify", pr, "--old", base, *more)


@lite
def test_verify_passes_a_clean_change(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, pr = _pr(tmp_path, rec_of(by="alice"))
    assert run("add", pr, put(Path(tmp_path) / "bob.json", rec_of(by="bob", at="2026-11-25T08:00:00Z")))[0] == 0
    code, out = _verify(pr, base, "--author", "bob")
    assert code == 0, out
    assert "confirmed" in out                                         # the maintainer is told it promotes the part
    code, out = _verify(pr, base, "--author", "mallory")             # evidence in someone else's name
    assert code == 1 and "mallory" in out, out


@lite
def test_verify_fails_a_tampered_pad(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, pr = _pr(tmp_path, rec_of())
    f = rec_path(pr, *TPS)
    rec = json.loads(f.read_text())
    rec["footprint"]["kicad_mod"] = rec["footprint"]["kicad_mod"].replace(*MOVE, 1)
    f.write_text(json.dumps(rec))
    run("index", pr)
    code, out = _verify(pr, base)
    assert code == 1 and "hash" in out, out
    put(f, moved(json.loads(rec_path(base, *TPS).read_text())))       # hashes made to match: still the same rev
    run("index", pr)
    code, out = _verify(pr, base)
    assert code == 1 and "without a new rev" in out, out


@lite
def test_verify_fails_a_wrong_path(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, pr = _pr(tmp_path, rec_of())
    f = rec_path(pr, *TPS)
    wrong = pr / "parts" / ("00" if f.parent.name != "00" else "01") / f.name
    wrong.parent.mkdir(parents=True, exist_ok=True)
    f.rename(wrong)
    code, out = run("verify", pr, "--all")
    assert code == 1 and "shard" in out, out
    wrong.rename(f.parent / ("0" * 32 + ".json"))
    code, out = run("verify", pr, "--all")
    assert code == 1 and "key_id" in out, out


@lite
def test_verify_evidence_only_appended(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, pr = _pr(tmp_path, rec_of(by="alice"), rec_of(by="bob", at="2026-11-25T08:00:00Z"))
    f = rec_path(pr, *TPS)
    for edit in (lambda r: r["evidence"].pop(1), lambda r: r["evidence"].pop(0),
                 lambda r: r["evidence"][0].update(at="2026-01-01T00:00:00Z")):
        rec = json.loads(rec_path(base, *TPS).read_text())
        edit(rec)
        f.write_text(json.dumps(rec))
        run("index", pr)
        code, out = _verify(pr, base)
        assert code == 1 and "evidence" in out and "maintainer-only" in out, out


@lite
def test_verify_contributor_signoff_is_maintainer_only(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, pr = _pr(tmp_path, rec_of(by="alice"))
    f = rec_path(pr, *TPS)
    rec = json.loads(f.read_text())
    for kind in ("signoff", "kicad-exact"):
        r = SR.add_evidence(rec, _ev(rec, "mallory", kind=kind, at="2026-11-27T00:00:00Z"))
        f.write_text(json.dumps(r))
        run("index", pr)
        code, out = _verify(pr, base)
        assert code == 1 and kind in out and "maintainer-only" in out, out
        assert _verify(pr, base, "--maintainer")[0] == 0


@lite
def test_verify_one_contributor_per_change(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, pr = _pr(tmp_path, rec_of(by="alice"))
    f = rec_path(pr, *TPS)
    rec = json.loads(f.read_text())
    rec = SR.add_evidence(SR.add_evidence(rec, _ev(rec, "sock1", at="2026-11-27T00:00:00Z")),
                          _ev(rec, "sock2", at="2026-11-27T00:00:01Z"))
    f.write_text(json.dumps(rec))
    run("index", pr)
    code, out = _verify(pr, base)
    assert code == 1 and "sock1" in out and "sock2" in out, out


@lite
def test_verify_status_and_store_files_maintainer_only(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base = store(Path(tmp_path) / "b", rec_of(by="alice"))
    run("dispute", base, SK.key(*TPS), "--reason", "pad 3 looks wide", "--at", AT)
    pr = Path(tmp_path) / "pr"
    shutil.copytree(base, pr)
    f = rec_path(pr, *TPS)
    f.write_text(json.dumps(dict(json.loads(f.read_text()), status="active")))
    run("index", pr)
    code, out = _verify(pr, base)
    assert code == 1 and "disputed" in out and "maintainer-only" in out, out
    assert _verify(pr, base, "--maintainer")[0] == 0
    shutil.rmtree(pr)
    shutil.copytree(base, pr)
    meta = json.loads((pr / "store.json").read_text())
    (pr / "store.json").write_text(json.dumps(dict(meta, maintainers=["mallory"])))
    code, out = _verify(pr, base)
    assert code == 1 and "maintainers" in out and "maintainer-only" in out, out
    (pr / "store.json").write_text(json.dumps(dict(meta, updated="2027-01-01T00:00:00Z")))     # only the date: fine
    assert _verify(pr, base)[0] == 0
    (pr / "withdrawn.json").write_text("[]\n ")
    code, out = _verify(pr, base)
    assert code == 1 and "withdrawn.json" in out


@lite
def test_verify_fails_a_stale_index(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, pr = _pr(tmp_path, rec_of(by="alice"))
    f = rec_path(pr, *TPS)
    rec = json.loads(f.read_text())
    f.write_text(json.dumps(SR.add_evidence(rec, _ev(rec, "bob", at="2026-11-27T00:00:00Z"))))
    code, out = _verify(pr, base)
    assert code == 1 and "index" in out and "not up to date" in out, out
    assert run("index", pr, "--check")[0] == 1
    run("index", pr)
    assert _verify(pr, base)[0] == 0


@lite
def test_verify_fails_forbidden_files(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path, rec_of())
    for rel, data in (("models/R.step", b"ISO-10303-21;"), ("parts/ds.pdf", b"%PDF-1.7"), ("snapshot.zip", b"PK"),
                      ("tools/run.py", b"print(1)"), ("ecadlib.toml", b"[release]"),
                      ("notes.md", b"x" * 500_000)):
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        code, out = run("verify", root)
        assert code == 1 and rel in out, (rel, out)
        f.unlink()
    assert run("verify", root)[0] == 0


@lite
def test_verify_fails_a_record_whose_rebuild_would_be_held(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path, rec_of())
    f = rec_path(root, *TPS)
    good = json.loads(f.read_text())
    nocrt = copy.deepcopy(good)                                       # no courtyard: the release check holds it
    t = nocrt["footprint"]["kicad_mod"]
    nocrt["footprint"]["kicad_mod"] = "\n".join(l for l in t.splitlines() if "CrtYd" not in l) + "\n"
    assert "CrtYd" not in nocrt["footprint"]["kicad_mod"] and SR.validate(rehash(nocrt)) == []
    f.write_text(json.dumps(nocrt))
    run("index", root)
    code, out = run("verify", root, "--all")
    assert code == 1 and "held" in out and "courtyard" in out.lower(), out
    assert "withdraw" in out                                          # the nightly prints the command, never runs it
    # add refuses it too
    root2 = store(Path(tmp_path) / "two")
    code, out = run("add", root2, put(Path(tmp_path) / "nocrt.json", nocrt))
    assert code == 1 and "held" in out and not rec_path(root2, *TPS).exists()


@lite
def test_verify_changed_list_and_git_base(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, pr = _pr(tmp_path, rec_of(by="alice"))
    f = rec_path(pr, *TPS)
    rel = f.relative_to(pr).as_posix()
    rec = json.loads(f.read_text())
    f.write_text(json.dumps(SR.add_evidence(rec, _ev(rec, "mallory", kind="signoff", at="2026-11-27T00:00:00Z"))))
    run("index", pr)
    code, out = run("verify", pr, "--changed", rel)                 # no base: every entry counts as contributed
    assert code == 1 and "signoff" in out
    calls = []

    def fake_git(root, *args):
        calls.append(args)
        if args[0] == "diff":
            assert "-z" in args and "--name-only" in args and "--no-renames" in args, args
            return f"{rel}\0index/{f.parent.name}.json\0"
        if args[0] == "ls-files":
            return ""
        assert args[0] == "show"
        p = base / args[1].split(":", 1)[1].lstrip("./")
        return p.read_text() if p.is_file() else None
    monkeypatch.setattr(SR2, "_git", fake_git)
    code, out = run("verify", pr, "--base", "origin/main")
    assert code == 1 and "signoff" in out and "maintainer-only" in out, out
    assert any(a[0] == "diff" and "origin/main" in a for a in calls)


# ---------------------------------------------------------------- withdraw, dispute
@lite
def test_withdraw_then_a_corrected_rev(tmp_path, monkeypatch):
    _offline(monkeypatch)
    rec = rec_of(by="alice")
    root = store(tmp_path, rec)
    k, kid = SK.key(*TPS), SK.key_id(SK.key(*TPS))
    code, out = run("withdraw", root, "Acme|TPS1234DSGR", "--reason", "pad 1 pitch wrong", "--issue", "#7", "--at", AT)
    assert code == 0, out
    assert json.loads((root / "withdrawn.json").read_text()) == [
        {"key_id": kid, "key": k, "rev": 1, "reason": "pad 1 pitch wrong", "issue": "#7", "at": AT}]
    assert not rec_path(root, *TPS).exists()
    assert (root / "withdrawn" / SK.shard(k) / f"{kid}.r1.json").is_file()      # kept for the audit trail
    st = SC.Store(str(root))
    assert st.lookup(*TPS) is None and st.is_withdrawn(kid, 1)["reason"] == "pad 1 pitch wrong"
    assert run("verify", root, "--all")[0] == 0
    code, out = run("add", root, Path(tmp_path) / "in" / "0.json")             # the same wrong part again: refused
    assert code == 1 and "withdrawn" in out and not rec_path(root, *TPS).exists(), out
    fixed = put(Path(tmp_path) / "fixed.json", moved(rec_of(by="bob")))
    code, out = run("add", root, fixed)                                   # bringing a withdrawn part back: a maintainer's
    assert code == 1 and "withdrawn" in out and not rec_path(root, *TPS).exists(), out
    code, out = run("add", root, fixed, "--maintainer")
    assert code == 0, out
    h = SC.Store(str(root)).lookup(*TPS)
    assert h and h.rev == 2 and not SC.Store(str(root)).is_withdrawn(kid, 2)
    assert run("verify", root, "--all")[0] == 0
    f = rec_path(root, *TPS)                                       # a record at a withdrawn rev under parts/: fails
    f.write_text(json.dumps(dict(json.loads(f.read_text()), rev=1)))
    code, out = run("verify", root, "--all")
    assert code == 1 and "withdrawn" in out
    assert run("index", root)[0] == 1 and SC.Store(str(root)).entry(kid, TPS[1]) is None     # never indexed
    assert run("withdraw", root, "ACME|NOPE", "--reason", "x")[0] == 2
    assert run("withdraw", root, k)[0] == 2                        # --reason is required


@lite
def test_dispute_by_key_id(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path, rec_of(by="alice"), rec_of(by="bob", at="2026-11-25T08:00:00Z"))
    kid = SK.key_id(SK.key(*TPS))
    assert SC.Store(str(root)).lookup(*TPS).level == "confirmed"
    assert run("dispute", root, kid, "--reason", "pin 3 is EN, not FB", "--at", AT)[0] == 0
    h = SC.Store(str(root)).lookup(*TPS)
    assert h.status == "disputed" and h.rev == 1 and h.level == "checked" and SC.decide(h) == "second-opinion"


# ---------------------------------------------------------------- snapshot, stats
class ZipHTTP:
    def __init__(self, data):
        self.data, self.urls = data, []

    def get(self, url, **kw):
        raise OSError("snapshot mode asks for nothing but the zip")

    def get_bytes(self, url):
        self.urls.append(url)
        return self.data


@lite
def test_snapshot_round_trip_and_deterministic(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path, rec_of(by="alice", also=["TPS1234DSGT"]))
    a, b = Path(tmp_path) / "a.zip", Path(tmp_path) / "b.zip"
    assert run("snapshot", root, "-o", a)[0] == 0 and run("snapshot", root, "-o", b)[0] == 0
    assert a.read_bytes() == b.read_bytes()
    import zipfile
    names = zipfile.ZipFile(a).namelist()
    assert {i.date_time for i in zipfile.ZipFile(a).infolist()} == {(1980, 1, 1, 0, 0, 0)}
    assert names == sorted(names) and "store.json" in names and "withdrawn.json" in names
    assert all(re.fullmatch(r"store\.json|withdrawn\.json|index/[0-9a-f]{2}\.json|parts/[0-9a-f]{2}/[0-9a-f]{32}\.json",
                            n) for n in names), names
    http = ZipHTTP(a.read_bytes())
    url = "https://example.com/store"
    st = SC.Store(url, http=http, cache_dir=Path(tmp_path) / "c", mode="snapshot",
                  snapshot_url=url + "/releases/snapshot.zip")
    h = st.lookup(*TPS)
    assert h and h.level == "checked" and st.trouble == [] and http.urls == [url + "/releases/snapshot.zip"]
    assert st.lookup("ACME", "TPS1234DSGT")


@lite
def test_stats(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path, rec_of("TPS1234DSGR", by="alice"), rec_of("TPS1234DSGR", by="bob"),
                 rec_of("RES-0603-10k", by="alice"))
    s = SR2.stats(root)
    assert s["parts"] == 2 and s["level"] == {"checked": 1, "confirmed": 1} and s["status"] == {"active": 2}
    assert s["source"] == {"kicad": 2} and s["license"] == {"CC-BY-SA-4.0": 2} and s["withdrawn"] == 0
    code, out = run("stats", root)
    assert code == 0 and "confirmed" in out


# ---------------------------------------------------------------- the store repository's own files
def test_template_workflows_are_safe():
    tpl = SR2.TEMPLATE
    files = SR2.template_files()
    assert {".github/workflows/verify.yml", ".github/workflows/nightly.yml", ".github/workflows/snapshot.yml",
            ".github/ISSUE_TEMPLATE/bad-part.yml", ".github/PULL_REQUEST_TEMPLATE.md", "README.md", "LICENSE",
            "NOTICE.md", "CONTRIBUTING.md"} <= set(files), files
    try:
        import yaml
    except ImportError:
        yaml = None
    for rel, src in files.items():
        text = Path(src).read_text(encoding="utf-8")
        assert "pull_request_target" not in text, rel
        if rel.endswith(".yml"):
            if yaml is not None:
                d = yaml.safe_load(text)
                assert isinstance(d, dict), rel
            if "/workflows/" in rel:
                assert re.search(r"(?m)^permissions:\n  contents: (read|write)", text), rel
                assert "${{ secrets." not in text, rel
                for use in re.findall(r"uses: (\S+)", text):
                    assert re.fullmatch(r"[\w-]+/[\w-]+@v\d+", use), (rel, use)
    v = (tpl / "github" / "workflows" / "verify.yml").read_text()
    assert re.search(r"(?m)^on:\n  pull_request:", v) and "contents: read" in v and not re.search(r":\s*write", v)
    assert "store-repo verify" in v and "--base" in v and "persist-credentials: false" in v
    assert "${{ github" not in "\n".join(l for l in v.splitlines() if l.strip().startswith("run:")), \
        "untrusted values go through env, never into a run line"
    n = (tpl / "github" / "workflows" / "nightly.yml").read_text()
    assert "schedule:" in n and "--all" in n and "git push" not in n and "contents: write" not in n
    s = (tpl / "github" / "workflows" / "snapshot.yml").read_text()
    assert "schedule:" in s and "store-repo snapshot" in s and "snapshot.zip" in s
    notice = (tpl / "NOTICE.md").read_text()
    for word in ("MIT", "CC-BY-SA 4.0", "KiCad", "EasyEDA", "pointer"):
        assert word in notice, word


# ---------------------------------------------------------------- review findings: contributor rules
def pointer(rec, lcsc="C999999"):
    """The record turned into an EasyEDA pointer record (no drawing; only an LCSC number and the hashes)."""
    out = copy.deepcopy(rec)
    for half, text in (("symbol", "kicad_sym"), ("footprint", "kicad_mod")):
        out[half].update({"source": f"easyeda:{lcsc}", text: None, "pointer": {"lcsc": lcsc},
                          "license": SR.LICENSE["easyeda"], "credit": SR.credit_of("easyeda")})
    return out


def _new_in_pr(tmp_path, rec, *base_recs, maintainers=("oli",)):
    """(base, pr): rec written by hand into the pull request's copy, index rebuilt."""
    base, pr = _pr(tmp_path, *base_recs, maintainers=maintainers)
    put(pr / SR2.rel_path(rec), rec)
    run("index", pr)
    return base, pr


def check_index(root):
    """The index is exactly what the strict client accepts: keyed by key_id(entry key), listed in the shard of the key
    and of every also code, rev / status / sha256 the record's, level recomputed with store.json's trusted and
    maintainers (a pointer record never above checked)."""
    meta = json.loads((Path(root) / "store.json").read_text())
    trusted, maint = SR2._people(meta)
    seen = {}
    for f in sorted((Path(root) / "index").glob("*.json")):
        for kid, e in json.loads(f.read_text()).items():
            assert kid == SK.key_id(e["key"]), (f.name, kid, e["key"])
            rec = json.loads((Path(root) / SR2.rel_path({"key": {"key": e["key"]}})).read_text())
            assert rec["key"]["key"] == e["key"] and e["rev"] == rec["rev"] and e["status"] == rec["status"]
            assert e["sha256"] == SK.digest(rec) and e["also"] == SR2.also_keys(rec)
            want = SR.level(rec, trusted, maint)
            if SR2.is_pointer(rec):
                want = "checked"
            assert e["level"] == want, (e, want)
            assert f.stem in {SK.shard(e["key"])} | {SK.shard(a) for a in e["also"]}
            seen.setdefault(kid, set()).add(f.stem)
    for kid, shards in seen.items():
        e = json.loads((Path(root) / "index" / f"{sorted(shards)[0]}.json").read_text())[kid]
        assert shards == {SK.shard(e["key"])} | {SK.shard(a) for a in e["also"]}, (kid, shards)
    return seen


@lite
def test_also_on_a_new_record_is_maintainer_only(tmp_path, monkeypatch):
    """probe_also / test_also_on_new_record: a contributor's NEW record claiming another code (LM7805CT) took over
    lookups of that code. also codes are a maintainer's, on a new record as on an existing one."""
    _offline(monkeypatch)
    rec = rec_of(by="mallory", also=["LM7805CT"])
    base, pr = _new_in_pr(tmp_path, rec)
    code, out = _verify(pr, base, "--author", "mallory")
    assert code == 1 and "also" in out and "maintainer-only" in out and "LM7805CT" in out, out
    assert _verify(pr, base, "--author", "mallory", "--maintainer")[0] == 0
    root = store(Path(tmp_path) / "c")
    before = tree(root)
    code, out = run("add", root, put(Path(tmp_path) / "m.json", rec))
    assert code == 1 and "also" in out and tree(root) == before, out
    assert SC.Store(str(root)).lookup("ACME", "LM7805CT") is None
    assert SC.Store(str(root)).lookup("", "LM7805CT") is None
    # an existing record: a contributor's incoming also is refused too (merge never takes it)
    root2 = store(Path(tmp_path) / "d", rec_of(by="alice"))
    before = tree(root2)
    code, out = run("add", root2, put(Path(tmp_path) / "b.json", rec_of(by="bob", also=["LM7805CT"])))
    assert code == 1 and "also" in out and tree(root2) == before, out
    code, out = run("add", root2, Path(tmp_path) / "b.json", "--maintainer")       # a maintainer may add codes
    assert code == 0, out
    assert json.loads(rec_path(root2, *TPS).read_text())["also"] == ["LM7805CT"]
    check_index(root2)


@lite
def test_a_withdrawn_part_comes_back_only_through_a_maintainer(tmp_path, monkeypatch):
    """probe_unwithdraw: a contributor re-added a withdrawn part at rev+1 with slightly moved pads and it was served."""
    _offline(monkeypatch)
    base = store(Path(tmp_path) / "b", rec_of(by="alice"), maintainers=["oli"])
    assert run("withdraw", base, SK.key(*TPS), "--reason", "pin 1 wrong", "--at", AT)[0] == 0
    pr = Path(tmp_path) / "pr"
    shutil.copytree(base, pr)
    m = moved(rec_of(by="mallory"))
    code, out = run("add", pr, put(Path(tmp_path) / "m.json", m))
    assert code == 1 and "withdrawn" in out and not rec_path(pr, *TPS).exists(), out
    put(rec_path(pr, *TPS), dict(m, rev=2))                     # by hand, at the rev a maintainer would give it
    run("index", pr)
    code, out = _verify(pr, base, "--author", "mallory")
    assert code == 1 and "withdrawn" in out and "maintainer-only" in out, out
    assert _verify(pr, base, "--author", "mallory", "--maintainer")[0] == 0
    assert SC.Store(str(base)).lookup(*TPS) is None
    # evidence added later to the corrected rev of a once-withdrawn part: still the maintainer's
    shutil.rmtree(base)
    shutil.copytree(pr, base)
    r2 = json.loads(rec_path(pr, *TPS).read_text())
    put(rec_path(pr, *TPS), SR.add_evidence(r2, _ev(r2, "bob", at="2026-11-28T00:00:00Z")))
    run("index", pr)
    code, out = _verify(pr, base, "--author", "bob")
    assert code == 1 and "withdrawn" in out and "maintainer-only" in out, out
    # a new file past rev 1 without any withdrawal: a maintainer's
    base2, pr2 = _new_in_pr(Path(tmp_path) / "x", rec_of(by="mallory", rev=3))
    code, out = _verify(pr2, base2, "--author", "mallory")
    assert code == 1 and "r3" in out and "maintainer-only" in out, out


@lite
def test_add_refuses_a_contributors_signoff(tmp_path, monkeypatch):
    """test_add_merges_signoff: add merged a contributor's signoff without a word and the part became confirmed."""
    _offline(monkeypatch)
    rec = rec_of(by="alice")
    root = store(tmp_path, rec, maintainers=["oli"])
    before = tree(root)
    for kind in ("signoff", "kicad-exact"):
        inc = SR.add_evidence(rec, dict(rec["evidence"][0], kind=kind, by="mallory"))
        code, out = run("add", root, put(Path(tmp_path) / f"{kind}.json", inc))
        assert code == 1 and kind in out and "maintainer" in out and tree(root) == before, out
        assert SC.Store(str(root)).lookup(*TPS).level == "checked"
    code, out = run("add", root, Path(tmp_path) / "signoff.json", "--maintainer")
    assert code == 0 and "confirmed" in out, out
    assert SC.Store(str(root)).lookup(*TPS).level == "confirmed"


@lite
def test_add_refuses_two_builders_in_one_file(tmp_path, monkeypatch):
    """test_add_two_builders_one_file: one new file carrying alice's and "bob"'s builds was confirmed at once."""
    _offline(monkeypatch)
    rec = rec_of(by="alice")
    rec["evidence"].append(_ev(rec, "bob", at="2026-11-24T00:00:00Z"))
    assert SR.validate(rec) == [] and SR.level(rec) == "confirmed"
    root = store(tmp_path)
    before = tree(root)
    code, out = run("add", root, put(Path(tmp_path) / "two.json", rec))
    assert code == 1 and tree(root) == before and "one" in out, out
    same = rec_of(by="alice")                                      # one person, two entries: still one build
    same["evidence"].append(_ev(same, "alice", "haiku", at="2026-11-24T00:00:00Z"))
    code, out = run("add", root, put(Path(tmp_path) / "same.json", same))
    assert code == 1 and tree(root) == before, out
    base, pr = _new_in_pr(Path(tmp_path) / "v", rec)
    code, out = _verify(pr, base)
    assert code == 1 and "bob" in out and "alice" in out, out
    assert _verify(pr, base, "--maintainer")[0] == 0
    code, out = run("add", root, Path(tmp_path) / "two.json", "--maintainer")
    assert code == 0, out


@lite
def test_add_one_contributor_per_call_and_author(tmp_path, monkeypatch):
    _offline(monkeypatch)
    root = store(tmp_path, rec_of("RES-0603-10k", by="alice"))
    a, b = put(Path(tmp_path) / "a.json", rec_of(by="bob")), put(Path(tmp_path) / "b.json",
                                                                 rec_of("RES-0603-10k", by="carol"))
    before = tree(root)
    code, out = run("add", root, a, b)
    assert code == 1 and "bob" in out and "carol" in out, out
    code, out = run("add", root, a, "--author", "carol")
    assert code == 1 and "carol" in out and tree(root) == before, out
    code, out = run("add", root, a, "--author", "bob")
    assert code == 0, out
    base = Path(tmp_path) / "base"                                  # verify: one contributor per change, across files
    shutil.copytree(root, base)
    res = SR2.rel_path(rec_of("RES-0603-10k"))
    r = json.loads((base / res).read_text())
    put(root / res, SR.add_evidence(r, _ev(r, "carol", at="2026-11-28T00:00:00Z")))
    t = json.loads(rec_path(base, *TPS).read_text())
    put(rec_path(root, *TPS), SR.add_evidence(t, _ev(t, "dave", at="2026-11-28T00:00:00Z")))
    run("index", root)
    code, out = _verify(root, base)
    assert code == 1 and "carol" in out and "dave" in out, out


@lite
def test_status_changes_are_maintainer_only(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, pr = _pr(tmp_path, rec_of(by="alice"))
    f = rec_path(pr, *TPS)
    f.write_text(json.dumps(dict(json.loads(f.read_text()), status="disputed")))
    run("index", pr)
    code, out = _verify(pr, base)
    assert code == 1 and "status" in out and "maintainer-only" in out, out
    assert _verify(pr, base, "--maintainer")[0] == 0


@lite
def test_pointer_records_are_maintainer_only_and_never_confirmed(tmp_path, monkeypatch):
    """An invented easyeda:C999999 pointer with copied hashes passed: nothing checks a pointer's claim in v1."""
    _offline(monkeypatch)
    rec = pointer(rec_of(by="mallory"))
    assert SR.validate(rec) == [], SR.validate(rec)
    root = store(Path(tmp_path) / "a")
    before = tree(root)
    code, out = run("add", root, put(Path(tmp_path) / "p.json", rec))
    assert code == 1 and "pointer records are not accepted from contributors yet" in out and tree(root) == before
    base, pr = _new_in_pr(Path(tmp_path) / "v", rec)
    code, out = _verify(pr, base, "--author", "mallory")
    assert code == 1 and "pointer records are not accepted from contributors yet" in out, out
    assert _verify(pr, base, "--author", "mallory", "--maintainer")[0] == 0
    two = copy.deepcopy(rec)                                         # two builders: confirmed, were it not a pointer
    two["evidence"].append(_ev(two, "bob", at="2026-11-24T00:00:00Z"))
    assert SR.level(two) == "confirmed"
    root = store(Path(tmp_path) / "b", two)
    e = json.loads((root / "index" / f"{SK.shard(SK.key(*TPS))}.json").read_text())[SK.key_id(SK.key(*TPS))]
    assert e["level"] == "checked", e
    check_index(root)
    assert SR2.stats(root)["level"] == {"checked": 1}


@lite
def test_check_rebuild_compares_the_rebuilt_part(tmp_path, monkeypatch):
    """The pin-map check compared the record with itself: compare the REBUILT symbol's pins and footprint's pads."""
    _offline(monkeypatch)
    good = rec_of()
    assert SR2.check_rebuild(good)[0] == []
    low = copy.deepcopy(good)                                     # an active-low name survives the Altium round trip
    low["symbol"]["kicad_sym"] = low["symbol"]["kicad_sym"].replace('(name "P3"', '(name "~{EN}"', 1)
    for pin in low["symbol"]["pins"]:
        pin["name"] = "~{EN}" if pin["num"] == "3" else pin["name"]
    assert SR.validate(rehash(low)) == [] and SR2.check_rebuild(low)[0] == []
    swapped = copy.deepcopy(good)                                 # pin 1 on pad 2 and pin 2 on pad 1, hashes made
    swapped["pin_map"].update({"1": "2", "2": "1"})               # to match: the rebuilt part wires pin n to pad n
    rehash(swapped)
    probs = SR2.check_rebuild(swapped)[0]
    assert any("pin_map" in p for p in probs), probs
    real_build = SR.build
    from ecadlib import librarian as L
    real_house = L.house_symbol

    def house(sym, *a, **kw):                                     # a rebuild whose symbol differs from the record
        out = real_house(sym, *a, **kw)
        for pr in out["prims"]:
            if pr[0] == "pin" and str(pr[1].get("num")) == "3":
                pr[1]["name"] = "XX"
        return out
    monkeypatch.setattr(L, "house_symbol", house)
    probs = SR2.check_rebuild(good)[0]
    assert any("pins" in p for p in probs), probs
    monkeypatch.setattr(L, "house_symbol", real_house)

    def build_moved(record, out_dir, rules=None):                 # a rebuild whose footprint differs from the record
        item = real_build(record, out_dir, rules)
        f = Path(out_dir) / item["footprint"]["kicad_mod"]
        f.write_text(f.read_text(encoding="utf-8").replace(*MOVE, 1), encoding="utf-8")
        return item
    monkeypatch.setattr(SR, "build", build_moved)
    probs = SR2.check_rebuild(good)[0]
    assert any("footprint" in p and "hash" in p for p in probs), probs
    monkeypatch.setattr(SR, "build", real_build)
    old = copy.deepcopy(good)                                     # hashed with an older digest version
    old["footprint"]["hash_v"] = 1
    old["footprint"]["hash"] = FS.digest(old["footprint"]["kicad_mod"], 1)
    probs = SR2.check_rebuild(old)[0]
    assert any("hash_v" in p or "digest version" in p for p in probs), probs


@lite
def test_index_is_what_the_strict_client_accepts(tmp_path, monkeypatch):
    """probe_index: an index entry under a kid that is not key_id(key), pointing at another part's bytes."""
    _offline(monkeypatch)
    root = store(tmp_path, rec_of(by="alice", also=["TPS1234DSGT"]), rec_of("RES-0603-10k", by="oli"),
                 maintainers=["oli"])
    seen = check_index(root)
    assert len(seen) == 2 and any(len(s) == 2 for s in seen.values())        # the also code's shard as well
    assert run("verify", root, "--all")[0] == 0
    k2 = SK.key("ACME", "OTHER99")
    rec = json.loads(rec_path(root, *TPS).read_text())
    f = root / "index" / f"{SK.shard(k2)}.json"
    idx = json.loads(f.read_text()) if f.is_file() else {}
    idx["not-a-key-id"] = {"key": k2, "also": [], "rev": 1, "sha256": SK.digest(rec), "level": "confirmed",
                           "status": "active"}
    f.write_text(json.dumps(idx))
    code, out = run("verify", root, "--all")
    assert code == 1 and "index" in out and "not up to date" in out, out
    assert run("index", root, "--check")[0] == 1
    assert run("index", root)[0] == 0 and check_index(root)


# ---------------------------------------------------------------- review findings: files outside parts/ and index/
@lite
def test_every_file_outside_parts_and_index_is_maintainer_only(tmp_path, monkeypatch):
    _offline(monkeypatch)
    base, _ = _pr(tmp_path, rec_of(by="alice"))
    for rel, text in (("README.md", "changed\n"), ("CONTRIBUTING.md", "x\n"), ("NOTICE.md", "x\n"),
                      ("LICENSE", "x\n"), ("LICENSE-DATA", "x\n"), ("notes.md", "x\n"), ("todo.txt", "x\n"),
                      ("ecadlib-version.txt", "0.0.1\n"), ("disputes.json", "[]\n"), (".github/CODEOWNERS", "* @m\n"),
                      (".github/workflows/übel.yml", "on: push\n"), ("withdrawn/00/" + "0" * 32 + ".r1.json", "{}\n")):
        pr = Path(tmp_path) / "pr"
        shutil.rmtree(pr, ignore_errors=True)
        shutil.copytree(base, pr)
        (pr / rel).parent.mkdir(parents=True, exist_ok=True)
        (pr / rel).write_text(text, encoding="utf-8")
        code, out = _verify(pr, base)
        assert code == 1 and rel in out and "maintainer-only" in out, (rel, out)
        if rel.startswith(".github/"):
            assert "WARNING" in out and ".github/" in out and "review" in out, out
        code, out = _verify(pr, base, "--maintainer")
        if rel not in ("ecadlib-version.txt", ".github/workflows/übel.yml", "withdrawn/00/" + "0" * 32 + ".r1.json"):
            assert code == 0, (rel, out)
        if rel.startswith(".github/"):
            assert "WARNING" in out, out                          # told even when a maintainer runs it


def _git_ok():
    return shutil.which("git") is not None


def _git_repo(root):
    """A throwaway git repository of root on branch main (its own identity; no global config)."""
    import os, subprocess
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def g(*a):
        return subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True, env=env).stdout
    g("init", "-q")
    g("symbolic-ref", "HEAD", "refs/heads/main")
    g("config", "user.name", "Test")
    g("config", "user.email", "test@example.com")
    g("config", "commit.gpgsign", "false")
    g("config", "core.quotepath", "true")
    g("add", "-A")
    g("commit", "-qm", "base")
    g("checkout", "-qb", "pr")
    return g


@lite
def test_verify_against_a_real_git_base(tmp_path, monkeypatch):
    """probe_gh: `.github/workflows/übel.yml` came out of git diff C-quoted and passed clean. Real git, a throwaway
    repository per case."""
    if not _git_ok():
        print("skipped: no git on this machine")
        return
    import os
    _offline(monkeypatch)
    seed = store(Path(tmp_path) / "seed", rec_of(by="alice"), maintainers=["oli"])

    def case(name):
        root = Path(tmp_path) / name
        shutil.copytree(seed, root)
        return root, _git_repo(root)

    def verify(root, *more):
        return run("verify", root, "--base", "main", *more)

    root, g = case("clean")                                       # a contributor's clean build passes
    assert run("add", root, put(Path(tmp_path) / "bob.json", rec_of(by="bob", at="2026-11-25T08:00:00Z")))[0] == 0
    g("add", "-A")
    g("commit", "-qm", "bob")
    code, out = verify(root, "--author", "bob")
    assert code == 0, out
    for name in ("evil.yml", "übel.yml"):                         # a new workflow, ASCII or not
        root, g = case("wf-" + name.encode().hex())
        (root / ".github" / "workflows" / name).write_text("on: pull_request_target\njobs: {}\n", encoding="utf-8")
        g("add", "-A")
        g("commit", "-qm", "x")
        code, out = verify(root, "--author", "mallory")
        assert code == 1 and f".github/workflows/{name}" in out and "maintainer-only" in out, out
        assert "WARNING" in out, out
    root, g = case("not-utf8")                                    # a file name that is not even UTF-8
    try:
        (root / ".github" / os.fsdecode(b"bad\xff.yml")).write_text("on: push\n", encoding="utf-8")
    except (OSError, UnicodeError):
        pass
    else:
        g("add", "-A")
        g("commit", "-qm", "x")
        code, out = verify(root, "--author", "mallory")
        assert code == 1 and ".github/bad" in out and "maintainer-only" in out, out
    root, g = case("nonascii-part")                               # a non-ASCII path under parts/: not a store path
    (root / "parts" / "zz").mkdir()
    (root / "parts" / "zz" / "ü.json").write_text("{}", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "x")
    code, out = verify(root, "--maintainer")
    assert code == 1 and "ü.json" in out, out
    root, g = case("untracked")                                   # not yet committed: still checked against main
    f = rec_path(root, *TPS)
    rec = json.loads(f.read_text())
    put(f, SR.add_evidence(rec, _ev(rec, "mallory", kind="signoff", at="2026-11-27T00:00:00Z")))
    (root / "extra.md").write_text("x\n")
    run("index", root)
    code, out = verify(root)
    assert code == 1 and "signoff" in out and "extra.md" in out, out
    if hasattr(os, "symlink"):
        root, g = case("symlink")                                 # a committed symbolic link
        try:
            os.symlink("../../store.json", root / "parts" / "link.json")
        except OSError:
            pass
        else:
            g("add", "-A")
            g("commit", "-qm", "x")
            code, out = verify(root, "--maintainer")
            assert code == 1 and "link.json" in out and "link" in out, out
            code, out = run("verify", root, "--all", "--maintainer")       # folder mode: lstat
            assert code == 1 and "link.json" in out, out
    root, g = case("exec")                                        # an executable file (git mode 100755)
    g("update-index", "--chmod=+x", "README.md")
    g("commit", "-qm", "x")
    code, out = verify(root, "--maintainer")
    assert code == 1 and "README.md" in out and "executable" in out, out
    root, g = case("submodule")                                   # a submodule (gitlink 160000)
    g("update-index", "--add", "--cacheinfo", "160000," + "1" * 40 + ",vendor")
    g("commit", "-qm", "x")
    code, out = verify(root, "--maintainer")
    assert code == 1 and "vendor" in out and "submodule" in out, out
    root, g = case("badref")
    assert run("verify", root, "--base", "no-such-ref")[0] == 2


@lite
def test_verify_refuses_symlinks_in_folder_mode(tmp_path, monkeypatch):
    import os
    _offline(monkeypatch)
    root = store(tmp_path, rec_of())
    try:
        os.symlink(str(root / "store.json"), root / "notes.md")
    except (OSError, NotImplementedError, AttributeError):
        print("skipped: no symbolic links here")
        return
    code, out = run("verify", root, "--all")
    assert code == 1 and "notes.md" in out and "link" in out, out


# ---------------------------------------------------------------- review findings: template and docs
@lite
def test_init_codeowners_version_pin_and_honest_docs(tmp_path, monkeypatch):
    root = Path(tmp_path) / "s"
    assert run("init", root, "--maintainer", "oli", "--maintainer", "bob-2", "--at", AT)[0] == 0
    owners = (root / ".github" / "CODEOWNERS").read_text()
    assert "@oli @bob-2" in owners and "MAINTAINER" not in owners.replace("maintainer", ""), owners
    rules = [l.split() for l in owners.splitlines() if l.strip() and not l.startswith("#")]
    assert rules[0] == ["*", "@oli", "@bob-2"] and ["/parts/"] in rules and ["/index/"] in rules, rules
    assert (root / "ecadlib-version.txt").read_text().strip() == SR2.__version__
    assert run("verify", root)[0] == 0
    (root / "ecadlib-version.txt").write_text("latest\n")
    code, out = run("verify", root)
    assert code == 1 and "ecadlib-version.txt" in out, out
    assert run("init", Path(tmp_path) / "t", "--maintainer", "not a name")[0] == 2
    assert run("init", Path(tmp_path) / "u")[0] == 0                              # no maintainer: placeholder left
    assert "@" in (Path(tmp_path) / "u" / ".github" / "CODEOWNERS").read_text()
    tpl = SR2.TEMPLATE
    readme = (tpl / "README.md").read_text()
    for s in ("Require review from Code Owners", "Require status checks", "CODEOWNERS", "ecadlib-version.txt",
              "snapshot"):
        assert s in readme, s
    assert "never the part" not in readme and "reveals" in readme
    contributing = (tpl / "CONTRIBUTING.md").read_text()
    for s in ("--author", "pointer records are not accepted from contributors yet", "also", "withdrawn"):
        assert s in contributing, s
    notice = (tpl / "NOTICE.md").read_text()
    assert "mislabelled" in notice and "contributor's word" in notice and "never confirmed" in notice
    assert "marks the part disputed" not in (tpl / "github" / "ISSUE_TEMPLATE" / "bad-part.yml").read_text()
    prt = (tpl / "github" / "PULL_REQUEST_TEMPLATE.md").read_text()
    assert "maintainer" in prt and ".github/" in prt
    v = (tpl / "github" / "workflows" / "verify.yml").read_text()
    assert "ecadlib-version.txt" in v and 'show "origin/$BASE_REF:ecadlib-version.txt"' in v and "ecadlib==" in v
    assert "--upgrade ecadlib" not in v and "python -I -m pip" in v
