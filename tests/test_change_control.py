"""Change control (#105): no library update can silently change a released part.

Real regressions as fixtures (KiCad library commits that swapped diode pin names, one that only added Sim.Pins; a
footprint going 0603 -> 0402 and back; a SOT-23 symbol remapped GSD -> SGD), seeded property tests (cosmetic edits
never hold; pin <-> pad permutations, pin renames and pad moves always hold), cross-tool identity (the same part read
from KiCad and from Altium has the same eid / gid), the gate in every writer (an AST check), and an Altium 365 mock
(approved = a new component revision, held = nothing new sent)."""
import ast, copy, json, random, shutil, subprocess, sys, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, change_cli as CC, change_diff as CD, change_gate as CG, fingerprint as FP
from ecadlib import kicad_import as K, ledger as LG, sexpr, stdparts as SP

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "change_control"
GOLDEN = ROOT / "tests" / "fixtures" / "kicad_golden"
ANN, BOB = "Ann Lee <ann@example.com>", "Bob Ray <bob@example.com>"
_DEMO = {}


# ---------------------------------------------------------------- helpers
def demo():
    """A copy of the offline demo library (built once per run): its plan, its folder."""
    if "dir" not in _DEMO:
        d = Path(tempfile.mkdtemp(prefix="ecadlib-cc-demo-"))
        subprocess.run([sys.executable, "-m", "ecadlib", "demo", "-o", str(d)], cwd=ROOT, check=True,
                       capture_output=True, timeout=300)
        _DEMO["dir"] = d / "librarian" / "demo_bom_demo"
    d = Path(tempfile.mkdtemp(prefix="ecadlib-cc-")) / "demo"
    shutil.copytree(_DEMO["dir"].parent.parent, d)          # the whole demo: the lock's BOM path stays valid
    lib = d / "librarian" / "demo_bom_demo"
    return lib / "plan.json", lib


def items_of(plan):
    return json.loads(Path(plan).read_text(encoding="utf-8"))


def item(items, name):
    return next(i for i in items if i["name"] == name)


def kicad_syms(path):
    lib = sexpr.parse(Path(path).read_text(encoding="utf-8"))
    syms = {str(s[1]): s for s in sexpr.find(lib, "symbol")}
    out = {}
    for n, s in syms.items():
        chain = [s]
        while sexpr.first(chain[-1], "extends"):
            chain.append(syms[str(sexpr.first(chain[-1], "extends")[1])])
        out[n] = FP.of_kicad(chain, part=n)
    return out


def texts(changes):
    return [c["text"] for c in changes]


def gate(lib, writer="librarian", **kw):
    g = CG.Guard(lib, writer, **kw)
    g.begin(items_of(lib / "plan.json"))
    return g


def write_symbol(lib, it, pins):
    """Rewrite the part's .SchLib with these (num, name, type) pins, as an upstream update would."""
    sym = AL.ic_symbol(it["symbol"]["ref"], [{"num": n, "name": nm, "type": t, "side": "left" if k % 2 == 0 else "right"}
                                             for k, (n, nm, t) in enumerate(pins)], layout=False)
    AL.write_schlib(lib / it["symbol"]["file"], [sym])


# ---------------------------------------------------------------- real regressions
def test_kicad_commit_that_swapped_dual_diode_pin_names_is_held():
    """kicad-symbols 177f3614596c "Fix dual diode AAK pin names": a library update that renamed pins of a released
    symbol. Every renamed pin is named in plain words and the change is held."""
    old, new = kicad_syms(FIX / "dual_diode_aak_old.kicad_sym"), kicad_syms(FIX / "dual_diode_aak_new.kicad_sym")
    ch = CD.diff(old["D_Schottky_Dual_CommonCathode_AAK_Parallel"], new["D_Schottky_Dual_CommonCathode_AAK_Parallel"])
    assert CD.worst(ch) == "electrical", ch
    t = texts(ch)
    assert "pin 1 was K now A" in t and "pin 2 was K now A" in t and "pin 3 was A now K" in t, t
    assert CG.decide(ch, CG.POLICY)["action"] == "hold"
    split = CD.diff(old["D_Schottky_Dual_CommonCathode_AAK_Split"], new["D_Schottky_Dual_CommonCathode_AAK_Split"])
    assert [c["text"] for c in split if c["class"] == "electrical"] == ["pin 2 was K now A"], split
    # even a policy that lets geometry and cosmetics through cannot let electrical through
    loose = CG.normalize_policy({"geometry_major": "pass", "geometry_minor": "pass", "electrical": "warn"})
    assert CG.decide(ch, loose)["action"] == "warn"
    for bad in ({"electrical": "pass"}, {"identity": "pass"}, {"approvers": 0}, {"nope": "hold"}):
        try:
            CG.normalize_policy(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} accepted")


