"""Assembly check (#104): regressions for the 2026-10-19 tester and critic review (docs/assembly.md). Each test names
the finding it covers. Run:  python3 -m tests.test_assembly_review_1019

Fixtures (tests/fixtures/assembly/review_1019, README.md there): the reviewers' small repro inputs. Boards that
differ from boards/two_sided.kicad_pcb by a line are made from it in a temporary folder."""
import contextlib, csv, io, re, tempfile
from pathlib import Path

from ecadlib import assembly as A, assembly_io as IO, assembly_cli as CLI
from ecadlib.httpcache import Response
from tests.test_assembly import ours, theirs, named, PROF, EE, K7, BOARDS, FakeHTTP

R19 = Path(__file__).parent / "fixtures" / "assembly" / "review_1019"


def mod(name):
    return A.from_kicad_mod((R19 / f"{name}.kicad_mod").read_text(encoding="utf-8"), name)


def board(d, name, edit):
    """two_sided.kicad_pcb with `edit` (text -> text) applied, written into folder d."""
    f = Path(d) / f"{name}.kicad_pcb"
    f.write_text(edit((BOARDS / "two_sided.kicad_pcb").read_text(encoding="utf-8")), encoding="utf-8")
    return f


def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = CLI.main(argv)
    return rc, buf.getvalue()


# ---------------------------------------------------------------- fix 1: polarity from a mark or pad 1 is assumed
def test_f1_polarity_from_pad1_rule_or_mark_is_assumed_not_verified():
    # F1: an Altium file has no pin names; LED_0603_A1 numbers the anode 1 and its silk marks neither end, so the
    # cathode comes from ecadlib's pad-1 rule: the turn is measured, never 'verified', and a person is asked
    pls, _ = IO.read_board(R19 / "led_a1_pnp.csv")
    rep = IO.run_check(pls, PROF, IO.recorded(EE), library={"LED_0603_A1": mod("LED_0603_A1")})
    d1 = next(p for p in rep["parts"] if p["ref"] == "D1")
    assert d1["rotation"] == "assumed" and d1["polarity"]["ours"] == A.HOW_RULE, d1
    assert any("polarity assumed" in r and "ecadlib's rule" in r for r in d1["review"]), d1["review"]
    assert any("Altium pick-and-place file carries no pin names" in n for n in d1["notes"]), d1["notes"]
    assert "polarity assumed" in A.evidence_text(d1) and IO.needs_a_look(d1)
    assert rep["summary"]["rotation"]["assumed"] == 2 and rep["summary"]["rotation"]["verified"] == 0


def test_n1_diode_arrow_points_at_the_cathode():
    # N1: a diode triangle points AT its cathode; its base is ink on the anode end and must not read as the mark
    for name in ("LED_0603_TRI", "LED_0603_SYM"):                     # triangle alone; triangle + bar
        fp = mod(name)
        assert A.frame(fp)["marked"] == "pin1", name                     # pad 1 (left) is the cathode
        a = A.assess(fp, theirs("C2286"), PROF, "C2286")
        assert A.correction(a) == 0 and a["rotation"] == "assumed", (name, A.correction(a), a["notes"])
    th = theirs("C9808")                                                 # LL4148: JLCPCB's filled arrow points at K
    assert A.frame(th)["marked"] == "pin1" and {p.num: p.func for p in th.pins} == {"1": "K", "2": "A"}
    a = A.assess(named(ours("D_MiniMELF"), "cathode"), th, PROF, "C9808")
    assert a["rotation"] == "verified" and A.correction(a) == 0 and not a["review"], a["review"]   # no false conflict
    for p in th.pads:
        p.func = ""
    a = A.assess(ours("D_MiniMELF"), th, PROF, "C9808")                  # names gone: the arrow alone still says 0
    assert A.correction(a) == 0 and a["rotation"] == "assumed", (A.correction(a), a["notes"])
    assert A.frame(theirs("C2297"))["marked"] == "pin2"                  # JLCPCB's LED0805 silk arrow: K is pad 2


