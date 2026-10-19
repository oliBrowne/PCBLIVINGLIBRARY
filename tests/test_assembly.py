"""Assembly-ready parts (#104, docs/assembly.md): the JLCPCB profile, registration of our pads on the assembler's
footprint, pad verdicts, rotation evidence, the golden set, the KiCad / Altium board readers and the BOM / CPL export.
Run:  python3 -m tests.test_assembly

Fixtures (tests/fixtures/assembly, README.md there):
  easyeda/<LCSC>.json   REAL easyeda.com answers (JLCPCB's own footprints), recorded 2026-10-18 from Oliver's PC
  kicad7.pretty/        REAL KiCad 7.0.11 library footprints (CC-BY-SA 4.0 with the library exception)
  boards/*.kicad_pcb    REAL KiCad boards built and saved by KiCad 7's own pcbnew (make_boards.py), with KiCad's own
                        position file (*.pos.csv) and every pad's board position as pcbnew reports it (*.truth.json)
  boards/two_sided_altium_pnp.csv   SYNTHETIC: the same board written in Altium's pick-and-place CSV format
  golden.json           the golden set: real parts with the answer decided by hand (see its 'how' lines)"""
import contextlib, copy, csv, io, json, math, random, tempfile
from pathlib import Path

from ecadlib import assembly as A, assembly_io as IO, assembly_cli as CLI, messages as M
from ecadlib.httpcache import Response

FIX = Path(__file__).parent / "fixtures" / "assembly"
EE, K7, BOARDS = FIX / "easyeda", FIX / "kicad7.pretty", FIX / "boards"
GOLDEN_KICAD = Path(__file__).parent / "fixtures" / "kicad_golden"
PROF = A.load_profile()


def ours(name):
    d = K7 if (K7 / f"{name}.kicad_mod").exists() else GOLDEN_KICAD
    return A.from_kicad_mod((d / f"{name}.kicad_mod").read_text(encoding="utf-8"), name)


def theirs(lcsc):
    return A.from_easyeda(EE / f"{lcsc}.json")


def moved(fp, turn, dx=0.0, dy=0.0, noise=0.0, rng=None, mirror=False):
    """fp's pads turned `turn` deg CCW (mirrored first), moved by (dx, dy), with uniform position noise: a stand-in for
    an assembler footprint drawn in another pose."""
    pads = A.transform(fp.pads, turn, mirror)
    for p in pads:
        p.x += dx + (rng.uniform(-noise, noise) if noise else 0)
        p.y += dy + (rng.uniform(-noise, noise) if noise else 0)
    return A.Footprint(fp.name + "_moved", pads, source="synthetic", package="SYNTHETIC")


# ---------------------------------------------------------------- the profile
def test_profile_loads_and_every_rule_cites_a_source():
    assert "jlcpcb" in A.profiles()
    p = A.load_profile("jlcpcb")
    assert p["profile"] == "jlcpcb" and A.validate_profile(p) == []
    for name, fam in p["families"].items():
        srcs = [fam["source"]] if isinstance(fam["source"], str) else fam["source"]
        assert srcs and all(s in p["sources"] for s in srcs), name
    for s in p["sources"].values():
        assert s["url"].startswith("https://") and s["title"] and s["note"]
    assert p["exports"]["cpl"]["columns"] == ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]
    assert p["exports"]["bom"]["columns"] == ["Comment", "Designator", "Footprint", "LCSC Part #"]


def test_profile_validation_names_the_problem():
    p = copy.deepcopy(PROF)
    p["families"]["chip"]["source"] = "nowhere"
    p["families"]["soic"]["zero"] = {"pin1": "middle"}
    p["overrides"] = [{"part": "C1", "rotation": "ninety", "source": "jlc-cpl"}]
    del p["rotation"]["bottom"]["source"]
    probs = A.validate_profile(p)
    assert any("family chip: source 'nowhere'" in x for x in probs), probs
    assert any("family soic: zero.pin1" in x for x in probs), probs
    assert any("override C1: rotation must be a number" in x for x in probs), probs
    assert any("rotation.bottom" in x for x in probs), probs
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "mine.json"
        f.write_text(json.dumps(p), encoding="utf-8")
        try:
            A.load_profile(str(f))
            raise AssertionError("a broken profile loaded")
        except A.AssemblyError as e:
            assert "nowhere" in str(e)
        try:
            A.load_profile("no-such-assembler")
            raise AssertionError("an unknown profile loaded")
        except A.AssemblyError as e:
            assert "jlcpcb" in str(e)


def test_canonical_frame_and_family_table():
    fr = A.frame(ours("SOIC-8_3.9x4.9mm_P1.27mm"))
    assert (fr["where"], fr["run"], fr["pins"]) == ("TL", "B", 8), fr
    assert A.turn_pose(("TL", "B"), 90) == ("BL", "R") and A.turn_pose(("L", None), 180) == ("R", None)
    assert A.table_turn(PROF, "soic", fr) == (90, "")                    # c = 90: Assembly Rotation 270
    assert A.frame(ours("R_0603_1608Metric"))["where"] == "L"
    assert A.family_of(PROF, ["R_0603_1608Metric"], ours("R_0603_1608Metric")) == "chip"
    assert A.family_of(PROF, ["LED_0603_1608Metric"], ours("LED_0603_1608Metric")) == "led_chip"
    assert A.family_of(PROF, ["SOT-23"], ours("SOT-23")) == "sot23"
    assert A.family_of(PROF, ["SOT-23"], ours("SOIC-8_3.9x4.9mm_P1.27mm")) is None     # 8 pins is no SOT-23
    a = A.assess(ours("SOIC-8_3.9x4.9mm_P1.27mm"))                       # no assembler footprint: the table
    assert (a["rotation"], A.correction(a), a["pads"]) == ("table", 270, "unchecked"), a
    a = A.assess(ours("SOT-23"))                                         # SOT-23: JLCPCB's ...-BR pose, 11 / 11 parts
    assert (a["rotation"], A.correction(a)) == ("table", 180), a
    a = A.assess(ours("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm"))          # JLCPCB draws QFNs both ways: no rule, said why
    assert a["rotation"] == "unknown" and A.correction(a) is None and any("no 0 deg rule for qfn" in n for n in a["notes"])


