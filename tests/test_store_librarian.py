"""Shared store, use side: the librarian's `store` source, the second opinion, withdrawn / disputed records, the
[store] settings and `ecadlib store` (ecadlib/librarian.py src_store, ecadlib/store_cli.py).

Every store here is a folder (or a fake HTTP server) made in tmp_path from records ecadlib itself wrote
(store_record.from_item) for the fake KiCad library and the mocked datasheet read of tests/test_librarian. Nothing
goes to the network, no API is called."""
import contextlib, copy, io, json, os, re, sys
from pathlib import Path
from unittest import mock

from ecadlib import datasheet as D, fp_share as FS, librarian as L, release_check as RC, store_cli as CLI, \
    store_client as SC, store_keys as SK, store_record as SR, upkeep as UK
from tests import test_datasheet as TD, test_librarian as TL
from tests.fixtures_lite import lite, scratch
from tests.test_store_client import BASE, FakeHTTP as StoreHTTP
from tests.test_store_record import AT, _kicad

_DS = {}
DS_SOURCES = ["kicad", "easyeda", "datasheet"]              # (no standard table: the footprint is the read's own)
DS_RULES = {"datasheet": {"standard_land": "off"}}


def _no_dns(monkeypatch):
    monkeypatch.setattr(D, "_resolve", lambda host: [])


def _reader():
    ext = copy.deepcopy(TD.GOOD)
    return mock.patch.object(D, "call", lambda headers, body, key, timeout=600: (ext, TD.message(ext)))


def _datasheet_part():
    """DSPART1 read from its (mocked) datasheet, its read's hold settled as after a second read: (item, plan folder)."""
    if "ds" not in _DS:
        d = scratch("ecadlib-store-ds-", keep=True)
        with _reader(), mock.patch.object(D, "_resolve", lambda host: []):
            items, _ = TL.run(d, api_key="sk-test", sources=DS_SOURCES, rules=DS_RULES)
        out, it = Path(d) / "out", items["DSPART1"]
        state = json.loads((out / "librarian_state.json").read_text(encoding="utf-8"))
        state["DSPART1"]["verify"], it["verify"] = [], []
        (out / "librarian_state.json").write_text(json.dumps(state), encoding="utf-8")
        RC.apply([it], out)
        _DS["ds"] = it, out
    it, out = _DS["ds"]
    return copy.deepcopy(it), out


def _record(item, base, by="alice", reader=None):
    """item's record by `by`. reader: {"provider", "model"} the record's build says read the datasheet (another
    model than this machine's mocked read: an independent read)."""
    rec = SR.from_item(item, base, by=by, at=AT)
    if reader is not None:
        for e in rec["evidence"]:
            e["reader"] = dict(reader)
    assert SR.validate(rec) == [], SR.validate(rec)
    return rec


OTHER_READER = {"provider": "other-ai", "model": "other-model-1"}


def _confirm(rec):
    """A second contributor's matching build: level confirmed (store_record.level)."""
    two = SR.add_evidence(rec, dict(rec["evidence"][0], by="bob"))
    assert SR.level(two) == "confirmed"
    return two


def _as_also(rec, mpn="DSPART1"):
    """rec (confirmed) filed under another code of its maker, with the part's own code in `also`: a lookup by the
    part's code finds it through `also`, so it is never used directly, only compared (a second opinion)."""
    out = copy.deepcopy(rec)
    maker = out["key"]["maker"]
    out["key"] = {"maker": maker, "mpn": f"{mpn}ALT", "key": SK.key(maker, f"{mpn}ALT")}
    out["also"] = [SK.key(maker, mpn)]
    assert SR.validate(out) == [], SR.validate(out)
    return out


