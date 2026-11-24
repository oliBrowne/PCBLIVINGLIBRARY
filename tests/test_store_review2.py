"""Shared store: regression tests for the second code review of the store PR (each pins one finding the reviewer
proved with a script; the script's name is in the test's docstring).

1  a second opinion lifts a hold only for a CONFIRMED record (one contributor's unverifiable reader claim lifts nothing)
2  content the pad hash does not see: refused by validate (zones, BOM / DNP attributes, graphics on copper / mask /
   paste, extra text, hidden pins, alternates), pin types in pins_hash, and text_hash: a confirmation is two builders
   with the same whole drawing
3  a part that depends on the store is held when the store cannot vouch for it (switched off, unreadable without a
   recent saved withdrawn list); a person's --allow keeps it; nothing changes for parts that owe the store nothing
4  KiCad-library footprints and symbols are shareable after cleaning; from_item never returns a record validate refuses
5  a broken store file never crashes a run or a publisher: it holds
6  links / e-mail in any string, bidi and zero-width characters, trusted + maintainers, no redirects, the rules hash

Every store is a folder (or a fake HTTP server) in tmp_path; nothing goes to the network, no API is called."""
import copy, glob, json, re, tempfile, time
from pathlib import Path

from ecadlib import altium_lib as AL, fp_share as FS, kicad_export as KX, kicad_import as K, \
    librarian as L, rebuild as LK, release_check as RC, rules as RU, sexpr, store_client as SC, store_keys as SK, \
    store_record as SR, upkeep as UK
from ecadlib.httpcache import CachedHTTP, Response
from ecadlib.sexpr import Q
from tests import test_librarian as TL
from tests.fixtures_lite import lite
from tests.test_store_client import BASE, FakeHTTP as StoreHTTP, make_store
from tests.test_store_librarian import DS_RULES, DS_SOURCES, OTHER_READER, _confirm, _datasheet_part, _no_dns, \
    _reader, _record, _run as _run_in, _store
from tests.test_store_record import AT, _kicad

FIX = Path(__file__).resolve().parent / "fixtures"
NOW = "2026-11-23T10:00:00Z"


def _run(d, **kw):
    Path(d).mkdir(parents=True, exist_ok=True)
    return _run_in(d, **kw)


def _bom_of(name):
    ls = TL.BOM.splitlines()
    return "\n".join([ls[0]] + [x for x in ls[1:] if f",{name}," in x]) + "\n"


def _ds_run(d, root, **kw):
    with _reader():
        return _run(d, api_key="sk-test", sources=DS_SOURCES, rules=DS_RULES, store=SC.Store(str(root)), **kw)


def _with_also(root, rec):
    """Index rec (already in the store at root) also under the shards of its `also` codes, as store_repo does."""
    ab = SK.shard(rec["key"]["key"])
    idx = json.loads((root / "index" / f"{ab}.json").read_text())
    for a in rec["also"]:
        f = root / "index" / f"{SK.shard(a)}.json"
        have = json.loads(f.read_text()) if f.is_file() else {}
        have.update(idx)
        f.write_text(json.dumps(have))
    return root


def _also_record(it, base):
    """A CONFIRMED record of DSPART1's land and pins under another code (ACME|DSPART1ALT, also ACME|DSPART1): found
    through `also`, so never used directly, only a second opinion (read by another model: independent)."""
    rec = _confirm(_record(it, base, by="carol", reader=OTHER_READER))
    rec["key"] = {"maker": "ACME", "mpn": "DSPART1ALT", "key": SK.key("ACME", "DSPART1ALT")}
    rec["also"] = [SK.key("ACME", "DSPART1")]
    assert SR.validate(rec) == [] and SR.level(rec) == "confirmed", SR.validate(rec)
    return rec


def _inject(text, s):
    last = text.rstrip().rfind(")")
    return text[:last] + s + text[last:]


def _tps():
    items, base = _kicad()
    return SR.from_item(items["TPS1234DSGR"], base, by="alice", at=AT)


