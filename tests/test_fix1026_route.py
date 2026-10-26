"""Fixes 2026-10-26 (route): review route.md R-RT-1..5 on PR #196. Offline: no key, no network, no model.
    python3 -m tests.run_all test_fix1026_route"""
from ecadlib import ds_route as DR
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- R-RT-1
OVERLAP = "pad_table: pads 1 and 2 overlap by 0.700 mm"


def test_rrt1_a_hard_failure_is_held_even_when_a_field_is_also_unresolved():
    """assess() calls a read 'incomplete' whenever a field is unresolved, and the hold only looked at 'failed': a read
    whose pads overlap was built as long as one field was also left blank (pitch doubled + one unresolved field)."""
    res = {"needs_review": [OVERLAP], "spec": {"unresolved": ["lead_span (not printed)"]}}
    assert DR.assess(res)[0] == "incomplete"
    assert DR.hold_reasons(res) == [OVERLAP]
    res3 = {"needs_review": [OVERLAP, "pin 3: has no source page"], "spec": {}}
    assert DR.hold_reasons(res3) == [OVERLAP]
    # the incomplete lines themselves never hold, nor does an unresolved field named like a failed check
    res4 = {"needs_review": ["pin 3: has no source page"], "spec": {"unresolved": ["lead_span min (not printed)"]}}
    assert DR.hold_reasons(res4) == []
    # a soft line (self-check) next to an unresolved field still does not hold, as for a failed read
    res5 = {"needs_review": ["self-check: pitch 0.45 is not a standard pitch"], "spec": {"unresolved": ["lead_span"]}}
    assert DR.hold_reasons(res5) == []
    # fp_needed=False: only the symbol's hard lines count, incomplete or not
    assert DR.hold_reasons(res, fp_needed=False) == []


def test_rrt1_an_incomplete_answer_with_a_hard_failure_ranks_below_a_plain_incomplete_one():
    """The route keeps its best answer: an incomplete one that also fails a hard check ranks with the failed ones,
    below an incomplete answer that fails nothing."""
    unres = "unresolved: lead_span (not printed)"
    assert DR.effective_kind("incomplete", [unres, OVERLAP]) == "failed"
    assert DR.effective_kind("incomplete", [unres]) == "incomplete"
    assert DR.effective_kind("incomplete", [unres, "self-check: pitch 0.45 is not a standard pitch"]) == "incomplete"
    assert DR.effective_kind("ok", []) == "ok"
    from ecadlib.llm import run as LR
    # the retry (second) is better when the first had a hard failure and the retry is only incomplete
    assert LR._better("incomplete", [unres, OVERLAP], "incomplete", [unres, "pin 3: has no source page"])
    assert not LR._better("incomplete", [unres], "incomplete", [unres, OVERLAP])


@lite
def test_rrt1_the_route_keeps_the_plain_incomplete_answer_over_one_with_overlapping_pads(tmp_path, monkeypatch):
    """Reader._walk: answer 1 incomplete and clean, answer 2 incomplete with overlapping pads (fewer gaps): keep 1."""
    from ecadlib import datasheet as D
    rd = D.Reader(tmp_path / "out", tmp_path / "cache", log=None)
    verdicts = {"k1": ("incomplete", ["unresolved: a, b", "pin 3: has no source page", "pin 4: has no source page"]),
                "k2": ("incomplete", ["unresolved: a", OVERLAP])}
    monkeypatch.setattr(rd, "_judge", lambda job, rec: verdicts[job["key"]] + ({"spec": {"unresolved": []}},))
    rd.results = {"k1": {"message": {}}, "k2": {"message": {}}}
    steps = {(0, 0): "k1", (0, 1): "k2"}

    def at(job, rung, level):
        return dict(job, rung=rung, level=level, key=steps[(rung, level)], final=False)
    monkeypatch.setattr(rd, "_at", at)
    monkeypatch.setattr(rd, "_package_retry", lambda *a: None)
    rd.router = DR.Router({"max_attempts": 2})
    job = {"key": "k1", "rung": 0, "level": 0, "levels": [1, 2], "model": "m", "offset": [0],
           "route": {"attempts": []}}
    out = rd._walk(job)
    assert out["final"] is True and out["key"] == "k1", out


