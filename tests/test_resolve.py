"""Offline tests for ecadlib.resolve (fixing held parts). Run:  python3 -m tests.test_resolve

HTTP is a fake serving a tiny KiCad library (GitLab tree page + raw files): hand-written symbols (tests.test_librarian.ksym)
and footprints drawn by kmod() below. Package specs are ecadlib's own (ecadlib/package_specs)."""
import json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import kicad_import as K, librarian as L, release_check as RC, resolve as R
from ecadlib.httpcache import Response
from tests import test_librarian as TL


def kmod(name, pads, pitch=1.0):
    """Minimal KiCad footprint: one row of SMD pads, 'SH' pads as plated shield holes below, fab + courtyard."""
    sig = [p for p in pads if p != "SH"]
    w = len(sig) * pitch / 2 + 1
    out = [f'(footprint "{name}" (version 20240108) (generator "test") (layer "F.Cu")',
           '  (property "Reference" "REF**" (at 0 -3 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))',
           f'  (property "Value" "{name}" (at 0 4 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))',
           f'  (fp_rect (start {-w + 0.5:g} -1.5) (end {w - 0.5:g} 2.5) (stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab"))',
           f'  (fp_rect (start {-w:g} -2) (end {w:g} 3.5) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))']
    for i, n in enumerate(sig):
        out.append(f'  (pad "{n}" smd roundrect (at {(i - (len(sig) - 1) / 2) * pitch:g} 0) (size 0.6 1.2) '
                   f'(layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))')
    for i in range(pads.count("SH")):
        out.append(f'  (pad "SH" thru_hole oval (at {(-1) ** i * (w - 0.8):g} 2.2) (size 0.8 1.4) (drill oval 0.5 1.0) (layers "*.Cu" "*.Mask"))')
    return "\n".join(out) + ")\n"


USB4 = ["A1", "A4", "B1", "B4"]
SYMS = {"Connector:USB_C_Receptacle_Fake_4P": TL.ksym("USB_C_Receptacle_Fake_4P", USB4 + ["S1"], "J"),
        "Connector:USB_C_Receptacle_Fake_4P_NoShield": TL.ksym("USB_C_Receptacle_Fake_4P_NoShield", USB4, "J"),
        "Connector:USB_C_Receptacle_Fake_2P": TL.ksym("USB_C_Receptacle_Fake_2P", ["A1", "B1"], "J"),
        "Connector_Generic:Conn_02x05_Odd_Even": TL.ksym("Conn_02x05_Odd_Even", [str(i) for i in range(1, 11)], "J"),
        "Transistor_FET:FAKEFET": TL.ksym("FAKEFET", ["1", "2", "3"], "Q", footprint="Package_TO_SOT_SMD:SOT-23"),
        "Device:R": TL.ksym("R", ["1", "2"], "R")}
FOOTS = {"Connector_USB:USB_C_Receptacle_Fake": kmod("USB_C_Receptacle_Fake", USB4 + ["SH", "SH"]),
         "Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical": kmod("PinHeader_2x05_P1.27mm_Vertical", [str(i) for i in range(1, 11)]),
         "Connector_PinHeader_1.27mm:PinHeader_2x04_P1.27mm_Vertical": kmod("PinHeader_2x04_P1.27mm_Vertical", [str(i) for i in range(1, 9)]),
         "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical": kmod("PinHeader_2x05_P2.54mm_Vertical", [str(i) for i in range(1, 11)], 2.54)}


class FakeHTTP(TL.FakeHTTP):
    def __init__(self):
        self.routes, self.calls = {}, []
        for repo, files, dsuf, fsuf in (("kicad-symbols", SYMS, ".kicad_symdir", ".kicad_sym"), ("kicad-footprints", FOOTS, ".pretty", ".kicad_mod")):
            self.routes[L.TREE.format(repo=repo, page=1)] = json.dumps(
                [{"type": "blob", "path": f"{r.split(':')[0]}{dsuf}/{r.split(':')[1]}{fsuf}"} for r in files])
            for r, text in files.items():
                lib, name = r.split(":")
                self.routes[K.GITLAB.format(repo=repo, path=f"{lib}{dsuf}/{name}{fsuf}")] = text