def test_n1_our_pin_names_are_checked_against_our_mark():
    fp = named(ours("LED_0603_1608Metric"), "cathode")
    for p in fp.pads:                                                    # a symbol whose pin 1 is the anode, on a
        p.func = {"1": "A", "2": "K"}[p.num]                             # footprint whose bar marks pad 1
    a = A.assess(fp, theirs("C2286"), PROF, "C2286")
    assert any("our footprint's pin names and its polarity mark point at opposite ends" in r for r in a["review"]), a["review"]
    assert A.correction(a) == 180                                        # the names win, and a person checks


# ---------------------------------------------------------------- fix 2: polarized by its pin names
def test_f2_two_pin_part_polarized_by_its_pin_names():
    th = theirs("C2297")
    th.package = th.name = "D0805"                                       # F2: a diode family no rule names
    d0805 = mod("D_0805_2012Metric")
    assert A.family_of(PROF, ["D_0805_2012Metric", "D0805"], d0805) == "chip"     # the non-polarized chip family
    a = A.assess(d0805, th, PROF, "C2297")
    assert a["polarized"] and A.correction(a) == 0 and a["rotation"] == "assumed", (A.correction(a), a["notes"])
    a = A.assess(named(mod("D_0805_2012Metric"), "cathode"), th, PROF, "C2297")
    assert a["rotation"] == "verified" and A.correction(a) == 0, a["notes"]
    rev = mod("D_0805_2012Metric")
    for p in rev.pads:                                                   # cathode on pad 2: turned, not paired by number
        p.func = {"1": "A", "2": "K"}[p.num]
    assert A.correction(A.assess(rev, th, PROF, "C2297")) == 180
    bare = mod("D_0805_2012Metric")
    bare.silk, bare.fab = [], []                                         # (d) nothing on ours tells: never pad 1
    a = A.assess(bare, th, PROF, "C2297")
    assert a["rotation"] == "unknown" and A.correction(a) is None and any("placement preview" in r for r in a["review"]), a


def test_f2_pin1_kind_single_names_bidirectional_and_bipolar():
    # (a) B5819W: JLCPCB names its pins '+' / '-' with '-' at the band: a diode, so '-' is the cathode
    assert A.pin1_kind(["D_SOD-123", "SOD-123_L2.7-W1.6-LS3.7-RD-1"], ours("D_SOD-123"), theirs("C8598")) == "cathode"
    assert A.pin1_kind(["CP_EIA-3528-21_Kemet-B"]) == "positive" and A.pin1_kind(["X_2PIN"]) is None
    # (b) one pin named 'A' (or 'C', 'VSS') is no evidence: both pins must name opposite terminals
    fp = ours("R_0603_1608Metric")
    for p, f in zip(fp.pads, ("A", "2")):
        p.func = f
    assert not A.named_polarity(fp) and not A.assess(fp, theirs("C25804"), PROF, "C25804")["polarized"]
    for p, f in zip(fp.pads, ("VSS", "VDD")):
        p.func = f
    assert not A.named_polarity(fp)
    # (c) a bidirectional package (-BI) stays non-polarized
    assert not A.assess(ours("D_SOD-323"), theirs("C2827694"), PROF, "C2827694")["polarized"]
    # a bipolar electrolytic (KiCad C_Elec_*, no family) on a part with plain pin numbers stays non-polarized
    cap = ours("CP_Elec_8x10")
    cap.name = "C_Elec_8x10"
    th = theirs("C3340")
    th.package, th.marks = "CAP-BIPOLAR_8x10", []
    a = A.assess(cap, th, PROF, "C3340")
    assert not a["polarized"] and a["rotation"] == "verified", (a["polarized"], a["notes"])


