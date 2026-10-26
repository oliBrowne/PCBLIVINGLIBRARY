"""Change control (#105) over what the publishers actually ship.

The KiCad publishers (kicad-export, kicad-pcm, kicad-dblib --export) ship each part's .kicad_mod: it is fingerprinted,
so a hand edit or a plan pointing at another file is held. A symbol they fetch again from the part's source (kicad:,
local:, easyeda:) must be the released revision, else the released .SchLib is exported. The Altium 365 push never
changes the identity of a component already in the workspace in place (a BOM with no librarian plan, no ledger)."""
import json, re
from pathlib import Path

from ecadlib import change_gate as CG, fingerprint as FP, kicad_export as KX, ledger as LG
from tests.test_change_control import FakeA365, demo, item, items_of

R0603 = "models/KiCad/R_0603_1608Metric.kicad_mod"


def _export(plan, out, **kw):
    return KX.export(plan, out, "x", log=lambda *a, **k: None, **kw)


def _pads(text):
    return re.findall(r'\(pad "(\w+)" smd \w+\s*\(at (-?[\d.]+) (-?[\d.]+)', text)


def _edit_r0603(lib):
    """Pad 1 moved 1 mm, pad 2 renumbered 3, in the .kicad_mod only (the PcbLib is untouched)."""
    k = lib / R0603
    t = k.read_text(encoding="utf-8")
    t = re.sub(r'\(pad "1" smd roundrect\s*\(at (-?[\d.]+) ', lambda m: f'(pad "1" smd roundrect\n\t\t(at {float(m.group(1)) - 1:.4f} ', t, count=1)
    k.write_text(t.replace('(pad "2"', '(pad "3"', 1), encoding="utf-8")


# ---------------------------------------------------------------- C1: the .kicad_mod is fingerprinted
def test_kicad_mod_hand_edit_is_held_and_the_released_footprint_ships():
    plan, lib = demo()
    before = (lib / R0603).read_bytes()
    _edit_r0603(lib)
    rep = _export(plan, lib.parent / "kx")
    assert (lib / R0603).read_bytes() == before, "the held edit is put back"
    shipped = (lib.parent / "kx" / "x.pretty" / "R_0603_1608Metric.kicad_mod").read_text(encoding="utf-8")
    assert [p[0] for p in _pads(shipped)] == ["1", "2"] and _pads(shipped)[0][1] == "-0.825", _pads(shipped)
    items = items_of(plan)
    for name in ("RES-0603-10k", "RES-0603-4.7k"):          # both use the footprint
        ch = item(items, name)["change"]
        assert ch["state"] == "held" and ch["classes"].get("electrical") and ch["classes"].get("geometry-major"), ch
        assert any("KiCad footprint" in c["text"] for c in ch["changes"]), ch
    assert any("held for approval" in w for w in rep["warnings"])
    assert LG.Ledger(lib).head("RES-0603-10k")["revision"] == 1


def test_plan_pointing_kicad_mod_at_another_file_is_held():
    plan, lib = demo()
    items = items_of(plan)
    item(items, "AO3401A")["footprint"]["kicad_mod"] = "models/KiCad/SOT-223-3_TabPin2.kicad_mod"
    CG.write_plan(plan, items)
    status, _ = CG.publish_guard(plan, items_of(plan), "kicad")
    it = item(items_of(plan), "AO3401A")
    assert it["change"]["state"] == "held" and it["footprint"]["kicad_mod"] == "models/KiCad/SOT-23.kicad_mod", it


def test_kicad_mod_and_pcblib_disagreeing_changes_the_land_hashes():
    plan, lib = demo()
    it = item(items_of(plan), "RES-0603-10k")
    a = FP.of_item(it, lib)
    assert a["kicad"] and "kicad" not in FP.geometry(a) and "kicad_pads" not in FP.electrical(a), "the two files agree"
    _edit_r0603(lib)
    b = FP.of_item(it, lib)
    assert b["hashes"]["gid"] != a["hashes"]["gid"] and b["hashes"]["eid"] != a["hashes"]["eid"]
    assert FP.electrical(b)["kicad_pads"] == ["1", "3"]


def test_a_deleted_pcblib_is_still_held_when_the_kicad_mod_is_there():
    plan, lib = demo()
    f = lib / item(items_of(plan), "AO3401A")["footprint"]["file"]
    before = f.read_bytes()
    f.unlink()
    CG.publish_guard(plan, items_of(plan), "kicad")
    assert item(items_of(plan), "AO3401A")["change"]["state"] == "held" and f.read_bytes() == before


def test_kicad_mod_beside_the_pcblib_without_a_plan_key_is_snapshotted_and_held():
    """An older plan without footprint.kicad_mod: kicad-export still ships models/KiCad/<ref>.kicad_mod."""
    plan, lib = demo()
    items = items_of(plan)
    for i in items:
        i["footprint"].pop("kicad_mod", None)
    CG.write_plan(plan, items)
    assert LG.item_files(items[0])["kicad_mod"].startswith("models/KiCad/")
    CG.publish_guard(plan, items_of(plan), "kicad")        # a cosmetic-free re-baseline: same files
    assert all("change" not in i for i in items_of(plan) if i["status"] == "ready")
    _edit_r0603(lib)
    CG.publish_guard(plan, items_of(plan), "kicad")
    assert item(items_of(plan), "RES-0603-10k")["change"]["state"] == "held"