# ================================================================ 1: only a confirmed record lifts a hold
@lite
def test_1_one_unconfirmed_build_lifts_nothing(tmp_path, monkeypatch):
    """p1_spoof.py, p12_relabel.py: one contributor's record (its reader claim cannot be checked, its sources can be
    relabelled) agrees with this build: noted, nothing lifted, whatever store.json trusts."""
    _no_dns(monkeypatch)
    it, base = _datasheet_part()
    spoof = _record(it, base, by="mallory", reader=OTHER_READER)
    relabel = _record(it, base, by="mallory")
    for h in ("symbol", "footprint"):
        relabel[h]["source"] = "standard:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" if h == "footprint" else "standard:G:IC"
        relabel[h]["license"], relabel[h]["credit"] = "MIT", ""
    for e in relabel["evidence"]:
        e["reader"] = None
    assert SR.validate(relabel) == []
    for tag, rec, meta in (("spoof", spoof, None), ("trusted", spoof, {"trusted": ["mallory"], "maintainers": ["oli"]}),
                           ("relabel", relabel, None)):
        root = _store(tmp_path / f"s_{tag}", [rec])
        if meta:
            m = json.loads((root / "store.json").read_text())
            (root / "store.json").write_text(json.dumps(dict(m, **meta)))
        got = _ds_run(tmp_path / tag, root)[0]["DSPART1"]
        assert got["status"] == "held", (tag, got["store_check"])
        assert got["store_check"]["result"] == "agreed, unconfirmed" and not got["store_check"].get("lifted"), tag
        assert any("not enough to release" in t for t in got["librarian"]["tried"]), tag
        assert any(h.startswith("verify: datasheet reader output") for h in got["hold_reasons"]), tag


