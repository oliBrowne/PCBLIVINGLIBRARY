"""The EasyEDA converter against real EasyEDA answers (tests/fixtures/easyeda_live: NE555DR C7593, LM358DR C5423,
recorded by `ecadlib sources-check --save`; EasyEDA's copyright notice is kept in them). The other EasyEDA fixtures
are hand-made. Run:  python -m tests.test_easyeda_live"""
import json
from pathlib import Path

from ecadlib import easyeda_convert as EC, sources_check as SC

LIVE = Path(__file__).parent / "fixtures" / "easyeda_live"
PARTS = [("C7593", "NE555DR", 8, 8), ("C5423", "LM358DR", 8, 8)]


def _load(lcsc):
    d = json.loads((LIVE / f"{lcsc}.json").read_text(encoding="utf-8"))
    assert d.pop("_fixture").startswith("REAL:")
    return d


def test_fixtures_keep_easyeda_licence_notice():
    for lcsc, *_ in PARTS:
        r = _load(lcsc)["result"]
        for head in (r["dataStr"]["head"], r["packageDetail"]["dataStr"]["head"]):
            assert "JLCEDA/EasyEDA Official Library" in head["license"] and "https://easyeda.com" in head["license"], lcsc


def test_real_answers_convert_to_the_right_counts():
    for lcsc, mpn, pads, pins in PARTS:
        status, detail = SC.check_easyeda_answer(_load(lcsc), mpn, pads, pins)
        assert status == "ok", (lcsc, detail)
        assert f"{pads} pads, {pins} pins" in detail, detail


def test_converted_parts_credit_easyeda():
    r = _load("C7593")["result"]
    pkg = r["packageDetail"]
    assert EC.CREDIT in EC.footprint_to_kicad(pkg["dataStr"], pkg["title"], [])
    assert EC.CREDIT in EC.symbol_to_kicad(r["dataStr"], "NE555DR", "", {}, [])


def test_pins_are_counted_inside_unit_symbols():
    sym = '(kicad_symbol_lib (symbol "X" (symbol "X_1_1" (pin passive line (at 0 0 0) (length 2.54)) (pin passive line (at 0 2 0) (length 2.54)))))'
    assert SC.count_pins(sym) == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_manufacturer_drops_the_chinese_alias():
    # real EasyEDA answers name the maker "TI(德州仪器)"; the library wants "TI"
    from ecadlib import providers as P
    for lcsc, *_ in PARTS:
        assert P.parse_easyeda(_load(lcsc), P.SourceResult("easyeda")).manufacturer == "TI", lcsc
    assert P.clean_maker("YAGEO(国巨)") == "YAGEO" and P.clean_maker("YAGEO（国巨）") == "YAGEO"
    assert P.clean_maker("Murata Electronics (村田)") == "Murata Electronics"
    assert P.clean_maker("德州仪器") == "德州仪器"                          # only a Chinese name: kept
    assert P.clean_maker("Amphenol (FCI)") == "Amphenol (FCI)"          # a Latin alias is part of the name
