"""Why parts of the first 1000-part real BOM (2026-11-16) got no package from the part search, beyond the shared part
numbers #258 fixes:
  * Mouser writes its category before NXP / Nexperia's 'TYPE/CASE' text ('Bipolar Transistors - BJT
    BC847C/SOT23/TO-236AB'), so the case was never read: BC847C,215 and MMBT3904,215 stayed held with "no case text
    confirms it" though Mouser named SOT-23.
  * DigiKey's per-minute limit (HTTP 429, 'Retry in 30 seconds') was read like the daily one: the part was dropped.
    A short wait is now taken once; the daily limit (hours) ends DigiKey's use for the rest of the run, so the other
    lines are not each sent to be refused.
Run:
    python -m tests.test_digikey_misses"""
import json, os

from ecadlib import part_lookup as S
from ecadlib import bom_triage as BT
from ecadlib.httpcache import Response


# ---------------------------------------------------------------- Mouser's category before the NXP 'TYPE/CASE' text
def test_slash_case_after_mouser_category():
    assert BT.slash_case("Bipolar Transistors - BJT BC847C/SOT23/TO-236AB", "BC847C,215") == "SOT23/TO-236AB"
    assert BT.slash_case("Schottky Diodes & Rectifiers PMEG4010CEJ/SOD323F/SOD323F", "PMEG4010CEJ,115") \
        == "SOD323F/SOD323F"
    assert BT.slash_case("Digital Transistors PUMH11/SOT363/SC-88", "PUMH11,115") == "SOT363/SC-88"
    assert BT.slash_case("Counter Shift Registers 74HC595D/SOT109/SO16", "74HC595D,118") == "SOT109/SO16"
    assert BT.slash_case("Inverters 74LVC1G04GW/SOT353/UMT5", "74LVC1G04GW,125") == "SOT353/UMT5"


def test_slash_case_stops_at_an_empty_field():
    assert BT.slash_case("ARM Microcontrollers - MCU LPC1768FBD100/LQFP100///STANDARD MARKING * TRAY DR",
                         "LPC1768FBD100") == "LQFP100"


def test_slash_case_needs_this_type_and_a_package_name():
    assert BT.slash_case("Bipolar Transistors - BJT BC847B/SOT23/TO-236AB", "BC847C,215") == ""   # another type
    assert BT.slash_case("Op Amps Single-Supply A 595-OPA2340UA/2K5", "OPA2340UA") == ""   # a reel code, not a case
    assert BT.slash_case("Linear Regulators LM1117MPX-3.3/NOPB", "LM1117MPX-3.3") == ""      # lead-free, not a case
    assert BT.slash_case("Gate Drivers 4A/8A Sgl Ch", "UCC27511DBVR") == ""
    assert BT.slash_case("Multilayer Ceramic Capacitors MLCC - SMD/SMT 2.2UF", "GRM155R61A225KE95D") == ""
    assert BT.slash_case("XYZ123/TR7", "XYZ123") == ""


def test_slash_case_still_reads_the_bare_form():
    assert BT.slash_case("BC847C/SOT23/TO-236AB", "BC847C,215") == "SOT23/TO-236AB"
    assert BT.slash_case("ESD Protection Diodes / TVS Diodes", "SMBJ5.0A") == ""
    assert BT.slash_case("", "X") == ""


def test_the_slash_case_names_the_land():
    from ecadlib import librarian as L
    assert L.case_land(BT.slash_case("Bipolar Transistors - BJT MMBT3904/SOT23/TO-236AB", "MMBT3904,215")) == "SOT-23"
    assert L.case_land(BT.slash_case("Digital Transistors PUMH11/SOT363/SC-88", "PUMH11,115")) == "SOT-363_SC-70-6"


# ---------------------------------------------------------------- DigiKey's rate limits
def _prod(mpn, maker, case):
    return {"ManufacturerProductNumber": mpn, "Manufacturer": {"Name": maker}, "Description": {"ProductDescription": "x"},
            "ProductStatus": {"Status": "Active"}, "Parameters": [{"ParameterText": "Package / Case", "ValueText": case}],
            "ProductVariations": []}


class HTTP:
    """productdetails answers with `answers` in turn (then the product); the keyword search is never needed here."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def request(self, method, url, body=None, headers=None, cache=True):
        if "oauth2/token" in url:
            return Response(200, json.dumps({"access_token": "t", "expires_in": 599}))
        self.calls.append(url)
        if self.answers:
            return self.answers.pop(0)
        return Response(200, json.dumps({"Product": _prod("LM358DR", "Texas Instruments", "8-SOIC")}))

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, body, **kw): return self.request("POST", url, body=body, **kw)


def _client(http):
    return S.DigiKeySourcing(http)


def _ask(cl, mpn="LM358DR", maker="Texas Instruments"):
    old = {k: os.environ.get(k) for k in ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")}
    os.environ.update(DIGIKEY_CLIENT_ID="id", DIGIKEY_CLIENT_SECRET="s")
    try:
        return cl.part({"mpn": mpn, "manufacturer": maker, "lcsc": ""})
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


BURST = Response(429, json.dumps({"status": 429, "detail": "Burst Ratelimit exceeded. Retry in 30 seconds."}))
DAILY = Response(429, json.dumps({"status": 429, "detail": "Daily Ratelimit exceeded. Retry in 36000 seconds."}))


def test_per_minute_limit_waits_and_asks_again():
    waits = []
    cl = _client(HTTP([BURST]))
    cl.sleep = waits.append
    r = _ask(cl)
    assert r.found and not r.error, (r.found, r.error)
    assert waits and 0 < waits[0] <= 61, waits


def test_per_minute_limit_twice_is_an_error_not_a_miss():
    from ecadlib import providers as P
    cl = _client(HTTP([BURST, BURST]))
    cl.sleep = lambda s: None
    r = _ask(cl)
    assert r.found is None and r.error and P.account_limit(r.error), (r.found, r.error)


def test_daily_limit_is_not_waited_for_and_ends_digikey_for_the_run():
    from ecadlib import providers as P
    waits, http = [], HTTP([DAILY])
    cl = _client(http)
    cl.sleep = waits.append
    r = _ask(cl)
    assert r.found is None and P.account_limit(r.error) and not waits, (r.error, waits)
    n = len(http.calls)
    r2 = _ask(cl, "NE555DR")
    assert r2.found is None and P.account_limit(r2.error) and "not asked again" in r2.error, r2.error
    assert len(http.calls) == n, "DigiKey was asked again after its daily limit"


def test_daily_limit_ends_when_digikey_said_it_resets():
    http = HTTP([DAILY])
    cl = _client(http)
    cl.sleep = lambda s: None
    _ask(cl)
    cl._spent_until = 0.0                    # the reset time has passed (an app left running overnight)
    r = _ask(cl)
    assert r.found and not r.error, (r.found, r.error)


def test_retry_after_header_decides_too():
    cl = _client(HTTP([]))
    burst = Response(429, "")
    burst.headers = {"retry-after": "12"}
    daily = Response(429, "")
    daily.headers = {"retry-after": "40000"}
    assert cl._limit_wait(burst) == 12
    assert cl._limit_wait(daily) is None
    assert cl._limit_wait(Response(429, "Too many requests")) is None      # nothing says how long: not waited for


def test_a_found_part_needs_no_wait():
    waits = []
    cl = _client(HTTP([]))
    cl.sleep = waits.append
    r = _ask(cl)
    assert r.found and not waits


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
