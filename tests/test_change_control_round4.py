"""Change control, fourth #105 review round (tester 4): value text the classifier cannot read as a number, a comma
before three digits, hidden and power pin names as nets, release-check never clearing a change hold, every publisher
through the gate (kicad-dblib, export-parts, exporter plugins, kicad-pcm and altium-pkg on a folder), an unreadable
.kicad_mod, project check reading the symbol's Footprint field, a lost ledger with the stamp / lock / export left,
approver names folded, and the small ones (exit codes, messages, a broken pipe, a killed approval).

Each test is a regression for a finding of the round-4 report (ids C1-C4, H1-H4, M1-M3, L)."""
import contextlib, copy, hashlib, io, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import change_cli as CC, change_diff as CD, change_gate as CG, fingerprint as FP, ledger as LG
from ecadlib import release_check as RC, sexpr
from tests.test_change_control import ANN, BOB, ROOT, demo, gate, item, items_of, write_symbol
from tests.test_change_control_workflow import raises, run_cli, to_0402

SGD = [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")]


def _quiet(*a, **k):
    pass


def _param(it, name, value):
    for p in it["parameters"]:
        if p["name"] == name:
            p["value"] = value
            return
    it["parameters"].append({"name": name, "value": value})


def _value(it):
    return next((p["value"] for p in it.get("parameters") or [] if p["name"] == "Value"), None)


# ---------------------------------------------------------------- C1, M1: the value field
def _vclass(a, b, mpn="X1", kind=""):
    def fp(v):
        return FP.build("", params={"Value": v, "Manufacturer Part Number": mpn, **({"Description": kind} if kind else {})})
    return CD.worst([c for c in CD.diff(fp(a), fp(b)) if c["area"] == "identity"])


def test_a_value_that_is_not_read_as_a_number_is_compared_as_text():
    """C1: '10k' -> '47kR' (or 1E4, 1uFd, 'C 1uF', a colour) was cosmetic when the MPN stayed: the side ecadlib cannot
    read was waved through. Any other text is an identity change, whatever the MPN; only spacing and case are not."""
    for a, b in (("10k", "47kR"), ("10k", "1kR"), ("10k", "R10k"), ("10k", "1E4"), ("10k", "10e3"), ("1uF", "1uFd"),
                 ("1uF", "C 1uF"), ("RED", "GREEN"), ("47kR", "10kR")):
        assert _vclass(a, b) == "identity", (a, b)
        assert _vclass(a, b, mpn="") == "identity", (a, b)
    for a, b in (("10k", "10 k"), ("10k", "10K"), ("10k", "10k 1%"), ("100n", "100n"), ("RED", "Red"),
                 ("47kR", "47 kr"), ("0.1uF", "0.1uF")):
        assert _vclass(a, b) in (None, "cosmetic"), (a, b, _vclass(a, b))
    fp = lambda v: FP.build("", params={"Value": v, "Manufacturer Part Number": "X1"})
    [c] = [c for c in CD.diff(fp("10k"), fp("47kR")) if c["area"] == "identity"]
    assert "not read as a number" in c["text"], c


def test_a_comma_before_three_digits_reads_two_ways():
    """M1: '100,000nF' is 100 nF with a decimal comma and 100 uF with a thousands separator; a change between such
    values is an identity change when the two differ under either reading."""
    assert _vclass("100nF", "100,000nF") == "identity"
    assert _vclass("10k", "10,000k") == "identity"
    assert _vclass("4,7k", "4.7k") in (None, "cosmetic")      # a decimal comma before one digit reads one way
    assert _vclass("100,000nF", "100,000nF") is None
    assert _vclass("10k", "1k") == "identity"
    fp = lambda v: FP.build("", params={"Value": v, "Manufacturer Part Number": "X1"})
    [c] = [c for c in CD.diff(fp("100nF"), fp("100,000nF")) if c["area"] == "identity"]
    assert "reads two ways" in c["text"], c
    [c] = [c for c in CD.diff(fp("10k"), fp("1k")) if c["area"] == "identity"]
    assert "reads two ways" not in c["text"], c


def test_the_demo_has_no_false_holds():
    """C1: the stricter value rule holds nothing in the offline demo (every part released, no hold)."""
    plan, lib = demo()
    held = [(i["name"], i.get("hold_reasons")) for i in items_of(plan) if i.get("change") or
            any(str(h).startswith(RC.CHANGE_HOLD) for h in i.get("hold_reasons") or [])]
    assert not held, held                      # (CH340C is held by the release check: the demo has no symbol for it)
    assert not LG.Ledger(lib).proposals()
    rows = gate(lib).settle(items_of(plan))
    assert rows == [], rows


# ---------------------------------------------------------------- C2: hidden and power pin names
def _sym(pins):
    """A KiCad symbol: pins (num, name, type, hidden)."""
    ps = "".join(f'(pin {t} line (at 0 {-2.54 * k} 0) (length 2.54){" (hide yes)" if h else ""} '
                 f'(name "{n}" (effects (font (size 1.27 1.27)))) (number "{num}" (effects (font (size 1.27 1.27)))))'
                 for k, (num, n, t, h) in enumerate(pins))
    return sexpr.parse(f'(symbol "U" (property "Reference" "U" (at 0 0 0)) (property "Value" "U" (at 0 0 0)) '
                       f'(symbol "U_1_1" {ps}))')


def _pin_class(old, new):
    a, b = FP.of_kicad(_sym(old), part="U"), FP.of_kicad(_sym(new), part="U")
    return CD.worst(CD.diff(a, b)), CD.diff(a, b)


def test_a_hidden_or_power_pins_name_is_its_net():
    """C2: a hidden power pin renamed VCC -> Vcc (or a visible power pin ~{CS} -> CS#) joins another net in KiCad,
    yet was cosmetic because the canonical name is the same. Active-low spelling stays cosmetic on visible signal
    pins only."""
    base = [("1", "IN", "input", False)]
    w, ch = _pin_class(base + [("2", "VCC", "power_in", True)], base + [("2", "Vcc", "power_in", True)])
    assert w == "electrical" and any("net it joins" in c["text"] for c in ch), ch
    w, _ = _pin_class(base + [("2", "VCC", "power_in", True)], base + [("2", "VCC ", "power_in", True)])
    assert w == "electrical"
    w, _ = _pin_class(base + [("2", "~{CS}", "power_in", False)], base + [("2", "CS#", "power_in", False)])
    assert w == "electrical"
    w, ch = _pin_class(base + [("2", "~{CS}", "input", False)], base + [("2", "CS#", "input", False)])
    assert w == "cosmetic", ch
    # Altium: a hidden pin's HiddenNetName, compared from the fingerprint as written
    a = FP.of_kicad(_sym(base + [("2", "VCC", "power_in", True)]), part="U")
    b = copy.deepcopy(a)
    for fp in (a, b):
        fp["tool"] = "altium"
        for p in [fp["symbol"]["pins"]["2"]] + fp["symbol"]["pins"]["2"].get("inst", []):
            p.update(type="power", ktype=None)
    for p in [b["symbol"]["pins"]["2"]] + b["symbol"]["pins"]["2"].get("inst", []):
        p.update(raw="vcc", net="vcc")
    a["hashes"], b["hashes"] = FP.hashes(a), FP.hashes(b)
    assert CD.worst(CD.diff(a, b)) == "electrical", CD.diff(a, b)


def test_hidden_pin_net_keeps_its_spelling_in_the_fingerprint():
    a = FP.of_kicad(_sym([("1", "VCC", "power_in", True)]), part="U")
    b = FP.of_kicad(_sym([("1", "vcc", "power_in", True)]), part="U")
    assert a["symbol"]["pins"]["1"]["net"] == "VCC" and b["symbol"]["pins"]["1"]["net"] == "vcc"
    assert a["hashes"]["digest"] != b["hashes"]["digest"] and FP.SCHEMA >= 3


def _age_to(lib, schema):
    """Every stored fingerprint as an ecadlib of `schema` wrote it (hidden nets in their canonical form)."""
    lg = LG.Ledger(lib)
    out = []
    for line in lg.path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("fingerprint"):
            fp = lg.get_json(e["fingerprint"])
            fp["schema"] = schema
            for p in ((fp.get("symbol") or {}).get("pins") or {}).values():
                if p.get("net"):
                    p["net"] = FP.canon_pin_name(p["net"])
            fp["hashes"] = FP.hashes(fp)
            e["fingerprint"], e["digest"] = lg.put_json(fp), fp["hashes"]["digest"]
        out.append(json.dumps(e, sort_keys=True, ensure_ascii=False))
    lg.path.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_a_library_released_with_schema_2_shows_no_change_and_a_real_change_still_holds():
    """SCHEMA 3: a library released by the previous ecadlib (schema 2 digests) shows no change after the upgrade; a
    real change made after it is still held."""
    plan, lib = demo()
    _age_to(lib, 2)
    n = len(LG.Ledger(lib).lines())
    assert gate(lib).settle(items_of(plan)) == []
    assert len(LG.Ledger(lib).lines()) == n
    status, _ = CG.publish_guard(plan, items_of(plan), "kicad")
    assert all(v is None or v.startswith("not released") for v in status.values()), status
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    [row] = g.settle(items)
    assert row["part"] == "AO3401A" and row["action"] == "hold", row


# ---------------------------------------------------------------- C3: release check never clears a change hold
def _twin_held(plan, lib):
    """RES-0402-10k: a new part with the MPN of released RES-0603-10k, held for approval (no released revision)."""
    old = items_of(plan)
    items = json.loads(json.dumps(old))
    it = item(items, "RES-0603-10k")
    it["name"] = "RES-0402-10k"
    to_0402(lib, it)
    g = CG.Guard(lib, "librarian")
    g.begin(old)
    g.settle(items)
    g.baseline(items)
    g.retire(["RES-0603-10k"], "renamed")
    g.close()
    CG.write_plan(plan, items)
    assert item(items_of(plan), "RES-0402-10k")["status"] == "held"


def test_release_check_and_release_anyway_never_clear_a_change_hold():
    """C3: release-check (with or without --allow), review's "release anyway", the web UI / MCP release_check made a
    twin-held part ready, and every publisher then shipped it. Only `part approve` releases it."""
    from ecadlib import review_cli as RV
    from ecadlib.app import Workspace
    plan, lib = demo()
    _twin_held(plan, lib)
    items = items_of(plan)
    RC.apply(items, lib)
    it = item(items, "RES-0402-10k")
    assert it["status"] == "held" and any(h.startswith(RC.CHANGE_HOLD) for h in it["hold_reasons"]), it["hold_reasons"]
    RC.apply(items, lib, allow=["RES-0402-10k"])
    assert item(items, "RES-0402-10k")["status"] == "held"
    # the plan's change block edited away by hand: the held proposal in the library still decides
    del item(items, "RES-0402-10k")["change"]
    RC.apply(items, lib, allow=["RES-0402-10k"])
    assert item(items, "RES-0402-10k")["status"] == "held"
    assert RV.release_anyway(plan, "RES-0402-10k", user=ANN)["status"] == "held"
    ws = Workspace(lib.parent.parent, [], offline=True)
    ws.release_check("uploads/demo/demo_bom.csv", allow=["RES-0402-10k"], user=ANN)
    assert item(items_of(plan), "RES-0402-10k")["status"] == "held"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        RC.main([str(plan), "--allow", "RES-0402-10k"])
    assert item(items_of(plan), "RES-0402-10k")["status"] == "held"
    # not exported by kicad-dblib either, and approval still releases it
    from ecadlib import kicad_dblib as KD
    tables, skipped = KD.part_rows(plan, "x")[:2]
    assert "RES-0402-10k" in {s["part"] for s in skipped}
    d = CG.review_digest(LG.Ledger(lib), "RES-0402-10k")
    [r] = CC.approve(plan, ["RES-0402-10k"], ANN, digests={"RES-0402-10k": d}, comment="0402 on the new board")
    assert r["result"] == "released" and item(items_of(plan), "RES-0402-10k")["status"] == "ready", r
    RC.apply(items_of(plan), lib)
    assert LG.Ledger(lib).head("RES-0402-10k")["revision"] == 1


# ---------------------------------------------------------------- H1, H2: every publisher through the gate
def _hand_edit_value(plan, name="CAP-0402-100nF", value="1uF"):
    items = items_of(plan)
    old = _value(item(items, name))
    _param(item(items, name), "Value", value)
    plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return old


def _held(lib, name):
    return LG.Ledger(lib).proposal(name) is not None


def test_kicad_dblib_and_export_parts_publish_the_released_fields():
    """H1: kicad-dblib without --export and export-parts read plan.json as it was: a hand edit of a released part's
    value went out. Both settle the plan first, so the edit is held and the released value goes out."""
    from ecadlib import kicad_dblib as KD, parts_export as PE
    plan, lib = demo()
    old = _hand_edit_value(plan)
    tables = KD.part_rows(plan, "x")[0]
    row = next(r for t in tables.values() for r in t if r["Part ID"] == "CAP-0402-100nF")
    assert old in json.dumps(row) and "1uF" not in json.dumps(row), row
    assert _held(lib, "CAP-0402-100nF") and _value(item(items_of(plan), "CAP-0402-100nF")) == old
    plan2, lib2 = demo()
    old = _hand_edit_value(plan2)
    [row] = [r for r in PE.rows(plan2) if r["Part ID"] == "CAP-0402-100nF"]
    assert row["Value"] == old, row
    assert _held(lib2, "CAP-0402-100nF")


class _EP:
    def __init__(self, name, obj):
        self.name, self.obj = name, obj

    def load(self):
        return self.obj


def test_exporter_plugins_get_the_released_parts():
    """H2: an exporter plugin got plan.json as it was (a hand edit, a held twin). It gets the settled items now, and a
    part waiting for approval with no released revision is left out."""
    from ecadlib import plugins as PL
    plan, lib = demo()
    _twin_held(plan, lib)
    old = _hand_edit_value(plan)
    seen = {}

    def exporter(items, out, rules):
        seen.update({i["name"]: _value(i) for i in items})
        (Path(out) / "x.txt").write_text("x", encoding="utf-8")
        return [Path(out) / "x.txt"]

    PL.run_exporter("t", plan, lib.parent / "px", eps=[_EP("t", exporter)])
    assert seen["CAP-0402-100nF"] == old and "RES-0402-10k" not in seen, seen
    assert _held(lib, "CAP-0402-100nF")


def test_web_ui_and_mcp_exports_are_gated():
    """H1, H2: the web UI / MCP export_parts and plugin_export go through the same functions."""
    from ecadlib.app import Workspace
    plan, lib = demo()
    old = _hand_edit_value(plan)
    ws, bom = Workspace(lib.parent.parent, [], offline=True), "uploads/demo/demo_bom.csv"
    r = ws.export_parts(bom, "json")
    rows = json.loads((ws.root / r["file"]).read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows.get("parts") or rows.get("rows") or []
    [row] = [x for x in rows if x["Part ID"] == "CAP-0402-100nF"]
    assert row["Value"] == old and _held(lib, "CAP-0402-100nF"), row


def test_every_publisher_settles_before_it_reads():
    """H1/H2: every WRITERS entry in publish mode calls publish_guard or settled_plan, or hands the work to a
    publisher that does (kicad-export, dblib-sync)."""
    for name, w in CG.WRITERS.items():
        if w["mode"] != "publish" or not w["module"]:
            continue
        src = (ROOT / (w["module"].replace(".", "/") + ".py")).read_text(encoding="utf-8")
        assert any(s in src for s in ("publish_guard(", "settled_plan(", "X.export(", "DS.sync(", "KX.export(",
                                      "_export(")), f"{name} ({w['module']}) publishes without the gate"
    for mod in ("ecadlib.kicad_dblib", "ecadlib.parts_export", "ecadlib.plugins", "ecadlib.kicad_pcm", "ecadlib.altium_pkg"):
        assert mod in {w["module"] for w in CG.WRITERS.values()}, mod
        assert mod not in CG.NOT_LIBRARY_WRITERS, mod


# ---------------------------------------------------------------- C4: an unreadable .kicad_mod
def test_an_unreadable_kicad_mod_is_held_and_never_read_from_the_pcblib():
    """C4: a truncated or garbled .kicad_mod fell back to the .PcbLib's pads, so it was 'no change' and shipped."""
    for kind, text in (("half", lambda t: t[:len(t) // 2]), ("garbage", lambda t: "garbage\n"),
                       ("binary", lambda t: None)):
        plan, lib = demo()
        it = item(items_of(plan), "AO3401A")
        f = FP.kicad_mod_path(it, lib)
        t = f.read_text(encoding="utf-8")
        if kind == "binary":
            f.write_bytes(b"\xff\xfe\x00(footprint")
        else:
            f.write_text(text(t), encoding="utf-8")
        fp = FP.of_item(it, lib)
        assert (fp.get("kicad") or {}).get("unreadable"), (kind, fp.get("kicad"))
        [row] = gate(lib).settle(items_of(plan))
        assert row["action"] == "hold" and "unreadable" in row["summary"], (kind, row)
        assert f.read_text(encoding="utf-8") == t, kind        # the released file is back


# ---------------------------------------------------------------- H3: packaging a folder
def test_kicad_pcm_packages_a_folder_only_as_kicad_export_wrote_it():
    from ecadlib import kicad_export as KX, kicad_pcm as PCM
    plan, lib = demo()
    out = lib.parent / "kx"
    KX.export(plan, out, "t", log=_quiet)
    rep = json.loads((out / "kicad_export.json").read_text(encoding="utf-8"))
    PCM.check_export_folder(out, "t", rep)                      # untouched: fine
    f = out / "t.pretty" / "SOT-23.kicad_mod"
    f.write_text(f.read_text(encoding="utf-8").replace("(at -0.9375 -0.95)", "(at -0.9375 -0.70)", 1), encoding="utf-8")
    raises(PCM.check_export_folder, out, "t", rep, exc=ValueError, contains="SOT-23.kicad_mod (changed since")
    (out / "t.pretty" / "NEW.kicad_mod").write_text("(footprint \"NEW\")", encoding="utf-8")
    raises(PCM.check_export_folder, out, "t", rep, exc=ValueError, contains="NEW.kicad_mod (not written")
    raises(PCM.check_export_folder, out, "t", {}, exc=ValueError, contains="no kicad_export.json")


def test_altium_pkg_folder_is_checked_against_the_released_revisions():
    from ecadlib import altium_pkg as AP
    plan, lib = demo()
    sch = lib / item(items_of(plan), "AO3401A")["symbol"]["file"]
    released = sch.read_bytes()
    # a hand edit in the library's own folder: settled (held, the released file put back) before it is packaged
    write_symbol(lib, item(items_of(plan), "AO3401A"), SGD)
    AP.check_released(lib / "models", plan)
    assert sch.read_bytes() == released and _held(lib, "AO3401A")
    # an edited copy in another folder that names the library: refused
    hand = lib.parent / "handoff" / "SchLib"
    hand.mkdir(parents=True)
    shutil.copy(sch, hand / sch.name)
    AP.check_released(hand.parent, plan)                       # the released bytes: fine
    keep = sch.read_bytes()
    write_symbol(lib, item(items_of(plan), "AO3401A"), SGD)
    shutil.copy(sch, hand / sch.name)
    sch.write_bytes(keep)
    raises(AP.check_released, hand.parent, plan, exc=ValueError, contains="AO3401A r1")
    assert AP.library_of(lib / "models") == lib / "plan.json"


# ---------------------------------------------------------------- H4: project check, the Footprint field
def test_project_check_compares_the_symbols_footprint_field():
    from ecadlib import project_check as PJ
    from tests.test_project_check import LIB_SYM, lib_fp, make_project, states
    with tempfile.TemporaryDirectory() as d:
        pro = make_project(d)
        lib = Path(d) / "lib"
        (lib / "TFp.pretty" / "SOT23B.kicad_mod").write_text(lib_fp("SOT23B"), encoding="utf-8")
        (lib / "TSym.kicad_sym").write_text(LIB_SYM.replace('"TFp:SOT23"', '"TFp:SOT23B"'), encoding="utf-8")
        rows = {r["kind"]: r for r in PJ.check_kicad(pro, use_global=False)["rows"]}
        sym = rows["symbol"]
        assert sym["worst"] == "geometry-major" and sym["action"] == "hold", sym
        assert any("Footprint field" in c["text"] and "SOT23B" in c["text"] for c in sym["changes"]), sym["changes"]
        (lib / "TSym.kicad_sym").write_text(LIB_SYM.replace('"TFp:SOT23"', '"TFp:GONE"'), encoding="utf-8")
        rows = {r["kind"]: r for r in PJ.check_kicad(pro, use_global=False)["rows"]}
        assert rows["symbol"]["action"] == "hold" and any("not in the library" in c["text"]
                                                          for c in rows["symbol"]["changes"]), rows["symbol"]
        (lib / "TSym.kicad_sym").write_text(LIB_SYM, encoding="utf-8")
        (lib / "TFp.pretty" / "SOT23.kicad_mod").unlink()
        rows = {r["kind"]: r for r in PJ.check_kicad(pro, use_global=False)["rows"]}
        assert rows["footprint"]["state"] == "not in library" and rows["footprint"]["action"] == "hold", rows["footprint"]


# ---------------------------------------------------------------- M2: the whole ledger lost
def _lose_all(lib, stamp=True):
    (lib / LG.LEDGER).unlink()
    shutil.rmtree(lib / LG.STORE, ignore_errors=True)
    (lib / LG.USAGE).unlink(missing_ok=True)
    if not stamp:
        (lib / "ecadlib_schema.json").unlink(missing_ok=True)


def test_a_lost_ledger_with_the_stamp_left_fails_closed():
    """M2: deleting library.ledger.jsonl, .ecadlib/ and library.usage.json made every part look new: an edit was
    taken as revision 1. The migrate stamp says the library keeps a ledger: refused; `part adopt` of untouched files
    compares them with library.lock.json."""
    from ecadlib import kicad_export as KX
    plan, lib = demo()
    _lose_all(lib)
    raises(KX.export, plan, lib.parent / "kx", "t", log=_quiet, exc=Exception, contains="ledger is missing or empty")
    rows = CC.adopt(plan, [], ANN, "ledger lost; files checked against the lock", all_lost=True)
    assert rows and all(r["result"] == "adopted" and "library.lock.json" in r["compared"] for r in rows), rows
    KX.export(plan, lib.parent / "kx", "t", log=_quiet)


def test_a_lost_ledger_and_stamp_with_an_edit_fails_closed_by_the_lock():
    from ecadlib import kicad_export as KX
    plan, lib = demo()
    _lose_all(lib, stamp=False)
    write_symbol(lib, item(items_of(plan), "AO3401A"), SGD)
    raises(KX.export, plan, lib.parent / "kx", "t", log=_quiet, exc=Exception, contains="library.lock.json records")
    e = raises(CC.adopt, plan, ["AO3401A"], ANN, "lost", contains="AO3401A")
    assert "library.lock.json" in str(e), e


def test_a_lost_ledger_and_stamp_with_untouched_files_exports_as_before():
    """A library from before change control (no stamp, no ledger) whose files are as built adopts them as before."""
    from ecadlib import kicad_export as KX
    plan, lib = demo()
    _lose_all(lib, stamp=False)
    rep = KX.export(plan, lib.parent / "kx", "t", log=_quiet)
    assert not rep["errors"] and LG.Ledger(lib).head("AO3401A")["revision"] == 1


def test_a_lost_ledger_with_an_export_left_fails_closed():
    """M2: the workspace's kicad/<library>/kicad_export.json, written by an ecadlib that keeps a ledger, lists the part."""
    from ecadlib import kicad_export as KX, rebuild as RB
    plan, lib = demo()
    KX.export(plan, lib.parent.parent / "kicad" / lib.name, "t", log=_quiet)
    _lose_all(lib, stamp=False)
    (lib / RB.LOCK).unlink(missing_ok=True)
    g = CG.Guard(lib, "kicad-export")
    why = g.lost_history("AO3401A", item(items_of(plan), "AO3401A"))
    assert why and "kicad_export.json" in why, why


# ---------------------------------------------------------------- M3: one person, however it is typed
def test_approver_names_fold_lookalikes_invisibles_accents_and_punctuation():
    from ecadlib import approvals as AP
    for a, b in (("Ann Lee <ann@x.com>", "Аnn Lee"), ("Ann Lee", "Ann​ Lee"), ("Ann Lee", "Ann Lee."),
                 ("Zoë Ray", "Zoe Ray"), ("ann@x.com", "ann@x.com."), ("ann@x.com", "mailto:ann@x.com"),
                 ("Bob Ray", "Bοb Ray"), ("Ann Lee", "ANN LEE"), ("ann@x.com", "ann+2@x.com")):
        assert AP.same_person(a, b), (a, b)
    assert not AP.same_person("Ann Lee <ann@x.com>", "Bob Ray <bob@x.com>")
    assert not AP.same_person("Ann Lee", "Anna Lee")


def test_the_second_approval_by_the_same_person_names_the_first():
    plan, lib = demo()
    (lib / "ecadlib.toml").write_text('[change]\napprovers = 2\n', encoding="utf-8")
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    g.settle(items)
    CG.write_plan(plan, items)
    CC.approve(plan, ["AO3401A"], who="Ann Lee <ann@x.com>")
    e = raises(CC.approve, plan, ["AO3401A"], who="Аnn​ Lee.", contains="Ann Lee <ann@x.com> approved")
    assert "<U+200B>" in str(e) and "same person" in str(e), e
    [r] = CC.approve(plan, ["AO3401A"], who=BOB)
    assert r["result"] == "released", r


# ---------------------------------------------------------------- the low ones
def test_kicad_export_exits_1_while_a_change_is_held_and_says_what_include_held_did():
    from ecadlib import kicad_export as KX
    plan, lib = demo()
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    g.settle(items)
    CG.write_plan(plan, items)
    with contextlib.redirect_stdout(io.StringIO()):
        assert KX.main([str(plan), "-o", str(lib.parent / "kx"), "--name", "t"]) == 1
    rep = json.loads((lib.parent / "kx" / "kicad_export.json").read_text(encoding="utf-8"))
    assert rep["held_changes"] == ["AO3401A"] and rep["change_control"] is True
    plan2, lib2 = demo()
    _twin_held(plan2, lib2)
    rep = KX.export(plan2, lib2.parent / "kx", "t", include_held=True, log=_quiet)
    assert any("--include-held exported the held version anyway" in w for w in rep["warnings"]), rep["warnings"]


def test_a_digest_that_matches_nothing_says_so():
    plan, lib = demo()
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    g.settle(items)
    CG.write_plan(plan, items)
    raises(CC.approve, plan, ["AO3401A"], ANN, digests={"AO3401A": "0123456789ab"}, contains="no held change has that digest")
    raises(CC.approve, plan, ["AO3401A"], ANN, digests={"AO3401A": "012"}, contains="at least 6 characters")


def test_a_parity_refusal_says_what_to_do():
    plan, lib = demo()
    (lib / "ecadlib.toml").write_text('[release]\nparity = "hold"\n', encoding="utf-8")
    items = items_of(plan)
    g = gate(lib)
    f = FP.kicad_mod_path(item(items, "AO3401A"), lib)
    f.write_text(f.read_text(encoding="utf-8").replace("(at -0.9375 -0.95)", "(at -0.9375 -0.70)", 1), encoding="utf-8")
    g.settle(items)
    CG.write_plan(plan, items)
    d = CG.review_digest(LG.Ledger(lib), "AO3401A")
    e = raises(CC.approve, plan, ["AO3401A"], ANN, digests={"AO3401A": d}, contains="parity")
    from ecadlib import cliutil
    msg, hint, code = cliutil.friendly(e)
    assert hint and "other file" in hint and "part reject" in hint, (msg, hint)


def test_odd_pin_numbers_and_invisible_characters_in_messages():
    assert CD._natkey("1²") and CD._natkey("²")       # '²' is a digit but not a decimal: no crash
    a = FP.of_kicad(_sym([("1", "A", "input", False), ("²", "B", "input", False)]), part="U")
    b = FP.of_kicad(_sym([("1", "A", "input", False), ("²", "C", "input", False)]), part="U")
    assert CD.worst(CD.diff(a, b)) == "electrical"
    assert CD.shown("VCC​") == "VCC<U+200B>" and CD.shown("VCC ") == '"VCC "' and CD.shown("VCC") == "VCC"
    a = FP.of_kicad(_sym([("1", "VCC", "power_in", True)]), part="U")
    b = FP.of_kicad(_sym([("1", "VCC​", "power_in", True)]), part="U")
    assert any("<U+200B>" in c["text"] for c in CD.diff(a, b)), CD.diff(a, b)


def test_a_broken_pipe_is_quiet():
    r = subprocess.run(f'"{sys.executable}" -m ecadlib --help | head -c 10', shell=True, cwd=ROOT, capture_output=True,
                       text=True, timeout=120)
    assert "Traceback" not in r.stderr and "BrokenPipe" not in r.stderr, r.stderr
    from ecadlib import cli

    def boom(*a, **k):
        raise BrokenPipeError
    with mock.patch.object(cli, "os") as fake_os, mock.patch("importlib.import_module") as imp:
        imp.return_value.main = boom
        assert cli.main(["part", "status", "x"]) == 1
        assert fake_os.dup2.called


def test_an_approval_killed_while_putting_files_in_place_is_not_an_overwrite():
    """L: an approval killed after some files were put in place left a mix of released and proposed files; the next
    approval reported it as a newer edit (`overwritten`). Files that are each one version's are no newer edit."""
    plan, lib = demo()
    items = items_of(plan)
    g = gate(lib)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    g.settle(items)
    CG.write_plan(plan, items)
    lg = LG.Ledger(lib)
    prop = lg.proposal("AO3401A")
    for kind, rec in (prop.get("files") or {}).items():         # the proposal's files put in place, then killed
        p = lib / rec["path"] if not Path(rec["path"]).is_absolute() else Path(rec["path"])
        p.write_bytes(lg.get_bytes(rec["sha256"]))
    d = CG.review_digest(lg, "AO3401A")
    rows = CC.approve(plan, ["AO3401A"], ANN, digests={"AO3401A": d}, comment="ok")
    assert rows[0]["result"] == "released" and not any(r.get("warning") for r in rows), rows
    assert not [e for e in LG.Ledger(lib).lines() if e.get("event") == "overwritten"]
    it = item(items_of(plan), "AO3401A")
    head = LG.Ledger(lib).head("AO3401A")
    assert CC._mix_of(it, lib, (head,)) is True