# ---------------------------------------------------------------- registration (synthetic robustness)
SHAPES = ["R_0603_1608Metric", "R_0402_1005Metric", "SOIC-8_3.9x4.9mm_P1.27mm", "SOT-23", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
          "LQFP-48_7x7mm_P0.5mm", "TSSOP-20_4.4x6.5mm_P0.65mm", "SOT-223-3_TabPin2", "SOT-23-5", "D_SOD-123",
          "BGA-256_17.0x17.0mm_Layout16x16_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD", "JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal"]


def test_registration_recovers_any_turn_and_offset():
    rng = random.Random(104)
    n = 0
    for name in SHAPES:
        fp = ours(name)
        for turn in (0, 90, 180, 270):
            for _ in range(3):
                dx, dy = rng.uniform(-2, 2), rng.uniform(-2, 2)
                th = moved(fp, turn, dx, dy, noise=0.004, rng=rng)
                a = A.assess(fp, th, PROF, polarized=False)
                assert a["pads"] == "identical" and a["rotation"] == "verified", (name, turn, a["pads"], a["reasons"])
                assert a["turn"] == turn, (name, turn, a["turn"])
                ex = A.rot(-dx, -dy, -turn)                              # their origin, in our frame
                assert math.hypot(a["offset"][0] - ex[0], a["offset"][1] - ex[1]) < 0.01, (name, turn, a["offset"], ex)
                assert A.correction(a) == (-turn) % 360
                n += 1
    assert n == len(SHAPES) * 12


def test_registration_off_grid_turns_and_noise():
    rng = random.Random(7)
    fp = ours("SOIC-8_3.9x4.9mm_P1.27mm")
    for turn in (30, 45, 135, 300):
        a = A.assess(fp, moved(fp, turn, 0.3, -0.2), PROF)
        assert abs(a["turn"] - turn) < 0.01 and a["pads"] == "identical", (turn, a["turn"], a["pads"])
    a = A.assess(fp, moved(fp, 90, noise=0.03, rng=rng), PROF)           # small drawing differences: compatible
    theirs45 = moved(fp, 45)                     # a fit a hair either side of 45 deg (libm differs per platform):
    for eps in (-1e-9, 0.0, 1e-9):               # our pads keep their pads' axes, nothing swaps
        class Fit:
            turn = 45 + eps

            def apply(self, x, y):
                return A.rot(x, y, self.turn)
        rows, lost_ours, lost_theirs, _ = A.compare(fp, theirs45, Fit(), PROF["tolerances"])
        assert not lost_ours and not lost_theirs and all(abs(r["dw"]) < 1e-6 and abs(r["dh"]) < 1e-6 for r in rows), (eps, rows[0])
    assert a["pads"] == "compatible" and a["turn"] == 90, (a["pads"], a["reasons"])


def test_mirror_renumbering_and_pin_count_are_incompatible():
    fp = ours("SOIC-8_3.9x4.9mm_P1.27mm")
    a = A.assess(fp, moved(fp, 90, mirror=True), PROF)                    # numbers run the other way round
    assert a["pads"] == "incompatible" and a["rotation"] == "unknown" and a["hold"], a
    assert any("other way round" in r or "carry other numbers" in r for r in a["reasons"]), a["reasons"]
    ren = moved(fp, 0)
    swap = {"1": "4", "2": "3", "3": "2", "4": "1", "5": "8", "6": "7", "7": "6", "8": "5"}
    for p in ren.pads:
        p.num = swap[p.num]
    a = A.assess(fp, ren, PROF)
    assert a["pads"] == "incompatible", (a["pads"], a["reasons"])
    short = moved(fp, 0)
    short.pads = [p for p in short.pads if p.num != "8"]
    a = A.assess(fp, short, PROF)
    assert a["pads"] == "incompatible" and any("pin count differs" in r for r in a["reasons"]), a["reasons"]
    fine = moved(fp, 0)
    for p in fine.pads:                                                  # pitch 1.27 -> 1.0: pads miss their lands
        p.y *= 1.0 / 1.27
    a = A.assess(fp, fine, PROF)
    assert a["pads"] == "incompatible" and any("do not line up" in r for r in a["reasons"]), a["reasons"]
    narrow = moved(fp, 0)
    for p in narrow.pads:                                                # rows 1.3 mm closer: another package width
        p.x *= (abs(p.x) - 0.65) / abs(p.x)
    a = A.assess(fp, narrow, PROF)
    assert a["pads"] == "incompatible" and any("wrong-size lands" in r and "row to row" in r for r in a["reasons"]), a["reasons"]


