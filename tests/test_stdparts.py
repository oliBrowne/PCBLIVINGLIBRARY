"""Offline tests for ecadlib.stdparts (the librarian's `standard` source). Run:  python3 -m tests.test_stdparts

tests/fixtures/stdparts_kicad_numbers.json.gz holds pad/courtyard numbers measured from KiCad's official footprints
(kicad-footprints f8440fa, CC-BY-SA 4.0; numbers only, no footprint files) for every package in the standard table."""
import copy, gzip, json, tempfile
from pathlib import Path

from ecadlib import footprint_bench as FB, kicad_import as K, librarian as L, rules as RU, sexpr, stdparts as SP
from tests import test_librarian as TL

FIX = Path(__file__).parent / "fixtures" / "stdparts_kicad_numbers.json.gz"


def _ref():
    return json.loads(gzip.decompress(FIX.read_bytes()))["footprints"]


def test_every_table_entry_matches_kicad():
    """Every package the source can build matches KiCad's footprint (pads and courtyard, footprint_bench tolerances).
    MSOP-8 / SSOP-n need footprint_gen's forced ipc_family (KiCad's force_small_pitch_ipc_definition)."""
    ref = _ref()
    assert set(ref) == set(SP.packages()) and len(ref) >= 600, (len(ref), len(SP.packages()))
    rows, summ = SP.bench(lambda n, lib: ref.get(n))
    assert summ["footprints"] == len(ref) and summ["both_ok"] == len(ref), (summ, [r for r in rows if not r.get("all_pads_ok")][:3])
    for name in ("MSOP-8_3x3mm_P0.65mm", "SSOP-20_5.3x7.2mm_P0.65mm", "SOIC-8_3.9x4.9mm_P1.27mm", "R_0603_1608Metric",
                 "SOT-23", "SOT-223-3_TabPin2", "D_SMA", "TO-220-3_Vertical", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm"):
        assert name in ref, name


def test_bench_catches_a_wrong_spec():
    from ecadlib import footprint_gen as F
    ref = _ref()
    name = "SOIC-8_3.9x4.9mm_P1.27mm"
    assert FB.compare(FB.numbers(SP.build(name)[0]), ref[name])["all_pads_ok"]
    sp = dict(copy.deepcopy(SP.spec(name)), name=name)
    sp["pitch"] = 1.0                                             # wrong pitch must not pass
    r = FB.compare(FB.numbers(F.build(sp)[0]), ref[name])
    assert not r["all_pads_ok"], "a 1.0 mm pitch SOIC-8 passed against KiCad's 1.27 mm"
    sp = dict(copy.deepcopy(SP.spec(name)), name=name)            # MSOP-8 without the forced IPC family is off
    msop = dict(copy.deepcopy(SP.spec("MSOP-8_3x3mm_P0.65mm")), name="MSOP-8_3x3mm_P0.65mm")
    assert FB.compare(FB.numbers(F.build(msop)[0]), ref[msop["name"]])["all_pads_ok"]
    msop.pop("ipc_family", None)
    r = FB.compare(FB.numbers(F.build(msop)[0]), ref[msop["name"]])
    assert not (r["all_pads_ok"] and r["courtyard_ok"]), "MSOP-8 matched KiCad without force_small_pitch"


def test_names_resolve_or_stay_ambiguous():
    ok = {("SOIC-8",): "SOIC-8_3.9x4.9mm_P1.27mm", ("SO-8",): "SOIC-8_3.9x4.9mm_P1.27mm",
          ("MSOP-8",): "MSOP-8_3x3mm_P0.65mm", ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",): "SOIC-8_3.9x4.9mm_P1.27mm",
          ("QFN-32_L5.0-W5.0-P0.50-EP3.5",): "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm", ("SOT-23-5",): "SOT-23-5"}
    for texts, want in ok.items():
        r = SP.resolve(list(texts))
        assert r["footprint"] == want, (texts, r)
    for text, kind, why in (("QFN-16", None, "ambiguous"), ("SOT-23-3", None, "ambiguous"), ("SOT-223", None, "ambiguous"),
                            ("LQFP-64", None, "ambiguous"), ("SMA", "U", "not a diode"), ("PowerPAK SO-8", "Q", "power package"),
                            ("TO-220 horizontal", "U", "horizontal"), ("SC-70", "Q", ""), ("TSOT-23", "Q", "")):
        r = SP.resolve([text], kind)
        assert r["footprint"] is None and why in r["fp_how"], (text, r)
    assert SP.resolve(["SMA"], "D", "SS14")["footprint"] == "D_SMA"
    assert SP.resolve(["TO-220"], "U", "LM7805CT")["footprint"] == "TO-220-3_Vertical"
    # an MPN family whose package is stated picks the KiCad variant with its tab numbering
    assert SP.resolve(["SOT-223"], "U", "AMS1117-3.3")["footprint"] == "SOT-223-3_TabPin2"
    # suffixes do not leak into a shorter name
    assert SP.resolve(["TO-220-15"], "U")["footprint"] is None and SP.resolve(["SOT-23-16"], "U")["footprint"] is None
    # a description is weak evidence: a bare family name on an IC is not a footprint
    assert SP.resolve([], "U", "", "LDO regulator SOT-23")["footprint"] is None
    assert SP.resolve([], "Q", "MMBT3904", "NPN transistor SOT-23")["footprint"] == "SOT-23"


def test_chip_sizes_and_mpns():
    assert SP.chip_size("0603") == SP.chip_size("1608Metric") == SP.chip_size("RESC1608X55N") == "0603"
    assert SP.chip_size("C0805") == "0805" and SP.chip_size("10k") is None
    assert str(SP.chip_size("0603 0805")).startswith("conflict")
    for mpn, want in (("RC0603FR-0710KL", ("R", "0603")), ("GRM188R71H104KA93D", ("C", "0603")), ("CL10A106KP8NNNC", ("C", "0603")),
                      ("C0603C104K5RACTU", ("C", "0603")), ("ERJ-3EKF1002V", ("R", "0603")), ("CRCW060310K0FKEA", ("R", "0603")),
                      ("RMCF0805JT10K0", ("R", "0805")), ("GRM21BR61E106KA73L", ("C", "0805")), ("XYZ123", (None, None))):
        assert SP.mpn_size(mpn) == want, (mpn, SP.mpn_size(mpn))
    assert SP.resolve(["0603"], "R")["footprint"] == "R_0603_1608Metric"
    assert SP.resolve([""], "C", "GRM188R71H104KA93D")["footprint"] == "C_0603_1608Metric"
    r = SP.resolve(["0603"], "C", "GRM21BR61E106KA73L")                   # footprint says 0603, MPN says 0805
    assert r["footprint"] is None and "disagree" in r["fp_how"], r
    assert SP.resolve(["0603"], None)["footprint"] is None                # a size code without a part kind is no footprint


def test_symbols_convert_and_match_pads():
    cases = [("R", "", "R_0603_1608Metric"), ("C", "", "C_0603_1608Metric"), ("CP", "", "C_1206_3216Metric"),
             ("L", "", "L_0603_1608Metric"), ("LED", "", "LED_0603_1608Metric"), ("D", "1N4148W", "D_SOD-123"),
             ("D_Schottky", "SS14", "D_SMA"), ("D_Zener", "BZT52C5V1", "D_SOD-123"), ("D_TVS", "SMAJ5.0A", "D_SMA"),
             ("NPN:BEC", "MMBT3904", "SOT-23"), ("PNP:BEC", "MMBT3906", "SOT-23"), ("NMOS:GSD", "2N7002", "SOT-23"),
             ("PMOS:GSD", "AO3401A", "SOT-23"), ("REG:1117", "AMS1117-3.3", "SOT-223-3_TabPin2"),
             ("REG:1117", "AMS1117-ADJ", "SOT-223-3_TabPin2"), ("REG:78", "L7805CV", "TO-220-3_Vertical")]
    for key, mpn, fp in cases:
        text, name = SP.symbol_text(key, mpn)
        chain = [K._find_symbol(sexpr.parse(text), name)]
        sym = K.convert_symbol(chain, name, None, [], None, [])
        pins = {pr[1]["num"] for pr in sym["prims"] if pr[0] == "pin"}
        pads = {p[1] for p in sexpr.find(sexpr.parse(SP.build(fp)[0]), "pad") if p[1]}
        assert pins == pads, (key, fp, pins, pads)
    t, n = SP.symbol_text("REG:1117", "AMS1117-3.3")
    assert "GND" in t and n.startswith("Regulator"), n
    assert "ADJ" in SP.symbol_text("REG:1117", "AMS1117")[0]


def test_symbol_only_when_the_pinout_is_standard():
    assert SP.resolve(["SOT-23"], "Q", "MMBT3904")["symbol"] == "NPN:BEC"
    assert SP.resolve(["SOT-23"], "Q", "MMBT3906")["symbol"] == "PNP:BEC"
    assert SP.resolve(["SOT-23"], "Q", "XYZ777")["symbol"] is None                    # unknown family: footprint only
    assert SP.resolve(["SOIC-8"], "U", "LM358")["symbol"] is None
    assert SP.resolve(["0603"], "D", "KT-0603G")["symbol"] is None                  # LED or diode? not stated
    assert SP.resolve(["SOD-123"], "D", "1N4148W")["symbol"] == "D"


def test_cli_list_and_resolve():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x.kicad_mod"
        assert SP.main(["resolve", "SOT-23-5", "--kicad-mod", str(out)]) == 0
        assert "SOT-23-5" in out.read_text()
        assert SP.main(["resolve", "QFN-16"]) == 1
        assert SP.main(["list", "SOT-23"]) == 0


def test_rules_accept_standard_source():
    assert "standard" in RU.DEFAULTS["sources"]["order"]
    assert RU.DEFAULTS["sources"]["order"].index("standard") < RU.DEFAULTS["sources"]["order"].index("datasheet")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ecadlib.toml"
        p.write_text('[sources]\norder = ["standard", "datasheet"]\n')
        assert RU.load(p)["sources"]["order"] == ["standard", "datasheet"]


BOM = """Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description,Datasheet
R1,1,10k,R_0603_1608Metric,Yageo,RC0603FR-0710KL,,Resistor 10k 0603,https://example.com/r.pdf
R2,1,1k,,Yageo,RC0402FR-071KL,,Resistor 1k,https://example.com/r.pdf
C1,1,100n,,Murata,GRM188R71H104KA93D,,Capacitor 100nF,https://example.com/c.pdf
Q1,1,MMBT3904,SOT-23,onsemi,MMBT3904LT1G,,NPN transistor,https://example.com/q.pdf
U1,1,AMS1117-3.3,SOT-223,AMS,AMS1117-3.3,,LDO 3.3V,https://example.com/u.pdf
U2,1,LM324,TSSOP-14,TI,LM324PWR,,Op amp,https://example.com/lm358.pdf
U3,1,QFNPART,QFN-16,ACME,QFNPART,,Mystery QFN,https://example.com/qfn.pdf
"""


def test_librarian_standard_source():
    with tempfile.TemporaryDirectory() as d:
        items, http = TL.run(d, bom=BOM, http=TL.FakeHTTP())
        # the KiCad library has R_0603 and Device:R: it wins, standard is not needed
        r1 = next(i for n, i in items.items() if "RC0603" in json.dumps(i))
        assert r1["source"] == {"symbol": "kicad:Device:R", "footprint": "kicad:Resistor_SMD:R_0603_1608Metric"}, r1["source"]
        assert not any(t.startswith("standard") for t in r1["librarian"]["tried"])
        r2 = next(i for i in items.values() if "RC0402" in json.dumps(i))      # footprint from the MPN, symbol from KiCad
        assert r2["source"]["footprint"] == "standard:Resistor_SMD:R_0402_1005Metric", r2["source"]
        c1 = next(i for i in items.values() if "GRM188" in json.dumps(i))       # no KiCad Device:C here: both from standard
        assert c1["source"] == {"symbol": "standard:C", "footprint": "standard:Capacitor_SMD:C_0603_1608Metric"}, c1["source"]
        q1 = next(i for i in items.values() if "MMBT3904" in json.dumps(i))
        assert q1["source"] == {"symbol": "standard:Q_NPN_BEC", "footprint": "standard:Package_TO_SOT_SMD:SOT-23"}, q1["source"]
        assert any("family table" in r for r in q1["librarian"]["review"]), q1["librarian"]["review"]
        u1 = next(i for i in items.values() if "AMS1117" in json.dumps(i))
        assert u1["source"]["footprint"] == "standard:Package_TO_SOT_SMD:SOT-223-3_TabPin2", u1["source"]
        assert u1["source"]["symbol"].startswith("standard:Regulator_Linear"), u1["source"]
        u2 = next(i for i in items.values() if "LM324" in json.dumps(i))        # footprint only; symbol left to datasheet
        assert u2["source"] == {"symbol": None, "footprint": "standard:Package_SO:TSSOP-14_4.4x5mm_P0.65mm"}, u2["source"]
        assert any(t.startswith("needs datasheet reader") for t in u2["librarian"]["tried"])
        u3 = next(i for i in items.values() if "QFNPART" in json.dumps(i))
        assert u3["source"]["footprint"] is None and any("ambiguous" in t for t in u3["librarian"]["tried"])
        out = Path(d) / "out"
        for it in (c1, q1, u1):
            assert it["status"] == "ready", (it["name"], it["hold_reasons"])
            assert (out / it["symbol"]["file"]).exists() and (out / it["footprint"]["kicad_mod"]).exists()
        assert not any("datasheet" in t for t in q1["librarian"]["tried"])     # Claude never asked


def test_librarian_standard_never_before_kicad():
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, bom=BOM, http=TL.FakeHTTP(), sources=["standard", "kicad"])
        r1 = next(i for i in items.values() if "RC0603" in json.dumps(i))
        assert r1["source"]["footprint"] == "kicad:Resistor_SMD:R_0603_1608Metric", r1["source"]
        q1 = next(i for i in items.values() if "MMBT3904" in json.dumps(i))
        tried = q1["librarian"]["tried"]
        assert tried.index(next(t for t in tried if t.startswith("kicad"))) < tried.index(next(t for t in tried if t.startswith("standard")))
    try:
        TL.run(tempfile.mkdtemp(), bom=BOM, http=TL.FakeHTTP(), sources=["stdparts"])
    except ValueError as e:
        assert "standard" in str(e)
    else:
        raise AssertionError("an unknown source name was accepted")


