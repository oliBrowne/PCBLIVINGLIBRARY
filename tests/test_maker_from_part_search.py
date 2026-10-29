"""Add part with the Manufacturer box empty (2026-10-29 full test, in Altium: NE555DR built from the KiCad library and
then held, "fields: Manufacturer is empty"). The KiCad library records no maker, so the part search's maker for that
exact MPN fills it, and only when that MPN is listed under one maker: LCSC also lists clones under the same MPN, and a
maker picked from several could name the wrong company. python3 -m tests.test_maker_from_part_search"""
import json, tempfile

from ecadlib import providers as P
from ecadlib.httpcache import Response
from tests import test_part_descriptions as TD

BOM = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
       "U1,1,TPS1234DSGR,,,TPS1234DSGR,,,\n")                      # as Add part writes it with the box left empty


def _hit(code, mpn, brand):
    return {"componentCode": code, "componentModelEn": mpn, "componentBrandEn": brand,
            "componentSpecificationEn": "SOIC-8", "describe": "Test regulator SOIC-8"}


def _answer(*hits):
    return json.dumps({"data": {"componentPageInfo": {"list": list(hits)}}})


def _run(jlc_answer):
    with tempfile.TemporaryDirectory() as d:
        items, _ = TD.TL.run(d, http=TD.HTTP({}, {"TPS1234DSGR": jlc_answer} if jlc_answer else {}), bom=BOM, sources=["kicad"],
                             providers=["jlcpcb_search"])
        triage = (TD.Path(d) / "out" / "triage.csv").read_text(encoding="utf-8")
    return next(iter(items.values())), triage


def _par(it, name):
    return next((p["value"] for p in it.get("parameters") or [] if p["name"] == name), "")


def test_parse_lists_every_maker_of_the_exact_mpn():
    r = Response(200, _answer(_hit("C1", "TPS1234DSGR", "TI(德州仪器)"), _hit("C2", "TPS1234DSGR", "UMW(友台半导体)"),
                              _hit("C3", "TPS1234DSGR-Q1", "Other")))
    res = P.parse_jlcpcb(r, "TPS1234DSGR", P.SourceResult("jlcpcb_search"))
    assert res.found and res.extra["makers"] == ["TI", "UMW"], res.extra       # the -Q1 is another MPN
    one = P.parse_jlcpcb(Response(200, _answer(_hit("C1", "TPS1234DSGR", "TI(德州仪器)"),
                                              _hit("C9", "TPS1234DSGR", "Texas Instruments"))),
                         "TPS1234DSGR", P.SourceResult("jlcpcb_search"))
    assert len(one.extra["makers"]) == 1, one.extra                             # TI and Texas Instruments: one maker


def test_one_maker_fills_the_empty_manufacturer_and_says_where_from():
    it, _ = _run(_answer(_hit("C3003", "TPS1234DSGR", "TI(德州仪器)")))
    assert it["source"]["symbol"].startswith("kicad:"), it["source"]           # KiCad built it, as in Altium
    assert _par(it, "Manufacturer") == "TI", it.get("parameters")
    assert "exact MPN" in it["field_sources"]["Manufacturer"] and "LCSC/JLCPCB" in it["field_sources"]["Manufacturer"]
    assert not any("Manufacturer is empty" in h for h in it.get("hold_reasons") or []), it.get("hold_reasons")


def test_clones_under_the_same_mpn_fill_nothing():
    it, triage = _run(_answer(_hit("C3003", "TPS1234DSGR", "TI(德州仪器)"), _hit("C4004", "TPS1234DSGR", "UMW(友台半导体)")))
    assert not _par(it, "Manufacturer"), it.get("parameters")
    assert any("Manufacturer is empty" in h for h in it.get("hold_reasons") or []), it.get("hold_reasons")
    assert "listed under 2 makers (TI, UMW)" in triage, triage


def test_a_bom_maker_is_never_replaced():
    bom = BOM.replace("TPS1234DSGR,,,TPS1234DSGR", "TPS1234DSGR,,ACME,TPS1234DSGR")
    with tempfile.TemporaryDirectory() as d:
        items, _ = TD.TL.run(d, http=TD.HTTP({}, {"TPS1234DSGR": _answer(_hit("C3003", "TPS1234DSGR", "TI(德州仪器)"))}),
                             bom=bom, sources=["kicad"], providers=["jlcpcb_search"])
    it = next(iter(items.values()))
    assert _par(it, "Manufacturer") == "ACME", it.get("parameters")


def test_no_answer_from_the_search_keeps_the_hold():
    it, _ = _run(None)                                                          # refused, as from the cloud
    assert not _par(it, "Manufacturer")
    assert any("Manufacturer is empty" in h for h in it.get("hold_reasons") or []), it.get("hold_reasons")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
