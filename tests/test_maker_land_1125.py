"""The maker's recommended land wins over a standard table's, where the datasheet is read anyway (Oliver 2026-11-25:
"Only where a datasheet is read anyway"). A part whose footprint the standard source built and whose symbol comes
from a datasheet read: when the read gives the maker's land, it is this package's (near the standard land's pads),
and a second read of the land drawing gives every pad the same place and size, the read's footprint replaces the
standard one. Otherwise the standard land stays; nothing is newly held but a second read your assistant still owes.
Plain functions: python3 -m tests.run_all test_maker_land_1125"""
import copy
from ecadlib import librarian as L, crosscheck as CC, stdparts as SP, footprint_gen as F, datasheet as D, footprint_bench as B
from tests.fixtures_lite import lite

LAND = {"pad_length": 0.95, "pad_width": 0.56, "pad_center": 0.975}          # onsemi case 318's SOT-23 land


def _spec(land=LAND, **extra):
    s = copy.deepcopy(SP.packages()["SOT-23"])
    s.update(name="SOT-23", height_max={"max": 1.1}, **extra)
    if land:
        s["land"] = dict(land)
    return s


STD = SP.build("SOT-23")[0]


def test_the_maker_land_builds_other_pads_than_the_standard_land_at_the_same_places():
    mine = F.build(_spec())[0]
    assert not L.lands_agree(mine, STD)[0]                                   # the pads the review found: not the same
    ok, how = L.near_standard(mine, STD)
    assert ok and "3 pads" in how, how
    pads = {p[0]: p for p in B.numbers(mine)["pads"]}
    assert abs(max(pads["1"][3], pads["1"][4]) - 0.95) < 0.02 and abs(min(pads["1"][3], pads["1"][4]) - 0.56) < 0.02, pads


def test_a_land_of_another_package_is_not_near_the_standard_one():
    sc70 = SP.build("SOT-323_SC-70")[0]
    assert not L.near_standard(sc70, STD)[0]                                 # the family datasheet's other drawing
    assert not L.near_standard(SP.build("SOT-23-5")[0], STD)[0]              # other pad numbers
    assert not L.near_standard(SP.build("SOT-523")[0], SP.build("SOT-323_SC-70")[0])[0] if "SOT-523" in SP.packages() else True
    ti = F.build(dict(copy.deepcopy(SP.packages()["SOT-23-5"]), name="SOT-23-5",
                      land={"pad_length": 1.1, "pad_width": 0.6, "pad_center": 1.35}))[0]
    assert L.near_standard(ti, SP.build("SOT-23-5")[0])[0]                   # TI's DBV land: 0.21 mm further out
    wide = F.build(_spec(dict(LAND, pad_center=1.5)))[0]
    assert not L.near_standard(wide, STD)[0]                                 # pads 0.5 mm further out
    swapped = STD.replace('(pad "1"', '(pad "X"').replace('(pad "3"', '(pad "1"').replace('(pad "X"', '(pad "3"')
    assert swapped != STD and not L.near_standard(swapped, STD)[0]           # pin 1 where pin 3 is
    assert not L.near_standard("not a footprint", STD)[0]


def test_built_layout_is_numbered_smd_pads_centred():
    lay = CC.built_layout(F.build(_spec())[0])
    assert sorted(p[0] for p in lay) == ["1", "2", "3"]
    assert abs(min(p[1] - p[3] / 2 for p in lay) + max(p[1] + p[3] / 2 for p in lay)) < 1e-6
    assert CC.built_layout("junk") is None
    holes = F.build(_spec())[0].replace('(pad "1" smd roundrect', '(pad "1" thru_hole circle', 1) \
        .replace("(size 0.95 0.56)", "(size 0.95 0.56) (drill 0.5)", 1)
    assert B.numbers(holes)["pads"][0][5] and CC.built_layout(holes) is None  # a hole: not compared


@lite
def test_land_second_asks_for_the_pads_and_agrees_only_pad_by_pad(monkeypatch):
    text = F.build(_spec())[0]
    seen = {}

    def second_read(reader, job, first, pins, model):
        seen.update(first=first, pins=pins)
        ok, a, b = CC.pads_alike(first["pad_layout"], seen["theirs"])
        return {"source": "second read", "status": "agreed" if ok else "disagreed", "agreed": ["pads"] if ok else [],
                "disagreed": [] if ok else [{"field": "pads", "first": a, "second": b}], "compared": ["pads"]}
    monkeypatch.setattr(CC, "second_read", second_read)
    res = {"spec": _spec(), "symbol": {"pins": [{"num": "1", "name": "A"}]}, "sidecar": {}}
    seen["theirs"] = CC.built_layout(text)
    rec = CC.land_second(None, {"data": None}, res, text, "m")
    assert rec["status"] == "agreed" and seen["first"]["pads_read"] and seen["first"]["pad_layout"]
    seen["theirs"] = CC.built_layout(STD)                                    # the second read saw the longer pads
    assert CC.land_second(None, {"data": None}, res, text, "m")["status"] == "disagreed"
    assert CC.land_second(None, {"data": None}, res, "junk", "m")["status"] == "unchecked"


class _Reader:
    cfg, estimate_only, model = {}, False, "m"


