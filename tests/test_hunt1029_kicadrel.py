"""2026-10-29 bug hunt (kicadrel): the KiCad name index, the free Manufacturer fill (#209) and the release check.
No network: every answer is a fake. Run:  python -m tests.test_hunt1029_kicadrel"""
import copy, json, tempfile

from ecadlib import bom_triage as T, providers as P, release_check as RC, rules as RU
from ecadlib.kicad_index import KiCadIndex
from tests import test_librarian as TL, test_part_descriptions as TP

BOM = ("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet\n"
       "U1,1,ABC1234,,,ABC1234,,,\n")


def _ee(maker, code):
    ee = copy.deepcopy(TP.XTAL16)
    ee["result"]["dataStr"]["head"]["c_para"].update({"Manufacturer": maker, "Manufacturer Part": "ABC1234",
                                                      "Supplier Part": code, "name": "ABC1234"})
    return ee


def _jlc(*brands):
    base = json.loads(TP.JLC16)["data"]["componentPageInfo"]["list"][0]
    rows = [dict(base, componentCode=f"C100{i + 1}", componentModelEn="ABC1234", componentBrandEn=b)
            for i, b in enumerate(brands)]
    return json.dumps({"code": 200, "data": {"componentPageInfo": {"list": rows}}})


def _maker(it):
    return {p["name"]: p["value"] for p in it["parameters"]}.get("Manufacturer", "")


# ---------------------------------------------------------------- H-KICADREL-1: a clone's maker filled
def test_maker_not_filled_from_easyeda_when_the_search_lists_several_makers():
    # LCSC lists ABC1234 under two makers: the search's first hit (C1001, CloneCo) gives the LCSC number, and
    # EasyEDA's answer for that number names CloneCo. The part is not CloneCo's because LCSC listed it first:
    # Manufacturer stays empty (a note), as the triage note says
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, http=TP.HTTP({"C1001": _ee("CloneCo", "C1001")}, {"ABC1234": _jlc("CloneCo", "RealMaker")}),
                          bom=BOM, sources=["easyeda"], providers=["jlcpcb_search", "easyeda"])
    it = next(iter(items.values()))
    assert _maker(it) == "", (_maker(it), it.get("field_sources"))
    assert any(w.startswith("Manufacturer is empty") for w in it["release_check"]["warnings"]), it["release_check"]


def test_maker_filled_when_the_search_lists_one_maker():
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, http=TP.HTTP({"C1001": _ee("RealMaker", "C1001")}, {"ABC1234": _jlc("RealMaker")}),
                          bom=BOM, sources=["easyeda"], providers=["jlcpcb_search", "easyeda"])
    it = next(iter(items.values()))
    assert _maker(it) == "RealMaker", (_maker(it), it.get("field_sources"))


def test_maker_from_the_boms_own_lcsc_number_is_filled():
    bom = BOM.replace("ABC1234,,,\n", "ABC1234,C1001,,\n")
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, http=TP.HTTP({"C1001": _ee("RealMaker", "C1001")}), bom=bom, sources=["easyeda"],
                          providers=["jlcpcb_search", "easyeda"])
    it = next(iter(items.values()))
    assert _maker(it) == "RealMaker", (_maker(it), it.get("field_sources"))


class _Src(P.Provider):
    """An official-style source (no CAD data) that found the exact MPN; makers: every maker it lists it under."""
    name, official = "fakeofficial", True

    def __init__(self, maker, makers=None):
        self.maker, self.makers = maker, makers

    def configured(self):
        return True

    def lookup(self, line):
        r = P.SourceResult(self.name, found=True, mpn=line.get("mpn", ""), manufacturer=self.maker)
        if self.makers is not None:
            r.extra["makers"] = self.makers
        return r


def test_official_source_first_of_several_makers_is_not_filled():
    line = {"designator": "U1", "mpn": "ABC1234"}
    rows = T.triage([dict(line)], KiCadIndex([]), [_Src("CloneCo", ["CloneCo", "RealMaker"])], None)
    assert rows[0]["src_manufacturer"] == "", rows[0]
    rows = T.triage([dict(line)], KiCadIndex([]), [_Src("CloneCo")], None)       # it does not say: not filled
    assert rows[0]["src_manufacturer"] == "", rows[0]
    rows = T.triage([dict(line)], KiCadIndex([]), [_Src("RealMaker", ["RealMaker"])], None)
    assert rows[0]["src_manufacturer"] == "RealMaker", rows[0]


def test_official_sources_say_which_makers_list_the_mpn():
    from ecadlib import part_lookup as PL
    data = {"supSearchMpn": {"results": [{"part": {"mpn": "ABC1234", "manufacturer": {"name": m}}}
                                         for m in ("CloneCo", "RealMaker", "Real Maker Inc.")]}}
    info = PL.parse_nexar_part(data, {"mpn": "ABC1234", "manufacturer": ""}, PL.PartInfo("nexar", mpn="ABC1234"))
    assert len(info.makers) == 2 and "CloneCo" in info.makers, info.makers
    res = P.from_part_info(info, P.SourceResult("nexar", mpn="ABC1234"))
    assert len(res.extra.get("makers") or []) == 2, res.extra


