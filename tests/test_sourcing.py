"""Sourcing (offline; every vendor response here is a FAKE shaped after the documented fields, see each provider's
`shapes` for what is checked and what is assumed). Run:
    python3 -m tests.test_sourcing"""
import csv, json, os, tempfile
from pathlib import Path

from ecadlib import altium365 as A, bom_triage as T, cli, sourcing as S
from ecadlib.httpcache import CachedHTTP, Response
from tests.test_lifecycle import _env, _schema

NO_CREDS = dict(NEXAR_SUPPLY_TOKEN=None, NEXAR_CLIENT_ID=None, NEXAR_CLIENT_SECRET=None, DIGIKEY_CLIENT_ID=None,
                DIGIKEY_CLIENT_SECRET=None, MOUSER_API_KEY=None)


class Router:
    """FakeHTTP that answers with fn(method, url, body) -> (status, payload)."""
    def __init__(self, fn):
        self.fn, self.calls, self.log = fn, [], []

    def request(self, method, url, body=None, headers=None, cache=True):
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers or {}, "cache": cache})
        status, payload = self.fn(method, url, body)
        if status == 0:
            return Response(0, "", error=payload)
        return Response(status, payload if isinstance(payload, str) else json.dumps(payload))

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, body, **kw): return self.request("POST", url, body=body, **kw)


# ------------------------------------------------------------------ Nexar-shaped fakes
def spec(name, value, short=None):
    return {"attribute": {"name": name, "shortname": short or name.lower().replace(" ", "")}, "displayValue": value, "value": value}


def offer(sku, stock, prices, moq=1, mult=1, lead=None, currency="USD"):
    return {"sku": sku, "inventoryLevel": stock, "moq": moq, "orderMultiple": mult, "factoryLeadDays": lead,
            "packaging": "Cut Tape", "clickUrl": f"https://octopart.com/click/{sku}",
            "prices": [{"quantity": q, "price": p, "currency": currency, "convertedPrice": p, "convertedCurrency": "USD"}
                       for q, p in prices]}


def npart(mpn, mfr, specs, sellers=(), similar=(), lifecycle="Production", lead=None):
    return {"mpn": mpn, "manufacturer": {"name": mfr}, "octopartUrl": f"https://octopart.com/{mpn.lower()}",
            "shortDescription": "", "estimatedFactoryLeadDays": lead,
            "specs": list(specs) + ([spec("Lifecycle Status", lifecycle, "lifecyclestatus")] if lifecycle else []),
            "sellers": [{"company": {"name": n}, "isAuthorized": True, "offers": offs} for n, offs in sellers],
            "similarParts": list(similar)}


R10K = [spec("Resistance", "10 kΩ"), spec("Tolerance", "±1%"), spec("Power Rating", "100 mW", "powerrating"),
        spec("Case/Package", "0603", "case_package")]
C100N = [spec("Capacitance", "100 nF"), spec("Tolerance", "±10%"), spec("Voltage Rating (DC)", "16 V", "voltagerating_dc"),
         spec("Dielectric", "X7R", "dielectric"), spec("Case/Package", "0402", "case_package")]
ONSTOCK = [("Digi-Key", [offer("ALT-ND", 100000, [(1, 0.05), (100, 0.002)])])]

