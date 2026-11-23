"""Shared store: the record format (ecadlib/store_record.py, docs/shared-store-plan.md, schema 1).

Real released items, built offline: the KiCad-library parts of tests/test_librarian's fake KiCad library (R_0603,
SOIC-8 regulator) and the standard-table parts (chip R / C) of an offline `standard` run. Nothing here goes to the
network: the datasheet-link test's host lookup (datasheet._resolve) is stubbed out."""
import copy, json
from pathlib import Path

from ecadlib import datasheet as D, fp_share as FS, librarian as L, release_check as RC, store_keys as SK, \
    store_record as SR
from tests import test_librarian as TL
from tests.fixtures_lite import lite, scratch

QUIET = lambda *a, **k: None
AT = "2026-11-23T10:00:00Z"
_LIBS = {}


def _kicad():
    """The fake-KiCad-library run of tests/test_librarian, once per process: (items by name, plan folder)."""
    if "kicad" not in _LIBS:
        d = scratch("ecadlib-store-kicad-", keep=True)
        items, _ = TL.run(d)
        _LIBS["kicad"] = items, Path(d) / "out"
    items, out = _LIBS["kicad"]
    return copy.deepcopy(items), out


def _standard():
    """An offline run on the standard tables (chip R and C), once per process: (items by name, plan folder)."""
    if "standard" not in _LIBS:
        d = scratch("ecadlib-store-std-", keep=True)
        (d / "bom.csv").write_text(
            "Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,Description,Datasheet\n"
            "R1,1,10k,0603,Yageo,RC0603FR-0710KL,RES 10k 1% 0603,https://www.yageo.com/upload/media/product/RC.pdf\n"
            "C1,1,100nF,0402,Murata,GRM155R71C104KA88D,CAP 100nF 16V 0402,\n", encoding="utf-8")
        items = L.run(d / "bom.csv", out=d / "out", sources=["standard"], offline=True, cache=d / "cache", rules={},
                      log=QUIET)
        _LIBS["standard"] = {i["name"]: i for i in items}, d / "out"
    items, out = _LIBS["standard"]
    return copy.deepcopy(items), out


def _offline(monkeypatch):
    monkeypatch.setattr(D, "_resolve", lambda host: [])        # no DNS lookup for the public-link test


def _par(item, name, value):
    item["parameters"] = [p for p in item["parameters"] if p["name"] != name] + [{"name": name, "value": value}]


def _round_trip(item, base, tmp_path, by="alice"):
    assert item["status"] == "ready", item.get("hold_reasons")
    assert SR.shareable(item, base) == [], SR.shareable(item, base)
    rec = SR.from_item(item, base, by=by, at=AT)
    assert SR.validate(rec) == [], SR.validate(rec)
    out = Path(tmp_path) / "rebuilt"
    got = SR.build(rec, out)
    assert got["source"]["symbol"] == got["source"]["footprint"] == f"store:{SK.key_id(rec['key']['key'])}@r1"
    fails, _, _ = RC.check_item(got, out)
    assert fails == [], fails
    kmod = (out / got["footprint"]["kicad_mod"]).read_text(encoding="utf-8")
    assert FS.digest(kmod) == rec["footprint"]["hash"] == FS.digest((Path(base) / item["footprint"]["kicad_mod"])
                                                                    .read_text(encoding="utf-8"))
    sym = L.AL.read_schlib(out / got["symbol"]["file"])[1][0]
    pins = {(r["num"], r.get("name", "")) for r in sym["records"] if r.get("RECORD") == "2"}
    orig = L.AL.read_schlib(Path(base) / item["symbol"]["file"])[1][0]
    assert pins == {(r["num"], r.get("name", "")) for r in orig["records"] if r.get("RECORD") == "2"}
    assert SR.pins_hash(rec) == rec["evidence"][0]["pins_hash"]
    return rec, got


