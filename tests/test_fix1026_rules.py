"""2026-10-26 fixes (rules): MPN decimal points, restyle ratings, case folding, house names, LCSC sign-off, costs.
Run:  python3 -m tests.test_fix1026_rules"""
import json

from tests.fixtures_lite import lite

DOTTED = (("SMAJ5.0A", "SMAJ50A"), ("1.5KE6.8A", "1.5KE68A"), ("P6KE6.8CA", "P6KE68CA"), ("SMBJ6.0A", "SMBJ60A"))


def _jlc(model):
    from ecadlib.httpcache import Response
    return {"data": {"componentPageInfo": {"list": [
        {"componentCode": "C114005", "componentModelEn": model, "componentBrandEn": "Littelfuse",
         "componentSpecificationEn": "SMA(DO-214AC)", "describe": "TVS 50V 400W SMA", "stockCount": 5000}]}}}, Response


# ------------------------------------------------------------------ R-RU-1: a decimal point is part of the MPN
def test_ru1_norm_mpn_keeps_a_decimal_point_between_digits():
    from ecadlib.kicad_index import norm_mpn
    for a, b in DOTTED:
        assert norm_mpn(a) != norm_mpn(b), (a, b)
    assert norm_mpn("SMAJ5.0A") == norm_mpn("smaj5.0a") == "SMAJ5.0A"
    assert norm_mpn("LP38693MP-1.8") == "LP38693MP1.8"
    assert norm_mpn("LM358-DR/NOPB") == "LM358DRNOPB" and norm_mpn("A.B") == "AB" and norm_mpn("U1.") == "U1"
    assert norm_mpn("µA741CN") == "UA741CN" and norm_mpn("部品5.0X") == "部品5.0X"


def test_ru1_jlc_lifecycle_and_lookup_do_not_take_another_dotted_part():
    from ecadlib import lifecycle as L, part_lookup as PL, providers as P
    listing, Response = _jlc("SMAJ50A")
    r = Response(200, json.dumps(listing))
    res = P.parse_jlcpcb(r, "SMAJ5.0A", P.SourceResult("jlcpcb_search", mpn="SMAJ5.0A"), maker="Littelfuse")
    assert not res.found and not res.lcsc
    res = P.parse_jlcpcb(Response(200, json.dumps(_jlc("SMAJ5.0A")[0])), "SMAJ5.0A",
                         P.SourceResult("jlcpcb_search", mpn="SMAJ5.0A"), maker="Littelfuse")
    assert res.found and res.lcsc == "C114005"                     # the right part still matches
    nexar = [{"mpn": "SMAJ50A", "manufacturer": {"name": "Littelfuse"}}]
    assert L._pick(nexar, {"mpn": "SMAJ5.0A", "manufacturer": "Littelfuse"}, lambda x: x["mpn"],
                   lambda x: x["manufacturer"]["name"]) is None

    class H:
        def post(s, url, body, headers=None, **k):
            return Response(200, json.dumps(listing))

        def get(s, *a, **k):
            return Response(404, "")
    info = PL.JLCSourcing(H()).part({"mpn": "SMAJ5.0A", "manufacturer": "Littelfuse"})
    assert not (info.found and info.mpn == "SMAJ50A")


def test_ru1_triage_notes_a_dotted_mismatch():
    from ecadlib import bom_triage as BT
    assert BT._n("SMAJ5.0A") != BT._n("SMAJ50A") and BT._n("lm358-dr") == BT._n("LM358DR")


def test_ru1_dedupe_and_export_keep_dotted_parts_apart():
    from ecadlib import dedupe as DD
    assert DD.mpn_key("SMAJ5.0A") != DD.mpn_key("SMAJ50A")
    assert DD.mpn_key("LM1117MPX-3.3/TR") == DD.mpn_key("LM1117MPX-3.3")


@lite
def test_ru1_old_lifecycle_state_keys_still_match(tmp_path):
    # a lifecycle_state.json written before the change keyed SMAJ5.0A as SMAJ50A: it is read under the new key
    from ecadlib import altium_project as AP, lifecycle as L
    from ecadlib.kicad_index import norm_mpn
    f = tmp_path / "lifecycle_state.json"
    f.write_text(json.dumps({"version": 1, "updated": "x", "parts": {
        "SMAJ50A": {"mpn": "SMAJ5.0A", "status": "obsolete"}, "LM358DR": {"mpn": "LM358-DR", "status": "active"},
        "LCSC:C1": {"mpn": "", "status": "active"}}}), encoding="utf-8")
    st = L.load_state(f)
    assert st["parts"][norm_mpn("SMAJ5.0A")]["status"] == "obsolete" and "SMAJ50A" not in st["parts"]
    assert "LM358DR" in st["parts"] and "LCSC:C1" in st["parts"]
    lc = AP.load_lifecycle(f)
    assert lc[norm_mpn("SMAJ5.0A")]["status"] == "obsolete"


def test_ru1_librarian_finds_a_dotted_kicad_symbol_and_its_ordering_suffix():
    from ecadlib import librarian as LB
    idx = LB.KiCadLibrary.__new__(LB.KiCadLibrary)
    idx.symbols = {"LP38693MP-1.8": ["Regulator_Linear"], "SMAJ5.0A": ["Diode"], "LMx58": ["Amplifier"]}
    idx.footprints = {}
    idx._norm, idx._wild, idx._fpn = {}, [], []
    LB.KiCadLibrary._build_norm(idx)
    assert idx.find_symbol("LP38693MP-1.8")[0] == "Regulator_Linear:LP38693MP-1.8"
    ref, how = idx.find_symbol("LP38693MP-1.8/NOPB")
    assert ref == "Regulator_Linear:LP38693MP-1.8" and "NOPB" in how
    assert idx.find_symbol("SMAJ50A")[0] is None
    assert idx.find_symbol("LM358")[0] == "Amplifier:LMx58"

def _main():
    import sys
    n = 0
    for k, f in sorted(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            n += 1
    print(f"{n} passed")
    sys.exit(0)


if __name__ == "__main__":
    _main()
