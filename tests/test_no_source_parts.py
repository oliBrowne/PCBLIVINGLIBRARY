"""Lines the 1000-part real BOM (pass 1, main 534cb9e, 2026-11-17) left with no datasheet and no source: DigiKey and
Mouser both answered "not found", triage wrote nothing about it, and the person could not tell a wrong part number or
maker from a lookup that never ran. Each source's miss is now said, with why when the answer shows it (the number
listed only by another maker, or only near numbers such as a packaging suffix). Near numbers are named, never used.
Run:
    python -m tests.test_no_source_parts"""
import json, os, time
from pathlib import Path

from tests.fixtures_lite import lite

from ecadlib import part_lookup as S
from ecadlib import providers as P
from ecadlib.httpcache import Response


def _prod(mpn, maker, case="8-SOIC"):
    return {"ManufacturerProductNumber": mpn, "Manufacturer": {"Name": maker}, "Description": {"ProductDescription": "x"},
            "ProductStatus": {"Status": "Active"}, "Parameters": [{"ParameterText": "Package / Case", "ValueText": case}],
            "ProductVariations": []}


class DK:
    """productdetails answers `details` (a product dict, or None for 404); the keyword search answers `listed`."""

    def __init__(self, details, listed):
        self.details, self.listed, self.calls = details, listed, []

    def request(self, method, url, body=None, headers=None, cache=True):
        if "oauth2/token" in url:
            return Response(200, json.dumps({"access_token": "t", "expires_in": 599}))
        self.calls.append(url)
        if "productdetails" in url:
            if self.details is None:
                return Response(404, json.dumps({"status": 404, "detail": "Requested Product Not Found"}))
            return Response(200, json.dumps({"Product": self.details}))
        if "search/keyword" in url:
            return Response(200, json.dumps({"ExactMatches": [], "Products": self.listed, "ProductsCount": len(self.listed)}))
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


def test_best_match_answer_still_finds_the_exact_listing():
    # productdetails answers another number, the keyword search lists this one: taken from the keyword search
    http = DK(_prod("LM358DRG4", "Texas Instruments"), [_prod("LM358DR", "Texas Instruments")])
    r = _dk(http, "LM358DR", "Texas Instruments")
    assert r.found and r.mpn == "LM358DR", (r.found, r.mpn, r.note)
    assert any("search/keyword" in u for u in http.calls)


def test_best_match_is_named_never_taken():
    r = _dk(DK(_prod("CRCW0402100RFKED", "Vishay Dale"), [_prod("CRCW0402100RFKED", "Vishay Dale", "0402")]),
            "CRCW0402100RFKEA", "Vishay")
    assert r.found is False and "CRCW0402100RFKED" in r.note and "check the BOM's part number" in r.note, (r.found, r.note)


def test_listed_only_by_another_maker_is_said():
    r = _dk(DK(None, [_prod("PCA9685PW", "NXP USA Inc.", "28-TSSOP")]), "PCA9685PW", "Texas Instruments")
    assert r.found is False and "only from NXP USA Inc." in r.note and "Texas Instruments" in r.note, r.note


def test_packaging_suffix_listings_are_named():
    r = _dk(DK(None, [_prod("PCA9685PW,118", "NXP USA Inc."), _prod("PCA9685PW,112", "NXP USA Inc."),
                      _prod("PCA9555PW,118", "NXP USA Inc.")]), "PCA9685PW", "NXP")
    assert r.found is False and "PCA9685PW,118" in r.note and "PCA9685PW,112" in r.note, r.note
    assert "PCA9555" not in r.note, r.note                      # another part is not a near number


def test_near_numbers():
    assert S.near_numbers("PCA9685PW", ["PCA9685PW,118", "PCA9685PW", "ABC"]) == ["PCA9685PW,118"]
    assert S.near_numbers("CRCW0402100RFKEA", ["CRCW0402100RFKED", "CRCW04021K00FKED"]) == ["CRCW0402100RFKED"]
    assert S.near_numbers("RP2040", ["SC0914(13)"]) == []
    assert S.near_numbers("R1", ["R10"]) == []                   # too short to say anything


