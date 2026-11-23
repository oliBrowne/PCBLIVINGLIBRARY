"""2026-11-23 full code review, part search and BOM reading (evidence folder a6).

A part is released with no person looking when the part search's package text names its footprint, so the listing the
text is read from has to be this part's own:
  1.11  the BOM names a maker and the source lists the number only from ONE other maker: Mouser, Nexar, DigiKey's
        product-details route and the LCSC searches took that listing (BOM "Vishay SS34", the source lists onsemi's
        SS34 = SMC; Vishay's is SMA). Now "not found", with the note DigiKey's keyword route already wrote.
  a6-3  a "Package (mm)" column holding only 0603 / 0402 was read as metric with no note.
  a6-4  no-MPN lines that differ in a column the reader does not map were merged into one part silently.
  a6-5  a crafted MPN cell made the BOM reader backtrack for minutes.
  a6-7  under a "Manufacturer/MPN" header any words before the number became the maker.
Run:
    python -m tests.test_review1123_part_search"""
import csv, time
from pathlib import Path

from tests.fixtures_lite import lite
from tests.sourcing_fixtures import Router, npart, spec

from ecadlib import bom_triage as BT
from ecadlib import lifecycle as L
from ecadlib import part_lookup as S
from ecadlib import providers as P
from ecadlib.kicad_index import KiCadIndex

NOTE = "SS34 only from onsemi, not Vishay: check the BOM's maker"


def _keys(mp):
    for k, v in dict(MOUSER_API_KEY="k" * 20, DIGIKEY_CLIENT_ID="id", DIGIKEY_CLIENT_SECRET="sec",
                     NEXAR_SUPPLY_TOKEN="tok").items():
        mp.setenv(k, v)


def _row(prov, maker="Vishay", mpn="SS34", **more):
    line = {"designator": "D1", "qty": "1", "mpn": mpn, "manufacturer": maker, "value": mpn, **more}
    return BT.triage([line], KiCadIndex([]), prov if isinstance(prov, list) else [prov],
                     (prov[0] if isinstance(prov, list) else prov).http)[0]


def _mouser(*parts):
    return P.Mouser(Router(lambda m, u, b: (200, {"Errors": [], "SearchResults": {"NumberOfResult": len(parts), "Parts": [
        {"ManufacturerPartNumber": "SS34", "Manufacturer": maker, "Description": "Schottky Diodes & Rectifiers 3A 40V",
         "ProductAttributes": [{"AttributeName": "Package / Case", "AttributeValue": case}]} for maker, case in parts]}})))


def _nexar(*parts):
    return P.Nexar(Router(lambda m, u, b: (200, {"data": {"supSearchMpn": {"hits": len(parts), "results": [
        {"part": npart("SS34", maker, [spec("Case/Package", case, "case_package")])} for maker, case in parts]}}})))


def _dk_prod(maker, case):
    return {"ManufacturerProductNumber": "SS34", "Manufacturer": {"Name": maker},
            "Parameters": [{"ParameterText": "Package / Case", "ValueText": case},
                           {"ParameterText": "Supplier Device Package", "ValueText": case.split(", ")[-1]}]}


def _digikey(details, listed=()):
    def web(method, url, body):
        if "oauth2/token" in url:
            return 200, {"access_token": "t", "expires_in": 600}
        if "productdetails" in url:
            return 200, {"Product": details}
        return 200, {"ExactMatches": [], "Products": list(listed), "ProductsCount": len(listed)}
    return P.DigiKey(Router(web))


# ------------------------------------------------------------------ 1.11 another maker's listing is never taken
@lite
def test_mouser_one_other_makers_listing_is_not_this_part(monkeypatch):
    _keys(monkeypatch)
    r = _row(_mouser(("onsemi", "DO-214AB-2")))
    assert r["src_package"] == "" and r["src_package_from"] == "", r
    assert f"mouser: not found (Mouser lists {NOTE})" in r["notes"], r["notes"]


@lite
def test_nexar_one_other_makers_listing_is_not_this_part(monkeypatch):
    _keys(monkeypatch)
    r = _row(_nexar(("onsemi", "DO-214AB")))
    assert r["src_package"] == "", r
    assert f"nexar: not found (Nexar lists {NOTE})" in r["notes"], r["notes"]


@lite
def test_digikey_product_details_by_another_maker_is_not_this_part(monkeypatch):
    _keys(monkeypatch)
    r = _row(_digikey(_dk_prod("onsemi", "DO-214AB, SMC")))
    assert r["src_package"] == "" and r["src_package_device"] == "", r
    assert f"digikey: not found (DigiKey lists {NOTE})" in r["notes"], r["notes"]


