"""Part numbers no distributor lists, and the field errors next to them, from the 1000-part real BOM (2026-11-19).

32 lines were listed by neither DigiKey nor Mouser. Checked one by one, most were one or two characters off a real
orderable number (WSL2512R010FEA for WSL2512R0100FEA, 25LC1024-I/SN for -I/SM, EEH-ZA1E101P for EEH-ZA1E101XP), and
DigiKey's answer for the whole wrong number showed nothing to suggest. Now a full miss asks for the number's stem and
names the listed numbers a typo away: suggested for the person, never used (built wrong stays 0).

Also here: an MPN cell holding only a maker ('onsemi:') was name-matched to KiCad's OnSemi_ECH8 land; a ferrite bead's
'1 KOHM' description was not read as its Value; 'RES-0Ω' and 'RES-0OHM' became one Altium name after Ω was spelt Ohm.
Run:
    python -m tests.test_unlisted_parts_1119"""
import json, os

from ecadlib import part_lookup as S
from ecadlib.httpcache import Response


def _prod(mpn, maker, case="0603"):
    return {"ManufacturerProductNumber": mpn, "Manufacturer": {"Name": maker}, "Description": {"ProductDescription": "x"},
            "ProductStatus": {"Status": "Active"}, "Parameters": [{"ParameterText": "Package / Case", "ValueText": case}],
            "ProductVariations": []}


class DK:
    """productdetails: 404. keyword search: `by_kw[keywords]` (a list of products), [] for any other keyword."""

    def __init__(self, by_kw):
        self.by_kw, self.keywords = by_kw, []

    def request(self, method, url, body=None, headers=None, cache=True):
        if "oauth2/token" in url:
            return Response(200, json.dumps({"access_token": "t", "expires_in": 599}))
        if "productdetails" in url:
            return Response(404, json.dumps({"status": 404, "detail": "Requested Product Not Found"}))
        if "search/keyword" in url:
            kw = json.loads(body)["Keywords"]
            self.keywords.append(kw)
            got = self.by_kw.get(kw, [])
            return Response(200, json.dumps({"ExactMatches": [], "Products": got, "ProductsCount": len(got)}))
        return Response(0, "", error="no route")

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, body, **kw): return self.request("POST", url, body=body, **kw)


