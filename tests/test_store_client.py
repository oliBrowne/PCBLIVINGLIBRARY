"""Shared store, read side (ecadlib/store_client.py): folder and HTTP stores, hashes, withdrawn list, snapshot mode,
the accept policy, and that a lookup never raises and never names the part in a URL."""
import io, json, re, zipfile
from pathlib import Path

import ecadlib
from ecadlib import store_client as SC
from ecadlib import store_keys as SK
from ecadlib.httpcache import Response
from tests.fixtures_lite import lite

BASE = "https://raw.githubusercontent.com/example/store/main"


def record(maker, mpn, rev=1, also=(), schema=1, status="active"):
    return {"schema": schema, "key": {"maker": maker, "mpn": mpn}, "also": list(also), "rev": rev, "status": status,
            "evidence": [{"kind": "build", "by": "someone"}]}


def make_store(root, parts=(), withdrawn=(), meta=None):
    """parts: (maker, mpn, dict of overrides) -> a store folder. Overrides: level, status, rev, also, schema, sha256."""
    root = Path(root)
    (root / "index").mkdir(parents=True, exist_ok=True)
    meta = dict({"schema": 1, "name": "test", "shards": 256, "min_ecadlib": "0.0.1", "updated": "2026-11-23"},
                **(meta or {}))
    (root / "store.json").write_text(json.dumps(meta))
    shards = {}
    for maker, mpn, o in parts:
        k = SK.key(maker, mpn)
        kid, ab = SK.key_id(k), SK.shard(k)
        rec = record(maker, mpn, o.get("rev", 1), [SK.key(maker, a) for a in o.get("also", ())], o.get("schema", 1))
        (root / "parts" / ab).mkdir(parents=True, exist_ok=True)
        (root / "parts" / ab / f"{kid}.json").write_text(json.dumps(rec))
        shards.setdefault(ab, {})[kid] = {"key": k, "also": rec["also"], "rev": rec["rev"],
                                          "sha256": o.get("sha256", SK.digest(rec)),
                                          "level": o.get("level", "confirmed"), "status": o.get("status", "active")}
    for ab, idx in shards.items():
        (root / "index" / f"{ab}.json").write_text(json.dumps(idx))
    (root / "withdrawn.json").write_text(json.dumps([dict(w) for w in withdrawn]))
    return root


class FakeHTTP:
    """Serves a store folder at BASE. fail: 'error' (Response with error), 'raise' (an exception) or None."""

    def __init__(self, root, fail=None, zips=None):
        self.root, self.fail, self.zips, self.urls = Path(root) if root else None, fail, zips or {}, []

    def get(self, url, **kw):
        self.urls.append(url)
        if self.fail == "raise":
            raise OSError("network is down")
        if self.fail == "error":
            return Response(0, "", error="ConnectionError: unreachable")
        if self.fail == "500":
            return Response(500, "oops", error="HTTP 500")
        assert url.startswith(BASE + "/"), url
        f = self.root / url[len(BASE) + 1:]
        return Response(200, f.read_text(encoding="utf-8")) if f.is_file() else Response(404, "")

    def get_bytes(self, url):
        self.urls.append(url)
        if self.fail:
            raise OSError("network is down")
        return self.zips[url]


def test_folder_hit_and_miss(tmp_path):
    root = make_store(tmp_path / "s", [("Texas Instruments", "NE555DR", {"rev": 2})])
    st = SC.Store(str(root))
    h = st.lookup("TI", "ne555dr")
    assert h and h.key == "TEXASINSTRUMENTS|NE555DR" and h.rev == 2 and h.level == "confirmed" and h.status == "active"
    assert h.key_id == SK.key_id(h.key) and h.sha256 == SK.digest(h.record) and h.record["key"]["mpn"] == "NE555DR"
    assert h.source == f"store:{h.key_id}@r2" and SC.parse_source(h.source) == (h.key_id, 2)
    assert st.lookup("TI", "LM358DR") is None and st.lookup("Analog Devices", "NE555DR") is None
    assert st.lookup("TI", "") is None and st.trouble == []
test_folder_hit_and_miss = lite(test_folder_hit_and_miss)