class MO:
    def __init__(self, parts):
        self.parts = parts

    def request(self, method, url, body=None, headers=None, cache=True):
        return Response(200, json.dumps({"Errors": [], "SearchResults": {"NumberOfResult": len(self.parts), "Parts": self.parts}}))

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, body, **kw): return self.request("POST", url, body=body, **kw)


def _mo(parts, mpn, maker):
    old = os.environ.get("MOUSER_API_KEY")
    os.environ["MOUSER_API_KEY"] = "k"
    try:
        return S.MouserSourcing(MO(parts)).part({"mpn": mpn, "manufacturer": maker, "lcsc": ""})
    finally:
        os.environ.pop("MOUSER_API_KEY", None) if old is None else os.environ.__setitem__("MOUSER_API_KEY", old)


def test_mouser_miss_says_why():
    two = [{"ManufacturerPartNumber": "SS34", "Manufacturer": "onsemi"},
           {"ManufacturerPartNumber": "SS34", "Manufacturer": "Vishay"}]
    r = _mo(two, "SS34", "Diodes Incorporated")
    assert r.found is False and "only from onsemi, Vishay" in r.note, r.note
    r = _mo([], "RP2040", "Raspberry Pi")
    assert r.found is False and r.note == "", r.note


def test_triage_writes_each_source_miss():
    from ecadlib import bom_triage as BT

    class Miss(P.Provider):
        def __init__(self, name, note=""):
            self.name, self._note = name, note

        def lookup(self, line):
            res = P.SourceResult(self.name, found=False, mpn=line.get("mpn", ""))
            if self._note:
                res.extra["note"] = self._note
            return res

    class Index:
        def match(self, *a, **k): return None, None
        def footprint(self, *a, **k): return None, None
        def find_footprint_for_package(self, *a, **k): return None

    rows = BT.triage([{"designator": "U1", "qty": "1", "mpn": "PCA9685PW", "manufacturer": "Texas Instruments"}],
                     Index(), [Miss("digikey", "DigiKey lists PCA9685PW only from NXP USA Inc., not Texas Instruments"),
                               Miss("mouser")], None)
    notes = rows[0]["notes"]
    assert "digikey: not found (DigiKey lists PCA9685PW only from NXP" in notes and "mouser: not found" in notes, notes


def test_part_info_note_reaches_the_source_result():
    info = S.PartInfo("digikey", False, "X", note="DigiKey lists no X; it lists X-1: check the BOM's part number")
    res = P.from_part_info(info, P.SourceResult("digikey"))
    assert res.found is False and "X-1" in res.extra["note"]


# ---------------------------------------------------------------- a day-old "not found" is asked again
def _cache_with(tmp, url, status, text, age_s, body=None):
    from ecadlib.httpcache import CachedHTTP, key_url
    h = CachedHTTP(str(tmp))
    f = Path(tmp) / f"{h._key('POST' if body else 'GET', key_url(url), body)}.json"
    f.write_text(json.dumps({"url": url, "status": status, "text": text}), encoding="utf-8")
    t = time.time() - age_s
    os.utime(f, (t, t))
    asked = []

    class R:
        status_code, text, ok, headers = 200, json.dumps({"fresh": True}), True, {}

    h.s.request = lambda *a, **k: asked.append(a) or R()
    return h, asked


