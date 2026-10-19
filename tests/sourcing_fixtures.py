"""Fake Nexar / JLC responses and a sample BOM, shared by the upkeep and hook tests (moved out of the old
sourcing tests when price and stock were cut on 2026-10-19)."""
import json

from ecadlib.httpcache import Response

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

