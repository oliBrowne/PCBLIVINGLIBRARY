"""`ecadlib testpack`: the 6-part Altium + KiCad pack a person opens in the real tools. Run:
    python3 -m tests.run_all test_testpack
Built by the real CLI from an empty folder; every claim in CHECKLIST.md's part table is checked against the files;
Altium files read back (pyaltiumlib + ecadlib), KiCad loads them (skips without kicad-cli / pcbnew). Nothing here is
opened in Altium Designer or Altium 365."""
import atexit, csv, json, re, shutil, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, sexpr, testpack as TP
from tests import e2e_support as E

_PACK = {}


def pack():
    if not _PACK:
        root = Path(tempfile.mkdtemp(prefix="ecadlib-testpack-"))
        atexit.register(shutil.rmtree, root, True)
        p = E.ecadlib(root, "testpack", "--out", "pack")
        _PACK.update(root=root, dir=root / "pack", out=p.stdout, snap=E.snapshot(root / "pack"))
    return _PACK


def pads(f):
    out = []
    for p in sexpr.find(sexpr.parse(Path(f).read_text()), "pad"):
        at, sz, dr = sexpr.first(p, "at"), sexpr.first(p, "size"), sexpr.first(p, "drill")
        out.append({"num": p[1], "kind": p[2], "shape": p[3], "x": float(at[1]), "y": float(at[2]), "w": float(sz[1]),
                    "h": float(sz[2]), "drill": [x for x in (dr or [])[1:] if x != "oval"]})
    return out


def test_pack_from_an_empty_folder():
    k = pack()
    d = k["dir"]
    assert re.search(r"6 parts, 0 held -> .*CHECKLIST\.md", k["out"]), k["out"]
    for f in ("CHECKLIST.md", "bom.csv", "testpack.json", "library/plan.json", "library/review.md", "library/parts.csv",
              "altium/testpack.DbLib", "altium/testpack.xlsx", "kicad/testpack.kicad_sym", "kicad/fp-lib-table",
              "kicad/sym-lib-table", "kicad/testpack.kicad_dbl", "kicad/testpack.sqlite"):
        assert (d / f).is_file(), f
    s = json.loads((d / "testpack.json").read_text())
    assert [p["status"] for p in s["parts"]] == ["ready"] * 6 and not s["held"], s
    cl = (d / "CHECKLIST.md").read_text()
    for h in ("## 1. Altium Designer", "## 2. Altium 365", "## 3. KiCad 8 or later", "Nothing was sent to Altium 365"):
        assert h in cl, h
    for p in s["parts"]:
        assert p["part"] in cl and p["expect"] in cl, p["part"]


