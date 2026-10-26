"""Fixes 2026-10-26 (bomflow): review round 5 on BOM triage -> librarian, end to end offline (R-BF-1..8). Each HIGH was a
part marked ready on the wrong land or symbol: it is now held (or built as the BOM names it)."""
import contextlib, io, json
from pathlib import Path

from tests.fixtures_lite import lite

KICAD_FIXTURE = Path(__file__).parent / "fixtures" / "e2e" / "kicad"


def _run(tmp, csv_text, sources, libs=(), extra_fp=(), extra_sym=()):
    """The librarian offline on a BOM -> {item name: item}. extra_fp / extra_sym: ("Lib:Name", kicad text or None =
    stdparts' own build of Name) added to the cached KiCad library."""
    from ecadlib import librarian as L
    from ecadlib.httpcache import CachedHTTP, redact
    from tests import e2e_support as E
    tmp = Path(tmp)
    bom = tmp / "bom.csv"
    bom.write_text(csv_text, encoding="utf-8")
    cache = tmp / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    E.seed_cache(cache)
    if extra_fp or extra_sym:
        from ecadlib import stdparts as SP
        h = CachedHTTP(cache, offline=True)
        idx = json.loads((cache / "kicad_library_index.json").read_text())
        for kind, repo, suf, extra in (("footprints", "kicad-footprints", ".pretty/{}.kicad_mod", extra_fp),
                                       ("symbols", "kicad-symbols", ".kicad_symdir/{}.kicad_sym", extra_sym)):
            for ref, text in extra:
                lib, name = ref.split(":")
                text = text if text is not None else SP.build(name)[0]
                url = E.GITLAB.format(repo=repo, path=lib + suf.format(name))
                (cache / f"{h._key('GET', redact(url), None)}.json").write_text(
                    json.dumps({"url": redact(url), "status": 200, "text": text}))
                idx[kind].setdefault(name, []).append(lib)
        (cache / "kicad_library_index.json").write_text(json.dumps(idx))
    with contextlib.redirect_stderr(io.StringIO()):
        items = L.run(str(bom), [str(x) for x in libs], str(tmp / "out"), list(sources), offline=True, cache=str(cache),
                      log=lambda *a, **k: None, gen_step=False)
    return {it["name"]: it for it in items}


def _holds(it):
    return " | ".join(it.get("hold_reasons") or [])


HDR = "Designator,Qty,Value,Footprint,Manufacturer,MPN,Description,Datasheet\n"


# --- R-BF-1: the MPN's own chip size must be the built land's size --------------------------------------------------

def _chip_item(mpn, fp_src):
    return {"name": "X", "folder": "Components/Resistors", "description": "Resistor",
            "parameters": [{"name": "Manufacturer Part Number", "value": mpn}],
            "symbol": {}, "footprint": {"ref": fp_src.split(":")[-1]}, "source": {"footprint": fp_src}}


def test_release_check_holds_a_chip_land_of_another_size_than_the_mpn():
    from ecadlib import release_check as RC
    fails, _, _ = RC.check_item(_chip_item("RC0603FR-0710KL", "standard:Resistor_SMD:R_0402_1005Metric"))
    msgs = [m for r, m in fails if r == "footprint" and "0603" in m and "0402" in m]
    assert msgs, fails
    fails, _, _ = RC.check_item(_chip_item("GRM155R71C104KA88D", "local:Capacitor_SMD:C_0603_1608Metric"))
    assert any(r == "footprint" and "0402" in m for r, m in fails), fails


def test_release_check_keeps_a_chip_land_of_the_mpns_size_and_parts_without_a_size_code():
    from ecadlib import release_check as RC
    for mpn, src in (("RC0603FR-0710KL", "standard:Resistor_SMD:R_0603_1608Metric"),
                     ("GRM155R71C104KA88D", "kicad:Capacitor_SMD:C_0402_1005Metric"),
                     ("ACME-10K-X", "standard:Resistor_SMD:R_0402_1005Metric"),           # no size in the MPN
                     ("RC0603FR-0710KL", "datasheet:https://example.com/a.pdf")):          # the MPN's own land
        fails, _, _ = RC.check_item(_chip_item(mpn, src))
        assert not [m for r, m in fails if r == "footprint" and "chip size" in m], (mpn, src, fails)


@lite
def test_librarian_holds_rc0603_on_an_0402_land(tmp_path):
    items = _run(tmp_path, HDR + "R3,1,10k,R_0402_1005Metric,Yageo,RC0603FR-0710KL,Resistor 10k 1%,https://example.com/a.pdf\n"
                 "C1,1,100nF,C_0603_1608Metric,Murata,GRM155R71C104KA88D,Capacitor 100nF,https://example.com/b.pdf\n",
                 ["standard"])
    for it in items.values():
        assert it["status"] == "held" and "chip size" in _holds(it), (it["name"], _holds(it))


