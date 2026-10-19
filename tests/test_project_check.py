"""Project drift and freeze (#105 phase 3): a KiCad project's cached symbols and placed footprints against the library,
`project freeze`, `project scan` and the Altium 365 out-of-date list. The KiCad project is made here: a library, a
schematic that caches its symbol, and a board that places its footprint rotated and on the bottom the way KiCad's
FOOTPRINT::Flip does (children mirrored top to bottom in the footprint's frame, pad angles negated, layers swapped).
Run:  python3 -m tests.test_project_check"""
import json, math, random, tempfile
from pathlib import Path

from ecadlib import change_diff as CD, fingerprint as FP, project_check as PJ, sexpr

LIB_SYM = """(kicad_symbol_lib (version 20241209) (generator "test")
  (symbol "QN" (pin_names (offset 0.254)) (in_bom yes) (on_board yes)
    (property "Reference" "Q" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "QN" (at 0 -2 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "TFp:SOT23" (at 0 -4 0) (effects (font (size 1.27 1.27)) (hide yes)))
    (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
    (symbol "QN_0_1" (polyline (pts (xy 0 1) (xy 0 -1)) (stroke (width 0.254) (type default)) (fill (type none))))
    (symbol "QN_1_1"
      (pin input line (at -5.08 0 0) (length 2.54) (name "G" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      (pin passive line (at 2.54 -5.08 90) (length 2.54) (name "S" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      (pin passive line (at 2.54 5.08 270) (length 2.54) (name "D" (effects (font (size 1.27 1.27)))) (number "3" (effects (font (size 1.27 1.27)))))))
)
"""


def lib_fp(name="SOT23", p3=(1.0, 0.0), pads=None):
    pads = pads or [("1", -1.0, -0.95, 0.9, 0.8, 0), ("2", -1.0, 0.95, 0.9, 0.8, 0), ("3", p3[0], p3[1], 0.9, 0.8, 0)]
    pad_s = "".join(f'(pad "{n}" smd roundrect (at {x} {y}{f" {a}" if a else ""}) (size {w} {h}) '
                    f'(layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))' for n, x, y, w, h, a in pads)
    return (f'(footprint "{name}" (version 20241229) (generator "test") (layer "F.Cu") '
            f'(property "Reference" "REF**" (at 0 -2.4 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15)))) '
            f'(property "Value" "{name}" (at 0 2.4 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15)))) '
            f'(fp_line (start -0.7 -1.5) (end 0.7 -1.5) (stroke (width 0.12) (type solid)) (layer "F.SilkS")) '
            f'(fp_line (start -1.6 -1.6) (end -0.5 -1.6) (stroke (width 0.12) (type solid)) (layer "F.SilkS")) '
            f'(fp_rect (start -1.9 -1.7) (end 1.9 1.7) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd")) '
            f'{pad_s})')


def place(fp_text, ref, x, y, rot, back):
    """A library footprint placed on a board as KiCad writes it: positions stay in the footprint's frame (mirrored top
    to bottom when on the back), pad and text angles become absolute, layers B.* on the back."""
    t = sexpr.parse(fp_text, keep_quotes=True)
    out = ["body"]

    def walk(n):
        for k, c in enumerate(n):
            if isinstance(c, str):
                if k and n[0] in ("layer", "layers") and back and c[:2] in ("F.", "B."):
                    n[k] = sexpr.Q(("B." if c[0] == "F" else "F.") + c[2:])
                continue
            if c and c[0] in ("start", "end", "mid", "center", "xy") and back:
                c[2] = f"{-float(c[2]):g}"
            elif c and c[0] == "at":
                if back:
                    c[2] = f"{-float(c[2]):g}"
                a = float(c[3]) if len(c) > 3 else 0.0
                a = rot + (-a if back else a)
                c[3:] = [f"{a:g}"] if a else []
            walk(c)
    for c in t[2:]:
        if isinstance(c, list) and c and c[0] in ("version", "generator", "layer"):
            continue
        if isinstance(c, list) and c and c[0] == "property" and str(c[1]) == "Reference":
            c[2] = sexpr.Q(ref)
        out.append(c)
    walk(out)
    return ["footprint", sexpr.Q("TFp:" + str(t[1])), ["layer", sexpr.Q("B.Cu" if back else "F.Cu")],
            ["at", str(x), str(y)] + ([str(rot)] if rot else [])] + out[1:]