def test_kicad_commit_that_only_added_sim_pins_passes():
    """kicad-symbols 051843b6abaf added Sim.Pins to LED: a field, so cosmetic, released without asking."""
    old, new = kicad_syms(FIX / "led_sim_pins_old.kicad_sym"), kicad_syms(FIX / "led_sim_pins_new.kicad_sym")
    ch = CD.diff(old["LED"], new["LED"])
    assert ch and CD.worst(ch) == "cosmetic" and CG.decide(ch, CG.POLICY)["action"] == "pass", ch
    assert old["LED"]["hashes"]["eid"] == new["LED"]["hashes"]["eid"]


def test_sot23_symbol_remap_gsd_to_sgd_is_held_and_the_released_file_stays():
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "AO3401A")
    f = lib / it["symbol"]["file"]
    before = f.read_bytes()
    g = gate(lib)
    write_symbol(lib, it, [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    [row] = g.settle(items)
    assert row["action"] == "hold" and row["part"] == "AO3401A", row
    t = texts(row["changes"])
    assert "pin 1 was G now S" in t[0] or "pin 1 was G now S" in t, t
    assert f.read_bytes() == before, "the released .SchLib is back, byte for byte"
    assert it["change"]["state"] == "held" and it["change"]["base"] == 1
    CG.write_plan(plan, items)
    prop = LG.Ledger(lib).proposal("AO3401A")
    assert prop and prop["classes"].get("electrical") and prop["writer"] == "librarian"
    st = CC.version(f"{plan}:AO3401A@proposed")[1]
    assert st["symbol"]["pins"]["1"]["name"] == "S"


def test_footprint_0603_to_0402_held_approved_then_reverted():
    """A refresh that gives a released resistor another land pattern: held; a person approves it (r2); the board
    was already made with 0603, so they revert to r1 (r3, the r1 files byte for byte)."""
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "RES-0603-10k")
    r1 = (lib / it["footprint"]["file"]).read_bytes()
    g = gate(lib)
    text, _ = SP.build("R_0402_1005Metric")
    new = lib / "models" / "PcbLib" / "R_0402_1005Metric.PcbLib"
    AL.write_pcblib(new, [AL.footprint_from_kicad(text)])
    (lib / "models" / "KiCad" / "R_0402_1005Metric.kicad_mod").write_text(text, encoding="utf-8")
    it["footprint"] = {"ref": "R_0402_1005Metric", "file": "models/PcbLib/R_0402_1005Metric.PcbLib",
                       "kicad_mod": "models/KiCad/R_0402_1005Metric.kicad_mod"}
    rows = g.settle(items)
    assert [r["part"] for r in rows] == ["RES-0603-10k"] and rows[0]["action"] == "hold", rows
    t = texts(rows[0]["changes"])
    assert any("footprint changed from R_0603_1608Metric to R_0402_1005Metric" in x for x in t), t
    assert it["footprint"]["ref"] == "R_0603_1608Metric", "the plan points at the released footprint again"
    CG.write_plan(plan, items)
    assert item(items_of(plan), "RES-0603-4.7k").get("change") is None, "the other 0603 part is untouched"

    [res] = CC.approve(plan, ["RES-0603-10k"], who=ANN, comment="0402 on the new board")
    assert res["result"] == "released" and res["revision"] == 2, res
    it = item(items_of(plan), "RES-0603-10k")
    assert it["footprint"]["ref"] == "R_0402_1005Metric" and it["status"] == "ready" and "change" not in it
    out = CC.revert(plan, "RES-0603-10k@1", who=BOB, comment="board already fabbed with 0603")
    assert [(o["part"], o["revision"], o["to"]) for o in out] == [("RES-0603-10k", 3, 1)], out
    it = item(items_of(plan), "RES-0603-10k")
    assert it["footprint"]["ref"] == "R_0603_1608Metric" and (lib / it["footprint"]["file"]).read_bytes() == r1
    lg = LG.Ledger(lib)
    got = [(e["event"], e.get("revision"), e.get("decision")) for e in lg.history("RES-0603-10k") if e["event"] != "publish"]
    assert got == [("release", 1, "baseline"), ("hold", None, None), ("approve", None, None), ("release", 2, "approved"),
                   ("revert", 3, "revert")], got
    assert lg.revision("RES-0603-10k", 3)["approved_by"] == [BOB]
    try:
        CC.revert(plan, "RES-0603-10k@1")
    except CG.ChangeError as e:
        assert "what is released now" in str(e)
    else:
        raise AssertionError("revert to the released revision accepted")


def test_web_ui_shows_the_held_update_and_approves_rejects_reverts():
    """The web UI's part card carries the held update (old -> new per change), /api/change/history lists the
    revisions, /api/change/decide approves or rejects (a reject needs a reason), /api/change/revert goes back."""
    from ecadlib.app import Workspace
    from tests.test_ui import serve
    plan, lib = demo()
    ws, bom = Workspace(lib.parent.parent, [], offline=True), "uploads/demo/demo_bom.csv"
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
    srv, call = serve(ws)
    try:
        st, cards = call("GET", "/api/parts?bom=" + bom)
        assert st == 200 and cards["counts"]["changes_held"] == 1, (st, cards.get("counts"))
        card = next(p for p in cards["parts"] if p["part"] == "RES-0603-10k")
        assert card["change"]["base"] == 1 and card["reasons"][0]["code"] == "change.held", card["reasons"][:1]
        assert any("R_0603_1608Metric to R_0402_1005Metric" in c["text"] for c in card["change"]["changes"]), card["change"]
        st, h = call("POST", "/api/change/history", {"bom": bom, "part": "RES-0603-10k"})
        assert st == 200 and h["revision"] == 1 and [e["event"] for e in h["history"] if e["event"] != "publish"] == ["hold", "release"], h
        st, e = call("POST", "/api/change/decide", {"bom": bom, "part": "RES-0603-10k", "decision": "reject", "user": ANN})
        assert st == 400 and "needs a reason" in e["error"], e
        st, e = call("POST", "/api/change/decide", {"bom": bom, "part": "RES-0603-10k", "decision": "approve", "user": ANN})
        assert st == 400 and "digest is required" in e["error"], e     # an approval names the change it was shown
        st, r = call("POST", "/api/change/decide", {"bom": bom, "part": "RES-0603-10k", "decision": "approve", "user": ANN,
                                                    "comment": "0402 on the new board", "digest": card["change"]["digest"]})
        assert st == 200 and r["results"][0]["revision"] == 2, r
        st, cards = call("GET", "/api/parts?bom=" + bom)
        assert next(p for p in cards["parts"] if p["part"] == "RES-0603-10k")["change"] is None
        st, r = call("POST", "/api/change/revert", {"bom": bom, "part": "RES-0603-10k", "revision": 1, "user": BOB,
                                                    "comment": "board already made with 0603"})
        assert st == 200 and r["results"][0]["revision"] == 3, r
        st, e = call("POST", "/api/change/revert", {"bom": bom, "part": "RES-0603-10k", "revision": "x", "user": BOB})
        assert st == 400 and "number" in e["error"], e
        st, h = call("POST", "/api/change/history", {"bom": bom, "part": "RES-0603-10k"})
        assert h["revision"] == 3 and h["history"][0]["event"] == "revert", h["history"][:1]
    finally:
        srv.shutdown()


def test_parts_sharing_a_file_are_held_together_and_keep_their_bytes():
    """RES-0603-10k and RES-0603-4.7k share R_0603 and Resistor.SchLib: a denser footprint holds both, each proposal
    keeps the new file (not the restored one), one approval releases both."""
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "RES-0603-10k")
    f = lib / it["footprint"]["file"]
    before = f.read_bytes()
    g = gate(lib)
    text, _ = SP.build("R_0603_1608Metric", "C")
    AL.write_pcblib(f, [AL.footprint_from_kicad(text)])     # the librarian writes both tools' files
    (lib / it["footprint"]["kicad_mod"]).write_text(text, encoding="utf-8")
    newer = f.read_bytes()
    rows = g.settle(items)
    assert sorted((r["part"], r["action"]) for r in rows) == [("RES-0603-10k", "hold"), ("RES-0603-4.7k", "hold")], rows
    assert f.read_bytes() == before
    CG.write_plan(plan, items)
    lg = LG.Ledger(lib)
    for p in lg.proposals():
        assert lg.get_bytes(p["files"]["footprint"]["sha256"]) == newer, p["part"]
    out = CC.approve(plan, ["RES-0603-10k"], who=ANN)
    assert sorted((o["part"], o["result"], o["revision"]) for o in out) == [
        ("RES-0603-10k", "released", 2), ("RES-0603-4.7k", "released", 2)], out
    assert f.read_bytes() == newer and not lg.proposals()


