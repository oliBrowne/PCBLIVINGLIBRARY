"""Regression tests from the 2026-10-18 review of the Altium 365 path, the librarian and the sourcing write-back.
Offline (fake HTTP, dry-run pushes). Run:
    python3 -m tests.run_all test_review_1018_a365_librarian"""
import copy, json, tempfile
from pathlib import Path

from ecadlib import altium365 as A, approvals as AP, release_check as RC, rules as RU, sourcing as S
from tests import test_librarian as TL
from tests.test_release_check import make

ANN = "Ann Lee <ann@example.com>"


def _abs(items, d):
    items = json.loads(json.dumps(items))
    for i in items:
        for k in ("symbol", "footprint"):
            i[k]["file"] = str((Path(d) / i[k]["file"]).resolve())
    return items


def test_push_with_house_parameter_names_keeps_a_signed_off_part_releasable():
    """[parameters.names] renames parameters on the way to Altium 365; the approval's content hash is over the plan's
    own names, so the sign-off gate must look at the item before the renaming (was: every approved part skipped as
    'changed since the release check')."""
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        Path(d, "approvals.json").write_text(json.dumps({"required": 1}))
        pol = AP.load_policy(d)
        RC.apply([it], d)
        AP.decide(it, "approve", ANN, base=d, policy=pol)
        assert AP.refresh_status(it, pol, d) == "ready"
        rules = copy.deepcopy(RU.DEFAULTS)
        rules["parameters"]["names"] = {"Manufacturer Part Number": "MPN"}
        r = A.push(_abs([it], d), None, "https://ws", dry_run=True, policy=pol, rules=rules)[0]
        assert r["action"] == "release", r
        sent = {p["name"] for p in r["detail"]["input"]["parameters"]}
        assert "MPN" in sent and "Manufacturer Part Number" not in sent         # still sent in the house names
        it["parameters"][0]["value"] = "OTHER"                                   # a real change is still caught
        r = A.push(_abs([it], d), None, "https://ws", dry_run=True, policy=pol, rules=rules)[0]
        assert r["action"] == "skip" and "changed since the release check" in r["detail"], r


def test_placeholder_mpn_lines_stay_separate_plan_items():
    """Two ICs whose MPN cell says 'N/A' (or '-') were one plan item named 'N/A': the second part vanished from the
    plan and the Altium 365 release."""
    lines = [{"designator": "U1", "qty": "1", "value": "LM358", "footprint": "SOIC-8_3.9x4.9mm_P1.27mm", "mpn": "N/A",
              "manufacturer": "TI", "lcsc": "C7950", "description": "Dual op-amp"},
             {"designator": "U2", "qty": "1", "value": "NE555", "footprint": "SOIC-8_3.9x4.9mm_P1.27mm", "mpn": "N/A",
              "manufacturer": "TI", "lcsc": "C7593", "description": "Timer"},
             {"designator": "U3", "qty": "1", "value": "TL431", "footprint": "SOT-23", "mpn": "-", "manufacturer": "TI"},
             {"designator": "U4", "qty": "1", "value": "LM317", "footprint": "SOT-223", "mpn": "-", "manufacturer": "TI"}]
    items = {i["name"]: i for i in A.plan(lines)}
    assert set(items) == {"LM358", "NE555", "TL431", "LM317"}, sorted(items)
    sup = {n: {p["name"]: p["value"] for p in i["parameters"]}.get("Supplier Part Number 1") for n, i in items.items()}
    assert sup["LM358"] == "C7950" and sup["NE555"] == "C7593"


def test_librarian_matches_placeholder_mpn_lines_by_value():
    bom = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
           "U1,1,PARTA1,SOIC-8_3.9x4.9mm_P1.27mm,ACME,N/A,,First part,https://example.com/a.pdf\n"
           "U2,1,PARTB2,SOIC-8_3.9x4.9mm_P1.27mm,ACME,N/A,,Second part,https://example.com/b.pdf\n")
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, bom=bom, sources=["kicad"])
        url = lambda n: {p["name"]: p["value"] for p in items[n]["parameters"]}.get("ComponentLink2URL")
        assert set(items) == {"PARTA1", "PARTB2"}, sorted(items)
        assert url("PARTA1") == "https://example.com/a.pdf" and url("PARTB2") == "https://example.com/b.pdf"
        assert items["PARTA1"]["source"]["footprint"]                            # its BOM footprint was used


def test_sourcing_write_plan_ignores_placeholder_mpn_rows():
    """A sourcing row for an MPN of '-' normalises to '' and was matched to every plan item without an MPN (generic
    passives), writing that part's LCSC number into all of them."""
    with tempfile.TemporaryDirectory() as d:
        plan = Path(d) / "plan.json"
        plan.write_text(json.dumps([{"name": "RES-0603-10k", "parameters": [{"name": "Value", "value": "10k"}], "cleared": []}]))
        row = {"mpn": "-", "flags": [], "suppliers": [("LCSC", "C25804")], "alternates": [], "kind": "other", "need": 1,
               "chosen": None, "lifecycle": "unknown"}
        assert S.write_plan(plan, [row], 1, "USD", now="t") == []
        assert json.loads(plan.read_text())[0]["parameters"] == [{"name": "Value", "value": "10k"}]
