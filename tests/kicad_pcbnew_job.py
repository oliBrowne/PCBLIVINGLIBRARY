"""Jobs run by tests/test_kicad_real.py inside a Python that can `import pcbnew` (KiCad's own, often /usr/bin/python3,
not the one running the tests). Self-contained: no ecadlib imports. Usage:

    python3 tests/kicad_pcbnew_job.py JOB ARGS_JSON      -> prints one JSON line

Jobs:
  load   {"lib": dir}                         every footprint in a .pretty as KiCad reads it (pads, courtyard, graphics)
  drc    {"lib": dir, "board": path}          all footprints on one board, nets per pad number, DRC -> violations per footprint
  libcheck {"dir": project dir, "lib": name, "fps": [names], "bogus": true}
                                              board in a project that has the exported fp-lib-table; DRC library checks
  step   {"lib": dir, "board": path, "models": {name: step path}}   board with one footprint per STEP model (saved only)
"""
import json, re, shutil, subprocess, sys
from pathlib import Path

import pcbnew

MM = pcbnew.ToMM


def _layer_names(board, lset):
    return sorted(board.GetLayerName(l) for l in lset.Seq())


def load(a):
    lib, out, keep = a["lib"], {}, []
    for f in sorted(Path(lib).glob("*.kicad_mod")):
        fp = pcbnew.FootprintLoad(lib, f.stem)
        if fp is None:
            out[f.stem] = None
            continue
        board = pcbnew.BOARD()                 # BuildCourtyardCaches needs a parent board (crashes without one);
        keep.append(board)                     # one each, kept alive: reusing one with Remove() broke SWIG's types
        board.Add(fp)
        fp.BuildCourtyardCaches()
        crt = fp.GetCourtyard(pcbnew.F_CrtYd)
        bb = crt.BBox() if crt.OutlineCount() else None
        pads = []
        for p in fp.Pads():
            d = p.GetDrillSize()
            pads.append({"num": p.GetNumber(), "x": MM(p.GetPosition().x), "y": MM(p.GetPosition().y),
                         "w": MM(p.GetSize().x), "h": MM(p.GetSize().y), "rot": p.GetOrientationDegrees(),
                         "custom": p.GetShape() == pcbnew.PAD_SHAPE_CUSTOM, "attr": int(p.GetAttribute()),
                         "drill": [MM(d.x), MM(d.y)], "layers": _layer_names(board, p.GetLayerSet())})
        gfx = {}
        for g in fp.GraphicalItems():
            k = board.GetLayerName(g.GetLayer())
            gfx[k] = gfx.get(k, 0) + 1
        out[f.stem] = {"pads": pads, "courtyard_outlines": crt.OutlineCount(),
                       "courtyard": [MM(bb.GetX()), MM(bb.GetY()), MM(bb.GetRight()), MM(bb.GetBottom())] if bb else None,
                       "graphics": gfx, "zones": len(list(fp.Zones())), "value": fp.GetValue(), "reference": fp.GetReference()}
    return out


def _edge(board, w, h):
    for a, c in [((0, 0), (w, 0)), ((w, 0), (w, h)), ((w, h), (0, h)), ((0, h), (0, 0))]:
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
        s.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(c[0]), pcbnew.FromMM(c[1])))
        board.Add(s)


def _violations(rpt, refmap):
    agg = {}
    for b in Path(rpt).read_text().split("\n[")[1:]:
        kind = b.split("]")[0]
        for t in set(re.findall(r"of (U\d+)\b|'(U\d+)'|Footprint (U\d+)\b", b)):
            r = next(x for x in t if x)
            agg.setdefault(refmap.get(r, r), {}).setdefault(kind, 0)
            agg[refmap.get(r, r)][kind] += 1
    return agg


