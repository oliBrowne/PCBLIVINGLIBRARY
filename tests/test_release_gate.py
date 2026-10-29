"""Every publisher runs the release check again before it sends a part's files: a plan that says `ready` without a
check (`altium365 plan --models`, a triage plan, a hand edit) or a file changed after its check is held there, and
parts from doubtful sources (EasyEDA conversions, family-table pinouts, KiCad parts picked by a near name) wait for
a person's sign-off (release anyway / --allow). Run:  python3 -m tests.test_release_gate"""
import json, tempfile
from pathlib import Path

from ecadlib import altium365 as A, altium_dblib_sync as S, altium_lib as AL, kicad_export as KX, release_check as RC
from tests.test_release_check import SOIC, make


class NewParts:
    """A workspace with none of the parts yet: records what would be uploaded and released."""
    def __init__(self):
        self.calls = []

    def release_defs(self, ws):
        return {}

    def find(self, ws, name):
        return None

    def find_model(self, ws, kind, name, folder):
        return None

    def folder_id(self, ws, path):
        return "fld"

    def upload(self, path):
        self.calls.append(("upload", Path(path).name))
        return "id-" + Path(path).name

    def release(self, inp):
        self.calls.append(("release", inp["componentName"] if "componentName" in inp else inp))


def _absolute(items, d):
    for it in items:
        for k in ("symbol", "footprint"):
            if it[k].get("file"):
                it[k]["file"] = str(Path(d) / it[k]["file"])
    return items


def test_push_rechecks_a_plan_that_was_never_checked():
    """`altium365 plan --models` says ready when the files are there; push checks them before anything goes up."""
    lines = [{"designator": "U1", "mpn": "CH340C", "manufacturer": "WCH", "footprint": "SOIC-16", "description": "USB UART"}]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "SchLib").mkdir(); (Path(d) / "PcbLib").mkdir()
        (Path(d) / "SchLib" / "CH340C.SchLib").write_bytes(b"x"); (Path(d) / "PcbLib" / "SOIC-16.PcbLib").write_bytes(b"x")
        items = A.plan(lines, models=d)
        assert items[0]["status"] == "ready" and "release_check" not in items[0], items[0]
        dry = A.push(items, None, "https://ws", dry_run=True)
        assert dry[0]["action"] == "skip" and dry[0]["detail"].startswith("held: files: symbol file does not read back"), dry
        fake = NewParts()
        out = A.push(items, fake, "https://ws")
        assert out[0]["action"] == "skip" and out[0]["recheck"], out
        assert fake.calls == [], fake.calls                 # nothing uploaded, nothing released


def test_push_releases_a_part_that_passes_and_holds_one_changed_after_its_check():
    with tempfile.TemporaryDirectory() as d:
        ok, later = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
        RC.apply([ok, later], d)
        assert (ok["status"], later["status"]) == ("ready", "ready")
        overlap = SOIC.replace('(pad "2" smd roundrect (at -2.4725 -0.635)', '(pad "2" smd roundrect (at -2.4725 -1.7)')
        (Path(d) / later["footprint"]["kicad_mod"]).write_text(overlap)     # edited after the check
        fake = NewParts()
        out = {r["name"]: r for r in A.push([ok, later], fake, "https://ws", folder=Path(d))}
        assert out["PART1"]["action"] == "release", out["PART1"]
        assert out["PART2"]["action"] == "skip" and "footprint: copper: pads 1 and 2 overlap" in out["PART2"]["detail"], out
        assert ("upload", "SOIC-8_other.PcbLib") not in fake.calls and ("upload", "SOIC-8_3.9x4.9mm_P1.27mm.PcbLib") in fake.calls