MOUSER = "https://api.mouser.com/api/v1/search/partnumber?apiKey=k"
EMPTY_MOUSER = json.dumps({"Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []}})
FOUND_MOUSER = json.dumps({"Errors": [], "SearchResults": {"NumberOfResult": 1, "Parts": [{"ManufacturerPartNumber": "X"}]}})


@lite
def test_day_old_empty_answer_is_asked_again(tmp_path):
    h, asked = _cache_with(tmp_path, MOUSER, 200, EMPTY_MOUSER, 2 * 86400, body="{}")
    h.min_interval = 0
    r = h.post(MOUSER, "{}")
    assert asked and not r.from_cache


@lite
def test_fresh_empty_answer_is_kept(tmp_path):
    h, asked = _cache_with(tmp_path, MOUSER, 200, EMPTY_MOUSER, 3600, body="{}")
    r = h.post(MOUSER, "{}")
    assert r.from_cache and not asked


@lite
def test_old_found_answer_keeps_the_full_ttl(tmp_path):
    h, asked = _cache_with(tmp_path, MOUSER, 200, FOUND_MOUSER, 3 * 86400, body="{}")
    r = h.post(MOUSER, "{}")
    assert r.from_cache and not asked


@lite
def test_offline_still_uses_an_old_empty_answer(tmp_path):
    h, asked = _cache_with(tmp_path, MOUSER, 200, EMPTY_MOUSER, 3 * 86400, body="{}")
    h.offline = True
    r = h.post(MOUSER, "{}")
    assert r.from_cache and not asked


def test_empty_answer_shapes():
    from ecadlib.httpcache import empty_answer
    dk = "https://api.digikey.com/products/v4/search/keyword"
    assert not empty_answer("https://api.digikey.com/products/v4/search/X/productdetails", 404, "{}")   # keyword decides
    assert empty_answer(dk, 200, json.dumps({"Products": [], "ExactMatches": []}))
    assert empty_answer(dk, 200, json.dumps({"Products": None}))
    assert not empty_answer(dk, 200, json.dumps({"Products": [{"ManufacturerProductNumber": "X"}], "ProductsCount": 1}))
    assert empty_answer(MOUSER, 200, EMPTY_MOUSER) and not empty_answer(MOUSER, 200, FOUND_MOUSER)
    assert not empty_answer("https://www.ti.com/lit/ds/x.pdf", 404, "")          # not an API: a dead link stays dead
    assert not empty_answer(dk, 200, "not json")


def test_librarian_says_no_distributor_lists_it():
    from ecadlib.librarian import no_listing
    row = {"notes": "digikey: not found (DigiKey lists no PCA9685PW, only PCA9685PW,118: check the BOM's part number); "
                    "mouser: not found; jlcpcb_search: JLCPCB refused the request (403); not asked again this run"}
    line = no_listing(row, "PCA9685PW")
    assert line.startswith("part search: no distributor lists PCA9685PW (digikey, mouser answered: not found"), line
    assert "PCA9685PW,118" in line and "not a real part" in line, line
    assert no_listing({"notes": "mouser: not found", "src_description": "Single Color LEDs"}, "X") == ""   # one found it
    assert no_listing({"notes": "jlcpcb_search: JLCPCB refused the request (403)"}, "X") == ""            # never asked
    assert no_listing({"notes": ""}, "X") == ""


def test_leading_zeros_and_former_names():
    from ecadlib.kicad_index import same_mpn
    assert same_mpn("22-23-2021", "0022232021") and same_mpn("47219-2001", "0472192001")
    assert not same_mpn("0402", "402") and not same_mpn("SMAJ5.0A", "SMAJ50A") and not same_mpn("12345", "012345")
    assert P.same_maker("Omron", "Aratas (Formerly Omron Components)")
    assert not P.same_maker("TDK", "Aratas (Formerly Omron Components)")
    # DigiKey's keyword search lists Molex's zero-padded number: it is this part
    r = _dk(DK(None, [_prod("0022232021", "Molex", "Through Hole")]), "22-23-2021", "Molex")
    assert r.found, (r.found, r.note)
    r = _dk(DK(None, [_prod("G5V-1 DC5", "Aratas (Formerly Omron Components)", "Through Hole")]), "G5V-1-DC5", "Omron")
    assert r.found, (r.found, r.note)


def test_reel_prefix_is_a_near_number():
    assert S.near_numbers("3216FF2-R", ["TR/3216FF2-R"]) == ["TR/3216FF2-R"]
    assert S.near_numbers("WS2812B", ["XL-5050RGBC-WS2812B"]) == []          # another maker's part named after it


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