# ---------------------------------------------------------------- (a) round trip
@lite
def test_round_trip_kicad_library_parts(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    for name in ("RES-0603-10k", "TPS1234DSGR"):
        rec, got = _round_trip(items[name], base, Path(tmp_path) / name)
        assert rec["symbol"]["license"] == rec["footprint"]["license"] == "CC-BY-SA-4.0"
        assert rec["symbol"]["credit"] == L.K.KICAD_CREDIT
        assert {"name": "Library Credit", "value": L.K.KICAD_CREDIT} in got["parameters"]
        assert rec["key"] == {"maker": "ACME", "mpn": items[name]["parameters"][2]["value"],
                              "key": SK.key("ACME", items[name]["parameters"][2]["value"])}
        ev = rec["evidence"][0]
        assert ev["kind"] == "build" and ev["by"] == "alice" and ev["at"] == AT and ev["reader"] is None
        assert ev["footprint_hash"] == rec["footprint"]["hash"] and "pins" in ev["checks"]
    assert SR.height(rec) == rec["body"]["height_mm"] > 0
    assert len(SR.symbol_chain(rec)) == 1 and SR.footprint_text(rec).startswith("(footprint")


@lite
def test_round_trip_standard_table_parts(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _standard()
    for name in ("RES-0603-10k", "CAP-0402-100nF"):
        rec, got = _round_trip(items[name], base, Path(tmp_path) / name)
        assert rec["footprint"]["license"] == rec["symbol"]["license"] == "MIT" and not rec["footprint"]["credit"]
        assert rec["footprint"]["source"].startswith("standard:") and not any(
            p["name"] == "Library Credit" for p in got["parameters"])


@lite
def test_round_trip_again_from_the_rebuilt_part_agrees(tmp_path, monkeypatch):
    """A second build of the same part from the record gives the same hashes: the record agrees with itself."""
    _offline(monkeypatch)
    items, base = _kicad()
    rec, got = _round_trip(items["TPS1234DSGR"], base, tmp_path)
    assert SR.shareable(dict(got, status="ready"), Path(tmp_path) / "rebuilt")          # a store part: not shared again
    src = {h: got["store"][h] for h in ("symbol", "footprint")}                         # as if built from its source
    again = SR.from_item(dict(got, status="ready", source=src), Path(tmp_path) / "rebuilt", by="bob", at=AT)
    assert SR.agree(rec, again) and SR.pins_hash(again) == SR.pins_hash(rec)


@lite
def test_round_trip_datasheet_read_part(tmp_path, monkeypatch):
    """The store's main value: a part read from its datasheet (reader mocked, as tests/test_librarian), with the read's
    hold settled. The record names the reader (from library.lock.json) and rebuilds into a part the check releases."""
    _offline(monkeypatch)
    from unittest import mock
    from tests import test_datasheet as TD
    ext = copy.deepcopy(TD.GOOD)
    d = Path(tmp_path) / "lib"
    d.mkdir()
    with mock.patch.object(D, "call", lambda headers, body, key, timeout=600: (ext, TD.message(ext))):
        items, _ = TL.run(d, api_key="sk-test")
    out, it = d / "out", items["DSPART1"]
    assert it["status"] == "held" and it["source"]["symbol"] == "datasheet:https://example.com/ds.pdf"
    assert SR.shareable(it, out)                          # held for the read's check: not shared
    state = json.loads((out / "librarian_state.json").read_text(encoding="utf-8"))
    state["DSPART1"]["verify"], it["verify"] = [], []     # as when a second read agreed (crosscheck)
    (out / "librarian_state.json").write_text(json.dumps(state), encoding="utf-8")
    RC.apply([it], out)
    rec, _ = _round_trip(it, out, Path(tmp_path) / "rb")
    assert rec["symbol"]["source"] == "datasheet" and rec["symbol"]["license"] == "MIT"
    assert rec["evidence"][0]["reader"] == {"provider": "anthropic", "model": "claude-opus-5-5"}
    assert rec["part"]["datasheet"]["url"] == "https://example.com/ds.pdf"
    assert "extraction" not in SK.dumps(rec) and "sk-test" not in SK.dumps(rec)


@lite
def test_3d_model_is_a_pointer_never_data(tmp_path, monkeypatch):
    """A KiCad footprint's (model ...) and KiCad 9's embedded files are taken out of the record; body.model points to
    the model in kicad-packages3D. The pads, and so the hash, are unchanged."""
    _offline(monkeypatch)
    import shutil
    items, base = _kicad()
    lib = Path(tmp_path) / "lib"
    shutil.copytree(base, lib)
    it = items["RES-0603-10k"]
    f = lib / it["footprint"]["kicad_mod"]
    before = f.read_text(encoding="utf-8")
    f.write_text(before.rstrip().rstrip(")") + '  (embedded_files (file (name "R.step") (type model) (data "' +
                 "QUFB" * 80 + '")))\n  (model "${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603_1608Metric.wrl"\n'
                 "    (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))\n)\n", encoding="utf-8")
    rec = SR.from_item(it, lib, at=AT)
    assert SR.validate(rec) == [], SR.validate(rec)
    text = SK.dumps(rec)
    assert "QUFB" not in text and "(model" not in text and "embedded_files" not in text
    assert rec["footprint"]["hash"] == FS.digest(before)
    assert rec["body"]["model"] == {"source": "kicad", "url": L.K.GITLAB.format(
        repo="kicad-packages3D", path="Resistor_SMD.3dshapes/R_0603_1608Metric.step")}


# ---------------------------------------------------------------- (b) what may not be shared
@lite
def test_refused_sources(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    for src in ("local:MyLib:R_0603", "snapmagic:12345", "samacsys:R0603", "ultralibrarian:R0603", "spec:R0603",
                "store:abc@r1", "unknown", None, "", "kicadx:Lib:R", "Datasheet:x"):
        for half in ("symbol", "footprint"):
            it = copy.deepcopy(items["RES-0603-10k"])
            it["source"][half] = src
            why = SR.shareable(it, base)
            assert any(w.startswith(f"{half} source") for w in why), (src, half, why)
            try:
                SR.from_item(it, base, at=AT)
                raise AssertionError("shared a refused source")
            except SR.NotShareable as e:
                assert e.reasons == why


@lite
def test_part_chosen_by_a_person_is_refused(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    it = items["RES-0603-10k"]
    it["librarian"]["tried"].append("resolve: footprint kicad:Resistor_SMD:R_0603_1608Metric chosen by user")
    assert any("chosen by a person" in w for w in SR.shareable(it, base))
    it = copy.deepcopy(_kicad()[0]["TPS1234DSGR"])
    it["symbol"]["pin_map"] = {"1": "2", "2": "1"}
    assert any("chosen by a person" in w for w in SR.shareable(it, base))


@lite
def test_override_hold_and_status_refused(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    it = copy.deepcopy(items["RES-0603-10k"])
    it["allow"] = {"by": "--allow", "date": "2026-11-01", "failures": []}
    assert any("--allow" in w for w in SR.shareable(it, base))
    it = copy.deepcopy(items["RES-0603-10k"])
    it["verify"] = ["symbol Device:R matched by name: check it"]
    assert any(w.startswith("verify") for w in SR.shareable(it, base))
    it = copy.deepcopy(items["RES-0603-10k"])
    it["status"], it["hold_reasons"] = "held", ["fields: Description is empty"]
    why = SR.shareable(it, base)
    assert any("not ready" in w for w in why) and any("Description" in w for w in why), why
    held = items["EEPART1"]                               # the librarian held it (an EasyEDA conversion to check)
    assert held["status"] == "held" and SR.shareable(held, base)
    it = copy.deepcopy(items["RES-0603-10k"])             # the release check, run again now, finds a problem
    it["description"] = ""
    assert any("Description is empty" in w for w in SR.shareable(it, base))
    it = copy.deepcopy(items["RES-0603-10k"])
    it["parameters"] = [p for p in it["parameters"] if p["name"] != "Manufacturer"]
    assert any("Manufacturer" in w for w in SR.shareable(it, base))


@lite
def test_easyeda_part_is_a_pointer_only(tmp_path, monkeypatch):
    """An EasyEDA conversion is shared as a pointer: the LCSC number, the pad and pin hashes, the evidence; never the
    drawing. It cannot be built from the record alone."""
    _offline(monkeypatch)
    items, base = _kicad()
    it = items["EEPART1"]                                 # as if a person had settled the conversion's check
    it["status"], it["verify"], it["hold_reasons"] = "ready", [], []
    state = json.loads((base / "librarian_state.json").read_text(encoding="utf-8"))
    if state.get("EEPART1", {}).get("verify"):
        d = Path(tmp_path) / "lib"
        import shutil
        shutil.copytree(base, d)
        state["EEPART1"]["verify"] = []
        (d / "librarian_state.json").write_text(json.dumps(state), encoding="utf-8")
        base = d
    it["librarian"]["review"] = [r for r in it["librarian"]["review"] if "EasyEDA" not in r]
    assert SR.shareable(it, base) == [], SR.shareable(it, base)
    rec = SR.from_item(it, base, by="alice", at=AT)
    assert SR.validate(rec) == [], SR.validate(rec)
    fp, sym = rec["footprint"], rec["symbol"]
    assert fp["kicad_mod"] is None and fp["pointer"] == {"lcsc": "C2002"} and fp["hash"]
    assert sym["kicad_sym"] is None and sym["pointer"] == {"lcsc": "C2002"} and sym["pins"]
    assert fp["source"] == sym["source"] == "easyeda:C2002" and fp["credit"] == L.EC.CREDIT
    text = SK.dumps(rec)
    assert "(pad" not in text and "(symbol" not in text
    try:
        SR.build(rec, Path(tmp_path) / "x")
        raise AssertionError("built a pointer")
    except SR.PointerOnly:
        pass


# ---------------------------------------------------------------- (c) datasheet links
@lite
def test_private_datasheet_link_is_dropped(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    for url in ("https://docs.acme.corp/ds/r.pdf", "http://10.1.2.3/r.pdf", "https://bob:pw@www.acme.com/r.pdf",
                "https://www.acme.com/r.pdf?X-Amz-Signature=abc", "http://localhost/r.pdf"):
        it = copy.deepcopy(items["RES-0603-10k"])
        _par(it, "ComponentLink2URL", url)
        assert SR.shareable(it, base) == [], (url, SR.shareable(it, base))
        rec = SR.from_item(it, base, at=AT)
        assert rec["part"]["datasheet"] is None and url not in SK.dumps(rec), url
        assert SR.validate(rec) == []
    import shutil                                         # the private link inside the drawing's text: taken out
    lib = Path(tmp_path) / "lib"
    shutil.copytree(base, lib)
    it = copy.deepcopy(items["RES-0603-10k"])
    _par(it, "ComponentLink2URL", "https://docs.acme.corp/ds/r.pdf")
    f = lib / it["footprint"]["kicad_mod"]
    f.write_text(f.read_text(encoding="utf-8").replace("(descr \"", "(descr \"see https://docs.acme.corp/ds/r.pdf ", 1),
                 encoding="utf-8")
    assert "docs.acme.corp" in f.read_text(encoding="utf-8")
    assert "docs.acme.corp" not in SK.dumps(SR.from_item(it, lib, at=AT))
    it = copy.deepcopy(items["RES-0603-10k"])
    rec = SR.from_item(it, base, at=AT)
    assert rec["part"]["datasheet"]["url"] == "https://example.com/r.pdf"
    bad = copy.deepcopy(rec)
    bad["part"]["datasheet"]["url"] = "https://docs.acme.corp/ds/r.pdf"
    assert any("datasheet" in p for p in SR.validate(bad))


@lite
def test_datasheet_read_needs_a_public_link(tmp_path, monkeypatch):
    """A part read from a datasheet with a private link has no provenance anyone else can check: refused."""
    _offline(monkeypatch)
    items, base = _kicad()
    it = items["TPS1234DSGR"]
    it["source"]["symbol"] = "datasheet:https://docs.acme.corp/tps.pdf"
    why = SR.shareable(it, base)
    assert any("datasheet" in w and "public" in w for w in why), why
    it["source"]["symbol"] = "datasheet:https://www.acme.com/tps.pdf"
    _par(it, "ComponentLink2URL", "https://docs.acme.corp/tps.pdf")
    assert not any("public" in w for w in SR.shareable(it, base)), SR.shareable(it, base)
    lock = {"parts": {"TPS1234DSGR": {"datasheet": {"url": "https://www.acme.com/tps.pdf", "pdf_sha256": "a" * 64,
                                                    "provider": "anthropic", "model": "claude-x"}}}}
    if not SR.shareable(it, base):
        rec = SR.from_item(it, base, at=AT, lock=lock)
        assert rec["part"]["datasheet"] == {"url": "https://www.acme.com/tps.pdf", "sha256": "a" * 64, "revision": None}
        assert rec["evidence"][0]["reader"] == {"provider": "anthropic", "model": "claude-x"}
        assert rec["symbol"]["source"] == "datasheet" and rec["symbol"]["license"] == "MIT"


# ---------------------------------------------------------------- (d) nothing of the user's
@lite
def test_no_user_fields_leak(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    it = items["RES-0603-10k"]
    for k, v in (("Internal PN", "ACME-INT-77821"), ("Supplier Part Number 1", "C1001"), ("Unit Price", "0.0371"),
                 ("Stock", "88123"), ("Project", "SecretRover")):
        _par(it, k, v)
    it["symbol"]["by"] = it["footprint"]["by"] = "Jane Q. Secret"
    it["approval"] = {"by": "jane.secret@acme-corp.example", "date": "2026-11-01"}
    it["librarian"]["notes"].append("from C:/Users/jsecret/projects/rover/lib")
    rec = SR.from_item(it, base, by="contributor", at=AT)
    text = SK.dumps(rec)
    for leak in ("ACME-INT-77821", "C1001", "0.0371", "88123", "SecretRover", "Jane", "jsecret", "acme-corp",
                 "lcsc.com", "Supplier", "Internal", str(base), "models/", ".PcbLib"):
        assert leak not in text, leak
    assert set(rec) == SR.TOP and SR.validate(rec) == []


# ---------------------------------------------------------------- (e) validate
@lite
def test_validate_rejects_tampering(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    rec = SR.from_item(items["TPS1234DSGR"], base, by="alice", at=AT)
    assert SR.validate(rec) == []
    bad = copy.deepcopy(rec)                              # a pad moved: the hash no longer matches the drawing
    bad["footprint"]["kicad_mod"] = bad["footprint"]["kicad_mod"].replace("(at -2.4725 -1.905)", "(at -2.6 -1.905)", 1)
    assert bad["footprint"]["kicad_mod"] != rec["footprint"]["kicad_mod"]
    assert any("hash" in p for p in SR.validate(bad)), SR.validate(bad)
    bad = copy.deepcopy(rec)                              # a pin renamed: the pins hash no longer matches
    bad["symbol"]["pins"][0]["name"] = "XX"
    assert any("pins_hash" in p for p in SR.validate(bad)), SR.validate(bad)
    for where, k in ((None, "bom"), ("part", "supplier"), ("footprint", "price"), ("evidence", "email")):
        bad = copy.deepcopy(rec)
        (bad if where is None else bad[where][0] if where == "evidence" else bad[where])[k] = "x"
        assert any("unknown" in p and k in p for p in SR.validate(bad)), (k, SR.validate(bad))
    bad = copy.deepcopy(rec)                              # an embedded STEP model
    bad["footprint"]["kicad_mod"] = bad["footprint"]["kicad_mod"].rstrip().rstrip(")") + \
        '\t(model "x.step" (data "ISO-10303-21;HEADER;' + "QUFB" * 100 + '"))\n)'
    assert any("STEP" in p or "model" in p for p in SR.validate(bad)), SR.validate(bad)
    bad = copy.deepcopy(rec)
    bad["body"]["model"] = {"source": "kicad", "url": "data:model/step;base64," + "QUFB" * 200}
    assert any("base64" in p or "data:" in p for p in SR.validate(bad)), SR.validate(bad)
    bad = copy.deepcopy(rec)
    bad["part"]["description"] = "%PDF-1.7 " + "x" * 10
    assert any("PDF" in p for p in SR.validate(bad))
    bad = copy.deepcopy(rec)
    bad["part"]["description"] = "x " * 250_000
    assert any("too large" in p for p in SR.validate(bad))
    for k, v, word in (("rev", 0, "rev"), ("status", "withdrawn", "status"), ("schema", 2, "schema")):
        bad = dict(copy.deepcopy(rec), **{k: v})
        assert any(word in p for p in SR.validate(bad)), (k, SR.validate(bad))
    bad = copy.deepcopy(rec)
    bad["key"]["key"] = "ACME|OTHER"
    assert any("key" in p for p in SR.validate(bad))
    bad = copy.deepcopy(rec)
    bad["footprint"]["source"] = "local:MyLib:SOIC-8"
    assert any("source" in p for p in SR.validate(bad))
    bad = copy.deepcopy(rec)
    bad["footprint"]["license"] = "MIT"                   # a KiCad footprint is CC-BY-SA, never relicensed
    assert any("license" in p for p in SR.validate(bad))
    bad = copy.deepcopy(rec)
    bad["evidence"][0]["at"] = "yesterday"
    assert any("evidence" in p for p in SR.validate(bad))
    bad = copy.deepcopy(rec)
    del bad["symbol"]["pins"]
    assert any("pins" in p for p in SR.validate(bad))
    assert SR.validate("not a record") and SR.validate({})


# ---------------------------------------------------------------- (f) trust
def _ev(rec, by, model=None, kind="build", **kw):
    e = dict(rec["evidence"][0], kind=kind, by=by, reader={"provider": "anthropic", "model": model} if model else None)
    e.update(kw)
    return e


@lite
def test_level_agree_merge(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    rec = SR.from_item(items["TPS1234DSGR"], base, by="alice", at=AT)
    assert SR.level(rec) == "checked"
    same = SR.add_evidence(rec, _ev(rec, "alice"))           # the same contributor again: never confirms
    assert SR.level(same) == "checked" and same["rev"] == rec["rev"] and len(rec["evidence"]) == 1
    assert len(same["evidence"]) == 1                         # (an identical entry is not added twice)
    same = SR.add_evidence(rec, _ev(rec, "alice", at="2026-11-24T10:00:00Z"))
    assert len(same["evidence"]) == 2 and SR.level(same) == "checked"
    two = SR.add_evidence(rec, _ev(rec, "bob"))
    assert SR.level(two) == "confirmed" and SR.validate(two) == []
    assert SR.level(two, trusted={"alice"}) == "checked"     # bob's evidence does not count yet
    assert SR.level(two, trusted={"alice", "bob"}) == "confirmed"
    assert SR.level(SR.add_evidence(rec, _ev(rec, "carol", kind="signoff"))) == "confirmed"
    assert SR.level(SR.add_evidence(rec, _ev(rec, "ci", kind="kicad-exact"))) == "confirmed"
    # two AI models by one name confirm only for a store maintainer (a seed run), never for a contributor's own word
    a = SR.add_evidence(SR.add_evidence(rec, _ev(rec, "alice", "opus", at="2026-11-24T10:00:00Z")),
                        _ev(rec, "alice", "haiku", at="2026-11-24T11:00:00Z"))
    assert SR.level(a) == "checked" and SR.level(a, maintainers={"alice"}) == "confirmed"
    one = SR.add_evidence(rec, _ev(rec, "alice", "opus", at="2026-11-24T10:00:00Z"))
    assert SR.level(one, maintainers={"alice"}) == "checked"         # one model twice is one read
    assert SR.level(dict(two, status="disputed")) == "checked"
    try:
        SR.add_evidence(rec, _ev(rec, "bob", footprint_hash="0" * 32))
        raise AssertionError("added disagreeing evidence")
    except SR.Conflict as e:
        assert "pads" in str(e)
    # merge: the same pads and pins add evidence; other pads or pins are a conflict naming what differs
    other = SR.from_item(items["TPS1234DSGR"], base, by="bob", at="2026-11-25T08:00:00Z")
    assert SR.agree(rec, other)
    m = SR.merge(dict(rec, also=["TPS1234DSGT"]), dict(other, also=["TPS1234DSGR-Q1"]))
    assert SR.level(m) == "confirmed" and m["also"] == ["TPS1234DSGT"] and m["rev"] == rec["rev"]   # (also: the
    #                                                                   store's own; a merge never adds a contributor's)
    assert len(SR.merge(m, other)["evidence"]) == 2           # merging the same evidence again adds nothing
    moved = copy.deepcopy(other)
    moved["footprint"]["kicad_mod"] = moved["footprint"]["kicad_mod"].replace("(at -2.4725 -1.905)",
                                                                              "(at -2.6 -1.905)", 1)
    moved["footprint"]["hash"] = FS.digest(moved["footprint"]["kicad_mod"])
    assert not SR.agree(rec, moved)
    try:
        SR.merge(rec, moved)
        raise AssertionError("merged other pads")
    except SR.Conflict as e:
        assert "pads" in str(e) and "pins" not in str(e)
    swapped = copy.deepcopy(other)
    swapped["symbol"]["pins"][0]["name"], swapped["symbol"]["pins"][1]["name"] = \
        swapped["symbol"]["pins"][1]["name"], swapped["symbol"]["pins"][0]["name"]
    try:
        SR.merge(rec, swapped)
        raise AssertionError("merged other pins")
    except SR.Conflict as e:
        assert "pins" in str(e)
    assert SR.pins_hash(rec["symbol"]["pins"], rec["pin_map"]) == SR.pins_hash(rec)
    assert SR.pins_hash(rec["symbol"]["pins"], dict(rec["pin_map"], **{"1": "9"})) != SR.pins_hash(rec)


@lite
def test_two_reader_models_from_one_contributor_do_not_confirm(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    rec = SR.from_item(items["TPS1234DSGR"], base, by="alice", at=AT)
    a = SR.add_evidence(dict(rec, evidence=[_ev(rec, "alice", "model-a")]), _ev(rec, "alice", "model-b"))
    assert SR.level(a) == "checked"
    b = SR.add_evidence(dict(rec, evidence=[_ev(rec, "alice", "model-a")]), _ev(rec, "bob", "model-a"))
    assert SR.level(b) == "confirmed"


# ---------------------------------------------------------------- (g) deterministic
@lite
def test_record_is_deterministic(tmp_path, monkeypatch):
    _offline(monkeypatch)
    items, base = _kicad()
    a = SR.from_item(items["TPS1234DSGR"], base, by="alice", at=AT)
    b = SR.from_item(copy.deepcopy(_kicad()[0]["TPS1234DSGR"]), base, by="alice", at=AT)
    assert SK.digest(a) == SK.digest(b)
    c = SR.from_item(items["TPS1234DSGR"], base, by="alice")
    assert c["evidence"][0]["at"].endswith("Z") and SR.validate(c) == []
