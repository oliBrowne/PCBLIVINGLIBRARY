"""Regression tests for the code review of the 2026-10-20 independent-testing fixes (T-ids of the fix each one
follows up in the comments). Nothing live. Run:
    python3 -m tests.test_indtest_review"""
import json
from pathlib import Path

from ecadlib import altium_lib as AL
from tests.fixtures_lite import lite, released_anyway
from tests.test_indtest_altium import _item, _models


# T092 follow-up: --no-native-build leaves the recipes out but keeps the .ecadstate edit records, so a library
# changed in Altium in the package is not copied over by the next run
@lite
def test_no_native_build_keeps_an_altium_edit(tmp_path, monkeypatch):
    from ecadlib import altium_native as AN, altium_pkg as PK
    d = tmp_path
    monkeypatch.setenv("ECADLIB_HOME", d / "home")
    _models(d)
    for f in (d / "models").rglob("*.*Lib"):
        f.with_name(f.name + AN.EXT).write_text("KIND\tx\n", encoding="utf-8")
    items = released_anyway([_item("RES-0603-10k", "Resistors", "Resistor", "R_0603_1608Metric", Value="10k")], d)
    (d / "plan.json").write_text(json.dumps(items, indent=1))
    for src in (d / "plan.json", d / "models"):
        pkg = d / f"pkg_{src.stem}"
        args = dict(plan=d / "plan.json") if src.is_dir() else {}
        PK.build(src, pkg, native=False, **args)
        assert list(pkg.rglob(f"*{AN.EXT}")) == []
        lib = next(pkg.rglob("R_0603_1608Metric.PcbLib"))
        data = bytearray(lib.read_bytes())
        data[-1] ^= 1                                       # changed in Altium
        lib.write_bytes(bytes(data))
        rep = PK.build(src, pkg, native=False, **args)
        assert lib.read_bytes() == bytes(data), src         # the edit is still there
        assert "changed in Altium" in json.dumps(rep), rep
        assert list(pkg.rglob(f"*{AN.EXT}")) == []


def _links(f):
    """The footprint links (RECORD=45 ModelName) and the parameters of a .SchLib's first symbol."""
    recs = AL.read_schlib(f)[1][0]["records"]
    return ([r.get("MODELNAME") for r in recs if r.get("RECORD") == "45"],
            {r.get("NAME"): r.get("TEXT") for r in recs if r.get("RECORD") == "41"})


# T011 follow-up: a footprint `resolve choose` names anew is the symbol's footprint link too, whatever source drew the
# symbol (here the standard symbols: they were left linking the other part's footprint)
def test_choose_footprint_relinks_a_standard_symbol():
    import tempfile
    from tests.test_indtest_change import _run
    bom = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
           "D1,1,RED,LED_0603_1608Metric,Hubei KENTO Elec,KT-0603R,C2286,LED red 0603,https://example.com/r.pdf\n"
           "D2,1,GREEN,LED_0603_1608Metric,Hubei KENTO Elec,KT-0603G,C2287,LED green 0603,https://example.com/g.pdf\n")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "bom.csv").write_text(bom, encoding="utf-8")
        assert _run(d, d / "home", "librarian", "bom.csv", "-o", "lib", "--offline", "--sources", "standard").returncode == 0
        lib = d / "lib"
        items = {i["name"]: i for i in json.loads((lib / "plan.json").read_text(encoding="utf-8"))}
        assert items["KT-0603R"]["source"]["symbol"].startswith("standard:"), items["KT-0603R"]["source"]
        green_sym = (lib / items["KT-0603G"]["symbol"]["file"]).read_bytes()
        r = _run(d, d / "home", "resolve", "lib/plan.json", "--part", "KT-0603R", "--offline", "choose", "footprint",
                 "spec:LED_0805_2012Metric")
        assert r.returncode == 0, r.stdout + r.stderr
        items = {i["name"]: i for i in json.loads((lib / "plan.json").read_text(encoding="utf-8"))}
        red = items["KT-0603R"]
        assert red["footprint"]["ref"] == "LED_0603_1608Metric_LED_0805_2012Metric", red["footprint"]
        assert _links(lib / red["symbol"]["file"])[0] == [red["footprint"]["ref"]], _links(lib / red["symbol"]["file"])
        assert red["status"] == "ready", red["hold_reasons"]
        assert (lib / items["KT-0603G"]["symbol"]["file"]).read_bytes() == green_sym