class _Run(L.Run):
    def __init__(self, tmp):
        self.out, self.reader, self.rules, self.written, self.put = tmp, _Reader(), {}, {}, []
        (tmp / "std.kicad_mod").write_text(STD, encoding="utf-8")

    def put_fp(self, st, text, name, source, http=None, height=None, height_src="", spec=None, model=None, meta=None):
        self.put.append((name, source, text))
        st["footprint"] = {"source": source, "name": name}


def _try(tmp_path, monkeypatch, second, spec=None, review=(), source="standard:Package_TO_SOT_SMD:SOT-23", **st_extra):
    run = _Run(tmp_path)
    fp = {"source": source, "kicad_mod": "std.kicad_mod", "file": "std.PcbLib", "by": "Q1", "name": "Q1"}
    run.written[str(tmp_path / "std.PcbLib")] = fp
    st = {"name": "Q1", "footprint": fp, "review": [], "verify": [], "tried": [], "found": {}, **st_extra}
    if isinstance(second, Exception):
        def land_second(*a):
            raise second
    else:
        land_second = lambda *a: dict(second)
    monkeypatch.setattr(CC, "land_second", land_second)
    res = {"spec": spec or _spec(), "symbol": {"pins": [{"num": n, "name": s} for n, s in (("1", "A"), ("2", "NC"), ("3", "K"))]},
           "sidecar": {}, "needs_review": list(review)}
    rec = run.maker_land(st, res, {"part": "BAS16LT1G", "data": None}, "Q1", "https://maker.example/ds.pdf")
    return rec, st, run


AGREE = {"source": "second read", "status": "agreed", "agreed": ["pin_count", "pads"], "disagreed": [], "compared": ["pads"],
         "against": "second read (m)"}


@lite
def test_a_confirmed_maker_land_replaces_the_standard_one(tmp_path, monkeypatch):
    rec, st, run = _try(tmp_path, monkeypatch, AGREE)
    assert rec and rec["status"] == "agreed" and rec["checks"][0]["agreed"] == ["pin_count", "pads"], rec
    (name, source, text), = run.put
    assert source == "datasheet:https://maker.example/ds.pdf" and L.near_standard(text, STD)[0] and not L.lands_agree(text, STD)[0]
    assert not run.written and not st["verify"]                               # its own standard file gives up its name
    assert "replaces standard:Package_TO_SOT_SMD:SOT-23" in st["tried"][-1]
    assert L.read_confirmed({"cross_check": rec, "needs_review": []}, {}, ["footprint"])   # released on the second read


@lite
def test_anything_short_of_pad_by_pad_agreement_keeps_the_standard_land_and_holds_nothing(tmp_path, monkeypatch):
    for second in (dict(AGREE, agreed=["pin_count"]),                         # agreed, but the pads were not compared
                   dict(AGREE, status="disagreed", agreed=[], disagreed=[{"field": "pads", "first": "a", "second": "b"}]),
                   dict(AGREE, status="unchecked", agreed=[], why="no answer"), RuntimeError("boom")):
        rec, st, run = _try(tmp_path, monkeypatch, second)
        assert rec is None and not run.put and not st["verify"] and st["footprint"]["source"].startswith("standard:"), second
        assert st["review"] and st["review"][-1].startswith("maker land:") and "is used" in st["review"][-1]


@lite
def test_no_land_another_source_or_a_doubtful_read_changes_nothing(tmp_path, monkeypatch):
    for kw in (dict(spec=_spec(None)), dict(source="kicad:Package_TO_SOT_SMD:SOT-23"), dict(alternate=True)):
        rec, st, run = _try(tmp_path, monkeypatch, AGREE, **kw)
        assert rec is None and not run.put and not st["review"] and not st["verify"], kw
    rec, st, run = _try(tmp_path, monkeypatch, AGREE, review=["The datasheet PDF does not seem to name this part"])
    assert rec is None and not run.put and not st["verify"]
    rec, st, run = _try(tmp_path, monkeypatch, AGREE, spec=_spec(dict(LAND, pad_center=1.6)))   # not this package's land
    assert rec is None and not run.put and "not the standard land's package" in st["review"][-1]
    _Reader.cfg = {"cross_check": "off"}
    try:
        rec, st, run = _try(tmp_path, monkeypatch, AGREE)
        assert rec is None and not run.put and not st["review"]
    finally:
        _Reader.cfg = {}


@lite
def test_a_second_read_still_owed_waits_and_says_so(tmp_path, monkeypatch):
    rec, st, run = _try(tmp_path, monkeypatch, D.AssistantPending("waiting for your assistant: check-abc"))
    assert rec is None and not run.put and st["footprint"]["source"].startswith("standard:")
    assert len(st["verify"]) == 1 and st["verify"][0].startswith("maker land: waiting for your assistant")
    owed = dict(AGREE, status="unchecked", agreed=[], why="waiting for your assistant's second read: Q1-check-abc")
    rec, st, run = _try(tmp_path, monkeypatch, owed)                         # as crosscheck.second_read reports it
    assert rec is None and not run.put and len(st["verify"]) == 1 and "Q1-check-abc" in st["verify"][0], st
