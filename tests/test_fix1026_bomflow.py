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
