"""Full review, Altium library files: example pinouts against the manufacturers' datasheets, and libraries holding
entries whose names share their first 31 characters (the OLE storage-name limit).
    python3 -m tests.run_all test_review_full_altiumfiles"""
import io, json, tempfile
from pathlib import Path

import olefile

from ecadlib import altium_lib as AL

ROOT = Path(__file__).parent.parent


def _pins(name):
    d = json.loads((ROOT / f"examples/symbols/{name}.json").read_text())
    nums = [p["num"] for p in d["pins"]]
    assert len(nums) == len(set(nums)), nums
    return {p["num"]: p["name"] for p in d["pins"]}, {p["num"]: p["type"] for p in d["pins"]}


def test_ch340c_pinout_matches_wch_datasheet():
    """WCH CH340DS1 (V3C) pin table, SOP-16: pin 8 is OUT# on the CH340C (a software-defined MODEM output), not NC."""
    names, types = _pins("CH340C")
    assert names == {"1": "GND", "2": "TXD", "3": "RXD", "4": "V3", "5": "UD+", "6": "UD-", "7": "NC", "8": "OUT#",
                     "9": "CTS#", "10": "DSR#", "11": "RI#", "12": "DCD#", "13": "DTR#", "14": "RTS#", "15": "R232", "16": "VCC"}
    assert types["8"] == "output" and types["2"] == "output" and types["3"] == "input"
    sym = AL.symbol_from_json(json.loads((ROOT / "examples/symbols/CH340C.json").read_text()))
    with tempfile.TemporaryDirectory() as d:
        _, syms = AL.read_schlib(AL.write_schlib(Path(d) / "CH340C.SchLib", [sym]))
    assert {r["num"]: r["name"] for r in syms[0]["records"] if r["RECORD"] == "2"} == names
    # the committed library is the regenerated one
    _, syms = AL.read_schlib(ROOT / "out/altium_models/SchLib/CH340C.SchLib")
    assert {r["num"]: r["name"] for r in syms[0]["records"] if r["RECORD"] == "2"} == names


def test_tps62133_pinout_matches_ti_datasheet():
    """TI SLVSAG7F pin functions, RGT (VQFN-16): the exposed pad has no number there (17 here, as in KiCad)."""
    names, _ = _pins("TPS62133RGTR")
    assert names == {"1": "SW", "2": "SW", "3": "SW", "4": "PG", "5": "FB", "6": "AGND", "7": "FSW", "8": "DEF", "9": "SS/TR",
                     "10": "AVIN", "11": "PVIN", "12": "PVIN", "13": "EN", "14": "VOS", "15": "PGND", "16": "PGND", "17": "EP"}


def _fp(name, n):
    return {"name": name, "description": name, "tracks": [], "fills": [], "regions": [],
            "pads": [{"designator": str(i + 1), "x": i * 2.54, "y": 0, "w": 1.7, "h": 1.7, "shape": "rect"} for i in range(n)]}


def test_one_pcblib_holds_footprints_sharing_their_first_31_characters():
    """'PinHeader_1x05_P2.54mm_Vertical' and its '_SMD_Pin1Left' / '_SMD_Pin1Right' variants (all shipped specs) have the
    same 31-character storage name; `altium_lib pcblib --single` refused the library ('names collide after truncation')."""
    names = ["PinHeader_1x05_P2.54mm_Vertical", "PinHeader_1x05_P2.54mm_Vertical_SMD_Pin1Left",
             "PinHeader_1x05_P2.54mm_Vertical_SMD_Pin1Right", "A/B", "A_B"]
    fps = [_fp(nm, i + 1) for i, nm in enumerate(names)]
    data = AL.pcblib_bytes(fps, stamp=("01/01/2026", "00:00:00"))
    o = olefile.OleFileIO(io.BytesIO(data), raise_defects=olefile.DEFECT_INCORRECT)
    tops = {e[0] for e in o.listdir(storages=True, streams=False)}
    assert all(len(t) <= 31 for t in tops) and "PinHeader_1x05_P2.54mm_Vertical" in tops, tops    # first keeps the plain key
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.PcbLib"
        p.write_bytes(data)
        got = AL.read_pcblib(p)
    assert [f["name"] for f in got] == names
    assert len({f["storage"].upper() for f in got}) == len(names)
    for f, fp in zip(got, fps):                       # each entry reads back as its own footprint
        pads = [r for r in f["records"] if r["type"] == "pad"]
        assert [r["designator"] for r in pads] == [q["designator"] for q in fp["pads"]], f["name"]
        assert [r["x"] for r in pads] == [AL.mm(q["x"]) for q in fp["pads"]] and f["params"]["PATTERN"] == f["name"]
    for bad in ([_fp("SOT-23", 3), _fp("sot-23", 3)], [_fp("Library", 1)]):    # same name twice / reserved: refused
        try:
            AL.pcblib_bytes(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {[f['name'] for f in bad]}")


def test_one_schlib_holds_symbols_sharing_their_first_31_characters():
    names = ["X" * 31, "X" * 40, "X" * 45, "X" * 29 + "~1"]
    syms = [AL.ic_symbol(nm, [{"num": str(k), "name": f"P{k}", "type": "io"} for k in range(1, i + 2)], layout=False)
            for i, nm in enumerate(names)]
    with tempfile.TemporaryDirectory() as d:
        p = AL.write_schlib(Path(d) / "x.SchLib", syms)
        olefile.OleFileIO(str(p), raise_defects=olefile.DEFECT_INCORRECT).close()
        hdr, got = AL.read_schlib(p)
    assert [s["name"] for s in got] == names
    assert [sum(r["RECORD"] == "2" for r in s["records"]) for s in got] == [1, 2, 3, 4]
    assert [s["records"][0]["LIBREFERENCE"] for s in got] == names
