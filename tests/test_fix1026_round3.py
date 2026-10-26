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


# ---------------------------------------------------------------- R-FX-2: a cell holding both size codes
def _read_fp(f, header, cells):
    from ecadlib import bom_triage as T
    text = f'Designator,Qty,MPN,Manufacturer,"{header}"\n' + "".join(f'R{i},1,RC{i},Yageo,"{c}"\n'
                                                                     for i, c in enumerate(cells))
    f.write_bytes(text.encode("utf-8"))
    with contextlib.redirect_stderr(io.StringIO()):
        lines, _ = T.read_bom(f)
    return {l["designator"]: l for l in lines}


@lite
def test_a_cell_with_a_metric_and_an_inch_code_gives_the_inch_one_of_the_pair(tmp_path):
    from ecadlib.kicad_index import package_of
    cells = {"0603/0201": "0201", "0201/0603": "0201", "0603 (0201)": "0201", "1608/0603": "0603",
             "0603 (1608)": "0603", "1608 (0603)": "0603", "0603 1608": "0603", "0201 (0603 metric)": "0201",
             "01005/0402": "01005", "3216 / 1206": "1206"}
    for hdr in ("Case (Metric / Imp.)", "Case (Imperial/Metric)", "Case Code (mm)", "Package"):
        by = _read_fp(tmp_path / "b.csv", hdr, list(cells))
        for i, (cell, want) in enumerate(cells.items()):
            assert package_of(by[f"R{i}"].get("footprint") or "") == want, (hdr, cell, by[f"R{i}"])


@lite
def test_two_codes_that_are_no_metric_inch_pair_are_held(tmp_path):
    for hdr in ("Case (Metric / Imp.)", "Package", "Case Code (mm)"):
        by = _read_fp(tmp_path / "b.csv", hdr, ["0603/0805", "0402 (0603)", "R_0603_1608Metric", "SOT-23-5"])
        for d in ("R0", "R1"):
            assert not by[d].get("footprint") and "two size codes" in by[d].get("bom_note", ""), (hdr, by[d])
        assert by["R2"]["footprint"] == "R_0603_1608Metric", by["R2"]               # a footprint name is kept
        assert by["R3"]["footprint"] == "SOT-23-5", by["R3"]


# ---------------------------------------------------------------- R-FX-3: no unchecked draft DbLib from the UI / MCP
@lite
def test_the_ui_and_mcp_dblib_export_refuse_a_bom_the_librarian_has_not_run_for(tmp_path):
    import json
    from pathlib import Path
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws", ["examples/kicad_lib"], providers=["digikey"], offline=True)
    rel = ws.save_upload("bom.csv", Path("examples/sample_bom.csv").read_bytes())
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ws.export_dblib(rel)
        raise AssertionError("an unchecked draft DbLib was written")
    except ValueError as e:
        assert "librarian" in str(e).lower() and "Fill the library" in str(e), e
    alt = ws.root / "altium"
    assert not alt.exists() or not any(p.suffix in (".DbLib", ".sqlite", ".xlsx") for p in alt.rglob("*")), \
        list(alt.rglob("*"))
    r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "export_altium_dblib", "arguments": {"bom": rel}}})
    res = r["result"]
    assert res.get("isError") and "librarian" in res["content"][0]["text"].lower(), r
    tool = next(t for t in M.TOOLS if t["name"] == "export_altium_dblib")
    assert "librarian" in tool["description"].lower(), tool
    page = Path("ecadlib/ui/static/index.html").read_text(encoding="utf-8")
    assert "Written (draft" not in page                                    # the UI has no draft branch any more


# ---------------------------------------------------------------- R-FX-4: maker matcher regressions
def test_maker_names_lost_by_the_fix_round_match_again_and_teco_is_not_te():
    from ecadlib.providers import same_maker as S
    for a, b in (("Wurth", "Würth Electronics Midcom"), ("Würth", "Wurth Electronics Midcom Inc"),
                 ("Central", "Central Semiconductor Corp"), ("Central", "Central Semiconductor"),
                 ("Texas", "TI"), ("Wurth", "WE"), ("TE", "TE Connectivity / Raychem"),
                 ("Murata", "Murata Power Solutions"), ("OSRAM", "ams OSRAM")):
        assert S(a, b) and S(b, a), (a, b)
    for a, b in (("Teco", "TE"), ("TECO", "TE Connectivity"), ("Tecate", "TE"), ("Texas", "TE"),
                 ("Wurth", "Winbond"), ("Central", "Central Technologies"), ("Vishay", "Vishay Precision Group"),
                 ("ST", "Stackpole Electronics"), ("TI", "Analog Devices Inc."), ("AMS", "ams OSRAM"),
                 ("Micron", "Micronas"), ("Analog Devices", "Analog Power"),
                 ("Integrated Device Technology", "Device Engineering Inc.")):
        assert not S(a, b) and not S(b, a), (a, b)
    # makers bought by another, found under the buyer's name at the distributor
    for a, b in (("Pericom", "Diodes Incorporated"), ("Pericom Semiconductor", "Diodes Inc"),
                 ("Dialog Semiconductor", "Renesas Electronics America Inc"), ("Sipex", "MaxLinear, Inc."),
                 ("Catalyst Semiconductor", "onsemi"), ("Integrated Device Technology", "Renesas Electronics"),
                 ("Integrated Device Technology Inc", "IDT")):
        assert S(a, b) and S(b, a), (a, b)


# ---------------------------------------------------------------- R-FX-5: an LCSC number in free text
def test_lcsc_number_skips_c0g_and_gives_none_for_two_different_numbers():
    from ecadlib.providers import lcsc_number as N
    for cell, want in (("C1663 C0G", "C1663"), ("C0G C1663", "C1663"), ("100nF C0G 50V, LCSC: C1663", "C1663"),
                       ("C2040, C1234", ""), ("C2040 / C2040", "C2040"), ("C2040", "C2040"), ("LCSC C2040", "C2040"),
                       ("https://www.lcsc.com/product-detail/C0G-Capacitors_C1663.html", "C1663"),
                       ("https://jlcpcb.com/partdetail/C2040", "C2040"), ("C0G", ""), ("C1206X7R", "")):
        assert N(cell) == want, (cell, N(cell), want)


# ---------------------------------------------------------------- R-FX-6: KiCad's _Handsoldering land copies
def test_a_handsoldering_copy_of_a_land_is_no_second_choice():
    from ecadlib.kicad_index import KiCadIndex, plain_land
    for name, plain in (("SOT-23_Handsoldering", "SOT-23"), ("SOT-23-5_HandSoldering", "SOT-23-5"),
                        ("D_SMA_Handsoldering", "D_SMA"), ("R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",
                                                           "R_0603_1608Metric")):
        assert plain_land(name) == plain, name
    for pkg, cls, fps in (("SOT23", "U", ["Package_TO_SOT_SMD:SOT-23", "Package_TO_SOT_SMD:SOT-23_Handsoldering"]),
                          ("SOT235", "U", ["Package_TO_SOT_SMD:SOT-23-5", "Package_TO_SOT_SMD:SOT-23-5_HandSoldering"])):
        for order in (fps, fps[::-1]):
            idx = KiCadIndex([])
            idx.footprints = {k: {"path": "", "has_3d": True} for k in order}
            assert idx.find_footprint_for_package(pkg, cls, hint=pkg) == fps[0], (pkg, order)