def make_project(d, rot_back=(90, 30)):
    """lib/TSym.kicad_sym + lib/TFp.pretty/SOT23.kicad_mod, and board.kicad_pro / .kicad_sch / .kicad_pcb using them:
    Q1 on the top at 90 degrees, Q2 on the bottom at 30 degrees."""
    d = Path(d)
    (d / "lib" / "TFp.pretty").mkdir(parents=True)
    (d / "lib" / "TSym.kicad_sym").write_text(LIB_SYM)
    fp = lib_fp()
    (d / "lib" / "TFp.pretty" / "SOT23.kicad_mod").write_text(fp)
    (d / "board.kicad_pro").write_text("{}")
    (d / "sym-lib-table").write_text('(sym_lib_table (version 7)\n  (lib (name "TSym")(type "KiCad")(uri "${KIPRJMOD}/lib/TSym.kicad_sym")(options "")(descr ""))\n)\n')
    (d / "fp-lib-table").write_text('(fp_lib_table (version 7)\n  (lib (name "TFp")(type "KiCad")(uri "${KIPRJMOD}/lib/TFp.pretty")(options "")(descr ""))\n)\n')
    sym = sexpr.parse(LIB_SYM, keep_quotes=True)[3]
    cached = sexpr.parse(sexpr.dumps(sym), keep_quotes=True)
    cached[1] = sexpr.Q("TSym:QN")
    inst = lambda ref, y: (f'(symbol (lib_id "TSym:QN") (at 50 {y} 0) (unit 1) (in_bom yes) (on_board yes) (uuid "u-{ref}") '
                           f'(property "Reference" "{ref}" (at 52 {y} 0)) (property "Value" "QN" (at 52 {y + 2} 0)) '
                           f'(property "Footprint" "TFp:SOT23" (at 50 {y} 0) (hide yes)) '
                           f'(instances (project "board" (path "/root" (reference "{ref}") (unit 1)))))')
    (d / "board.kicad_sch").write_text(f'(kicad_sch (version 20250114) (generator "test") (uuid "root") (paper "A4") '
                                       f'(lib_symbols {sexpr.dumps(cached)}) {inst("Q1", 50)} {inst("Q2", 60)})\n')
    board = ["kicad_pcb", ["version", "20241229"], ["generator", sexpr.Q("test")],
             place(fp, "Q1", 100, 50, rot_back[0], False), place(fp, "Q2", 120, 50, rot_back[1], True)]
    (d / "board.kicad_pcb").write_text(sexpr.dumps(board) + "\n")
    return d / "board.kicad_pro"


def states(res):
    return {(r["kind"], r["id"], tuple(r["refs"])): (r["state"], r["action"]) for r in res["rows"]}


def test_unplace_undoes_rotation_and_flip_property():
    """1,000 random footprints placed at random angles on either side read back exactly as the library part."""
    rnd = random.Random(105)
    for case in range(1000):
        n = rnd.randint(2, 12)
        pads = [(str(k + 1), round(rnd.uniform(-5, 5), 3), round(rnd.uniform(-5, 5), 3), round(rnd.uniform(0.2, 2), 3),
                 round(rnd.uniform(0.2, 2), 3), rnd.choice((0, 0, 90, 45, 180, -30))) for k in range(n)]
        text = lib_fp("P", pads=pads)
        lib = FP.build("P", None, *FP.kicad_footprint(text), tool="kicad")
        rot, back = rnd.choice((0, 90, -90, 180, 30, 12.5, -135)), rnd.random() < 0.5
        got = FP.build("P", None, *FP.kicad_footprint(FP.unplace(place(text, "U1", 10, 20, rot, back))), tool="kicad")
        ch = [c for c in CD.diff(lib, got) if c["class"] != "cosmetic"]
        assert not ch, (case, rot, back, ch[:3])