NEXAR = {
    "0603WAF1002T5E": npart("0603WAF1002T5E", "UNI-ROYAL", R10K, [
        ("Digi-Key", [offer("0603WAF1002T5ECT-ND", 50000, [(1, 0.10), (10, 0.016), (100, 0.0049)])]),
        ("Mouser", [offer("652-0603WAF1002T5E", 60, [(1, 0.10), (50, 0.01)])])]),
    "CL05B104KO5NNNC": npart("CL05B104KO5NNNC", "Samsung Electro-Mechanics", C100N, [
        ("Mouser", [offer("187-CL05B104KO5NNNC", 5, [(1, 0.02), (100, 0.004)])])]),
    "CH340C": npart("CH340C", "WCH", [], [("Digi-Key", [offer("CH340C-ND", 0, [(1, 0.9), (100, 0.6)], lead=56)])],
                    similar=[npart("CH340G", "WCH", [], [("LCSC", [offer("C14267", 9000, [(1, 0.5)])])]),
                             npart("CH340T", "WCH", [], lifecycle="NRND")], lead=56),
}
SEARCH = {   # supSearch results for the parametric queries
    "10k 0603 resistor": [
        npart("RC0603FR-0710KL", "YAGEO", R10K, ONSTOCK),
        npart("RC0603JR-0710KL", "YAGEO", [spec("Resistance", "10 kΩ"), spec("Tolerance", "±5%"),
                                           spec("Power Rating", "100 mW"), spec("Case/Package", "0603")], ONSTOCK),
        npart("ERJ-3EKF1002V", "Panasonic", R10K, ONSTOCK, lifecycle="NRND"),
        npart("CR0603-FX-1002ELF", "Bourns", [spec("Resistance", "10 kΩ"), spec("Tolerance", "±1%"),
                                              spec("Case/Package", "0603")], ONSTOCK),
        npart("0402WGF1002TCE", "UNI-ROYAL", [spec("Resistance", "10 kΩ"), spec("Tolerance", "±1%"),
                                              spec("Power Rating", "62.5 mW"), spec("Case/Package", "0402")], ONSTOCK)],
    "100nF 0402 capacitor X7R": [
        npart("GRM155R71C104KA88D", "Murata", C100N, ONSTOCK),
        npart("GRM155R61A104KA01D", "Murata", [spec("Capacitance", "100 nF"), spec("Tolerance", "±10%"),
                                               spec("Voltage Rating (DC)", "10 V"), spec("Dielectric", "X5R"),
                                               spec("Case/Package", "0402")], ONSTOCK),
        npart("CL05B104KB54PNC", "Samsung Electro-Mechanics", [spec("Capacitance", "100 nF"), spec("Tolerance", "±10%"),
              spec("Voltage Rating (DC)", "50 V"), spec("Dielectric", "X7R"), spec("Case/Package", "0402")], ONSTOCK)],
}
JLC = {"C25804": {"componentCode": "C25804", "componentModelEn": "0603WAF1002T5E", "componentBrandEn": "UNI-ROYAL",
                  "stockCount": 1000, "componentSpecificationEn": "0603", "describe": "10kΩ ±1% 100mW 0603",
                  "componentPrices": [{"startNumber": 20, "endNumber": 199, "productPrice": 0.0011},
                                      {"startNumber": 200, "endNumber": -1, "productPrice": 0.0008}]},
       "C1525": {"componentCode": "C1525", "componentModelEn": "CL05B104KO5NNNC", "componentBrandEn": "SAMSUNG",
                 "stockCount": 0, "componentPrices": [{"startNumber": 1, "productPrice": 0.003}]},
       "C84681": {"componentCode": "C84681", "componentModelEn": "CH340C", "componentBrandEn": "WCH", "stockCount": 0,
                  "componentPrices": []}}


def route(method, url, body):
    if "identity.nexar.com" in url:
        return 200, {"access_token": "tok"}
    b = json.loads(body) if body else {}
    if "api.nexar.com/graphql" in url:
        q, v = b["query"], b["variables"]
        if "supSearchMpn" in q:
            p = NEXAR.get(v["q"])
            return 200, {"data": {"supSearchMpn": {"hits": int(bool(p)), "results": [{"part": p}] if p else []}}}
        return 200, {"data": {"supSearch": {"hits": 1, "results": [{"part": p} for p in SEARCH.get(v["q"], [])]}}}
    if "jlcpcb.com" in url:
        it = JLC.get(b["keyword"])
        return 200, {"data": {"componentPageInfo": {"list": [it] if it else []}}}
    return 0, "no route"


BOM = ("Designator,Qty,Value,Footprint,Manufacturer,MPN,LCSC,Description,DNP\n"
       '"R1,R2,R3,R4",4,10k,R_0603_1608Metric,UNI-ROYAL,0603WAF1002T5E,C25804,Resistor 10k 1% 0603,\n'
       "C1,1,100nF,C_0402_1005Metric,Samsung Electro-Mechanics,CL05B104KO5NNNC,C1525,100nF 16V X7R 0402,\n"
       "U1,1,CH340C,SOIC-16,WCH,CH340C,C84681,USB UART,\n"
       "U2,1,OBS123,SOIC-8,Nobody,OBS123,,gone,\n"
       "J1,1,Conn,JST,JST,SM04B,,not fitted,DNP\n")