@lite
def test_digikey_takes_the_bom_makers_own_listing_the_keyword_search_shows(monkeypatch):
    # productdetails answers onsemi's product; the keyword search lists Vishay's too: Vishay's case is the part's
    _keys(monkeypatch)
    r = _row(_digikey(_dk_prod("onsemi", "DO-214AB, SMC"),
                      [_dk_prod("onsemi", "DO-214AB, SMC"), _dk_prod("Vishay General Semiconductor", "DO-214AC, SMA")]))
    assert r["src_package"] == "DO-214AC, SMA", r
    assert "only from" not in r["notes"], r["notes"]


@lite
def test_the_bom_makers_own_listing_is_still_taken(monkeypatch):
    _keys(monkeypatch)
    for prov in (_mouser(("onsemi", "DO-214AB-2"), ("Vishay Semiconductors", "DO-214AC-2")),
                 _nexar(("onsemi", "DO-214AB"), ("Vishay", "DO-214AC")),
                 _digikey(_dk_prod("Vishay General Semiconductor - Diodes Division", "DO-214AC, SMA"))):
        r = _row(prov)
        assert "214AC" in r["src_package"], (prov.name, r)
        assert "not found" not in r["notes"], (prov.name, r["notes"])


@lite
def test_a_renamed_maker_is_the_same_maker(monkeypatch):
    # the repo's own comparison (providers.same_maker): ON Semiconductor is onsemi
    _keys(monkeypatch)
    for prov in (_mouser(("onsemi", "DO-214AB-2")), _nexar(("onsemi", "DO-214AB")),
                 _digikey(_dk_prod("onsemi", "DO-214AB, SMC"))):
        r = _row(prov, maker="ON Semiconductor")
        assert "214AB" in r["src_package"], (prov.name, r)


@lite
def test_no_maker_on_the_line_reads_as_before(monkeypatch):
    _keys(monkeypatch)
    for prov in (_mouser(("onsemi", "DO-214AB-2")), _nexar(("onsemi", "DO-214AB")),
                 _digikey(_dk_prod("onsemi", "DO-214AB, SMC"))):
        r = _row(prov, maker="")
        assert "214AB" in r["src_package"], (prov.name, r)


def test_pick_for_the_part_search_refuses_one_other_maker_and_lifecycle_keeps_it():
    items = [{"mpn": "SS34", "m": "onsemi"}]
    part = {"mpn": "SS34", "manufacturer": "Vishay"}
    mpn_of, mfr_of = (lambda i: i["mpn"]), (lambda i: i["m"])
    assert L._pick(items, part, mpn_of, mfr_of, own_only=True) is None
    assert L._pick(items, part, mpn_of, mfr_of) is items[0]              # the lifecycle report: combine() guards it
    assert L._pick(items, {"mpn": "SS34", "manufacturer": ""}, mpn_of, mfr_of, own_only=True) is items[0]
    assert L._pick([{"mpn": "SS34", "m": ""}], part, mpn_of, mfr_of, own_only=True) is not None   # maker not known
    assert L._pick(items + [{"mpn": "SS34", "m": "Vishay Semiconductors"}], part, mpn_of, mfr_of, own_only=True)["m"] \
        == "Vishay Semiconductors"
    # the lifecycle report's own parser still reads the one other maker's listing (and marks it not firm)
    res = L.parse_mouser({"SearchResults": {"Parts": [{"ManufacturerPartNumber": "SS34", "Manufacturer": "onsemi",
                                                      "LifecycleStatus": "Obsolete"}]}}, part, L.LifecycleResult("mouser"))
    assert res.found and res.status == "obsolete" and L._other_maker(part, res)


def _lcsc_web(brand):
    def web(method, url, body):
        if "jlcpcb.com" in url:
            return 200, {"data": {"componentPageInfo": {"list": [
                {"componentCode": "C7950", "componentModelEn": "LM358DR", "componentBrandEn": b,
                 "componentSpecificationEn": "SOP-8", "describe": "Dual op amp"} for b in brand]}}}
        if "easyeda.com" in url:
            return 200, {"success": True, "result": {"title": "LM358DR", "dataStr": {"head": {"c_para": {
                "Manufacturer Part": "LM358DR", "Manufacturer": brand[0], "package": "SOP-8"}}, "shape": ["P~x"]},
                "packageDetail": {"title": "SOP-8", "dataStr": {"shape": ["PAD~x"]}}}}
        return 404, {}
    return Router(web)


