"""Offline tests for round 3 of the 2026-11-23 review (the second independent review), group second read: each fix has
the inputs that must be held and the ordinary parts that must stay ready.   python3 -m tests.test_review1123_round3_second_read
  B1  ds_route.open_unresolved is an allow-list: only an entry that is wholly about a thing no pad stands on is skipped
  B2  FC-QFN-16, DR-QFN-44, QFN-16-HR get no quarter-per-side default (the hyphen forms of FCQFN / DRQFN / QFN-HR)
  A9  a search result that names no maker, or more than one, is not the listing of the maker the BOM names
  B4  a BOM that says "ams" / "ams AG" for a part ams-OSRAM lists is the same maker; bare AMS stays apart otherwise"""
import copy
from pathlib import Path

from ecadlib import bom_triage as BT, datasheet as D, ds_route as DR, librarian as L, providers as P
from tests import test_fix1026_rd as T, test_review1123_second_read as R, test_review1123_round2_second_read as R2
from tests import test_review1123_part_search as PS1, test_review1123_round2_part_search as PS
from tests.fixtures_lite import lite

LIB_SYMBOL = R.LIB_SYMBOL


# ---------------------------------------------------------------- B1: unresolved entries, an allow-list
# the second review's entries that round 2 no longer held (scripts2/i4.py)
B1_HOLD = ("could not tell which variant applies", "variant A vs B", "e", "D", "E", "b", "L", "D2/E2", "symbol b unreadable",
           "value of e blurry", "numbering clockwise or counter-clockwise?", "mirrored view?", "bottom view vs top view",
           "unit unclear", "scale", "the scan is blurry", "text illegible", "page missing", "datasheet is for a different part",
           "wrong datasheet?", "part number not found in the document", "conflicting values",
           "values differ between pages 12 and 14", "ambiguous", "uncertain", "low confidence", "interpolated", "approximated",
           "from memory", "typical value used", "datasheet lacks this; used standard", "count uncertain",
           "not applicable: pitch", "lead_span: n/a")
# more of the kind: a harmless word beside a doubt, a dimension letter with n/a, words that name nothing at all
B1_HOLD_MORE = ("marking differs: datasheet is for a different part", "weight and pitch not given", "e: n/a", "D2, E2 not applicable",
                "A1", "cathode marking not found", "pin 1 marking unclear", "thermal pad n/a", "not given", "no data", "?",
                "everything", "unsure which package", "tolerance of e", "JEDEC reference used instead", "derived", "rounded",
                "ordering code decides the pinout", "tape orientation of pin 1", "package: n/a", "resistance not stated",
                "lead_span (not applicable: no-lead package)", "terminal_length n/a for this package", "typo in the datasheet?")
B1_READY = ("weight", "marking code", "moisture sensitivity level", "thermal resistance not stated", "RoHS status",
            "not applicable", "N/A", "none",
            "Not applicable.", "n/a", "weight not given", "top marking not shown", "tape and reel quantity", "Weight: n/a",
            "MSL", "MSL 3", "packing quantity not stated", "ordering information", "ordering code not listed", "reel size",
            "thermal resistance junction to ambient not specified", "device marking", "RoHS / REACH compliance",
            "mass not given in the datasheet", "packaging", "moisture level", "marking and weight not given", "nothing")


def test_an_unresolved_entry_holds_unless_it_is_wholly_about_a_harmless_thing():
    t = T.SMA
    for u in B1_HOLD + B1_HOLD_MORE:
        res = T.read(dict(copy.deepcopy(t), unresolved=[u]))
        assert DR.open_unresolved(res["spec"]) == [u], (u, DR.open_unresolved(res["spec"]))
        if DR.hold_reasons(res, True):                # held outright (a field the read gave and calls unread): as safe
            continue
        cc, job = R.checked(res, t)
        assert cc["status"] == "self-checked" and cc["unresolved"] == [u], (u, cc["status"], cc.get("unresolved"))
        assert R.put(res, job, LIB_SYMBOL)[0] == "verify", u


