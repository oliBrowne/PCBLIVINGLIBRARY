"""Offline tests for ecadlib.release_check and the held-item skip in altium365.push. Run:  python3 -m tests.test_release_check

Models are built here with altium_lib from ecadlib's own generated SOIC-8 footprint (out/footprints) and
synthetic pin lists; each test breaks one thing and checks that exactly that rule holds the part back."""
import json, re, tempfile
from pathlib import Path

from ecadlib import altium365 as A, altium_lib as AL, kicad_import as K, release_check as RC

SOIC = (Path(__file__).parent.parent / "out" / "footprints" / "ecadlib.pretty" / "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod").read_text()
PARAMS = [{"name": "Manufacturer", "value": "ACME"}, {"name": "Manufacturer Part Number", "value": "PART1"},
          {"name": "ComponentLink2URL", "value": "https://example.com/p.pdf"}]


def make(d, kmod=SOIC, pins=range(1, 9), height=None, source="local:ecadlib:SOIC-8", keep_kmod=True, name="PART1", fp_name="SOIC-8_3.9x4.9mm_P1.27mm"):
    """Write SchLib + PcbLib (+ .kicad_mod) under d/models and return a plan item pointing at them (paths relative to d)."""
    d, m = Path(d), Path(d) / "models"
    for s in ("SchLib", "PcbLib", "KiCad"):
        (m / s).mkdir(parents=True, exist_ok=True)
    fp, t = K.convert_footprint(kmod, fp_name)
    if height is not False:
        K.add_body(fp, t, None, height)
        if height:
            fp["bodies"][-1]["height"] = fp["height_mm"] = height
    AL.write_pcblib(m / "PcbLib" / f"{fp_name}.PcbLib", [fp])
    if keep_kmod:
        (m / "KiCad" / f"{fp_name}.kicad_mod").write_text(kmod)
    sym = AL.ic_symbol(name, [{"num": str(p), "name": f"P{p}", "type": "io", "side": "left"} for p in pins], footprint=fp_name)
    AL.write_schlib(m / "SchLib" / f"{name}.SchLib", [sym])
    return {"name": name, "folder": "Components/ICs", "description": "Test part", "comment": name, "parameters": [dict(p) for p in PARAMS],
            "cleared": [], "symbol": {"ref": name, "file": f"models/SchLib/{name}.SchLib"},
            "footprint": {"ref": fp_name, "file": f"models/PcbLib/{fp_name}.PcbLib",
                          **({"kicad_mod": f"models/KiCad/{fp_name}.kicad_mod"} if keep_kmod else {})},
            "status": "ready", "source": {"symbol": "local:x", "footprint": source}}


def fails(item, d):
    f, w, _ = RC.check_item(item, d)
    return [f"{r}: {m}" for r, m in f], w


def test_pass():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        f, w = fails(it, d)
        assert f == [], f
        rows = RC.apply([it], d)
        assert it["status"] == "ready" and rows[0]["result"] == "pass" and rows[0]["height_mm"] == "1.75" and it["hold_reasons"] == []


def test_files():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        Path(d, it["symbol"]["file"]).unlink()
        assert fails(it, d)[0] == [f"files: symbol file missing: {Path(d) / it['symbol']['file']}"]
        it["symbol"]["file"] = None
        assert fails(it, d)[0] == ["files: no symbol model (.SchLib)"]
        it = make(d)
        Path(d, it["footprint"]["file"]).write_bytes(b"not an OLE file")
        f = fails(it, d)[0]
        assert len(f) == 1 and f[0].startswith("files: footprint file does not read back"), f
        RC.apply([it], d, allow=["PART1"])                   # files cannot be overridden
        assert it["status"] == "held" and it["hold_reasons"][0].endswith("(cannot be overridden)"), it["hold_reasons"]


def test_pins_and_pads():
    with tempfile.TemporaryDirectory() as d:
        assert fails(make(d, pins=range(1, 8)), d)[0] == ["pins: pads without a symbol pin: 8"]
        assert fails(make(d, pins=range(1, 10)), d)[0] == ["pins: symbol pins without a pad: 9"]
        mp = SOIC.replace("\n)", '\n  (pad "MP" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask"))'
                                 '\n  (pad "" np_thru_hole circle (at 0 1.5) (size 0.8 0.8) (drill 0.8) (layers "*.Cu" "*.Mask"))\n)')
        f, w = fails(make(d, kmod=mp), d)
        assert not [x for x in f if x.startswith("pins")] and any("mechanical pads without a pin (allowed): MP" in x for x in w), (f, w)