def test_src_standard_keeps_what_is_there():
    """An earlier source (the user's own library, KiCad) already gave the footprint: it is kept, and the standard
    pinout is not attached to a footprint whose pad numbers it does not know."""
    with tempfile.TemporaryDirectory() as d:
        r = L.Run(Path(d), None, None, [], ["standard"])
        mine = {"source": "local:mylib:SOT23_HOUSE", "file": "x"}
        st = {"name": "Q1", "symbol": None, "footprint": dict(mine), "tried": [], "review": [], "verify": [], "found": {}, "hint_fp": ""}
        item = {"name": "Q1", "folder": "Transistors", "description": "NPN transistor", "footprint": {"kicad": "", "ref": "Q1"},
                "symbol": {"ref": "Q1"}}
        r.src_standard(st, item, {"footprint": "SOT-23"}, {}, None, "MMBT3904", "Q1", "Q1")
        assert st["footprint"] == mine and st["symbol"] is None, st
        assert "not known to match" in st["tried"][-1], st["tried"]
        st2 = dict(st, symbol={"source": "local:mylib:NPN"}, footprint=None, tried=[])
        r.src_standard(st2, item, {"footprint": "SOT-23"}, {}, None, "MMBT3904", "Q1", "Q1")
        assert st2["symbol"] == {"source": "local:mylib:NPN"} and st2["footprint"]["source"] == "standard:Package_TO_SOT_SMD:SOT-23"


def test_coverage_counts_bom_lines():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d) / "bom.csv"
        b.write_text(BOM)
        cov = SP.coverage([b])
        fp, both = [r for r in cov if r["footprint"]], [r for r in cov if r["footprint"] and r["symbol"]]
        assert (len(cov), len(fp), len(both)) == (7, 6, 5), [(r["designator"], r["footprint"], r["symbol"]) for r in cov]


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_stdparts."]))


def test_pin_removed_variants_are_not_the_standard_package():
    # the eval set (kicad-v1) found SOIC-14-16, SOIC-16W-12 and SOIC-8-N7 resolved as the full SOIC: wrong pads
    for t in ("SOIC-8-N7", "SOIC-14-16", "SOIC-16W-12"):
        name, why = SP.footprint_for([t])
        assert name is None and "pins removed" in why, (t, name, why)
    for t, want in (("SOIC-8-150mil", "SOIC-8_3.9x4.9mm_P1.27mm"), ("LQFP-48-7x7mm", "LQFP-48_7x7mm_P0.5mm")):
        assert SP.footprint_for([t])[0] == want, t