def test_an_unresolved_entry_about_a_harmless_thing_does_not_keep_the_part_with_a_person():
    t = T.SMA
    for u in B1_READY:
        res = T.read(dict(copy.deepcopy(t), unresolved=[u]))
        assert not DR.open_unresolved(res["spec"]), (u, DR.open_unresolved(res["spec"]))
        assert not DR.hold_reasons(res, True), (u, DR.hold_reasons(res, True))
        cc, job = R.checked(res, t)
        assert cc["status"] == "agreed" and not cc.get("unresolved"), (u, cc["status"], cc.get("unresolved"))
        assert R.put(res, job, LIB_SYMBOL)[0] == "released", u
    # what was advice before round 2 is advice still: the height, and a limit of a dimension whose value was read
    for u in ("height_max", "seated height not given", "height n/a", "description"):
        assert not DR.open_unresolved(dict(T.read(copy.deepcopy(t))["spec"], unresolved=[u])), u
    assert not DR.open_unresolved({"body_length": 4.3, "unresolved": ["body_length min/max (not printed)"]})
    # one harmless entry beside one that is not: the other still counts
    assert DR.open_unresolved({"unresolved": ["weight", "scale", "N/A", "e"]}) == ["scale", "e"]


# ---------------------------------------------------------------- B2: FC-QFN, DR-QFN, QFN-16-HR
def test_a_flip_chip_dual_row_or_hot_rod_qfn_written_with_a_hyphen_gets_no_quarter_per_side():
    for name, n, side, pitch, ep in (("FC-QFN-16", 16, 3.0, 0.5, 1.6), ("DR-QFN-44", 44, 5.0, 0.4, None), ("QFN-16-HR", 16, 3.0, 0.5, None),
                                     ("FC QFN-16", 16, 3.0, 0.5, 1.6), ("FC_QFN_16", 16, 3.0, 0.5, 1.6), ("DR QFN 44", 44, 5.0, 0.4, None),
                                     ("DR_QFN-44", 44, 5.0, 0.4, None), ("QFN-16 HR", 16, 3.0, 0.5, None), ("QFN_16_HR", 16, 3.0, 0.5, None),
                                     ("QFN16-HR", 16, 3.0, 0.5, None), ("QFN16HR", 16, 3.0, 0.5, None), ("VQFN-16-HR", 16, 3.0, 0.5, None),
                                     ("QFN-FC-16", 16, 3.0, 0.5, 1.6), ("QFN-16-FC", 16, 3.0, 0.5, 1.6), ("QFN-DR-44", 44, 5.0, 0.4, None),
                                     ("FC-VQFN-16", 16, 3.0, 0.5, 1.6), ("DR-WQFN-44", 44, 5.0, 0.4, None),
                                     ("FCQFN-16", 16, 3.0, 0.5, 1.6), ("FCQFN16", 16, 3.0, 0.5, None), ("DRQFN-44", 44, 5.0, 0.4, None), ("VQFN-HR-12", 12, 2.0, 0.4, None)):
        res = T.read(R2.quad(name, n, side, pitch, ep))
        s = res["spec"]
        assert "pins_left_right" not in s and "pins_top_bottom" not in s, (name, s.get("pins_left_right"), res["needs_review"])
        assert any(r.startswith(D.ROWS_UNREAD) for r in res["needs_review"]), (name, res["needs_review"])
        out = R.put(res, symbol=LIB_SYMBOL)[0]            # ("verify": a checked standard land of the read's numbers is
        assert out.startswith("held: ") and D.ROWS_UNREAD in out or out == "verify", (name, out)   # built, for a person)


