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

# ------------------------------------------------------------------ R-RU-2: a restyle never changes a rating
def test_ru2_model_restyle_cannot_change_voltage_or_tolerance():
    import tempfile
    from pathlib import Path
    from ecadlib import house_style as H
    from tests.test_house_style import FakeModel, _rules, altium_house, plan_items
    with tempfile.TemporaryDirectory() as td:
        prof = H.learn([altium_house(Path(td) / "lib")])
        prof["_file"] = str(Path(td) / "house_style.json")
        items = plan_items()
        ans = {"parts": [{"name": "CAP-0402-100nF", "description": "CAP 100N 100V X7R 0402", "comment": "=Value",
                          "parameters": [{"name": "Voltage", "value": "100 V"}]},
                         {"name": "RES-0603-10k", "description": "RES 10K ±10% 0603", "comment": "=Value",
                          "parameters": [{"name": "Tolerance", "value": "±10%"}]}]}
        done, rejected = H.adapt_with_model(items, prof, _rules("claude-opus-5-5"), send=FakeModel([ans]))
        by = {i["name"]: i for i in items}
        prm = lambda n: {p["name"]: p["value"] for p in by[n]["parameters"]}
        assert prm("CAP-0402-100nF")["Voltage"] == "16V" and prm("RES-0603-10k")["Tolerance"] == "1%", rejected
        assert by["CAP-0402-100nF"]["description"] == "Capacitor 100nF 16V X7R 0402"
        assert by["RES-0603-10k"]["description"] == "Resistor 10k 1% 0603"
        assert len(rejected) >= 4, rejected
        # the same facts written another way still go through
        items = plan_items()
        ans = {"parts": [{"name": "CAP-0402-100nF", "description": "CAP 100nF 16 V X7R 0402", "comment": "=Value",
                          "parameters": [{"name": "Voltage", "value": "16 VDC"}]},
                         {"name": "RES-0603-10k", "description": "RES 10K ±1% 0603", "comment": "=Value",
                          "parameters": [{"name": "Tolerance", "value": "±1 %"}]}]}
        done, rejected = H.adapt_with_model(items, prof, _rules("claude-opus-5-5"), send=FakeModel([ans]))
        by = {i["name"]: i for i in items}
        assert {p["name"]: p["value"] for p in by["CAP-0402-100nF"]["parameters"]}["Voltage"] == "16 VDC", rejected
        assert {p["name"]: p["value"] for p in by["RES-0603-10k"]["parameters"]}["Tolerance"] == "±1 %", rejected
        assert by["CAP-0402-100nF"]["description"] == "CAP 100nF 16 V X7R 0402", rejected
        assert by["RES-0603-10k"]["description"] == "RES 10K ±1% 0603", rejected


def test_ru2_change_control_classes_a_rating_change_as_identity():
    from ecadlib import change_diff as CD, fingerprint as FP
    base = {"Manufacturer Part Number": "CL05B104KO5NNNC", "Manufacturer": "Samsung", "Voltage": "16V",
            "Tolerance": "10%", "Dielectric": "X7R"}
    a = FP.build("", params=base)
    for k, v in (("Voltage", "100 V"), ("Tolerance", "±1%"), ("Dielectric", "X5R")):
        b = FP.build("", params=dict(base, **{k: v}))
        assert CD.worst(CD.diff(a, b)) == "identity", (k, v, CD.diff(a, b))
    for k, v in (("Voltage", "16 V"), ("Tolerance", "±10 %"), ("Dielectric", "x7r")):
        b = FP.build("", params=dict(base, **{k: v}))
        assert CD.worst(CD.diff(a, b)) == "cosmetic", (k, v, CD.diff(a, b))