def test_lcsc_number_of_another_makers_clone_is_not_taken():
    http = _lcsc_web(["HGSEMI"])
    r = _row([P.JLCPCBSearch(http), P.EasyEDA(http)], maker="Texas Instruments", mpn="LM358DR")
    assert r["lcsc"] == "" and r["src_package"] == "", r
    assert "jlcpcb: not found (LCSC lists LM358DR only from HGSEMI, not Texas Instruments: check the BOM's maker)" \
        in r["notes"], r["notes"]
    assert "MPN -> C7950" not in r["notes"] and not any("easyeda.com" in c["url"] for c in http.calls), r["notes"]


def test_lcsc_number_is_taken_from_the_bom_makers_listing_and_with_no_maker():
    for maker, brand in (("Texas Instruments", ["TI"]), ("Texas Instruments", ["HGSEMI", "Texas Instruments"]),
                         ("", ["HGSEMI"]), ("Texas Instruments", [""])):
        r = _row([P.JLCPCBSearch(_lcsc_web(brand))], maker=maker, mpn="LM358DR")
        assert r["lcsc"] == "C7950 (via MPN)", (maker, brand, r)


def test_pick_listing_says_who_lists_it():
    items = [{"componentCode": "C1", "componentModelEn": "LM358DR", "componentBrandEn": "HGSEMI"},
             {"componentCode": "C2", "componentModelEn": "LM358DR", "componentBrandEn": "UMW(Youtai Semiconductor)"}]
    res = P.pick_listing(items, "LM358DR", P.SourceResult("jlcpcb_search"), maker="Texas Instruments")
    assert res.found is False and not res.lcsc and res.extra.get("other_maker"), res
    assert "only from HGSEMI, UMW" in res.extra["note"], res.extra
    # an LCSC number the BOM itself gives is that listing, whoever makes it (triage says the maker differs)
    res = P.pick_listing(items, "", P.SourceResult("jlcpcb_search"), code="C1", maker="Texas Instruments")
    assert res.found and res.lcsc == "C1", res


class _Plugin(P.Provider):
    """Any source (a plugin, EasyEDA for a BOM's own LCSC number) whose answer is another maker's listing."""
    name = "plug"

    def __init__(self, maker):
        self.http, self.maker = None, maker

    def lookup(self, line):
        return P.SourceResult(self.name, found=True, mpn=line["mpn"], manufacturer=self.maker, package="DO-214AB (SMC)",
                              lcsc="C8678", extra={"device_package": "SMC", "dims": {"Length": "6.9 mm"}})


def test_triage_refuses_case_measurements_and_lcsc_number_of_another_makers_result():
    r = _row(_Plugin("onsemi"))
    assert (r["src_package"], r["src_package_device"], r["src_dims"], r["lcsc"]) == ("", "", "", ""), r
    assert "plug: SS34 is by onsemi, not Vishay: its package and LCSC number are not used" in r["notes"], r["notes"]
    for maker, theirs in (("Vishay", "Vishay General Semiconductor"), ("", "onsemi"), ("Vishay", "")):
        r = _row(_Plugin(theirs), maker=maker)
        assert r["src_package"] == "DO-214AB (SMC)" and r["src_package_device"] == "SMC" and r["src_dims"] \
            and r["lcsc"].startswith("C8678"), (maker, theirs, r)
        assert "not used" not in r["notes"], r["notes"]


# ------------------------------------------------------------------ a6-3 a "Package (mm)" column of 0603 / 0402 only
def _read(tmp_path, text, name="bom.csv"):
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return BT.read_bom(f)[0]


def _holds(lines):
    from ecadlib import altium365 as A, rules as RU
    return {i["name"]: i.get("bom_holds") or [] for i in A.plan(lines, None, rules=RU.DEFAULTS)}


@lite
def test_mm_column_with_only_0603_and_0402_is_noted_and_held(tmp_path):
    lines = _read(tmp_path, "Designator,Qty,Value,Package (mm)\nR1,1,10k,0603\nR2,1,4k7,0402\nC1,1,100nF,0402\n")
    by = {l["designator"]: l for l in lines}
    assert "0603 read as metric = 0201 because the column says mm" in by["R1"].get("bom_note", ""), by["R1"]
    assert "0402 read as metric = 01005 because the column says mm" in by["C1"].get("bom_note", ""), by["C1"]
    for l in lines:                                    # nothing in the column backs the header up: a person looks
        assert "on the header alone" in l.get("bom_hold", ""), l
    holds = _holds(lines)
    assert len(holds) == 3 and all("on the header alone" in " ".join(v) for v in holds.values()), holds
    row = BT.triage(lines, KiCadIndex([]), [], None)[0]
    assert "read as metric = 0201" in row["notes"], row["notes"]


