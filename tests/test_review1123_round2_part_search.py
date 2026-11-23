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
                          ("-", "onsemi")):                 # own, renamed, no maker on the line (round 3, A9: a result
                                                            # that names no maker is not Vishay's own any more)
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


# ------------------------------------------------------------------ I7 one company under two names
SAME = [("MCC", "Micro Commercial Components"), ("MCC", "Micro Commercial Co"), ("Micro Commercial Components", "Micro Commercial Co"),
        ("MCC", "MCC (Micro Commercial Components)"),
        ("austriamicrosystems", "ams OSRAM"), ("austriamicrosystems AG", "ams-OSRAM USA INC."), ("OSRAM Opto Semiconductors", "ams-OSRAM"),
        ("UTC", "Unisonic Technologies"), ("UTC", "UTC(Unisonic Tech)"), ("Unisonic", "Unisonic Technologies Co., Ltd"),
        ("Cree", "Wolfspeed"), ("Cree Inc.", "Wolfspeed, Inc."), ("Cree/Wolfspeed", "Wolfspeed"),
        ("Silicon Labs", "Skyworks Solutions Inc."), ("Silicon Laboratories", "Skyworks"), ("SiLabs", "Skyworks Solutions"),
        ("Skyworks", "Skyworks Solutions Inc."),
        ("ISSI", "Integrated Silicon Solution Inc."), ("ISSI", "ISSI, Integrated Silicon Solution Inc"),
        ("MXIC", "Macronix"), ("Macronix International", "MXIC(Macronix)"),
        ("InvenSense", "TDK InvenSense"), ("InvenSense", "TDK"),
        ("RFMD", "Qorvo"), ("RF Micro Devices", "Qorvo US Inc."), ("TriQuint", "Qorvo"),
        ("HRS", "Hirose Electric Co Ltd"), ("HRS(Hirose)", "Hirose"),
        ("JAE", "JAE Electronics"), ("JAE", "Japan Aviation Electronics"),
        # in the table before this round: still one maker
        ("Fairchild", "onsemi"), ("Linear Technology", "Analog Devices Inc."), ("Maxim Integrated", "Analog Devices"),
        ("International Rectifier", "Infineon Technologies"), ("Atmel", "Microchip Technology"),
        ("Microsemi", "Microchip"), ("Cree", "Cree LED")]
# a wrong alias lets another maker's listing through: none of these is the other
DIFFER = [("MCC", "Microchip"), ("Micro Commercial Co", "Microchip Technology"), ("MCC", "Micro Crystal"),
          ("ams", "Advanced Monolithic Systems"), ("AMS", "AMSC"), ("ams", "Analog Devices"), ("OSRAM", "Cree"),
          ("AMS", "ams OSRAM"),        # "AMS" on a BOM is also Advanced Monolithic Systems (AMS1117): left apart
                                       # (round 3, B4: "ams" in small letters, as ams AG writes itself, is ams-OSRAM)
          ("UTC", "UMW"), ("Unisonic", "Unisem"), ("UTC", "Texas Instruments"),
          ("Cree", "Infineon"), ("Wolfspeed", "Cree LED"), ("Wolfspeed", "onsemi"),
          ("Silicon Labs", "Silicon Storage Technology"), ("Skyworks", "Qorvo"), ("Silicon Labs", "Silicon Motion"),
          ("Skyworks", "Microchip"), ("ISSI", "Winbond"), ("ISSI", "Silicon Labs"), ("MXIC", "Micron"), ("Macronix", "Microchip"),
          ("InvenSense", "Bosch Sensortec"), ("RFMD", "Skyworks"), ("TriQuint", "Analog Devices"), ("HRS", "Harris"),
          ("Hirose", "Hirosugi"), ("JAE", "JST"), ("JAE", "Japan Display"),
          ("Nexperia", "onsemi"), ("Nexperia", "Diodes Incorporated"), ("Nexperia", "Texas Instruments"), ("NXP", "onsemi")]


RENAMED = [("Cree", "Wolfspeed, Inc.", "C3M0065090D"), ("MCC", "Micro Commercial Co", "SS34-TP"),
           ("Silicon Labs", "Skyworks Solutions Inc.", "SI8621BB-B-IS"), ("UTC", "Unisonic Technologies", "LM358G-S08-R")]


def test_one_company_under_two_names_is_one_maker():
    for a, b in SAME:
        assert P.same_maker(a, b) and P.same_maker(b, a), (a, b)


def test_another_company_is_still_another_maker():
    for a, b in DIFFER:
        assert not P.same_maker(a, b) and not P.same_maker(b, a), (a, b)


def test_a_renamed_makers_own_listing_is_taken_and_a_strangers_refused():
    for maker, theirs, mpn in RENAMED:
        r = _row(_Cad(theirs), maker=maker, mpn=mpn)
        assert r["src"] == "plug" and r["src_datasheet"] == SHEET and "not used" not in r["notes"], (maker, theirs, r)
        r = _row(_Cad("HGSEMI"), maker=maker, mpn=mpn)
        assert r["src"] == "" and r["src_datasheet"] == "" and f"is by HGSEMI, not {maker}" in r["notes"], (maker, r)


# ------------------------------------------------------------------ I7 NXP / Nexperia
NXP_SAME = [("NXP", "Nexperia"), ("Nexperia", "NXP USA Inc."), ("NXP Semiconductors", "Nexperia USA Inc."), ("Philips", "Nexperia")]


def test_nexperia_sells_nxps_standard_products_under_the_same_numbers():
    for a, b in NXP_SAME:
        assert P.same_maker(a, b) and P.same_maker(b, a), (a, b)
    assert P.maker_key("Nexperia") == "NEXPERIA" and P.maker_key("NXP USA Inc.") == "NXP"     # still two names
    for a, b in (("Freescale", "Nexperia"), ("Freescale Semiconductor", "Nexperia USA Inc."), ("Nexperia", "onsemi"),
                 ("Nexperia", "NXP Power"), ("Philips Lumileds", "Nexperia")):     # Freescale's parts stayed with NXP
        assert not P.same_maker(a, b) and not P.same_maker(b, a), (a, b)
    for maker, theirs in (("NXP", "Nexperia USA Inc."), ("Nexperia", "NXP Semiconductors")):
        r = _row(_Cad(theirs), maker=maker, mpn="BC847C,215")
        assert r["src"] == "plug" and r["src_datasheet"] == SHEET and "not used" not in r["notes"], (maker, theirs, r)
        r = _row(_Cad("HGSEMI"), maker=maker, mpn="BC847C,215")
        assert r["src"] == "" and r["src_datasheet"] == "" and f"is by HGSEMI, not {maker}" in r["notes"], (maker, r)


if __name__ == "__main__":
    import sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for f in fns:
        f()
    print(f"{len(fns)} tests passed")
    sys.exit(0)