def _store(root, recs, status=None, withdrawn=()):
    """A store folder holding these records (index level from store_record.level). status: key_id -> status."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "store.json").write_text(json.dumps({"schema": 1, "name": "test", "shards": 256, "min_ecadlib": "0.0.1",
                                                 "updated": "2026-11-23"}))
    shards = {}
    for rec in recs:
        k = rec["key"]["key"]
        kid, ab = SK.key_id(k), SK.shard(k)
        (root / "parts" / ab).mkdir(parents=True, exist_ok=True)
        (root / "parts" / ab / f"{kid}.json").write_text(SK.dumps(rec))
        entry = {"key": k, "also": rec.get("also") or [], "rev": rec["rev"], "sha256": SK.digest(rec),
                 "level": SR.level(rec), "status": (status or {}).get(kid, "active")}
        for s in {ab} | {SK.shard(a) for a in rec.get("also") or []}:   # as store_repo indexes `also` codes
            shards.setdefault(s, {})[kid] = entry
    (root / "index").mkdir(exist_ok=True)
    for ab, idx in shards.items():
        (root / "index" / f"{ab}.json").write_text(json.dumps(idx))
    (root / "withdrawn.json").write_text(json.dumps(list(withdrawn)))
    return root


def _run(d, bom=TL.BOM, http=None, log=None, **kw):
    """tests/test_librarian.run, with the log kept: (items by name, log lines)."""
    lines = []
    b = Path(d) / "bom.csv"
    b.write_text(bom)
    http = http or TL.FakeHTTP({"C2002": TL.EE, "C3003": TL.ee_as("TPS1234DSGR", "C3003", TL.SOIC8)})
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(D, "_resolve", lambda host: []):
        items = L.run(b, [], Path(d) / "out", cache=Path(d) / "cache", http=http, log=lambda *a: lines.append(
            " ".join(map(str, a))), **kw)
    return {i["name"]: i for i in items}, lines


def _fp_text(it, d):
    return (Path(d) / "out" / it["footprint"]["kicad_mod"]).read_text(encoding="utf-8")


def _tamper(rec):
    """The record with its first pad 0.1 mm wider (hashes and evidence made to match, so it validates)."""
    out = copy.deepcopy(rec)
    text = out["footprint"]["kicad_mod"]
    m = re.search(r"\(pad [^()]*?\(at [^)]*\)\s*\(size ([\d.]+) ([\d.]+)\)", text)
    assert m, text[:400]
    w = f"{float(m.group(1)) + 0.1:g}"
    out["footprint"]["kicad_mod"] = text[:m.start(1)] + w + text[m.end(1):]
    SR.rehash(out)
    assert SR.validate(out) == [], SR.validate(out)
    return out


# ---------------------------------------------------------------- (a) + (g) a confirmed record builds the part
@lite
def test_confirmed_record_builds_the_part_with_kicad_credit(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    recs = {n: _confirm(_record(items[n], base)) for n in ("RES-0603-10k", "TPS1234DSGR")}
    root = _store(tmp_path / "store", recs.values())
    d = tmp_path / "ws"
    d.mkdir()
    got, log = _run(d, http=TL.FakeHTTP(), sources=["store"], store=SC.Store(str(root)))
    for n, rec in recs.items():
        it, kid = got[n], SK.key_id(rec["key"]["key"])
        assert it["status"] == "ready", (n, it["hold_reasons"])
        assert it["source"] == {"symbol": f"store:{kid}@r1", "footprint": f"store:{kid}@r1"}, it["source"]
        assert FS.digest(_fp_text(it, d)) == rec["footprint"]["hash"]
        assert it["store"]["footprint"].startswith("kicad:") and it["store"]["level"] == "confirmed"
        credit = {p["name"]: p["value"] for p in it["parameters"]}.get("Library Credit", "")
        assert "CC-BY-SA" in credit or "KiCad" in credit, it["parameters"]
        assert any("from the shared store's record" in t for t in it["librarian"]["tried"]), it["librarian"]["tried"]
    assert "KiCad" in (d / "out" / "NOTICE.txt").read_text(encoding="utf-8")
    assert not any(l.startswith("  store:") for l in log), log            # a store that answers: no trouble line


# ---------------------------------------------------------------- (b) accept levels
@lite
def test_checked_record_needs_accept_checked(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = _record(items["RES-0603-10k"], base)
    assert SR.level(rec) == "checked"
    root = _store(tmp_path / "store", [rec])
    bom = TL.BOM.splitlines()[0] + "\n" + TL.BOM.splitlines()[1] + "\n"
    for accept, used in (("confirmed", False), ("checked", True), ("off", False)):
        d = tmp_path / accept
        d.mkdir()
        it = _run(d, bom=bom, http=TL.FakeHTTP(), sources=["store"],
                  store=SC.Store(str(root), accept=accept))[0]["RES-0603-10k"]
        assert str(it["source"]["footprint"]).startswith("store:") == used, (accept, it["source"])
        if accept == "confirmed":
            assert it["status"] == "held" and any("kept as a second opinion" in t for t in it["librarian"]["tried"])
        if accept == "checked":
            assert it["status"] == "ready" and it["store"]["level"] == "checked", it["hold_reasons"]


# ---------------------------------------------------------------- (c) second opinion
@lite
def test_second_opinion_agreeing_lifts_the_read_hold_and_disagreeing_holds(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    it, base = _datasheet_part()
    rec = _record(it, base, by="carol", reader=OTHER_READER)   # another person's read, by another model: checked
    assert SR.level(rec) == "checked"
    agree = _as_also(_confirm(rec))                           # confirmed, found through `also`: a second opinion
    for name, recs, ready in (("agree", [agree], True), ("disagree", [_tamper(rec)], False)):
        d = tmp_path / name
        d.mkdir()
        with _reader():
            got = _run(d, api_key="sk-test", sources=["store"] + DS_SOURCES, rules=DS_RULES,
                       store=SC.Store(str(_store(tmp_path / f"s_{name}", recs))))[0]["DSPART1"]
        assert got["source"]["footprint"] == "datasheet:https://example.com/ds.pdf", got["source"]
        if ready:
            assert got["status"] == "ready", got["hold_reasons"]
            assert any("matches an independent build in the shared store" in t for t in got["librarian"]["tried"])
            assert got["store_check"]["result"] == "agreed" and got["store_check"]["lifted"]
        else:
            assert got["status"] == "held"
            assert any("has other pads than this build" in r for r in got["hold_reasons"]), got["hold_reasons"]
            assert any(r.startswith("verify: datasheet reader output") for r in got["hold_reasons"])   # not lifted
            assert got["store_check"]["result"] == "disagreed"
    # without the store, the same read stays held (the hold the agreement lifted is real)
    d = tmp_path / "none"
    d.mkdir()
    with _reader():
        got = _run(d, api_key="sk-test", sources=DS_SOURCES, rules=DS_RULES)[0]["DSPART1"]
    assert got["status"] == "held" and "store_check" not in got


@lite
def test_second_opinion_from_the_same_kicad_entry_lifts_nothing(tmp_path, monkeypatch):
    """A confirmed record made from the same KiCad footprint this build used is no independent build."""
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = _as_also(_confirm(_record(items["TPS1234DSGR"], base)), "TPS1234DSGR")
    d = tmp_path / "ws"
    d.mkdir()
    got = _run(d, store=SC.Store(str(_store(tmp_path / "s", [rec]))))[0]["TPS1234DSGR"]
    assert got["source"]["footprint"].startswith("kicad:")
    assert got["store_check"]["result"] == "agreed, not independent", got.get("store_check")


# ---------------------------------------------------------------- (d) store off: nothing changes
@lite
def test_store_off_changes_nothing_and_an_explicit_order_is_kept(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    plans = {}
    for name, kw in (("default", {}), ("without", {"sources": ["kicad", "easyeda", "standard", "datasheet"]})):
        d = tmp_path / name
        d.mkdir()
        http = TL.FakeHTTP({"C2002": TL.EE, "C3003": TL.ee_as("TPS1234DSGR", "C3003", TL.SOIC8)})
        _, log = _run(d, http=http, rules={}, **kw)
        assert not any("store" in u for u in http.calls) and not any("store" in l.lower() for l in log), log
        plans[name] = [{k: v for k, v in i.items() if k not in ("librarian", "built_at", "release_check")}
                       for i in json.loads((d / "out" / "plan.json").read_text(encoding="utf-8"))]
        for i in plans[name]:
            assert "store" not in i and not any(str(s).startswith("store:") for s in i["source"].values())
    assert json.dumps(plans["default"], sort_keys=True, default=str) == \
        json.dumps(plans["without"], sort_keys=True, default=str)
    # a store set: asked first, though [sources] order does not name it ("store" is never needed there)
    items, base = _kicad()
    root = _store(tmp_path / "s", [_confirm(_record(items["RES-0603-10k"], base))])
    d = tmp_path / "order"
    d.mkdir()
    rules = {"store": {"url": str(root)}, "sources": {"order": ["kicad", "easyeda"]}}
    got, log = _run(d, rules=rules)
    assert got["RES-0603-10k"]["source"]["footprint"].startswith("store:"), got["RES-0603-10k"]["source"]
    lock = json.loads((d / "out" / "library.lock.json").read_text(encoding="utf-8"))
    assert lock["settings"]["sources"][0] == "store"
    # and accept = "off" is the off switch: as without a store
    d = tmp_path / "off"
    d.mkdir()
    got, log = _run(d, rules=dict(rules, store={"url": str(root), "accept": "off"}))
    assert got["RES-0603-10k"]["source"]["footprint"].startswith("kicad:")
    lock = json.loads((d / "out" / "library.lock.json").read_text(encoding="utf-8"))
    assert "store" not in lock["settings"]["sources"]


# ---------------------------------------------------------------- (e) withdrawn / disputed
@lite
def test_withdrawn_record_holds_the_part_on_the_next_run_and_upkeep_lists_it(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = _confirm(_record(items["RES-0603-10k"], base))
    kid = SK.key_id(rec["key"]["key"])
    root = _store(tmp_path / "s", [rec])
    bom = "\n".join(TL.BOM.splitlines()[:2]) + "\n"
    d = tmp_path / "ws"
    d.mkdir()
    it = _run(d, bom=bom, http=TL.FakeHTTP(), sources=["store", "kicad"], store=SC.Store(str(root)))[0]["RES-0603-10k"]
    assert it["status"] == "ready" and it["source"]["footprint"] == f"store:{kid}@r1"
    _store(root, [rec], withdrawn=[{"key_id": kid, "rev": 1, "reason": "pad 1 too small"}])
    for _ in range(2):                                     # resumed (skipped) parts too, and it stays held
        it = _run(d, bom=bom, http=TL.FakeHTTP(), sources=["store", "kicad"], store=SC.Store(str(root)))[0]["RES-0603-10k"]
        assert it["status"] == "held" and it["source"]["footprint"] == f"store:{kid}@r1"
        assert any("was withdrawn from the store (pad 1 too small)" in r for r in it["hold_reasons"]), it["hold_reasons"]
    plan = d / "out" / "plan.json"
    item = json.loads(plan.read_text(encoding="utf-8"))[0]
    assert RC.store_problems(item, SC.Store(str(root))) == [
        f"verify: shared store record {rec['key']['key']} r1 was withdrawn from the store (pad 1 too small): this part "
        "was built from it"]
    probs = RC.publish_problems(dict(item, status="ready"), d / "out", store=SC.Store(str(root)))   # (a hand edit)
    assert any("withdrawn" in p for p in probs), probs
    res = UK.run(plan, checks=["store"], state_dir=tmp_path / "upk", out=tmp_path / "upk" / "upkeep",
                 ctx=UK.Ctx(store=SC.Store(str(root))), now="2026-11-23T10:00:00Z")
    text = json.dumps(res, default=str)
    assert "store: withdrawn" in text and "RES-0603-10k" in text, text[:2000]


@lite
def test_disputed_record_holds_a_part_built_from_it(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = _confirm(_record(items["RES-0603-10k"], base))
    kid = SK.key_id(rec["key"]["key"])
    bom = "\n".join(TL.BOM.splitlines()[:2]) + "\n"
    root = _store(tmp_path / "s", [rec])
    d = tmp_path / "ws"
    d.mkdir()
    assert _run(d, bom=bom, http=TL.FakeHTTP(), sources=["store"], store=SC.Store(str(root)))[0]["RES-0603-10k"][
        "status"] == "ready"
    _store(root, [rec], status={kid: "disputed"})
    it = _run(d, bom=bom, http=TL.FakeHTTP(), sources=["store"], store=SC.Store(str(root)))[0]["RES-0603-10k"]
    assert it["status"] == "held" and any("is disputed" in r for r in it["hold_reasons"]), it["hold_reasons"]


# ---------------------------------------------------------------- (f) an unreachable store holds nothing
@lite
def test_unreachable_store_completes_with_one_note(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    d = tmp_path / "ws"
    d.mkdir()
    store = SC.Store(BASE, http=StoreHTTP(None, fail="error"), cache_dir=tmp_path / "sc")
    got, log = _run(d, store=store)
    ref = tmp_path / "ref"
    ref.mkdir()
    want, _ = _run(ref, sources=["kicad", "easyeda", "standard", "datasheet"])
    assert {n: (i["status"], i["source"]) for n, i in got.items()} == \
        {n: (i["status"], i["source"]) for n, i in want.items()}
    notes = [l for l in log if l.startswith("  store:")]
    assert len(notes) == 1, log
    assert not any("store" in r for i in got.values() for r in i["hold_reasons"])


# ---------------------------------------------------------------- (h) the BOM's package against the record's
@lite
def test_bom_package_clash_holds_a_store_part(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    root = _store(tmp_path / "s", [_confirm(_record(items["RES-0603-10k"], base))])
    bom = TL.BOM.splitlines()[0] + "\n" + TL.BOM.splitlines()[1].replace("R_0603_1608Metric", "R_1206_3216Metric") + "\n"
    d = tmp_path / "ws"
    d.mkdir()
    got = _run(d, bom=bom, http=TL.FakeHTTP(), sources=["store"], store=SC.Store(str(root)))[0]
    assert len(got) == 1, got.keys()
    it = list(got.values())[0]
    assert it["source"]["footprint"].startswith("store:")
    assert it["status"] == "held" and any("R_1206_3216Metric" in r and "shared store" in r for r in it["hold_reasons"]), \
        it["hold_reasons"]


# ---------------------------------------------------------------- (j) --offline asks nothing
@lite
def test_offline_run_contacts_no_store(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    http = StoreHTTP(tmp_path / "nothing")
    d = tmp_path / "ws"
    d.mkdir()
    rules = {"store": {"url": BASE}}
    store = SC.from_rules(rules, http=http, cache_dir=tmp_path / "sc", offline=True)
    got, _ = _run(d, offline=True, rules=rules, store=store)
    assert http.urls == [] and not any(str(s).startswith("store:") for i in got.values() for s in i["source"].values())


# ---------------------------------------------------------------- (i) ecadlib store
def _cli(*argv):
    buf = io.StringIO()
    code = CLI.main([str(a) for a in argv], out=buf)
    return code, buf.getvalue()


@lite
def test_cli_share_writes_only_shareable_parts_and_no_bom_fields(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    _, base = _kicad()
    plan = json.loads((base / "plan.json").read_text(encoding="utf-8"))
    for i in plan:                                         # a field of the user's own: never shared
        i["parameters"] = list(i.get("parameters") or []) + [{"name": "Internal Code", "value": "SECRET-4242"}]
    p = Path(base) / "plan_secret.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    try:
        code, text = _cli("share", p, "-o", tmp_path / "share", "--by", "alice")
    finally:
        p.unlink()
    files = sorted((tmp_path / "share").rglob("*.json"))
    ready = [i for i in plan if not SR.shareable(i, base)]
    assert code == 0 and len(files) == len(ready) and ready, (text, files)
    assert {json.loads(f.read_text())["key"]["mpn"] for f in files} == \
        {SR.from_item(i, base, by="alice", at=AT)["key"]["mpn"] for i in ready}
    for f in files:
        body = f.read_text(encoding="utf-8")
        assert "SECRET-4242" not in body and "Internal Code" not in body and "C1001" not in body
        rec = json.loads(body)
        assert SR.validate(rec) == [] and f.parent.name == SK.shard(rec["key"]["key"])
        assert f.stem == SK.key_id(rec["key"]["key"])
    assert "Nothing has been sent" in text and "EEPART1" in text      # a part not shared is named, with why


@lite
def test_cli_status_report_and_store_repo(tmp_path):
    toml = tmp_path / "ecadlib.toml"
    toml.write_text("")
    code, text = _cli("status", "--rules", toml)
    assert code == 0 and "off" in text.lower(), text
    toml.write_text('[store]\nurl = "https://raw.githubusercontent.com/acme/parts-store/main"\n')
    code, text = _cli("report", "ACME", "TPS1234DSGR", "--reason", "pad 1 is too small", "--print", "--rules", toml)
    assert code == 0, text
    url = re.search(r"https://github\.com/\S+", text).group(0)
    assert url.startswith("https://github.com/acme/parts-store/issues/new?") and "TPS1234DSGR" in url, url
    with mock.patch.dict(sys.modules, {"ecadlib.store_repo": None}), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        assert CLI.store_repo_main(["verify", "."]) == 2       # not in this install: said, no traceback