# ---------------------------------------------------------------- fix 3: wrong-size lands
def test_n2_wrong_size_lands_are_incompatible():
    for lcsc, name in (("C17414", "R_0603_1608Metric"), ("C2297", "LED_0603_1608Metric"), ("C2099", "D_SOD-323"),
                       ("C7171", "LED_0603_1608Metric"), ("C97521", "SOIC-8_3.9x4.9mm_P1.27mm"),
                       ("C7195", "CP_EIA-3216-18_Kemet-A")):
        a = A.assess(ours(name), theirs(lcsc), PROF, lcsc)
        assert a["pads"] == "incompatible" and a["hold"] and any("wrong-size lands" in r for r in a["reasons"]), (lcsc, name, a["reasons"])
    for lcsc, name, want in (("C25744", "R_0402_1005Metric", "risky"), ("C7195", "CP_EIA-3528-21_Kemet-B", "risky"),
                             ("C11376", "CP_EIA-6032-28_Kemet-C", "risky"), ("C22452", "D_SMA", "risky"),
                             ("C2135", "SOT-23", "compatible"), ("C15127", "SOT-23", "compatible")):
        a = A.assess(ours(name), theirs(lcsc), PROF, lcsc)              # the golden set's worst correct pairings
        assert a["pads"] == want, (lcsc, name, a["pads"], a["reasons"])
    assert PROF["tolerances"]["incompatible_span_share"] == 0.3 and PROF["tolerances"]["incompatible_span_cover"] == 0.55


# ---------------------------------------------------------------- fix 4: the parameter path
def test_n3_stored_parameters_are_tied_to_the_part_and_never_give_a_pad_verdict():
    pls, _ = IO.read_board(R19 / "tant_pnp.csv")
    rep = IO.run_check(pls, PROF, None, library=IO.footprints_from(K7))
    c1 = next(p for p in rep["parts"] if p["ref"] == "C1")
    assert c1["rotation"] == "parameter" and c1["pads"] == "unchecked", c1      # 'pads compatible' is not taken over
    assert any("Assembly Rotation parameter" in r and "placement preview" in r for r in c1["review"]), c1["review"]
    pls, _ = IO.read_board(R19 / "param_pnp.csv")
    for p in pls:
        if p.ref == "U4":
            p.fields["Assembly Rotation"] = "ninety"
        if p.ref == "Q1":                                                 # measured for another part: not used
            p.fields.update({"Assembly Rotation": "90", "Assembly Evidence": "jlcpcb footprint SOT-23-5 (C7666); fit 0.01 mm RMS"})
    by = {p["ref"]: p for p in IO.run_check(pls, PROF, None, library=IO.footprints_from(K7))["parts"]}
    assert by["U2"]["rotation"] == "parameter" and by["U2"]["correction"] == 90       # '90°'
    assert by["U3"]["rotation"] == "parameter" and by["U3"]["correction"] == 270      # '+270'
    assert by["U1"]["pads"] == "unchecked"
    assert by["U4"]["rotation"] == "table" and any("'ninety' is not a number" in r for r in by["U4"]["review"]), by["U4"]
    assert by["Q1"]["rotation"] == "unknown" and any("measured for C7666" in r for r in by["Q1"]["review"]), by["Q1"]
    pl = IO.Placement("U9", footprint="SOIC-8_3.9x4.9mm_P1.27mm", fields={
        "LCSC Part": "C5423", "Assembly Rotation": "270",
        "Assembly Evidence": "jlcpcb footprint SOIC-8 (C5423) on SOIC-8_5.23x5.23mm_P1.27mm; fit 0.01 mm RMS"})
    assert IO._param_assessment(pl, PROF, "C5423")[0] is None             # measured on another footprint


# ---------------------------------------------------------------- fix 5: footprints not fetched
class DownHTTP:
    def get(self, url, **kw):
        return Response(0, "", error="ConnectionError: the network is down")