# --- R-BF-2: one MPN on two lines with two footprints: each line keeps its own, and the part is held -----------------

F2 = (HDR + '"R1,R2",2,10k,R_0603_1608Metric,Acme,ACME-10K-X,Resistor 10k,https://example.com/a.pdf\n'
      "R3,1,10k,R_0402_1005Metric,Acme,ACME-10K-X,Resistor 10k,https://example.com/a.pdf\n")


def test_to_rows_reads_each_lines_own_triage_row_and_holds_two_footprints():
    import pandas as pd
    from ecadlib import altium_export as AX
    lines = [{"designator": "R1,R2", "qty": "2", "value": "10k", "footprint": "R_0603_1608Metric", "mpn": "ACME-10K-X",
              "description": "Resistor 10k"},
             {"designator": "R3", "qty": "1", "value": "10k", "footprint": "R_0402_1005Metric", "mpn": "ACME-10K-X",
              "description": "Resistor 10k"}]
    tri = pd.DataFrame([{"line": "1", "mpn": "ACME-10K-X", "local_footprint": "Resistor_SMD:R_0603_1608Metric"},
                        {"line": "2", "mpn": "ACME-10K-X", "local_footprint": "Resistor_SMD:R_0402_1005Metric"}])
    with contextlib.redirect_stderr(io.StringIO()):
        res = AX.to_rows(lines, tri)
    df = next(iter(res.values()))
    assert list(df["Footprint Ref"]) == ["R_0603_1608Metric"], list(df["Footprint Ref"])   # the first line's own land
    holds = df.attrs.get("holds", {}).get(df["Part Number"].iloc[0]) or []
    assert any("R_0603_1608Metric" in h and "R_0402_1005Metric" in h for h in holds), df.attrs


def test_to_rows_does_not_hold_one_land_written_two_ways():
    from ecadlib import altium_export as AX
    lines = [{"designator": "R1", "qty": "1", "value": "10k", "footprint": "0603", "mpn": "ACME-10K-X"},
             {"designator": "R3", "qty": "1", "value": "10k", "footprint": "Resistor_SMD:R_0603_1608Metric",
              "mpn": "ACME-10K-X"}]
    with contextlib.redirect_stderr(io.StringIO()):
        df = next(iter(AX.to_rows(lines).values()))
    assert not df.attrs.get("holds"), df.attrs.get("holds")


@lite
def test_librarian_holds_one_mpn_on_an_0603_and_an_0402_line(tmp_path):
    items = _run(tmp_path, F2, ["kicad", "standard"], [KICAD_FIXTURE])
    assert len(items) == 1, list(items)
    it = next(iter(items.values()))
    assert it["status"] == "held", (it["name"], it["status"])
    assert "R_0603_1608Metric" in _holds(it) and "R_0402_1005Metric" in _holds(it), _holds(it)


# --- R-BF-3: BOM lines that are one part but differ in what is built hold the part (not only a note) ----------------

def _rows(lines):
    from ecadlib import altium_export as AX
    with contextlib.redirect_stderr(io.StringIO()):
        res = AX.to_rows(lines)
    holds = {}
    for df in res.values():
        holds.update(df.attrs.get("holds") or {})
    return res, holds


def test_two_values_on_one_mpn_hold():
    _, holds = _rows([{"designator": "R1", "value": "10k", "footprint": "R_0603_1608Metric", "mpn": "RC0603FR-0710KL"},
                      {"designator": "R2", "value": "1k", "footprint": "R_0603_1608Metric", "mpn": "RC0603FR-0710KL"}])
    assert any("'10k' vs '1k'" in h for v in holds.values() for h in v), holds


def test_two_mpns_merged_into_one_part_hold():
    """SMAJ5.0A and SMAJ50A are two diodes: merged into one part (norm_mpn drops the dot), the part is held."""
    res, holds = _rows([{"designator": "D1", "value": "SMAJ5.0A", "footprint": "D_SMA", "mpn": "SMAJ5.0A"},
                        {"designator": "D2", "value": "SMAJ50A", "footprint": "D_SMA", "mpn": "SMAJ50A"}])
    names = [n for df in res.values() for n in df["Part Number"]]
    assert len(names) == 2 or any("SMAJ50A" in h for v in holds.values() for h in v), (names, holds)


def test_one_part_written_two_ways_does_not_hold():
    _, holds = _rows([{"designator": "C1", "value": "100nF", "footprint": "0603", "mpn": "GRM188R71C104KA01D",
                       "manufacturer": "Murata"},
                      {"designator": "C2", "value": "0.1uF", "footprint": "C_0603_1608Metric", "mpn": "GRM188R71C104KA01D",
                       "manufacturer": "Murata Electronics"},
                      {"designator": "U1", "value": "TPS62133", "footprint": "", "mpn": "TPS62133RGTR"},
                      {"designator": "U2", "value": "TPS62133", "footprint": "", "mpn": "tps62133-rgtr"}])
    assert not holds, holds