def test_kicad_project_matches_its_library_then_reports_drift():
    with tempfile.TemporaryDirectory() as d:
        pro = make_project(d)
        res = PJ.check_kicad(pro, use_global=False)
        assert states(res) == {("symbol", "TSym:QN", ("Q1", "Q2")): ("same", "pass"),
                               ("footprint", "TFp:SOT23", ("Q1", "Q2")): ("same", "pass")}, res["rows"]
        # the library is updated: G and D swap names, pad 3 moves 0.3 mm
        lib = Path(d) / "lib"
        (lib / "TSym.kicad_sym").write_text(LIB_SYM.replace('(name "G"', '(name "X"').replace('(name "D"', '(name "G"')
                                            .replace('(name "X"', '(name "D"'))
        (lib / "TFp.pretty" / "SOT23.kicad_mod").write_text(lib_fp(p3=(1.3, 0.0)))
        res = PJ.check_kicad(pro, use_global=False)
        rows = {r["kind"]: r for r in res["rows"]}
        assert rows["symbol"]["worst"] == "electrical" and rows["symbol"]["action"] == "hold", rows["symbol"]
        assert any("pin 1" in c["text"] and "G" in c["text"] for c in rows["symbol"]["changes"]), rows["symbol"]["changes"]
        assert rows["footprint"]["worst"] == "geometry-major" and rows["footprint"]["refs"] == ["Q1", "Q2"]
        assert any(c["text"] == "pad 3 moved 0.3 mm in x" for c in rows["footprint"]["changes"]), rows["footprint"]["changes"]
        assert res["held"] == 2
        assert PJ.main(["check", str(pro), "--no-global", "-o", str(Path(d) / "out" / "drift")]) == 1
        md = (Path(d) / "out" / "drift.md").read_text()
        assert "`TSym:QN`" in md and "pad 3 moved 0.3 mm in x" in md
        # a library that is gone, and a part that is gone
        (lib / "TFp.pretty" / "SOT23.kicad_mod").unlink()
        rows = {r["kind"]: r for r in PJ.check_kicad(pro, use_global=False)["rows"]}
        assert rows["footprint"]["state"] == "not in library" and "no longer in library TFp" in rows["footprint"]["why"]


def test_freeze_points_the_project_at_its_own_copies():
    with tempfile.TemporaryDirectory() as d:
        pro = make_project(d)
        before = {f: (Path(d) / f).read_text() for f in ("board.kicad_sch", "board.kicad_pcb")}
        dry = PJ.freeze_kicad(pro, dry_run=True)
        assert dry["symbols"] == {"TSym:QN": "board-frozen:QN"} and dry["footprints"] == {"TFp:SOT23": "board-frozen:SOT23"}
        assert {f: (Path(d) / f).read_text() for f in before} == before and not (Path(d) / "board-frozen.kicad_sym").exists()
        res = PJ.freeze_kicad(pro)
        assert (Path(d) / "board-frozen.kicad_sym").is_file() and (Path(d) / "board-frozen.pretty" / "SOT23.kicad_mod").is_file()
        assert res["backup"] and sorted(p.name for p in Path(res["backup"]).iterdir()) == \
            ["board.kicad_pcb", "board.kicad_sch", "fp-lib-table", "sym-lib-table"]
        sch = (Path(d) / "board.kicad_sch").read_text()
        assert '"board-frozen:QN"' in sch and '"TSym:QN"' not in sch and '"board-frozen:SOT23"' in sch
        assert '"board-frozen:SOT23"' in (Path(d) / "board.kicad_pcb").read_text()
        assert "${KIPRJMOD}/board-frozen.pretty" in (Path(d) / "fp-lib-table").read_text()
        # the frozen footprint is the library footprint, not a placed copy
        frozen = FP.build("SOT23", None, *FP.kicad_footprint((Path(d) / "board-frozen.pretty" / "SOT23.kicad_mod").read_text()),
                          tool="kicad")
        lib = FP.build("SOT23", None, *FP.kicad_footprint(lib_fp()), tool="kicad")
        assert [c for c in CD.diff(lib, frozen) if c["class"] != "cosmetic"] == []
        # the shared library changes; the frozen project does not see it
        (Path(d) / "lib" / "TFp.pretty" / "SOT23.kicad_mod").write_text(lib_fp(p3=(1.3, 0.0)))
        res = PJ.check_kicad(pro, use_global=False)
        assert {r["state"] for r in res["rows"]} == {"same"} and res["held"] == 0, res["rows"]
        assert PJ.freeze_kicad(pro, dry_run=True)["symbols"] == {"board-frozen:QN": "board-frozen:QN"}


class FakeNexar:
    def __init__(self, latest):
        self.latest, self.asked = latest, []

    def find(self, ws, name):
        self.asked.append(name)
        rid = self.latest.get(name)
        return {"id": f"id-{name}", "name": name, "revision": {"revisionId": rid}} if rid else None