def test_risky_pad_differences():
    fp = ours("SOIC-8_3.9x4.9mm_P1.27mm")
    long_ = moved(fp, 0)
    for p in long_.pads:                                                 # their lands much longer than ours
        p.h *= 1.8
    a = A.assess(fp, long_, PROF)
    assert a["pads"] == "risky" and a["rotation"] == "verified" and not a["hold"], (a["pads"], a["reasons"])
    assert A.assess(fp, long_, PROF, strict=True)["hold"]                # [assembly] strict holds risky pads
    q = ours("QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm")
    no_ep = A.Footprint("QFN no EP", [p for p in q.pads if p.num != "33"])
    a = A.assess(no_ep, q, PROF)
    assert a["pads"] == "risky" and any("exposed pad" in r for r in a["reasons"]), (a["pads"], a["reasons"])


# ---------------------------------------------------------------- real parts and real mismatches
SYMBOL_PINS = {"cathode": {"1": "K", "2": "A"}, "positive": {"1": "+", "2": "-"}}   # a symbol that names its pins


def named(fp, kind):
    """fp with the pin names a symbol gives its pads (KiCad's Device:LED / D: 1 = K, 2 = A), as the librarian and a
    KiCad board pass them."""
    for q in fp.pads:
        q.func = SYMBOL_PINS[kind].get(q.num, "")
    return fp


def test_golden_set_agreement():
    # the turn and the pad verdict agree on every golden part. A polarized part is 'verified' only when both sides
    # name the pins; a bare library footprint (no symbol) gets its cathode / + from its drawing or ecadlib's pad-1
    # rule, so its turn is measured but its polarity 'assumed', with a review item
    g = json.loads((FIX / "golden.json").read_text(encoding="utf-8"))["parts"]
    assert len(g) >= 60 and sum(p["polarized"] for p in g) >= 15
    agree, rows, verified = 0, [], 0
    for p in g:
        a = A.assess(ours(p["ours"]), theirs(p["lcsc"]), PROF, p["lcsc"])
        ok = A.correction(a) == p["correction"] and a["pads"] == p["pads"] and a["rotation"] == ("assumed" if p["polarized"] else "verified")
        ok = ok and (not p["polarized"] or any("polarity assumed" in r for r in a["review"])) and (p["polarized"] or not a["review"])
        if p["polarized"]:
            b = A.assess(named(ours(p["ours"]), "positive" if p["ours"].startswith("CP_") else "cathode"), theirs(p["lcsc"]), PROF, p["lcsc"])
            want = "verified" if A.named_polarity(theirs(p["lcsc"])) else "assumed"
            ok = ok and A.correction(b) == p["correction"] and b["pads"] == p["pads"] and b["rotation"] == want
            verified += b["rotation"] == "verified"
        agree += ok
        rows.append((p["lcsc"], p["ours"], A.correction(a), p["correction"], a["pads"], p["pads"], a["rotation"], ok))
    bad = [r for r in rows if not r[-1]]
    assert not bad, f"golden set: {agree}/{len(g)} agree; wrong: {bad}"
    assert verified == 19, verified                                      # every diode / LED: JLCPCB names K / A


def test_family_table_matches_every_golden_part():
    # offline (no assembler footprint) the 0 deg rule of the family gives the rotation: it must give the golden answer
    # on every golden-set part of that family, and a family without a rule must say why ('measured')
    g = json.loads((FIX / "golden.json").read_text(encoding="utf-8"))["parts"]
    seen = {}
    for p in g:
        fp = ours(p["ours"])
        a = A.assess(fp, None, PROF, p["lcsc"], names=[theirs(p["lcsc"]).package], polarized=p["polarized"])
        if (PROF["families"].get(a["family"] or "") or {}).get("zero"):
            assert a["rotation"] == "table" and A.correction(a) == p["correction"], (p["lcsc"], a["family"], A.correction(a), a["notes"])
            seen[a["family"]] = seen.get(a["family"], 0) + 1
    assert all(n >= 2 for n in seen.values()) and len(seen) >= 10, seen
    for name, fam in PROF["families"].items():
        assert fam.get("zero") or fam.get("measured") or name == "two_pin", name


def test_kicad_0402_against_jlcpcb_0402_is_risky():
    a = A.assess(ours("R_0402_1005Metric"), theirs("C25744"), PROF, "C25744")
    assert a["pads"] == "risky" and A.correction(a) == 0 and a["rotation"] == "verified", a
    assert any("pad spacing differs" in r and "+18%" in r for r in a["reasons"]), a["reasons"]
    a = A.assess(ours("R_0603_1608Metric"), theirs("C25804"), PROF, "C25804")
    assert a["pads"] == "compatible", a["reasons"]                       # KiCad 0603 is within JLCPCB's tolerance