@lite
def test_librarian_holds_two_values_on_one_mpn(tmp_path):
    items = _run(tmp_path, HDR + "R1,1,10k,R_0603_1608Metric,Yageo,RC0603FR-0710KL,Resistor 10k,https://example.com/a.pdf\n"
                 "R2,1,1k,R_0603_1608Metric,Yageo,RC0603FR-0710KL,Resistor,https://example.com/a.pdf\n", ["standard"])
    it = next(iter(items.values()))
    assert it["status"] == "held" and "'1k'" in _holds(it), (it["status"], _holds(it))


# --- R-BF-4: the land the BOM names is built as named (or the part is held); a package guess never drops an EP -------

SRR = """(footprint "L_Bourns_SRR1210A" (version 20240108) (generator "pcbnew") (layer "F.Cu")
  (attr smd)
  (fp_line (start -6.1 -6.1) (end 6.1 -6.1) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
  (fp_rect (start -6.5 -6.5) (end 6.5 6.5) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))
  (pad "1" smd roundrect (at -4.85 0) (size 2.9 5.4) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.086))
  (pad "2" smd roundrect (at 4.85 0) (size 2.9 5.4) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.086))
)
"""


def _l_symbol():
    r = (KICAD_FIXTURE / "kicad-symbols" / "Device.kicad_symdir" / "R.kicad_sym").read_text(encoding="utf-8")
    for a, b in (('(symbol "R"', '(symbol "L"'), ('"R_0_1"', '"L_0_1"'), ('"R_1_1"', '"L_1_1"'),
                 ('(property "Reference" "R"', '(property "Reference" "L"'), ('(property "Value" "R"', '(property "Value" "L"')):
        r = r.replace(a, b)
    return r


@lite
def test_kicad_builds_the_named_bourns_land_not_the_1210_chip(tmp_path):
    items = _run(tmp_path, HDR + "L1,1,10uH,Inductor_SMD:L_Bourns_SRR1210A,Bourns,SRR1210A-100M,Power inductor 10uH,"
                 "https://example.com/b.pdf\n", ["kicad", "standard"],
                 extra_fp=[("Inductor_SMD:L_1210_3225Metric", None), ("Inductor_SMD:L_Bourns_SRR1210A", SRR)],
                 extra_sym=[("Device:L", _l_symbol())])
    it = next(iter(items.values()))
    assert it["source"]["footprint"] == "kicad:Inductor_SMD:L_Bourns_SRR1210A", it["source"]
    assert it["footprint"]["ref"] == "L_Bourns_SRR1210A", it["footprint"]


@lite
def test_a_named_kicad_array_or_maker_land_is_never_built_as_a_plain_chip(tmp_path):
    items = _run(tmp_path, HDR + "RN1,1,10k,R_Array_Convex_4x0603,Yageo,YC164-JR-0710KL,Resistor array 4x10k,"
                 "https://example.com/w.pdf\n"
                 "L1,1,10uH,L_Wuerth_WE-PD-Typ-LS_1210,Wurth,7447779110,Power inductor 10uH 12x12mm,"
                 "https://example.com/w.pdf\n", ["kicad", "standard"], [KICAD_FIXTURE],
                 extra_fp=[("Inductor_SMD:L_1210_3225Metric", None)])
    for it in items.values():
        fp = str(it["source"]["footprint"] or "")
        assert not fp.endswith(("R_0603_1608Metric", "L_1210_3225Metric")), (it["name"], fp)
        assert it["status"] == "held", (it["name"], it["status"])


def _index(tmp, names):
    from ecadlib.kicad_index import KiCadIndex
    for ref in names:
        lib, name = ref.split(":")
        d = Path(tmp) / f"{lib}.pretty"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.kicad_mod").write_text(f'(footprint "{name}")\n', encoding="utf-8")
    return KiCadIndex([tmp])


@lite
def test_package_guess_never_drops_or_changes_an_exposed_pad(tmp_path):
    ix = _index(tmp_path / "a", ["Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"])
    assert ix.find_footprint_for_package("SOIC8", "U", hint="SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm") is None
    assert ix.find_footprint_for_package("SOIC8", "U", hint="SOIC-8_3.9x4.9mm_P1.27mm") == \
        "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
    ix = _index(tmp_path / "b", ["Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.45x1.45mm"])
    assert ix.find_footprint_for_package("QFN16", "U", hint="QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm") is None
    assert ix.find_footprint_for_package("QFN16", "U", hint="QFN-16-1EP_3x3mm_P0.5mm_EP1.45x1.45mm") == \
        "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.45x1.45mm"
    ix = _index(tmp_path / "c", ["Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm"])
    assert ix.find_footprint_for_package("SOIC8", "U", hint="SOIC-8_3.9x4.9mm_P1.27mm") is None   # no EP added either