def test_footprint_rules():
    with tempfile.TemporaryDirectory() as d:
        overlap = SOIC.replace('(pad "2" smd roundrect (at -2.4725 -0.635)', '(pad "2" smd roundrect (at -2.4725 -1.7)')
        f = fails(make(d, kmod=overlap), d)[0]
        assert any(x.startswith("footprint: copper: pads 1 and 2 overlap") for x in f), f
        no_crt = "\n".join(l for l in SOIC.splitlines() if "F.CrtYd" not in l)
        assert fails(make(d, kmod=no_crt), d)[0] == ["footprint: courtyard: no courtyard on F.CrtYd"]
        no_p1 = SOIC.replace('(pad "1" ', '(pad "9" ')
        f = fails(make(d, kmod=no_p1, pins=range(2, 10)), d)[0]
        assert "footprint: pin1: no pad numbered 1" in f, f
        a1 = SOIC.replace('(pad "1" ', '(pad "A1" ')
        assert fails(make(d, kmod=a1, pins=["A1"] + list(range(2, 9))), d)[0] == []      # A1 counts as pin 1
        silk = SOIC.replace("(start -2.06 -2.56) (end 2.06 -2.56)", "(start -2.06 -1.9) (end 2.06 -1.9)")
        f, w = fails(make(d, kmod=silk, source="easyeda:C1"), d)
        assert any(x.startswith("footprint: silk:") for x in f), f        # full rule set for non-KiCad sources
        f, w = fails(make(d, kmod=silk, source="kicad:Package_SO:SOIC-8"), d)
        assert f == [] and any(x.startswith("error silk:") for x in w), (f, w)   # KiCad library: minimum rules only
        f = fails(make(d, kmod=overlap, source="kicad:Package_SO:SOIC-8"), d)[0]
        assert any("overlap" in x for x in f), f
        f = fails(make(d, kmod=no_crt, source="kicad:Package_SO:SOIC-8"), d)[0]
        assert f == ["footprint: courtyard: no courtyard on F.CrtYd"], f


def test_stacked_same_net_pads():
    """USB-C style: overlapping pads on symbol pins with the same name (GND) are a warning, not a hold."""
    with tempfile.TemporaryDirectory() as d:
        overlap = SOIC.replace('(pad "2" smd roundrect (at -2.4725 -0.635)', '(pad "2" smd roundrect (at -2.4725 -1.7)')
        it = make(d, kmod=overlap, source="kicad:Package_SO:SOIC-8")
        sym = AL.ic_symbol("PART1", [{"num": str(p), "name": "GND" if p <= 2 else f"P{p}", "type": "io", "side": "left"} for p in range(1, 9)])
        AL.write_schlib(Path(d, it["symbol"]["file"]), [sym])
        f, w = fails(it, d)
        assert f == [] and any("pads 1 and 2 overlap" in x and "stacked on purpose" in x for x in w), (f, w)
        Path(d, it["footprint"]["kicad_mod"]).unlink()                     # same rule on the PcbLib-only path
        f, w = fails(it, d)
        assert f == [] and any(x.startswith("pads 1 and 2 overlap") for x in w), (f, w)
        assert fails(make(d, kmod=overlap), d)[0]                          # different pin names: still held


def test_footprint_minimal_on_pcblib():
    with tempfile.TemporaryDirectory() as d:
        f, w = fails(make(d, keep_kmod=False), d)
        assert f == [] and any("no .kicad_mod" in x for x in w), (f, w)
        overlap = SOIC.replace('(pad "2" smd roundrect (at -2.4725 -0.635)', '(pad "2" smd roundrect (at -2.4725 -1.7)')
        no_crt = "\n".join(l for l in overlap.splitlines() if "F.CrtYd" not in l)
        f = fails(make(d, kmod=no_crt, keep_kmod=False), d)[0]
        assert f == ["footprint: pads 1 and 2 overlap", "footprint: no courtyard (Mechanical 15)"], f
        bad = SOIC.replace("(start -3.71 -2.7)", "(center -3.71 -2.7)").replace("fp_rect", "fp_circle", 1)   # KiCad 6+ circles
        it = make(d, kmod=SOIC)
        Path(d, it["footprint"]["kicad_mod"]).write_text(bad.replace("(fp_poly (pts (xy -0.975", "(fp_circle (center 0 0) (end 1 0) (layer \"F.SilkS\")) (fp_poly (pts (xy -0.975"))
        f, w = fails(it, d)                   # the edited courtyard now differs from the PcbLib's: parity says so
        assert [x for x in f if not x.startswith("parity: courtyard")] == [] and not any(x.startswith("footprint_check failed") for x in w), (f, w)


