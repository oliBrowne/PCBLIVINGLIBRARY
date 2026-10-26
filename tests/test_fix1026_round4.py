"""2026-10-26 re-review of the round-3 fixes on PR #196 (reviews/2026-10-26_review_196/fixes4.md, R-FX4-1..7).
Plain functions: python -m tests.run_all test_fix1026_round4"""
import contextlib, io

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-FX4-2: DNS64 / NAT64 (IPv6-only networks)
_NAT64 = {"www.ti.com": ["64:ff9b::1701:203", "23.1.2.3"], "www.st.com": ["64:ff9b::1702:304"],
          "lu.maker.com": ["64:ff9b:1::1703:405"],                       # RFC 8215 local-use prefix, /96 form
          "meta64.example.org": ["64:ff9b::a9fe:a9fe"], "lo64.example.org": ["64:ff9b::7f00:1"],
          "metalu.example.org": ["64:ff9b:1::a9fe:a9fe"], "lan64.maker.com": ["64:ff9b::a00:5"]}


def _nat64_resolve(host):
    return _NAT64.get(host.rstrip(".").lower(), [])


@lite
def test_a_nat64_address_is_judged_by_the_ipv4_address_it_carries(monkeypatch):
    from ecadlib import datasheet as D
    monkeypatch.setattr(D, "_resolve", _nat64_resolve)
    for host in ("www.ti.com", "www.st.com", "lu.maker.com"):      # a public site behind DNS64: downloaded
        for public in (False, True):
            assert D.unsafe_download(f"https://{host}/ds/x.pdf", public=public) is None, (host, public)
    assert D.redirect_refused("https://www.st.com/a", "https://www.ti.com/b.pdf", public=False) is None
    # the IPv4 inside is metadata / loopback: still refused, also written as an address
    for host in ("meta64.example.org", "lo64.example.org", "metalu.example.org", "[64:ff9b::a9fe:a9fe]",
                 "[64:ff9b::127.0.0.1]"):
        for public in (False, True):
            assert D.unsafe_download(f"http://{host}/latest/meta-data/", public=public), (host, public)
    # a private address behind NAT64: allowed for a BOM link, not for a web search's link
    assert D.unsafe_download("http://lan64.maker.com/x.pdf", public=False) is None
    assert D.unsafe_download("http://lan64.maker.com/x.pdf", public=True)
    # other reserved IPv6 space stays refused
    assert D.unsafe_download("http://[100::1]/x.pdf", public=False)