def test_f3_parts_not_checked_are_named_and_strict_holds():
    pls, _ = IO.read_board(BOARDS / "two_sided.kicad_pcb")
    rules = {"providers": {"unofficial": True}}
    rep = IO.run_check(pls, PROF, IO.fetcher(DownHTTP(), rules, PROF))
    (why, refs), = IO.not_checked(rep)
    assert "network is down" in why and len(refs) == 13 and "Q1" not in refs, (why, refs)
    assert rep["summary"]["not_fetched"] == 13 and not rep["summary"]["hold"]
    rep = IO.run_check(pls, PROF, IO.fetcher(DownHTTP(), rules, PROF), strict=True)
    assert all(any("pads unchecked ([assembly] strict)" in h for h in p["hold"]) for p in rep["parts"] if p["lcsc"])
    rep = IO.run_check(pls, PROF, IO.fetcher(None, {"providers": {"unofficial": False}}, PROF))
    assert any("unofficial = false" in w for w, _ in IO.not_checked(rep))
    with tempfile.TemporaryDirectory() as d:
        f = board(d, "typo", lambda t: t.replace('"C25744"', '"C99999999"'))
        rep = IO.run_check(IO.read_board(f)[0], PROF, IO.fetcher(FakeHTTP(), rules, PROF))
        r3 = next(p for p in rep["parts"] if p["ref"] == "R3")
        assert r3["not_found"] and any(t.startswith("LCSC number not found") for t, _ in IO.listed(rep))
        res = IO.menu_check(f, Path(d) / "out", {"providers": {"unofficial": True}, "assembly": {}}, FakeHTTP(), opener=False)
        assert "nothing needs a look" not in res["message"], res["message"]
        assert "LCSC number not found" in Path(res["report"]).read_text(encoding="utf-8")
        rc, out = run(["check", str(BOARDS / "two_sided.kicad_pcb"), "--offline", "--cache", str(Path(d) / "empty")])
        line = next(ln for ln in out.splitlines() if ln.startswith("pads not checked"))
        assert rc == 0 and "offline: not in cache" in line and "C1, C2, C3, D1" in line, out
        rc, out = run(["check", str(BOARDS / "two_sided.kicad_pcb"), "--offline", "--cache", str(Path(d) / "empty"), "--strict"])
        assert rc == 1 and "pads unchecked ([assembly] strict)" in out, out


# ---------------------------------------------------------------- fix 6: LCSC fields
def test_n4_conflicting_lcsc_fields_are_held_and_supplier_pairs_read():
    with tempfile.TemporaryDirectory() as d:
        f = board(d, "twolcsc", lambda t: t.replace('(property "LCSC" "C2286")', '(property "LCSC" "C2286")\n    (property "LCSC Part" "C7171")', 1))
        rep = IO.run_check(IO.read_board(f)[0], PROF, IO.recorded(EE))
        d1 = next(p for p in rep["parts"] if p["ref"] == "D1")
        assert any("conflicting LCSC numbers" in h and "C7171" in h and "C2286" in h for h in d1["hold"]), d1["hold"]
        assert not d1["in_files"] and not any("D1" in r[1] for r in IO.bom_rows(rep, PROF)[1])
    assert IO.lcsc_of({"Supplier 1": "Mouser", "Supplier Part Number 1": "x", "Supplier 2": "LCSC",
                       "Supplier Part Number 2": "c25804"}, PROF) == "C25804"
    assert IO.lcsc_problems({"LCSC Part": "C25804", "Supplier 1": "JLCPCB", "Supplier Part Number 1": "C25744"}, PROF)
    assert any("not an LCSC part number" in p for p in IO.lcsc_problems({"LCSC": "25804"}, PROF))


