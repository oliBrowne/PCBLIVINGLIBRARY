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
        it["status"] = "ready"                              # a hand-edited plan: the recheck still holds it
        assert RC.publish_problems(it, d, None) == [f"verify: {it['verify'][0]}"]
        assert A.push(_absolute([dict(it)], d), None, "https://ws", dry_run=True)[0]["action"] == "skip"
        RC.apply([it], d, allow=["PART1"])                  # release anyway: who, when and what it covers
        assert it["status"] == "ready" and RC.publish_problems(it, d, None) == []
        assert A.push(_absolute([json.loads(json.dumps(it))], d), None, "https://ws", dry_run=True)[0]["action"] == "release"
        it["verify"].append("symbol pinout X from the Y family table (ecadlib.stdparts): check pins 1-3 against the datasheet")
        assert RC.publish_problems(it, d, None) == [f"verify: {it['verify'][1]}"]     # a new doubt is not signed off


def test_dblib_sync_rechecks_ready_parts():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        ok, bad = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
        RC.apply([ok, bad], d)
        bad["parameters"] = [p for p in bad["parameters"] if p["name"] != "Manufacturer"]   # after the check
        (d / "plan.json").write_text(json.dumps([ok, bad]))
        out = S.sync(d / "plan.json", d / "lib")
        acts = {r["name"]: (r["action"], r["detail"]) for r in out["results"]}
        assert acts["PART1"][0] == "added" and acts["PART2"] == ("held", "fields: Manufacturer is empty"), acts
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
