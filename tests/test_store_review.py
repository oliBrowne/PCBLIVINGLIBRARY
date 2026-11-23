"""Shared store: regression tests for the code review of the store PR (each pins one finding the reviewers proved).

A  the store is a true no-op while [store] url is empty: default order, init, rules hash, upkeep, plan items
B  a second opinion lifts a hold only for a record with a drawing, built independently, compared at this digest
   version; the lift is conditional, keeps the read's review items, and an `also` / no-maker match is never used
C  a record used directly: the index must agree with the record, the lower level counts, strangers' footprint extras
   hold, store_holds runs whenever a store is set, a stale copy builds nothing, publishers see withdrawals, http:// and
   oversized answers are refused
D  records: current digest version only, no `also` merging, no BOM text, no DNS lookups in validate, KiCad text that
   carries only the record's fields, active-low pins and mounting pads in pins_hash, an identity pin map

Every store is a folder (or a fake HTTP server) in tmp_path; nothing goes to the network, no API is called."""
import base64, copy, io, json, os, re, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path

from ecadlib import datasheet as D, fp_share as FS, librarian as L, release_check as RC, rebuild as LK, \
    rules as RU, store_cli as CLI, store_client as SC, store_keys as SK, store_record as SR, upkeep as UK
from tests import test_librarian as TL
from tests.fixtures_lite import lite
from tests.test_store_client import BASE, FakeHTTP as StoreHTTP, make_store, record as fake_record
from tests.test_store_librarian import DS_RULES, DS_SOURCES, OTHER_READER, _confirm, _datasheet_part, _no_dns, \
    _reader, _record, _run as _run_in, _store
from tests.test_store_record import AT, _kicad

ROOT = Path(__file__).resolve().parent.parent


def _run(d, **kw):
    Path(d).mkdir(parents=True, exist_ok=True)
    return _run_in(d, **kw)


def _bom(*idx):
    ls = TL.BOM.splitlines()
    return "\n".join([ls[0]] + [ls[i] for i in idx]) + "\n"