PARAMS = [{"name": "Manufacturer", "value": "ACME"}, {"name": "ComponentLink2URL", "value": "https://example.com/ds.pdf"}]


def item(name, folder, desc, sym_ref, fp_ref):
    return {"name": name, "folder": f"Components/{folder}", "description": desc, "comment": "*",
            "parameters": PARAMS + [{"name": "Manufacturer Part Number", "value": name}], "cleared": [],
            "symbol": {"ref": sym_ref, "file": None}, "footprint": {"ref": fp_ref, "file": None},
            "source": {"symbol": None, "footprint": None}, "librarian": {"tried": [], "review": [], "notes": []}}


def make_plan(d):
    """USBC: footprint only. HDR: symbol only. FET: symbol only (SOT-23, a package spec). Held by release_check."""
    d, http = Path(d), FakeHTTP()
    r = L.Run(d, http, None, [], [])
    items = [item("USBC-FAKE", "Connectors", "USB Type-C receptacle 4-pin", "USBC-FAKE", "USB_C_Receptacle_Fake"),
             item("FTSH-105-TEST", "Connectors", "1.27mm 2x5 SWD header", "FTSH-105-TEST", "Samtec_FTSH-105-TEST"),
             item("FAKEFET1", "Transistors", "N-MOSFET SOT-23", "FAKEFET1", "SOT-23")]
    st = {"name": "USBC-FAKE", "symbol": None, "footprint": None}
    r.put_fp(st, FOOTS["Connector_USB:USB_C_Receptacle_Fake"], "USB_C_Receptacle_Fake", "kicad:Connector_USB:USB_C_Receptacle_Fake", http)
    items[0]["footprint"].update(file=st["footprint"]["file"], kicad_mod=st["footprint"]["kicad_mod"])
    items[0]["source"]["footprint"] = "kicad:Connector_USB:USB_C_Receptacle_Fake"
    for it, ref in ((items[1], "Connector_Generic:Conn_02x05_Odd_Even"), (items[2], "Transistor_FET:FAKEFET")):
        st = {"name": it["name"], "symbol": None, "footprint": None}
        r.put_kicad_sym(st, K.load_symbol(ref, http)[0], it["name"], it["footprint"]["ref"], f"kicad:{ref}")
        it["symbol"]["file"], it["source"]["symbol"] = st["symbol"]["file"], f"kicad:{ref}"
    RC.apply(items, d)
    (d / "plan.json").write_text(json.dumps(items, indent=2))
    return d / "plan.json", R.Sources(d / "cache", http=http)


def test_tokens_and_compare():
    t = R.tokens("PinHeader_2x05_P1.27mm_Vertical", "USB Type-C 16-pin", "Crystal_SMD_3225-4Pin", "Connectors")
    assert {"pinheader", "header", "2x5", "p1.27mm", "16p", "4p", "3225", "connector"} <= t, t
    assert R.tokens("1.27mm 2x5") == {"p1.27mm", "2x5"}
    pads = [("A1", False), ("B1", False), ("SH", True), ("SH", True), ("", True)]
    assert R.compare(["A1", "B1"], pads)["match"]
    c = R.compare(["A1", "B1", "S1"], pads)
    assert not c["match"] and c["pins_without_pad"] == ["S1"] and c["pin_map"] == {"S1": "SH"}, c
    c = R.compare(["1", "2"], [("1", False), ("3", False)])
    assert c["pin_map"] == {"2": "3"} and c["pads_without_pin"] == ["3"], c
    assert R.compare([str(i) for i in range(1, 9)], [(f"A{i}", False) for i in range(1, 9)])["pin_map"] is None   # renumbering: no guess


