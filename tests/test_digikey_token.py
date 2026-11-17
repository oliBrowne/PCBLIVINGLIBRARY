"""DigiKey's OAuth token lasts about ten minutes: a long BOM run must renew it, not get HTTP 401 for every later part
(1000-part run, 2026-11-16: every part after the 287th came back 'digikey: HTTP 401'). Run:
    python -m tests.test_digikey_token"""
import json, os

from ecadlib import lifecycle as L, part_lookup as S
from ecadlib.httpcache import Response


class TokenHTTP:
    """Hands out tokens t1, t2, ...; a product request with a token in `dead` gets 401."""
    def __init__(self, expires_in=599):
        self.n, self.dead, self.calls, self.log, self.expires_in = 0, set(), [], [], expires_in

    def request(self, method, url, body=None, headers=None, cache=True):
        self.calls.append((method, url, (headers or {}).get("Authorization")))
        if "oauth2/token" in url:
            self.n += 1
            return Response(200, json.dumps({"access_token": f"t{self.n}", "expires_in": self.expires_in}))
        if (headers or {}).get("Authorization", "").split()[-1] in self.dead:
            return Response(401, "Bearer token expired")
        return Response(200, json.dumps({"Product": {"ManufacturerProductNumber": "LM339N",
                                                     "ProductStatus": {"Status": "Active"}, "QuantityAvailable": 5}}))

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, body, **kw): return self.request("POST", url, body=body, **kw)


def _env():
    old = {k: os.environ.get(k) for k in ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")}
    os.environ.update(DIGIKEY_CLIENT_ID="id", DIGIKEY_CLIENT_SECRET="s")
    return old


def _restore(old):
    for k, v in old.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_token_renewed_after_401():
    old = _env()
    try:
        for make, ask in ((L.DigiKeyLifecycle, lambda c: c.check({"mpn": "LM339N"})),
                          (S.DigiKeySourcing, lambda c: c.part({"mpn": "LM339N", "manufacturer": "", "lcsc": ""}))):
            http = TokenHTTP()
            c = make(http)
            assert ask(c).found
            http.dead.add("t1")                       # DigiKey let the first token run out
            r = ask(c)
            assert r.found and not r.error, (make.__name__, r.error)
            assert [a for _, u, a in http.calls if "productdetails" in u][-1] == "Bearer t2", http.calls
            assert sum("oauth2/token" in u for _, u, _ in http.calls) == 2
    finally:
        _restore(old)


def test_token_renewed_before_expiry():
    old = _env()
    try:
        http = TokenHTTP(expires_in=0)                # already due: the next request asks for a new one first
        c = L.DigiKeyLifecycle(http)
        c.check({"mpn": "LM339N"})
        c.check({"mpn": "LM339N"})
        assert sum("oauth2/token" in u for _, u, _ in http.calls) == 2, http.calls
    finally:
        _restore(old)


def test_401_twice_is_an_error_not_a_loop():
    old = _env()
    try:
        http = TokenHTTP()
        http.dead.update({"t1", "t2", "t3"})
        r = L.DigiKeyLifecycle(http).check({"mpn": "LM339N"})
        assert r.error and "401" in r.error, r
        assert sum("oauth2/token" in u for _, u, _ in http.calls) == 2
    finally:
        _restore(old)


def test_an_odd_expires_in_keeps_the_token():
    old = _env()
    try:
        for odd in ("soon", None, [], "nan", "inf", -5):
            http = TokenHTTP(expires_in=odd)
            c = L.DigiKeyLifecycle(http)
            r = c.check({"mpn": "LM339N"})
            assert r.found and not r.error, (odd, r.error)
            c.check({"mpn": "LM339N"})
            assert sum("oauth2/token" in u for _, u, _ in http.calls) == 1, (odd, http.calls)
    finally:
        _restore(old)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