def test_two_person_rule_reject_and_quiet_rehold():
    plan, lib = demo()
    (lib / "ecadlib.toml").write_text('[change]\napprovers = 2\n', encoding="utf-8")
    items = items_of(plan)
    it = item(items, "AO3401A")
    g = gate(lib)
    write_symbol(lib, it, [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    g.settle(items)
    CG.write_plan(plan, items)
    [r] = CC.approve(plan, ["AO3401A"], who=ANN)
    assert r["result"] != "released" and r["approvals"] == 1 and r["needed"] == 2, r
    try:
        CC.approve(plan, ["AO3401A"], who=ANN)
    except CG.ChangeError as e:
        assert "already" in str(e)
    else:
        raise AssertionError("the same person approved twice")
    try:
        CC.reject(plan, ["AO3401A"], who=BOB)
    except CG.ChangeError as e:
        assert "reason" in str(e)
    else:
        raise AssertionError("a reject without a reason")
    CC.reject(plan, ["AO3401A"], who=BOB, comment="our boards use GSD")
    lg = LG.Ledger(lib)
    assert not lg.proposals() and lg.head("AO3401A")["revision"] == 1
    # the same upstream change comes again: held without a new hold line (nobody is asked twice)
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    [row] = g.settle(items)
    assert row["action"] == "hold" and row["rejected"], row
    assert sum(e["event"] == "hold" for e in LG.Ledger(lib).history("AO3401A")) == 1


def test_a_persons_choice_is_one_approval():
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "AO3401A")
    g = CG.Guard(lib, "resolve", chosen_by=ANN)
    g.begin(items)
    write_symbol(lib, it, [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    [row] = g.settle(items)
    assert row["action"] == "pass" and row["revision"] == 2, row
    head = LG.Ledger(lib).head("AO3401A")
    assert head["decision"] == "approved" and head["approved_by"] == [ANN]


def test_restyle_may_only_change_cosmetics():
    plan, lib = demo()
    before = items_of(plan)
    after = copy.deepcopy(before)
    item(after, "KT-0603R")["description"] = "LED red 0603 (house style)"
    for p in item(after, "AO3401A")["parameters"]:
        if p["name"] == "Manufacturer Part Number":
            p["value"] = "AO3401"                 # a "restyle" that renamed the part: an ecadlib bug, refused
    g = CG.Guard(lib, "style-apply")
    g.begin(before)
    rows = {r["part"]: r for r in g.restyle(before, after)}
    assert rows["AO3401A"]["action"] == "refused" and "identity" in rows["AO3401A"]["summary"], rows
    assert any(p["value"] == "AO3401A" for p in item(after, "AO3401A")["parameters"] if p["name"] == "Manufacturer Part Number")
    assert rows["KT-0603R"]["action"] == "pass" and LG.Ledger(lib).head("KT-0603R")["revision"] == 2


def test_hand_edit_found_by_a_publisher_is_held():
    """A footprint edited outside ecadlib, then a KiCad export: the export settles it first (writer hand-edit),
    holds it and exports the released revision."""
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "CAP-0402-100nF")
    f = lib / it["footprint"]["file"]
    before = f.read_bytes()
    text, _ = SP.build("C_0402_1005Metric")
    conv = AL.footprint_from_kicad(text)
    conv["pads"][0]["x"] -= 0.07               # pad 1 moved by hand in the footprint editor
    AL.write_pcblib(f, [conv])
    status, _ = CG.publish_guard(plan, items, "kicad")
    assert f.read_bytes() == before and item(items_of(plan), "CAP-0402-100nF")["change"]["state"] == "held"
    assert LG.Ledger(lib).proposal("CAP-0402-100nF")["writer"] == "hand-edit"
    assert status["CAP-0402-100nF"] is None and status["CH340C"].startswith("not released")


def test_an_approval_the_release_check_refuses_is_not_counted():
    """Found in the live check on AD26: an approved change that fails the release check stays held, and the approval
    is not recorded as one (status said "1/1 approvals" on a part that was still held). It is a `refused` line."""
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "RES-0603-4.7k")
    f = lib / it["footprint"]["file"]
    before = f.read_bytes()
    conv = AL.footprint_from_kicad(SP.build("R_0603_1608Metric")[0])
    conv["pads"][0]["x"] -= 0.25               # the PcbLib only: KiCad / Altium parity fails on the approved version
    AL.write_pcblib(f, [conv])
    CG.publish_guard(plan, items, "kicad")
    CG.write_plan(plan, items)
    try:
        CC.approve(plan, ["RES-0603-4.7k"], who=ANN, comment="new drawing")
    except CG.ChangeError as e:
        assert "fails the release check" in str(e) and "parity" in str(e), e
    else:
        raise AssertionError("a version that fails the release check was approved")
    lg = LG.Ledger(lib)
    prop = lg.proposal("RES-0603-4.7k")
    assert prop["approvals"] == [] and prop["refused"]["by"] == ANN, prop
    assert [e["event"] for e in lg.history("RES-0603-4.7k")][-2:] == ["hold", "refused"]
    assert f.read_bytes() == before, "the released footprint is back in place"
    ch = item(items_of(plan), "RES-0603-4.7k")["change"]
    assert ch["state"] == "held" and ch["approvals"] == [] and "release check" in ch["refused"], ch
    a = __import__("argparse").Namespace(plan=str(plan), json=False)
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        CC.cmd_status(a)
    assert "approval refused: release check" in buf.getvalue() and "approvals]" not in buf.getvalue(), buf.getvalue()


def test_ledger_is_append_only_json_lines_and_status_cli():
    plan, lib = demo()
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    g.settle(items)
    CG.write_plan(plan, items)
    raw = (lib / LG.LEDGER).read_text(encoding="utf-8").splitlines()
    assert all(json.loads(x)["schema_version"] == LG.SCHEMA_VERSION for x in raw)
    assert all(list(json.loads(x)) == sorted(json.loads(x)) for x in raw), "sorted keys: git-friendly diffs"
    r = subprocess.run([sys.executable, "-m", "ecadlib", "part", "status", str(lib)], cwd=ROOT, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 1 and "AO3401A" in r.stdout and "electrical" in r.stdout, r.stdout + r.stderr
    r = subprocess.run([sys.executable, "-m", "ecadlib", "part", "diff", f"{plan}:AO3401A@head", f"{plan}:AO3401A@proposed"],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert "pin 1 was G now S" in r.stdout and "hold" in r.stdout, r.stdout + r.stderr
    js = json.loads(subprocess.run([sys.executable, "-m", "ecadlib", "part", "history", str(lib), "AO3401A", "--json"],
                                   cwd=ROOT, capture_output=True, text=True, timeout=120).stdout)
    assert [e["event"] for e in js if e["event"] != "publish"] == ["release", "hold"], js


def test_rebuild_check_ignores_the_ledger():
    from ecadlib import rebuild as RB
    plan, lib = demo()
    rep = RB.check(lib, log=lambda *a: None)
    assert rep["ok"], [r for r in rep["rows"] if r["result"] == "DRIFT"]


def test_kicad9_tilde_and_kicad10_empty_are_the_same_pin_and_field():
    """KiCad 9 wrote an empty pin name, pin number or field as ~, KiCad 10 writes "": the corpus run found two false
    electrical holds (Valve:ECH81, Valve:EM84, a pin with no number) and 11,671 cosmetic lines from this alone."""
    def sym(empty):
        return sexpr.parse(f'(symbol "V" (property "Reference" "V" (at 0 0 0)) (property "Datasheet" "{empty}" (at 0 0 0)) '
                           f'(symbol "V_1_1" (pin passive line (at 0 0 0) (length 2.54) (name "{empty}") (number "1")) '
                           f'(pin input line (at 0 2.54 0) (length 2.54) (name "K_G5") (number "{empty}"))))')
    old, new = (FP.of_kicad(sym(e), None, part="V") for e in ("~", ""))
    assert CD.diff(old, new) == []
    renamed = FP.of_kicad(sexpr.parse('(symbol "V" (property "Reference" "V" (at 0 0 0)) (symbol "V_1_1" (pin passive line '
                                      '(at 0 0 0) (length 2.54) (name "~") (number "1")) (pin input line (at 0 2.54 0) '
                                      '(length 2.54) (name "K_G5") (number "7"))))'), None, part="V")
    assert CD.worst(CD.diff(old, renamed)) == "electrical"


# ---------------------------------------------------------------- property tests
NAMES = ["VCC", "GND", "SDA", "SCL", "EN", "~{RESET}", "TX", "RX", "MISO", "MOSI", "SCK", "~{CS}", "INT", "VREF", "OUT",
         "IN+", "IN-", "FB", "SW", "BOOT", "PG", "NC1", "NC2", "VBAT", "D+", "D-", "CLK", "DATA", "ADJ", "SENSE"]
KTYPES = ["input", "output", "bidirectional", "passive", "power_in", "open_collector", "tri_state"]


def _kicad_symbol(name, pins, desc="", value=None):
    pin_s = "".join(f'(pin {t} line (at {x} {y} {a}) (length {ln}) (name "{nm}" (effects (font (size 1.27 1.27)))) '
                    f'(number "{n}" (effects (font (size 1.27 1.27)))))' for n, nm, t, x, y, a, ln in pins)
    return sexpr.parse(f'(symbol "{name}" (property "Reference" "U" (at 0 0 0)) (property "Value" "{value or name}" (at 0 0 0)) '
                       f'(property "Description" "{desc}" (at 0 0 0)) '
                       f'(symbol "{name}_0_1" (rectangle (start -5 5) (end 5 -5) (stroke (width 0.254)) (fill (type background)))) '
                       f'(symbol "{name}_1_1" {pin_s}))')


def _kicad_mod(name, pads, silk=((-1, -1, 1, -1),), ref_at=(0, -2)):
    pad_s = "".join(f'(pad "{n}" smd roundrect (at {x} {y}) (size {w} {h}) (layers "F.Cu" "F.Paste" "F.Mask") '
                    f'(roundrect_rratio 0.25))' for n, x, y, w, h in pads)
    silk_s = "".join(f'(fp_line (start {a} {b}) (end {c} {d}) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))'
                     for a, b, c, d in silk)
    return (f'(footprint "{name}" (layer "F.Cu") (fp_text reference "REF**" (at {ref_at[0]} {ref_at[1]}) (layer "F.SilkS") '
            f'(effects (font (size 1 1) (thickness 0.15)))) {silk_s} '
            f'(fp_rect (start -3 -3) (end 3 3) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd")) {pad_s})')


def _part(rnd):
    n = rnd.randint(2, 24)
    names = rnd.sample(NAMES, n) if n <= len(NAMES) else NAMES[:n]
    pitch = rnd.choice((0.5, 0.65, 0.8, 1.27))
    rows = (n + 1) // 2
    pads, pins = [], []
    for k in range(n):
        col, row = (-1, k) if k < rows else (1, k - rows)
        pads.append([str(k + 1), round(col * rnd.uniform(1.0, 3.0), 3) if k in (0, rows) else None,
                     round(row * pitch - rows * pitch / 2, 3), round(rnd.uniform(0.25, 0.6), 3), round(rnd.uniform(0.8, 1.6), 3)])
        pins.append([str(k + 1), names[k], rnd.choice(KTYPES), -7.62 if col < 0 else 7.62, round(2.54 * (rows / 2 - row), 2),
                     0 if col < 0 else 180, 2.54])
    for k in range(n):                         # one x per column
        if pads[k][1] is None:
            pads[k][1] = pads[0 if k < rows else rows][1]
    return {"pins": pins, "pads": pads, "desc": "part", "silk": [(-1, -1, 1, -1)]}


def _fp(p, name="P"):
    return FP.of_kicad(_kicad_symbol(name, p["pins"], p["desc"]), _kicad_mod("F", p["pads"], p["silk"]), part=name)


def test_property_cosmetic_edits_never_hold():
    rnd = random.Random(105)
    cases = 0
    for _ in range(250):
        p = _part(rnd)
        base = _fp(p)
        for kind in ("silk", "pin_place", "desc", "spelling"):
            q = copy.deepcopy(p)
            if kind == "silk":
                q["silk"] = [(-1.2, -1.1, 1.3, -1.1), (rnd.uniform(-2, 2), 1, 1, 1)]
            elif kind == "pin_place":
                k = rnd.randrange(len(q["pins"]))
                q["pins"][k][3] += rnd.choice((-2.54, 2.54))
                q["pins"][k][6] = rnd.choice((1.27, 3.81, 5.08))
            elif kind == "desc":
                q["desc"] = "another description"
            else:                              # an active-low pin written another way: same signal
                ks = [k for k, x in enumerate(q["pins"]) if x[1].startswith("~{")]
                if not ks:
                    q["pins"][0][1] = q["pins"][0][1]
                for k in ks:
                    q["pins"][k][1] = q["pins"][k][1][2:-1] + "#"
            ch = CD.diff(base, _fp(q))
            assert CD.worst(ch) in (None, "cosmetic"), (kind, texts(ch))
            assert CG.decide(ch, CG.POLICY)["action"] == "pass", (kind, texts(ch))
            cases += 1
    assert cases >= 1000


def test_property_pin_pad_permutations_renames_and_pad_moves_always_hold():
    rnd = random.Random(1050)
    cases = 0
    for _ in range(300):
        p = _part(rnd)
        base = _fp(p)
        n = len(p["pins"])
        for kind in ("swap_names", "rename", "pad_numbers", "pad_move"):
            q = copy.deepcopy(p)
            a, b = rnd.sample(range(n), 2)
            if kind == "swap_names":
                q["pins"][a][1], q["pins"][b][1] = q["pins"][b][1], q["pins"][a][1]
            elif kind == "rename":
                q["pins"][a][1] = "XNEW" + str(rnd.randint(0, 99))
            elif kind == "pad_numbers":         # the land pattern renumbered: pin a now lands on pad b's copper
                q["pads"][a][0], q["pads"][b][0] = q["pads"][b][0], q["pads"][a][0]
            else:
                q["pads"][a][rnd.choice((1, 2))] += rnd.choice((-1, 1)) * rnd.uniform(0.002, 0.5)
            ch = CD.diff(base, _fp(q))
            want = "geometry-major" if kind == "pad_move" else "electrical"
            assert CD.worst(ch) == want, (kind, texts(ch))
            assert CG.decide(ch, CG.POLICY)["action"] == "hold", (kind, texts(ch))
            loose = CG.normalize_policy({"geometry_major": "warn", "electrical": "warn"})
            if want == "electrical":           # warn is the most a policy can do for electrical; it never passes
                assert CG.decide(ch, loose)["action"] == "warn"
            cases += 1
    assert cases >= 1200


# ---------------------------------------------------------------- cross-tool identity
def test_same_part_from_kicad_and_altium_has_the_same_identity():
    """Every golden KiCad footprint and the fixture symbols, read from KiCad and from the Altium files ecadlib converts
    them to: the same gid (footprints) and eid (symbols)."""
    d = Path(tempfile.mkdtemp(prefix="ecadlib-cc-x-"))
    for f in sorted(GOLDEN.glob("*.kicad_mod")):
        text = f.read_text(encoding="utf-8")
        land, art, model = FP.kicad_footprint(text)
        a = FP.build(f.stem, land=land, art=art, model=model, tool="kicad")
        conv, _ = K.convert_footprint(text, notes=[])
        l2, a2, m2 = FP.altium_footprint(AL.read_pcblib(AL.write_pcblib(d / f"{f.stem}.PcbLib", [conv]))[0])
        b = FP.build(f.stem, land=l2, art=a2, model=m2, tool="altium")
        assert a["hashes"]["gid"] == b["hashes"]["gid"], (f.stem, texts(CD.diff(a, b)))
    for f in [FIX / "dual_diode_aak_new.kicad_sym", FIX / "led_sim_pins_new.kicad_sym",
              ROOT / "tests" / "fixtures" / "kicad" / "Regulator_Linear_AMS1117.kicad_sym"]:
        lib = sexpr.parse(f.read_text(encoding="utf-8"))
        syms = {str(s[1]): s for s in sexpr.find(lib, "symbol")}
        for name, s in syms.items():
            chain = [s]
            while sexpr.first(chain[-1], "extends"):
                chain.append(syms[str(sexpr.first(chain[-1], "extends")[1])])
            ka = FP.of_kicad(chain, part=name)
            sym = K.convert_symbol(chain, notes=[])
            path = AL.write_schlib(d / f"{AL.file_stem(sym['name'])}.SchLib", [sym])
            _, entries = AL.read_schlib(path)
            kb = FP.build(name, FP.altium_symbol(entries[0])[0], tool="altium")
            assert FP.build(name, ka["symbol"], tool="kicad")["hashes"]["eid"] == kb["hashes"]["eid"], \
                (name, texts(CD.diff(FP.build(name, ka["symbol"]), kb)))


# ---------------------------------------------------------------- every writer is gated
WRITE_CALLS = {"write_schlib", "write_pcblib", "import_part", "reconvert", "attach_altium", "write_plan", "adapt_items"}


def test_every_library_writer_is_gated_or_exempt():
    """A module that writes a part's files or a plan (calls a writer, or writes plan.json) is a registered writer
    that goes through change_gate, or is listed in NOT_LIBRARY_WRITERS with the reason."""
    import re
    gated = {w["module"] for w in CG.WRITERS.values() if w["module"]}
    plan_write = re.compile(r"(plan|plan_f|lib_plan)\.write_text\(|write_text_atomic\((plan|plan_f|lib_plan)\b")
    missing, ungated = [], []
    for f in sorted((ROOT / "ecadlib").rglob("*.py")):
        mod = "ecadlib." + ".".join(f.relative_to(ROOT / "ecadlib").with_suffix("").parts)
        src = f.read_text(encoding="utf-8")
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                 for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
        defs = {n.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
        writes = (calls & WRITE_CALLS) - defs or plan_write.search(src)
        if not writes:
            continue
        if mod in CG.NOT_LIBRARY_WRITERS:
            continue
        if mod not in gated:
            missing.append(mod)
        elif "change_gate" not in src:
            ungated.append(mod)
    assert not missing, f"library writers not in change_gate.WRITERS / NOT_LIBRARY_WRITERS: {missing}"
    assert not ungated, f"registered writers that never call change_gate: {ungated}"
    for name, w in CG.WRITERS.items():
        assert w["mode"] in ("gate", "restyle", "observe", "publish"), name
        if w["module"] and w["mode"] != "publish":         # publishers settle as writer hand-edit (publish_guard)
            assert (ROOT / (w["module"].replace(".", "/") + ".py")).is_file(), w["module"]
            assert f'"{name}"' in (ROOT / (w["module"].replace(".", "/") + ".py")).read_text(encoding="utf-8") \
                or name in ("librarian",), f"{w['module']} never uses writer {name!r}"


# ---------------------------------------------------------------- Altium 365
class FakeA365:
    """Nexar mock: components with revisions; desReleaseComponent of an existing item makes its next revision."""
    def __init__(self):
        self.comps, self.calls, self.n = {}, [], 0

    def release_defs(self, ws):
        return {"componentLifeCycle": {"id": "LC1"}}

    def upload(self, p):
        self.calls.append(("upload", Path(p).name))
        return f"file:{Path(p).name}"

    def find(self, ws, name):
        return self.comps.get(name)

    def component(self, cid):
        return next((c for c in self.comps.values() if c["id"] == cid), None)

    def find_model(self, ws, kind, name, folder):
        return f"{kind}:{name}"

    def folder_id(self, ws, path):
        return f"folder:{path}"

    def release(self, inp):
        self.calls.append(("release", inp["componentItemName"], bool(inp["symbolFiles"]), bool(inp["footprintFiles"])))
        self.n += 1
        c = self.comps.setdefault(inp["componentItemName"], {"id": f"C{self.n}", "name": inp["componentItemName"],
                                                             "revisions": []})
        c["revisions"].append(inp)
        c["revision"] = {"revisionId": f"R{len(c['revisions'])}"}
        c["details"] = {"parameters": inp["parameters"]}

    def update_params(self, cid, params, note, replace=False):
        self.calls.append(("update_params", cid))
        return cid

    def transition(self, ws, defs, cid, name, note):
        self.calls.append(("transition", cid, name))
        return name


def test_a365_approved_change_is_a_new_revision_and_held_sends_nothing():
    from ecadlib import altium365 as A
    plan, lib = demo()
    ws = "https://test.365.altium.com"
    nx = FakeA365()
    from ecadlib import rules as RU
    rules = RU.load(None, start=lib)
    rules["folders"]["root"], rules["change"]["a365_transition"] = "T", "Promote"

    def push(dry=False):
        items = items_of(plan)
        for i in items:
            for k in ("symbol", "footprint"):
                if i[k].get("file"):
                    i[k]["file"] = str(lib / i[k]["file"])
        return {r["name"]: r for r in A.push(items, nx, ws, dry_run=dry, rules=rules, folder=lib)}
    first = push()
    assert first["AO3401A"]["action"] == "release" and nx.comps["AO3401A"]["revision"]["revisionId"] == "R1"
    assert LG.Ledger(lib).published("AO3401A", f"altium365:{ws}")["revision"] == 1
    assert LG.Ledger(lib).published("AO3401A", f"altium365:{ws}")["detail"] == {"action": "release", "a365_revision": "R1"}
    # a held change: the plan still has the released revision, so nothing new goes up
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    g.settle(items)
    CG.write_plan(plan, items)
    nx.calls.clear()
    held = push()
    assert held["AO3401A"]["action"] == "up_to_date" and "held" in held["AO3401A"]["change"], held["AO3401A"]
    assert not [c for c in nx.calls if c[0] in ("release", "update_params")], nx.calls
    assert len(nx.comps["AO3401A"]["revisions"]) == 1
    # approved: the symbol goes up as a new revision of the same component; the footprint is linked, not re-sent
    CC.approve(plan, ["AO3401A"], who=ANN)
    dry = push(dry=True)["AO3401A"]
    assert dry["action"] == "new_revision" and dry["detail"]["changed"] == ["symbol"], dry
    nx.calls.clear()
    res = push()["AO3401A"]
    assert res["action"] == "new_revision" and res["detail"]["revision"] == "R2", res
    assert res["detail"]["transition"] == "Promote"
    assert ("release", "AO3401A", True, False) in nx.calls, nx.calls
    assert nx.comps["AO3401A"]["revisions"][0]["componentItemName"] == "AO3401A", "revision 1 is never edited"
    assert LG.Ledger(lib).published("AO3401A", f"altium365:{ws}")["revision"] == 2
    assert LG.Ledger(lib).published("AO3401A", f"altium365:{ws}")["detail"]["a365_revision"] == "R2"
    nx.calls.clear()
    again = push()["AO3401A"]
    assert again["action"] == "up_to_date" and not [c for c in nx.calls if c[0] == "release"], (again, nx.calls)


def test_a365_new_queries_match_the_nexar_schema():
    try:
        from graphql import build_schema, parse, validate
    except ImportError:
        print("skip test_a365_new_queries_match_the_nexar_schema (pip install graphql-core)")
        return
    from ecadlib import altium365 as A
    schema = build_schema((ROOT / "tests" / "nexar" / "schema.graphql").read_text(), assume_valid_sdl=True)
    for q in (A.Q_TRANSITIONS, A.M_LIFECYCLE):
        assert not validate(schema, parse(q)), q