def _resistors(d):
    from tests import test_librarian as TL
    bom = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
           "R1,1,10k,R_0603_1608Metric,ACME,RES-0603-10k,,Resistor 10k 0603,\n"
           "R2,1,22k,R_0603_1608Metric,ACME,RES-0603-22k,,Resistor 22k 0603,\n")
    items, http = TL.run(d, bom=bom, sources=["kicad"], allow=["RES-0603-10k", "RES-0603-22k"],
                         user="Test Reviewer <r@example.com>")
    return items, http, Path(d) / "out"


# T011 x T039 follow-up: a footprint chosen for one resistor (other pads: its own name) leaves the Resistor symbol
# every resistor shares as it is, with its CC-BY-SA credit
@lite
def test_choose_footprint_keeps_the_shared_symbol(tmp_path, monkeypatch):
    from ecadlib import kicad_import as K, resolve as R
    from tests import test_librarian as TL
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    items, http, out = _resistors(tmp_path)
    sf = out / items["RES-0603-22k"]["symbol"]["file"]
    before = sf.read_bytes()
    assert _links(sf)[1].get("Library Credit") == K.KICAD_CREDIT, _links(sf)
    alt = TL.FOOTS["Resistor_SMD:R_0603_1608Metric"].replace("R_0603_1608Metric", "R_0603_ALT") \
        .replace("(size 0.825 0.91)", "(size 0.9 0.91)")
    http.routes[K.GITLAB.format(repo="kicad-footprints", path="Resistor_SMD.pretty/R_0603_ALT.kicad_mod")] = alt
    r = R.choose(out / "plan.json", "RES-0603-10k", "footprint", "kicad:Resistor_SMD:R_0603_ALT",
                 src=R.Sources(tmp_path / "cache", http=http))
    assert r["file"].endswith("R_0603_1608Metric_R_0603_ALT.PcbLib"), r
    assert sf.read_bytes() == before


# T039 follow-up: a KiCad symbol `resolve choose symbol` writes carries the CC-BY-SA credit and, for a resistor, is the
# shared symbol (no footprint link); the parts sharing it are re-checked
@lite
def test_choose_kicad_symbol_has_the_credit(tmp_path, monkeypatch):
    from ecadlib import kicad_import as K, resolve as R
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    items, http, out = _resistors(tmp_path)
    r = R.choose(out / "plan.json", "RES-0603-10k", "symbol", "kicad:Device:R", src=R.Sources(tmp_path / "cache", http=http))
    links, pars = _links(out / r["file"])
    assert pars.get("Library Credit") == K.KICAD_CREDIT and links == [], (links, pars)
    assert "RES-0603-22k" in {c["part"] for c in r["change"]} or any("RES-0603-22k" in n for n in r["notes"]), r


# T011 x T039 follow-up: the parts sharing a symbol file `resolve choose footprint` relinks are re-checked: the link
# now names another part's footprint, so the release check holds them
def test_choose_footprint_rechecks_parts_sharing_the_relinked_symbol():
    import tempfile
    from ecadlib import kicad_import as K, release_check as RC, resolve as R
    from tests import test_resolve as TR
    with tempfile.TemporaryDirectory() as d:
        plan, src = TR.make_plan(d)
        R.choose(plan, "FAKEFET1", "footprint", "spec:SOT-23", src=src)
        items = json.loads(plan.read_text(encoding="utf-8"))
        a = next(i for i in items if i["name"] == "FAKEFET1")
        items.append(dict(json.loads(json.dumps(a)), name="FAKEFET2"))          # the same symbol and footprint files
        RC.apply(items, Path(d))
        plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
        assert [i["status"] for i in items[-2:]] == ["ready", "ready"], [i["hold_reasons"] for i in items[-2:]]
        src.http.routes[K.GITLAB.format(repo="kicad-footprints", path="Package_TO_SOT_SMD.pretty/SOT-23_ALT.kicad_mod")] = \
            TR.kmod("SOT-23_ALT", ["1", "2", "3"])
        r = R.choose(plan, "FAKEFET1", "footprint", "kicad:Package_TO_SOT_SMD:SOT-23_ALT", src=src)
        items = {i["name"]: i for i in json.loads(plan.read_text(encoding="utf-8"))}
        assert r["status"] == "ready" and _links(Path(d) / a["symbol"]["file"])[0] == [items["FAKEFET1"]["footprint"]["ref"]], r
        b = items["FAKEFET2"]
        assert b["status"] == "held" and any("the symbol links footprint SOT-23_ALT" in h for h in b["hold_reasons"]), b