@lite
def test_1_a_confirmed_record_through_also_lifts_and_a_withdrawal_takes_it_back(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    it, base = _datasheet_part()
    rec = _also_record(it, base)
    root = _with_also(_store(tmp_path / "s", [rec]), rec)
    d = tmp_path / "ws"
    got = _ds_run(d, root)[0]["DSPART1"]
    assert got["status"] == "ready" and got["store_check"]["result"] == "agreed", got.get("store_check")
    assert got["store_check"]["lifted"] and got["source"]["footprint"].startswith("datasheet:")
    kid = SK.key_id(rec["key"]["key"])
    (root / "withdrawn.json").write_text(json.dumps([{"key_id": kid, "rev": 1, "reason": "pin 3 and 4 swapped"}]))
    got = _ds_run(d, root)[0]["DSPART1"]
    assert got["status"] == "held" and got["store_check"]["revoked"]
    assert any("pin 3 and 4 swapped" in h for h in got["hold_reasons"])
    assert any(h.startswith("verify: datasheet reader output") for h in got["hold_reasons"])


# ================================================================ 2a: what validate refuses beyond the pad hash
ZONE = '\n  (zone (layer "F.Cu") (hatch edge 0.5) (connect_pads yes (clearance 0)) (min_thickness 0.25) (fill yes) ' \
       '(polygon (pts (xy -3 -3) (xy 3 -3) (xy 3 3) (xy -3 3))))'


def _fp_cases(km):
    ins = lambda s: _inject(km, s)
    return [
        ("zone no net", ins(ZONE), "zone"),
        ("zone mask", ins(ZONE.replace('"F.Cu"', '"F.Mask"')), "zone"),
        ("keepout", ins('\n  (zone (layers "F.Cu" "B.Cu") (hatch edge 0.5) (keepout (tracks not_allowed)) '
                        '(polygon (pts (xy -3 -3) (xy 3 -3) (xy 3 3))))'), "zone"),
        ("attr dnp", km.replace("(attr smd", "(attr smd exclude_from_bom dnp", 1), "attr exclude_from_bom dnp"),
        ("attr board_only", km.replace("(attr smd", "(attr smd board_only", 1), "attr board_only"),
        ("fp_poly copper", ins('\n  (fp_poly (pts (xy -3 -3) (xy 3 -3) (xy 3 3)) (layer "F.Cu") (width 0) (fill solid))'),
         "on a copper, mask or paste layer"),
        ("fp_line mask", ins('\n  (fp_line (start -2 0) (end 2 0) (layer "B.Mask") (width 0.5))'),
         "on a copper, mask or paste layer"),
        ("text on copper", ins('\n  (fp_text user "X" (at 0 3) (layer "F.Cu"))'), "on a copper, mask or paste layer"),
        ("extra silk text", ins('\n  (fp_text user "PIN1 IS BOTTOM RIGHT" (at 0 3) (layer "F.SilkS"))'),
         "only its reference"),
        ("second ${REFERENCE}", ins('\n  (fp_text user "${REFERENCE}" (at 0 3) (layer "F.SilkS"))'),
         "more than one ${REFERENCE}"),
        ("Datasheet field", ins('\n  (property "Datasheet" "https://example.com/x.pdf" (at 0 0 0) (layer "F.Fab"))'),
         "only its reference"),
        ("private link", ins('\n  (property "Datasheet" "http://192.168.1.5/x.pdf" (at 0 0 0) (layer "F.Fab"))'),
         "not a public web link"),
        ("descr e-mail", km.replace('(descr "', '(descr "ask bob@corp.example.com ', 1), "e-mail"),
        ("descr private link", km.replace('(descr "', '(descr "see http://intranet/x.pdf ', 1), "not a public web link"),
        ("RLO", km.replace('(descr "', '(descr "‮DNG ', 1), "bidirectional"),
        ("zero width", km.replace('(descr "', '(descr "SO​IC ', 1), "zero-width"),
    ]


@lite
def test_2a_validate_refuses_content_outside_the_pad_hash(monkeypatch):
    """p4_smuggle.py, p5b.py, p9_repo.py: none of these changes the pad hash; each is refused now."""
    _no_dns(monkeypatch)
    rec = _tps()
    assert SR.validate(rec) == []
    km = rec["footprint"]["kicad_mod"]
    for tag, text, want in _fp_cases(km):
        r = copy.deepcopy(rec)
        r["footprint"]["kicad_mod"] = text
        SR.rehash(r)
        probs = SR.validate(r)
        assert any(want in p for p in probs), (tag, probs)
    ks = rec["symbol"]["kicad_sym"]
    i = ks.find("(pin ")
    for tag, text, want in (
            ("alternate", ks.replace("(number", '(alternate "EVIL" bidirectional line) (number', 1), "alternate"),
            ("unit text e-mail", ks.replace("(pin ", '(text "bob@corp.example.com" (at 0 0 0) (effects (font (size 1 '
                                                     '1)))) (pin ', 1), "e-mail"),
            ("hidden pin", ks[:i] + ks[i:].replace("(length", "(hide yes) (length", 1), "hidden"),
            ("bare hide", ks[:i] + ks[i:].replace("(length 2.54)", "(length 2.54) hide", 1), "hidden")):
        r = copy.deepcopy(rec)
        r["symbol"]["kicad_sym"] = text
        SR.rehash(r)
        probs = SR.validate(r)
        assert any(want in p for p in probs), (tag, probs)


@lite
def test_2a_a_footprint_with_a_zone_and_dnp_is_never_built_or_indexed(tmp_path, monkeypatch):
    """p5b.py / p9_repo.py: the smuggled footprint (its pad hash unchanged) is refused by the client and by the store."""
    from ecadlib import store_repo as R
    _no_dns(monkeypatch)
    items, base = _kicad()
    rec = _confirm(_record(items["TPS1234DSGR"], base))
    bad = copy.deepcopy(rec)
    bad["footprint"]["kicad_mod"] = _inject(rec["footprint"]["kicad_mod"], ZONE).replace(
        "(attr smd", "(attr smd exclude_from_bom dnp", 1)
    assert FS.digest(bad["footprint"]["kicad_mod"]) == rec["footprint"]["hash"]
    SR.rehash(bad)
    assert SR.validate(bad)
    got = _run(tmp_path / "ws", bom=_bom_of("TPS1234DSGR"), http=TL.FakeHTTP(), sources=["store"],
               store=SC.Store(str(_store(tmp_path / "s", [bad]))))[0]["TPS1234DSGR"]
    assert not str(got["source"]["footprint"]).startswith("store:")
    assert any("refused" in t for t in got["librarian"]["tried"]), got["librarian"]["tried"]
    root = tmp_path / "repo"
    R.init(root, name="t", maintainers=["oli"], at=AT, log=lambda *a: None)
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(bad))
    probs = R.add(root, [f], at=AT, log=lambda *a: None, author="alice")
    assert probs and "refused" in probs[0] and not list((root / "parts").glob("*/*.json")), probs


@lite
def test_2a_pin_types_are_compared(monkeypatch):
    """p11_hidepin.py type: every pin relabelled no_connect is not "the same pins"."""
    _no_dns(monkeypatch)
    rec = _tps()
    nc = copy.deepcopy(rec)
    nc["symbol"]["kicad_sym"] = re.sub(r"\(pin (\w+) ", "(pin no_connect ", rec["symbol"]["kicad_sym"])
    SR.rehash(nc)
    assert SR.validate(nc) == [] and SR.pins_hash(nc) != SR.pins_hash(rec)
    try:
        SR.merge(rec, nc)
        raise AssertionError("merged")
    except SR.Conflict as e:
        assert "pins" in str(e)


