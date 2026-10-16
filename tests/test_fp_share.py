"""Two BOM lines with the same footprint name share one file only when the pads are the same (ecadlib/fp_share.py).

The eval set found it: `SOIC-14` on a 3.9 mm and a 5.3 mm part gave the second part the first one's footprint.
Fixtures: tests/test_librarian's fake KiCad library, plus a 5.3 mm SOIC-8 made from the 3.9 mm one by moving the
pads out (synthetic)."""
import json, re, tempfile
from pathlib import Path

from ecadlib import fp_share as FS, librarian as L, kicad_export as KE, resolve as R
from tests import test_librarian as TL

NARROW = TL.FOOTS["Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"]
WIDE = re.sub(r"\(at (-?[\d.]+) (-?[\d.]+)\)", lambda m: f"(at {float(m.group(1)) * 1.45:.3f} {m.group(2)})",
              NARROW.replace("SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8_5.3x5.3mm_P1.27mm"))
BOM = """Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,Description,Datasheet
U1,1,NARROW1,SOIC-8,ACME,NARROW1,Narrow op amp,https://example.com/a.pdf
U2,1,WIDE1,SOIC-8,ACME,WIDE1,Wide op amp,https://example.com/b.pdf
U3,1,NARROW2,SOIC-8,ACME,NARROW2,Another narrow op amp,https://example.com/c.pdf
"""


def http():
    h = TL.FakeHTTP()
    syms = {"Amp:NARROW1": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "Amp:WIDE1": "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm",
            "Amp:NARROW2": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}
    foots = {"Package_SO:SOIC-8_3.9x4.9mm_P1.27mm": NARROW, "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm": WIDE}
    for repo, files, dsuf, fsuf in (("kicad-symbols", {**TL.SYMS, **{k: TL.ksym(k.split(":")[1], [str(i) for i in range(1, 9)],
                                                                                 footprint=v) for k, v in syms.items()}},
                                     ".kicad_symdir", ".kicad_sym"),
                                    ("kicad-footprints", {**TL.FOOTS, **foots}, ".pretty", ".kicad_mod")):
        tree = [{"type": "tree", "path": f"{r.split(':')[0]}{dsuf}"} for r in files]
        tree += [{"type": "blob", "path": f"{r.split(':')[0]}{dsuf}/{r.split(':')[1]}{fsuf}"} for r in files]
        h.routes[L.TREE.format(repo=repo, page=1)] = json.dumps(tree)
        for r, text in files.items():
            lib, name = r.split(":")
            h.routes[L.K.GITLAB.format(repo=repo, path=f"{lib}{dsuf}/{name}{fsuf}")] = text
    return h


def run(d, **kw):
    return TL.run(d, http(), bom=BOM, sources=["kicad"], **kw)[0]


def test_same_name_other_pads_get_their_own_file():
    with tempfile.TemporaryDirectory() as d:
        items = run(d)
        out = Path(d) / "out"
        n1, w, n2 = items["NARROW1"], items["WIDE1"], items["NARROW2"]
        assert n1["footprint"]["ref"] == "SOIC-8" and n1["footprint"]["file"] == "models/PcbLib/SOIC-8.PcbLib"
        # before the fix WIDE1 got models/PcbLib/SOIC-8.PcbLib, NARROW1's 3.9 mm footprint
        assert w["footprint"]["file"] == "models/PcbLib/SOIC-8_5.3x5.3mm_P1.27mm.PcbLib", w["footprint"]
        assert w["footprint"]["ref"] == "SOIC-8_5.3x5.3mm_P1.27mm" and w["footprint"]["bom_ref"] == "SOIC-8"
        assert "SOIC-8_5.3x5.3mm_P1.27mm" in (out / w["footprint"]["kicad_mod"]).read_text()
        assert [f["name"] for f in L.AL.read_pcblib(out / w["footprint"]["file"])] == ["SOIC-8_5.3x5.3mm_P1.27mm"]
        assert b"ModelName=SOIC-8_5.3x5.3mm_P1.27mm|" in (out / w["symbol"]["file"]).read_bytes()   # symbol relinked
        assert b"ModelName=SOIC-8|" in (out / n1["symbol"]["file"]).read_bytes()
        # same pads: still one shared file
        assert n2["footprint"]["file"] == n1["footprint"]["file"] and n2["footprint"]["ref"] == "SOIC-8"
        assert "shared with NARROW1" in n2["librarian"]["notes"]
        assert not any("shared with" in x for x in w["librarian"]["notes"])
        assert any(x.startswith("named SOIC-8_5.3x5.3mm_P1.27mm") for x in w["librarian"]["notes"])
        # resume: nothing renamed, nothing rewritten
        snap = {f.name: f.stat().st_mtime_ns for f in (out / "models").rglob("*.*")}
        again = run(d)
        assert {n: i["footprint"] for n, i in again.items()} == {n: i["footprint"] for n, i in items.items()}
        assert {f.name: f.stat().st_mtime_ns for f in (out / "models").rglob("*.*")} == snap
        # consumers keyed by ref see two footprints
        rep = KE.export(out / "plan.json", Path(d) / "kx", "lib", include_held=True, log=lambda *a: None)
        assert {"SOIC-8", "SOIC-8_5.3x5.3mm_P1.27mm"} <= set(rep["footprints"]), rep["footprints"]