def test_led_cathode_found_by_pin_names_not_pad_numbers():
    th = theirs("C2286")                                                 # JLCPCB numbers this LED's cathode pad 2
    assert {p.num: p.func for p in th.pins} == {"1": "A", "2": "K"}
    a = A.assess(ours("LED_0603_1608Metric"), th, PROF, "C2286")         # KiCad: cathode = pad 1 (KLC), its silk bar
    assert A.correction(a) == 0 and a["rotation"] == "assumed" and any("polarity" in n for n in a["notes"]), a
    assert any("polarity mark on our footprint" in r for r in a["review"]), a["review"]
    a = A.assess(named(ours("LED_0603_1608Metric"), "cathode"), th, PROF, "C2286")   # the symbol's K / A: measured
    assert A.correction(a) == 0 and a["rotation"] == "verified" and not a["review"], a
    rev = ours("LED_0603_1608Metric")                                    # a footprint whose cathode is on the right
    for p in rev.pads:
        p.func = {"1": "A", "2": "K"}.get(p.num, "")
    a = A.assess(rev, th, PROF, "C2286")
    assert A.correction(a) == 180 and a["rotation"] == "verified", (A.correction(a), a["notes"])
    blind = A.Footprint("no names", [A.Pad(p.num, p.x, p.y, p.w, p.h, p.rot, p.shape, p.kind) for p in th.pads])
    a = A.assess(ours("LED_0603_1608Metric"), blind, PROF, "C2286")      # nothing tells their cathode: never guessed
    assert a["rotation"] == "table" and A.correction(a) == 0 and a["pads"] == "compatible", a   # the family's pose,
    assert any("placement preview" in r for r in a["review"]) and any("which pad is the cathode" in n for n in a["notes"]), a
    a = A.assess(ours("LED_0603_1608Metric"), None, PROF)                 # table: polarized parts always get a review
    assert a["rotation"] == "table" and any("placement preview" in r for r in a["review"]), a


def test_sot23_with_pin1_in_the_other_corner_is_incompatible():
    fp = ours("SOT-23")
    other = moved(fp, 0)
    for p in other.pads:                                                 # pins 1 and 2 swap corners
        p.num = {"1": "2", "2": "1"}.get(p.num, p.num)
    a = A.assess(fp, other, PROF)
    assert a["pads"] == "incompatible" and a["hold"], (a["pads"], a["reasons"])


def test_polarity_read_off_the_assemblers_filled_silk_marks():
    # JLCPCB's pins on these carry no names: the band of a tantalum and the '+' of an electrolytic are filled silk
    # shapes, which tell the + end (decided by eye on each drawing, see golden.json)
    for lcsc, name, want in (("C11376", "CP_EIA-6032-28_Kemet-C", 180), ("C7195", "CP_EIA-3528-21_Kemet-B", 180),
                             ("C37308", "CP_Elec_6.3x5.4", 0), ("C3338", "CP_Elec_6.3x7.7", 180)):
        th = theirs(lcsc)
        assert th.marks and {p.func for p in th.pins} <= {"1", "2"}, lcsc
        a = A.assess(named(ours(name), "positive"), th, PROF, lcsc)
        assert A.correction(a) == want and a["rotation"] == "assumed", (lcsc, A.correction(a), a["notes"])   # a mark is
        assert any("polarity mark on the assembler's footprint" in r for r in a["review"]), a["review"]     # no pin name
    th = theirs("C11376")
    th.marks = []                                                        # without the band: never guessed
    a = A.assess(ours("CP_EIA-6032-28_Kemet-C"), th, PROF, "C11376")
    assert a["rotation"] == "unknown" and any("placement preview" in r for r in a["review"]), a


def test_diode_whose_assembler_numbers_the_cathode_pad_2():
    th = theirs("C22452")                                                # SS54: JLCPCB's pad 1 = A, pad 2 = K
    assert {p.num: p.func for p in th.pins} == {"1": "A", "2": "K"}
    a = A.assess(named(ours("D_SMA"), "cathode"), th, PROF, "C22452")
    assert A.correction(a) == 0 and a["rotation"] == "verified" and a["pads"] == "risky", a   # not the 180 by number
    for p in th.pads:                                                    # names gone: its silk bar still says, as an
        p.func = ""                                                      # assumption a person confirms
    a = A.assess(named(ours("D_SMA"), "cathode"), th, PROF, "C22452")
    assert A.correction(a) == 0 and a["rotation"] == "assumed" and a["review"], (A.correction(a), a["notes"])


def test_wrong_footprint_for_the_part_is_flagged():
    a = A.assess(ours("SOT-23"), theirs("C82942"), PROF, "C82942")      # SOT-23 lands for a SOT-23-5 regulator
    assert a["pads"] == "incompatible" and a["hold"] and a["rotation"] == "unknown", a
    a = A.assess(ours("D_SOD-123"), theirs("C8678"), PROF, "C8678")      # SOD-123 lands for an SMA diode: wrong size
    assert a["pads"] == "incompatible" and any("wrong-size lands" in r for r in a["reasons"]), a
    a = A.assess(ours("SOT-23"), theirs("C2135"), PROF, "C2135")         # the right one: JLCPCB's SOT-23 -BR pose
    assert a["pads"] == "compatible" and A.correction(a) == 180, a


def test_same_pin_on_two_numbers_is_not_a_renumbering():
    # SOT-223: KiCad numbers the tab 2, JLCPCB numbers it 4 and its own symbol names 4 and 2 both VOUT
    a = A.assess(ours("SOT-223-3_TabPin2"), theirs("C6186"), PROF, "C6186")
    assert a["pads"] == "risky" and A.correction(a) == 180 and not any("other numbers" in r for r in a["reasons"]), a
    th = theirs("C6186")
    for p in th.pads:
        if p.num == "4":
            p.func = "TAB"                                               # a different pin: a real renumbering
    a = A.assess(ours("SOT-223-3_TabPin2"), th, PROF, "C6186")
    assert a["pads"] == "incompatible" and any("ours 2 = theirs 4" in r for r in a["reasons"]), a
    # USB-C: KiCad stacks A1 and B12 on one land, JLCPCB numbers that land 'A1B12'
    usb = ours("USB_C_Receptacle_HRO_TYPE-C-31-M-12")
    a = A.assess(usb, theirs("C165948"), PROF, "C165948")
    assert a["pads"] == "compatible" and A.correction(a) == 0 and a["rotation"] == "verified", a
    th = theirs("C165948")
    for p in th.pads:                                                    # CC1 and DP1 swapped: caught
        p.num = {"A5": "A6", "A6": "A5"}.get(p.num, p.num)
    a = A.assess(usb, th, PROF, "C165948")
    assert a["pads"] == "incompatible" and any("ours A5 = theirs A6" in r for r in a["reasons"]), a
    assert A._aliases(theirs("C165948"))["A1B12"] >= {"A1", "B12"}