# ------------------------------------------------------------------ unit tests
def test_numbers_and_text_specs():
    got = [S.si(x) for x in ("10 kΩ", "100nF", "0.1 µF", "4k7", "1/10W", "0.1W, 1/10W", "16 VDC", "4.7 MOhms", "1 mH")]
    assert all(S._close(a, b) for a, b in zip(got, [1e4, 1e-7, 1e-7, 4.7e3, 0.1, 0.1, 16, 4.7e6, 1e-3])), got
    assert S.pct("±1%") == 1 and S.dielectric("cog") == "C0G" and S.dielectric("NPO 50V") == "NP0"
    assert S.money("$0.10") == 0.1 and S.money("1.234,50 €") == 1234.5 and S.money("1,234.50") == 1234.5 \
        and S.money("0,10 €") == 0.1 and S.money("") is None
    assert S.lead_days("84 Days") == 84 and S.lead_days("18 Weeks") == 126 and S.lead_days(None) is None
    c = S.specs_from_text("Multilayer Ceramic Capacitors MLCC - SMD/SMT 0402 100nF 16V X7R 10%", "C")
    assert (round(c["value"], 12), c["voltage"], c["dielectric"], c["tolerance"], c["package"]) == (1e-7, 16, "X7R", 10, "0402"), c
    r = S.specs_from_text("Thick Film Resistors - SMD 10K OHM 1% 1/10W 0603", "R")
    assert (r["value"], r["tolerance"], r["power"], r["package"]) == (1e4, 1, 0.1, "0603"), r
    assert S.specs_from_text("RES 4K7 5% 0402", "R")["value"] == 4700
    assert S.specs_from_text("6.3V X5R", "C")["voltage"] == 6.3
    p = S.specs_from_pairs([("Package / Case", "0603 (1608 Metric)"), ("Case Code - mm", "0402"),
                            ("Temperature Coefficient", "±100ppm/°C"), ("Voltage - Rated", "25V"), ("Power (Watts)", "0.1W, 1/10W")])
    assert p == {"package": "0603", "voltage": 25, "power": 0.1}, p           # mm case code and resistor tempco ignored
    assert S.fmt_si(1e-7, "F") == "100nF" and S.fmt_si(1e4, "R") == "10k" and S.fmt_si(4.7e-6, "H") == "4.7uH"
    assert S.dist_key("Digi-Key Electronics") == "digikey" and S.dist_key("JLCPCB") == "lcsc" == S.dist_key("LCSC")


def test_buy_moq_multiple_and_price_breaks():
    o = S.Offer("x", "D", "s", stock=1000, breaks=[(1, 0.10), (10, 0.016), (100, 0.0049)])
    assert S.buy(o, 40) == {"order_qty": 100, "unit": 0.0049, "ext": 0.49, "covers": True}     # buying up is cheaper
    assert S.buy(o, 5) == {"order_qty": 10, "unit": 0.016, "ext": 0.16, "covers": True}       # 10 cost less than 5
    assert S.buy(o, 1)["order_qty"] == 1 and S.buy(o, 1)["ext"] == 0.1
    o2 = S.Offer("x", "D", "s", stock=50, moq=10, mult=4, breaks=[(10, 1.0), (1000, 0.5)])
    b = S.buy(o2, 3)
    assert (b["order_qty"], b["ext"], b["covers"]) == (12, 12.0, True), b                       # MOQ 10, multiple 4 -> 12
    assert S.buy(S.Offer("x", "D", breaks=[(20, 0.0011)]), 3)["order_qty"] == 20                # first break is a MOQ
    assert S.buy(S.Offer("x", "D", stock=5, breaks=[]), 3) is None
    assert S.buy(S.Offer("x", "D", stock=None, breaks=[(1, 1.0)]), 3)["covers"] is False         # unknown stock never covers
    offers = [S.Offer("a", "Cheap-but-short", "1", stock=5, breaks=[(1, 0.01)]),
              S.Offer("b", "Dear", "2", stock=500, breaks=[(1, 0.5)]),
              S.Offer("c", "Cheaper", "3", stock=500, breaks=[(1, 0.2)]),
              S.Offer("d", "None", "4", stock=0, breaks=[(1, 0.001)], lead_days=10),
              S.Offer("e", "Euro", "5", stock=900, breaks=[(1, 0.001)], currency="EUR")]
    assert [o.distributor for o, _ in S.rank_offers(offers, 10, "USD")] == ["Cheaper", "Dear", "Cheap-but-short", "None"]


def test_drop_in_rules():
    req = {"value": 1e-7, "unit": "F", "package": "0402", "voltage": 16, "dielectric": "X7R", "tolerance": 10}
    ok = lambda **kw: S.drop_in(req, {**req, **kw}, "C")[0]
    assert ok() == "drop_in" and ok(voltage=50) == "drop_in" and ok(dielectric="C0G") == "drop_in" and ok(tolerance=5) == "drop_in"
    assert ok(voltage=10) == ok(dielectric="X5R") == ok(tolerance=20) == ok(package="0603") == ok(value=1e-6) == "mismatch"
    v, why = S.drop_in(req, {k: v for k, v in req.items() if k != "voltage"}, "C")
    assert v == "unconfirmed" and "voltage" in why[0]
    r = {"value": 1e4, "unit": "R", "package": "0603", "tolerance": 1}                    # power unknown: not checked
    assert S.drop_in(r, {"value": 1e4, "unit": "R", "package": "0603", "tolerance": 0.5}, "R")[0] == "drop_in"