def test_held_and_show():
    with tempfile.TemporaryDirectory() as d:
        plan, _ = make_plan(d)
        h = {x["name"]: x for x in R.held(plan)}
        assert set(h) == {"USBC-FAKE", "FTSH-105-TEST", "FAKEFET1"}, h
        assert h["USBC-FAKE"]["missing"] == ["symbol"] and "files: no symbol model (.SchLib)" in h["USBC-FAKE"]["hold_reasons"]
        assert h["FTSH-105-TEST"]["missing"] == ["footprint"]
        s = R.show(plan, "USBC-FAKE")
        assert s["symbol"]["pins"] is None and sorted(set(s["footprint"]["pads"])) == ["A1", "A4", "B1", "B4", "SH"], s
        try:
            R.show(plan, "NOPE")
            assert False
        except KeyError:
            pass


def test_symbol_candidates_and_pin_map():
    with tempfile.TemporaryDirectory() as d:
        plan, src = make_plan(d)
        it = next(i for i in json.loads(plan.read_text()) if i["name"] == "USBC-FAKE")
        cs = R.candidates(it, "symbol", plan.parent, src)
        by = {c["id"]: c for c in cs}
        assert cs[0]["id"] == "kicad:Connector:USB_C_Receptacle_Fake_4P_NoShield" and cs[0]["match"] and cs[0]["count"] == 4, cs[0]
        near = by["kicad:Connector:USB_C_Receptacle_Fake_4P"]
        assert near["match"] is False and near["pins_without_pad"] == ["S1"] and near["pin_map"] == {"S1": "SH"}, near
        assert by["kicad:Connector:USB_C_Receptacle_Fake_2P"]["pads_without_pin"] == ["A4", "B4"]
        assert cs.index(near) < cs.index(by["kicad:Connector:USB_C_Receptacle_Fake_2P"])
        assert "kicad:Device:R" not in by                                                  # shares nothing with the item
        # choose the shielded symbol without a map: held on pins; then map S1 -> SH through a footprint re-choose
        r = R.choose(plan, "USBC-FAKE", "symbol", near["id"], src=src)
        assert r["status"] == "held" and any(m.startswith("pins: symbol pins without a pad: S1") for m in r["hold_reasons"]), r
        assert not any(m.startswith("files") for m in r["hold_reasons"])
        r = R.choose(plan, "USBC-FAKE", "footprint", "kicad:Connector_USB:USB_C_Receptacle_Fake", src=src, pin_map={"S1": "SH"})
        assert r["status"] == "ready" and r["pins_vs_pads"]["match"], r
        it = next(i for i in json.loads(plan.read_text()) if i["name"] == "USBC-FAKE")
        assert it["source"] == {"symbol": near["id"], "footprint": "kicad:Connector_USB:USB_C_Receptacle_Fake"}
        assert it["symbol"]["pin_map"] == {"S1": "SH"} and it["symbol"]["file"] == "models/SchLib/USBC-FAKE.SchLib"
        assert any("chosen by user (pin map {'S1': 'SH'})" in t for t in it["librarian"]["tried"]), it["librarian"]["tried"]
        assert it["release_check"]["result"] == "pass"


def test_footprint_candidates_rank_by_pitch_and_pads():
    with tempfile.TemporaryDirectory() as d:
        plan, src = make_plan(d)
        it = next(i for i in json.loads(plan.read_text()) if i["name"] == "FTSH-105-TEST")
        cs = R.candidates(it, "footprint", plan.parent, src)
        ids = [c["id"] for c in cs]
        good = "kicad:Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical"
        assert ids[0] == good and cs[0]["match"] and cs[0]["count"] == 10, cs[0]
        names = R._name_scores(it, "footprint", src)
        for bad in ("2.54mm:PinHeader_2x05_P2.54mm_Vertical", "1.27mm:PinHeader_2x04_P1.27mm_Vertical"):     # stated pitch / grid rule them out
            assert f"kicad:Connector_PinHeader_{bad}" not in ids and f"kicad:Connector_PinHeader_{bad}" not in names
        it = dict(it, description="1.27mm SWD header", source={"symbol": None, "footprint": None})   # no grid known: 2x04 offered, pads compared
        c = {c["id"]: c for c in R.candidates(it, "footprint", plan.parent, src)}["kicad:Connector_PinHeader_1.27mm:PinHeader_2x04_P1.27mm_Vertical"]
        assert c["match"] is False and c["pins_without_pad"] == ["9", "10"], c
        r = R.choose(plan, "FTSH-105-TEST", "footprint", good, src=src)
        assert r["status"] == "ready" and r["hold_reasons"] == [], r                           # no KiCad 3D model: step_gen made one
        assert any("generated STEP" in n for n in r["notes"]), r
        it = next(i for i in json.loads(plan.read_text()) if i["name"] == "FTSH-105-TEST")
        assert it["footprint"]["file"] == "models/PcbLib/Samtec_FTSH-105-TEST.PcbLib" and (plan.parent / it["footprint"]["kicad_mod"]).exists()
        assert it["source"]["footprint"] == good and it["release_check"]["failures"] == r["hold_reasons"]