# ------------------------------------------------------------------ R-RU-3: case never folds milli into mega
def test_ru3_naming_case_keeps_milli_and_mega_apart():
    from ecadlib import rules as RU
    for case in ("upper", "lower"):
        for style in ("keep", "si", "rkm_lower", "si_upper"):
            r = RU.load({"naming": {"case": case, "value_style": style}})
            a = RU.component_name(RU.line_fields({"value": "10m", "mpn": "WSL0603R0100FEA"}, "Resistors", "0603", r), r)
            b = RU.component_name(RU.line_fields({"value": "10M", "mpn": "RC0603FR-0710ML"}, "Resistors", "0603", r), r)
            assert a != b, (case, style, a, b)
            if case == "upper" and style == "keep":
                assert a == "RES-0603-10m" and b == "RES-0603-10M", (a, b)
    r = RU.load({"naming": {"case": "upper"}})
    assert RU.component_name(RU.line_fields({"value": "100nF"}, "Capacitors", "0402", r), r) == "CAP-0402-100NF"
    r = RU.load({"naming": {"case": "lower"}})
    assert RU.component_name(RU.line_fields({"value": "4K7"}, "Resistors", "0603", r), r) == "res-0603-4k7"


def test_ru3_upper_case_export_keeps_both_resistors():
    import contextlib, io
    from ecadlib import altium_export as AE, rules as RU
    lines = [{"designator": "R1", "value": "10m", "footprint": "Resistor_SMD:R_0603_1608Metric",
              "description": "shunt 10mOhm 0603", "qty": "1"},
             {"designator": "R2", "value": "10M", "footprint": "Resistor_SMD:R_0603_1608Metric", "description": "10M 0603",
              "qty": "1"}]
    with contextlib.redirect_stderr(io.StringIO()):
        df = AE.to_rows(lines, None, RU.load({"naming": {"case": "upper"}}))["Resistors"]
    assert sorted(zip(df["Part Number"], df["Value"])) == [("RES-0603-10M", "10M"), ("RES-0603-10m", "10m")]


# ------------------------------------------------------------------ R-RU-4: house parameter names are checked
def test_ru4_parameter_names_onto_a_key_column_or_another_name_are_refused():
    from ecadlib import rules as RU
    bad = ({"Manufacturer Part Number": "Part Number"}, {"Manufacturer Part Number": "Library Ref"},
           {"Manufacturer": "description"}, {"Manufacturer Part Number": "PN", "Supplier Part Number 1": "PN"},
           {"Manufacturer Part Number": "PN", "Supplier Part Number 1": "pn"}, {"Manufacturer": "Value"},
           {"Manufacturer": "Supplier 1", "Supplier 1": "Vendor"}, {"Part Number": "Our PN"},
           {"Manufacturer Part Number": " "})
    for names in bad:
        try:
            RU.load({"parameters": {"names": names}})
        except RU.RulesError as e:
            assert "parameters.names" in str(e), (names, e)
        else:
            raise AssertionError(f"accepted {names}")
    ok = RU.load({"parameters": {"names": {"Manufacturer Part Number": "Mfr PN", "Manufacturer": "Mfr",
                                           "Supplier Part Number 1": "LCSC #", "Value": "Value"}}})
    assert ok["parameters"]["names"]["Manufacturer Part Number"] == "Mfr PN"


# ------------------------------------------------------------------ R-RU-5: an LCSC Part change needs a new sign-off
def test_ru5_lcsc_part_change_makes_the_sign_off_stale():
    from ecadlib import approvals as A
    mpn = {"name": "Manufacturer Part Number", "value": "SMAJ5.0A"}
    it = {"name": "SMAJ5.0A", "description": "TVS 5V", "parameters": [mpn, {"name": "LCSC Part", "value": "C35496"}]}
    b = dict(it, parameters=[mpn, {"name": "LCSC Part", "value": "C114005"}])
    assert A.content_hash(it) != A.content_hash(b)
    c = dict(it, parameters=[mpn, {"name": "LCSC Part", "value": "C35496"}, {"name": "Assembly Rotation", "value": "90"}])
    assert A.content_hash(it) == A.content_hash(c)          # the removed assembly check's own fields stay unsigned


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
