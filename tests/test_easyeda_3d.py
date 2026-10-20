"""EasyEDA / LCSC 3D models (ecadlib.easyeda_3d): the footprint's STEP model is downloaded, placed on the footprint
and gives the body height, so an LCSC part is no longer held for "no 3D height" (2026-10-20: every LCSC part, e.g. the
YXC crystal X322512MSB4SI / C9002, was held for it). python3 -m tests.test_easyeda_3d

easyeda.com is blocked here: the STEP files are made with step_gen, placed off-centre and in inches on purpose; the
payloads are the recorded C7593 answer and a crystal shaped like it."""
import copy, json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_lib as AL, easyeda_3d as E3, kicad_import as K, release_check as RC, step_gen as SG
from ecadlib.httpcache import Response
from tests import test_librarian as TL

FIX = Path(__file__).parent / "fixtures"
NE555 = json.loads((FIX / "easyeda_live" / "C7593.json").read_text())       # REAL answer, with its outline3D node


def step(x0, y0, z0, x1, y1, z1, inch=False):
    """A one-box STEP model; inch=True writes the same box in inches (a CONVERSION_BASED_UNIT file)."""
    k = 25.4 if inch else 1.0
    text = SG.write_step([SG.box(x0 / k, y0 / k, z0 / k, x1 / k, y1 / k, z1 / k, "body")], "test model")
    if inch:
        text = text.replace("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )",
                            "( CONVERSION_BASED_UNIT('INCH',#1) LENGTH_UNIT() NAMED_UNIT(*) )")
    return text


def ref(**kw):
    r = {"uuid": "abc12345", "title": "T", "url": E3.STEP_URL.format("abc12345"), "origin": (0.0, 0.0), "z": 0.0,
         "rotation": (0, 0, 0), "size": None, "through_hole": False}
    r.update(kw)
    return r


def crystal(lcsc="C9002", mpn="X322512MSB4SI", uuid="0f1e2d3c4b5a69788796a5b4c3d2e1f0"):
    """A 3.2 x 2.5 mm 4-pad SMD crystal (YXC X322512MSB4SI shape) with its outline3D node, as EasyEDA serves it."""
    e = TL.ee_as(mpn, lcsc)
    head = {"x": 4000, "y": 3000, "c_para": {"package": "CRYSTAL-SMD_4P-L3.2-W2.5-BL", "pre": "X?"}}
    pads = [f"PAD~RECT~{4000 + dx}~{3000 + dy}~5.5118~4.7244~1~~{n}~0~~0~gge{n}~0~~Y~0"
            for n, (dx, dy) in enumerate(((-4.3307, 3.3465), (4.3307, 3.3465), (4.3307, -3.3465), (-4.3307, -3.3465)), 1)]
    node = {"gId": "g1_outline", "nodeName": "g", "attrs": {"c_width": "12.5984", "c_height": "9.8425", "c_rotation": "0,0,0",
            "z": "0", "c_origin": "4000,3000", "uuid": uuid, "c_etype": "outline3D", "id": "g1_outline",
            "title": "CRYSTAL-SMD_4P-L3.2-W2.5-H0.8", "layerid": "19"}, "childNodes": []}
    e["result"]["packageDetail"] = {"title": head["c_para"]["package"],
                                    "dataStr": {"head": head, "shape": pads + ["SVGNODE~" + json.dumps(node)]}}
    e["result"]["dataStr"]["head"]["c_para"]["package"] = head["c_para"]["package"]
    return e


class Http(TL.FakeHTTP):
    def __init__(self, easyeda, models=None):
        super().__init__(easyeda)
        for uuid, text in (models or {}).items():
            self.routes[E3.STEP_URL.format(uuid)] = text


# ---------------------------------------------------------------- the outline3D node
def test_model_ref_reads_the_outline3d_node():
    r = E3.model_ref(NE555["result"]["packageDetail"]["dataStr"])
    assert r["uuid"] == "ec3b9f9b31a74655be3e55848dbee9c1" and r["url"].endswith("/ec3b9f9b31a74655be3e55848dbee9c1")
    assert r["origin"] == (0.0, 0.0) and r["z"] == 0 and r["rotation"] == (0, 0, 0) and not r["through_hole"]
    assert abs(r["size"][0] - 4.9) < 0.01 and abs(r["size"][1] - 6.0) < 0.01, r["size"]      # 10 mil units
    d = copy.deepcopy(NE555["result"]["packageDetail"]["dataStr"])
    d["shape"] = [s.replace('"c_origin":"4000,3000"', '"c_origin":"4010,2990"') for s in d["shape"]]
    assert E3.model_ref(d)["origin"] == (10 * 0.254, 10 * 0.254)                               # y flipped: up
    assert E3.model_ref({"head": {}, "shape": ["PAD~RECT~0~0~1~1~1~~1~0~~0~g~0~~Y~0"]}) is None
    bad = ['SVGNODE~{"attrs":{"c_etype":"outline3D","uuid":"../../etc"}}', "SVGNODE~not json", 'SVGNODE~{"attrs":7}']
    assert E3.model_ref({"head": {}, "shape": bad}) is None                                     # untrusted data
    tht = {"head": {"x": 0, "y": 0}, "shape": ["PAD~ELLIPSE~0~0~6~6~11~~1~1.5~~0~g1~0~~Y~0", bad[0].replace("../../etc", "abcdef0123")]}
    assert E3.model_ref(tht)["through_hole"]


