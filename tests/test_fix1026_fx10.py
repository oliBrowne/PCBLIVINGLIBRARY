"""Fixes 2026-10-26 (fx10): second review of the R-RU / R-FX4 / R-BF fixes (R-FX10-1..5, 7). Each test failed on
9b3c729 and passes with its fix."""
from tests.fixtures_lite import lite
from tests.test_fix1026_bomflow import HDR, SRR, _holds, _run


def _device_l():
    """KiCad's Device:R symbol renamed Device:L (the recorded library has no inductor symbol)."""
    from tests import e2e_support as E
    import re
    text = (E.KICAD / "kicad-symbols" / "Device.kicad_symdir" / "R.kicad_sym").read_text(encoding="utf-8")
    return re.sub(r'\(symbol "R(_\d_\d)?"', lambda m: f'(symbol "L{m.group(1) or ""}"', text)


# --- R-FX10-1: a maker / own land the index lacks is held, never built as the plain chip ---------------------------

_OWN = [("L1", "10uH", "Inductor_SMD:L_Bourns_SRR1210", "Bourns", "SRR1210A-100M"),        # a typo'd maker land
        ("L2", "22uH", "L_Bournz_SRR1210A", "Bourns", "SRR1210A-220M"),
        ("R9", "0.01", "R_Shnt_Vishay_WSK2512", "Vishay", "WSK2512R0100FEA"),             # 4-terminal Kelvin shunt
        ("L3", "1uH", "L_0603_1608Metric_typo", "Murata", "LQM18PN1R0MFRL")]               # a suffix KiCad lacks
_EXTRA = [("Inductor_SMD:L_1210_3225Metric", None), ("Inductor_SMD:L_0603_1608Metric", None),
          ("Resistor_SMD:R_2512_6332Metric", None), ("Inductor_SMD:L_Bourns_SRR1210A", SRR)]


@lite
def test_a_maker_land_the_index_lacks_is_held_not_built_as_the_plain_chip(tmp_path):
    for sources in (("kicad", "standard"), ("standard",)):
        csv = HDR + "".join(f"{d},1,{v},{fp},{mk},{mpn},part,https://example.com/a.pdf\n" for d, v, fp, mk, mpn in _OWN)
        sub = tmp_path / "_".join(sources)
        sub.mkdir()
        items = _run(sub, csv, sources, extra_fp=_EXTRA, extra_sym=[("Device:L", _device_l())])
        assert len(items) == len(_OWN), list(items)
        for it in items.values():
            src = str(it["source"].get("footprint") or "")
            assert it.get("status") != "ready", (sources, it["name"], it["footprint"], src)
            assert not src.endswith(("L_1210_3225Metric", "R_2512_6332Metric", "L_0603_1608Metric")), (sources, src)


@lite
def test_a_bare_chip_land_name_is_still_built_by_its_size(tmp_path):
    csv = HDR + ("R1,1,10k,R_0603_1608,Yageo,RC0603FR-0710KL,part,https://example.com/a.pdf\n"
                 "R2,1,1k,Resistor_SMD:R_0603_1608Metric,Yageo,RC0603FR-071KL,part,https://example.com/a.pdf\n")
    items = _run(tmp_path, csv, ("kicad", "standard"))
    for it in items.values():
        assert it.get("status") == "ready", (it["name"], _holds(it))
        assert str(it["source"]["footprint"]).endswith("R_0603_1608Metric"), it["source"]