# ---------------------------------------------------------------- H-KICADREL-2: a house name for Manufacturer
def test_empty_manufacturer_under_its_house_name_is_a_note_not_a_hold():
    # `ecadlib init` learns house names and writes the required list in them ("Mfr", not "Manufacturer")
    r = copy.deepcopy(RU.DEFAULTS)
    r["parameters"]["names"] = {"Manufacturer": "Mfr", "Manufacturer Part Number": "MPN"}
    r["parameters"]["required"] = ["Mfr", "MPN", "Datasheet", "Description"]
    item = {"name": "X", "description": "d", "parameters": [{"name": "Manufacturer Part Number", "value": "ABC"},
                                                           {"name": "ComponentLink2URL", "value": "https://x.example"}]}
    fails, warns, _ = RC.check_item(item, ".", r)
    assert not [f for f in fails if f[0] == "fields"], fails
    assert any("Mfr is empty" in w for w in warns), warns
    r["release"] = dict(r["release"], manufacturer="hold")
    fails, _, _ = RC.check_item(item, ".", r)
    assert ("fields", "Mfr is empty") in fails, fails


# ---------------------------------------------------------------- H-KICADREL-3: a BOM maker of "-" / "N/A"
def test_placeholder_maker_on_the_bom_is_filled_like_an_empty_one():
    # the release check reads "-", "N/A", "~" as empty (a note), but the fill took them for a maker: the part kept
    # "N/A" as its Manufacturer although the search named its one maker
    for mk in ("-", "N/A", "~"):
        bom = BOM.replace("U1,1,ABC1234,,,ABC1234", f"U1,1,ABC1234,,{mk},ABC1234")
        with tempfile.TemporaryDirectory() as d:
            items, _ = TL.run(d, http=TP.HTTP({"C1001": _ee("RealMaker", "C1001")}, {"ABC1234": _jlc("RealMaker")}),
                              bom=bom, sources=["easyeda"], providers=["jlcpcb_search", "easyeda"])
        it = next(iter(items.values()))
        assert _maker(it) == "RealMaker", (mk, _maker(it), it.get("field_sources"))
        assert not any(w.startswith("Manufacturer is empty") for w in it["release_check"]["warnings"]), it["release_check"]


def test_several_makers_note_only_when_the_bom_names_none():
    http = TP.HTTP({}, {"ABC1234": _jlc("CloneCo", "RealMaker")})
    rows = T.triage([{"designator": "U1", "mpn": "ABC1234", "manufacturer": "RealMaker"},
                     {"designator": "U2", "mpn": "ABC1234"}], KiCadIndex([]), [P.JLCPCBSearch(http)], None)
    assert "the maker is not filled" not in rows[0]["notes"], rows[0]["notes"]
    assert "the maker is not filled" in rows[1]["notes"] and rows[1]["src_manufacturer"] == "", rows[1]


# ---------------------------------------------------------------- review M2: two sources, two makers
def test_two_sources_naming_two_makers_fill_none():
    # LCSC sells ABC1234 under one maker (a clone) and an official source lists it under one other maker: each alone
    # says "one maker", together they say two, so no maker is filled (the clone's name was)
    line = {"designator": "U1", "mpn": "ABC1234"}
    http = TP.HTTP({}, {"ABC1234": _jlc("CloneCo")})
    rows = T.triage([dict(line)], KiCadIndex([]), [P.JLCPCBSearch(http), _Src("RealMaker", ["RealMaker"])], None)
    assert rows[0]["src_manufacturer"] == "", rows[0]
    assert "the maker is not filled" in rows[0]["notes"], rows[0]["notes"]
    rows = T.triage([dict(line)], KiCadIndex([]), [_Src("RealMaker", ["RealMaker"]), _Src("CloneCo", ["CloneCo"])], None)
    assert rows[0]["src_manufacturer"] == "", rows[0]
    # the same company under two spellings is one maker
    rows = T.triage([dict(line)], KiCadIndex([]), [P.JLCPCBSearch(TP.HTTP({}, {"ABC1234": _jlc("Murata")})),
                                                   _Src("Murata Electronics", ["Murata Electronics"])], None)
    assert rows[0]["src_manufacturer"] == "Murata", rows[0]


# ---------------------------------------------------------------- review M4: "N/A" sent to the lookups as a maker
def test_placeholder_maker_is_not_sent_to_the_sources():
    # a BOM maker of "N/A" names none; sent to Nexar / Mouser as the wanted maker ("NA") it made their pick of the
    # exact MPN among two makers say "not found", and the line lost its description, datasheet and lifecycle
    seen = []

    class Rec(_Src):
        def lookup(self, line):
            seen.append(line.get("manufacturer"))
            return super().lookup(line)

    for mk in ("N/A", "-", "~"):
        seen.clear()
        T.triage([{"designator": "U1", "mpn": "ABC1234", "manufacturer": mk}], KiCadIndex([]), [Rec("X", ["X"])], None)
        assert seen == [""], (mk, seen)
    seen.clear()
    T.triage([{"designator": "U1", "mpn": "ABC1234", "manufacturer": "RealMaker"}], KiCadIndex([]), [Rec("X", ["X"])], None)
    assert seen == ["RealMaker"], seen


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