def test_file_url(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    assert SC.Store(root.resolve().as_uri()).lookup("TI", "NE555DR")
test_file_url = lite(test_file_url)


def test_also_match(tmp_path):
    """An also code is found in its own shard: the store's index lists the entry under every shard its codes fall in
    (the shard is by MPN), so the test copies the entry there as the store's indexer would."""
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {"also": ["NE555DT"]})])
    idx = json.loads((root / "index" / f"{SK.shard('NE555DR')}.json").read_text())
    (root / "index" / f"{SK.shard('NE555DT')}.json").write_text(json.dumps(idx))
    h = SC.Store(str(root)).lookup("Texas Instruments", "ne555-dt")
    assert h and h.key == "TEXASINSTRUMENTS|NE555DR" and "NE555DT" in h.note and h.record["also"]
    assert SC.Store(str(root)).lookup("onsemi", "NE555DT") is None              # another maker's NE555DT is not it
    h = SC.Store(str(root)).lookup("", "NE555DT")                                # no maker: also codes count too
    assert h and h.key == "TEXASINSTRUMENTS|NE555DR" and "without a maker" in h.note
test_also_match = lite(test_also_match)


def test_no_maker_one_maker_hit_with_note(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    h = SC.Store(str(root)).lookup("", "NE555DR")
    assert h and h.key == "TEXASINSTRUMENTS|NE555DR" and "without a maker" in h.note and "TEXASINSTRUMENTS" in h.note
    assert SC.Store(str(root)).lookup("N/A", "NE555DR")
test_no_maker_one_maker_hit_with_note = lite(test_no_maker_one_maker_hit_with_note)


def test_no_maker_two_makers_miss(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "LM358DR", {}), ("onsemi", "LM358DR", {})])
    st = SC.Store(str(root))
    assert st.lookup(None, "LM358DR") is None and st.trouble == []
    assert st.lookup("onsemi", "LM358DR").key == "ON|LM358DR"                  # with a maker it is exact
    root = make_store(tmp_path / "t", [("TI", "NE555DR", {}), ("onsemi", "NE555DRX", {"also": ["NE555DR"]})])
    idx = json.loads((root / "index" / f"{SK.shard('NE555DRX')}.json").read_text())
    f = root / "index" / f"{SK.shard('NE555DR')}.json"
    f.write_text(json.dumps(dict(json.loads(f.read_text()), **idx)))
    assert SC.Store(str(root)).lookup("", "NE555DR") is None          # one maker by name, another through also
    assert SC.Store(str(root)).lookup("TI", "NE555DR").key == "TEXASINSTRUMENTS|NE555DR"
test_no_maker_two_makers_miss = lite(test_no_maker_two_makers_miss)


def test_sha_mismatch_refused(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {"sha256": "0" * 64})])
    st = SC.Store(str(root))
    assert st.lookup("TI", "NE555DR") is None and st.lookup("TI", "NE555DR") is None
    assert len(st.trouble) == 1 and "hash" in st.trouble[0]
test_sha_mismatch_refused = lite(test_sha_mismatch_refused)


def test_withdrawn_same_rev_refused_later_rev_allowed(tmp_path):
    k = SK.key("TI", "NE555DR")
    w = {"key_id": SK.key_id(k), "key": k, "rev": 2, "reason": "wrong pad pitch", "issue": "#1", "at": "2026-11-20"}
    st = SC.Store(str(make_store(tmp_path / "a", [("TI", "NE555DR", {"rev": 2})], [w])))
    assert st.lookup("TI", "NE555DR") is None
    assert st.is_withdrawn(SK.key_id(k), 2)["reason"] == "wrong pad pitch" and st.is_withdrawn(SK.key_id(k), 1)
    assert st.is_withdrawn(SK.key_id(k), 3) is None and SK.key_id(k) in st.withdrawn()
    st = SC.Store(str(make_store(tmp_path / "b", [("TI", "NE555DR", {"rev": 3})], [w])))
    assert st.lookup("TI", "NE555DR").rev == 3
test_withdrawn_same_rev_refused_later_rev_allowed = lite(test_withdrawn_same_rev_refused_later_rev_allowed)


def test_parse_source():
    assert SC.parse_source("store:" + "a" * 32 + "@r12") == ("a" * 32, 12)
    for s in ("kicad", "store:xyz@r1", "store:" + "a" * 32, None, "", "store:" + "a" * 32 + "@rX"):
        assert SC.parse_source(s) is None