@lite
def test_mm_column_backed_by_a_metric_only_code_is_noted_not_held(tmp_path):
    lines = _read(tmp_path, "Designator,Qty,Value,Package (mm)\nR1,1,10k,0603\nR2,1,4k7,1608\nC1,1,100nF,2012\n")
    by = {l["designator"]: l for l in lines}
    assert by["R1"]["footprint"] == "0201" and by["R2"]["footprint"] == "0603" and by["C1"]["footprint"] == "0805", by
    assert "0603 read as metric = 0201 because the column says mm" in by["R1"]["bom_note"], by["R1"]
    assert not any(l.get("bom_hold") for l in lines), lines
    assert not by["R2"].get("bom_note") and not by["C1"].get("bom_note"), by      # 1608 is metric anywhere: no note
    # a cell that says mm itself needs no other cell to back it
    lines = _read(tmp_path, "Designator,Qty,Value,Package (mm)\nR1,1,10k,0603 mm\n")
    assert lines[0]["footprint"] == "0201" and "read as metric" in lines[0]["bom_note"] and not lines[0].get("bom_hold")
    # an inch column is untouched
    lines = _read(tmp_path, "Designator,Qty,Value,Package\nR1,1,10k,0603\n")
    assert lines[0]["footprint"] == "0603" and not lines[0].get("bom_note") and not lines[0].get("bom_hold"), lines


# ------------------------------------------------------------------ a6-4 no-MPN lines that differ in an unmapped column
@lite
def test_no_mpn_lines_that_differ_in_an_unmapped_column_are_held(tmp_path):
    lines = _read(tmp_path, "Designator,Qty,Value,Footprint,Color,Tolerance,Voltage,Dielectric\n"
                            "D1,1,LED,0603,red,,,\nD2,1,LED,0603,green,,,\nR1,1,10k,0603,,1%,,\nR2,1,10k,0603,,5%,,\n"
                            "C1,1,100n,0402,,,16V,X7R\nC2,1,100n,0402,,,50V,C0G\n")
    holds = _holds(lines)
    assert len(holds) == 3, holds
    said = {n: " ".join(h) for n, h in holds.items()}
    for name, a, b, what in (("LED", "D1", "D2", "Color 'red' vs 'green'"), ("RES-0603-10k", "R1", "R2", "Tolerance '1%' vs '5%'"),
                             ("CAP-0402-100n", "C1", "C2", "Voltage '16V' vs '50V'; Dielectric 'X7R' vs 'C0G'")):
        assert "have no part number" in said[name] and "described differently in other columns" in said[name], said
        assert a in said[name] and b in said[name] and what in said[name], said[name]


@lite
def test_no_mpn_lines_that_say_the_same_are_still_one_part(tmp_path):
    for text in ("Designator,Qty,Value,Footprint\nD1,1,LED,0603\nD2,1,LED,0603\n",
                 # the same cells, an empty cell beside a filled one, another case and spacing: one part
                 "Designator,Qty,Value,Footprint,Color,Tolerance\nD1,1,LED,0603,Red,\nD2,1,LED,0603,red ,\n"
                 "R1,1,10k,0603,,1%\nR2,1,10k,0603,,\n",
                 # a row counter, where the part sits, a second quantity column: not what the part is
                 "Item,Designator,Qty,Value,Footprint,Mid X,Mid Y,Rotation,Layer,Quantity\n"
                 "1,R1,1,10k,0603,10.5,3.2,90,Top,1\n2,R2,1,10k,0603,14.0,3.2,0,Bottom,1\n3,C1,1,100n,0402,1,2,0,Top,1\n",
                 "Pos,Designator,Qty,Value,Footprint,Line Item\n10,R1,1,10k,0603,9\n20,R2,1,10k,0603,12\n",
                 "Designator,Qty,Value,Footprint,Seq\nR1,1,10k,0603,4\nR2,1,10k,0603,5\nR3,1,10k,0603,6\n"):
        holds = _holds(_read(tmp_path, text))
        assert not any(holds.values()), (text, holds)