def _rules_hash_of(tree):
    """rules_hash of the default rules as the ecadlib in folder `tree` computes it, in a clean process (the tests'
    own settings, e.g. no web search, left out), or None."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from ecadlib import rules as RU, rebuild as R; "
            "print(R.rules_hash(RU.load({})))")
    try:
        r = subprocess.run([sys.executable, "-I", "-c", code, str(tree)], capture_output=True, text=True, timeout=120,
                           cwd=tempfile.gettempdir())
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else None


def _main_rules_hash():
    """(this tree's, origin/main's) default rules_hash, or None when origin/main is not here (a shallow CI
    checkout)."""
    try:
        data = subprocess.run(["git", "-C", str(ROOT), "archive", "origin/main", "ecadlib"], capture_output=True,
                              timeout=60).stdout
        if not data:
            return None
        with tempfile.TemporaryDirectory() as d:
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                t.extractall(d, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(d)
            main = _rules_hash_of(d)
    except (OSError, subprocess.SubprocessError, tarfile.TarError):
        return None
    return (_rules_hash_of(ROOT), main) if main else None


# ================================================================ A: a no-op while the store is off
def test_a1_default_order_and_init_never_name_the_store():
    assert "store" not in RU.DEFAULTS["sources"]["order"] and "store" in RU.SOURCES     # (an explicit order may)
    assert "store" not in RU.DEFAULTS["upkeep"]["checks"] and "store" not in UK.DEFAULTS["checks"]
    from ecadlib import firstrun as F
    ans = iter(["both", "", "", "y", "y", "n"])
    a = F.ask_setup(inp=lambda p: next(ans), out=lambda *x: None, environ={})
    assert "store" not in a["sources"] and "store" not in json.dumps(F.setup_raw(a))
    RU.validate({"sources": {"order": ["store", "kicad"]}})                  # harmless when named


def test_a2_default_rules_hash_is_mains():
    """Existing parts are not rebuilt as "built with other rules" and `rebuild --check` shows no drift."""
    h = LK.rules_hash(RU.load({}))
    eff = {k: v for k, v in RU.effective(RU.load({})).items() if k != "store"}
    assert h == LK.sha(json.dumps(eff, sort_keys=True, default=str))         # [store] off: out of the hash
    on = RU.load({})
    on["store"] = dict(on["store"], url="https://raw.githubusercontent.com/o/r/main")
    assert LK.rules_hash(on) != h                                            # set: in it
    both = _main_rules_hash()
    if both is not None:
        assert both[0] == both[1], both


@lite
def test_a_store_off_run_writes_nothing_of_the_store(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    got, log = _run(tmp_path, rules={})
    out = tmp_path / "out"
    for it in json.loads((out / "plan.json").read_text(encoding="utf-8")):
        assert not {"store", "store_check"} & set(it), it["name"]
        assert not {"spec", "origin", "store_key"} & set(it.get("footprint") or {}), it["footprint"]
        assert not any(str(s).startswith("store:") for s in it["source"].values())
    lock = json.loads((out / "library.lock.json").read_text(encoding="utf-8"))
    assert lock["settings"]["sources"] == list(RU.DEFAULT_SOURCES)
    eff = {k: v for k, v in RU.effective(RU.load({})).items() if k != "store"}      # (main's hash: test_a2)
    assert lock["settings"]["rules_sha256"] == LK.sha(json.dumps(eff, sort_keys=True, default=str))
    state = json.loads((out / "librarian_state.json").read_text(encoding="utf-8"))
    assert not any({"store", "store_check", "read_review"} & set(st) for st in state.values())
    assert not any("store" in l.lower() for l in log), log


@lite
def test_a3_upkeep_runs_the_store_check_only_when_a_store_is_set(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    _run(tmp_path / "ws", bom=_bom(1), http=TL.FakeHTTP(), rules={})
    plan = tmp_path / "ws" / "out" / "plan.json"
    rep = UK.run(plan, state_dir=tmp_path / "u", out=tmp_path / "u" / "r", ctx=UK.Ctx(env={}, offline=True),
                 now="2026-11-23T10:00:00Z")
    assert [c["check"] for c in rep["checks"]] == list(UK.CHECKS)                       # no store row
    assert "Shared store" not in (tmp_path / "u" / "r.md").read_text(encoding="utf-8")
    root = make_store(tmp_path / "s")
    rep = UK.run(plan, state_dir=tmp_path / "u2", out=tmp_path / "u2" / "r", ctx=UK.Ctx(env={}, offline=True),
                 now="2026-11-23T10:00:00Z", rules={"store": {"url": str(root)}})
    assert [c["check"] for c in rep["checks"]] == list(UK.CHECKS) + ["store"]


# ================================================================ B: second opinions
def _ds_run(d, recs, **kw):
    d.mkdir(parents=True, exist_ok=True)
    with _reader():
        return _run(d, api_key="sk-test", sources=DS_SOURCES, rules=DS_RULES,
                    store=SC.Store(str(_store(d.parent / f"s_{d.name}", recs, **kw))))[0]["DSPART1"]


@lite
def test_b1_a_pointer_record_never_lifts(tmp_path, monkeypatch):
    from ecadlib import easyeda_convert as EC
    _no_dns(monkeypatch)
    it, base = _datasheet_part()
    rec = copy.deepcopy(_record(it, base, by="mallory", reader=OTHER_READER))
    for h, key in (("symbol", "kicad_sym"), ("footprint", "kicad_mod")):
        rec[h].update({"source": "easyeda:C999999", key: None, "pointer": {"lcsc": "C999999"},
                       "license": "pointer only", "credit": EC.CREDIT})
    got = _ds_run(tmp_path / "ws", [rec])
    assert got["status"] == "held" and "store_check" not in got, got.get("store_check")
    assert any("is a pointer to easyeda:C999999" in t for t in got["librarian"]["tried"])


@lite
def test_b2_same_reader_model_is_not_an_independent_read(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    it, base = _datasheet_part()
    same = _record(it, base, by="carol")                     # read by the same model as this machine's read
    got = _ds_run(tmp_path / "same", [same])
    assert got["status"] == "held" and got["store_check"]["result"] == "agreed, not independent"
    assert "not an independent read" in got["store_check"]["why"]
    unknown = _record(it, base, by="carol", reader=None)
    unknown["evidence"][0]["reader"] = None                  # the record does not say who read it
    got = _ds_run(tmp_path / "unknown", [unknown])
    assert got["status"] == "held" and got["store_check"]["result"] == "agreed, not independent"
    got = _ds_run(tmp_path / "other", [_record(it, base, by="carol", reader=OTHER_READER)])
    assert got["status"] == "ready" and got["store_check"]["result"] == "agreed", got["hold_reasons"]


@lite
def test_b3_compared_at_this_digest_version(tmp_path, monkeypatch):
    """A pad moved to the bottom copper: an old (v1) hash cannot see it, so a v1 record is refused, and a current one
    disagrees with the local build."""
    _no_dns(monkeypatch)
    it, base = _datasheet_part()
    rec = _record(it, base, by="carol", reader=OTHER_READER)
    text = rec["footprint"]["kicad_mod"]
    m = re.search(r'\(pad "1" smd .*?\(layers ([^)]*)\)', text, re.S)
    bad = text[:m.start(1)] + m.group(1).replace("F.", "B.") + text[m.end(1):]
    assert FS.digest(bad, 1) == FS.digest(text, 1) and FS.digest(bad) != FS.digest(text)
    v1 = copy.deepcopy(rec)
    v1["footprint"].update(kicad_mod=bad, hash_v=1, hash=FS.digest(bad, 1))
    for e in v1["evidence"]:
        e["footprint_hash"] = v1["footprint"]["hash"]
    assert any("older digest version" in p for p in SR.validate(v1)), SR.validate(v1)
    got = _ds_run(tmp_path / "v1", [v1])
    assert got["status"] == "held" and "store_check" not in got
    v2 = copy.deepcopy(rec)
    v2["footprint"].update(kicad_mod=bad, hash=FS.digest(bad))
    for e in v2["evidence"]:
        e["footprint_hash"] = v2["footprint"]["hash"]
    assert SR.validate(v2) == []
    got = _ds_run(tmp_path / "v2", [v2])
    assert got["status"] == "held" and got["store_check"]["result"] == "disagreed", got.get("store_check")


@lite
def test_b4_b5_a_lift_keeps_the_review_items_and_is_taken_back(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    it, base = _datasheet_part()
    rec = _record(it, base, by="carol", reader=OTHER_READER)
    kid = SK.key_id(rec["key"]["key"])
    d = tmp_path / "ws"
    got = _ds_run(d, [rec])
    assert got["status"] == "ready" and got["store_check"]["result"] == "agreed" and got["store_check"]["lifted"]
    review = got["librarian"]["review"]
    assert any(r.startswith("datasheet read: ") for r in review), review          # (B5: as a cross-check's)
    assert "read_review" not in json.loads((d / "out" / "librarian_state.json").read_text())["DSPART1"]
    root = d.parent / f"s_{d.name}"
    plan = d / "out" / "plan.json"
    # the record is withdrawn and taken out of the index: the lifted hold is back, on a resumed (skipped) run
    shutil.rmtree(root / "parts")
    (root / "index" / f"{SK.shard(rec['key']['key'])}.json").write_text("{}")
    (root / "withdrawn.json").write_text(json.dumps([{"key_id": kid, "rev": 1, "reason": "pin 3 and 4 swapped"}]))
    item = next(i for i in json.loads(plan.read_text()) if i["name"] == "DSPART1")
    probs = RC.publish_problems(item, d / "out", store=SC.Store(str(root)))          # a publisher sees it at once
    assert any("agreed with it" in p and "withdrawn" in p for p in probs), probs
    res = UK.run(plan, checks=["store"], state_dir=tmp_path / "u", out=tmp_path / "u" / "r",
                 ctx=UK.Ctx(store=SC.Store(str(root))), now="2026-11-23T10:00:00Z")
    sec = [c for c in res["checks"] if c["check"] == "store"][0]
    assert sec["changes"] and "lifted holds back" in sec["changes"][0]["kind"], sec
    for _ in range(2):
        with _reader():
            got = _run(d, api_key="sk-test", sources=DS_SOURCES, rules=DS_RULES, store=SC.Store(str(root)))[0]["DSPART1"]
        assert got["status"] == "held", got["hold_reasons"]
        assert any(r.startswith("verify: datasheet reader output") for r in got["hold_reasons"]), got["hold_reasons"]
        assert any("pin 3 and 4 swapped" in r and "holds it lifted are back" in r for r in got["hold_reasons"])
        assert got["store_check"]["revoked"]
    # unreachable store: nothing changes either way (a lift stands, a hold stays), one note
    d2 = tmp_path / "ws2"
    got = _ds_run(d2, [rec])
    assert got["status"] == "ready"
    down = SC.Store(BASE, http=StoreHTTP(None, fail="error"), cache_dir=tmp_path / "sc")
    with _reader():
        got, log = _run(d2, api_key="sk-test", sources=DS_SOURCES, rules=DS_RULES, store=down)
    assert got["DSPART1"]["status"] == "ready" and sum(l.startswith("  store:") for l in log) == 1, log


@lite
def test_b6_also_and_no_maker_matches_are_never_used(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = _confirm(_record(items["TPS1234DSGR"], base))
    root = _store(tmp_path / "s", [rec])
    kid, e = next(iter(json.loads((root / "index" / f"{SK.shard(rec['key']['key'])}.json").read_text()).items()))
    e["also"] = [SK.key("ACME", "TPS1234P")]
    for ab in {SK.shard("TPS1234P"), SK.shard(rec["key"]["key"])}:
        (root / "index" / f"{ab}.json").write_text(json.dumps({kid: e}))
    st = SC.Store(str(root))
    hit = st.lookup("ACME", "TPS1234P")
    assert hit.via == "also" and SC.decide(hit, "checked") == "second-opinion"
    hit = st.lookup("", "TPS1234DSGR")
    assert hit.via == "no-maker" and SC.decide(hit, "checked") == "second-opinion"
    assert SC.decide(st.lookup("ACME", "TPS1234DSGR"), "confirmed") == "use"
    bom = TL.BOM.splitlines()[0] + "\nU1,1,TPS1234DSGR,,,TPS1234DSGR,,Test regulator,https://example.com/x.pdf\n"
    got = _run(tmp_path / "ws", bom=bom, http=TL.FakeHTTP(), sources=["kicad"], store=SC.Store(str(root)),
               rules={"release": {"manufacturer": "hold"}})[0]
    it = list(got.values())[0]
    par = {p["name"]: p["value"] for p in it["parameters"]}
    assert not str(it["source"]["footprint"]).startswith("store:") and not par.get("Manufacturer"), (it["source"], par)
    assert it["status"] == "held" and any("Manufacturer is empty" in r for r in it["hold_reasons"])


# ================================================================ C: using a record
def _cfm(maker, mpn, **o):
    return (maker, mpn, o)


@lite
def test_c1_the_index_must_agree_with_the_record(tmp_path):
    # the index says active, the record disputed (or the other way round): refused
    root = make_store(tmp_path / "a", [_cfm("TI", "NE555DR", record=fake_record("TI", "NE555DR", status="disputed"))])
    st = SC.Store(str(root))
    assert st.lookup("TI", "NE555DR") is None and "index entry" in st.trouble[0]
    root = make_store(tmp_path / "b", [_cfm("TI", "NE555DR", entry={"rev": 2})])
    assert SC.Store(str(root)).lookup("TI", "NE555DR") is None                  # rev differs
    # the lower level counts: the index says confirmed, the record holds one build
    root = make_store(tmp_path / "c", [_cfm("TI", "NE555DR", record=fake_record("TI", "NE555DR", level="checked"),
                                            level="confirmed")])
    h = SC.Store(str(root)).lookup("TI", "NE555DR")
    assert h.level == "checked" and SC.decide(h) == "second-opinion"
    # store.json "trusted": only their evidence counts
    root = make_store(tmp_path / "d", [_cfm("TI", "NE555DR")], meta={"trusted": ["maintainer"]})
    assert SC.Store(str(root)).lookup("TI", "NE555DR").level == "checked"
    # an index entry under a key id that is not its key's, pointing at another part's record: ignored
    root = make_store(tmp_path / "e", [_cfm("TI", "NE555DR")])
    k1, k2 = SK.key("TI", "NE555DR"), SK.key("TI", "OTHER99")
    real = root / "parts" / SK.shard(k1) / f"{SK.key_id(k1)}.json"
    for kid in ("not-a-key-id", SK.key_id(k2)):
        (root / "parts" / SK.shard(k2)).mkdir(parents=True, exist_ok=True)
        (root / "parts" / SK.shard(k2) / f"{kid}.json").write_text(real.read_text())
        (root / "index" / f"{SK.shard(k2)}.json").write_text(json.dumps({kid: {
            "key": k2, "also": [], "rev": 1, "sha256": SK.digest(json.loads(real.read_text())), "level": "confirmed",
            "status": "active"}}))
        assert SC.Store(str(root)).lookup("TI", "OTHER99") is None, kid
    (root / "index" / f"{SK.shard(k1)}.json").write_text(json.dumps({SK.key_id(k1): {
        "key": k1, "also": "NE555DT", "rev": 1, "sha256": SK.digest(json.loads(real.read_text())),
        "level": "confirmed", "status": "active"}}))
    assert SC.Store(str(root)).lookup("TI", "NE555DR") is None                   # also: not a list


@lite
def test_c2_store_footprint_extras_hold_the_part(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    it, base = _datasheet_part()
    rec = _confirm(_record(it, base, by="carol", reader=OTHER_READER))
    assert L.store_extras(rec["footprint"]["kicad_mod"])                         # (paste apertures on the pad)
    got = _run(tmp_path / "ws", bom=_bom(5), http=TL.FakeHTTP(), sources=["kicad"],
               store=SC.Store(str(_store(tmp_path / "s", [rec]))))[0]["DSPART1"]
    assert got["source"]["footprint"].startswith("store:"), got["source"]
    assert got["status"] == "held" and any("the store's footprint has" in r for r in got["hold_reasons"]), \
        got["hold_reasons"]


@lite
def test_c3_store_holds_whatever_the_sources_and_cannot_tell(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = _confirm(_record(items["RES-0603-10k"], base))
    kid = SK.key_id(rec["key"]["key"])
    root = _store(tmp_path / "s", [rec])
    d = tmp_path / "ws"
    it = _run(d, bom=_bom(1), http=TL.FakeHTTP(), store=SC.Store(str(root)))[0]["RES-0603-10k"]
    assert it["status"] == "ready" and it["source"]["footprint"] == f"store:{kid}@r1"
    _store(root, [rec], withdrawn=[{"key_id": kid, "rev": 1, "reason": "pad 1 too small"}])
    it = _run(d, bom=_bom(1), http=TL.FakeHTTP(), sources=["kicad"], store=SC.Store(str(root)))[0]["RES-0603-10k"]
    assert it["status"] == "held" and any("pad 1 too small" in r for r in it["hold_reasons"]), it["hold_reasons"]
    # a store that answers but has no withdrawn.json: cannot tell, held
    d2, root2 = tmp_path / "ws2", _store(tmp_path / "s2", [rec])
    assert _run(d2, bom=_bom(1), http=TL.FakeHTTP(), store=SC.Store(str(root2)))[0]["RES-0603-10k"]["status"] == "ready"
    (root2 / "withdrawn.json").unlink()
    it = _run(d2, bom=_bom(1), http=TL.FakeHTTP(), store=SC.Store(str(root2)))[0]["RES-0603-10k"]
    assert it["status"] == "held" and any("cannot tell" in r for r in it["hold_reasons"]), it["hold_reasons"]
    # its level dropped below [store] accept = "confirmed"
    d3, root3 = tmp_path / "ws3", _store(tmp_path / "s3", [rec])
    assert _run(d3, bom=_bom(1), http=TL.FakeHTTP(), store=SC.Store(str(root3)))[0]["RES-0603-10k"]["status"] == "ready"
    f = root3 / "index" / f"{SK.shard(rec['key']['key'])}.json"
    f.write_text(json.dumps({k: dict(v, level="checked") for k, v in json.loads(f.read_text()).items()}))
    it = _run(d3, bom=_bom(1), http=TL.FakeHTTP(), store=SC.Store(str(root3)))[0]["RES-0603-10k"]
    assert it["status"] == "held" and any("is now only checked" in r for r in it["hold_reasons"]), it["hold_reasons"]


@lite
def test_c4_a_stale_copy_builds_nothing_and_says_how_old(tmp_path):
    root = make_store(tmp_path / "s", [_cfm("TI", "NE555DR")])
    clock = [1_000_000.0]
    st = SC.Store(BASE, http=StoreHTTP(root), cache_dir=tmp_path / "c", now=lambda: clock[0])
    assert st.lookup("TI", "NE555DR") is not None
    clock[0] += 2 * 86400                                    # two days, the network down: the saved copy serves
    down = SC.Store(BASE, http=StoreHTTP(root, fail="error"), cache_dir=tmp_path / "c", now=lambda: clock[0])
    assert down.lookup("TI", "NE555DR") is not None
    assert down.trouble and "using the copy saved" in down.trouble[0] and "built without it" not in down.trouble[0]
    clock[0] += 88 * 86400                                   # 90 days, offline: never built from
    off = SC.Store(BASE, http=StoreHTTP(root), cache_dir=tmp_path / "c", offline=True, now=lambda: clock[0])
    assert off.lookup("TI", "NE555DR") is None and off.lookup("TI", "NE555DR") is None
    assert len(off.trouble) == 1 and "90 days old" in off.trouble[0], off.trouble


@lite
def test_c5_publishers_see_withdrawals_without_a_store_argument(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = _confirm(_record(items["RES-0603-10k"], base))
    kid = SK.key_id(rec["key"]["key"])
    root = _store(tmp_path / "s", [rec])
    d = tmp_path / "ws"
    rules = {"store": {"url": str(root)}}
    it = _run(d, bom=_bom(1), http=TL.FakeHTTP(), rules=rules)[0]["RES-0603-10k"]
    assert it["status"] == "ready" and it["source"]["footprint"] == f"store:{kid}@r1"
    assert RC.publish_problems(it, d / "out", rules=RU.resolve(rules, d / "out")) == []
    _store(root, [rec], withdrawn=[{"key_id": kid, "rev": 1, "reason": "pad 1 too small"}])
    RC._STORES.clear()
    probs = RC.publish_problems(it, d / "out", rules=RU.resolve(rules, d / "out"))
    assert any("pad 1 too small" in p for p in probs), probs
    # an unreachable store: no problem, no crash, asked once for a whole plan
    RC._STORES.clear()
    made, http = [], StoreHTTP(None, fail="error")
    monkeypatch.setattr(SC, "from_rules", lambda r, **kw: made.append(1) or SC.Store(BASE, http=http,
                                                                                         cache_dir=tmp_path / "sc"))
    for _ in range(5):
        assert RC.publish_problems(it, d / "out", rules=RU.resolve({"store": {"url": BASE}}, d / "out")) == []
    assert len(made) == 1 and len(http.urls) <= 1, (made, http.urls)
    plain = dict(it, source={"symbol": "kicad:Device:R", "footprint": "kicad:R:R_0603"})
    plain.pop("store", None)
    assert RC.store_for(plain, d / "out", RU.resolve({"store": {"url": BASE}}, d / "out")) is None
    RC._STORES.clear()


@lite
def test_c6_plain_http_and_oversized_answers_are_refused(tmp_path, monkeypatch):
    http = StoreHTTP(None, fail="raise")
    st = SC.Store("http://example.com/store", http=http, cache_dir=tmp_path / "a")
    assert st.enabled and st.lookup("TI", "NE555DR") is None and http.urls == []
    assert len(st.trouble) == 1 and "http://" in st.trouble[0]
    st = SC.Store(BASE, http=http, cache_dir=tmp_path / "b", mode="snapshot", snapshot_url="http://x.example/s.zip")
    assert st.lookup("TI", "NE555DR") is None and http.urls == [] and "http://" in st.trouble[0]
    root = make_store(tmp_path / "s", [_cfm("TI", "NE555DR")])
    monkeypatch.setattr(SC, "FETCH_MAX_BYTES", 50)
    st = SC.Store(BASE, http=StoreHTTP(root), cache_dir=tmp_path / "c")
    assert st.lookup("TI", "NE555DR") is None and any("larger than" in t for t in st.trouble), st.trouble


# ================================================================ D: records
@lite
def test_d1_d2_digest_version_and_also(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = SR.from_item(items["TPS1234DSGR"], base, by="alice", at=AT)
    assert rec["also"] == [] and rec["footprint"]["hash_v"] == FS.HASH_V
    old = copy.deepcopy(rec)
    old["footprint"].update(hash_v=1, hash=FS.digest(old["footprint"]["kicad_mod"], 1))
    for e in old["evidence"]:
        e["footprint_hash"] = old["footprint"]["hash"]
    assert any("older digest version" in p for p in SR.validate(old))
    other = SR.from_item(items["TPS1234DSGR"], base, by="bob", at=AT)
    m = SR.merge(dict(rec, also=["ACME|TPS1234DSGT"]), dict(other, also=["ACME|LM7805CT"]))
    assert m["also"] == ["ACME|TPS1234DSGT"] and len(m["evidence"]) == 2


@lite
def test_d3_the_bom_description_is_never_shared(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    from ecadlib import librarian as L
    d = tmp_path
    (d / "bom.csv").write_text(
        "Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,Description,Datasheet\n"
        "R1,1,10k,0603,Yageo,RC0603FR-0710KL,RES 10k 0603 - ACME-INT-4471 rover-mainboard ask Jim x221,"
        "https://www.yageo.com/upload/media/product/RC.pdf\n", encoding="utf-8")
    it = L.run(d / "bom.csv", out=d / "out", sources=["standard"], offline=True, cache=d / "cache", rules={},
               log=lambda *a, **k: None)[0]
    assert "ACME-INT-4471" in it["description"]
    rec = SR.from_item(it, d / "out", by="x", at=AT)
    assert rec["part"]["description"] == "" and "ACME-INT" not in SK.dumps(rec), rec["part"]
    assert SR.validate(rec) == []
    found = dict(it, description="Thick film resistor 10k 1% 0603", field_sources=dict(
        it.get("field_sources") or {}, Description="Nexar part data"))
    rec = SR.from_item(found, d / "out", by="x", at=AT)
    assert rec["part"]["description"] == "Thick film resistor 10k 1% 0603"
    assert '(property "Description" "Thick film resistor 10k 1% 0603"' in rec["symbol"]["kicad_sym"]
    assert SR.shared_description(dict(found, house_style={"changes": [{"field": "description"}]})) == ""


@lite
def test_d4_validate_makes_no_name_lookup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(D, "_resolve", lambda host: (calls.append(host), [])[1])
    items, base = _kicad()
    rec = SR.from_item(items["TPS1234DSGR"], base, by="a", at=AT)
    calls.clear()
    rec["part"]["datasheet"]["url"] = "https://victim-7f3a.tracker.attacker.net/ds.pdf"
    rec["body"]["model"] = {"source": "kicad", "url": "https://m-7f3a.tracker.attacker.net/x.step"}
    SR.validate(rec)
    assert calls == []
    assert SR.private("https://intranet/ds.pdf", resolve=False)                  # (still a syntax test)
    SR.private("https://datasheets.example.org/x.pdf")                          # share keeps the lookup
    assert calls == ["datasheets.example.org"], calls


def _rehash(rec):
    rec["footprint"]["hash"] = FS.digest(rec["footprint"]["kicad_mod"])
    for e in rec["evidence"]:
        e["footprint_hash"], e["pins_hash"] = rec["footprint"]["hash"], SR.pins_hash(rec)
    return rec


@lite
def test_d5_kicad_text_carries_only_the_records_fields(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = SR.from_item(items["TPS1234DSGR"], base, by="mallory", at=AT)
    assert SR.validate(rec) == []
    props = re.findall(r'\(property "([^"]+)"', rec["symbol"]["kicad_sym"])
    assert sorted(props) == sorted(SR.SYM_FIELDS)
    ks = rec["symbol"]["kicad_sym"]
    bad = copy.deepcopy(rec)                                 # probe_symprop: fields beyond the record's, other values
    bad["symbol"]["kicad_sym"] = re.sub(r'\(property "Datasheet" "[^"]*"',
                                        '(property "Datasheet" "file://attacker.example/share/ds.pdf"', ks, count=1) \
        .replace('(property "Reference"', '(property "Supplier Part Number 1" "C999999") (property "Reference"', 1)
    probs = SR.validate(bad)
    assert any("properties are" in p for p in probs) and any("Datasheet" in p for p in probs), probs
    esc = copy.deepcopy(rec)                                 # terminal escapes anywhere
    esc["evidence"][0]["reader"] = {"provider": "x", "model": "\x1b[31mRED"}
    assert any("control characters" in p for p in SR.validate(esc))
    assert SC.safe("ok \x1b[2J\x1b]8;;https://evil.example\x07click") == "ok [2J]8;;https://evil.exampleclick"
    km = rec["footprint"]["kicad_mod"].rstrip().rstrip(")")
    blob = base64.b64encode(os.urandom(6000)).decode()        # probe_blob: a file split over many text items
    many = copy.deepcopy(rec)
    many["footprint"]["kicad_mod"] = km + "".join(
        f'\t(fp_text user "{blob[i:i + 60]}" (at 0 0) (layer "Cmts.User") (effects (font (size 0.1 0.1))))\n'
        for i in range(0, len(blob), 60)) + ")\n"
    assert any("text items" in p for p in SR.validate(_rehash(many)))
    for extra, why in (('(model "${KIPRJMOD}/x.step")', "3D model"), ('(fp_text user "${SECRET}" (at 0 0) '
                                                                      '(layer "F.Fab"))', "text variables"),
                       ('(fp_line (start 0 0) (end 1 1) (layer "Evil.Layer") (width 0.1))', "layers"),
                       ('(image (at 0 0) (data "x"))', "image")):
        t = copy.deepcopy(rec)
        t["footprint"]["kicad_mod"] = km + "\t" + extra + "\n)\n"
        probs = SR.validate(_rehash(t))
        assert probs and any(why.split()[0] in p for p in probs), (extra, probs)
    out = io.StringIO()
    CLI._say(out, "x \x1b]0;pwned\x07 y")
    assert "\x1b" not in out.getvalue() and "\x07" not in out.getvalue()


@lite
def test_d6_pins_hash_keeps_active_low_and_mounting_pads_and_the_map_is_identity(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    pins = [{"num": "1", "name": "~{CS}", "type": "input"}, {"num": "2", "name": "SCK", "type": "input"}]
    plain = [dict(pins[0], name="CS"), pins[1]]
    assert SR.pins_hash(pins) != SR.pins_hash(plain)
    assert SR.pins_hash(pins) == SR.pins_hash([dict(pins[0], name="C\\S\\"), pins[1]])   # Altium's spelling
    assert SR.pins_hash(pins) == SR.pins_hash([dict(pins[0], name="~CS~"), pins[1]])      # KiCad 6's
    assert SR.pins_hash(pins, mount_pads=2) != SR.pins_hash(pins) == SR.pins_hash(pins, mount_pads=0)
    items, base = _kicad()
    rec = SR.from_item(items["TPS1234DSGR"], base, by="a", at=AT)
    assert all(k == v for k, v in rec["pin_map"].items())
    swap = copy.deepcopy(rec)
    swap["pin_map"]["1"], swap["pin_map"]["2"] = "2", "1"
    for e in swap["evidence"]:
        e["pins_hash"] = SR.pins_hash(swap)
    assert any("pin N on pad N" in p for p in SR.validate(swap)), SR.validate(swap)
    mp = copy.deepcopy(rec)
    mp["footprint"]["mount_pads"] = 1
    assert SR.pins_hash(mp) != SR.pins_hash(rec)


def test_d7_privacy_wording_is_exact():
    for f in ("PRIVACY.md", "docs/shared-store.md", "site/privacy.html"):
        t = re.sub(r"\s+", " ", (ROOT / f).read_text(encoding="utf-8"))
        assert "1/256" in t and "exact part" in t and "snapshot" in t, f
        assert "no counts" not in t.lower(), f
    assert "exact part" in SC.__doc__