# ================================================================ 2b: text_hash
@lite
def test_2b_text_hash_is_the_whole_drawing_and_only_it_confirms(tmp_path, monkeypatch):
    """p14_silk.py: an extra pin-1 marker on silk survived a second honest build's "confirmation"."""
    _no_dns(monkeypatch)
    rec = _tps()
    km = rec["footprint"]["kicad_mod"]
    # white space, quoting and item order do not change it; anything drawn does
    t = sexpr.parse(km, keep_quotes=True)
    shuffled = copy.deepcopy(rec)
    shuffled["footprint"]["kicad_mod"] = sexpr.dumps(t[:2] + list(reversed(t[2:]))).replace("\t", "  ") + "\n"
    assert SR.text_hash(shuffled) == SR.text_hash(rec) == rec["text_hash"] == rec["evidence"][0]["text_hash"]
    silk = copy.deepcopy(rec)
    silk["footprint"]["kicad_mod"] = _inject(km, '\n  (fp_circle (center 4.0 2.7) (end 4.2 2.7) (layer "F.SilkS") '
                                                 '(stroke (width 0.2) (type solid)) (fill solid))')
    assert FS.digest(silk["footprint"]["kicad_mod"]) == rec["footprint"]["hash"]
    assert SR.text_hash(silk) != rec["text_hash"]
    assert any("text_hash does not match" in p for p in SR.validate(silk))
    silk["evidence"][0]["by"] = "mallory"
    SR.rehash(silk)
    assert SR.validate(silk) == [] and SR.level(silk) == "checked"
    both = SR.merge(silk, rec)                       # bob's honest build: same pads and pins, another drawing
    assert len(both["evidence"]) == 2 and SR.level(both) == "checked"
    assert SR.level(SR.merge(rec, silk)) == "checked"
    assert SR.level(SR.merge(rec, dict(copy.deepcopy(rec), evidence=[dict(rec["evidence"][0], by="bob")]))) == \
        "confirmed"
    sign = copy.deepcopy(silk)                        # a sign-off of another drawing confirms nothing either
    sign["evidence"].append(dict(rec["evidence"][0], kind="signoff", by="oli"))
    assert SR.level(sign) == "checked"


@lite
def test_2b_store_repo_merges_another_drawing_but_never_confirms_it(tmp_path, monkeypatch):
    """p9_repo.py with silk: mallory's drawing, then bob's honest build: served "checked", and add says why."""
    from ecadlib import store_repo as R
    _no_dns(monkeypatch)
    honest = _tps()
    honest["evidence"][0]["by"] = "bob"
    bad = copy.deepcopy(honest)
    bad["footprint"]["kicad_mod"] = _inject(honest["footprint"]["kicad_mod"], '\n  (fp_circle (center 4.0 2.7) '
                                            '(end 4.2 2.7) (layer "F.SilkS") (stroke (width 0.2) (type solid)) '
                                            '(fill solid))')
    bad["evidence"][0]["by"] = "mallory"
    SR.rehash(bad)
    root = tmp_path / "s"
    R.init(root, name="t", maintainers=["oli"], at=AT, log=lambda *a: None)
    for who, rec in (("mallory", bad), ("bob", honest)):
        f = tmp_path / f"{who}.json"
        f.write_text(json.dumps(rec))
        out = []
        assert R.add(root, [f], at=AT, log=out.append, author=who) == []
    assert any("does not confirm" in o for o in out), out
    h = SC.Store(str(root)).lookup("ACME", "TPS1234DSGR")
    assert h.level == "checked" and len(h.record["evidence"]) == 2
    # a contributor's entry must attest the drawing it came with
    lie = copy.deepcopy(honest)
    lie["evidence"][0].update(by="carol", text_hash=bad["text_hash"])
    f = tmp_path / "lie.json"
    f.write_text(json.dumps(lie))
    probs = R.add(root, [f], at=AT, log=lambda *a: None, author="carol")
    assert probs and "text_hash" in probs[0], probs