def test_nexar_queries_match_schema_and_parse():
    schema = _schema()
    if schema:
        from graphql import parse, validate
        from graphql.execution.values import get_variable_values
        for q in (S.NexarSourcing.Q_PART, S.NexarSourcing.Q_PART_ALT, S.NexarSourcing.Q_SEARCH):
            doc = parse(q)
            assert not validate(schema, doc), (q, validate(schema, doc))
            op = [d for d in doc.definitions if d.kind == "operation_definition"][0]
            got = get_variable_values(schema, op.variable_definitions, {"q": "X", "limit": 5, "country": "US", "currency": "EUR"})
            assert not isinstance(got, list), got
    else:
        print("skip schema half of test_nexar_queries_match_schema_and_parse (pip install graphql-core)")
    old = _env(**{**NO_CREDS, "NEXAR_CLIENT_ID": "cid", "NEXAR_CLIENT_SECRET": "sec"})
    try:
        http = Router(route)
        n = S.NexarSourcing(http)
        n.currency = "EUR"
        info = n.part({"mpn": "CH340C", "manufacturer": "WCH"}, alternates=True)
        q = json.loads([c for c in http.calls if "graphql" in c["url"]][0]["body"])
        assert "similarParts" in q["query"] and q["variables"]["currency"] == "EUR"
        assert [c["cache"] for c in http.calls if "identity" in c["url"]] == [False]           # token never cached
        assert (info.found, info.lifecycle, info.lead_days) == (True, "active", 56)
        o = info.offers[0]
        assert (o.distributor, o.sku, o.stock, o.breaks, o.lead_days, o.currency) == ("Digi-Key", "CH340C-ND", 0, [(1, 0.9), (100, 0.6)], 56, "EUR")
        assert [(s.mpn, s.lifecycle) for s in info.similar] == [("CH340G", "active"), ("CH340T", "nrnd")]
        r = n.part({"mpn": "0603WAF1002T5E", "manufacturer": ""})
        assert r.specs == {"value": 1e4, "unit": "R", "tolerance": 1, "power": 0.1, "package": "0603"}, r.specs
        assert "similarParts" not in json.loads(http.calls[-1]["body"])["query"]
        assert n.part({"mpn": "NOPE", "manufacturer": ""}).found is False
        both = S.nexar_info({"mpn": "X", "sellers": [{"company": {"name": "D"}, "offers": [{"sku": "s", "inventoryLevel": 1, "prices": [
            {"quantity": 1, "price": 0.9, "currency": "GBP", "convertedPrice": 1.2, "convertedCurrency": "USD"},
            {"quantity": 1, "price": 1.1, "currency": "USD", "convertedPrice": 1.1, "convertedCurrency": "USD"},
            {"quantity": 10, "price": 0.7, "currency": "GBP", "convertedPrice": 0.9, "convertedCurrency": "USD"}]}]}]}, "nexar", "USD")
        assert both.offers[0].breaks == [(1, 1.1), (10, 0.9)]                               # native price wins, else converted
        found, err = n.search("10k 0603 resistor")
        assert not err and len(found) == 5 and found[2].lifecycle == "nrnd"
        bad = Router(lambda m, u, b: (200, {"errors": [{"message": "quota exceeded"}]}) if "graphql" in u else (200, {"access_token": "t"}))
        assert S.NexarSourcing(bad).part({"mpn": "X", "manufacturer": ""}).error == "quota exceeded"
    finally:
        _env(**old)