def test_kicad_custom_and_paste_split_pads():
    sot89 = {p.num: p for p in ours("SOT-89-3").pads}                    # pad 2 is a custom lead + tab shape
    assert sot89["2"].w > 4 and sot89["2"].rot == 0, sot89["2"]
    a = A.assess(ours("SOT-89-3"), theirs("C21583"), PROF, "C21583")
    assert A.correction(a) == 180 and a["pads"] == "risky" and not any("lacks" in r for r in a["reasons"]), a
    dpak = ours("TO-252-2")                                              # four copper + paste windows on the tab
    assert sorted(p.num for p in dpak.pads) == ["1", "2", "3"], [(p.num, p.w, p.h) for p in dpak.pads]
    a = A.assess(dpak, theirs("C99124"), PROF, "C99124")
    assert a["pads"] == "compatible" and A.correction(a) == 0, a


def test_bidirectional_and_square_lands():
    a = A.assess(ours("D_SOD-323"), theirs("C2827694"), PROF, "C2827694")   # PESD5V0S1BA, package ...-BI
    assert not a["polarized"] and a["rotation"] == "verified" and A.correction(a) == 0, a
    th = theirs("C2827694")
    th.package = th.package.replace("-BI", "")
    a = A.assess(ours("D_SOD-323"), th, PROF, "C2827694")                # a plain diode name: the family table,
    assert a["polarized"] and a["rotation"] == "table", a                # never the unmarked pads, and a person checks
    assert any("placement preview" in r for r in a["review"]), a
    a = A.assess(ours("D_SMA"), theirs("C2480"), PROF, "C2480")          # SS14's 1.52 x 1.68 mm lands: the lead runs
    assert a["pads"] == "compatible", a["reasons"]                       # along x, so 1.68 vs 1.80 is the width


def test_override_and_parameters():
    p = copy.deepcopy(PROF)
    p["overrides"] = [{"part": "C25804", "rotation": 90, "offset": [0.1, -0.2], "source": "golden-set", "note": "test"}]
    a = A.assess(ours("R_0603_1608Metric"), None, p, "C25804")
    assert a["rotation"] == "override" and A.correction(a) == 90 and a["offset"] == (0.1, 0.2), a
    par = A.parameters(a)
    assert list(par) == list(A.PARAMS)
    assert par["Assembly Rotation"] == "90" and par["Assembly Offset"] == "0.100, -0.200" and par["LCSC Part"] == "C25804"
    assert A.parse_offset(par["Assembly Offset"]) == (0.1, 0.2)
    a = A.assess(ours("SOIC-8_3.9x4.9mm_P1.27mm"), theirs("C5423"), PROF, "C5423")
    par = A.parameters(a)
    assert par["Assembly Rotation"] == "270" and par["Assembly Check"] == "pads compatible; rotation verified", par
    assert "SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL (C5423)" in par["Assembly Evidence"]
    assert th_meta()["part_class"] == "Basic Part" and th_meta()["credit"].startswith("JLCEDA/EasyEDA Official Library")


def th_meta():
    return theirs("C2286").meta


def test_overlay_picture():
    try:
        import PIL  # noqa: F401
    except ImportError:
        return
    with tempfile.TemporaryDirectory() as d:
        a = A.assess(ours("SOIC-8_3.9x4.9mm_P1.27mm"), theirs("C5423"), PROF, "C5423")
        f = A.render_overlay(ours("SOIC-8_3.9x4.9mm_P1.27mm"), theirs("C5423"), a, Path(d) / "x.png")
        from PIL import Image
        with Image.open(f) as im:                # closed before the folder goes (Windows keeps open files)
            assert im.size[0] >= 720 and im.size[1] > 300


# ---------------------------------------------------------------- fetching the assembler's footprint
class FakeHTTP:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, **kw):
        self.calls.append((url, headers))
        lc = url.split("/products/")[1].split("/")[0]
        f = EE / f"{lc}.json"
        return Response(200, f.read_text(encoding="utf-8")) if f.exists() else Response(404, "", error="HTTP 404")


def test_fetch_uses_the_easyeda_provider_and_respects_unofficial():
    http = FakeHTTP()
    fp, note = A.fetch_theirs("C25744", http, {"providers": {"unofficial": True}}, PROF)
    assert fp is not None and fp.package == "R0402" and not note
    url, h = http.calls[0]
    assert url == "https://easyeda.com/api/products/C25744/components?version=6.4.19.5" and h["Referer"] == "https://easyeda.com/"
    assert "User-Agent" not in h                                         # ecadlib's own client UA; never a browser's
    fp, note = A.fetch_theirs("C99999", http, None, PROF)
    assert fp is None and "404" in note
    http = FakeHTTP()
    fp, note = A.fetch_theirs("C25744", http, {"providers": {"unofficial": False}}, PROF)
    assert fp is None and "unofficial = false" in note and not http.calls