@lite
def test_a_third_line_is_compared_with_the_first_that_fills_the_cell(tmp_path):
    holds = _holds(_read(tmp_path, "Designator,Qty,Value,Footprint,Color\nD1,1,LED,0603,\nD2,1,LED,0603,red\n"
                                   "D3,1,LED,0603,green\n"))
    assert "BOM lines D2 and D3" in " ".join(holds["LED"]) and "Color 'red' vs 'green'" in " ".join(holds["LED"]), holds


def test_one_maker_written_two_ways_is_one_maker():
    a = {"designator": "R1", "value": "10k", "footprint": "0603", "manufacturer": "Yageo"}
    assert not any(_holds([a, dict(a, designator="R2", manufacturer="YAGEO Corporation")]).values())
    (h,) = _holds([a, dict(a, designator="R2", manufacturer="Vishay")]).values()
    assert h and "Manufacturer 'Yageo' vs 'Vishay'" in h[0], h


@lite
def test_a_house_number_column_is_compared(tmp_path):
    holds = _holds(_read(tmp_path, "Designator,Qty,Value,Footprint,Item Number\nR1,1,10k,0603,100-0023\n"
                                   "R2,1,10k,0603,100-0977\n"))
    assert "Item Number '100-0023' vs '100-0977'" in " ".join(holds["RES-0603-10k"]), holds


@lite
def test_lines_with_a_part_number_are_not_held_for_other_columns(tmp_path):
    lines = _read(tmp_path, "Designator,Qty,Value,Footprint,MPN,Assembly\nR1,1,10k,0603,RC0603FR-0710KL,near U1\n"
                            "R2,1,10k,0603,RC0603FR-0710KL,pull-up\n")
    assert lines[0]["other_cells"] == "Assembly: near U1", lines[0]
    assert not any(_holds(lines).values()), _holds(lines)


def test_no_mpn_lines_with_another_symbol_or_datasheet_are_held():
    a = {"designator": "D1", "value": "LED", "footprint": "0603", "libref": "LED_RED"}
    b = {"designator": "D2", "value": "LED", "footprint": "0603", "libref": "LED_GREEN"}
    (h,) = _holds([a, b]).values()
    assert h and "LibRef 'LED_RED' vs 'LED_GREEN'" in h[0], h
    assert not any(_holds([a, dict(b, libref="led_red")]).values())
    assert not any(_holds([a, dict(b, libref="")]).values())


# ------------------------------------------------------------------ a6-5 a crafted MPN cell does not hang the reader
def test_size_and_value_cell_check_does_not_backtrack():
    for n in (24, 40, 400):
        t = time.time()
        assert BT._not_a_part_number("0603 " * n + "!") is None
        assert time.time() - t < 0.5, (n, time.time() - t)
    t = time.time()
    assert BT._not_a_part_number(("0603 " * 16)[:79] + "!") is None      # the longest cell the check walks
    assert BT._not_a_part_number("0603 4k7 " * 8 + "x") is None
    assert time.time() - t < 0.5, time.time() - t


@lite
def test_a_bom_with_a_40_token_mpn_cell_is_read_at_once(tmp_path):
    t = time.time()
    lines = _read(tmp_path, "Designator,Qty,MPN\nR1,1," + "0603 " * 40 + "!\nR2,1,RC0603FR-0710KL\n")
    assert time.time() - t < 5.0, time.time() - t      # (the old pattern: weeks; the check itself is timed above)
    assert len(lines) == 2 and lines[1]["mpn"] == "RC0603FR-0710KL", lines


def test_size_and_value_cells_read_as_before():
    for cell in ("0805, 4.7uF", "0603 10 ohm", "1608 330", "0402/100nF/16V", "4,7uF 0805", "0603 10k 1%", "0805;22pF",
                 "10 uF 0805 16 V", "0603  4k7", "0603 0603 0603 0603 0603 0603 0603 0603 0603 0603 10k"):
        assert BT._not_a_part_number(cell) == "size_value", cell
    for cell in ("RC0603FR-0710KL", "0603", "10k", "4.7uF 16V", "0603 10k!", "0603-10k", "LM358 0603", "0603 10k x",
                 "GRM188R71H104KA93D", "0603,", ", 0603 10k", "0603 " * 20 + "10k"):
        assert BT._not_a_part_number(cell) != "size_value", cell


if __name__ == "__main__":
    import sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for f in fns:
        f()
    print(f"{len(fns)} tests passed")
    sys.exit(0)