# ---------------------------------------------------------------- R-RT-3
def test_rrt3_a_second_read_that_names_no_pin_does_not_agree_on_pin_names():
    """AQY282GS's cached second read gave all four names empty: the LED anode / cathode swap was built 'agreed'.
    Empty names mean the names were not compared, never that they agreed."""
    from ecadlib import crosscheck as CC
    spec = CC.critical({"package_type": "gullwing", "pin_count": 4, "pitch": 2.54})
    read = [("1", "LED_K"), ("2", "LED_A"), ("3", "OUT"), ("4", "OUT")]
    blank = [("1", ""), ("2", ""), ("3", ""), ("4", "")]
    rec = CC.against_read(spec, dict(spec), read, blank)
    assert "pin_names" not in rec["agreed"] and "pin_names" in rec["not_compared"], rec
    assert "pins" in rec["agreed"]
    # one name of four on the second read is still not a comparison of the names
    rec = CC.against_read(spec, dict(spec), read, [("1", "LED_K"), ("2", ""), ("3", ""), ("4", "")])
    assert "pin_names" not in rec["agreed"] and "pin_names" in rec["not_compared"], rec
    # names on both sides: compared and agreed as before
    rec = CC.against_read(spec, dict(spec), read, list(read))
    assert "pin_names" in rec["agreed"] and "pin_names" not in rec["not_compared"], rec
    # a real difference among the few named pins is still a disagreement
    rec = CC.against_read(spec, dict(spec), read, [("1", "LED_A"), ("2", ""), ("3", ""), ("4", "")])
    assert rec["status"] == "disagreed" and any(d["field"] == "pin_names" for d in rec["disagreed"]), rec


# ---------------------------------------------------------------- R-RT-2
SOIC8 = {"name": "SOIC-8_3.9x4.9mm_P1.27mm", "package_type": "gullwing", "pin_count": 8, "pitch": 1.27,
         "body_length": {"min": 4.8, "nom": 4.9, "max": 5.0}, "body_width": {"min": 3.8, "nom": 3.9, "max": 4.0},
         "lead_span": {"min": 5.8, "nom": 6.0, "max": 6.2}, "terminal_length": 0.6, "terminal_width": 0.42}
NAMES = ["GND", "VIN", "EN", "NC", "FB", "SW", "BST", "VOUT"]          # VIN and GND swapped: nothing compares them


class _NoSecondRead:
    cfg = {}
    effort = "high"

    def check_message(self, *a, **k):
        raise AssertionError("no second read may be made in this round (a cost decision)")


def _res(names=NAMES):
    return {"spec": dict(SOIC8), "symbol": {"pins": [{"num": str(i + 1), "name": n} for i, n in enumerate(names)]}}


def test_rrt2_a_standard_table_match_never_says_the_pin_names_were_checked():
    """A strong table match accepts the read without a second read, but the table compares only dimensions: the
    record must say the pin names were not compared, and the review must say so (no new paid read is added)."""
    from ecadlib import crosscheck as CC
    rec = CC.run(_NoSecondRead(), {"part": "X", "package": "SOIC-8", "fp_needed": True}, _res(), "m")
    assert rec["status"] in ("agreed", "self-checked"), rec
    assert rec["pins"] == "unchecked", rec
    assert "pin names" in CC.summary_line(rec) and "not compared" in CC.summary_line(rec)
    assert any("pin names" in ln and "not compared" in ln for ln in CC.report_md(rec))


def test_rrt2_a_symbol_only_read_skipped_in_auto_marks_its_pin_names_unchecked():
    from ecadlib import crosscheck as CC
    rec = CC.run(_NoSecondRead(), {"part": "X", "package": "SOT-23", "fp_needed": False}, _res(["G", "S", "D"]), "m")
    assert rec["status"] == "skipped" and rec["pins"] == "unchecked", rec
    assert "pin names" in CC.summary_line(rec)


def test_rrt2_pins_are_agreed_only_when_a_second_read_named_them():
    from ecadlib import crosscheck as CC
    first = CC.critical(SOIC8)
    pins = [(str(i + 1), n) for i, n in enumerate(NAMES)]

    class Second(_NoSecondRead):
        def __init__(self, other):
            self.other, self.cfg = other, {"cross_check": "always"}

    import ecadlib.crosscheck as M
    for other, want in ((pins, "agreed"), ([(n, "") for n, _ in pins], "unchecked")):
        orig = M.second_read
        M.second_read = lambda reader, job, f, fp, model, other=other: dict(
            M.against_read(f, dict(f), fp, other), source="second read", against="second read (m)", model="m")
        try:
            rec = CC.run(Second(other), {"part": "X", "package": "SOIC-8", "fp_needed": True}, _res(), "m")
        finally:
            M.second_read = orig
        assert rec["pins"] == want, (want, rec)