def test_unofficial_false_degrades_to_the_table():
    http = FakeHTTP()
    pls, info = IO.read_board(BOARDS / "two_sided.kicad_pcb")
    rep = IO.run_check(pls, PROF, IO.fetcher(http, {"providers": {"unofficial": False}}, PROF))
    assert not http.calls
    by = {p["ref"]: p for p in rep["parts"]}
    assert by["R1"]["rotation"] == "table" and by["R1"]["correction"] == 0 and by["R1"]["pads"] == "unchecked"
    assert by["U1"]["rotation"] == "table" and by["U1"]["correction"] == 270
    assert by["Q1"]["rotation"] == "table" and by["Q1"]["correction"] == 180
    assert any("unofficial = false" in n for n in by["R1"]["notes"])


# ---------------------------------------------------------------- boards
def _pos(name):
    return {r["Ref"]: r for r in csv.DictReader(open(BOARDS / f"{name}.pos.csv", encoding="utf-8"))}


def test_kicad_board_reader_matches_kicads_own_position_file():
    for name in ("two_sided", "page_origin"):
        pls, info = IO.read_board(BOARDS / f"{name}.kicad_pcb")
        pos, truth = _pos(name), json.loads((BOARDS / f"{name}.truth.json").read_text())
        assert info["origin"] == ((100.0, 100.0) if name == "two_sided" else (0.0, 0.0)), info
        for pl in pls:
            if pl.skip:
                assert pl.ref not in pos, pl.ref
                continue
            r = pos[pl.ref]
            assert abs(float(r["PosX"]) - pl.x) < 1e-6 and abs(float(r["PosY"]) - pl.y) < 1e-6, (pl.ref, r, pl.x, pl.y)
            assert (float(r["Rot"]) - pl.angle) % 360 == 0 and r["Side"] == pl.side, (pl.ref, r, pl.angle, pl.side)
            t = truth["parts"][pl.ref]                                   # pads, back in the library frame, land where
            for p in pl.fp.pads:                                         # pcbnew put them on the board
                px, py = (p.x, -p.y) if pl.side == "bottom" else (p.x, p.y)
                dx, dy = A.rot(px, py, pl.angle)
                wx, wy = t["at"][0] + dx, t["at"][1] + dy
                assert min(math.hypot(q["x"] - wx, q["y"] - wy) for q in t["pads"][p.num]) < 1e-4, (pl.ref, p.num)
        assert sorted(p.ref for p in pls if not p.skip) == sorted(pos)
    pls, _ = IO.read_board(BOARDS / "two_sided.kicad_pcb")
    assert {p.ref: p.skip for p in pls if p.skip} == {"R4": "excluded from position files"}
    d1 = next(p for p in pls if p.ref == "D1")
    assert {p.num: p.func for p in d1.fp.pads} == {"1": "K", "2": "A"}   # the schematic's pin names come along
    pls, info = IO.read_board(BOARDS / "two_sided.kicad_pcb", origin="page")
    assert info["origin"] == (0.0, 0.0) and next(p for p in pls if p.ref == "R1").y == -110.0


def _assembler_places(part, origin):
    """Where the assembler puts its footprint's pads (page frame, +y down), from the CPL row alone: the footprint at
    the CPL rotation about Mid X / Mid Y, seen from the top (Top) or from the bottom (Bottom, mirrored back)."""
    X, Y = part["mid_x"] + origin[0], -part["mid_y"] + origin[1]
    out = {}
    for q in part["_theirs"].pins:
        dx, dy = A.rot(q.x, q.y, part["cpl_rotation"])
        out[q.num] = (X + dx, Y + dy) if part["side"] == "top" else (X - dx, Y + dy)
    return out


def test_round_trip_assembler_pads_land_on_the_board_pads():
    for name in ("two_sided", "page_origin"):
        pls, info = IO.read_board(BOARDS / f"{name}.kicad_pcb")
        truth = json.loads((BOARDS / f"{name}.truth.json").read_text())
        rep = IO.run_check(pls, PROF, IO.recorded(EE))
        checked = 0
        for p in rep["parts"]:
            if p["_theirs"] is None or p["correction"] is None:
                continue
            ours_fp, th = p["_ours"], p["_theirs"]
            if p["polarized"]:                                           # their pad -> our pad by terminal
                ra, rb = A.roles(ours_fp, "cathode", True)[0], A.roles(th, "cathode")[0]
                pair = {nb: next(na for na, r in ra.items() if r == rb[nb]) for nb in rb}
            else:
                pair = {A._num(q.num): A._num(q.num) for q in th.pins}
            board = truth["parts"][p["ref"]]["pads"]
            for num, (x, y) in _assembler_places(p, info["origin"]).items():
                tgt = board[pair[A._num(num)]][0]
                d = math.hypot(tgt["x"] - x, tgt["y"] - y)
                assert d < 0.25, (name, p["ref"], num, round(d, 3), p["cpl_rotation"])
            checked += 1
        assert checked >= (13 if name == "two_sided" else 2), checked