def test_newer_record_schema(tmp_path):
    st = SC.Store(str(make_store(tmp_path / "s", [("TI", "NE555DR", {"schema": SK.SCHEMA + 1})])))
    assert st.lookup("TI", "NE555DR") is None and len(st.trouble) == 1 and "update ecadlib" in st.trouble[0]
test_newer_record_schema = lite(test_newer_record_schema)


def test_min_ecadlib(tmp_path, monkeypatch):
    monkeypatch.setattr(ecadlib, "__version__", "0.4.1")
    st = SC.Store(str(make_store(tmp_path / "s", [("TI", "NE555DR", {}), ("TI", "LM358DR", {})],
                                 meta={"min_ecadlib": "0.10.0"})))
    assert st.lookup("TI", "NE555DR") is None and st.lookup("TI", "LM358DR") is None
    assert len(st.trouble) == 1 and "0.10.0" in st.trouble[0] and "update ecadlib" in st.trouble[0]
    monkeypatch.setattr(ecadlib, "__version__", "0.10.0")
    assert SC.Store(str(tmp_path / "s")).lookup("TI", "NE555DR")
test_min_ecadlib = lite(test_min_ecadlib)


def test_unreachable_one_trouble_never_raises(tmp_path):
    for fail in ("error", "raise", "500"):
        http = FakeHTTP(None, fail=fail)
        st = SC.Store(BASE, http=http, cache_dir=tmp_path / fail)
        assert all(st.lookup("TI", f"PART{i}") is None for i in range(20))
        assert len(st.trouble) == 1 and "could not be reached" in st.trouble[0], (fail, st.trouble)
        assert len(http.urls) <= 1                       # gave up after the first failure, not one wait per part
        assert st.withdrawn() == {} and st.is_withdrawn("a" * 32, 1) is None and st.entry("a" * 32, "ab") is None
test_unreachable_one_trouble_never_raises = lite(test_unreachable_one_trouble_never_raises)


