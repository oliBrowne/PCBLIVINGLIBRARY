"""Offline tests for ecadlib.dedupe: MPN normalisation, footprint geometry and symbol pinout fingerprints, keeper
choice, merge into the plan (library files untouched), the BOM mapping, and the push / release_check interplay.
Run:  python3 -m tests.test_dedupe"""
import contextlib, hashlib, io, json, sqlite3, tempfile
from pathlib import Path

from ecadlib import altium365 as A, altium_lib as AL, cli, dedupe as DD, kicad_import as K, release_check as RC
from tests.test_release_check import SOIC, make


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def test_mpn_key_packaging_suffixes():
    k = DD.mpn_key
    assert k("LM1117MPX-3.3/TR") == k("LM1117MPX-3.3") == "LM1117MPX3.3"   # the decimal point is kept (R-RU-1)
    assert k("AD8605ARTZ-REEL7") == k("AD8605ARTZ-R7") == k("AD8605ARTZ") == "AD8605ARTZ"
    assert k("LT1763CS8#TRPBF") == k("LT1763CS8#PBF") == k("LT1763CS8")
    assert k("IRLML6344TRPBF") == k("IRLML6344PBF") == k("IRLML6344")
    assert k("SM04B-SRSS-TB(LF)(SN)") == k("SM04B-SRSS-TB")
    assert k("BC847B,215") == k("BC847B") and k("ABC123 T&R") == k("ABC123")
    assert k("STM32F103C8T6-TR") == k("stm32f103c8t6")
    # no separator: TI's reel letters are kept (TPS62133RGTR and ...RGTT stay apart) unless configured
    assert k("TPS62133RGTR") != k("TPS62133RGTT") and k("CL05B104KO5NNNC") == "CL05B104KO5NNNC"
    assert k("TR") == "TR" and k("-REEL7") == "REEL7" and k("") == ""            # never stripped to nothing
    cfg = DD.load_config()
    cfg["patterns"].append(__import__("re").compile(r"(?<=RG)T[RT]$"))
    assert k("TPS62133RGTR", cfg) == k("TPS62133RGTT", cfg) == "TPS62133RG"
    with tempfile.TemporaryDirectory() as d:
        Path(d, "dedupe.json").write_text(json.dumps({"default_suffixes": False, "strip_suffixes": ["-X$"]}))
        cfg = DD.load_config(base=d)
        assert k("ABC123-X", cfg) == "ABC123" and k("ABC123/TR", cfg) == "ABC123TR"
        Path(d, "dedupe.json").write_text(json.dumps({"bogus": 1}))
        try:
            DD.load_config(base=d); assert False
        except ValueError:
            pass


def test_footprint_geometry_pcblib_equals_kicad():
    with tempfile.TemporaryDirectory() as d:
        fp, _ = K.convert_footprint(SOIC, "SOIC-8")
        AL.write_pcblib(Path(d, "a.PcbLib"), [fp])
        fp2, _ = K.convert_footprint(SOIC, "MY_SO8_RENAMED")
        AL.write_pcblib(Path(d, "b.PcbLib"), [fp2])
        ha = DD.fp_hash(DD.fp_geometry_pcblib(AL.read_pcblib(Path(d, "a.PcbLib"))[0]))
        hb = DD.fp_hash(DD.fp_geometry_pcblib(AL.read_pcblib(Path(d, "b.PcbLib"))[0]))
        hk = DD.fp_hash(DD.fp_geometry_kicad(SOIC))
        assert ha == hb == hk, "name must not matter; .PcbLib and .kicad_mod hash alike"
        moved = SOIC.replace("(at -2.4725 -1.905)", "(at -2.6 -1.905)", 1)
        assert moved != SOIC and DD.fp_hash(DD.fp_geometry_kicad(moved)) != hk
        g = DD.fp_geometry_kicad(SOIC)
        assert len(g["pads"]) == 8 and g["courtyard"] and DD._q(1.27499994) == DD._q(1.275)


