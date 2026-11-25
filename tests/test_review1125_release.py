"""Findings of the 2026-11-25 full code review, release side. Plain functions, no pytest needed:
python3 -m tests.run_all test_review1125_release"""
import copy

from ecadlib import crosscheck as CC, librarian as L, pin_text as PT, stdparts as SP
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None
PINS = [("1", "IN"), ("2", "GND"), ("3", "EN"), ("4", "NC"), ("5", "OUT")]
URL = "https://www.ti.com/lit/ds/symlink/tlv700.pdf"


class _Reader:
    cfg, estimate_only, model, offline, key, assistant = {}, False, "m", False, "k", None


def _src_datasheet(self, st, item, ln, row, kind, mpn, fp_name, sym_name):
    spec = copy.deepcopy(SP.packages()["SOT-23-5"])
    spec.update(name="SOT-23-5", height_max={"max": 1.45})
    spec["land"] = {"pad_length": 1.1, "pad_width": 0.6, "pad_center": 1.35}   # the maker's own land
    self.reader = _Reader()
    res = {"spec": spec, "symbol": {"pins": [{"num": n, "name": s, "type": "passive"} for n, s in PINS]},
           "sidecar": {"pdf": {"url": URL, "where": "BOM"}}, "needs_review": []}
    job = {"url": URL, "where": "BOM", "part": mpn, "name": item["name"], "data": b"%PDF-1.4"}
    self.put_datasheet(st, res, job, fp_name, sym_name)


def _run(tmp_path, monkeypatch, line):
    monkeypatch.setattr(L.Run, "src_datasheet", _src_datasheet)
    monkeypatch.setattr(CC, "land_second", lambda *a: {
        "source": "second read", "status": "agreed", "agreed": ["pin_count", "pads", "pins", "pin_names"],
        "disagreed": [], "compared": ["pads", "pins", "pin_names"], "against": "second read (m)"})
    monkeypatch.setattr(PT, "check_pdf", lambda *a, **k: {"status": "agreed", "pins": 5, "page": 3, "where": "pin table"})
    (tmp_path / "bom.csv").write_text("Designator,Qty,Value,Footprint,MPN,Manufacturer,Description\n" + line + "\n",
                                      encoding="utf-8")
    return L.run(tmp_path / "bom.csv", out=tmp_path / "out", sources=["standard", "datasheet"], offline=True,
                 cache=tmp_path / "cache", rules={}, log=QUIET)


@lite
def test_a_maker_land_keeps_the_package_clash_of_the_land_it_replaced(tmp_path, monkeypatch):
    # review 2026-11-25 (r1 F1): maker_land swapped the standard SOT-23-5 land for a datasheet: land in the same pass,
    # and the clash checks (which read only kicad: / standard: lands) said nothing: the SC-70-5 description was lost
    items = _run(tmp_path, monkeypatch, "U1,1,TLV70033,SOT-23-5,TLV70033DBVR,Texas Instruments,LDO 3.3V SC-70-5")
    assert len(items) == 1 and items[0]["status"] == "held", items[0].get("hold_reasons")
    assert any("SC-70" in str(h) for h in items[0].get("hold_reasons") or ()), items[0].get("hold_reasons")


@lite
def test_a_maker_land_with_no_clash_still_replaces_the_standard_land(tmp_path, monkeypatch):
    items = _run(tmp_path, monkeypatch, "U1,1,TLV70033,SOT-23-5,TLV70033DBVR,Texas Instruments,LDO 3.3V SOT-23-5")
    src = str((items[0].get("source") or {}).get("footprint"))
    assert src.startswith("datasheet:"), (src, items[0].get("hold_reasons"))
    assert not any("SC-70" in str(h) or "SOT-23" in str(h) and "names" in str(h)
                   for h in items[0].get("hold_reasons") or ()), items[0].get("hold_reasons")


@lite
def test_a_maker_land_second_read_that_disagrees_on_the_pins_holds_the_part(tmp_path, monkeypatch):
    # review 2026-11-25 (r1 F4): the land's second read also compares the pins; a disagreement there was only a note
    from tests import test_maker_land_1125 as ML
    bad = dict(ML.AGREE, status="disagreed", agreed=["pin_count", "pads"],
               disagreed=[{"field": "pin_names", "first": "1 A, 3 K", "second": "1 K, 3 A"}])
    rec, st, run = ML._try(tmp_path, monkeypatch, bad)
    assert rec is None and not run.put and st["footprint"]["source"].startswith("standard:")
    assert any("disagrees on the pins" in v for v in st["verify"]), st["verify"]