def test_a_sign_off_is_what_lets_a_doubtful_part_go():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        it["verify"] = ["EasyEDA C8734 conversion: check pads and pins against the datasheet (native converter, format assumed)"]
        it["source"] = {"symbol": "easyeda:C8734", "footprint": "easyeda:C8734"}
        it["status"] = "ready"                              # a hand-edited plan: the recheck still holds it
        assert RC.publish_problems(it, d, None) == [f"verify: {it['verify'][0]}"]
        assert A.push(_absolute([dict(it)], d), None, "https://ws", dry_run=True)[0]["action"] == "skip"
        RC.apply([it], d, allow=["PART1"])                  # release anyway: who, when and what it covers
        assert it["status"] == "ready" and RC.publish_problems(it, d, None) == []
        assert A.push(_absolute([json.loads(json.dumps(it))], d), None, "https://ws", dry_run=True)[0]["action"] == "release"
        it["verify"].append("symbol pinout X from the Y family table (ecadlib.stdparts): check pins 1-3 against the datasheet")
        it["source"]["symbol"] = "standard:X"
        assert RC.publish_problems(it, d, None) == [f"verify: {it['verify'][1]}"]     # a new doubt is not signed off


def test_dblib_sync_rechecks_ready_parts():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        ok, bad = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
        RC.apply([ok, bad], d)
        bad["parameters"] = [p for p in bad["parameters"] if p["name"] != "ComponentLink2URL"]   # after the check
        (d / "plan.json").write_text(json.dumps([ok, bad]))
        out = S.sync(d / "plan.json", d / "lib")
        acts = {r["name"]: (r["action"], r["detail"]) for r in out["results"]}
        assert acts["PART1"][0] == "added" and acts["PART2"] == ("held", "fields: no datasheet link"), acts
        assert not (d / "lib" / "PcbLib" / "SOIC-8_other.PcbLib").exists()
        rows = [r for _, (_, rs) in S.read_tables(d / "lib" / "ecadlib.xlsx").items() for r in rs]
        assert [r["Part Number"] for r in rows] == ["PART1"], rows


def test_kicad_export_rechecks_ready_parts():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        ok, bad = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
        RC.apply([ok, bad], d)
        bad["description"] = ""                             # after the check
        (d / "plan.json").write_text(json.dumps([ok, bad]))
        rep = KX.export(d / "plan.json", d / "kicad", "mylib", log=lambda *a, **k: None)
        assert [p["part"] for p in rep["parts"]] == ["PART1"], rep["parts"]
        skip = {s["part"]: s for s in rep["skipped"]}
        assert skip["PART2"]["why"] == "held: fields: Description is empty", skip
        assert skip["PART2"]["says"][0]["code"] == "fields.no_description", skip
        assert not (d / "kicad" / "mylib.pretty" / "SOIC-8_other.kicad_mod").exists()


def test_librarian_holds_a_footprint_picked_by_a_near_name():
    from tests import test_librarian as TL
    bom = TL.BOM.split("\n")[0] + "\nU5,1,GENERIC8,SOIC-8,ACME,GENERIC8,,Eight pins,https://example.com/g.pdf\n"
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, bom=bom, sources=["kicad"])
        it = items["GENERIC8"]
        assert it["source"]["footprint"] == "kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", it["source"]
        assert [h for h in it["hold_reasons"] if h.startswith("verify: footprint Package_SO:SOIC-8_3.9x4.9mm_P1.27mm "
                                                               "chosen by shortest KiCad name")], it["hold_reasons"]
        assert not [r for r in it["librarian"]["review"] if "chosen by" in r]      # a hold now, not only a note


def test_files_cannot_be_signed_off():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        Path(d, it["footprint"]["file"]).write_bytes(b"not an OLE file")
        it["allow"] = {"by": "--allow", "failures": [f for f in (f"{r}: {m}" for r, m in RC.check_item(it, d)[0])]}
        assert RC.publish_problems(it, d, None)[0].startswith("files: footprint file does not read back")


def test_parts_pulled_from_altium_365_are_not_rechecked():
    it = {"name": "X", "a365": {"id": "c1"}, "status": "existing", "symbol": {"ref": "X", "file": None},
          "footprint": {"ref": "X", "file": None}}
    assert RC.publish_problems(it) == []


