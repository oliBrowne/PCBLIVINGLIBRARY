"""2026-10-26 re-review of the fix round on PR #196 (reviews/2026-10-26_review_196/fixes1.md, R-FX-1..10).
Plain functions: python -m tests.run_all test_fix1026_round3"""
import contextlib, io

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-FX-1: a BOM link's host NAME is resolved too
_NAMES = {"169.254.169.254.nip.io": ["169.254.169.254"], "localtest.me": ["127.0.0.1", "::1"],
          "127.0.0.1.sslip.io": ["127.0.0.1"], "zero.example.org": ["0.0.0.0"], "mc.example.org": ["224.0.0.1"],
          "v6meta.example.org": ["fd00:ec2::254"], "mapped.example.org": ["::ffff:169.254.169.254"],
          "intranet.maker.com": ["10.0.0.5"], "nas.local.maker.com": ["192.168.1.20"],
          "www.ti.com": ["23.1.2.3"], "both.maker.com": ["23.1.2.3", "127.0.0.1"]}


def _fake_resolve(host):
    return _NAMES.get(host.rstrip(".").lower(), [])


@lite
def test_a_bom_link_whose_name_resolves_to_this_machine_or_metadata_is_refused(monkeypatch):
    from ecadlib import datasheet as D
    monkeypatch.setattr(D, "_resolve", _fake_resolve)
    for host in ("169.254.169.254.nip.io", "localtest.me", "127.0.0.1.sslip.io", "zero.example.org",
                 "mc.example.org", "mapped.example.org", "both.maker.com", "LOCALTEST.ME."):
        for public in (False, True):
            assert D.unsafe_download(f"http://{host}/latest/meta-data/x.pdf", public=public), (host, public)
        # and as a redirect hop of a BOM link or of a web search's link
        assert D.redirect_refused("http://www.ti.com/a.pdf", f"http://{host}/latest/meta-data/", public=False), host
        assert D.redirect_refused("http://www.ti.com/a.pdf", f"http://{host}/x", public=True), host
    # a datasheet server on your own network stays allowed for a BOM link (not for a web search's link)
    for host in ("intranet.maker.com", "nas.local.maker.com"):
        assert D.unsafe_download(f"http://{host}/ds/x.pdf", public=False) is None, host
        assert D.unsafe_download(f"http://{host}/ds/x.pdf", public=True), host
    assert D.unsafe_download("https://www.ti.com/lit/ds/symlink/lm358.pdf", public=False) is None
    assert D.unsafe_download("https://nowhere.maker.com/x.pdf", public=False) is None      # does not resolve: no refusal
    assert D.redirect_refused("http://www.ti.com/a.pdf", "https://www.ti.com/b.pdf", public=False) is None


@lite
def test_the_metadata_address_is_refused_even_where_private_addresses_are_allowed(monkeypatch):
    from ecadlib import datasheet as D
    # fd00:ec2::254 is AWS's IPv6 metadata address: a unique-local (private) address, which a BOM link may name
    monkeypatch.setattr(D, "_resolve", _fake_resolve)
    assert D.unsafe_download("http://v6meta.example.org/latest/meta-data/", public=False)
    assert D.unsafe_download("http://[fd00:ec2::254]/latest/meta-data/", public=False)