def test_checklist_geometry_claims_are_true():
    k = pack()
    lib = k["dir"] / "kicad" / "testpack.pretty"
    r = pads(lib / "R_0603_1608Metric.kicad_mod")
    assert [(p["w"], p["h"]) for p in r] == [(0.8, 0.95)] * 2 and abs(r[1]["x"] - r[0]["x"] - 1.65) < 1e-6
    q = {p["num"]: p for p in pads(lib / "SOT-23.kicad_mod")}
    assert set(q) == {"1", "2", "3"} and q["1"]["x"] == q["2"]["x"] < 0 < q["3"]["x"]
    assert abs(q["2"]["y"] - q["1"]["y"] - 1.9) < 1e-6 and abs(q["3"]["x"] - q["1"]["x"] - 1.875) < 1e-6
    so = pads(lib / "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod")
    assert len(so) == 8 and {(p["w"], p["h"]) for p in so} == {(1.95, 0.6)}
    assert abs(so[1]["y"] - so[0]["y"] - 1.27) < 1e-6 and abs(so[7]["x"] - so[0]["x"] - 4.95) < 1e-6
    assert so[0]["num"] == "1" and so[0]["x"] < 0 and so[0]["y"] == min(p["y"] for p in so)       # pin 1 top-left
    qf = pads(lib / "VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm.kicad_mod")
    sig = [p for p in qf if p["num"] not in ("", "17")]
    assert len(sig) == 16 and {tuple(sorted((p["w"], p["h"]))) for p in sig} == {(0.25, 0.835)}
    ep = [p for p in qf if p["num"] == "17"]
    assert len(ep) == 1 and (ep[0]["x"], ep[0]["y"], ep[0]["w"], ep[0]["h"]) == (0, 0, 1.68, 1.68)
    assert sum(p["num"] == "" for p in qf) == 4                                                 # paste windows
    assert sig[0]["num"] == "1" and sig[0]["x"] < 0 and sig[0]["y"] == min(p["y"] for p in sig if p["x"] == sig[0]["x"])
    u = pads(lib / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod")
    smd = [p for p in u if p["kind"] == "smd"]
    assert len(smd) == 16 and len({(p["x"], p["y"]) for p in smd}) == 12
    stacked = {frozenset(p["num"] for p in smd if (p["x"], p["y"]) == xy) for xy in {(p["x"], p["y"]) for p in smd}}
    assert {s for s in stacked if len(s) == 2} == {frozenset(x) for x in (("A1", "B12"), ("A4", "B9"), ("A9", "B4"), ("A12", "B1"))}
    sh = [p for p in u if p["num"] == "SH"]
    assert len(sh) == 4 and all(p["kind"] == "thru_hole" and len(p["drill"]) == 2 for p in sh)      # slotted
    np_ = [p for p in u if p["kind"] == "np_thru_hole"]
    assert len(np_) == 2 and all(p["drill"] == ["0.65"] for p in np_)
    h = pads(lib / "PinHeader_1x05_P2.54mm_Vertical.kicad_mod")
    assert [p["num"] for p in h] == list("12345") and all(p["drill"] == ["1"] and p["w"] == 1.7 for p in h)
    assert h[0]["shape"] == "rect" and {p["shape"] for p in h[1:]} == {"circle"} and h[1]["y"] - h[0]["y"] == 2.54
    # symbols: the pins the checklist names
    syms = {s[1]: s for s in sexpr.find(sexpr.parse((k["dir"] / "kicad" / "testpack.kicad_sym").read_text()), "symbol")}
    def pin(name):
        found, todo = {}, [syms[name]]
        while todo:
            n = todo.pop()
            for c in n[1:]:
                if isinstance(c, list) and c and c[0] == "pin":
                    found[sexpr.first(c, "number")[1]] = sexpr.first(c, "name")[1]
                elif isinstance(c, list):
                    todo.append(c)
        return found
    assert {k_: v for k_, v in pin("LM358DR").items() if k_ in "148"} == {"1": "OUT_A", "4": "V-", "8": "V+"}
    assert pin("TPS62133RGTR")["17"] == "EP" and len(pin("TPS62133RGTR")) == 17
    assert set(pin("TYPE-C-31-M-12")) == {p["num"] for p in u if p["num"]} and pin("TYPE-C-31-M-12")["SH"] == "SHIELD"
    bjt = pin("MMBT3904LT1G")
    assert (bjt["1"], bjt["2"], bjt["3"]) == ("B", "E", "C"), bjt


def test_altium_side_reads_back_and_resolves():
    d = pack()["dir"] / "altium"
    rows = list(csv.DictReader(open(d / "testpack.csv", encoding="utf-8-sig")))
    assert len(rows) == 6
    try:
        import logging
        logging.getLogger("pyaltiumlib").setLevel(logging.CRITICAL)
        import pyaltiumlib as pal
    except ImportError:
        pal = None
    for r in rows:
        assert "\\" in r["Library Path"] and "/" not in r["Library Path"] + r["Footprint Path"], r   # Windows separators
        sp, fp = d / r["Library Path"].replace("\\", "/"), d / r["Footprint Path"].replace("\\", "/")
        sym = AL.read_schlib(sp)[1]
        foot = AL.read_pcblib(fp)
        assert [s["name"] for s in sym] == [r["Library Ref"]] and [f["name"] for f in foot] == [r["Footprint Ref"]], r
        nums = {x["num"] for x in sym[0]["records"] if x.get("RECORD") == "2"}
        pads_ = {x.get("designator") for x in foot[0]["records"] if x["type"] == "pad"} - {""}
        assert nums == pads_, (r["Part Number"], nums, pads_)                      # every pin has a pad and back
        assert r["Manufacturer Part Number"] and r["ComponentLink2URL"].startswith("https://"), r
        if pal:
            assert [p.Name for p in pal.read(str(sp)).Parts] == [r["Library Ref"]]
            assert [p.Name for p in pal.read(str(fp)).Parts] == [r["Footprint Ref"]]
    dblib = (d / "testpack.DbLib").read_text(encoding="utf-8", errors="replace")
    assert r"Data Source=C:\ecadlib\testpack\testpack.xlsx" in dblib and "DatabasePathRelative=1" in dblib, dblib[:400]
    assert TP.WIN_DIR in (pack()["dir"] / "CHECKLIST.md").read_text()


def test_kicad_loads_the_pack():
    from tests import test_kicad_real as KR
    if not KR.kicad_cli():
        return KR._skip("kicad-cli")
    tmp = Path(tempfile.mkdtemp(prefix="ecadlib-testpack-kicad-"))
    atexit.register(shutil.rmtree, tmp, True)
    src = pack()["dir"] / "kicad"
    k = Path(shutil.copytree(src, tmp / "kicad")) if (KR.kicad_major() or 8) >= 8 else E.kicad7_copy(src, tmp / "kicad")
    r = KR.cli("sym", "export", "svg", k / "testpack.kicad_sym", "-o", tmp / "svg")
    assert r.returncode == 0 and len(list((tmp / "svg").glob("*.svg"))) >= 6, (r.stdout[-1500:], r.stderr[-1500:])
    r = KR.cli("fp", "export", "svg", k / "testpack.pretty", "-o", tmp / "fpsvg")
    assert r.returncode == 0 and len(list((tmp / "fpsvg").glob("*.svg"))) == 6, (r.stdout[-1500:], r.stderr[-1500:])
    if not KR.pcbnew_python():
        return KR._skip("pcbnew")
    fps = sorted(f.stem for f in (k / "testpack.pretty").glob("*.kicad_mod"))
    res = KR.pcbnew_job("libcheck", dir=str(k), lib="testpack", fps=fps, bogus=False)
    detail = res.pop("_detail")
    assert not {k_: v for k_, v in res.items() if v}, (res, detail)
    v = KR.pcbnew_job("drc", lib=str(k / "testpack.pretty"), board=str(tmp / "drc.kicad_pcb"))
    # the USB-C shell pads share the number SH (as in KiCad's own footprint); anything else is a finding
    assert set(v) <= {"USB_C_Receptacle_HRO_TYPE-C-31-M-12"}, v               # stacked pads A1/B12 ... on 2 nets here
    assert set(v.get("USB_C_Receptacle_HRO_TYPE-C-31-M-12", {})) <= {"clearance", "solder_mask_bridge", "unconnected_items"}, v
    print(f"      KiCad {KR.kicad_major()}: 6 symbols, 6 footprints loaded; libcheck via fp-lib-table and DRC: {v or 'clean'}")


def test_second_testpack_run_changes_nothing():
    k = pack()
    E.ecadlib(k["root"], "testpack", "--out", "pack")
    again = E.snapshot(k["dir"])
    changed = sorted(x for x in set(k["snap"]) | set(again) if k["snap"].get(x) != again.get(x))
    assert changed in ([], ["library/librarian_state.json"]), changed