# ---------------------------------------------------------------- placing the model
def test_place_centres_the_model_on_the_footprint_and_rests_it_on_the_board():
    text = step(10, 20, -3, 14.9, 26, -1.3)                     # 4.9 x 6 x 1.7 mm, far from the origin, below z = 0
    out, info = E3.place(text, ref())
    x0, y0, z0, x1, y1, z1 = info["bbox"]
    assert abs(x0 + 2.45) < 1e-6 and abs(x1 - 2.45) < 1e-6 and abs(y0 + 3) < 1e-6 and abs(y1 - 3) < 1e-6, info
    assert abs(z0) < 1e-6 and info["height"] == 1.7, info
    assert not SG.check(out)["errors"], SG.check(out)                   # still a valid solid
    ext = E3.extent(out)
    assert [round(v, 4) for v in ext] == [-2.45, -3.0, 0.0, 2.45, 3.0, 1.7], ext
    _, info = E3.place(text, ref(origin=(1.0, -2.0), z=0.254))  # c_origin off the footprint origin, z offset
    assert [round(v, 4) for v in info["bbox"]] == [-1.45, -5.0, 0.254, 3.45, 1.0, 1.954], info


def test_place_keeps_inches_and_turns_with_c_rotation():
    out, info = E3.place(step(0, 0, 0, 4.9, 6.0, 1.7, inch=True), ref())
    assert info["unit"] == 25.4 and [round(v, 3) for v in info["bbox"]] == [-2.45, -3.0, 0.0, 2.45, 3.0, 1.7], info
    assert "CONVERSION_BASED_UNIT('INCH'" in out                 # the file's units are left as they are
    _, info = E3.place(step(0, 0, 0, 4.9, 6.0, 1.7), ref(rotation=(0, 0, 90)))
    x0, y0, z0, x1, y1, z1 = info["bbox"]
    assert abs((x1 - x0) - 6.0) < 1e-6 and abs((y1 - y0) - 4.9) < 1e-6 and abs(z1 - 1.7) < 1e-6, info


def test_through_hole_model_keeps_its_pins_below_the_board():
    _, info = E3.place(step(-1, -1, -3.0, 1, 1, 8.5), ref(through_hole=True))
    assert info["bbox"][2] == -3.0 and info["height"] == 8.5, info


def test_place_only_moves_3d_points():
    text = ("ISO-10303-21;\nDATA;\n#1=CARTESIAN_POINT('',(1.,2.,3.));\n#2=CARTESIAN_POINT('',(5.,6.));\n"
            "#3=DIRECTION('',(1.,0.,0.));\n#4=CARTESIAN_POINT('it''s',(3.,4.,5.));\nENDSEC;\nEND-ISO-10303-21;\n")
    out, info = E3.place(text, ref(rotation=(0, 0, 90)))
    assert "#2=CARTESIAN_POINT('',(5.,6.));" in out              # a 2D (parameter space) point is not touched
    assert "#3=DIRECTION('',(6.12323400e-17,1.,0.));" in out or "#3=DIRECTION('',(6.123234e-17,1.,0.));" in out, out
    assert "'it''s'" in out and info["bbox"][2] == 0
    try:
        E3.place("ISO-10303-21;\nDATA;\nENDSEC;\n", ref())
    except ValueError as e:
        assert "no 3D points" in str(e)
    else:
        raise AssertionError("a file with no points must raise")


def test_checks_flag_a_turned_or_shifted_model():
    _, info = E3.place(step(0, 0, 0, 6.0, 4.9, 1.7), ref(size=(4.9, 6.0)))
    assert any("turned 90 degrees" in n for n in E3.checks(info, ref(size=(4.9, 6.0))))
    _, info = E3.place(step(0, 0, 0, 4.9, 6.0, 1.7), ref(size=(4.9, 6.0), origin=(3.0, 0.0)))
    pads = [{"x": -1.0, "y": 0.0}, {"x": 1.0, "y": 0.0}]
    assert any("off the pads' centre" in n for n in E3.checks(info, ref(size=(4.9, 6.0)), pads))
    _, info = E3.place(step(0, 0, 0, 4.9, 6.0, 1.7), ref())
    assert E3.checks(info, ref(size=(4.9, 6.0)), pads) == []