def test_a_doubt_an_older_ecadlib_only_noted_still_holds():
    """Up to 0.3 an EasyEDA conversion or a near-name match was a review note, not a hold; a part an older version
    finished keeps that note, so a plan from then (pushed as it is, or kept by the librarian) is held the same way."""
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        note = "EasyEDA C2002 conversion: check pads and pins against the datasheet (native converter, format assumed)"
        it["librarian"] = {"review": [note]}
        assert RC.publish_problems(it, d, None) == []                  # EasyEDA supplied nothing: only a note
        it["source"] = {"symbol": "easyeda:C2002", "footprint": "easyeda:C2002"}
        assert RC.publish_problems(it, d, None) == ["verify: " + note]
        it["status"] = "ready"
        assert A.push(_absolute([dict(it)], d), None, "https://ws", dry_run=True)[0]["action"] == "skip"
        RC.apply([it], d, allow=["PART1"])
        assert it["status"] == "ready" and RC.publish_problems(it, d, None) == []


def _one_emptied(d):
    """PART1 and PART2 both pass the check; PART2's description is emptied after it, and plan.json still says ready."""
    ok, bad = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
    RC.apply([ok, bad], d)
    bad["description"] = ""
    (d / "plan.json").write_text(json.dumps([ok, bad]))
    return d / "plan.json"


def _one_emptied_released(d):
    """_one_emptied, with the emptied PART2 recorded as its released revision (as a version released before a stricter
    rule was, or by an older ecadlib that released pass / warn changes without the release check). Since the fix
    a field edit that fails the check is held for approval instead (tests/test_fix1026_changes.py), and the released
    version is put back: these tests are about a released head that fails the check now."""
    from ecadlib import change_gate as CG, fingerprint as FP
    plan = _one_emptied(d)
    items = json.loads(plan.read_text())
    g = CG.Guard(d, "librarian")
    g.hold_lock()
    try:
        g._release(items[1], FP.of_item(items[1], d), [], {"action": "pass", "why": []})
    finally:
        g.close()
    return plan


def test_kicad_dblib_export_parts_and_plugins_recheck_ready_parts():
    from ecadlib import kicad_dblib as KD, parts_export as PE, plugins as PL
    from tests.test_change_control_round4 import _EP
    with tempfile.TemporaryDirectory() as t:
        plan = _one_emptied(Path(t))
        tables, skipped = KD.part_rows(plan, "mylib")
        assert [r["Part ID"] for rs in tables.values() for r in rs] == ["PART1"], tables
        assert skipped == [{"part": "PART2", "why": "held: fields: Description is empty"}], skipped
        assert [r["Part ID"] for r in PE.rows(plan)] == ["PART1"]
        held = {r["Part ID"]: r for r in PE.rows(plan, include_held=True)}["PART2"]
        assert (held["Status"], held["Release Check"], held["Hold Reasons"]) == ("held", "held", "fields: Description is empty")
        house = Path(t) / "cfg" / "house.toml"                        # the rules the library is checked with
        house.parent.mkdir()
        house.write_text('[parameters]\nrequired = ["Manufacturer", "Manufacturer Part Number"]\n')
        assert [r["Part ID"] for r in PE.rows(plan, rules=house)] == ["PART1", "PART2"]
        seen = {}
        PL.run_exporter("t", plan, Path(t) / "px", eps=[_EP("t", lambda items, out, rules: seen.update(
            {i["name"]: (i["status"], i.get("hold_reasons")) for i in items}) or [])])
        assert seen == {"PART1": ("ready", []), "PART2": ("held", ["fields: Description is empty"])}, seen


