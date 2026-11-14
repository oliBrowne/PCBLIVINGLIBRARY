"""DigiKey / Mouser / Nexar part-lookup keys (ecadlib.lookup_keys): the setup page's "Part lookups" card, the free test
search behind its Check button and `ecadlib keys check`, and the guide being the same on the page, in docs/ and on the
site. Offline: the test search uses a fake HTTP client. Run:  python3 -m tests.test_lookup_keys"""
import contextlib, io, json, os, sys
from pathlib import Path

from ecadlib import easy_setup as ES, lookup_keys as LK, userconfig as UC
from ecadlib.httpcache import Response
from tests.fixtures_lite import clean_all, scratch

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET", "MOUSER_API_KEY", "NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET",
         "NEXAR_SUPPLY_TOKEN")


@contextlib.contextmanager
def _home():
    d = scratch("ecadlib-lookupkeys-")
    keep = {k: os.environ.get(k) for k in ("ECADLIB_HOME",) + NAMES}
    os.environ["ECADLIB_HOME"] = str(d / "home")
    for k in NAMES:
        os.environ.pop(k, None)
    try:
        yield d
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeHTTP:
    """Answers by URL substring; records every request (so a test can see a key never went anywhere else)."""
    def __init__(self, answers):
        self.answers, self.seen = answers, []

    def request(self, method, url, body=None, headers=None, **kw):
        self.seen.append((method, url, body, headers))
        for part, (status, data) in self.answers.items():
            if part in url:
                return Response(status, json.dumps(data), error=None if 200 <= status < 300 or status == 404
                                else f"HTTP {status}")
        return Response(0, "", error="ConnectionError: no route")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, body, **kw):
        return self.request("POST", url, body=body, **kw)


DK_TOKEN = {"access_token": "tok-dk", "expires_in": 599}
DK_PART = {"Product": {"ManufacturerProductNumber": "NE555DR", "Manufacturer": {"Name": "Texas Instruments"},
                       "Description": {"ProductDescription": "IC OSC SINGLE TIMER 100KHZ 8SOIC"}}}
MOUSER_PART = {"Errors": [], "SearchResults": {"NumberOfResult": 1, "Parts": [
    {"ManufacturerPartNumber": "NE555DR", "Manufacturer": "Texas Instruments", "Description": "Timers 555 timer"}]}}


def test_the_three_lookups_and_their_key_names():
    assert [g["id"] for g in LK.GUIDES] == ["digikey", "mouser", "nexar"]
    by = {g["id"]: g for g in LK.GUIDES}
    assert [f[0] for f in by["digikey"]["fields"]] == ["DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET"]
    assert [f[0] for f in by["mouser"]["fields"]] == ["MOUSER_API_KEY"]
    assert [f[0] for f in by["nexar"]["fields"]] == ["NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET"]
    from ecadlib import providers as P                   # the names the lookups really read
    for g in LK.GUIDES:
        cls = {"digikey": P.DigiKey, "mouser": P.Mouser, "nexar": P.Nexar}[g["id"]]
        assert tuple(f[0] for f in g["fields"]) == cls.env
        assert g["steps"] and all(isinstance(s, str) and s for s in g["steps"])


def test_setup_page_stores_lookup_keys_and_never_shows_a_value():
    with _home() as d:
        ES.store_key({"name": "DIGIKEY_CLIENT_ID", "value": "dk-id-secretish"})
        ES.store_key({"name": "MOUSER_API_KEY", "value": "mouser-zzz-secret"})
        st = ES.state(d)
        blob = json.dumps(st)
        assert "dk-id-secretish" not in blob and "mouser-zzz-secret" not in blob
        by = {g["id"]: g for g in st["lookups"]}
        assert by["mouser"]["ready"] and not by["digikey"]["ready"]          # DigiKey still needs its secret
        assert [f["stored"] for f in by["digikey"]["fields"]] == [True, False]
        assert ES.forget_key({"name": "MOUSER_API_KEY"})["removed"]
        assert not {g["id"]: g for g in ES.state(d)["lookups"]}["mouser"]["ready"]


def test_check_sends_nothing_until_the_keys_are_saved():
    with _home():
        http = FakeHTTP({})
        r = LK.check("digikey", http=http)
        assert not r["ok"] and "DIGIKEY_CLIENT_ID" in r["message"] and not http.seen
        try:
            LK.check("octopart", http=http)
            raise AssertionError
        except ValueError:
            pass


def test_digikey_check_found_refused_and_not_subscribed():
    with _home():
        UC.set_key("DIGIKEY_CLIENT_ID", "id-123")
        UC.set_key("DIGIKEY_CLIENT_SECRET", "sec-456")
        http = FakeHTTP({"oauth2/token": (200, DK_TOKEN), "productdetails": (200, DK_PART)})
        r = LK.check("digikey", http=http)
        assert r["ok"] and "NE555DR" in r["message"] and "Texas Instruments" in r["message"]
        assert all(u.startswith("https://api.digikey.com/") for _, u, _, _ in http.seen)
        r = LK.check("digikey", http=FakeHTTP({"oauth2/token": (401, {"error": "invalid_client"})}))
        assert not r["ok"] and "refused" in r["message"] and "sec-456" not in r["message"]
        r = LK.check("digikey", http=FakeHTTP({"oauth2/token": (200, DK_TOKEN), "productdetails": (403, {})}))
        assert not r["ok"] and "Product Information V4" in r["message"]