def test_altium_out_of_date_list_with_the_ledger_diff():
    from unittest import mock
    from ecadlib import altium_project as AP, change_gate as CG, ledger as LG
    from tests.test_change_control import demo, gate, item, items_of
    from ecadlib import altium_lib as AL, change_cli as CC, stdparts as SP
    plan, lib = demo()
    items = items_of(plan)
    g = gate(lib)
    it = item(items, "RES-0603-10k")
    text, _ = SP.build("R_0402_1005Metric")
    AL.write_pcblib(lib / "models" / "PcbLib" / "R_0402_1005Metric.PcbLib", [AL.footprint_from_kicad(text)])
    (lib / "models" / "KiCad" / "R_0402_1005Metric.kicad_mod").write_text(text, encoding="utf-8")
    it["footprint"] = {"ref": "R_0402_1005Metric", "file": "models/PcbLib/R_0402_1005Metric.PcbLib",
                       "kicad_mod": "models/KiCad/R_0402_1005Metric.kicad_mod"}
    g.settle(items)
    CG.write_plan(plan, items)
    CC.approve(plan, ["RES-0603-10k"], who="Ann <ann@x.com>")
    target = "altium365:https://ws"
    lg = LG.Ledger(lib)
    for rev, rid in ((1, "{AAAA-1}"), (2, "{AAAA-2}")):
        lg.append({"event": "publish", "part": "RES-0603-10k", "target": target, "revision": rev,
                   "detail": {"action": "release", "a365_revision": rid}})
    comps = [{"designator": "R1", "design_item_id": "RES-0603-10k", "vault_guid": "v", "item_guid": "i", "revision_guid": "{AAAA-1}",
              "libref": "RES"},
             {"designator": "R2", "design_item_id": "RES-0603-10k", "vault_guid": "v", "item_guid": "i", "revision_guid": "aaaa-1",
              "libref": "RES"},
             {"designator": "C1", "design_item_id": "CAP-1", "vault_guid": "v", "item_guid": "j", "revision_guid": "{BBBB}",
              "libref": "CAP"},
             {"designator": "U1", "design_item_id": "GONE", "vault_guid": "v", "item_guid": "k", "revision_guid": "{CCCC}",
              "libref": "U"},
             {"designator": "J1", "design_item_id": "LOCAL", "revision_guid": "", "libref": "J"}]
    nx = FakeNexar({"RES-0603-10k": "AAAA-2", "CAP": "BBBB"})
    with mock.patch.object(AP, "load_project", lambda p, rules=None: {"path": p, "components": comps}):
        res = PJ.check_altium("board.PrjPcb", nx, "https://ws", lib)
    rows = {r["id"]: r for r in res["rows"]}
    assert rows["RES-0603-10k"]["state"] == "out of date" and rows["RES-0603-10k"]["refs"] == ["R1", "R2"], rows
    assert rows["RES-0603-10k"]["why"] == "ledger r1 -> r2" and rows["RES-0603-10k"]["action"] == "hold"
    assert any("R_0603_1608Metric to R_0402_1005Metric" in c["text"] for c in rows["RES-0603-10k"]["changes"])
    assert rows["CAP"]["state"] == "same" and rows["GONE"]["state"] == "not found" and "LOCAL" not in rows
    assert res["out_of_date"] == 1 and res["held"] == 1 and nx.asked.count("RES-0603-10k") == 1


def test_scan_records_which_parts_a_bom_uses():
    from ecadlib import ledger as LG
    from tests.test_change_control import demo
    plan, lib = demo()
    bom = lib.parent.parent / "uploads" / "demo" / "demo_bom.csv"
    res = PJ.scan(bom, lib)
    assert res["parts"]["RES-0603-10k"] == {"revision": 1, "designators": ["R1", "R2", "R3"]}, res["parts"]
    assert res["parts"]["CH340C"]["revision"] is None and not res["not_in_library"], res    # held: never released
    row = LG.load_usage(lib)["projects"][res["project"]]
    assert row["tool"] == "bom" and set(row["parts"]) == set(res["parts"])
    name, rec = next(iter(res["parts"].items()))
    assert rec["designators"] and LG.used_in(lib, name)[0][0] == res["project"]


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_project_check"]))