# ================================================================ 4: KiCad-library parts are shareable
@lite
def test_4_every_golden_kicad_footprint_cleans_into_a_valid_record(monkeypatch):
    """p7_golden.py / p8b.py: 22 of 26 were refused (KiLib_Generator, (point ...)); now each cleans into a record
    validate takes, with the pads unchanged. Any refusal is listed with its reason."""
    _no_dns(monkeypatch)
    rec = _tps()
    refused = {}
    files = sorted(glob.glob(str(FIX / "kicad_golden" / "*.kicad_mod")) + glob.glob(str(FIX / "kicad" / "*.kicad_mod"))
                   + glob.glob(str(FIX / "kicad_mech" / "*.kicad_mod")))
    assert len(files) >= 24
    for f in files:
        raw, name = Path(f).read_text(encoding="utf-8"), Path(f).stem
        r = copy.deepcopy(rec)
        r["part"]["package"] = name
        r["footprint"]["kicad_mod"], _ = SR.clean_footprint(raw, name)
        assert FS.digest(r["footprint"]["kicad_mod"]) == FS.digest(raw), name
        r["pin_map"] = {}
        SR.rehash(r)
        probs = [p for p in SR.validate(r) if not p.startswith("pin_map")]
        if probs:
            refused[name] = probs
    # a fiducial and a mounting hole are off the BOM (exclude_from_bom): no maker part, never shared (shareable refuses
    # them first); every other KiCad footprint here is shareable
    assert set(refused) == {"Fiducial_1mm_Mask2mm", "MountingHole_3.2mm_M3"}, refused
    assert all(len(v) == 1 and "attr exclude_from_bom" in v[0] for v in refused.values()), refused


def _sym_record(rec, f, name):
    chain, _ = K.load_symbol(f"{f}:{name}", None)
    sym = K.convert_symbol(chain, name, None, (), None, [])
    d = Path(tempfile.mkdtemp(prefix="ecadlib-sym-"))
    AL.write_schlib(d / "x.SchLib", [sym])
    tree = SR._clean_symbol(SR.schlib_symbol(d / "x.SchLib", KX.ksym(name), None, None, f"kicad:X:{name}"),
                            KX.ksym(name), rec["part"]["datasheet"]["url"], rec["part"]["description"])
    r = copy.deepcopy(rec)
    r["symbol"]["kicad_sym"] = sexpr.dumps(["kicad_symbol_lib", ["version", KX.SYM_VERSION], ["generator",
                                                                                              Q("ecadlib")], tree]) + "\n"
    r["pin_map"] = {}
    SR.rehash(r)
    return [p for p in SR.validate(r) if not p.startswith("pin_map")]


@lite
def test_4_kicad_symbols_offline_validate_or_are_refused_for_a_reason(monkeypatch):
    """The symbols available offline, the way from_item takes them (the released .SchLib read back): an `extends`
    child and a De Morgan symbol validate; one with hidden power pins is refused, naming them."""
    _no_dns(monkeypatch)
    rec = _tps()
    got = {}
    for f in sorted(glob.glob(str(FIX / "kicad" / "*.kicad_sym"))):
        for s in sexpr.find(sexpr.parse(Path(f).read_text(encoding="utf-8")), "symbol"):
            got[str(s[1])] = _sym_record(rec, f, str(s[1]))
    assert got["AP1117-15"] == [] and got["AMS1117-3.3"] == [] and got["PIN_SHAPES"] == [], got
    assert got["NAND2_DM"] and all("is hidden" in p for p in got["NAND2_DM"]), got["NAND2_DM"]


