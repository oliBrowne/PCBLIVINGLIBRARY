"""DigiKey's productdetails answers 404 when several makers sell the same part number (LM358DR from TI and Rohm,
SMBJ5.0A, SS34): 175 of a 1000-part real BOM lost their case that way (2026-11-16). The keyword search lists them all;
the case is taken only from the BOM maker's own listing, or, with no maker on the line, only when every maker's case
agrees (onsemi's SS34 is SMC, Vishay's is SMA). Run:
    python -m tests.test_digikey_shared_mpn"""
import json, os

from ecadlib import part_lookup as S
from ecadlib.httpcache import Response


def _prod(mpn, maker, case):
    return {"ManufacturerProductNumber": mpn, "Manufacturer": {"Name": maker}, "Description": {"ProductDescription": "x"},
            "ProductStatus": {"Status": "Active"}, "Parameters": [{"ParameterText": "Package / Case", "ValueText": case}],
            "ProductVariations": []}


class HTTP:
    def __init__(self, exact, products=None):
        self.exact, self.products, self.calls, self.log = exact, products, [], []

    def request(self, method, url, body=None, headers=None, cache=True):
        self.calls.append((method, url, body))
        if "oauth2/token" in url:
            return Response(200, json.dumps({"access_token": "t", "expires_in": 599}))
        if "productdetails" in url:
            return Response(404, json.dumps({"status": 404, "detail": "Requested Product Not Found"}))
        if "search/keyword" in url:
            return Response(200, json.dumps({"ExactMatches": self.exact,
                                             "Products": self.products if self.products is not None else self.exact}))
        return Response(0, "", error="no route")

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, body, **kw): return self.request("POST", url, body=body, **kw)


SS34 = [_prod("SS34", "onsemi", "DO-214AB, SMC"), _prod("SS34", "Vishay General Semiconductor - Diodes Division", "DO-214AC, SMA")]


def _case(r):
    return r.specs.get("package_text") or r.specs.get("package") or ""


def _ask(http, mpn, maker=""):
    old = {k: os.environ.get(k) for k in ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")}
    os.environ.update(DIGIKEY_CLIENT_ID="id", DIGIKEY_CLIENT_SECRET="s")
    try:
        return S.DigiKeySourcing(http).part({"mpn": mpn, "manufacturer": maker, "lcsc": ""})
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_bom_maker_picks_its_own_listing():
    r = _ask(HTTP(SS34), "SS34", "onsemi")
    assert r.found and r.manufacturer == "onsemi" and "SMC" in _case(r), (r.found, r.manufacturer, r.specs)
    r = _ask(HTTP(SS34), "SS34", "Vishay")
    assert r.found and "SMA" in _case(r), r.specs


def test_no_maker_and_cases_differ_is_not_found():
    r = _ask(HTTP(SS34), "SS34")
    assert not r.found and "several makers" in (r.note or ""), (r.found, r.note)


def test_no_maker_and_cases_agree():
    two = [_prod("LM358DR", "Texas Instruments", '8-SOIC (0.154", 3.90mm Width)'),
           _prod("LM358DR", "Rohm Semiconductor", '8-SOIC (0.154", 3.90mm Width)')]
    r = _ask(HTTP(two), "LM358DR")
    assert r.found and "8-SOIC" in _case(r), (r.found, r.note)


def test_maker_not_among_listings_is_not_found():
    r = _ask(HTTP(SS34), "SS34", "Diodes Incorporated")
    assert not r.found, r.manufacturer


def test_renamed_part_is_not_taken():
    # 0603YC104KAT2A: the keyword search answers with AVX's new number, which is not this part number
    r = _ask(HTTP([], [_prod("KGM15AR71C104KT", "KYOCERA AVX", "0603 (1608 Metric)")]), "0603YC104KAT2A", "AVX")
    assert not r.found, r.mpn


def test_a_division_name_is_not_another_maker():
    from ecadlib import providers as P
    assert not P.same_maker("Diodes Incorporated", "Vishay General Semiconductor - Diodes Division")
    assert P.same_maker("Vishay", "Vishay General Semiconductor - Diodes Division")
    assert P.same_maker("TDK", "EPCOS - TDK Electronics")


def test_no_maker_and_cases_agree_but_device_packages_differ_is_not_found():
    def prod(maker, dev):
        x = _prod("XYZ1", maker, "")
        x["Parameters"] = [{"ParameterText": "Supplier Device Package", "ValueText": dev}]
        return x
    r = _ask(HTTP([prod("Maker A", "SOT-23-3"), prod("Maker B", "SC-70-3")]), "XYZ1")
    assert not r.found, r.specs


def test_no_maker_and_a_cut_short_result_page_is_not_found():
    class Cut(HTTP):
        def request(self, method, url, body=None, headers=None, cache=True):
            r = super().request(method, url, body, headers, cache)
            if "search/keyword" in url:
                d = json.loads(r.text)
                d["ProductsCount"] = 37                   # other makers' listings past the first page
                return Response(200, json.dumps(d))
            return r
    two = [_prod("LM358DR", "Texas Instruments", '8-SOIC'), _prod("LM358DR", "Rohm Semiconductor", '8-SOIC')]
    r = _ask(Cut(two), "LM358DR")
    assert not r.found, r.specs
    assert _ask(Cut(two), "LM358DR", "Texas Instruments").found      # the BOM maker's own listing is still found


class Limited(HTTP):
    """DigiKey's daily allowance is used up: it answers 429 (keyword search, and productdetails unless keyword_only)."""
    def __init__(self, keyword_only=True):
        super().__init__([])
        self.keyword_only = keyword_only

    def request(self, method, url, body=None, headers=None, cache=True):
        if "search/keyword" in url or ("productdetails" in url and not self.keyword_only):
            self.calls.append((method, url, body))
            return Response(429, json.dumps({"status": 429, "detail": "Daily Ratelimit exceeded. Retry in 36000 seconds."}))
        return super().request(method, url, body, headers, cache)


def test_daily_limit_is_an_error_not_not_found():
    from ecadlib import providers as P
    for http in (Limited(), Limited(keyword_only=False)):
        r = _ask(http, "LM358DR", "Texas Instruments")
        assert r.found is None and r.error and P.account_limit(r.error), (r.found, r.error)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