# --- R-BF-5: the BOM's footprint beats the symbol's Footprint property; when they disagree the part is held ----------

QFN48 = "Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.6x5.6mm"


@lite
def test_kicad_builds_the_boms_land_and_holds_when_the_symbol_names_another(tmp_path):
    items = _run(tmp_path, HDR + "U2,1,STM32F103C8T6,QFN-48-1EP_7x7mm_P0.5mm_EP5.6x5.6mm,STMicroelectronics,STM32F103C8T6,"
                 "MCU,https://example.com/s.pdf\n", ["kicad", "standard"], extra_fp=[(QFN48, None)])
    it = next(iter(items.values()))
    assert it["source"]["footprint"] == "kicad:" + QFN48, it["source"]
    assert it["status"] == "held" and "LQFP-48" in _holds(it) and "QFN-48" in _holds(it), (it["status"], _holds(it))


@lite
def test_kicad_does_not_hold_when_the_bom_and_symbol_name_one_land(tmp_path):
    items = _run(tmp_path, HDR + "U2,1,STM32F103C8T6,LQFP-48_7x7mm_P0.5mm,STMicroelectronics,STM32F103C8T6,"
                 "MCU,https://example.com/s.pdf\n", ["kicad", "standard"])
    it = next(iter(items.values()))
    assert it["source"]["footprint"] == "kicad:Package_QFP:LQFP-48_7x7mm_P0.5mm", it["source"]
    assert "symbol" not in _holds(it).lower() or "Footprint" not in _holds(it), _holds(it)


@lite
def test_a_local_symbols_footprint_does_not_replace_the_boms_ep_land(tmp_path):
    items = _run(tmp_path, HDR + "U1,1,LM358,SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm,TI,LM358DDAR,Dual op amp,"
                 "https://example.com/t.pdf\n", ["kicad", "standard"], [KICAD_FIXTURE])
    it = next(iter(items.values()))
    assert not str(it["source"]["footprint"] or "").endswith("SOIC-8_3.9x4.9mm_P1.27mm"), it["source"]
    assert it["status"] == "held", it["status"]


SOIC8EP = "SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm"


def _local_lib(tmp):
    """A local library: LM2904 whose Footprint property is plain SOIC-8, and both SOIC-8 lands."""
    from ecadlib import stdparts as SP
    lib = Path(tmp) / "mylib"
    (lib / "Package_SO.pretty").mkdir(parents=True)
    for n in ("SOIC-8_3.9x4.9mm_P1.27mm", SOIC8EP):
        (lib / "Package_SO.pretty" / f"{n}.kicad_mod").write_text(SP.build(n)[0], encoding="utf-8")
    sym = (KICAD_FIXTURE / "kicad-symbols" / "Amplifier_Operational.kicad_symdir" / "LM2904.kicad_sym").read_text(
        encoding="utf-8").replace('(property "Footprint" ""', '(property "Footprint" "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"', 1)
    (lib / "LM2904.kicad_sym").write_text(sym, encoding="utf-8")
    return lib


@lite
def test_triage_takes_the_boms_local_land_over_the_symbols(tmp_path):
    from ecadlib import bom_triage as T
    from ecadlib.kicad_index import KiCadIndex
    ix = KiCadIndex([_local_lib(tmp_path)])
    rows = T.triage([{"designator": "U1", "qty": "1", "value": "LM2904", "footprint": SOIC8EP, "mpn": "LM2904"}], ix, [], None)
    assert rows[0]["local_footprint"].split(" ")[0] == "Package_SO:" + SOIC8EP, rows[0]
    rows = T.triage([{"designator": "U1", "qty": "1", "value": "LM2904", "footprint": "", "mpn": "LM2904"}], ix, [], None)
    assert rows[0]["local_footprint"].split(" ")[0] == "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", rows[0]   # BOM silent


@lite
def test_librarian_builds_the_boms_local_land_and_holds_the_symbols_other_one(tmp_path):
    items = _run(tmp_path, HDR + f"U1,1,LM2904,{SOIC8EP},TI,LM2904,Dual op amp,https://example.com/t.pdf\n",
                 ["standard"], [_local_lib(tmp_path)])
    it = next(iter(items.values()))
    assert it["source"]["footprint"] == "local:Package_SO:" + SOIC8EP, it["source"]
    assert it["status"] == "held" and "Footprint property" in _holds(it), _holds(it)