def test_an_ordinary_qfn_name_with_letters_like_hr_fc_or_dr_in_it_keeps_its_quarter_per_side():
    for name, n, side, pitch, ep in R2.QUADS + (("QFN-16 (DRB)", 16, 3.0, 0.5, 1.6), ("QFN-16-HRS", 16, 3.0, 0.5, 1.6),
                                               ("AFC-QFN-16", 16, 3.0, 0.5, 1.6), ("QFN-16-DRV", 16, 3.0, 0.5, 1.6),
                                               ("PG-VQFN-48", 48, 7.0, 0.5, 5.35), ("QFN-16-EP", 16, 3.0, 0.5, 1.6)):
        res = T.read(R2.quad(name, n, side, pitch, ep))
        s = res["spec"]
        assert (s.get("pins_left_right"), s.get("pins_top_bottom")) == (n // 4, n // 4), (name, n, side, res["needs_review"])
        assert not DR.hold_reasons(res, True), (name, DR.hold_reasons(res, True))
    R2.test_the_rows_default_changed_no_text_sent_to_a_model()      # the schema's words on the rows are as they were


# ---------------------------------------------------------------- A9: a result with no single maker name
NO_ONE_MAKER = ("", "  ", "-", "onsemi / Vishay", "Vishay / onsemi", "Vishay (onsemi second source)", "Vishay, onsemi",
                "onsemi; Vishay", "Vishay or onsemi", "Vishay & onsemi", "Vishay | Diodes Incorporated", ["onsemi"],
                ["Vishay", "onsemi"], ["Vishay"], None, 7, {"name": "Vishay"})
ONE_MAKER = ("Vishay", "Vishay General Semiconductor", "Vishay General Semiconductor - Diodes Division", "VISHAY(威世)",
             "Vishay Semiconductors", "Vishay Intertechnology, Inc.", "Vishay / Siliconix", "Vishay Siliconix",
             "Vishay Beyschlag/Draloric/BC Components", "Vishay (General Semiconductor)", "Vishay (Formerly General Semiconductor)")


class _Listing(P.Provider):
    """A part search whose result carries the maker field it is given (the second review's plug: scripts2/i8.py)."""
    name = "digikey"

    def __init__(self, maker, desc="DIODE SCHOTTKY 40V 3A SMC", pkg="DO-214AB (SMC)", lcsc=""):
        self.http, self.maker, self.desc, self.pkg, self.lcsc = None, maker, desc, pkg, lcsc

    def lookup(self, line):
        return P.SourceResult(self.name, found=True, mpn=line.get("mpn") or "", manufacturer=self.maker, package=self.pkg,
                              lcsc=self.lcsc, extra={"description": self.desc})


def _run(tmp_path, bom, *provs):
    """librarian.run on a one-line BOM, offline, the standard source only, with these part searches answering."""
    (tmp_path / "bom.csv").write_text(bom, encoding="utf-8")
    real = BT.triage
    BT.triage = lambda lines, index, ps, http, *a, **k: real(lines, index, list(provs), http)
    try:
        return L.run(str(tmp_path / "bom.csv"), [], tmp_path / "lib", ["standard"], None, False, [], True, tmp_path / "cache",
                     0.0, [], log=lambda *a, **k: None, user="Rev <rev@x.com>")
    finally:
        BT.triage = real


SS34 = "Designator,Qty,Value,Footprint,MPN,Manufacturer\nD1,1,,,SS34,Vishay\n"


def test_a_result_that_names_no_maker_or_more_than_one_gives_nothing_when_the_bom_names_a_maker():
    for theirs in NO_ONE_MAKER:
        r = PS._row(PS._Cad(theirs))
        assert [r[k] for k in PS.TAKEN] == [""] * len(PS.TAKEN), (theirs, {k: r[k] for k in PS.TAKEN})
        assert r["status"] == "needs_footprint_generation" and r["src_manufacturer"] == "", (theirs, r["status"])
        assert ("not Vishay's own listing" in r["notes"] or ", not Vishay:" in r["notes"]) and "are not used" in r["notes"], (theirs, r["notes"])
        r = PS._row(_Listing(theirs))                              # its package and measurements neither
        assert (r["src_package"], r["src_package_device"], r["src_dims"]) == ("", "", ""), (theirs, r)
    assert "plug: SS34 names no maker, so it is not Vishay's own listing" in PS._row(PS._Cad(""))["notes"]
    assert "plug: SS34 names more than one maker (onsemi / Vishay), so it is not Vishay's own listing" in \
        PS._row(PS._Cad("onsemi / Vishay"))["notes"]
    # the maker's own source after it is still asked and taken
    r = PS._row([PS._Cad("", "first"), PS._Cad("onsemi / Vishay", "second"), PS._Cad("Vishay", "third")])
    assert (r["src"], r["src_description_from"], r["src_datasheet_from"], r["status"]) == ("third",) * 3 + ("available_easyeda",), r


@lite
def test_a_listing_with_no_single_maker_does_not_pick_the_land_of_the_boms_part(tmp_path):
    # the second review's A9: Vishay's SS34 is SMA; a listing by nobody / by two makers in SMC put it on D_SMC, ready
    for n, theirs in enumerate(("", "onsemi / Vishay", "Vishay (onsemi second source)", ["onsemi"])):
        (tmp_path / str(n)).mkdir()
        it = _run(tmp_path / str(n), SS34, _Listing(theirs))[0]
        assert it["status"] != "ready", (theirs, it["status"], it.get("source"))
        assert "D_SMC" not in str((it.get("source") or {}).get("footprint")), (theirs, it.get("source"))


def test_a_result_that_names_the_boms_maker_alone_is_taken_as_before():
    for theirs in ONE_MAKER:
        r = PS._row(PS._Cad(theirs))
        assert (r["src"], r["src_datasheet"], r["src_description_from"], r["status"]) == \
            ("plug", PS.SHEET, "plug", "available_easyeda"), (theirs, r)
        assert "not used" not in r["notes"], (theirs, r["notes"])
        assert PS._row(_Listing(theirs, pkg="DO-214AC (SMA)"))["src_package"] == "DO-214AC (SMA)", theirs
    for maker, theirs in (("Maxim", "Analog Devices Inc./Maxim Integrated"), ("TDK", "EPCOS - TDK Electronics"),
                          ("Omron", "Aratas (Formerly Omron Components)"), ("Amphenol", "Amphenol ICC (FCI)"),
                          ("NXP", "NXP Semiconductors / Nexperia"), ("Cree", "Cree/Wolfspeed"), ("TI", "TI(德州仪器)"),
                          ("Laird", "Laird - Performance Materials"), ("Texas Instruments", "Texas Instruments (National Semiconductor)"),
                          ("", ""), ("", "onsemi / Vishay"), ("-", ""), ("N/A", "onsemi / Vishay")):   # no maker on the line
        r = PS._row(PS._Cad(theirs), maker=maker)
        assert r["src"] == "plug" and r["src_datasheet"] == PS.SHEET and "not used" not in r["notes"], (maker, theirs, r["notes"])
    # a result for the LCSC number the BOM itself gives is that listing, whoever it names or does not (EasyEDA's
    # answer has no maker for many parts)
    r = PS._row(_Listing("", lcsc="C2975360"), lcsc="C2975360")
    assert r["src"] == "digikey" and r["src_package"] and "not used" not in r["notes"], r
    for theirs, lcsc in (("", "C111"), ("onsemi / Vishay", "C2975360"), ("onsemi", "C2975360")):   # another number's
        r = PS._row(_Listing(theirs, lcsc=lcsc), lcsc="C2975360")     # listing is not, nor one that names another maker
        assert r["src"] == "" and "are not used" in r["notes"], (theirs, lcsc, r)


@lite
def test_the_makers_own_listing_still_gives_the_land_and_the_part_is_ready(tmp_path):
    for n, (theirs, pkg, fp) in enumerate((("Vishay General Semiconductor - Diodes Division", "DO-214AC (SMA)", "D_SMA"),
                                           ("Vishay / Siliconix", "DO-214AC (SMA)", "D_SMA"))):
        (tmp_path / str(n)).mkdir()
        it = _run(tmp_path / str(n), SS34, _Listing(theirs, "DIODE SCHOTTKY 40V 3A SMA", pkg))[0]
        assert it["status"] == "ready" and fp in str(it["source"]["footprint"]), (theirs, it["status"], it.get("source"))
    it = _run(tmp_path, SS34.replace(",Vishay", ","), _Listing("", "DIODE SCHOTTKY 40V 3A SMA", "DO-214AC (SMA)"))[0]
    assert it["status"] == "ready" and "D_SMA" in str(it["source"]["footprint"]), (it["status"], it.get("source"))   # no BOM maker


def _lcsc_web(listed, easyeda):
    """JLCPCB's search listing LM358DR as C7950 under the maker `listed`, EasyEDA answering for C7950 as `easyeda`."""
    def web(method, url, body):
        if "jlcpcb.com" in url:
            return 200, {"data": {"componentPageInfo": {"list": [
                {"componentCode": "C7950", "componentModelEn": "LM358DR", "componentBrandEn": listed,
                 "componentSpecificationEn": "SOP-8", "describe": "Dual op amp", "dataManualUrl": "https://example.com/lm358.pdf"}]}}}
        if "easyeda.com" in url:
            return 200, {"success": True, "result": {"title": "LM358DR", "dataStr": {"head": {"c_para": {
                "Manufacturer Part": "LM358DR", "Manufacturer": easyeda, "package": "SOP-8"}}, "shape": ["P~x"]},
                "packageDetail": {"title": "SOP-8", "dataStr": {"shape": ["PAD~x"]}}}}
        return 404, {}
    return PS1.Router(web)


def _lcsc_row(listed, easyeda, **more):
    http = _lcsc_web(listed, easyeda)
    return PS1._row([P.JLCPCBSearch(http), P.EasyEDA(http)], maker="Texas Instruments", mpn="LM358DR", **more)


def test_an_lcsc_listing_with_no_maker_gives_its_number_only_and_easyeda_must_name_the_maker():
    r = _lcsc_row("", "")                             # nobody's listing, and EasyEDA names nobody either: nothing built
    assert r["lcsc"] == "C7950 (via MPN)" and (r["src"], r["src_package"], r["src_description"], r["src_datasheet"]) == ("",) * 4, r
    assert r["status"] == "needs_footprint_generation", r["status"]
    assert "jlcpcb_search: LM358DR names no maker, so it is not Texas Instruments's own listing: only its LCSC number is used" in r["notes"], r["notes"]
    assert "easyeda: LM358DR names no maker" in r["notes"] and "nor its description, datasheet and CAD data" in r["notes"], r["notes"]
    r = _lcsc_row("", "HGSEMI")                       # EasyEDA says whose it is: a clone's
    assert r["src"] == "" and r["status"] == "needs_footprint_generation" and "easyeda: LM358DR is by HGSEMI, not Texas Instruments" in r["notes"], r


def test_an_lcsc_number_easyeda_or_the_listing_names_the_maker_for_is_taken_as_before():
    r = _lcsc_row("", "TI(德州仪器)")                  # nobody's listing, but EasyEDA names the BOM's maker for the number
    assert (r["lcsc"], r["src"], r["status"]) == ("C7950 (via MPN)", "easyeda", "available_easyeda"), r
    for listed, easyeda in (("Texas Instruments", ""), ("TI", "Texas Instruments"), ("TI", "TI")):
        r = _lcsc_row(listed, easyeda)                # the maker's own listing: its number is that part's, whoever EasyEDA names
        assert (r["lcsc"], r["src"], r["status"]) == ("C7950 (via MPN)", "easyeda", "available_easyeda"), (listed, easyeda, r)
        assert r["src_description"] == "Dual op amp" and r["src_datasheet"] == "https://example.com/lm358.pdf", (listed, r)
        assert "not used" not in r["notes"] and "only its LCSC number" not in r["notes"], (listed, easyeda, r["notes"])
    r = _lcsc_row("HGSEMI", "", lcsc="C7950")         # the BOM's own LCSC number: EasyEDA's answer for it needs no maker
    assert (r["src"], r["status"]) == ("easyeda", "available_easyeda") and "not used" not in r["notes"], r


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
