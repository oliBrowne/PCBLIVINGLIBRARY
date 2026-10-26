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