def test_bad_json_never_raises(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    (root / "index" / f"{SK.shard('NE555DR')}.json").write_text("{not json")
    st = SC.Store(str(root))
    assert st.lookup("TI", "NE555DR") is None and len(st.trouble) == 1
    (root / "store.json").write_text("[]")
    st = SC.Store(str(root))
    assert st.lookup("TI", "NE555DR") is None and len(st.trouble) == 1
    st = SC.Store(str(tmp_path / "nowhere"))
    assert st.lookup("TI", "NE555DR") is None and len(st.trouble) == 1
test_bad_json_never_raises = lite(test_bad_json_never_raises)


def test_http_hit_and_cache(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    http = FakeHTTP(root)
    st = SC.Store(BASE, http=http, cache_dir=tmp_path / "c")
    assert st.lookup("TI", "NE555DR") and st.lookup("TI", "NE555DR") and st.trouble == []
    n = len(http.urls)
    assert n == 4                                              # store.json, index, withdrawn, record: each once
    off = FakeHTTP(root, fail="raise")
    st = SC.Store(BASE, http=off, cache_dir=tmp_path / "c", offline=True)
    assert st.lookup("TI", "NE555DR") and off.urls == [] and st.trouble == []
    assert SC.Store(BASE, http=off, cache_dir=tmp_path / "other", offline=True).lookup("TI", "NE555DR") is None
test_http_hit_and_cache = lite(test_http_hit_and_cache)


def test_offline_cache_only(tmp_path):
    http = FakeHTTP(make_store(tmp_path / "s", [("TI", "NE555DR", {})]))
    st = SC.Store(BASE, http=http, cache_dir=tmp_path / "c", offline=True)
    assert st.lookup("TI", "NE555DR") is None and http.urls == [] and len(st.trouble) == 1
    assert "offline" in st.trouble[0]
test_offline_cache_only = lite(test_offline_cache_only)


def test_stale_index_used_when_refresh_fails(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    t = [1000.0]
    SC.Store(BASE, http=FakeHTTP(root), cache_dir=tmp_path / "c", now=lambda: t[0]).lookup("TI", "NE555DR")
    t[0] += SC.TTL_S + 1                                        # the index is old: asked again, the network fails
    down = FakeHTTP(root, fail="error")
    st = SC.Store(BASE, http=down, cache_dir=tmp_path / "c", now=lambda: t[0])
    assert st.lookup("TI", "NE555DR") and len(down.urls) == 1 and len(st.trouble) == 1
    fresh = FakeHTTP(root)                                      # within the TTL: nothing asked again
    st = SC.Store(BASE, http=fresh, cache_dir=tmp_path / "c", now=lambda: t[0] - SC.TTL_S)
    assert st.lookup("TI", "NE555DR") and fresh.urls == []
test_stale_index_used_when_refresh_fails = lite(test_stale_index_used_when_refresh_fails)


def test_refresh_sees_new_index(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {"rev": 1})])
    t = [1000.0]
    SC.Store(BASE, http=FakeHTTP(root), cache_dir=tmp_path / "c", now=lambda: t[0]).lookup("TI", "NE555DR")
    make_store(root, [("TI", "NE555DR", {"rev": 2, "status": "disputed"})])
    t[0] += SC.TTL_S + 1
    h = SC.Store(BASE, http=FakeHTTP(root), cache_dir=tmp_path / "c", now=lambda: t[0]).lookup("TI", "NE555DR")
    assert h.rev == 2 and h.status == "disputed"
test_refresh_sees_new_index = lite(test_refresh_sees_new_index)


def test_url_privacy(tmp_path):
    root = make_store(tmp_path / "s", [("Texas Instruments", "NE555DR", {}), ("TI", "LM358DR", {})])
    http = FakeHTTP(root)
    st = SC.Store(BASE, http=http, cache_dir=tmp_path / "c")
    for maker, mpn in (("TI", "NE555DR"), ("TI", "LM358DR"), ("Murata", "GRM188R71H104KA93D"), ("", "BAT54S")):
        st.lookup(maker, mpn)
    allowed = {"store.json", "withdrawn.json"}
    for u in http.urls:
        rel = u[len(BASE) + 1:]
        assert rel in allowed or re.fullmatch(r"index/[0-9a-f]{2}\.json|parts/[0-9a-f]{2}/[0-9a-f]{32}\.json", rel), u
        for word in ("NE555", "LM358", "GRM188", "BAT54", "TEXAS", "MURATA", "?"):
            assert word.lower() not in u.lower(), u
test_url_privacy = lite(test_url_privacy)


def test_store_off_contacts_nothing(tmp_path):
    http = FakeHTTP(None, fail="raise")
    for st in (SC.Store("", http=http, cache_dir=tmp_path), SC.from_rules({}, http, tmp_path, False),
               SC.from_rules({"store": {"url": BASE, "accept": "off"}}, http, tmp_path, False)):
        assert st.lookup("TI", "NE555DR") is None and st.withdrawn() == {} and st.trouble == [] and not st.enabled
    assert http.urls == []


def test_from_rules(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    st = SC.from_rules({"store": {"url": str(root), "accept": "checked"}}, None, tmp_path / "c", False)
    assert st.enabled and st.accept == "checked" and st.mode == "online" and st.lookup("TI", "NE555DR")
    assert SC.from_rules({"store": {"url": str(root)}}, None, None, False).accept == "confirmed"
test_store_off_contacts_nothing = lite(test_store_off_contacts_nothing)
test_from_rules = lite(test_from_rules)


def test_decide_table():
    h = lambda level, status="active": SC.Hit({}, "A|B", "x", 1, level, status, "s")
    assert SC.decide(h("confirmed")) == "use" and SC.decide(h("checked")) == "second-opinion"
    assert SC.decide(h("checked"), accept="checked") == "use"
    assert SC.decide(h("confirmed", "disputed")) == "second-opinion"
    assert SC.decide(h("confirmed", "disputed"), accept="checked") == "second-opinion"
    assert SC.decide(h("confirmed"), accept="off") == "skip" and SC.decide(None) == "skip"
    assert SC.decide(h("withdrawn")) == "skip" and SC.decide(h("confirmed", "odd")) == "skip"
    assert SC.decide(h("confirmed"), accept="bogus") == "skip"


def test_entry_and_status(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {"status": "disputed", "level": "checked"})])
    st, kid = SC.Store(str(root)), SK.key_id(SK.key("TI", "NE555DR"))
    assert st.entry(kid, "NE555DR")["level"] == "checked" and st.status(kid, SK.shard("NE555DR")) == "disputed"
    assert st.status(kid, SK.key("TI", "NE555DR")) == "disputed" and st.entry("f" * 32, "NE555DR") is None
    h = st.lookup("TI", "NE555DR")
    assert h.status == "disputed" and SC.decide(h, "checked") == "second-opinion"
