"""Thermistors and varistors (1000-part BOM run, 2026-11-16: NCP18XH103F03RB, NTCG103JF103FT1, NTCS0603E3103FMT,
V18MLA0805NH and B72500E0050K060 were held with nothing built). A thermistor on a resistor's chip land was also
built as a plain resistor (Device:R, filed with the resistors as RES-0603-10k...): the wrong symbol on a schematic.
They are now their own kinds: KiCad's Thermistor_NTC / Thermistor_PTC / Varistor symbol, the part number kept, a
resistor's chip land by the size the line, the part search or the maker's size code says. A line that says
thermistor and varistor both, or a thermistor of unsaid type, is held; an IC whose text says 'NTC input' and an
onsemi NCP regulator stay ICs."""
from pathlib import Path

from ecadlib import librarian as L, stdparts as SP
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def test_what_says_thermistor_or_varistor():
    K = SP.sensor_kind
    assert K(["NTC thermistor 10k 3380K"]) == "NTC" and K(["THERMISTOR NTC 10KOHM 3380K 0603"]) == "NTC"
    assert K(["PTC thermistor 470R"]) == "PTC" and K(["VARISTOR 25V 120A 0805"]) == "VAR" and K(["MOV 275VAC"]) == "VAR"
    assert K(["10k"], "NCP18XH103F03RB") == "NTC" and K([""], "PRF18BB471QB1RB") == "PTC"
    assert K([""], "V18MLA0805NH") == "VAR" and K([""], "B72500E0050K060") == "VAR" and K([""], "ERZ-V07D471") == "VAR"
    assert K(["10k"], "", "TH1") == "TH" and K(["10k"], "", "NTC1") == "NTC"       # TH: type not said, no symbol
    assert K(["NTC"], "V18MLA0805NH") == "conflict" and K(["Varistor 10k"], "NTCS0603E3103FMT") == "conflict"
    for words, mpn, ref in ((["PTC resettable fuse 500mA"], "", "F1"), (["Polyfuse 1.1A"], "", "PTC1"), (["PTC"], "", ""), (["RES 10k 0603"], "RC0603FR-0710KL", "R1"),
                            ([""], "NCP1117ST33T3G", "U1"), ([""], "NCP3170ADR2G", "U2"), ([""], "", "RV1")):
        assert K(words, mpn, ref) is None, (words, mpn, ref)          # RV is a potentiometer's designator too


def test_size_from_the_series_size_code():
    S = SP.sensor_size
    for mpn, size in (("NCP18XH103F03RB", "0603"), ("NCP15XH103F03RC", "0402"), ("NTCG103JF103FT1", "0402"),
                      ("NTCG163JF103FT1", "0603"), ("NTCG063JF103FTB", "0201"), ("ERT-J1VG103FA", "0603"),
                      ("ERTJ0EM103J", "0402"), ("NTCS0603E3103FMT", "0603"), ("V18MLA0805NH", "0805"),
                      ("CG0603MLC-05E", "0603")):
        assert S(mpn) == size, (mpn, S(mpn))
    for mpn in ("B72500E0050K060", "B57861S0103F040", "NTCLE100E3103JB0", "NCP1117ST33T3G"):
        assert S(mpn) is None, mpn                 # no size code printed: the part search's case or a person says


def _run(tmp_path, monkeypatch, said, text):
    from ecadlib import bom_triage as T
    real = T.triage

    def fake(lines, *a, **k):
        rows = real(lines, *a, **k)
        for r in rows:
            if r["mpn"] in said:
                pkg, desc = said[r["mpn"]]
                r.update(src_package=pkg, src_package_from="digikey", src_description=desc, src_description_from="digikey")
        return rows
    monkeypatch.setattr(T, "triage", fake)
    d = Path(tmp_path)
    (d / "bom.csv").write_text(text, encoding="utf-8")
    items = L.run(d / "bom.csv", out=d / "out", sources=["standard"], offline=True, cache=d / "cache", rules={}, log=QUIET)
    return {i["librarian"].get("designators", "") or i["name"]: i for i in items}, items