def test_export_files_and_listed_parts():
    pls, info = IO.read_board(BOARDS / "two_sided.kicad_pcb")
    prof = json.loads(json.dumps(PROF))
    prof["families"]["sot23"]["zero"] = None                            # Q1 (no LCSC number) with no rule: unknown
    rep = IO.run_check(pls, prof, IO.recorded(EE))
    with tempfile.TemporaryDirectory() as d:
        files = IO.export(rep, info, PROF, d)
        assert [f.name for f in files] == ["two_sided-jlcpcb-bom.csv", "two_sided-jlcpcb-cpl.csv", "two_sided-jlcpcb-assembly.md",
                                           "two_sided-jlcpcb-assembly.json"]
        cpl = list(csv.reader(open(files[1], encoding="utf-8")))
        assert cpl[0] == ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]
        by = {r[0]: r for r in cpl[1:]}
        assert "R4" not in by and "Q1" not in by and len(by) == 14         # Q1: rotation unknown, left out
        assert by["U1"] == ["U1", "10.0000", "-45.0000", "Top", "270"]       # SOIC-8 -BL: +270 (JLCPCB's preview)
        assert by["U3"][4] == "0" and by["U2"][3:] == ["Bottom", "60"]       # 90 + 270; bottom: (180 - 30) + 270
        assert by["C1"][3:] == ["Bottom", "180"] and by["D1"][4] == "180" and by["R3"][4] == "45"
        bom = list(csv.reader(open(files[0], encoding="utf-8")))
        assert bom[0] == ["Comment", "Designator", "Footprint", "LCSC Part #"]
        rows = {r[1]: r for r in bom[1:]}
        assert rows["U2,U3"] == ["NE555", "U2,U3", "SOIC-8_3.9x4.9mm_P1.27mm", "C7593"]
        assert "Q1" not in rows and rows["R5"][3] == ""
        md = files[2].read_text(encoding="utf-8")
        assert "## Rotation unknown" in md and "Q1 (SOT-23)" in md and "## Left out of the BOM and CPL" in md and "## No LCSC part number" in md and "R5 (4k7" in md
        assert "## Pads risky" in md and "R3 C25744" in md and "JLCEDA/EasyEDA Official Library" in md
        js = json.loads(files[3].read_text(encoding="utf-8"))
        assert js["summary"]["placed"] == 15 and js["summary"]["no_lcsc"] == 2 and js["origin"] == [100.0, 100.0]


def test_altium_pick_and_place_gives_the_same_cpl():
    k_pls, k_info = IO.read_board(BOARDS / "two_sided.kicad_pcb")
    k = {p["ref"]: p for p in IO.run_check(k_pls, PROF, IO.recorded(EE))["parts"]}
    a_pls, a_info = IO.read_board(BOARDS / "two_sided_altium_pnp.csv")
    assert a_info["format"] == "Altium pick-and-place CSV" and len(a_pls) == 15
    assert {p.side for p in a_pls} == {"top", "bottom"} and all(p.point == "origin" for p in a_pls)
    lib = IO.footprints_from(K7)
    rep = IO.run_check(a_pls, PROF, IO.recorded(EE), library=lib)
    for p in rep["parts"]:
        q = k[p["ref"]]
        assert (p["mid_x"], p["mid_y"], p["cpl_rotation"], p["correction"]) == (q["mid_x"], q["mid_y"], q["cpl_rotation"], q["correction"]), \
            (p["ref"], p["mid_x"], p["mid_y"], p["cpl_rotation"], q["mid_x"], q["mid_y"], q["cpl_rotation"])
        if p["side"] == "bottom":
            assert any("Altium" in r and "unverified" in r for r in p["review"]), p["review"]
    no_lib = {p["ref"]: p for p in IO.run_check(a_pls, PROF, IO.recorded(EE))["parts"]}   # no pads: nothing guessed
    assert no_lib["U1"]["rotation"] == "unknown" and no_lib["U1"]["cpl_rotation"] == 0


def test_altium_file_in_mil_with_parameter_columns_and_a_bom():
    text = ("Altium Designer Pick and Place Locations\r\nUnits used in this file : Imperial (mil)\r\n\r\n"
            '"Designator","Footprint","Center-X(mil)","Center-Y(mil)","Layer","Rotation","Comment","Assembly Rotation","Assembly Offset"\r\n'
            '"U1","SOIC8","1000","2000","TopLayer","90","LM358","270","0.000, 0.100"\r\n'
            '"R1","R0603","500mil","500mil","BottomLayer","0","10k","",""\r\n')
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "pnp.csv"
        f.write_text(text, encoding="cp1252")
        pls, info = IO.read_board(f)
        u1, r1 = pls[1], pls[0]
        assert (u1.ref, round(u1.x, 4), round(u1.y, 4), u1.angle, u1.point) == ("U1", 25.4, 50.8, 90.0, "center")
        assert r1.side == "bottom" and round(r1.x, 4) == 12.7 and r1.tool == "altium"
        bom = Path(d) / "bom.csv"
        bom.write_text("Comment,Designator,LCSC Part\nLM358,U1,C5423\n10k,\"R1, R2\",C25804\n", encoding="utf-8")
        assert IO.merge_bom(pls, bom) == 2
        assert IO.lcsc_of(u1.fields, PROF) == "C5423" and IO.lcsc_of(r1.fields, PROF) == "C25804"
        rep = IO.run_check(pls, PROF, IO.recorded(EE))
        by = {p["ref"]: p for p in rep["parts"]}
        assert by["U1"]["rotation"] == "parameter" and by["U1"]["correction"] == 270 and by["U1"]["cpl_rotation"] == 0
        assert any("Center-X/Y used" in n for n in by["U1"]["notes"])     # Center: the offset is not applied
        assert by["R1"]["rotation"] == "unknown" and by["R1"]["cpl_rotation"] == 0