def test_symbol_pinout():
    pins = [{"num": str(p), "name": f"IO{p}", "type": "io", "side": "left"} for p in range(1, 9)]
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d, "a.SchLib"), [AL.ic_symbol("PARTA", pins)])
        AL.write_schlib(Path(d, "b.SchLib"), [AL.ic_symbol("PARTB", pins[::-1])])
        AL.write_schlib(Path(d, "c.SchLib"), [AL.ic_symbol("PARTC", pins[:-1] + [{**pins[-1], "name": "GND"}])])
        AL.write_schlib(Path(d, "r.SchLib"), [AL.resistor_symbol()])
        AL.write_schlib(Path(d, "c2.SchLib"), [AL.capacitor_symbol()])
        po = {n: DD.sym_pinout(AL.read_schlib(Path(d, f"{n}.SchLib"))[1][0]) for n in ("a", "b", "c", "r", "c2")}
        assert po["a"][0] == "U" and len(po["a"][1]) == 8
        assert DD.sym_hash(po["a"]) == DD.sym_hash(po["b"]) != DD.sym_hash(po["c"])
        assert DD.sym_hash(po["r"]) is None and DD.sym_hash(po["c2"]) is None       # 2-pin: not compared by default
        assert DD.sym_hash(po["r"], 2) != DD.sym_hash(po["c2"], 2)                   # R? vs C? differ anyway


def _setup(d):
    """Plan: PART1, PART1/TR (same MPN, other name, own footprint file under another name with identical geometry),
    PART2 (other part, own symbol identical to PART1's). Library: a .DbLib + SQLite row, a .PcbLib copy."""
    d = Path(d)
    a = make(d)
    b = make(d, name="PART1-TR", fp_name="SO8_COPY")
    b["parameters"] = [p if p["name"] != "Manufacturer Part Number" else {**p, "value": "PART1/TR"} for p in b["parameters"]]
    c = make(d, name="PART2", fp_name="SOIC-8_3.9x4.9mm_P1.27mm")
    c["parameters"] = [p if p["name"] != "Manufacturer Part Number" else {**p, "value": "PART2"} for p in c["parameters"]]
    lib = d / "lib"; lib.mkdir()
    fp, t = K.convert_footprint(SOIC, "COMPANY_SOIC8")
    K.add_body(fp, t, None, None)                             # with a 3D body, so the merged items still pass the release check
    AL.write_pcblib(lib / "COMPANY_SOIC8.PcbLib", [fp])
    with contextlib.closing(sqlite3.connect(lib / "parts.sqlite")) as con, con:   # closed: Windows locks an open file
        con.execute('create table ICs ("Part Number", "Manufacturer Part Number", "Library Ref", "Footprint Ref")')
        con.execute("insert into ICs values ('ACME-0002', 'PART2-REEL7', 'PART2', 'SOIC-8')")
        con.execute("insert into ICs values ('PART1', 'PART1', 'PART1', 'SOIC-8')")    # same part as the plan item
    (lib / "parts.DbLib").write_text("[DatabaseLinks]\r\nConnectionString=Provider=MSDASQL.1;Driver=SQLite3 ODBC Driver;"
                                     "Database=C:\\ecadlib\\altium\\parts.sqlite\r\n")
    plan = d / "plan.json"
    plan.write_text(json.dumps([a, b, c], indent=2))
    return plan, lib


def test_groups_keeper_and_report():
    with tempfile.TemporaryDirectory() as d:
        plan, lib = _setup(d)
        r = _quiet(DD.run, plan, [lib / "parts.DbLib"])
        g = {(x["kind"], x["keeper"]): x for x in r["groups"]}
        mpn = {x["key"]: x for x in r["groups"] if x["kind"] == "mpn"}
        assert set(mpn) == {"PART1", "PART2"}
        assert mpn["PART1"]["keeper"] == "PART1" and [m["name"] for m in mpn["PART1"]["members"]] == ["PART1", "PART1-TR"]
        assert mpn["PART2"]["keeper"] == "ACME-0002"                # the existing library row wins over a new plan item
        fpg = [x for x in r["groups"] if x["kind"] == "footprint"]
        assert len(fpg) == 1 and fpg[0]["keeper"] == "SOIC-8_3.9x4.9mm_P1.27mm"  # used by 2 plan items
        assert {m["name"] for m in fpg[0]["members"]} == {"SOIC-8_3.9x4.9mm_P1.27mm", "SO8_COPY"}
        sym = [x for x in r["groups"] if x["kind"] == "symbol"]
        assert len(sym) == 1 and {m["name"] for m in sym[0]["members"]} == {"PART1", "PART1-TR", "PART2"}
        assert not [x for x in r["groups"] if x["kind"].endswith("conflict")]
        md = r["report"].read_text()
        assert "keep **PART1**" in md and "ACME-0002" in md and r["map"].exists()
        assert json.loads(plan.read_text())[1]["status"] == "ready"          # report only without --merge
        r = _quiet(DD.run, plan, [lib])                             # whole folder: the .PcbLib joins the footprint group
        fpg = [x for x in r["groups"] if x["kind"] == "footprint"][0]
        assert fpg["keeper"] == "COMPANY_SOIC8" and len(fpg["members"]) == 3
        r = _quiet(DD.run, plan, [lib], keep=["SO8_COPY"])
        assert [x for x in r["groups"] if x["kind"] == "footprint"][0]["keeper"] == "SO8_COPY"