def test_kicad_export_keeps_a_released_part_it_now_holds():
    """dblib-sync leaves a held part's row and files alone and push sends nothing for it; kicad-export keeps the version
    it exported before too, and says why, instead of taking the part out as if it had left the plan."""
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        ok, two = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
        RC.apply([ok, two], d)
        (d / "plan.json").write_text(json.dumps([ok, two]))
        quiet = lambda *a, **k: None
        assert [p["part"] for p in KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)["parts"]] == ["PART1", "PART2"]
        sym, fp = d / "kicad" / "mylib.kicad_sym", d / "kicad" / "mylib.pretty" / "SOIC-8_other.kicad_mod"
        exported = (sym.read_text(), fp.read_text())
        _one_emptied_released(d)
        rep = KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        assert [p["part"] for p in rep["parts"]] == ["PART1"] and rep["skipped"][0]["recheck"], rep["skipped"]
        assert (sym.read_text(), fp.read_text()) == exported            # the symbol and footprint stay as exported
        assert rep["kept"] == [{"part": "PART2", "symbol_name": "PART2", "symbol": True, "footprint": "SOIC-8_other"}]
        assert rep["warnings"] == ["PART2: held (fields: Description is empty); the version exported before stays in "
                                   "mylib until it passes the check again"], rep["warnings"]
        again = KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)      # kept on the next run too
        assert again["kept"] == rep["kept"] and (sym.read_text(), fp.read_text()) == exported
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as shown:
            KX.print_result(again, d / "kicad", d / "plan.json")
        said = " ".join(shown.getvalue().split())                    # as one line, whatever the terminal width
        assert "1 held part kept as exported before" in said and "left out" not in said, said
        two["description"] = "Test part"                                # passes again: written anew
        (d / "plan.json").write_text(json.dumps([ok, two]))
        back = KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        assert [p["part"] for p in back["parts"]] == ["PART1", "PART2"] and back["kept"] == [] and not back["warnings"]


def test_kicad_dblib_keeps_the_row_of_a_part_kicad_export_kept():
    """kicad-dblib --export: the database row of a released part the check now holds stays as exported before (its
    released fields, not the held ones), like its symbol and footprint."""
    import sqlite3
    from contextlib import closing
    from ecadlib import kicad_dblib as KD
    with tempfile.TemporaryDirectory() as t:
        d, quiet = Path(t), lambda *a, **k: None
        ok, two = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
        RC.apply([ok, two], d)
        (d / "plan.json").write_text(json.dumps([ok, two]))
        rep = KD.build(d / "plan.json", d / "k", "mylib", export=True, log=quiet)

        def described():
            with closing(sqlite3.connect(d / "k" / "mylib.sqlite")) as con:
                return {r[0]: r[1] for t in rep["tables"] for r in
                        con.execute(f'SELECT "Part ID", "Description" FROM {KD._ident(t)}').fetchall()}
        assert described() == {"PART1": "Test part", "PART2": "Test part"}
        _one_emptied_released(d)
        rep = KD.build(d / "plan.json", d / "k", "mylib", export=True, log=quiet)
        assert described() == {"PART1": "Test part", "PART2": "Test part"} and rep["skipped"] == [], rep
        assert "PART2: held (fields: Description is empty); the row exported before stays in mylib.sqlite until it " \
               "passes the check again" in rep["warnings"], rep["warnings"]
        assert not [w for w in rep["warnings"] if "is not in mylib" in w], rep["warnings"]


def test_kicad_export_says_a_held_part_it_takes_out_was_not_released():
    """A part --include-held exported was never released: a normal export takes it out, and says it is held."""
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        ok, bad = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
        bad["description"] = ""
        RC.apply([ok, bad], d)
        assert bad["status"] == "held"
        (d / "plan.json").write_text(json.dumps([ok, bad]))
        quiet = lambda *a, **k: None
        for plan in ("held", "says ready"):         # held by release-check, or a plan that says ready and fails it
            if plan == "says ready":
                bad["description"] = "Test part"
                RC.apply([ok, bad], d)
                bad["description"] = ""
                (d / "plan.json").write_text(json.dumps([ok, bad]))
                assert bad["status"] == "ready"
            KX.export(d / "plan.json", d / "kicad", "mylib", include_held=True, log=quiet)
            assert "PART2" in (d / "kicad" / "mylib.kicad_sym").read_text()
            rep = KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
            assert rep["kept"] == [] and "PART2" not in (d / "kicad" / "mylib.kicad_sym").read_text(), plan
            assert rep["warnings"] == ["PART2: held (fields: Description is empty); taken out of mylib until it is "
                                       "released (a KiCad project using it loses the link)"], (plan, rep["warnings"])