def test_spec_candidate_and_alternatives():
    with tempfile.TemporaryDirectory() as d:
        plan, src = make_plan(d)
        items = json.loads(plan.read_text())
        it = next(i for i in items if i["name"] == "FAKEFET1")
        it["librarian"]["alternatives"] = [{"kind": "footprint", "id": "kicad:Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical", "why": "test"}]
        plan.write_text(json.dumps(items))
        cs = {c["id"]: c for c in R.candidates(it, "footprint", plan.parent, src)}
        spec = cs["spec:SOT-23"]
        assert spec["match"] and spec["count"] == 3 and any("package SOT23" in w for w in spec["why"]), spec
        assert list(cs)[0] == "spec:SOT-23"
        alt = cs["kicad:Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical"]
        assert alt["why"][0] == "recorded by the librarian: test" and alt["match"] is False
        r = R.choose(plan, "FAKEFET1", "footprint", "spec:SOT-23", src=src)
        assert not any(m.startswith(("files", "pins", "footprint", "height")) for m in r["hold_reasons"]), r
        it = next(i for i in json.loads(plan.read_text()) if i["name"] == "FAKEFET1")
        assert it["source"]["footprint"] == "spec:SOT-23" and 0.8 <= it["release_check"]["height"] <= 1.45, it["release_check"]
        assert any("package spec" in n for n in r["notes"]) or it["release_check"]["height"], r["notes"]
        for bad in ("nope:X", "kicad:Device:Missing"):
            try:
                R.choose(plan, "FAKEFET1", "symbol", bad, src=src)
                assert False, bad
            except (ValueError, FileNotFoundError):
                pass


def test_librarian_keeps_the_choice():
    with tempfile.TemporaryDirectory() as d:
        items, http = TL.run(d, sources=["kicad"])
        out = Path(d) / "out"
        assert items["NOWHERE1"]["footprint"]["file"] is None
        src = R.Sources(Path(d) / "cache", http=http)
        r = R.choose(out / "plan.json", "NOWHERE1", "footprint", "kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", src=src)
        assert r["source"] == "kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" and "files: no footprint model (.PcbLib)" not in r["hold_reasons"]
        state = json.loads((out / "librarian_state.json").read_text())
        assert state["NOWHERE1"]["footprint"]["chosen"]
        items, _ = TL.run(d, sources=["kicad"])                       # NOWHERE1 is still unfinished: redone, choice kept
        it = items["NOWHERE1"]
        assert it["source"]["footprint"] == "kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" and it["footprint"]["file"], it["source"]
        assert any("chosen by user (kept)" in t for t in it["librarian"]["tried"]), it["librarian"]["tried"]