def _sign_off(plan):
    """A merge that changes a released part's 3D body (the keeper's file has another, or none) is held for sign-off
    (R-FL-5, review of #196): a person approves the held merges. -> the parts approved."""
    from ecadlib import change_cli as CC
    held = [i["name"] for i in json.loads(Path(plan).read_text()) if (i.get("change") or {}).get("state") == "held"]
    if held:
        CC.approve(plan, held, who="Ann Lee <ann@example.com>")
    return held


def test_merge_rewrites_plan_never_library():
    with tempfile.TemporaryDirectory() as d:
        plan, lib = _setup(d)
        before = {p: _sha(p) for p in Path(d).rglob("*.*") if p.suffix in (".PcbLib", ".SchLib", ".sqlite", ".DbLib")}
        bom = Path(d, "bom.csv")
        bom.write_text("Designator,Comment,LibRef,Footprint,Manufacturer Part Number\nU1,PART1,PART1-TR,SO8_COPY,PART1/TR\n"
                       "U2,PART2,PART2,Lib:SO8_COPY,PART2\n")
        r = _quiet(DD.run, plan, [lib], do_merge=True, kinds=("mpn", "footprint"), bom=bom)
        assert _sign_off(plan)
        items = {i["name"]: i for i in json.loads(plan.read_text())}
        assert Path(str(plan) + ".pre-dedupe").exists()
        assert items["PART1-TR"]["status"] == "duplicate" and items["PART1-TR"]["duplicate_of"] == "PART1"
        assert items["PART2"]["duplicate_of"] == "ACME-0002" and items["PART1"]["status"] == "ready"
        for n in ("PART1", "PART2", "PART1-TR"):                    # every footprint now points at the library keeper
            assert items[n]["footprint"]["ref"] == "COMPANY_SOIC8" and items[n]["footprint"]["file"] == "lib/COMPANY_SOIC8.PcbLib"
            assert "kicad_mod" not in items[n]["footprint"] and items[n]["merged_from"]["footprint"]["ref"]
        assert items["PART2"]["symbol"]["ref"] == "PART2"           # symbols were not in --kinds
        assert before == {p: _sha(p) for p in before}, "library and model files must not change"
        assert all(p.exists() for p in before)                      # nothing deleted
        mp = r["map"].read_text()
        assert "part,PART1-TR,PART1" in mp and "footprint,SO8_COPY,COMPANY_SOIC8" in mp
        out, n = r["bom"]
        rows = out.read_text().splitlines()
        assert n == 2 and rows[1].split(",")[3] == "COMPANY_SOIC8" and rows[2].split(",")[3] == "Lib:COMPANY_SOIC8"
        assert rows[1].split(",")[4] == "PART1/TR"                  # the MPN purchasing orders is left alone
        # release_check keeps the duplicate status; push skips it; the merged footprint still passes
        lst = list(items.values())
        RC.apply(lst, d)
        st = {i["name"]: i["status"] for i in lst}
        assert st == {"PART1": "ready", "PART1-TR": "duplicate", "PART2": "duplicate"}, st
        for i in lst:
            for k in ("symbol", "footprint"):
                i[k]["file"] = str((Path(d) / i[k]["file"]).resolve())
        res = {x["name"]: x for x in A.push(lst, None, "https://ws", dry_run=True)}
        assert res["PART1"]["action"] == "release" and res["PART1-TR"]["detail"].startswith("duplicate of PART1")
        r2 = _quiet(DD.run, plan, [lib], do_merge=True)             # idempotent: nothing left to rewrite
        assert not [m for m in r2["mapping"] if m["parts"] and m["kind"] == "footprint"]


