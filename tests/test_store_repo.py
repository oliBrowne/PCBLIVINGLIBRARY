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
    rec = SR.from_item(items[name], base, by=by, at=at)
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
    root = Path(tmp_path) / "store"
    SR2.init(root, name="test", maintainers=maintainers, at=AT)
    for i, r in enumerate(recs):
        assert run("add", root, put(Path(tmp_path) / "in" / f"{i}.json", r), "--at", AT)[0] == 0
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
    code, out = run("add", root, other, "--dispute", "--at", "2026-11-26T00:00:00Z")
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
    nodesc = dict(copy.deepcopy(good), part=dict(good["part"], description=""))
    assert SR.validate(nodesc) == []
    f.write_text(json.dumps(nodesc))
    run("index", root)
    code, out = run("verify", root, "--all")
    assert code == 1 and "held" in out and "Description" in out, out
    assert "withdraw" in out                                          # the nightly prints the command, never runs it
    nocrt = copy.deepcopy(good)
    t = nocrt["footprint"]["kicad_mod"]
    nocrt["footprint"]["kicad_mod"] = "\n".join(l for l in t.splitlines() if "CrtYd" not in l) + "\n"
    assert "CrtYd" not in nocrt["footprint"]["kicad_mod"] and SR.validate(rehash(nocrt)) == []
    f.write_text(json.dumps(nocrt))
    run("index", root)
    code, out = run("verify", root, "--all")
    assert code == 1 and "held" in out and "courtyard" in out.lower(), out
    # add refuses it too
    root2 = store(Path(tmp_path) / "two")
    code, out = run("add", root2, put(Path(tmp_path) / "nodesc.json", nodesc))
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
            return f"{rel}\nindex/{f.parent.name}.json\n"
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
    code, out = run("add", root, put(Path(tmp_path) / "fixed.json", moved(rec_of(by="bob"))))
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