@lite
def test_4_from_item_never_returns_a_record_validate_refuses(monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    real = SR.validate
    monkeypatch.setattr(SR, "validate", lambda r, resolve=False: real(r, resolve) + ["planted problem"])
    try:
        SR.from_item(items["TPS1234DSGR"], base, by="alice", at=AT)
        raise AssertionError("no NotShareable")
    except SR.NotShareable as e:
        assert any("planted problem" in r for r in e.reasons), e.reasons


@lite
def test_4_ecadlib_own_parts_validate(tmp_path, monkeypatch):
    """p6_legit.py: every part an offline standard-table run releases is shared with a valid record."""
    _no_dns(monkeypatch)
    (tmp_path / "bom.csv").write_text(
        "Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,Description,Datasheet\n"
        "R1,1,10k,0603,Yageo,RC0603FR-0710KL,RES 10k 1% 0603,https://www.yageo.com/r.pdf\n"
        "C1,1,100nF,0402,Murata,GRM155R71C104KA88D,CAP 100nF 0402,https://www.murata.com/c.pdf\n"
        "D1,1,1N4148W,SOD-123,Diodes Incorporated,1N4148W-7-F,Diode,https://www.diodes.com/d.pdf\n"
        "D2,1,RED,LED_0805_2012Metric,Lite-On,LTST-C171KRKT,LED red 0805,https://optoelectronics.liteon.com/x.pdf\n",
        encoding="utf-8")
    items = L.run(tmp_path / "bom.csv", out=tmp_path / "out", sources=["standard"], offline=True,
                  cache=tmp_path / "cache", rules={}, log=lambda *a: None)
    ready = [i for i in items if i["status"] == "ready"]
    assert len(ready) >= 3
    for it in ready:
        rec = SR.from_item(it, tmp_path / "out", by="alice", at=AT)
        assert SR.validate(rec) == [] and rec["evidence"][0]["text_hash"] == rec["text_hash"]


# ================================================================ 3: parts the store cannot vouch for
def _built(tmp_path, name="TPS1234DSGR"):
    """A workspace whose TPS1234DSGR was built from a confirmed store record: (folder, store root, record)."""
    items, base = _kicad()
    rec = _confirm(_record(items[name], base))
    root = _store(tmp_path / "s", [rec])
    d = tmp_path / "ws"
    got = _run(d, bom=_bom_of(name), http=TL.FakeHTTP(), sources=["store"], store=SC.Store(str(root)))[0][name]
    assert got["status"] == "ready" and got["source"]["footprint"].startswith("store:"), got["hold_reasons"]
    return d, root, rec


def _again(d, name="TPS1234DSGR", store=None, **kw):
    return _run(d, bom=_bom_of(name), http=TL.FakeHTTP(), sources=["kicad"], store=store, **kw)[0][name]


def _plan_item(d, name="TPS1234DSGR"):
    return next(i for i in json.loads((d / "out" / "plan.json").read_text()) if i["name"] == name)


@lite
def test_3_store_switched_off_holds_what_came_from_it_and_allow_keeps_it(tmp_path, monkeypatch):
    """p3_publish.py: url empty and accept "off" let a store part through every publisher."""
    _no_dns(monkeypatch)
    d, root, rec = _built(tmp_path)
    item, out = _plan_item(d), d / "out"
    for rules in ({}, {"store": {"url": str(root), "accept": "off"}}):
        probs = RC.publish_problems(item, out, RU.load(rules))
        assert any(SC.OFF in p for p in probs), (rules, probs)
    assert RC.publish_problems(item, out, RU.load({"store": {"url": str(root)}})) == []
    got = _again(d)                                       # the librarian, store off (no [store] url)
    assert got["status"] == "held" and any(SC.OFF in h for h in got["hold_reasons"]), got["hold_reasons"]
    rep = UK.run(out / "plan.json", state_dir=tmp_path / "u", out=tmp_path / "u" / "r", ctx=UK.Ctx(env={}, offline=True),
                 now=NOW, rules={})
    sec = [c for c in rep["checks"] if c["check"] == "store"][0]
    assert sec["changes"] and sec["changes"][0]["kind"] == "store: cannot vouch", sec
    got = _again(d, allow=["TPS1234DSGR"])               # a person releases it
    assert got["status"] == "ready" and got["release_check"]["result"] == "allowed", got["hold_reasons"]
    for _ in range(2):                                    # and it is not held again on the next runs
        got = _again(d)
        assert got["status"] == "ready", got["hold_reasons"]
    assert RC.publish_problems(_plan_item(d), out, RU.load({})) == []
    # the store switched on again: the store's notes go
    got = _again(d, store=SC.Store(str(root)))
    assert not any(SC.OFF in v for v in got["verify"]), got["verify"]
    st = json.loads((out / "librarian_state.json").read_text())["TPS1234DSGR"]
    assert "store_unchecked" not in st


@lite
def test_3_store_off_changes_nothing_for_parts_that_owe_it_nothing(tmp_path, monkeypatch):
    _no_dns(monkeypatch)
    items, base = _kicad()
    it = items["RES-0603-10k"]
    assert SC.depends(it) == [] and SC.problems(it, None) == []
    assert not any("store" in p for p in RC.publish_problems(it, base, RU.load({})))
    st = {"name": "X", "verify": ["a"], "source": {"symbol": "kicad:A:B", "footprint": "kicad:A:C"}}
    before = copy.deepcopy(st)
    L.store_holds(st, None, "X")
    assert st == before
    rep = UK.run(base / "plan.json", state_dir=tmp_path / "u", out=tmp_path / "u" / "r",
                 ctx=UK.Ctx(env={}, offline=True), now=NOW, rules={})
    assert "store" not in [c["check"] for c in rep["checks"]]


@lite
def test_3_unreachable_store_holds_unless_a_recent_withdrawn_list_is_saved(tmp_path, monkeypatch):
    """p2_revoke.py (withdrawn + store unreachable: was released) and p3_publish.py (a stale cache taken for "not
    withdrawn"): each state of the reviewer's table."""
    _no_dns(monkeypatch)
    d, root, rec = _built(tmp_path)
    item, out = _plan_item(d), d / "out"
    kid = SK.key_id(rec["key"]["key"])
    # a folder store that is not there: nothing saved, held
    gone = SC.Store(str(root) + "_missing")
    probs = SC.problems(item, gone)
    assert probs and probs[0][0].get("unchecked") and "withdrawals could not be checked" in probs[0][1], probs
    got = _again(d, store=SC.Store(str(root) + "_missing"))
    assert got["status"] == "held" and any("withdrawals could not be checked" in h for h in got["hold_reasons"])
    got = _again(d, store=SC.Store(str(root)))           # reachable again: released again
    assert got["status"] == "ready", got["hold_reasons"]
    # an HTTP store: a copy saved 10 days ago, then the store goes down
    now = [time.time() - 10 * 86400]
    cache = tmp_path / "cache"
    up = SC.Store(BASE, http=StoreHTTP(root), cache_dir=cache, now=lambda: now[0])
    assert up.lookup("ACME", "TPS1234DSGR") is not None
    now[0] = time.time()
    down = lambda **kw: SC.Store(BASE, http=StoreHTTP(None, fail="error"), cache_dir=cache, now=lambda: now[0], **kw)
    st = down()
    assert SC.problems(item, st) == []
    assert any("checked against the list saved on this computer 10 day" in t for t in st.trouble), st.trouble
    st = down(offline=True)
    assert SC.problems(item, st) == []
    # the saved list holds a withdrawal: it counts
    wd = [{"key_id": kid, "rev": 1, "reason": "bad pin"}]
    f = next((cache / "store").glob("*/live/withdrawn.json"))
    f.write_text(json.dumps({"at": now[0] - 10 * 86400, "text": json.dumps(wd)}))
    probs = SC.problems(item, down())
    assert probs and "withdrawn from the store (bad pin)" in probs[0][1] and not probs[0][0].get("unchecked")
    # older than HOLD_MAX_AGE_S: held, never "not withdrawn"
    f.write_text(json.dumps({"at": now[0] - SC.HOLD_MAX_AGE_S - 86400, "text": "[]"}))
    for st in (down(), down(offline=True)):
        probs = SC.problems(item, st)
        assert probs and "could not be checked for more than 30 days" in probs[0][1], probs
        assert st.withdrawn_age > SC.HOLD_MAX_AGE_S
    probs = RC.publish_problems(item, out, store=down())
    assert any("could not be checked for more than 30 days" in p for p in probs), probs


# ================================================================ 5: a broken store file holds, never crashes
@lite
def test_5_deeply_nested_json_never_crashes(tmp_path, monkeypatch):
    """p13_deep.py: withdrawn.json of 200 000 nested lists raised RecursionError out of problems()."""
    _no_dns(monkeypatch)
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    (root / "withdrawn.json").write_text("[" * 200000 + "]" * 200000)
    kid = SK.key_id(SK.key("TI", "NE555DR"))
    item = {"name": "x", "status": "ready", "source": {"symbol": f"store:{kid}@r1", "footprint": f"store:{kid}@r1"},
            "store": {"key": SK.key("TI", "NE555DR")}}
    assert SC.Store(str(root)).lookup("TI", "NE555DR") is None
    probs = SC.problems(item, SC.Store(str(root)))
    assert probs and probs[0][0].get("unchecked"), probs
    st = SC.Store(str(root))
    monkeypatch.setattr(st, "withdrawals", lambda: 1 / 0)
    probs = SC.problems(item, st)
    assert probs and "ZeroDivisionError" in probs[0][1], probs
    assert any("withdrawals could not be checked" in p for p in RC.store_problems(item, st))
    state = dict(item, verify=[])
    L.store_holds(state, st, "x")
    assert state["verify"] and state["store_unchecked"]


# ================================================================ 6: minor findings
def test_6_safe_and_validate_take_out_bidi_and_zero_width():
    assert SC.safe("a‮b​c⁦d\x1b[31m") == "abcd[31m"


@lite
def test_6_trusted_list_counts_the_maintainers(tmp_path):
    root = make_store(tmp_path / "s", meta={"trusted": ["alice"], "maintainers": ["oli"]})
    st = SC.Store(str(root))
    assert st.usable() and st.trusted() == ["alice", "oli"]
    root = make_store(tmp_path / "t", meta={"trusted": None, "maintainers": ["oli"]})
    st = SC.Store(str(root))
    assert st.usable() and st.trusted() is None


class _Redirect(StoreHTTP):
    def get(self, url, **kw):
        self.urls.append(url)
        return Response(302, "", headers={"Location": "http://evil.example.com/x"})


class _Resp:
    def __init__(self, status, body=b"", location=None):
        self.status_code, self.body, self.headers = status, body, ({"Location": location} if location else {})

    def iter_content(self, n):
        yield self.body

    def close(self):
        pass


class _Session:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return self.answers[url]


@lite
def test_6_store_fetches_never_follow_a_redirect(tmp_path):
    st = SC.Store(BASE, http=_Redirect(None), cache_dir=tmp_path / "c")
    assert st.lookup("TI", "NE555DR") is None and any("redirect" in t for t in st.trouble), st.trouble
    http = CachedHTTP(cache_dir=tmp_path / "h", min_interval=0)
    http.s = _Session({f"{BASE}/store.json": _Resp(301, location="https://evil.example.com/store.json")})
    st = SC.Store(BASE, http=http, cache_dir=tmp_path / "c2")
    assert st.lookup("TI", "NE555DR") is None
    assert http.s.calls and all(kw.get("allow_redirects") is False for _, kw in http.s.calls), http.s.calls


@lite
def test_6_a_snapshot_follows_only_githubs_release_redirect(tmp_path):
    root = make_store(tmp_path / "s", [("TI", "NE555DR", {})])
    from tests.test_store_client import zip_of
    data = zip_of(root)
    snap = SC.default_snapshot_url(BASE)
    asset = "https://objects.githubusercontent.com/github-production-release-asset/1/snapshot.zip"
    for target, ok in ((asset, True), ("http://objects.githubusercontent.com/x.zip", False),
                       ("https://evil.example.com/x.zip", False)):
        http = CachedHTTP(cache_dir=tmp_path / "h", min_interval=0)
        http.s = _Session({snap: _Resp(302, location=target), target: _Resp(200, data)})
        st = SC.Store(BASE, http=http, cache_dir=tmp_path / f"c{len(target)}{ok}", mode="snapshot")
        hit = st.lookup("TI", "NE555DR")
        assert (hit is not None) == ok, (target, st.trouble)
        assert all(kw.get("allow_redirects") is False for _, kw in http.s.calls)
        if not ok:
            assert any("redirected to" in t for t in st.trouble), st.trouble


def test_6_the_default_rules_hash_does_not_see_the_store_section():
    """Replaces test_a2's git comparison (skipped in CI's shallow checkout): the [store] defaults change nothing."""
    rules = RU.load({})
    assert "store" in rules and not str(rules["store"].get("url") or "").strip()
    bare = {k: v for k, v in rules.items() if k != "store"}
    assert LK.rules_hash(rules) == LK.rules_hash(bare)
    eff = RU.effective(rules)
    hashed = {k: v for k, v in eff.items() if k != "store"}
    assert LK.rules_hash(rules) == LK.sha(json.dumps(hashed, sort_keys=True, default=str))
    on = copy.deepcopy(rules)
    on["store"]["url"] = "https://raw.githubusercontent.com/o/r/main"
    assert LK.rules_hash(on) != LK.rules_hash(rules)


@lite
def test_6_a_bug_while_checking_the_store_holds_with_the_error_name(tmp_path, monkeypatch):
    """problems() never raises: anything that goes wrong while checking holds the part and names the error."""
    _no_dns(monkeypatch)
    d, root, rec = _built(tmp_path)
    item = _plan_item(d)

    def boom(*a, **k):
        raise KeyError("x")
    monkeypatch.setattr(SC, "_problems", boom)
    probs = SC.problems(item, SC.Store(str(root)))
    assert probs and all(p[0].get("unchecked") for p in probs), probs
    assert "withdrawals could not be checked (KeyError)" in probs[0][1], probs