DK_PRODUCT = {"SearchLocaleUsed": {"Site": "US", "Language": "en", "Currency": "USD"}, "Product": {
    "ManufacturerProductNumber": "GRM155R71C104KA88D", "Manufacturer": {"Id": 490, "Name": "Murata Electronics"},
    "Description": {"ProductDescription": "CAP CER 0.1UF 16V X7R 0402", "DetailedDescription": "0.1 µF ±10% 16V Ceramic Capacitor X7R 0402"},
    "ProductUrl": "https://www.digikey.com/en/products/detail/murata/GRM155R71C104KA88D/1", "QuantityAvailable": 2000000,
    "ProductStatus": {"Id": 0, "Status": "Active"}, "ManufacturerLeadWeeks": "16", "Discontinued": False, "EndOfLife": False,
    "Parameters": [{"ParameterId": 2049, "ParameterText": "Capacitance", "ValueText": "0.1 µF"},
                   {"ParameterId": 3, "ParameterText": "Tolerance", "ValueText": "±10%"},
                   {"ParameterId": 14, "ParameterText": "Voltage - Rated", "ValueText": "16V"},
                   {"ParameterId": 17, "ParameterText": "Temperature Coefficient", "ValueText": "X7R"},
                   {"ParameterId": 16, "ParameterText": "Package / Case", "ValueText": "0402 (1005 Metric)"}],
    "ProductVariations": [
        {"DigiKeyProductNumber": "490-3261-1-ND", "PackageType": {"Id": 2, "Name": "Cut Tape (CT)"},
         "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.1, "TotalPrice": 0.1}, {"BreakQuantity": 10, "UnitPrice": 0.025, "TotalPrice": 0.25}],
         "MinimumOrderQuantity": 1, "QuantityAvailableforPackageType": 150000},
        {"DigiKeyProductNumber": "490-3261-6-ND", "PackageType": {"Id": 243, "Name": "Digi-Reel®"},
         "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.1}], "MinimumOrderQuantity": 1, "QuantityAvailableforPackageType": 150000},
        {"DigiKeyProductNumber": "490-3261-2-ND", "PackageType": {"Id": 1, "Name": "Tape & Reel (TR)"},
         "StandardPricing": [{"BreakQuantity": 10000, "UnitPrice": 0.0021}], "MinimumOrderQuantity": 10000,
         "QuantityAvailableforPackageType": 1850000}]}}


def test_digikey_parse_search_substitutes():
    old = _env(**{**NO_CREDS, "DIGIKEY_CLIENT_ID": "id", "DIGIKEY_CLIENT_SECRET": "s"})
    try:
        def dk(m, u, b):
            if "oauth2/token" in u: return 200, {"access_token": "t"}
            if u.endswith("/productdetails"): return 200, DK_PRODUCT
            if u.endswith("/substitutions"): return 200, {"ProductSubstitutesCount": 1, "ProductSubstitutes": [
                {"SubstituteType": "Similar", "ManufacturerProductNumber": "CH9102F", "Manufacturer": {"Name": "WCH"},
                 "QuantityAvailable": 100, "ProductUrl": "u", "Description": "USB UART"}]}
            if u.endswith("/keyword"): return 200, {"ProductsCount": 1, "Products": [DK_PRODUCT["Product"]]}
            return 0, "no route"
        http = Router(dk)
        p = S.DigiKeySourcing(http)
        p.currency = "USD"
        info = p.part({"mpn": "GRM155R71C104KA88D", "manufacturer": ""})
        assert (info.lifecycle, info.lead_days, info.manufacturer) == ("active", 112, "Murata Electronics")
        assert [o.sku for o in info.offers] == ["490-3261-1-ND", "490-3261-2-ND"]               # Digi-Reel skipped
        assert info.offers[1].moq == 10000 and info.offers[0].stock == 150000
        assert S._close(info.specs.pop("value"), 1e-7) and info.specs == {"unit": "F", "tolerance": 10, "voltage": 16, "dielectric": "X7R", "package": "0402"}, info.specs
        h = http.calls[-1]["headers"]
        assert h["X-DIGIKEY-Client-Id"] == "id" and h["X-DIGIKEY-Locale-Currency"] == "USD" and h["Authorization"] == "Bearer t"
        found, err = p.search("100nF 0402 capacitor X7R")
        assert not err and found[0].mpn == "GRM155R71C104KA88D"
        body = json.loads([c for c in http.calls if c["url"].endswith("/keyword")][0]["body"])
        assert body == {"Keywords": "100nF 0402 capacitor X7R", "Limit": 20, "Offset": 0}
        subs, err = p.substitutes({"mpn": "CH340C"})
        assert [(s.mpn, s.manufacturer, s.note) for s in subs] == [("CH9102F", "WCH", "DigiKey substitute: Similar")]
        assert S.DigiKeySourcing(Router(lambda m, u, b: (200, {"access_token": "t"}) if "oauth2" in u else (404, "{}"))).part(
            {"mpn": "X", "manufacturer": ""}).found is False
    finally:
        _env(**old)


MOUSER_PART = {"ManufacturerPartNumber": "RC0603FR-0710KL", "Manufacturer": "YAGEO", "MouserPartNumber": "603-RC0603FR-0710KL",
               "Description": "Thick Film Resistors - SMD 10K OHM 1% 1/10W 0603", "AvailabilityInStock": "1234567",
               "Availability": "1234567 In Stock", "LifecycleStatus": None, "Min": "1", "Mult": "1", "LeadTime": "84 Days",
               "SuggestedReplacement": "", "ProductDetailUrl": "https://www.mouser.com/ProductDetail/603-RC0603FR-0710KL",
               "ProductAttributes": [{"AttributeName": "Packaging", "AttributeValue": "Cut Tape"},
                                     {"AttributeName": "Packaging", "AttributeValue": "Reel"}],
               "PriceBreaks": [{"Quantity": 1, "Price": "$0.10", "Currency": "USD"}, {"Quantity": 100, "Price": "$0.011", "Currency": "USD"},
                               {"Quantity": 5000, "Price": "$0.002", "Currency": "USD"}]}


class FakeRequests:
    """Stands in for requests.Session inside a real CachedHTTP, to check what lands on disk."""
    def __init__(self, payload):
        self.payload, self.urls, self.headers = payload, [], {}

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.urls.append(url)
        r = type("R", (), {})()
        r.status_code, r.text, r.ok = 200, json.dumps(self.payload), True
        return r


def test_mouser_parse_and_cache_redacts_key():
    old = _env(**{**NO_CREDS, "MOUSER_API_KEY": "SECRETKEY123"})
    try:
        with tempfile.TemporaryDirectory() as d:
            http = CachedHTTP(d, min_interval=0)
            http.s = FakeRequests({"Errors": [], "SearchResults": {"NumberOfResult": 1, "Parts": [MOUSER_PART]}})
            m = S.MouserSourcing(http)
            info = m.part({"mpn": "RC0603FR-0710KL", "manufacturer": "Yageo"})
            o = info.offers[0]
            assert (info.lifecycle, info.lead_days, o.sku, o.stock, o.breaks, o.currency) == \
                ("active", 84, "603-RC0603FR-0710KL", 1234567, [(1, 0.1), (100, 0.011), (5000, 0.002)], "USD"), info
            assert "apiKey=SECRETKEY123" in http.s.urls[0]
            files = list(Path(d).glob("*.json"))
            assert files and not any("SECRETKEY123" in f.read_text() for f in files)          # cached, key redacted
            assert "apiKey=REDACTED" in json.loads(files[0].read_text())["url"]
            m.part({"mpn": "RC0603FR-0710KL", "manufacturer": ""})
            assert len(http.s.urls) == 1 and http.log[-1][3] is True                        # second call from the cache
            found, err = m.search("10k 0603 resistor")
            spec = S.specs_from_text(found[0].description, "R")
            assert not err and spec["value"] == 1e4 and spec["power"] == 0.1
            assert json.loads(Path(d).joinpath(files[0].name).read_text())["status"] == 200
        err = S.MouserSourcing(Router(lambda m, u, b: (200, {"Errors": [{"Code": "Invalid", "Message": "Invalid key"}]}))).part(
            {"mpn": "X", "manufacturer": ""})
        assert err.error == "Invalid key"
        rep = S.MouserSourcing(Router(lambda m, u, b: (200, {"SearchResults": {"Parts": [
            {**MOUSER_PART, "ManufacturerPartNumber": "OLD1", "LifecycleStatus": "Obsolete", "SuggestedReplacement": "NEW1"}]}})))
        r = rep.part({"mpn": "OLD1", "manufacturer": ""})
        assert r.lifecycle == "obsolete" and [s.mpn for s in r.similar] == ["NEW1"]
        down = S.MouserSourcing(Router(lambda m, u, b: (0, "ConnectionError: url=/api?apiKey=SECRETKEY123")))
        assert "SECRETKEY123" not in down.part({"mpn": "X", "manufacturer": ""}).error
    finally:
        _env(**old)


def test_jlc_parse():
    j = S.JLCSourcing(Router(route))
    info = j.part({"mpn": "0603WAF1002T5E", "manufacturer": "", "lcsc": "C25804"})
    o = info.offers[0]
    assert (o.distributor, o.sku, o.stock, o.breaks, o.currency) == ("LCSC", "C25804", 1000, [(20, 0.0011), (200, 0.0008)], "USD")
    assert json.loads(j.http.calls[0]["body"])["keyword"] == "C25804"
    assert S.specs_from_text(info.description, "R")["value"] == 1e4
    assert j.part({"mpn": "", "manufacturer": "", "lcsc": ""}).error


def test_merge_dedupes_direct_and_nexar_offers():
    a = S.PartInfo("nexar", True, "X", "M", lifecycle="active", offers=[S.Offer("nexar", "Digi-Key Electronics", "1-ND", 5),
                                                                        S.Offer("nexar", "Arrow", "A1", 9)])
    b = S.PartInfo("digikey", True, "X", lifecycle="nrnd", specs={"package": "0603"},
                   offers=[S.Offer("digikey", "Digi-Key", "1-ND", 7)])
    m = S.merge_infos([a, b, S.PartInfo("mouser", False)])
    assert m.lifecycle == "nrnd" and m.specs == {"package": "0603"} and m.manufacturer == "M"
    assert sorted((o.source, o.distributor, o.stock) for o in m.offers) == [("digikey", "Digi-Key", 7), ("nexar", "Arrow", 9)]
    assert S.merge_infos([S.PartInfo("x", False)]) is None


# ------------------------------------------------------------------ end to end
def _run(alternates=True, qty=10):
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "bom.csv"
        f.write_text(BOM)
        lines = T.read_bom(f)[0]
    http = Router(route)
    provs = [c(http) for c in S.PROVIDERS]
    rows, unconf = S.source_bom(lines, provs, qty, "USD", alternates)
    return lines, rows, unconf, http


def test_source_bom_end_to_end():
    old = _env(**{**NO_CREDS, "NEXAR_SUPPLY_TOKEN": "t"})
    try:
        lines, rows, unconf, http = _run()
        assert unconf == ["digikey", "mouser"], unconf
        by = {r["mpn"]: r for r in rows}
        r = by["0603WAF1002T5E"]
        assert (r["need"], r["chosen"]["distributor"], r["chosen"]["sku"], r["chosen"]["order_qty"], r["chosen"]["ext"]) == \
            (40, "LCSC", "C25804", 40, 0.044), r["chosen"]
        assert [o["distributor"] for o in r["offers"]] == ["LCSC", "Digi-Key", "Mouser"]      # Digi-Key buys up to 100 @ 0.0049
        assert r["offers"][1]["order_qty"] == 100 and r["offers"][2]["order_qty"] == 50
        assert r["flags"] == [] and r["distributors_in_stock"] == 3 and r["lifecycle"] == "active"
        assert r["suppliers"] == [("LCSC", "C25804"), ("Digi-Key", "0603WAF1002T5ECT-ND"), ("Mouser", "652-0603WAF1002T5E")]
        alts = {a["mpn"]: a for a in r["alternates"]}
        assert set(alts) == {"RC0603FR-0710KL", "CR0603-FX-1002ELF"}, alts                    # 5% / 0402 / NRND dropped
        assert alts["RC0603FR-0710KL"]["verdict"] == "drop_in" and alts["CR0603-FX-1002ELF"]["verdict"] == "unconfirmed"
        assert r["alternates"][0]["mpn"] == "RC0603FR-0710KL" and "single_source" not in r["flags"]
        assert r["required"]["tolerance"] == 1 and r["required"]["power"] == 0.1

        c = by["CL05B104KO5NNNC"]
        assert c["need"] == 10 and c["chosen"]["distributor"] == "Mouser" and not c["chosen"]["covers"]
        assert set(c["flags"]) == {"low_stock", "single_distributor"}, c["flags"]
        assert [(a["mpn"], a["verdict"]) for a in c["alternates"]] == [("GRM155R71C104KA88D", "drop_in"),
                                                                       ("CL05B104KB54PNC", "drop_in")]   # 50V X7R is fine
        assert all(a["mpn"] != "GRM155R61A104KA01D" for a in c["alternates"])                   # X5R 10V is not a drop-in
        assert c["alternates"][0]["same_manufacturer"] is False                                  # other makers first

        u = by["CH340C"]
        assert "no_stock" in u["flags"] and "alternates_need_review" in u["flags"] and u["lead_days"] == 56
        assert [(a["mpn"], a["verdict"]) for a in u["alternates"]] == [("CH340G", "needs engineer review")]   # NRND CH340T dropped
        assert u["chosen"]["distributor"] == "Digi-Key" and u["chosen"]["stock"] == 0

        assert "not_found" in by["OBS123"]["flags"] and by["OBS123"]["chosen"] is None
        assert by["SM04B"]["flags"] == ["dnp"]
        nexar_calls = [json.loads(c["body"])["variables"]["q"] for c in http.calls if "graphql" in c["url"]]
        assert "SM04B" not in nexar_calls and "10k 0603 resistor" in nexar_calls and "100nF 0402 capacitor X7R" in nexar_calls

        roll = S.rollup(rows, 10, "USD")
        exp = round(0.044 + c["chosen"]["ext"] + u["chosen"]["ext"], 4)
        assert (roll["lines"], roll["priced"], roll["in_stock"], roll["total"], roll["complete"]) == (4, 3, 1, exp, False), roll
        assert roll["per_board"] == round(exp / 10, 4) and roll["longest_lead_days"] == 56
        assert roll["per_distributor"]["LCSC"]["lines"] == 1 and roll["per_distributor"]["Digi-Key"]["lines"] == 1

        _, rows2, _, _ = _run(alternates=False, qty=1)
        assert all(not r["alternates"] for r in rows2)
        assert {r["mpn"]: r["need"] for r in rows2}["0603WAF1002T5E"] == 4
        assert {r["mpn"]: r["flags"] for r in rows2}["0603WAF1002T5E"] == []
    finally:
        _env(**old)


def test_write_plan_fills_suppliers_without_breaking_push():
    old = _env(**{**NO_CREDS, "NEXAR_SUPPLY_TOKEN": "t"})
    try:
        lines, rows, _, _ = _run()
        with tempfile.TemporaryDirectory() as d:
            plan = Path(d) / "plan.json"
            plan.write_text(json.dumps(A.plan([ln for ln in lines if not ln.get("dnp")])))
            before = {i["name"]: i for i in json.loads(plan.read_text())}
            assert "Supplier 2" in before["RES-0603-10k"]["cleared"]
            changes = dict(S.write_plan(plan, rows, 10, "USD", now="T"))
            items = {i["name"]: i for i in json.loads(plan.read_text())}
            par = lambda n: {p["name"]: p["value"] for p in items[n]["parameters"]}
            r = par("RES-0603-10k")
            assert (r["Supplier 1"], r["Supplier Part Number 1"]) == ("LCSC", "C25804")             # BOM supplier kept
            assert (r["Supplier 2"], r["Supplier Part Number 2"]) == ("Digi-Key", "0603WAF1002T5ECT-ND")
            assert (r["Alternate Manufacturer 1"], r["Alternate Manufacturer Part Number 1"]) == ("YAGEO", "RC0603FR-0710KL")
            assert "Alternate Manufacturer Part Number 2" not in r                                   # unconfirmed never written
            assert not {"Supplier 2", "Supplier Part Number 2"} & set(items["RES-0603-10k"]["cleared"])
            assert changes["RES-0603-10k"] == sorted(["Supplier 2", "Supplier Part Number 2", "Alternate Manufacturer 1",
                                                      "Alternate Manufacturer Part Number 1"])
            ch = par("CH340C")
            assert ch["Supplier 2"] == "Digi-Key" and not any(k.startswith("Alternate") for k in ch)  # ICs never substituted
            src = items["CH340C"]["sourcing"]
            assert src["alternates"][0]["verdict"] == "needs engineer review" and "no_stock" in src["flags"] and src["checked"] == "T"
            assert items["RES-0603-10k"]["sourcing"]["chosen"]["sku"] == "C25804"
            # what push would send to an existing workspace component: the new fields, and hand-added ones survive
            existing = {"details": {"parameters": [{"name": "Supplier 2", "value": ""}, {"name": "Shelf", "value": "B3"}]}}
            sent = {p["name"]: p["value"] for p in A.merged_params(existing, items["RES-0603-10k"])}
            assert sent["Supplier 2"] == "Digi-Key" and sent["Shelf"] == "B3" and sent["Alternate Manufacturer Part Number 1"] == "RC0603FR-0710KL"
            # idempotent: a second run fills nothing new and does not duplicate parameters
            again = dict(S.write_plan(plan, rows, 10, "USD", now="T2"))
            assert again["RES-0603-10k"] == [] and len(par("RES-0603-10k")) == len(r)
            assert A.push(list(json.loads(plan.read_text())), None, "https://ws", dry_run=True)        # still a valid plan
    finally:
        _env(**old)


def _seed(cache, kw, item):
    http = CachedHTTP(cache, offline=True)
    body = json.dumps({"keyword": kw, "currentPage": 1, "pageSize": 10})
    payload = {"data": {"componentPageInfo": {"list": [item] if item else []}}}
    (Path(cache) / f"{http._key('POST', S.JLCSourcing.URL, body)}.json").write_text(
        json.dumps({"url": S.JLCSourcing.URL, "status": 200, "text": json.dumps(payload)}))


def test_cli_offline_reports_and_exit_codes():
    old = _env(**NO_CREDS)
    try:
        with tempfile.TemporaryDirectory() as d:
            bom = Path(d) / "bom.csv"
            bom.write_text("Designator,Qty,Value,MPN,LCSC\nR1,2,10k,0603WAF1002T5E,C25804\nC1,1,100nF,CL05B104KO5NNNC,C1525\n")
            args = [str(bom), "--qty", "25", "--offline", "--cache", f"{d}/c", "-o", f"{d}/r"]
            assert S.main(args) == 3                                                     # nothing cached, nothing answered
            assert all(r["flags"] == "no_answer" for r in csv.DictReader(open(f"{d}/r.csv")))
            assert S.main(args + ["--alternates"]) == 3                                  # no false single_source either
            assert all(r["flags"] == "no_answer" for r in csv.DictReader(open(f"{d}/r.csv")))
            _seed(f"{d}/c", "C25804", JLC["C25804"])
            _seed(f"{d}/c", "C1525", {**JLC["C1525"], "stockCount": 5000})
            assert cli.main(["sourcing"] + args) == 0
            rows = {r["mpn"]: r for r in csv.DictReader(open(f"{d}/r.csv"))}
            r = rows["0603WAF1002T5E"]
            assert (r["need"], r["order_qty"], r["unit_price"], r["ext_price"], r["Supplier 1"], r["Supplier Part Number 1"]) == \
                ("50", "50", "0.0011", "0.055", "LCSC", "C25804"), r
            assert "single_distributor" in r["flags"].split() and rows["CL05B104KO5NNNC"]["ext_price"] == "0.075"
            md = Path(f"{d}/r.md").read_text()
            assert "0.13 USD for 25 boards" in md and "0.0052 USD per board" in md and "## Sources" in md and "nexar | no" in md
            _seed(f"{d}/c", "C1525", JLC["C1525"])                                       # out of stock -> exit 1
            assert S.main(args) == 1
            assert "no_stock" in {r["mpn"]: r for r in csv.DictReader(open(f"{d}/r.csv"))}["CL05B104KO5NNNC"]["flags"]
            assert S.main([str(Path(d) / "missing.csv")]) == 2
            assert S.main(args + ["--write-plan", str(Path(d) / "nope.json")]) == 2
            plan = Path(d) / "plan.json"
            plan.write_text(json.dumps(A.plan(T.read_bom(bom)[0])))
            assert S.main(args + ["--write-plan", str(plan), "--alternates"]) == 1
            assert all("sourcing" in i for i in json.loads(plan.read_text()))
            assert "## Plan write-back" in Path(f"{d}/r.md").read_text()
    finally:
        _env(**old)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
