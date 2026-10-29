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


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