def _age_ledger(lib):
    """Every ledger line as an ecadlib before this fix wrote it: fingerprint schema 1, no .kicad_mod in it."""
    lg = LG.Ledger(lib)
    out = []
    for line in lg.path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("fingerprint"):
            fp = lg.get_json(e["fingerprint"])
            fp["schema"] = 1
            fp.pop("kicad", None)
            fp["hashes"] = FP.hashes(fp)
            e["fingerprint"], e["digest"] = lg.put_json(fp), fp["hashes"]["digest"]
            e.update({k: fp["hashes"][k] for k in ("eid", "gid", "mid", "cid")})
        out.append(json.dumps(e, sort_keys=True, ensure_ascii=False))
    lg.path.write_text("\n".join(out) + "\n", encoding="utf-8")
    import shutil                              # ... which kept no release marks either (R-G2-3)
    shutil.rmtree(lg.store / "marks", ignore_errors=True)


def test_library_released_before_the_kicad_mod_was_fingerprinted_shows_no_change_until_it_changes():
    plan, lib = demo()
    _age_ledger(lib)
    n = len(LG.Ledger(lib).lines())
    _export(plan, lib.parent / "kx")
    lines = LG.Ledger(lib).lines()[n:]
    assert not [e for e in lines if e["event"] in ("release", "hold")], lines
    assert all("change" not in i for i in items_of(plan) if i["status"] == "ready")
    _edit_r0603(lib)
    _export(plan, lib.parent / "kx")
    assert item(items_of(plan), "RES-0603-10k")["change"]["state"] == "held"


# ---------------------------------------------------------------- C2: a re-fetched symbol must be the released one
SYM = """(kicad_symbol_lib (version 20231120) (generator test)
  (symbol "QP" (pin_names (offset 0)) (in_bom yes) (on_board yes)
    (property "Reference" "Q" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "QP" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (symbol "QP_1_1"
      (pin input line (at -5.08 0 0) (length 2.54) (name "{0}" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      (pin passive line (at 2.54 5.08 270) (length 2.54) (name "{1}" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      (pin passive line (at 2.54 -5.08 90) (length 2.54) (name "D" (effects (font (size 1.27 1.27)))) (number "3" (effects (font (size 1.27 1.27)))))
    )
  )
)
"""


def _pins(path, name):
    sym, _ = FP.kicad_sym_file(path, name)
    return {n: p["raw"] for n, p in sym["pins"].items()}


def test_a_symbol_changed_at_its_source_since_the_release_is_not_shipped():
    plan, lib = demo()
    items = items_of(plan)
    item(items, "AO3401A")["source"]["symbol"] = "local:Mine:QP"
    CG.write_plan(plan, items)
    klib = lib.parent / "klib"
    klib.mkdir()
    (klib / "Mine.kicad_sym").write_text(SYM.format("G", "S"), encoding="utf-8")   # as released: G S D
    rep = _export(plan, lib.parent / "k1", libs=[klib])
    row = next(r for r in rep["parts"] if r["part"] == "AO3401A")
    assert row["symbol"].startswith("local KiCad Mine:QP"), row
    assert _pins(lib.parent / "k1" / "x.kicad_sym", "AO3401A") == {"1": "G", "2": "S", "3": "D"}
    (klib / "Mine.kicad_sym").write_text(SYM.format("S", "G"), encoding="utf-8")   # upstream swapped G and S
    rep = _export(plan, lib.parent / "k2", libs=[klib])
    row = next(r for r in rep["parts"] if r["part"] == "AO3401A")
    assert "read back" in row["symbol"] and "not the released revision" in row["symbol"], row
    assert _pins(lib.parent / "k2" / "x.kicad_sym", "AO3401A") == {"1": "G", "2": "S", "3": "D"}
    assert any("AO3401A" in w and "not the released revision" in w for w in rep["warnings"]), rep["warnings"]


# ---------------------------------------------------------------- N1: Altium 365 push without a ledger
def test_a365_push_without_a_ledger_never_changes_a_components_identity_in_place():
    from ecadlib import altium365 as A
    plan, lib = demo()
    ws, nx = "https://test.365.altium.com", FakeA365()

    def push(edit=None):
        items = [i for i in items_of(plan) if i["name"] == "RES-0603-10k"]
        for i in items:
            for k in ("symbol", "footprint"):
                i[k]["file"] = str(lib / i[k]["file"])
            if edit:
                edit(i)
        return {r["name"]: r for r in A.push(items, nx, ws)}         # folder=None: the web UI's triage path
    assert push()["RES-0603-10k"]["action"] == "release"
    nx.calls.clear()

    def mpn(i):
        for p in i["parameters"]:
            if p["name"] == "Manufacturer Part Number":
                p["value"] = "RC0603FR-07100RL"
    r = push(mpn)["RES-0603-10k"]
    assert r["action"] == "skip" and "identity change" in r["detail"] and "RC0603FR-07100RL" in r["detail"], r
    assert not [c for c in nx.calls if c[0] in ("update_params", "release")], nx.calls

    def value(i):
        for p in i["parameters"]:
            if p["name"] == "Value":
                p["value"] = "100R"
    assert push(value)["RES-0603-10k"]["action"] == "skip"
    assert not nx.calls

    def desc(i):
        i["parameters"].append({"name": "Note", "value": "house stock"})
    assert push(desc)["RES-0603-10k"]["action"] == "update_parameters"
    assert [c[0] for c in nx.calls] == ["update_params"], nx.calls