# T011 follow-up: the release check holds a part whose symbol links another footprint than the part's
@lite
def test_release_check_compares_the_symbol_link(tmp_path):
    from ecadlib import release_check as RC
    from tests.test_indtest_altium import _item
    d = tmp_path
    _models(d)
    AL.write_schlib(d / "models/SchLib/Resistor.SchLib", [dict(AL.resistor_symbol(), footprint="R_0603_1608Metric")])
    ok = _item("RES-0603-10k", "Resistors", "Resistor", "R_0603_1608Metric", Value="10k")
    bad = dict(ok, footprint={"ref": "C_0402_1005Metric", "file": "models/PcbLib/C_0402_1005Metric.PcbLib"})
    assert not [f for f in RC.check_item(ok, d)[0] if "links footprint" in f[1]], RC.check_item(ok, d)[0]
    assert ("pins", "the symbol links footprint R_0603_1608Metric, not the part's C_0402_1005Metric") in RC.check_item(bad, d)[0]


# T011 follow-up: the "footprint file is shared with" note names the other parts using the file, once each, not the
# part that wrote it (here the chosen part itself)
@lite
def test_choose_shared_footprint_note_names_the_other_parts(tmp_path, monkeypatch):
    from ecadlib import resolve as R
    monkeypatch.setenv("ECADLIB_HOME", tmp_path / "home")
    items, http, out = _resistors(tmp_path)
    r = R.choose(out / "plan.json", "RES-0603-10k", "footprint", "kicad:Resistor_SMD:R_0603_1608Metric",
                 src=R.Sources(tmp_path / "cache", http=http))
    assert "footprint file is shared with RES-0603-22k: the same pads" in r["notes"], r["notes"]


# T011 follow-up: a renamed footprint is "the same land pattern" in the gate summary only when its pads are the same
@lite
def test_renamed_footprint_with_other_pads_is_not_the_same_land_pattern(tmp_path):
    import copy
    from ecadlib import change_diff as CD, fingerprint as FP
    _models(tmp_path)
    old = FP.of_item(_item("R1", "Resistors", "Resistor", "R_0603_1608Metric"), tmp_path)
    same = copy.deepcopy(old)
    same["footprint"]["name"], same["hashes"] = "R_0603_ALT", {}
    other = copy.deepcopy(same)
    other["footprint"]["pads"][0]["corner"] = 25                         # a minor pad change: not the same pads
    texts = lambda new: [c["text"] for c in CD.diff(old, new)]
    assert "footprint renamed R_0603_1608Metric -> R_0603_ALT (the same land pattern)" in texts(same), texts(same)
    assert not any("the same land pattern" in t for t in texts(other)), texts(other)
    assert any("footprint renamed R_0603_1608Metric -> R_0603_ALT" in t for t in texts(other)), texts(other)


# kicad-pcm follow-up: -q keeps the warning that the library's KiCad content (CC-BY-SA) may not allow --license
@lite
def test_kicad_pcm_quiet_keeps_the_license_warning(tmp_path):
    import shutil
    from ecadlib import term as TM
    from tests.test_indtest_kicadinstall import _demo, _run
    shutil.copytree(_demo(), tmp_path / "ws")
    plan = tmp_path / "ws/librarian/demo_bom_demo/plan.json"
    items = json.loads(plan.read_text(encoding="utf-8"))
    items[0]["source"]["footprint"] = "kicad:Capacitor_SMD:C_0402_1005Metric"     # some KiCad library content
    plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
    old = TM.VERBOSITY
    try:
        rc, out, err = _run(["-q", "kicad-pcm", str(plan), "--out", str(tmp_path / "pcm"), "--license", "MIT",
                             "--offline", "--cache", str(tmp_path / "cache")], cwd=tmp_path,
                            env={"ECADLIB_HOME": str(tmp_path / "home")})
    finally:
        TM.VERBOSITY = old
    assert rc == 0, out + err
    assert "--license MIT may not be allowed" in out + err, out + err


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