def test_merge_keeper_files_and_conflicts():
    with tempfile.TemporaryDirectory() as d:
        plan, lib = _setup(d)
        r = _quiet(DD.run, plan, do_merge=True, kinds=("footprint",))     # keeper is the plan's own SOIC-8 footprint
        _sign_off(plan)
        it = {i["name"]: i for i in json.loads(plan.read_text())}["PART1-TR"]
        assert it["footprint"] == {"ref": "SOIC-8_3.9x4.9mm_P1.27mm", "file": "models/PcbLib/SOIC-8_3.9x4.9mm_P1.27mm.PcbLib",
                                   "kicad_mod": "models/KiCad/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod"}
        assert it["merged_from"]["footprint"]["ref"] == "SO8_COPY" and it["status"] == "ready"
    with tempfile.TemporaryDirectory() as d:
        plan, lib = _setup(d)
        kl = Path(d, "klib"); kl.mkdir()
        (kl / "AAA_SO8.kicad_mod").write_text(SOIC)                       # a .kicad_mod-only keeper (shortest name wins)
        (kl / "SO8_COPY.kicad_mod").write_text(SOIC.replace("(at -2.4725 -1.905)", "(at -2.6 -1.905)", 1))
        Path(d, "dedupe.json").write_text(json.dumps({"keep": ["AAA_SO8"]}))
        r = _quiet(DD.run, plan, [kl], do_merge=True, kinds=("footprint",))
        assert all(m["note"].startswith("keeper has no .PcbLib") for m in r["mapping"] if m["kind"] == "footprint")
        conf = [g for g in r["groups"] if g["kind"] == "footprint_conflict"]
        assert len(conf) == 1 and conf[0]["key"] == "SO8_COPY" and len(conf[0]["members"]) == 2


def test_a365_state_and_cli():
    with tempfile.TemporaryDirectory() as d:
        plan, lib = _setup(d)
        Path(d, "a365_state.json").write_text(json.dumps({"PART1-REEL": {"revision": "r1"}, "PART1": {"revision": "r2"}}))
        r = _quiet(DD.run, plan)
        g = [x for x in r["groups"] if x["kind"] == "mpn"][0]
        assert g["keeper"] == "PART1" and {m["name"] for m in g["members"]} == {"PART1", "PART1-TR", "PART1-REEL"}
        assert [m["origin"] for m in g["members"]][0] == "a365"     # released component outranks the plan copy
        nodes = [{"name": "WS-7", "details": {"parameters": [{"name": "Manufacturer Part Number", "value": "PART2 T&R"}]}}]
        Path(d, "nodes.json").write_text(json.dumps(nodes))
        r = _quiet(DD.run, plan, a365=Path(d, "nodes.json"))
        assert [x["keeper"] for x in r["groups"] if x["kind"] == "mpn" and x["key"] == "PART2"] == ["WS-7"]
        assert _quiet(cli.main, ["dedupe", str(plan), "--lib", str(lib), "-o", str(Path(d, "rep", "dd"))]) == 1
        assert Path(d, "rep", "dd.md").exists() and Path(d, "rep", "dd_map.csv").exists()
        assert _quiet(cli.main, ["dedupe", str(plan), "--lib", str(Path(d, "nope"))]) == 2
        Path(d, "a365_state.json").unlink()
        one = Path(d, "one"); one.mkdir()
        p1 = one / "plan.json"; p1.write_text(json.dumps([make(one)]))
        assert _quiet(cli.main, ["dedupe", str(p1)]) == 0


def test_merge_survives_librarian_rerun():
    """A footprint merged onto its keeper stays merged when the librarian runs again (was: reverted, merged_from lost).
    The keeper is the same footprint in an existing library under the library's own name. The fixture's LED_0603 is
    R_0603's drawing renamed: the resistor is merged, the LED (another kind of part) keeps its own footprint."""
    from tests import test_librarian as TL
    with tempfile.TemporaryDirectory() as d:
        TL.run(d, sources=["kicad"])
        out = Path(d) / "out"
        lib = Path(d) / "house"
        lib.mkdir()
        fp, _ = K.convert_footprint((out / "models" / "KiCad" / "R_0603_1608Metric.kicad_mod").read_text(encoding="utf-8"),
                                    "HOUSE_R_0603")
        AL.write_pcblib(lib / "HOUSE_R_0603.PcbLib", [fp])
        r = DD.run(out / "plan.json", libs=[lib], do_merge=True, log=lambda *a: None)
        _sign_off(out / "plan.json")
        assert [(m["parts"], m["to"]) for m in r["mapping"] if m["kind"] == "footprint"] == [("RES-0603-10k", "HOUSE_R_0603")], \
            r["mapping"]
        items, _ = TL.run(d, sources=["kicad"])
        res = items["RES-0603-10k"]
        assert res["footprint"]["ref"] == "HOUSE_R_0603", res["footprint"]
        assert res["merged_from"]["footprint"]["ref"] == "R_0603_1608Metric"
        led = items["LED-RED-0603"]
        assert led["footprint"]["ref"] == "LED_0603_1608Metric" and not led.get("merged_from"), led