test_entry_and_status = lite(test_entry_and_status)


def zip_of(root, prefix="", extra=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(Path(root).rglob("*")):
            if f.is_file():
                z.write(f, prefix + f.relative_to(root).as_posix())
        for name, data in extra:
            z.writestr(name, data)
    return buf.getvalue()


def test_snapshot_serves_without_further_requests(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {}), ("TI", "LM358DR", {})])
    snap = SC.default_snapshot_url(BASE)
    http = FakeHTTP(root, zips={snap: zip_of(root, prefix="store-main/")})
    st = SC.Store(BASE, http=http, cache_dir=tmp_path / "c", mode="snapshot")
    assert st.lookup("TI", "NE555DR") and st.lookup("TI", "LM358DR") and st.lookup("TI", "X1") is None
    assert http.urls == [snap] and st.trouble == []
    again = FakeHTTP(root, fail="raise")                          # within the TTL: the unpacked copy, no request
    st = SC.Store(BASE, http=again, cache_dir=tmp_path / "c", mode="snapshot")
    assert st.lookup("TI", "NE555DR") and again.urls == []
    t = SC.SNAPSHOT_TTL_S + 10 ** 10                              # old and the download fails: the old copy serves
    st = SC.Store(BASE, http=again, cache_dir=tmp_path / "c", mode="snapshot", now=lambda: t)
    assert st.lookup("TI", "NE555DR") and len(again.urls) == 1 and len(st.trouble) == 1
test_snapshot_serves_without_further_requests = lite(test_snapshot_serves_without_further_requests)


def test_snapshot_unsafe_zip_refused(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    snap = SC.default_snapshot_url(BASE)
    for bad in ("../evil.json", "/etc/evil.json", "a/../../evil.json", "C:/evil.json", "a\\..\\..\\evil.json"):
        http = FakeHTTP(root, zips={snap: zip_of(root, extra=[(bad, "{}")])})
        cache = tmp_path / ("c" + str(abs(hash(bad))))
        st = SC.Store(BASE, http=http, cache_dir=cache, mode="snapshot")
        assert st.lookup("TI", "NE555DR") is None and len(st.trouble) == 1 and "snapshot" in st.trouble[0], bad
        assert not (tmp_path / "evil.json").exists() and not (cache / "evil.json").exists()
        assert not any(p.name == "evil.json" for p in tmp_path.rglob("evil.json")), bad


def test_snapshot_caps(tmp_path, monkeypatch):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    snap = SC.default_snapshot_url(BASE)
    monkeypatch.setattr(SC, "SNAPSHOT_MAX_MEMBERS", 2)
    st = SC.Store(BASE, http=FakeHTTP(root, zips={snap: zip_of(root)}), cache_dir=tmp_path / "a", mode="snapshot")
    assert st.lookup("TI", "NE555DR") is None and "snapshot" in st.trouble[0]
    monkeypatch.setattr(SC, "SNAPSHOT_MAX_MEMBERS", 1000)
    monkeypatch.setattr(SC, "SNAPSHOT_MAX_BYTES", 100)
    st = SC.Store(BASE, http=FakeHTTP(root, zips={snap: zip_of(root)}), cache_dir=tmp_path / "b", mode="snapshot")
    assert st.lookup("TI", "NE555DR") is None and "snapshot" in st.trouble[0]
    st = SC.Store(BASE, http=FakeHTTP(root, zips={snap: b"not a zip"}), cache_dir=tmp_path / "d", mode="snapshot")
    assert st.lookup("TI", "NE555DR") is None and len(st.trouble) == 1
test_snapshot_unsafe_zip_refused = lite(test_snapshot_unsafe_zip_refused)
test_snapshot_caps = lite(test_snapshot_caps)


def test_default_snapshot_url():
    from ecadlib import store_client as SC
    assert SC.default_snapshot_url("https://raw.githubusercontent.com/o/r/main") == \
        "https://github.com/o/r/releases/download/snapshot/snapshot.zip"
    assert SC.default_snapshot_url("https://x.test/store") == "https://x.test/store/snapshot.zip"
    assert SC.default_snapshot_url("") == ""
