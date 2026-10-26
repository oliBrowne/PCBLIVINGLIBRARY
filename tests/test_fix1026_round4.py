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


# ---------------------------------------------------------------- R-FX4-3: no DNS lookup offline; one per host in the browser
@lite
def test_an_offline_run_refuses_an_unfetched_link_without_looking_its_host_up(tmp_path, monkeypatch):
    from ecadlib import datasheet as D
    looked = []
    monkeypatch.setattr(D, "_resolve", lambda h: looked.append(h) or [])
    store = D.PdfStore(tmp_path, offline=True)
    try:
        store._fetch("https://www.ti.com/lit/ds/symlink/lm358.pdf")
        raise AssertionError("an offline fetch of a link never downloaded must fail")
    except D.DatasheetError as e:
        assert "offline" in str(e), e
    assert looked == [], looked
    # online, the host is still checked before anything is requested
    store.offline = False
    monkeypatch.setattr(D, "_resolve", lambda h: ["169.254.169.254"])
    try:
        store._fetch("https://meta.maker.com/x.pdf")
        raise AssertionError("a link to the metadata address must be refused")
    except D.DatasheetError as e:
        assert "not downloaded" in str(e), e


class _Req:
    def __init__(self, url):
        self.url = url


class _Route:
    def __init__(self, url):
        self.request, self.did = _Req(url), None

    def abort(self):
        self.did = "abort"

    def continue_(self):
        self.did = "continue"


@lite
def test_the_browser_route_check_looks_each_host_up_once(monkeypatch):
    from ecadlib import datasheet as D, pdf_fetch as PF
    looked = []
    ips = {"www.ti.com": ["23.1.2.3"], "meta.maker.com": ["169.254.169.254"], "lan.maker.com": ["10.0.0.5"]}
    monkeypatch.setattr(D, "_resolve", lambda h: looked.append(h) or ips.get(h, []))
    s = PF._State()
    check = PF.route_check(s)
    did = []
    for u in ("https://www.ti.com/a.js", "https://www.ti.com/b.css", "https://www.ti.com/lit/x.pdf",
              "https://meta.maker.com/latest", "https://meta.maker.com/other", "http://lan.maker.com/x.pdf"):
        r = _Route(u)
        check(r)
        did.append(r.did)
    assert did == ["continue"] * 3 + ["abort"] * 2 + ["continue"], did
    assert looked == ["www.ti.com", "meta.maker.com", "lan.maker.com"], looked
    s.public = True                                   # a web search's link: judged again, by its own rule
    r = _Route("http://lan.maker.com/y.pdf")
    check(r)
    assert r.did == "abort" and looked[-1] == "lan.maker.com", (r.did, looked)
    r = _Route("file:///etc/passwd")                  # not a host's verdict: the URL's own scheme still refused
    check(r)
    assert r.did == "abort"


# ---------------------------------------------------------------- Altium project fixtures (R-FX4-1, R-FX4-4)
def _blk(s):
    import struct
    b = s.encode("cp1252") + b"\0"
    return struct.pack("<I", len(b)) + b


def _project(d, params, variation="", footprint="SOIC-8_3.9x4.9mm_P1.27mm"):
    from ecadlib import cfb
    recs = ["|HEADER=Protel for Windows - Schematic Capture Binary File Version 5.0|WEIGHT=4",
            "|RECORD=1|LIBREFERENCE=OPAMP|DESIGNITEMID=OPAMP|UNIQUEID=ABCDEFGH|CURRENTPARTID=1|PARTCOUNT=2",
            "|RECORD=34|OWNERINDEX=0|TEXT=U1|NAME=Designator"]
    recs += [f"|RECORD=41|OWNERINDEX=0|TEXT={v}|NAME={k}" for k, v in params.items()]
    n = len(recs) - 1
    recs += ["|RECORD=44|OWNERINDEX=0", f"|RECORD=45|OWNERINDEX={n}|MODELNAME={footprint}|MODELTYPE=PCBLIB|ISCURRENT=T"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "Sheet1.SchDoc").write_bytes(cfb.to_bytes({"FileHeader": b"".join(_blk(r) for r in recs)}))
    vs = [variation] if variation else []
    (d / "B.PrjPcb").write_text("[Design]\r\n[Document1]\r\nDocumentPath=Sheet1.SchDoc\r\n[ProjectVariant1]\r\n"
                                f"Description=V2\r\nVariationCount={len(vs)}\r\n" +
                                "".join(f"Variation{i}={v}\r\n" for i, v in enumerate(vs, 1)) +
                                "ParamVariationCount=0\r\n", encoding="utf-8")
    return d / "B.PrjPcb"


_HOUSE = '[parameters.names]\n"Manufacturer Part Number" = "MfrPN"\n"Manufacturer" = "Maker"\n'


# ---------------------------------------------------------------- R-FX4-4: where-used reads with the workspace's rules
@lite
def test_where_used_reads_house_named_mpn_parameters(tmp_path):
    import json
    from ecadlib import altium_project as AP
    prj_dir = tmp_path / "proj"
    _project(prj_dir, {"MfrPN": "LM358DR", "Maker": "TI"})
    (tmp_path / "ecadlib.toml").write_text(_HOUSE, encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = AP.where_used_main(["--projects", str(prj_dir), "-o", str(tmp_path / "wu")])
    assert not rc
    res = json.loads((tmp_path / "wu.json").read_text(encoding="utf-8"))
    mpns = [p.get("mpn") for p in (res["parts"].values() if isinstance(res["parts"], dict) else res["parts"])]
    assert mpns == ["LM358DR"], mpns
    # the UI / MCP: the workspace's own ecadlib.toml
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws", offline=True)
    (tmp_path / "ws" / "ecadlib.toml").write_text(_HOUSE, encoding="utf-8")
    _project(tmp_path / "ws" / "p2", {"MfrPN": "LM358DR", "Maker": "TI"})
    out = ws.where_used([str(tmp_path / "ws" / "p2")])
    assert [r.get("mpn") for r in out["rows"]] == ["LM358DR"], out["rows"]
