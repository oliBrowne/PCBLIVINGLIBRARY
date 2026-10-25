"""Axial glass packages named on the BOM: DO-34 (SOD68, JEDEC DO-204AG) and DO-35 (SOD27, DO-204AH) resolve to KiCad's
checked horizontal land, as DO-41 and DO-201AD already did (KTY83, benchmark 2026-10-25: the BOM says DO-34 and NXP's
datasheet and SOD68 package document print no hole or bend pitch, so the datasheet route held it; KiCad's
D_DO-34_SOD68_P7.62mm_Horizontal is 7.62 mm pitch, 0.75 mm holes, the IPC-2222 hole the hold suggested)."""
import tempfile
from pathlib import Path

from ecadlib import footprint_bench as FB, stdparts as SP
from ecadlib.eval import runner as R

DO34 = "D_DO-34_SOD68_P7.62mm_Horizontal"
DO35 = "D_DO-35_SOD27_P7.62mm_Horizontal"


def test_do34_and_do35_names_resolve_to_kicads_axial_land():
    for text in ("DO-34", "SOD68", "SOD-68", "DO-204AG", "DO34"):
        assert SP.resolve([text], "D", "KTY83")["footprint"] == DO34, text
    for text in ("DO-35", "SOD27", "DO-204AH"):
        assert SP.resolve([text], "D", "1N4148")["footprint"] == DO35, text
    assert SP.kicad_ref(DO34) == "Diode_THT:" + DO34
    assert SP.resolve(["DO-34"], "U")["footprint"] is None           # a diode package on a part that isn't


def test_the_do34_land_is_kicads():
    pads = {p[0]: p[1:] for p in FB.numbers(SP.build(DO34)[0])["pads"]}
    assert set(pads) == {"1", "2"}
    (x1, y1, w1, h1, d1), (x2, y2, w2, h2, d2) = pads["1"], pads["2"]
    assert round(x2 - x1, 3) == 7.62 and y1 == y2 and (w1, h1, d1) == (w2, h2, d2) == (1.5, 1.5, 0.75), pads


def test_the_eval_bom_gives_kty83_a_diode_line_with_its_package():
    with tempfile.TemporaryDirectory() as d:
        case = [c for c in R.load_set("kicad-v1")["cases"] if c["mpn"] == "KTY83"]
        rows = Path(d) / "b.csv"
        R.write_bom(case, rows)
        line = rows.read_text().splitlines()[1].split(",")
        assert line[0].startswith("D") and line[3] == "DO-34", line
        assert SP.resolve([line[3]], "D", line[2])["footprint"] == DO34


def test_kty83_is_built_right_from_the_standard_table_with_nothing_sent():
    with tempfile.TemporaryDirectory() as d:
        s = R.run(ids=["tht-KTY83"], sources=["standard"], offline=True, out=d, log=lambda *a: None)
        assert s["summary"]["overall"]["right"] == 1 and s["summary"]["headline"]["from_standard"] == 1, s["summary"]
        assert s["summary"]["overall"]["usd"] == 0


def test_an_axial_name_that_says_standing_or_another_bend_is_not_the_laid_flat_land():
    """KiCad's axial lands are laid flat at one bend (DO-34/35 7.62 mm, DO-41 10.16 mm); a BOM that says the part
    stands up, or bends its leads to another pitch, is another footprint (self-review 2026-10-25)."""
    for text in ("DO-34 vertical", "DO-35 standing", "DO-41 Vertical", "DO-34_P5.08mm", "DO-35 (10.16mm)", "DO-41, P7.62",
                 "SOD68 upright"):
        r = SP.resolve([text], "D", "X")
        assert r["footprint"] is None and "bend" in r["fp_how"], (text, r)
    for text in ("DO-34_P7.62mm", "DO-35 (7.62mm) horizontal", "DO-41, 10.16 mm", "DO-201AD"):
        assert SP.resolve([text], "D", "X")["footprint"], text