def test_workspace_from_before_the_check_is_repaired_without_renaming_owners():
    with tempfile.TemporaryDirectory() as d:
        run(d)
        out = Path(d) / "out"
        st = json.loads((out / "librarian_state.json").read_text())
        for k in ("NARROW1", "NARROW2", "WIDE1"):            # what the old librarian wrote: one file per BOM name
            fp = st[k]["footprint"]
            for x in ("hash", "name", "renamed_from"):
                fp.pop(x, None)
            if k != "NARROW1":
                st[k]["footprint"] = dict(st["NARROW1"]["footprint"], notes=["shared with NARROW1"])
        (out / "librarian_state.json").write_text(json.dumps(st))
        (out / "models" / "PcbLib" / "SOIC-8_5.3x5.3mm_P1.27mm.PcbLib").unlink()
        owner = (out / "models" / "PcbLib" / "SOIC-8.PcbLib").stat().st_mtime_ns
        items = run(d)
        assert items["WIDE1"]["footprint"]["file"] == "models/PcbLib/SOIC-8_5.3x5.3mm_P1.27mm.PcbLib"
        assert items["NARROW2"]["footprint"]["file"] == items["NARROW1"]["footprint"]["file"] == "models/PcbLib/SOIC-8.PcbLib"
        assert (out / "models" / "PcbLib" / "SOIC-8.PcbLib").stat().st_mtime_ns == owner
        assert FS.unchecked_share(st["WIDE1"]["footprint"]) and not FS.unchecked_share(
            json.loads((out / "librarian_state.json").read_text())["WIDE1"]["footprint"])


def test_resolve_choice_on_a_renamed_part_keeps_its_name():
    with tempfile.TemporaryDirectory() as d:
        run(d)
        out = Path(d) / "out"
        src = R.Sources(http=http(), cache=Path(d) / "cache")
        before = (out / "models" / "PcbLib" / "SOIC-8.PcbLib").read_bytes()
        res = R.choose(out / "plan.json", "WIDE1", "footprint", "kicad:Package_SO:SOIC-8_5.3x5.3mm_P1.27mm", src=src)
        assert res["file"] == "models/PcbLib/SOIC-8_5.3x5.3mm_P1.27mm.PcbLib", res
        assert (out / "models" / "PcbLib" / "SOIC-8.PcbLib").read_bytes() == before      # NARROW1's file untouched
        items = run(d)                                                                    # and a re-run keeps the name
        assert items["WIDE1"]["footprint"]["ref"] == "SOIC-8_5.3x5.3mm_P1.27mm"
        assert items["WIDE1"]["footprint"]["file"] == res["file"]


def test_candidates_and_digest():
    assert FS.candidates("SOIC-8", "SOIC-8_5.3x5.3mm_P1.27mm", "WIDE1") == ["SOIC-8", "SOIC-8_5.3x5.3mm_P1.27mm", "SOIC-8_WIDE1"]
    assert FS.candidates("SO8", "SOIC-8_5.3x5.3mm_P1.27mm", "W") == ["SO8", "SO8_SOIC-8_5.3x5.3mm_P1.27mm", "SO8_W"]
    assert FS.candidates("SOIC-8", "", "W") == ["SOIC-8", "SOIC-8_W"]
    assert FS.digest(NARROW) == FS.digest(NARROW.replace("SOIC-8_3.9x4.9mm_P1.27mm", "Other")) != FS.digest(WIDE)