def test_kicad_export_does_not_take_in_a_hand_edit_as_the_kept_version():
    """A held part is kept only from the symbol library as the last export wrote it: edited by hand since, the edit is
    copied aside as for any other part and the held part goes, so a kicad-pcm package never carries the edit."""
    with tempfile.TemporaryDirectory() as t:
        d, quiet = Path(t), lambda *a, **k: None
        ok, two = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
        RC.apply([ok, two], d)
        (d / "plan.json").write_text(json.dumps([ok, two]))
        KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        _one_emptied_released(d)
        sym = d / "kicad" / "mylib.kicad_sym"
        sym.write_text(sym.read_text().replace('(property "Value" "PART2"', '(property "Value" "PART2-EDITED"', 1))
        rep = KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        assert rep["kept"] == [] and "PART2" not in sym.read_text(), rep["kept"]
        assert rep["warnings"] == [
            "PART2: held (fields: Description is empty); taken out of mylib until it is released (a KiCad project using "
            "it loses the link)", "mylib.kicad_sym: edited since the last export; replaced from the plan (the edited file "
            "is in ecadlib-hand-edits/mylib.kicad_sym)"], rep["warnings"]
        assert "PART2-EDITED" in (d / "kicad" / "ecadlib-hand-edits" / "mylib.kicad_sym").read_text()


def test_kicad_export_keeps_a_hand_put_3d_file_the_users():
    """A STEP file put in <lib>.3dshapes by hand (what an offline export says to do) stays the user's when its part is
    kept: once the part leaves the plan the file is moved aside, not deleted."""
    model = '\t(model "${KICAD8_3DMODEL_DIR}/Package_SO.3dshapes/MYPKG.step"\n\t\t(offset (xyz 0 0 0))\n' \
            '\t\t(scale (xyz 1 1 1))\n\t\t(rotate (xyz 0 0 0))\n\t)\n'
    with tempfile.TemporaryDirectory() as t:
        d, quiet = Path(t), lambda *a, **k: None
        i = SOIC.rstrip().rindex(")")
        ok, two = make(d), make(d, kmod=SOIC[:i] + model + ")\n", name="PART2", fp_name="SOIC-8_other")
        RC.apply([ok, two], d)
        (d / "plan.json").write_text(json.dumps([ok, two]))
        step = d / "kicad" / "mylib.3dshapes" / "MYPKG.step"
        step.parent.mkdir(parents=True)
        step.write_bytes(b"ISO-10303-21; the user's own STEP file\n")
        KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        _one_emptied_released(d)
        rep = KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        assert rep["kept"] and "mylib.3dshapes/MYPKG.step" not in rep["written"], rep["written"]
        (d / "plan.json").write_text(json.dumps([ok]))                 # PART2 leaves the plan
        rep = KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        assert (d / "kicad" / "ecadlib-hand-edits" / "mylib.3dshapes" / "MYPKG.step").read_bytes().startswith(b"ISO")
        assert "mylib.3dshapes/MYPKG.step: not written by ecadlib and not in the plan: moved to " \
               "ecadlib-hand-edits/mylib.3dshapes/MYPKG.step" in rep["warnings"], rep["warnings"]