# ---------------------------------------------------------------- fixes 7 and 8: what goes into the files
def test_f5_f8_duplicates_held_and_held_parts_left_out_of_the_files():
    with tempfile.TemporaryDirectory() as d:
        f = board(d, "dupref", lambda t: t.replace('(fp_text reference "R2"', '(fp_text reference "R1"', 1))
        pls, info = IO.read_board(f)
        rep = IO.run_check(pls, PROF, IO.recorded(EE))
        r1 = [p for p in rep["parts"] if p["ref"] == "R1"]
        assert len(r1) == 2 and all(any("designator R1 is on 2 parts" in h for h in p["hold"]) for p in r1)
        files = IO.export(rep, info, PROF, Path(d) / "out")
        cpl = [r[0] for r in csv.reader(open(files[1], encoding="utf-8"))]
        bom = files[0].read_text(encoding="utf-8")
        assert "R1" not in cpl and not re.search(r"\bR1\b", bom), (cpl, bom)
        md = files[2].read_text(encoding="utf-8")
        assert "## Left out of the BOM and CPL" in md and "R1 (R_0603_1608Metric): held" in md
        res = IO.menu_check(f, Path(d) / "menu", {"providers": {"unofficial": False}, "assembly": {}}, None, opener=False)
        assert res["status"] == "held" and "left out of the CPL" in res["message"], res["message"]
        assert "Left out of the BOM and CPL" in Path(res["report"]).read_text(encoding="utf-8")


def test_f4_altium_short_rows_and_empty_rotation_are_held_not_dropped():
    pls, _ = IO.read_board(R19 / "short_row.csv")
    by = {p.ref: p for p in pls}
    assert set(by) == {"R1", "R2", "R3"} and "cut short" in by["R2"].problem and "Rotation cell is empty" in by["R3"].problem
    rep = IO.run_check(pls, PROF, None, library=IO.footprints_from(K7))
    assert [r[0] for r in IO.cpl_rows(rep, PROF)[1]] == ["R1"]
    assert {ln.split(":")[0] for t, rows in IO.listed(rep) if t.startswith("Held") for ln in rows} == {"R2", "R3"}


def test_f6_bom_lines_stay_under_2048_bytes():
    text = "Designator,Comment,Layer,Footprint,Ref-X(mm),Ref-Y(mm),Rotation,LCSC Part\n" + "".join(
        f"C{i},100n,TopLayer,C_0402_1005Metric,{i},0,0,C1525\n" for i in range(1, 501))
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "pnp.csv"
        f.write_text(text, encoding="utf-8")
        rep = IO.run_check(IO.read_board(f)[0], PROF, IO.recorded(EE), library=IO.footprints_from(K7))
        cols, rows = IO.bom_rows(rep, PROF)
        assert len(rows) == 2 and all(IO._line_bytes(r) <= 2048 for r in rows), [IO._line_bytes(r) for r in rows]
        refs = [x for r in rows for x in r[1].split(",")]
        assert len(refs) == 500 and len(set(refs)) == 500


# ---------------------------------------------------------------- F10: messages
def test_f10_messages():
    with tempfile.TemporaryDirectory() as d:
        e = Path(d) / "empty.kicad_pcb"
        e.write_text("(kicad_pcb (version 20221018) (generator pcbnew))\n", encoding="utf-8")
        rc, out = run(["check", str(e), "--recorded", str(EE), "--cache", str(Path(d) / "c")])
        assert rc == 0 and "no parts to place" in out and "pads ;" not in out, out
        rc, out = run(["part", str(K7 / "R_0603_1608Metric.kicad_mod"), "--lcsc", "25804", "--cache", str(Path(d) / "c")])
        assert rc == 2 and "not an LCSC part number" in out, out
    pls, _ = IO.read_board(R19 / "led_a1_pnp.csv")                     # positions only: says why, not 'no rule'
    rep = IO.run_check(pls, PROF, IO.recorded(EE))
    unknown = dict(IO.listed(rep))["Rotation unknown: not in the CPL"]
    assert all("give --footprints" in ln for ln in unknown), unknown


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_assembly_review_1019"]))