def test_cli_export_check_part_and_profiles():
    with tempfile.TemporaryDirectory() as d:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CLI.main(["export", str(BOARDS / "two_sided.kicad_pcb"), "-o", d, "--recorded", str(EE), "--cache", str(Path(d) / "c")])
        assert rc == 0, buf.getvalue()
        assert (Path(d) / "two_sided-jlcpcb-cpl.csv").exists() and any((Path(d) / "overlays").glob("*.png")) in (True, False)
        assert "No LCSC part number" in buf.getvalue() and "Q1 (MMBT3904, SOT-23)" in buf.getvalue()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CLI.main(["check", str(BOARDS / "two_sided.kicad_pcb"), "--strict", "--recorded", str(EE), "--json",
                           "--cache", str(Path(d) / "c")])
        js = json.loads(buf.getvalue())
        held = [x["ref"] for x in js["parts"] if x.get("hold")]
        assert rc == 1 and "R3" in held, (js["summary"], held)              # strict: risky R3 held
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CLI.main(["part", str(K7 / "R_0402_1005Metric.kicad_mod"), "--theirs", str(EE / "C25744.json"), "--json",
                           "--cache", str(Path(d) / "c")])
        js = json.loads(buf.getvalue())
        assert rc == 0 and js["pads"] == "risky" and js["parameters"]["LCSC Part"] == "C25744", js
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert CLI.main(["profiles"]) == 0
        assert "soic" in buf.getvalue() and "pin 1 / cathode BL" in buf.getvalue()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            assert CLI.main(["check", str(Path(d) / "nope.kicad_pcb"), "--cache", str(Path(d) / "c")]) == 2
        assert "no such file" in err.getvalue()


# ---------------------------------------------------------------- the library side
def test_librarian_annotation_release_holds_and_messages():
    from ecadlib import release_check as RC
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "models" / "KiCad").mkdir(parents=True)
        (Path(d) / "models" / "KiCad" / "R_0402.kicad_mod").write_text((K7 / "R_0402_1005Metric.kicad_mod").read_text(), encoding="utf-8")
        item = {"name": "RC0402-10K", "symbol": {}, "footprint": {"ref": "R_0402", "kicad_mod": "models/KiCad/R_0402.kicad_mod"},
                "parameters": [{"name": "Supplier 1", "value": "LCSC"}, {"name": "Supplier Part Number 1", "value": "C25744"}],
                "librarian": {"review": [], "tried": []}}
        assert A.item_lcsc(item, PROF) == "C25744"
        n = A.annotate_items([item], d, FakeHTTP(), {"providers": {"unofficial": True}, "assembly": {"check": True, "profile": "jlcpcb"}},
                             log=lambda *a: None)
        assert n == 1
        par = {p["name"]: p["value"] for p in item["parameters"]}
        assert par["Assembly Rotation"] == "0" and par["Assembly Check"] == "pads risky; rotation verified", par
        assert item["assembly"]["pads"] == "risky" and item["assembly"]["lcsc"] == "C25744"
        assert A.holds(item, {"assembly": {"strict": False}}) == []
        h = A.holds(item, {"assembly": {"strict": True}})
        assert len(h) == 1 and M.explain("assembly: " + h[0])["code"] == "assembly.risky", h
        fails, warns, _ = RC.check_item(item, d, None)
        assert not any(f[0] == "assembly" for f in fails) and any(w.startswith("assembly: pads risky") for w in warns), (fails, warns)
        item["assembly"]["pads"] = "incompatible"
        fails, _, _ = RC.check_item(item, d, None)
        msgs = [f[1] for f in fails if f[0] == "assembly"]
        assert msgs and M.explain("assembly: " + msgs[0])["code"] == "assembly.incompatible", fails
        assert "assembly" in RC.RULES
        off = {"name": "X", "parameters": [], "footprint": {}, "symbol": {}}
        assert A.annotate_items([off], d, FakeHTTP(), {"assembly": {"check": False}}) == 0


def test_assembly_parameters_never_void_a_sign_off():
    """The check's parameters are measurements repeated on every run: writing or changing them keeps a sign-off valid
    (an older workspace's approvals still count after the upgrade); any other parameter still voids it."""
    from ecadlib import approvals as AP
    assert AP.DERIVED_PARAMS == set(A.PARAMS)
    item = {"name": "R1", "symbol": {}, "footprint": {}, "parameters": [{"name": "Value", "value": "10k"}]}
    h = AP.content_hash(item)
    item["parameters"] += [{"name": k, "value": "x"} for k in A.PARAMS]
    assert AP.content_hash(item) == h
    item["parameters"][0]["value"] = "4k7"
    assert AP.content_hash(item) != h


def test_altium_menu_assembly_check_wants_a_pcb_project():
    # AD26 run 2026-10-19: with a script project focused the menu said "h47.PrjScr: no pick-and-place file yet"
    from ecadlib import altium_menu as AM
    with tempfile.TemporaryDirectory() as d:
        cfg = {"workspace": str(Path(d) / "ws"), "offline": True}
        r = AM.run_action(cfg, "assembly", {"project": str(Path(d) / "h47.PrjScr")})
        assert r["status"] == "error" and "not a PCB project" in r["message"] and ".PrjPcb" in r["fix"], r
        prj = Path(d) / "nopnp" / "nopnp.PrjPcb"
        prj.parent.mkdir()
        prj.write_text("[Design]\n", encoding="utf-8")
        r = AM.run_action(cfg, "assembly", {"project": str(prj)})
        assert r["status"] == "error" and "no pick-and-place file yet" in r["message"], r