def test_a_doubt_goes_when_a_person_picks_another_half():
    """A doubtful-source note holds the part until a person releases it or picks another symbol / footprint for that
    half (ecadlib resolve, the review queue's Pick). The plan's `source` alone decides nothing: the librarian gives a
    part another part's shared record and its source, and change control can hold a pick back."""
    import io
    from contextlib import redirect_stdout
    from ecadlib import kicad_import as K, resolve as R, review_cli as RV
    from tests import test_librarian as TL
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        easy = "EasyEDA C2 conversion: check pads and pins against the datasheet (native converter, format assumed)"
        guess = "footprint Package_SO:SOIC-8_X chosen by shortest KiCad name starting with 'SOIC-8': compare with the " \
                "datasheet land pattern"
        it["verify"] = [easy, guess]
        it["source"] = {"symbol": "kicad:Device:Q", "footprint": "easyeda:C1"}        # a record shared from C1
        assert RC.doubts(it) == [easy, guess]
        assert RC.clear_doubts(it, it["source"], ["footprint"]) == [guess] and it["verify"] == [easy]
    bom = TL.BOM.split("\n")[0] + "\nU5,1,GENERIC8,SOIC-8,ACME,GENERIC8,,Eight pins,https://example.com/g.pdf\n"
    with tempfile.TemporaryDirectory() as d:
        items, http = TL.run(d, bom=bom, sources=["kicad"])
        alt = TL.FOOTS["Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"].replace("SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8_ALT") \
            .replace("(size 1.965 0.58)", "(size 1.765 0.58)")          # other pads: a change control looks at
        http.routes[K.GITLAB.format(repo="kicad-footprints", path="Package_SO.pretty/SOIC-8_ALT.kicad_mod")] = alt
        plan, src = Path(d) / "out" / "plan.json", R.Sources(Path(d) / "cache", http=http)
        part = lambda: next(i for i in json.loads(plan.read_text()) if i["name"] == "GENERIC8")
        noted = lambda it: [v for v in it["verify"] if "chosen by shortest" in v]
        r = R.choose(plan, "GENERIC8", "symbol", "kicad:Regulator_Test:TPS1234DSG", src=src, user="Rev <r@example.com>")
        assert r["status"] == "held" and [h for h in r["hold_reasons"] if "chosen by shortest" in h], r   # the symbol
        assert noted(part())                                     # pick says nothing about the footprint: still noted
        with redirect_stdout(io.StringIO()):
            RV.release_anyway(plan, "GENERIC8", user="Ann Lee <ann@example.com>")    # released as revision 1
        r = R.choose(plan, "GENERIC8", "footprint", "kicad:Package_SO:SOIC-8_ALT", src=src, approve=False)
        assert [c["action"] for c in r["change"]] == ["hold"], r      # an agent's pick of a released part waits
        assert [v for v in RC.doubts(part()) if "chosen by shortest" in v]   # the released footprint is still the guess
        r = R.choose(plan, "GENERIC8", "footprint", "kicad:Package_SO:SOIC-8_ALT", src=src, user="Rev <r@example.com>")
        assert part()["source"]["footprint"] == "kicad:Package_SO:SOIC-8_ALT" and not noted(part()), part()["verify"]
        assert r["status"] == "ready" and r["hold_reasons"] == [], r
        state = json.loads((plan.parent / "librarian_state.json").read_text())
        assert not noted(state["GENERIC8"])                      # a librarian re-run does not bring it back
        items, _ = TL.run(d, http=http, bom=bom, sources=["kicad"])
        assert items["GENERIC8"]["status"] == "ready" and not noted(items["GENERIC8"]), items["GENERIC8"]["hold_reasons"]


def test_a_hand_written_allow_does_not_release_a_broken_file():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        Path(d, it["footprint"]["file"]).write_bytes(b"not an OLE file")
        it["allow"] = {"by": "--allow", "failures": [f"{r}: {m}" for r, m in RC.check_item(it, d)[0]]}
        RC.apply([it], d)
        assert it["status"] == "held" and it["hold_reasons"][0].startswith("files: footprint file does not read back")
        assert it["hold_reasons"][0].endswith("(cannot be overridden)"), it["hold_reasons"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