def test_height():
    with tempfile.TemporaryDirectory() as d:
        f = fails(make(d, height=5.0), d)[0]
        assert f == ["height: 3D height 5.00 mm outside 1.2-1.85 mm for ^(SOIC|SOP|SO-)"], f
        it = make(d, height=False, fp_name="Odd_Part")
        f = fails(it, d)[0]
        assert f == ["height: no 3D height (no 3D body and no HEIGHT)"], f
        assert RC.height_range("QFN-16-1EP_3x3mm")[:2] == (0.3, 1.05) and RC.height_range("R_0603_1608Metric")[:2] == (0.3, 1.1)
        assert RC.height_range("SOT-223-3_TabPin2")[:2] == (1.4, 1.95) and RC.height_range("TSSOP-16")[:2] == (0.75, 1.25)
        assert RC.height_range("Mystery")[2] is None
        f, w = fails(make(d, height=2.0, fp_name="Mystery_Package"), d)
        assert f == [] and any("no height range" in x for x in w), (f, w)


def test_fields_and_verify():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        it["parameters"] = [p for p in it["parameters"] if p["name"] not in ("Manufacturer", "ComponentLink2URL")]
        it["description"] = " "
        it["verify"] = ["datasheet reader output: compare"]
        assert fails(it, d)[0] == ["fields: Manufacturer is empty", "fields: no datasheet link", "fields: Description is empty",
                                   "verify: datasheet reader output: compare"]
        it["parameters"].append({"name": "Datasheet", "value": "https://x/y.pdf"})
        assert "fields: no datasheet link" not in fails(it, d)[0]


def test_allow_recorded_and_scoped():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        it["verify"] = ["check me"]
        RC.apply([it], d)
        assert it["status"] == "held" and it["hold_reasons"] == ["verify: check me"] and "allow" not in it
        RC.apply([it], d, allow=["PART1"])
        assert it["status"] == "ready" and it["allow"]["failures"] == ["verify: check me"] and it["release_check"]["result"] == "allowed"
        RC.apply([it], d)                                   # recorded: still allowed on the next check
        assert it["status"] == "ready"
        it["description"] = ""                              # a new failure is not covered by the old --allow
        RC.apply([it], d)
        assert it["status"] == "held" and it["hold_reasons"] == ["fields: Description is empty (not covered by --allow)"], it["hold_reasons"]
        RC.apply([it], d, unallow=["PART1"])
        assert "allow" not in it and it["status"] == "held"


class FakeNexar:
    def __init__(self):
        self.calls = []

    def release_defs(self, ws):
        return {}

    def find(self, ws, name):                               # every part already exists in the workspace
        self.calls.append(("find", name))
        return {"id": "c1", "details": {"parameters": []}}

    def update_params(self, cid, params, note, replace=False):
        self.calls.append(("update", cid))
        return "c2"


def test_held_items_not_pushed():
    with tempfile.TemporaryDirectory() as d:
        ok, held = make(d), make(d, name="PART2")
        held["verify"] = ["unchecked"]
        RC.apply([ok, held], d)
        assert (ok["status"], held["status"]) == ("ready", "held")
        for it in (ok, held):
            for k in ("symbol", "footprint"):
                it[k]["file"] = str(Path(d) / it[k]["file"])
        res = {r["name"]: r for r in A.push([ok, held], None, "https://ws", dry_run=True)}
        assert res["PART1"]["action"] == "release" and res["PART2"]["action"] == "skip"
        assert res["PART2"]["detail"] == "held: verify: unchecked", res["PART2"]
        fake = FakeNexar()
        res = {r["name"]: r for r in A.push([ok, held], fake, "https://ws")}
        assert res["PART2"]["action"] == "skip" and ("find", "PART2") not in fake.calls and ("find", "PART1") in fake.calls
        other = dict(ok, name="PART3", status="needs_altium_models")
        fake2 = FakeNexar()
        fake2.find = lambda ws, name: None
        assert A.push([other], fake2, "https://ws")[0]["action"] == "skip"


def test_cli_and_report():
    with tempfile.TemporaryDirectory() as d:
        ok, held = make(d), make(d, name="PART2")
        held["verify"] = ["unchecked | pipe"]
        plan = Path(d) / "plan.json"
        plan.write_text(json.dumps([ok, held]))
        assert RC.main([str(plan)]) == 2
        items = {i["name"]: i for i in json.loads(plan.read_text())}
        assert items["PART2"]["status"] == "held" and items["PART1"]["status"] == "ready"
        md = (Path(d) / "release_check.md").read_text()
        assert "1 ready (0 by --allow), 1 held" in md and "unchecked / pipe" in md, md
        assert re.search(r"^name,status,result", (Path(d) / "release_check.csv").read_text())
        assert RC.main([str(plan), "--allow", "PART2", "-o", str(Path(d) / "r2")]) == 0
        assert json.loads(plan.read_text())[1]["allow"]["by"] == "--allow" and (Path(d) / "r2.md").exists()
        try:
            RC.main([str(plan), "--allow", "NOPE"])
            raise AssertionError("unknown part accepted")
        except SystemExit as e:
            assert "NOPE" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