def test_cli():
    with tempfile.TemporaryDirectory() as d:
        plan, src = make_plan(d)
        with mock.patch.object(R, "CachedHTTP", lambda *a, **k: src.http):
            from ecadlib import cli
            run = lambda *a: cli.main(["resolve", str(plan), "--cache", str(Path(d) / "cache"), *a])
            out = []
            with mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
                assert run("list") == 0 and "3 held" in out[-1]
                out.clear()
                assert run("--part", "FTSH-105-TEST", "--json", "list") == 0
                assert json.loads(out[0])["footprint"][0]["id"].endswith("PinHeader_2x05_P1.27mm_Vertical")
                out.clear()
                assert run("--part", "USBC-FAKE", "show") == 0 and "USBC-FAKE: held" in out[0]
                out.clear()
                rc = run("--part", "USBC-FAKE", "choose", "symbol", "kicad:Connector:USB_C_Receptacle_Fake_4P", "--map", "S1=SH")
                assert rc == 0 and "now ready" in out[0], out
        assert {i["name"]: i["status"] for i in json.loads(plan.read_text())}["USBC-FAKE"] == "ready"


def test_choose_uses_house_layer_rules():
    """choose writes the PcbLib with the ecadlib.toml layers, like the librarian (was: always Mechanical 15)."""
    from ecadlib import altium_lib as AL
    with tempfile.TemporaryDirectory() as d:
        plan, src = make_plan(d)
        (Path(d) / "ecadlib.toml").write_text('[layers]\ncourtyard = "Mechanical11"\n')
        R.choose(plan, "FTSH-105-TEST", "footprint", "kicad:Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical", src=src)
        it = next(i for i in json.loads(plan.read_text()) if i["name"] == "FTSH-105-TEST")
        fp = AL.read_pcblib(Path(d) / it["footprint"]["file"])[0]
        layers = {r["layer"] for r in fp["records"] if r["type"] in ("track", "arc", "region")}
        assert 56 + 11 in layers and AL.MECH15 not in layers, layers


def test_librarian_refresh_does_not_overwrite_a_shared_choice():
    """A footprint chosen for one of two parts sharing a file gets its own file (other pads: fp_share names it), so the
    other part keeps its released pads, and `librarian --refresh` re-fetches over neither (T011).

    The chosen footprint has the part's 8 pads: a choice that fails the release check is no longer applied to a
    released part (it waits as a held change), so a 2-pad LED footprint would leave the file as it was."""
    from ecadlib import kicad_import as K
    bom = TL.BOM + "U5,1,OTHER1,SOIC-8_3.9x4.9mm_P1.27mm,ACME,OTHER1,,Other SOIC part,\n"
    with tempfile.TemporaryDirectory() as d:          # TPS1234DSGR's suffix-matched symbol: checked, released anyway
        items, http = TL.run(d, bom=bom, sources=["kicad"], allow=["TPS1234DSGR"], user="Test Reviewer <reviewer@example.com>")
        out = Path(d) / "out"
        assert items["TPS1234DSGR"]["status"] == "ready", items["TPS1234DSGR"]["hold_reasons"]
        assert items["OTHER1"]["footprint"]["file"] == items["TPS1234DSGR"]["footprint"]["file"]
        alt = TL.FOOTS["Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"].replace("SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8_ALT") \
            .replace("(size 1.965 0.58)", "(size 1.965 0.6)")                      # other pads than the shared file's
        http.routes[K.GITLAB.format(repo="kicad-footprints", path="Package_SO.pretty/SOIC-8_ALT.kicad_mod")] = alt
        f = out / items["TPS1234DSGR"]["footprint"]["file"]
        before = f.read_bytes()
        r = R.choose(out / "plan.json", "TPS1234DSGR", "footprint", "kicad:Package_SO:SOIC-8_ALT",
                     src=R.Sources(Path(d) / "cache", http=http))
        own = out / r["file"]
        assert r["status"] == "ready" and own != f and own.name == "SOIC-8_3.9x4.9mm_P1.27mm_SOIC-8_ALT.PcbLib", r
        assert f.read_bytes() == before                                         # OTHER1 keeps its released file
        chosen = own.read_bytes()
        items, _ = TL.run(d, bom=bom, sources=["kicad"], refresh=[])
        assert own.read_bytes() == chosen and f.read_bytes() == before
        assert items["TPS1234DSGR"]["source"]["footprint"] == "kicad:Package_SO:SOIC-8_ALT"
        assert items["TPS1234DSGR"]["footprint"]["file"] == r["file"] and items["OTHER1"]["footprint"]["file"] != r["file"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