BOM = ("Designator,Quantity,Value,Footprint,Manufacturer,Manufacturer Part Number,Description\n"
       "R1,1,10k,Resistor_SMD:R_0603_1608Metric,Yageo,RC0603FR-0710KL,RES 10k 1% 0603\n"
       "F24,1,,,Murata,NCP18XH103F03RB,\n"                              # the 1000-part BOM's lines: MPN only
       "F20,1,,,Littelfuse,V18MLA0805NH,\n"
       "F21,1,,,TDK,B72500E0050K060,\n"
       "TH1,1,10k,Resistor_SMD:R_0603_1608Metric,,,\n"
       "RT9,1,10k,Resistor_SMD:R_0603_1608Metric,Vishay,NTCS0603E3103FMT,Varistor 10k\n"
       "R5,1,10k NTC,Resistor_SMD:R_0402_1005Metric,Murata,NCP15XH103F03RC,NTC thermistor 0805\n"
       "U1,1,BQ24075,,TI,BQ24075RGTR,Li-ion charger with NTC thermistor input\n"
       "U2,1,NCP1117,,onsemi,NCP1117ST33T3G,LDO regulator 3.3V\n"
       "F1,1,500mA,Fuse:Fuse_1206_3216Metric,,,PTC resettable fuse\n")


def by_mpn(items, mpn):
    return next(i for i in items if any(p["name"] == "Manufacturer Part Number" and p["value"] == mpn for p in i["parameters"]))


@lite
def test_thermistors_and_varistors_build_as_themselves(tmp_path, monkeypatch):
    said = {"B72500E0050K060": ("0402 (1005 Metric)", "VARISTOR 5.6V 0402")}      # DigiKey's case for it
    _, items = _run(tmp_path, monkeypatch, said, BOM)
    for mpn, sym, land, folder, desc in (
            ("NCP18XH103F03RB", "Thermistor_NTC", "R_0603_1608Metric", "Thermistors", "NTC thermistor, 0603"),
            ("V18MLA0805NH", "Varistor", "R_0805_2012Metric", "Varistors", "Varistor, 0805"),
            ("B72500E0050K060", "Varistor", "R_0402_1005Metric", "Varistors", "VARISTOR 5.6V 0402")):
        it = by_mpn(items, mpn)
        assert it["status"] == "ready", (mpn, it["hold_reasons"], it["librarian"].get("tried"))
        assert it["source"]["symbol"] == f"standard:{sym}" and it["source"]["footprint"].endswith(land), (mpn, it["source"])
        assert it["folder"].endswith(folder) and it["name"] == mpn and it["description"] == desc, \
            (mpn, it["folder"], it["name"], it["description"])
    r = by_mpn(items, "RC0603FR-0710KL")                     # the plain resistor is untouched
    assert r["folder"].endswith("Resistors") and r["name"] == "RES-0603-10k", (r["folder"], r["name"])


@lite
def test_doubtful_lines_are_held_and_others_keep_their_kind(tmp_path, monkeypatch):
    _, items = _run(tmp_path, monkeypatch, {}, BOM)
    th = next(i for i in items if i["folder"].endswith("Thermistors") and not any(
        p["name"] == "Manufacturer Part Number" for p in i["parameters"]))
    assert th["status"] == "held" and not th["source"]["symbol"], th["source"]          # TH1: NTC or PTC unsaid
    assert any("NTC or PTC is not said" in h for h in th["hold_reasons"]), th["hold_reasons"]
    both = by_mpn(items, "NTCS0603E3103FMT")                 # the words say varistor, the series a thermistor
    assert both["status"] == "held" and any("both thermistor and varistor" in h for h in both["hold_reasons"]), \
        both["hold_reasons"]
    clash = by_mpn(items, "NCP15XH103F03RC")                 # the land the BOM names is an 0402, the text says 0805
    assert clash["status"] == "held", clash["hold_reasons"]
    for mpn in ("BQ24075RGTR", "NCP1117ST33T3G"):            # 'NTC thermistor input' and onsemi's NCP are ICs
        assert by_mpn(items, mpn)["folder"].endswith("ICs"), (mpn, by_mpn(items, mpn)["folder"])
    fuse = next(i for i in items if "Fuse_1206" in str(i["source"]["footprint"]))
    assert not fuse["folder"].endswith("Thermistors"), fuse["folder"]


def test_the_size_code_must_agree_with_the_land():
    st = {"kind": "NTC", "footprint": {"source": "kicad:Resistor_SMD:R_0402_1005Metric"}}
    item = {"parameters": [{"name": "Manufacturer Part Number", "value": "NCP18XH103F03RB"}], "description": ""}
    msg = L.chip_size_clash(st, item, {"value": "10k"})
    assert msg and "NCP18XH103F03RB is an 0603" in msg, msg
    st["footprint"]["source"] = "kicad:Resistor_SMD:R_0603_1608Metric"
    assert L.chip_size_clash(st, item, {"value": "10k"}) is None