def test_mouser_check_reads_errors_in_a_200_answer():
    with _home():
        UC.set_key("MOUSER_API_KEY", "m-key-789")
        http = FakeHTTP({"api.mouser.com": (200, MOUSER_PART)})
        r = LK.check("mouser", http=http)
        assert r["ok"] and "NE555DR" in r["message"]
        bad = FakeHTTP({"api.mouser.com": (200, {"Errors": [{"Code": "Invalid", "Message": "Invalid unique identifier."}]})})
        r = LK.check("mouser", http=bad)
        assert not r["ok"] and "Invalid unique identifier" in r["message"] and "m-key-789" not in r["message"]


def test_nexar_check_explains_a_missing_supply_scope_or_plan():
    with _home():
        UC.set_key("NEXAR_CLIENT_ID", "nx-id")
        UC.set_key("NEXAR_CLIENT_SECRET", "nx-sec")
        r = LK.check("nexar", http=FakeHTTP({"connect/token": (400, {"error": "invalid_scope"})}))
        assert not r["ok"] and "Supply" in r["message"]
        quota = {"errors": [{"message": "Part limit exceeded"}]}
        r = LK.check("nexar", http=FakeHTTP({"connect/token": (200, {"access_token": "t"}), "graphql": (200, quota)}))
        assert not r["ok"] and "Part limit exceeded" in r["message"] and "plan" in r["message"]
        hit = {"data": {"supSearchMpn": {"hits": 1, "results": [{"part": {
            "mpn": "NE555DR", "manufacturer": {"name": "Texas Instruments"}, "sellers": [], "specs": []}}]}}}
        http = FakeHTTP({"connect/token": (200, {"access_token": "t"}), "graphql": (200, hit)})
        r = LK.check("nexar", http=http)
        assert r["ok"] and "Texas Instruments" in r["message"]
        ask = json.loads(next(b for _, u, b, _ in http.seen if "graphql" in u))
        assert ask["variables"]["limit"] == 1                 # Nexar counts every part it returns: the test asks for one
        assert "scope=supply.domain" in next(b for _, u, b, _ in http.seen if "connect/token" in u)


def test_an_outage_is_not_called_a_wrong_key():
    with _home():
        UC.set_key("DIGIKEY_CLIENT_ID", "id")
        UC.set_key("DIGIKEY_CLIENT_SECRET", "sec")
        r = LK.check("digikey", http=FakeHTTP({"oauth2/token": (503, {})}))
        assert not r["ok"] and "refused" not in r["message"] and "503" in r["message"]


def test_nexar_supply_token_alone_counts_as_set_up():
    with _home() as d:
        UC.set_key("NEXAR_SUPPLY_TOKEN", "tok")
        nx = {g["id"]: g for g in ES.state(d)["lookups"]}["nexar"]
        assert nx["ready"] and nx["supply_token"]
        quota = {"errors": [{"message": "Part limit exceeded"}]}
        r = LK.check("nexar", http=FakeHTTP({"graphql": (200, quota)}))
        assert not r["ok"] and "NEXAR_SUPPLY_TOKEN" in r["message"]          # says which credential was tested


def test_keys_check_command():
    with _home():
        UC.set_key("MOUSER_API_KEY", "m-key")
        out = []
        assert UC.main(["check", "mouser"], out=out.append, http=FakeHTTP({"api.mouser.com": (200, MOUSER_PART)})) == 0
        assert any("NE555DR" in line for line in out)
        out = []
        assert UC.main(["check", "digikey"], out=out.append, http=FakeHTTP({})) == 1     # not saved: says so, fails
        assert any("DIGIKEY_CLIENT_ID" in line for line in out)


def test_guide_is_the_same_in_docs_on_the_site_and_on_the_setup_page():
    """One guide, three places: every sign-up address and key name the setup page shows is in docs/ and on the site,
    and the setup page draws its card from the server's guide (no second copy to drift)."""
    doc = (ROOT / "docs" / "part-lookup-keys.md").read_text(encoding="utf-8")
    site = (ROOT / "site" / "keys.html").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for g in LK.GUIDES:
        for text in [g["url"]] + [f[0] for f in g["fields"]]:
            for name, where in (("docs", doc), ("site", site), ("README", readme)):
                assert text in where, (name, g["id"], text)
    for text in ("ecadlib keys check", "http://localhost:3000/login", "optional"):
        assert text in doc and text in site, text
    page = (ROOT / "ecadlib" / "ui" / "static" / "setup.html").read_text(encoding="utf-8")
    assert 'id="lookups"' in page and "S.lookups" in page and "/api/setup/lookup/check" in page
    assert "/api/setup/lookup/check" in ES.POST_ROUTES
    assert 'href="/keys"' in (ROOT / "site" / "index.html").read_text(encoding="utf-8")    # cleanUrls: keys.html
    assert "part-lookup-keys.md" in (ROOT / "docs" / "api-keys.md").read_text(encoding="utf-8")
    assert "part-lookup-keys.md" in (ROOT / "README.md").read_text(encoding="utf-8")


if __name__ == "__main__":
    from tests.run_all import main
    sys.exit(main(["test_lookup_keys"]))


clean_all(globals())