def test_fetch_refuses_what_is_not_a_step_file():
    class H:
        def __init__(self, r):
            self.r, self.kw = r, None

        def get(self, url, **kw):
            self.kw = kw
            return self.r
    h = H(Response(200, step(0, 0, 0, 1, 1, 1)))
    assert E3.fetch(h, ref())[0].startswith("ISO-10303-21") and h.kw["headers"]["Referer"] == "https://easyeda.com/"
    assert E3.fetch(H(Response(200, "<html>login</html>")), ref()) == (None, "the answer is not a STEP file")
    assert E3.fetch(H(Response(404, "")), ref()) == (None, "HTTP 404")
    assert E3.fetch(H(Response(0, "", error="offline: not in cache")), ref()) == (None, "offline: not in cache")


# ---------------------------------------------------------------- through the librarian
CRYSTAL_BOM = TL.BOM.split("\n")[0] + "\nY1,1,16MHz,,YXC,X322512MSB4SI,C9002,Crystal 16MHz 3225,\n"


def _pcb_bodies(d, it):
    lib = AL.read_pcblib(Path(d) / "out" / it["footprint"]["file"])
    return [r for r in lib[0]["records"] if r["type"] == "body"], lib[0].get("models") or []


def test_lcsc_crystal_without_its_model_is_held_for_height():
    """The bug: no 3D model downloaded and no table knows CRYSTAL-SMD_4P-L3.2-W2.5: held for a missing height."""
    with tempfile.TemporaryDirectory() as d:
        items, http = TL.run(d, Http({"C9002": crystal()}), bom=CRYSTAL_BOM)
        it = items["X322512MSB4SI"]
        assert it["source"]["footprint"] == "easyeda:C9002", it["source"]
        assert any("3D model 0f1e2d3c4b5a69788796a5b4c3d2e1f0 not downloaded" in t for t in it["librarian"]["tried"]), it["librarian"]
        assert any(E3.STEP_URL.format("0f1e2d3c4b5a69788796a5b4c3d2e1f0") == u for u in http.calls)
        assert it["status"] == "held" and any(h.startswith("height:") for h in it["hold_reasons"]), it["hold_reasons"]


def test_lcsc_crystal_with_its_model_is_not_held_for_height():
    model = step(5, 5, 2, 8.2, 7.5, 2.8)                        # 3.2 x 2.5 x 0.8 mm, placed anywhere in its file
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, Http({"C9002": crystal()}, {"0f1e2d3c4b5a69788796a5b4c3d2e1f0": model}), bom=CRYSTAL_BOM)
        it = items["X322512MSB4SI"]
        assert not any(h.startswith("height:") for h in it["hold_reasons"]), it["hold_reasons"]
        assert round(it["release_check"]["height"], 3) == 0.8, it["release_check"]
        notes = it["librarian"]["notes"]
        assert any("embedded EasyEDA STEP model CRYSTAL-SMD_4P-L3.2-W2.5-H0.8" in n and "height 0.8 mm" in n for n in notes), notes
        assert it["footprint"]["model"]["source"] == "easyeda:0f1e2d3c4b5a69788796a5b4c3d2e1f0", it["footprint"]
        assert "JLCEDA/EasyEDA" in it["footprint"]["model"]["credit"]
        bodies, models = _pcb_bodies(d, it)
        assert len(bodies) == 1 and len(models) == 1, (bodies, models)
        ext = E3.extent(models[0][1].decode("utf-8"))
        assert [round(v, 3) for v in ext] == [-1.6, -1.25, 0.0, 1.6, 1.25, 0.8], ext       # on the footprint origin


def test_box_mode_uses_the_model_outline_without_embedding_it():
    model = step(0, 0, 0, 3.2, 2.5, 0.8)
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, Http({"C9002": crystal()}, {"0f1e2d3c4b5a69788796a5b4c3d2e1f0": model}), bom=CRYSTAL_BOM,
                          gen_step=False)
        it = items["X322512MSB4SI"]
        assert round(it["release_check"]["height"], 3) == 0.8 and not any(h.startswith("height:") for h in it["hold_reasons"])
        bodies, models = _pcb_bodies(d, it)
        assert len(bodies) == 1 and models == [], models
        assert any("box from the EasyEDA STEP model" in n for n in it["librarian"]["notes"])


def test_a_bad_model_falls_back_to_the_generated_package():
    """NE555 (SOIC-8): a model that is not a STEP file leaves the step_gen package model, as before."""
    bom = TL.BOM.split("\n")[0] + "\nU1,1,NE555DR,,TI,NE555DR,C7593,timer,\n"
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, Http({"C7593": NE555}, {"ec3b9f9b31a74655be3e55848dbee9c1": "<html>blocked</html>"}), bom=bom)
        it = items["NE555DR"]
        assert any("not a STEP file" in t for t in it["librarian"]["tried"]), it["librarian"]["tried"]
        assert any("generated STEP" in n for n in it["librarian"]["notes"]), it["librarian"]["notes"]
        assert "model" not in it["footprint"]


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_easyeda_3d"]))