def _dk(http, mpn, maker):
    old = {k: os.environ.get(k) for k in ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")}
    os.environ.update(DIGIKEY_CLIENT_ID="id", DIGIKEY_CLIENT_SECRET="s")
    try:
        return S.DigiKeySourcing(http).part({"mpn": mpn, "manufacturer": maker, "lcsc": ""})
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_stems_are_the_number_less_its_tail_then_its_series():
    assert S.near_stems("WSL2512R010FEA") == ["WSL2512R010", "WSL2512"]
    assert S.near_stems("25LC1024-I/SN") == ["25LC1024-I", "25LC1024"]
    assert S.near_stems("ISL28134IHZ-T7") == ["ISL28134IHZ", "ISL28134"]
    assert S.near_stems("ECS-160-20-33-AGN-TR") == ["ECS-160-20-33-AGN", "ECS-160-20-33"]
    assert S.near_stems("ACM4520V-231-2P-T000") == ["ACM4520V-231-2P-T", "ACM4520"]
    assert S.near_stems("865080542007") == ["865080542"]          # all digits: no series to cut to
    assert S.near_stems("LM358DR") == [] and S.near_stems("") == []     # short numbers: no stem search


def test_typo_distance_is_one_character_below_12_and_two_from_12():
    assert S.typo_distance("WSL2512R010FEA", "WSL2512R0100FEA") == 1     # a dropped digit
    assert S.typo_distance("ISL28134IHZ-T7", "ISL28134FHZ-T7") == 1      # one letter
    assert S.typo_distance("PRG18BB471MB1ON", "PRG18BB471MB1RB") == 2    # two letters on a 15-character number
    assert S.typo_distance("7447779110", "744777910") == 1               # an extra digit
    assert S.typo_distance("PCA9685PW", "PCA9555PW") is None             # two off on 9 characters: another part
    assert S.typo_distance("25LC1024-I/SN", "25LC1024-I/P") is None
    assert S.typo_distance("LM358", "LM358") is None


def test_a_typo_is_named_from_the_stem_search_and_never_taken():
    http = DK({"WSL2512R010": [_prod("WSL2512R0100FEA", "Vishay Dale", "2512"), _prod("WSL2512R0100FEB", "Vishay Dale", "2512"),
                               _prod("WSL2512R2000FEA", "Vishay Dale", "2512")]})
    r = _dk(http, "WSL2512R010FEA", "Vishay")
    assert r.found is False, (r.found, r.mpn)                      # suggested, never the part
    assert "nearest listed: WSL2512R0100FEA" in r.note and "check the BOM's part number" in r.note, r.note
    assert "WSL2512R2000FEA" not in r.note, r.note                 # 3 characters off: another value, not a typo
    assert http.keywords == ["WSL2512R010FEA", "WSL2512R010"], http.keywords   # stopped at the first stem with a hit


def test_at_most_two_stem_searches_and_nothing_said_when_nothing_is_near():
    http = DK({"RA4M1AB3": [_prod("R7FA4M1AB3CFM#AA0", "Renesas")]})
    r = _dk(http, "RA4M1AB3CFM", "Renesas")
    assert r.found is False and r.note == "", r.note
    http = DK({})
    r = _dk(http, "XAL5030-103MEB", "Coilcraft")
    assert r.found is False and len(http.keywords) == 3, http.keywords   # the number, then two stems


def test_the_boms_maker_comes_first_and_another_maker_is_named():
    http = DK({"25LC1024-I": [_prod("25LC1024-I/SM", "Microchip Technology"), _prod("25LC1024-I/SN", "Other Co")]})
    r = _dk(http, "25LC1024-I/SX", "Microchip")
    assert r.note.index("25LC1024-I/SM") < r.note.index("25LC1024-I/SN (Other Co)"), r.note


def test_a_shorter_listed_number_the_bom_number_starts_with_is_named():
    http = DK({"1N4148W": [_prod("1N4148WT", "onsemi", "SOD-523"), _prod("1N4148WS", "onsemi", "SOD-323")]})
    r = _dk(http, "1N4148WT1G", "onsemi")
    assert r.found is False and "1N4148WT" in r.note, r.note


def test_a_maker_name_in_the_mpn_cell_is_no_part_number():
    from ecadlib.bom_triage import _split_mpn, maker_only_mpn
    core, notes, _ = _split_mpn("onsemi:", "Diodes Incorporated")
    assert core == "" and any("a maker's name or a label, not a part number" in n for n in notes), (core, notes)
    assert maker_only_mpn("Texas Instruments") and maker_only_mpn("Acme Widgets:")
    assert not maker_only_mpn("TPS62133") and not maker_only_mpn("BAT-HLD-001") and not maker_only_mpn("")
    assert _split_mpn("TPS62133:", "")[0] == "TPS62133"          # a part number with a stray colon keeps its number


def test_a_name_with_no_digit_never_picks_a_kicad_footprint_by_name():
    from ecadlib.librarian import KiCadLibrary
    lib = KiCadLibrary.__new__(KiCadLibrary)
    lib._fpn = [("ONSEMIECH8", "Package_TO_SOT_SMD:OnSemi_ECH8"), ("SOT23", "Package_TO_SOT_SMD:SOT-23")]
    assert lib.footprint_by_mpn("onsemi:") is None
    assert lib.footprint_by_mpn("OnSemiECH8") == "Package_TO_SOT_SMD:OnSemi_ECH8"


def test_a_ferrite_beads_kilo_ohm_description_is_its_value():
    from ecadlib.altium_export import description_value
    assert description_value("FERRITE BEAD 1 KOHM 0603 1LN", "Inductors") == "1 KOHM"
    assert description_value("FERRITE BEAD 2.2 KOHM 0603 1LN", "Inductors") == "2.2 KOHM"
    assert description_value("FIXED IND 2.2UH 600MA 425 MOHM", "Inductors") == "2.2UH"   # MOHM is DigiKey's milliohm


def test_names_that_differ_only_after_ohm_is_spelt_out_are_two_parts():
    import pandas as pd
    from ecadlib import altium_export as AX
    lines = [dict(designator="R1", mpn="CRCW12060000Z0EA", manufacturer="Vishay", value="", description=""),
             dict(designator="R2", mpn="RC0603JR-070RL", manufacturer="Yageo", value="0Ω", description=""),
             dict(designator="R3", mpn="RC0402JR-070RL", manufacturer="Yageo", value="0Ω", description="")]
    tri = pd.DataFrame([{"line": str(i), "mpn": ln["mpn"], "src_description": d, "local_footprint": "", "package": ""}
                        for i, (ln, d) in enumerate(zip(lines, ["RES SMD 0 OHM JUMPER 1/4W 1206", "", ""]), 1)])
    names = [n for df in AX.to_rows(lines, tri).values() for n in df["Part Number"]]
    assert len({n.casefold() for n in names}) == 3, names


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
    print("ok")