def drc(a):
    lib, path = a["lib"], a["board"]
    names = sorted(p.stem for p in Path(lib).glob("*.kicad_mod"))
    board = pcbnew.NewBoard(path)
    cur, y, rowh = 10.0, 10.0, 0.0
    for i, n in enumerate(names):
        fp = pcbnew.FootprintLoad(lib, n)
        fp.SetReference(f"U{i + 1}")
        board.Add(fp)
        bb = fp.GetBoundingBox(False, False)
        w, h = MM(bb.GetWidth()), MM(bb.GetHeight())
        if cur + w > 200:
            cur, y, rowh = 10.0, y + rowh + 5, 0.0
        ox, oy = cur - MM(bb.GetX() - fp.GetPosition().x), y - MM(bb.GetY() - fp.GetPosition().y)
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(ox), pcbnew.FromMM(oy)))
        cur, rowh = cur + w + 5, max(rowh, h)
        nets = {}
        for p in fp.Pads():                    # one net per pad number: same-number pads may touch, others may not
            if p.GetNumber():
                key = f"/U{i + 1}-{p.GetNumber()}"
                if key not in nets:
                    nets[key] = pcbnew.NETINFO_ITEM(board, key)
                    board.Add(nets[key])
                p.SetNet(nets[key])
    _edge(board, 210, y + rowh + 10)
    board.Save(path)
    b2 = pcbnew.LoadBoard(path)
    rpt = str(Path(path).with_suffix(".rpt"))
    pcbnew.WriteDRCReport(b2, rpt, pcbnew.EDA_UNITS_MILLIMETRES, True)
    agg = _violations(rpt, {f"U{i + 1}": n for i, n in enumerate(names)})
    for v in agg.values():
        v.pop("lib_footprint_issues", None)    # no project library table for this scratch board
    return {k: v for k, v in agg.items() if v}


def libcheck(a):
    d = Path(a["dir"])
    path = str(d / "check.kicad_pcb")
    pro = d / "check.kicad_pro"                # a real project has one; without it KiCad 8 loads no project, so
    if not pro.exists():                       # ${KIPRJMOD} isn't this folder and the table's library looks empty
        pro.write_text(json.dumps({"meta": {"filename": pro.name, "version": 1}}))
    board = pcbnew.NewBoard(path)
    refs = {}
    items = [(a["lib"], n) for n in a["fps"]] + ([("not_a_lib", "X"), (a["lib"], "NOT_IN_LIB")] if a.get("bogus") else [])
    for i, (lib, n) in enumerate(items):
        src = n if lib == a["lib"] and n in a["fps"] else a["fps"][0]
        fp = pcbnew.FootprintLoad(str(d / f"{a['lib']}.pretty"), src)
        fp.SetFPID(pcbnew.LIB_ID(lib, n))
        fp.SetReference(f"U{i + 1}")
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(20 + 20 * i), pcbnew.FromMM(20)))
        board.Add(fp)
        refs[f"U{i + 1}"] = f"{lib}:{n}"
    _edge(board, 40 + 20 * len(items), 40)
    board.Save(path)
    rpt = d / "check.rpt"
    if int(pcbnew.Version().split(".")[0]) >= 8 and shutil.which("kicad-cli"):
        # KiCad 8's Python module doesn't resolve ${KIPRJMOD} for a board it loads, so every footprint looks missing;
        # kicad-cli (new in 8) runs DRC the way the app does, with the project and its fp-lib-table loaded
        subprocess.run(["kicad-cli", "pcb", "drc", "--format", "report", "--severity-all", "--units", "mm",
                        "-o", str(rpt), path], capture_output=True, text=True, timeout=600)
    else:
        b2 = pcbnew.LoadBoard(path)            # loads the project next to it, with its fp-lib-table
        pcbnew.WriteDRCReport(b2, str(rpt), pcbnew.EDA_UNITS_MILLIMETRES, True)
    agg = _violations(str(rpt), refs)
    out = {k: {kk: vv for kk, vv in v.items() if kk.startswith("lib_footprint")} for k, v in agg.items()}
    blocks = [b for b in (d / "check.rpt").read_text().split("\n[")[1:] if b.startswith("lib_footprint")]
    out["_detail"] = [b.strip()[:300] for b in blocks[:3]]     # KiCad's own words, for the test's failure message
    return out


def step(a):
    lib, path = a["lib"], a["board"]
    board = pcbnew.NewBoard(path)
    for i, (n, model) in enumerate(sorted(a["models"].items())):
        fp = pcbnew.FootprintLoad(lib, n)
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = model
        fp.Models().clear()
        fp.Models().push_back(m)
        fp.SetReference(f"U{i + 1}")
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(20 + 40 * (i % 5)), pcbnew.FromMM(20 + 40 * (i // 5))))
        board.Add(fp)
    _edge(board, 220, 40 * ((len(a["models"]) + 4) // 5) + 20)
    board.Save(path)
    return {"saved": path}


if __name__ == "__main__":
    job, args = sys.argv[1], json.loads(sys.argv[2])
    print(json.dumps({"load": load, "drc": drc, "libcheck": libcheck, "step": step}[job](args)))