def test_led_and_resistor_footprints_with_the_same_pads_stay_apart():
    """An LED_0603 with KiCad's cathode mark and R_0603 have the same pads and courtyard. The mark makes the drawing
    turn into another one at 180 degrees, so it is a different footprint (was: the LED was merged onto R_0603, losing
    its polarity mark, or an inductor onto LED_0603, taking the mark and the LED's 3D body). The .PcbLib and the
    .kicad_mod agree; without a mark, parts of different kinds (plan folders) or 3D heights are still kept apart."""
    from tests import test_librarian as TL
    with tempfile.TemporaryDirectory() as d:
        TL.run(d, sources=["kicad"])
        out = Path(d) / "out"
        r_text = (out / "models" / "KiCad" / "R_0603_1608Metric.kicad_mod").read_text(encoding="utf-8")
        led_text = r_text.replace("R_0603_1608Metric", "LED_0603_1608Metric").replace(
            '  (fp_rect (start -1.46', '  (fp_line (start -1.3 -0.6) (end -1.3 0.6) (stroke (width 0.12) (type solid)) '
            '(layer "F.SilkS"))\n  (fp_rect (start -1.46', 1)
        assert led_text.count("fp_line") == r_text.count("fp_line") + 1
        g = {}
        for n, text in (("LED", led_text), ("R", r_text)):
            fp, _ = K.convert_footprint(text, n)
            AL.write_pcblib(Path(d, f"{n}.PcbLib"), [fp])
            gk, gp = DD.fp_geometry_kicad(text), DD.fp_geometry_pcblib(AL.read_pcblib(Path(d, f"{n}.PcbLib"))[0])
            assert gk["oriented"] == gp["oriented"] and DD.fp_hash(gk) == DD.fp_hash(gp), n
            g[n] = gk
        assert g["LED"]["oriented"] and not g["R"]["oriented"] and g["LED"]["pads"] == g["R"]["pads"]
        assert DD.fp_hash(g["LED"]) != DD.fp_hash(g["R"])
        # the fixture's LED is R_0603's drawing (no mark): its part is a diode, the other a resistor -> not merged
        r = DD.run(out / "plan.json", log=lambda *a: None)
        assert not [x for x in r["groups"] if x["kind"] == "footprint"], [(x["keeper"], [m["name"] for m in x["members"]])
                                                                         for x in r["groups"]]
    a = {"part_kinds": ["Components/Inductors"], "height": 0.8}
    assert not DD._fits(a, {"part_kinds": ["Components/Diodes"], "height": 0.8})        # another kind of part
    assert not DD._fits(a, {"part_kinds": ["Components/Inductors"], "height": 0.6})     # another 3D body
    assert DD._fits(a, {"part_kinds": [], "height": None}) and DD._fits(a, {"part_kinds": ["Components/Inductors"], "height": 0.82})
    parts = DD._compatible([{"ref": "C", **a}, {"ref": "L", **a}, {"ref": "D", "part_kinds": ["Components/Diodes"], "height": 0.8}])
    assert [[m["ref"] for m in p] for p in parts] == [["C", "L"], ["D"]]


def test_house_courtyard_layer_hashes_like_kicad_mod():
    """With ecadlib.toml putting the courtyard on another layer, a PcbLib and its .kicad_mod still hash alike
    (was: no courtyard read from the PcbLib -> false 'same name, different geometry' conflicts)."""
    from tests import test_librarian as TL
    with tempfile.TemporaryDirectory() as d:
        Path(d, "out").mkdir()
        Path(d, "out", "ecadlib.toml").write_text('[layers]\ncourtyard = "Mechanical11"\n')
        TL.run(d, sources=["kicad"])
        out = Path(d) / "out"
        r = DD.run(out / "plan.json", libs=[out / "models" / "KiCad"], log=lambda *a: None)
        assert not [g for g in r["groups"] if g["kind"].endswith("_conflict")], [(g["kind"], g["key"]) for g in r["groups"]]


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_dedupe"]))
