"""2026-11-23 review, round 2 (the independent review of the fix branch), part search:
  I5  the BOM names a maker and a source's result is another maker's listing of the number: its package was refused
      (review 1.11) but its description, datasheet link(s) and CAD answer were still taken, so the line could be built
      from another maker's datasheet and footprint (onsemi's SS34 is SMC, Vishay's SMA).
  I7  same_maker did not know one company under two names, nor a line that moved to another company with its part
      numbers (MCC / Micro Commercial Components, ams / ams-OSRAM, UTC / Unisonic, Cree / Wolfspeed, Silicon Labs /
      Skyworks, NXP / Nexperia): the maker's own listing was refused as "another maker's" and the part held.
Run:
    python -m tests.test_review1123_round2_part_search"""
from ecadlib import bom_triage as BT
from ecadlib import providers as P
from ecadlib.kicad_index import KiCadIndex

SHEET, MORE = "https://example.com/ss34.pdf", "https://example.com/ss34-b.pdf"


class _Cad(P.Provider):
    """Any source (a plugin, EasyEDA for the BOM's own LCSC number) that answers with a description, datasheet links
    and CAD data, as the maker it is given."""
    name = "plug"

    def __init__(self, maker, name="plug", cad=True):
        self.http, self.maker, self.name, self.cad = None, maker, name, cad

    def lookup(self, line):
        return P.SourceResult(self.name, found=True, mpn=line["mpn"], manufacturer=self.maker, has_footprint=self.cad,
                              has_symbol=self.cad, extra={"description": f"Schottky 40V 3A by {self.maker or 'nobody'}",
                                                          "datasheet": SHEET, "datasheets": [SHEET, MORE]})


def _row(provs, maker="Vishay", mpn="SS34", **more):
    line = {"designator": "D1", "qty": "1", "mpn": mpn, "manufacturer": maker, "value": mpn, **more}
    return BT.triage([line], KiCadIndex([]), provs if isinstance(provs, list) else [provs], None)[0]


TAKEN = ("src", "src_mpn", "src_description", "src_description_from", "src_datasheet", "src_datasheet_from",
         "src_datasheet_more", "src_symbol", "src_footprint")


# ------------------------------------------------------------------ I5 another maker's result supplies nothing
def test_another_makers_result_gives_no_description_datasheet_or_cad_answer():
    r = _row(_Cad("onsemi"))
    assert [r[k] for k in TAKEN] == [""] * len(TAKEN), {k: r[k] for k in TAKEN}
    assert r["status"] == "needs_footprint_generation", r["status"]         # not "available_easyeda": not its footprint
    assert r["src_makers"] == "onsemi" and r["src_manufacturer"] == "", r     # who lists it is still said
    assert "plug: SS34 is by onsemi, not Vishay: its package and LCSC number are not used, nor its description, " \
           "datasheet and CAD data (check the BOM's maker)" in r["notes"], r["notes"]


def test_the_makers_own_result_is_taken_as_before():
    for maker, theirs in (("Vishay", "Vishay General Semiconductor"), ("Fairchild", "onsemi"), ("", "onsemi"),
                          ("-", "onsemi"), ("Vishay", "")):                 # own, renamed, no maker on the line, none named
        r = _row(_Cad(theirs), maker=maker)
        assert (r["src"], r["src_datasheet"], r["src_datasheet_from"], r["src_datasheet_more"]) == \
            ("plug", SHEET, "plug", MORE), (maker, theirs, r)
        assert r["src_description"].startswith("Schottky 40V 3A") and r["src_description_from"] == "plug", r
        assert r["status"] == "available_easyeda" and "not used" not in r["notes"], (maker, theirs, r["status"], r["notes"])


def test_the_makers_own_source_after_another_makers_is_still_asked_and_taken():
    r = _row([_Cad("onsemi", "first"), _Cad("Vishay Semiconductors", "second")])     # the first's CAD answer does not end it
    assert (r["src"], r["src_description_from"], r["src_datasheet_from"], r["status"]) == \
        ("second", "second", "second", "available_easyeda"), r
    assert r["src_datasheet_more"] == MORE and "first: SS34 is by onsemi, not Vishay" in r["notes"], r
    r = _row([_Cad("Vishay", "first"), _Cad("onsemi", "second", cad=False)])        # and one before it stays
    assert (r["src"], r["src_description_from"], r["src_datasheet"], r["src_datasheet_more"]) == \
        ("first", "first", SHEET, MORE), r


class _Lcsc(P.JLCPCBSearch):
    """LCSC's answer for the BOM's own LCSC number (its description of it), under LCSC's own name for the maker."""
    def __init__(self, maker):
        self.http, self.maker = None, maker

    def lookup_lcsc(self, lcsc):
        return P.SourceResult(self.name, found=True, lcsc=lcsc, manufacturer=self.maker,
                              extra={"description": "16MHz 20pF Crystals"})


def test_lcsc_description_of_the_boms_own_lcsc_number_is_still_taken():
    # the BOM names that very listing, and LCSC's two routes spell its maker two ways (EasyEDA "SOSET", the search
    # "SST": tests/fixtures/jlcpcb/C2975360.json): not "another maker's result", its description stays
    for maker, theirs in (("SOSET", "SST"), ("Vishay", "VISHAY(威世)"), ("", "onsemi")):
        r = _row(_Lcsc(theirs), maker=maker, mpn="16M 20PF 10PPM", lcsc="C2975360")
        assert r["src_description"] == "16MHz 20pF Crystals" and "not used" not in r["notes"], (maker, theirs, r)


if __name__ == "__main__":
    import sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for f in fns:
        f()
    print(f"{len(fns)} tests passed")
    sys.exit(0)
