"""Parts the part search's description settles (1000-part BOM run, 2026-11-16). A tantalum's part search gives its
size ('1411 (3528 Metric)') and its body's maximum height ('0.083" (2.11mm)'), not the case letter: the two together
name the EIA case (3528-21 = KEMET / AVX case B) and so the table's land. A height that fits two cases, or a case the
table does not have, builds nothing."""
from pathlib import Path

from ecadlib import librarian as L, part_lookup as PL, providers as P, stdparts as SP
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def test_tantalum_eia_from_size_and_height():
    E = SP.tantalum_eia
    assert E("1411 (3528 Metric)", '0.083" (2.11mm)') == "3528-21"        # Vishay 293D B
    assert E("1206 (3216 Metric)", '0.071" (1.80mm)') == "3216-18"        # 293D A, AVX F931A
    assert E("2312 (6032 Metric)", '0.110" (2.80mm)') == "6032-28"
    assert E("2917 (7343 Metric)", '0.122" (3.10mm)') == "7343-31"
    assert E("1411 (3528 Metric), 1210", '0.083" (2.10mm)') == "3528-21"   # AVX TPSB
    assert E("1411 (3528 Metric)", '0.079" (2.00mm)') == "3528-19"        # KEMET T520B: a case the table lacks
    assert E("2917 (7343 Metric)", '0.169" (4.30mm)') == "7343-43"        # T495X
    for case, h in (("1411 (3528 Metric)", ""), ("1411 (3528 Metric)", '0.130" (3.30mm)'), ("Radial, Can - SMD", "2 mm"),
                    ("1206 (3216 Metric), 2012 (0805 Metric)", "1.8mm"), ("1411 (3528 Metric)", '0.055" (1.40mm)')):
        assert E(case, h) is None, (case, h)
    land = lambda c, h: SP.resolve([f"{SP.tantalum_eia(c, h)} ({c})"], "CP", "X1", "CAP TANT 10UF 16V")["footprint"]
    assert land("1411 (3528 Metric)", "2.11mm") == "CP_EIA-3528-21_Kemet-B"
    assert land("1411 (3528 Metric)", "2.00mm") is None and land("2917 (7343 Metric)", "4.30mm") is None


def test_digikey_height_reaches_the_source_result():
    specs = PL.specs_from_pairs([("Package / Case", "1411 (3528 Metric)"), ("Height - Seated (Max)", '0.083" (2.11mm)')])
    assert specs["package_text"] == "1411 (3528 Metric)" and specs["height_text"] == '0.083" (2.11mm)', specs
    info = PL.PartInfo("digikey", True, "293D106X9016B2TE3", "Vishay", "CAP TANT 10UF 10% 16V 1411", specs=specs)
    res = P.from_part_info(info, P.SourceResult("digikey"))
    assert res.package == "1411 (3528 Metric)" and res.extra["height"] == '0.083" (2.11mm)', (res.package, res.extra)


def _run(tmp_path, monkeypatch, said, text):
    from ecadlib import bom_triage as T
    real = T.triage

    def fake(lines, *a, **k):
        rows = real(lines, *a, **k)
        for r in rows:
            if r["mpn"] in said:
                pkg, height, desc = said[r["mpn"]]
                r.update(src_package=pkg, src_package_from="digikey", src_height=height,
                         src_description=desc, src_description_from="digikey")
        return rows
    monkeypatch.setattr(T, "triage", fake)
    d = Path(tmp_path)
    (d / "bom.csv").write_text(text, encoding="utf-8")
    items = L.run(d / "bom.csv", out=d / "out", sources=["standard"], offline=True, cache=d / "cache", rules={}, log=QUIET)
    return {next(p["value"] for p in i["parameters"] if p["name"] == "Manufacturer Part Number"): i for i in items}


@lite
def test_tantalums_build_on_the_case_their_part_search_names(tmp_path, monkeypatch):
    said = {"293D106X9016B2TE3": ("1411 (3528 Metric)", '0.083" (2.11mm)', "CAP TANT 10UF 10% 16V 1411"),
            "T495D107K010ATE100": ("2917 (7343 Metric)", '0.122" (3.10mm)', "CAP TANT 100UF 10% 10V 2917"),
            "T495X107K016ATE100": ("2917 (7343 Metric)", '0.169" (4.30mm)', "CAP TANT 100UF 10% 16V 2917"),
            "T520B107M006ATE040": ("1411 (3528 Metric)", '0.079" (2.00mm)', "CAP TANT POLY 100UF 6.3V 1411"),
            "TAJB106K016RNJ": ("1411 (3528 Metric)", "", "CAP TANT 10UF 10% 16V 1411")}
    got = _run(tmp_path, monkeypatch, said, "Designator,Qty,Value,Footprint,Manufacturer,MPN\n"
               "C1,1,10uF,,Vishay,293D106X9016B2TE3\nC2,1,100uF,,KEMET,T495D107K010ATE100\n"
               "C3,1,100uF,,KEMET,T495X107K016ATE100\nC4,1,100uF,,KEMET,T520B107M006ATE040\n"
               "C5,1,10uF,,Kyocera AVX,TAJB106K016RNJ\n")
    for mpn, land in (("293D106X9016B2TE3", "CP_EIA-3528-21_Kemet-B"), ("T495D107K010ATE100", "CP_EIA-7343-31_Kemet-D"),
                      ("TAJB106K016RNJ", "CP_EIA-3528-21_Kemet-B")):           # no height: the AVX code alone (as before)
        it = got[mpn]
        assert it["status"] == "ready" and land in str(it["source"]["footprint"]), (mpn, it["source"], it["hold_reasons"])
        assert it["source"]["symbol"] == "standard:C_Polarized", it["source"]
    for mpn in ("T495X107K016ATE100", "T520B107M006ATE040"):    # 7343-43: not in the table; T520 is a polymer part
        assert got[mpn]["status"] == "held" and not got[mpn]["source"]["footprint"], (mpn, got[mpn]["source"])


def test_a_chip_size_only_the_value_decoder_reads_names_the_land():
    """KYOCERA AVX's 0603YC104KAT2A codes its size like Murata's GRM188: stdparts.mpn_size now asks mpn_decode too."""
    assert SP.mpn_size("0603YC104KAT2A") == ("C", "0603") and SP.mpn_size("12065C104KAT2A") == ("C", "1206")
    assert SP.resolve([""], "C", "0603YC104KAT2A", "")["footprint"] == "C_0603_1608Metric"
    assert SP.mpn_size("T491A106K016AT") == (None, None)       # a tantalum's case is not a chip size
    assert SP.mpn_size("LM358DR") == (None, None)
