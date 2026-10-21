"""An erratum that renumbers KiCad's reference pads to the datasheet's (AH1806-W: Diodes' SC59 has pin 2 alone on one
side, KiCad's SOT-23 numbering puts pin 3 there): built the datasheet's way, the part is scored right."""
from ecadlib.eval import runner as R, score as S


def test_ah1806_w_built_by_its_datasheet_matches_the_renumbered_reference():
    data, er = R.load_set("kicad-v1"), R.load_errata("kicad-v1")
    case = next(c for c in data["cases"] if c["mpn"] == "AH1806-W")
    kicad = data["references"][case["footprint"]]
    ref = R.reference_numbers(case, er, data["references"])
    pos = lambda num, fp: next(p[1:3] for p in fp["pads"] if str(p[0]) == num)
    assert pos("2", ref) == pos("3", kicad) and pos("3", ref) == pos("2", kicad) and pos("1", ref) == pos("1", kicad)
    ours = dict(kicad, pads=[[{"2": "3", "3": "2"}.get(str(p[0]), p[0])] + list(p[1:]) for p in kicad["pads"]])
    assert S.score_footprint(ours, ref)["pads"]["ok"]
    assert not S.score_footprint(kicad, ref)["pads"]["ok"]                     # KiCad's own numbering is now the miss


def test_a_case_without_a_renumber_erratum_keeps_its_reference():
    data, er = R.load_set("kicad-v1"), R.load_errata("kicad-v1")
    case = next(c for c in data["cases"] if c["mpn"] == "27C256")
    assert R.reference_numbers(case, er, data["references"]) is data["references"][case["footprint"]]