@lite
def test_rrt2_the_read_review_and_the_librarian_record_say_the_pin_names_are_unchecked(tmp_path, monkeypatch):
    """datasheet: a cross-check that 'agreed' on the package but compared no pin names puts a review line on the read;
    the librarian keeps pins: unchecked with the part's cross-check record."""
    from ecadlib import crosscheck as CC, datasheet as D
    rec = {"mode": "auto", "status": "agreed", "pins": "unchecked", "checks": []}
    lines = D.cross_check_review(rec)
    assert any("pin names" in ln and "not compared" in ln for ln in lines), lines
    from ecadlib import ds_route as DR
    for ln in lines:                                  # a review note, never a failed or incomplete check
        assert not DR.FAILED.search(ln) and not DR.INCOMPLETE.search(ln), ln
    assert D.cross_check_review({"mode": "auto", "status": "agreed", "pins": "agreed", "checks": []}) == []
    from ecadlib import librarian as L
    assert L.cross_check_state(rec) == {"status": "agreed", "summary": CC.summary_line(rec), "pins": "unchecked"}
    assert "pins" not in L.cross_check_state(dict(rec, pins="agreed"))


# ---------------------------------------------------------------- R-RT-4
@lite
def test_rrt4_a_symbol_gone_from_its_library_is_held_as_a_footprint_gone_is(tmp_path):
    """project check: a symbol renamed away in a library that is there passed (exit 0) while the same for a footprint
    held. An update cannot find either, so both hold."""
    from pathlib import Path
    from ecadlib import project_check as PJ
    from tests.test_project_check import make_project
    pro = make_project(tmp_path)
    lib = Path(tmp_path) / "lib"
    t = (lib / "TSym.kicad_sym").read_text()
    (lib / "TSym.kicad_sym").write_text(t.replace('(symbol "QN"', '(symbol "QN_RENAMED"'))
    res = PJ.check_kicad(pro, use_global=False)
    sym = [r for r in res["rows"] if r["kind"] == "symbol"]
    assert sym and sym[0]["state"] == "not in library" and sym[0]["action"] == "hold", sym
    assert res["held"] >= 1
    assert PJ.main(["check", str(pro), "--no-global", "-o", str(Path(tmp_path) / "o" / "r")]) != 0
    # a library that is not on this computer is not 'gone': no hold, as for footprints
    (lib / "TSym.kicad_sym").unlink()
    res = PJ.check_kicad(pro, use_global=False)
    sym = [r for r in res["rows"] if r["kind"] == "symbol"]
    assert sym[0]["state"] == "not in library" and sym[0]["action"] != "hold", sym


# ---------------------------------------------------------------- R-RT-5
def _row_fp(w, pitch, h=0.8, n=4):
    from ecadlib import fingerprint as F
    x0 = -pitch * (n - 1) / 2
    pads = "".join(f'(pad "{i + 1}" smd rect (at {x0 + pitch * i:.3f} 0) (size {w} {h}) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
                   for i in range(n))
    mod = f'(footprint "T" (version 20240108) (generator x) (layer "F.Cu")\n{pads})'
    return F.build("T", None, *F.kicad_footprint(mod), tool="kicad")


def test_rrt5_a_fine_pitch_pad_widened_by_the_old_floor_is_a_major_change():
    """0.4 mm pitch, pads 0.20 -> 0.25 mm (the gap 0.20 -> 0.15 mm, +25 % copper) was geometry-minor under the flat
    50 um floor. The floor now scales with the pitch."""
    from ecadlib import change_diff as CD
    assert CD.worst(CD.diff(_row_fp(0.20, 0.4), _row_fp(0.25, 0.4))) == "geometry-major"
    # the old floor still applies where the pitch is wide: 1.27 mm pitch, 0.60 -> 0.65 mm stays minor
    assert CD.worst(CD.diff(_row_fp(0.60, 1.27), _row_fp(0.65, 1.27))) == "geometry-minor"
    # 10 % of the side is still minor at fine pitch: 0.20 -> 0.21 mm on 0.4 mm
    assert CD.worst(CD.diff(_row_fp(0.20, 0.4), _row_fp(0.21, 0.4))) == "geometry-minor"
    # a lone pad keeps the 50 um floor
    assert CD.worst(CD.diff(_row_fp(1.00, 1.0, n=1), _row_fp(1.04, 1.0, n=1))) == "geometry-minor"
