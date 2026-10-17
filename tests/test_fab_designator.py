"""#37: a .Designator on the assembly (fab) layer of every Altium footprint, compared by parity and the footprint check."""
import json, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, footprint_gen as F, footprint_rules as FR, kicad_import as K, parity as PY

SPECS = Path(__file__).resolve().parents[1] / "ecadlib" / "package_specs"


def _spec():
    return json.loads((SPECS / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text()) if (SPECS / "SOIC-8_3.9x4.9mm_P1.27mm.json").is_file() \
        else json.loads(sorted(SPECS.glob("*.json"))[0].read_text())


def _texts(path):
    return [(r["text"], r["layer"], r) for r in AL.read_pcblib(path)[0]["records"] if r["type"] == "text"]


def test_generated_footprint_carries_reference_to_mechanical_13():
    spec = _spec()
    fp, _ = AL.footprint_from_spec(spec)
    fab = [t for t in fp["texts"] if t[0] == ".Designator" and t[6] == AL.MECH13]
    assert len(fab) == 1 and [t[6] for t in fp["texts"] if t[0] == ".Designator"] == [AL.TOP_OVERLAY, AL.MECH13]
    cx, cy = AL.text_centre(*fab[0][:4], fab[0][5])
    assert abs(cx) < 1e-3 and abs(cy) < 1e-3                       # footprint_gen puts ${REFERENCE} at the origin
    with tempfile.TemporaryDirectory() as d:
        path = AL.write_pcblib(Path(d) / "a.PcbLib", [fp])
        assert sorted((t, l) for t, l, _ in _texts(path)) == [(".Comment", 69), (".Designator", 33), (".Designator", 69)]
        afp = AL.read_pcblib(path)[0]
        txt, _ = F.build(spec)
        assert PY.footprint_pair(txt, afp) == []
        assert not [m for _, _, m in FR.check_pcblib(afp) if "Designator" in m]


def test_footprint_without_fab_reference_gets_one_centred_on_the_body():
    txt, _ = F.build(_spec())
    fp, _ = K.convert_footprint(txt.replace("${REFERENCE}", "ASM"), "x", "spec")
    assert not [t for t in fp["texts"] if t[6] == AL.MECH13 and t[0] == ".Designator"]
    out = AL.add_fab_designator(fp)
    assert out is not fp and AL.add_fab_designator(out) is out
    (t, x, y, h, w, rot, layer), = [t for t in out["texts"] if t[0] == ".Designator" and t[6] == AL.MECH13]
    cx, cy = AL.text_centre(t, x, y, h, rot)
    assert abs(cx) < 1e-3 and abs(cy) < 1e-3 and 0.25 <= h <= 1.0 and abs(w - 0.15 * h) < 1e-3
    with tempfile.TemporaryDirectory() as d:          # the writer adds it; rules move it to the library's assembly layer
        path = AL.write_pcblib(Path(d) / "b.PcbLib", [fp], {"assembly": 59})
        assert sorted((t, l) for t, l, _ in _texts(path)) == [(".Comment", 59), (".Designator", 33), (".Designator", 59)]


def test_tall_body_turns_the_designator():
    fp = {"name": "tall", "pads": [{"designator": "1", "x": 0, "y": -3, "w": 1, "h": 1}, {"designator": "2", "x": 0, "y": 3, "w": 1, "h": 1}],
          "tracks": [], "texts": []}
    (_, x, y, h, _, rot, _), = AL.add_fab_designator(fp)["texts"]
    assert rot == 90.0 and h == 1.0
    cx, cy = AL.text_centre(".Designator", x, y, h, rot)
    assert abs(cx) < 1e-3 and abs(cy) < 1e-3
    assert AL.add_fab_designator(dict(fp, pads=[])) is not None and not AL.add_fab_designator(dict(fp, pads=[]))["texts"]


def test_parity_and_check_flag_a_missing_fab_designator():
    spec = _spec()
    txt, _ = F.build(spec)
    fp, _ = AL.footprint_from_spec(spec)
    fp = dict(fp, texts=[t for t in fp["texts"] if not (t[0] == ".Designator" and t[6] == AL.MECH13)])
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "c.PcbLib"
        Path(path).write_bytes(AL.pcblib_bytes([fp], path.name))   # bypass write_pcblib, which would add it back
        afp = AL.read_pcblib(path)[0]
        diffs = PY.footprint_pair(txt, afp)
        assert [(x["level"], x["what"], x["field"]) for x in diffs] == [("warn", "fab designator", "presence")]
        assert any("no .Designator on Mechanical13" in m for _, _, m in FR.check_pcblib(afp))
        moved = dict(fp, texts=fp["texts"] + [(".Designator", 1.0, 1.0, 0.8, 0.12, 0.0, AL.MECH13)])
        path.write_bytes(AL.pcblib_bytes([moved], path.name))
        diffs = PY.footprint_pair(txt, AL.read_pcblib(path)[0])
        assert [(x["level"], x["what"], x["field"]) for x in diffs] == [("warn", "fab designator", "centre")]


def test_kicad_fab_reference_conversion_notes():
    txt, _ = F.build(_spec())
    notes = []
    K.convert_footprint(txt, "x", "spec", notes)
    assert any("${REFERENCE} on F.Fab -> .Designator (Mechanical 13)" in n for n in notes)
    assert not any("user fp_text not converted" in n for n in notes)
