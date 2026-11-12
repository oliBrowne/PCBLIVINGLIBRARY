"""Mutation check 2026-11-12: edge tests of safety rules. A mutation run changed one comparison, and / or, or number at
a time in the release check, the eval scorer, the change gate, the approvals rule, the footprint generator's sanity
check, the footprint checker, the cross-check and the standard-part match; the changes below passed every test. Each
test here sits on the edge of one such rule, so that change now fails a test.
Run:  python3 -m tests.run_all test_mutation_gaps1112"""
import json, tempfile
from pathlib import Path

from ecadlib import release_check as RC, rules as RU
from tests.fixtures_lite import lite
from tests import test_release_check as TRC, test_review1110_fpgen as TFP

GEN = "datasheet:https://example.com/x.pdf"      # a footprint ecadlib generated: the numbering hold applies


# ---------------------------------------------------------------- release_check.numbering_hold
def _soic16(right):
    text, _ = TFP.F.build(TFP.soic(16, right))
    return text


NAMES16 = {str(i): f"P{i}" for i in range(1, 17)}


def test_numbering_hold_two_swapped_pairs_is_still_a_swap():
    # "one or two pairs of leads swapped" is held as a swap: exactly two pairs is the edge (len(pairs) > 2)
    h = RC.numbering_hold(_soic16((15, 16, 14, 13, 11, 12, 10, 9)), GEN, NAMES16)
    assert h and "pads 11 and 12 are swapped" in h and "pads 15 and 16 are swapped" in h, h
    one = RC.numbering_hold(_soic16((15, 16, 14, 13, 12, 11, 10, 9)), GEN, NAMES16)
    assert one and "pads 15 and 16 are swapped" in one and "11" not in one, one


def test_numbering_hold_three_swapped_pairs_is_not_a_swap():
    # more than two pairs out of place: numbered where the pads fall, held only when one whole run reads backwards
    assert RC.numbering_hold(_soic16((15, 16, 13, 14, 11, 12, 10, 9)), GEN, NAMES16) is None


def test_numbering_hold_skips_a_part_the_order_rule_does_not_apply_to():
    # perimeter_order -> None (a single row): no hold and no crash ("not po or not po['misplaced']")
    row = TRC.SOIC
    for n, x in zip("5678", (2.4725, 2.4725, 2.4725, 2.4725)):
        row = row.replace(f'(pad "{n}" smd roundrect (at {x} ', f'(pad "{n}" smd roundrect (at -{x} ')
    assert RC.FC.perimeter_order(row) is None, "the fixture must be a part the order rule skips"
    assert RC.numbering_hold(row, GEN, {str(i): f"P{i}" for i in range(1, 9)}) is None
    assert RC.numbering_hold(_soic16(range(16, 8, -1)), GEN, NAMES16) is None            # in order: nothing misplaced


# ---------------------------------------------------------------- release_check.check_item
HOLE = '\n  (pad "{n}" {kind} circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers "*.Cu" "*.Mask"))\n)'


def test_check_item_plated_hole_with_a_number_needs_a_symbol_pin():
    # only an UNPLATED hole is mechanical: a numbered plated hole is a pin's pad (p["hole"] and not p["plated"])
    with tempfile.TemporaryDirectory() as d:
        plated = TRC.SOIC.replace("\n)", HOLE.format(n="9", kind="thru_hole"))
        f, w = TRC.fails(TRC.make(d, kmod=plated), d)
        assert "pins: pads without a symbol pin: 9" in f, f
        assert not any("mechanical pads" in x for x in w), w
    with tempfile.TemporaryDirectory() as d:
        bare = TRC.SOIC.replace("\n)", HOLE.format(n="9", kind="np_thru_hole"))
        f, w = TRC.fails(TRC.make(d, kmod=bare), d)
        assert not [x for x in f if x.startswith("pins")], f
        assert any("mechanical pads without a pin (allowed): 9" in x for x in w), w


def test_check_item_h1_is_a_mounting_hole_off_a_ball_grid():
    # H1 is a ball only on a ball grid (grid and H<n>): on a SOIC it is a mounting pad, allowed without a pin
    with tempfile.TemporaryDirectory() as d:
        h1 = TRC.SOIC.replace("\n)", '\n  (pad "H1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask"))\n)')
        f, w = TRC.fails(TRC.make(d, kmod=h1), d)
        assert not [x for x in f if x.startswith("pins")], f
        assert any("mechanical pads without a pin (allowed): H1" in x for x in w), w


def test_check_item_pin1_name_from_the_rules_counts_as_pin_1():
    # "no pad numbered 1" is dropped only for that message and only when a pad carries one of the rules' pin-1 names
    p1 = TRC.SOIC.replace('(pad "1" ', '(pad "P1" ')
    pins = ["P1"] + list(range(2, 9))
    with tempfile.TemporaryDirectory() as d:
        assert "footprint: pin1: no pad numbered 1" in TRC.fails(TRC.make(d, kmod=p1, pins=pins), d)[0]
        mine = {**RU.DEFAULTS, "footprint": {**RU.DEFAULTS["footprint"], "pin1": ["1", "A1", "P1"]}}
        f = TRC.fails(TRC.make(d, kmod=p1, pins=pins), d, mine)[0]
        assert not any("no pad numbered 1" in x for x in f), f


def test_check_item_unreadable_kicad_footprint_is_one_hold_not_a_numbering_crash():
    # the numbering hold runs only on a footprint that read back (text is not None and syms and a numbering warning)
    with tempfile.TemporaryDirectory() as d:
        it = TRC.make(d, source=GEN)
        (Path(d) / it["footprint"]["kicad_mod"]).write_text("(footprint oops")
        f = TRC.fails(it, d)[0]
        assert len([x for x in f if x.startswith("files: KiCad footprint file does not read back")]) == 1, f
        assert not any("numbering" in x for x in f), f


# ---------------------------------------------------------------- eval.score
def test_score_symbol_names_pass_exactly_at_the_share():
    # TOL["names"] = 0.8: 4 of 5 names agreeing is the share itself and passes (>=), 3 of 5 fails
    from ecadlib.eval import score as S
    ref = [("1", "ALPHA"), ("2", "BRAVO"), ("3", "CHARLIE"), ("4", "DELTA"), ("5", "FOXTROT")]
    at = S.score_symbol(ref[:4] + [("5", "ZULU")], ref)["symbol_names"]
    assert at["ok"] and (at["matched"], at["named"], at["moved"]) == (4, 5, 0), at
    under = S.score_symbol(ref[:3] + [("4", "YANKEE"), ("5", "ZULU")], ref)["symbol_names"]
    assert not under["ok"] and (under["matched"], under["moved"]) == (3, 0), under


def test_maker_land_small_tab_and_a_lead_sharing_its_number():
    # the part's centre is taken from the LARGEST pad carrying the tab's number (area, p[3] * p[4]), and a tab too
    # small to pass for an exposed pad is the maker's pad when it sits on the tab land (fits: on_tab_land -> True)
    from ecadlib.eval import score as S
    from tests.test_tab_land_1024 import sot223
    s = sot223(tab_length=3.3, tab_width=1.0, tab_center=2.5)
    lead = lambda n, y: [n, -3.15, y, 2.0, 1.2, 0.0]
    tab = ["4", 2.5, 0.0, 3.3, 1.0, 0.0]
    mine = {"pads": [lead("1", -2.3), lead("2", 0.0), lead("3", 2.3), tab], "courtyard": [-4.4, -3.6, 4.4, 3.6]}
    assert S.ep_numbers(mine) == [], "the fixture's tab must not count as an exposed pad"
    ml = S.maker_land(mine, s)
    assert ml["followed"] and ml["pads"] == ["1", "2", "3", "4"], ml
    # the centre lead numbered as the tab it is joined to (taller than the tab's land, smaller in area)
    joined = {"pads": [lead("1", -2.3), lead("4", 0.0), lead("3", 2.3), tab], "courtyard": mine["courtyard"]}
    ml = S.maker_land(joined, s)
    assert ml["followed"] and ml["pads"] == ["1", "4", "3", "4"], ml


# ---------------------------------------------------------------- change_gate
def _ch(cls):
    return {"class": cls, "text": f"a {cls} change"}


def test_decide_observe_warns_on_anything_but_cosmetics():
    # observe mode (released in the workspace) never holds: warn when any change is not cosmetic, pass when all are
    from ecadlib import change_gate as CG
    assert CG.decide([_ch("cosmetic")], None, "observe")["action"] == "pass"
    assert CG.decide([_ch("cosmetic"), _ch("electrical")], None, "observe")["action"] == "warn"
    assert CG.decide([_ch("identity")], None, "observe")["action"] == "warn"
    assert CG.decide([], None, "observe")["action"] == "pass"


def test_decide_restyle_holds_on_anything_but_cosmetics():
    # restyle mode: cosmetics alone pass; a change of any other class is a hold whatever the policy says, and the
    # reason names the first change that is not cosmetic
    from ecadlib import change_gate as CG
    lax = {**CG.POLICY, "geometry_minor": "pass"}
    assert CG.decide([_ch("cosmetic")], lax, "restyle")["action"] == "pass"
    d = CG.decide([_ch("cosmetic"), _ch("geometry-minor")], lax, "restyle")
    assert d["action"] == "hold" and "this one changed geometry-minor (a geometry-minor change)" in d["why"][0], d
    assert CG.decide([_ch("cosmetic"), _ch("geometry-minor")], lax)["action"] == "pass"       # the gate alone: policy


def test_held_block_classes_are_the_proposals_or_counted_from_its_changes():
    from ecadlib import change_gate as CG
    ch = [_ch("electrical"), _ch("cosmetic"), _ch("cosmetic")]
    assert CG.held_block({"changes": ch})["classes"] == {"electrical": 1, "cosmetic": 2}     # none recorded: counted
    assert CG.held_block({"changes": ch, "classes": {"identity": 5}})["classes"] == {"identity": 5}


def _publish_guard_writes(monkeypatch, edit, folder=False, **kw):
    """publish_guard on a fresh released demo library -> how many times it wrote the plan file."""
    from ecadlib import change_gate as CG
    from tests import test_change_control as TCC
    from tests.test_change_control_round4 import _hand_edit_value
    plan, lib = TCC.demo()
    if edit:
        _hand_edit_value(plan)                 # a Value typed into plan.json by hand: held, the released one put back
    wrote = []
    monkeypatch.setattr(CG, "write_plan", lambda *a, **k: wrote.append(a))
    CG.publish_guard(lib if folder else plan, TCC.items_of(plan), "kicad", log=lambda *a, **k: None, **kw)
    return len(wrote)


@lite
def test_publish_guard_writes_the_plan_only_when_asked_and_changed(monkeypatch):
    # save and a plan.json and items changed by settling: all three, or the plan file is left alone
    assert _publish_guard_writes(monkeypatch, edit=True) == 1
    assert _publish_guard_writes(monkeypatch, edit=False) == 0                 # nothing settled: nothing to write
    assert _publish_guard_writes(monkeypatch, edit=True, save=False) == 0      # a caller that saves for itself
    assert _publish_guard_writes(monkeypatch, edit=False, folder=True) == 0    # given the folder: no plan file named


# ---------------------------------------------------------------- approvals
def test_evaluate_one_person_under_two_keys_is_one_approval():
    # records are grouped by person: the same key OR the same person (same_person), so a second mailbox spelling
    # stored under another key is not a second approver; a different person is
    from ecadlib import approvals as AP
    from tests import test_approvals as TA
    with tempfile.TemporaryDirectory() as d:
        it = TRC.make(d)
        TA._plan(d, [], {"required": 2})
        pol = AP.load_policy(d)
        AP.decide(it, "approve", TA.ANN, base=d, policy=pol)
        first = it["approvals"][0]
        alias = "Ann Lee <ann+work@example.com>"
        assert AP.user_key(alias) != AP.user_key(TA.ANN) and AP.same_person(alias, TA.ANN)
        it["approvals"] = [first, {**first, "who": alias, "key": AP.user_key(alias)}]
        ev = AP.evaluate(it, pol, d)
        assert not ev["met"] and ev["missing"] == ["1 more approval(s) (1/2)"] and ev["approved_by"] == [alias], ev
        it["approvals"] = [first, {**first, "who": TA.BOB, "key": AP.user_key(TA.BOB)}]
        assert AP.evaluate(it, pol, d)["met"]


def test_post_check_without_rows_and_with_fewer_rows_than_items():
    # rows are optional and may be shorter than items (rows and i < len(rows)): an item with no row is still checked
    from ecadlib import approvals as AP
    from tests import test_approvals as TA
    with tempfile.TemporaryDirectory() as d:
        TA._plan(d, [], {"required": 1})
        a, b = TRC.make(d, name="PARTA"), TRC.make(d, name="PARTB")
        assert AP.post_check([a, b], d) and a["status"] == b["status"] == "awaiting_approval"
        a["status"] = b["status"] = "ready"
        rows = [{}]
        AP.post_check([a, b], d, rows)
        assert rows == [{"approval": "1 more approval(s) (0/1)", "status": "awaiting_approval"}], rows
        assert b["status"] == "awaiting_approval"


# ---------------------------------------------------------------- footprint_gen.sanity
def _spec(name, **kw):
    s = json.loads((Path(RC.__file__).parent / "package_specs" / f"{name}.json").read_text(encoding="utf-8"))
    s.update(kw)
    return {k: v for k, v in s.items() if v is not None}


def _sanity(name, **kw):
    from ecadlib import footprint_gen as F
    return F.sanity(_spec(name, **kw))


SOIC8, LQFP48 = "SOIC-8_3.9x4.9mm_P1.27mm", "LQFP-48_7x7mm_P0.5mm"       # body 3.9 x 4.9, span 6; body 7 x 7, span 9


def test_sanity_lead_span_must_clear_the_body_by_a_tenth():
    # the leads stick out past the plastic by 0.1 mm or more in all: 0.12 over is fine, 0.05 over is "swapped?"
    swapped = lambda e: [x for x in e if "is not bigger than" in x]
    assert _sanity(SOIC8) == [] and _sanity(LQFP48) == []
    assert swapped(_sanity(SOIC8, lead_span=4.02, terminal_length=0.5)) == []
    assert len(swapped(_sanity(SOIC8, lead_span=3.95, terminal_length=0.5))) == 1
    assert swapped(_sanity(LQFP48, lead_span=7.12)) == []
    assert len(swapped(_sanity(LQFP48, lead_span=7.05))) == 2                  # across both body sides
    assert swapped(_sanity(LQFP48, body_width=5.0, lead_span_y=5.12)) == []    # a rectangular QFP: its own Y span
    e = swapped(_sanity(LQFP48, body_width=5.0, lead_span_y=5.05))
    assert len(e) == 1 and e[0].startswith("lead_span_y 5.05 is not bigger than body_width 5.0"), e


def test_sanity_a_foot_of_half_the_lead_span_is_refused():
    # T >= span / 2 (the edge itself included), for a gull-wing and a QFP; with no lead_span read the rule is skipped
    half = "is half the lead span or more"
    assert any(half in x for x in _sanity(SOIC8, terminal_length=3.0))
    assert not any(half in x for x in _sanity(SOIC8, terminal_length=2.9))
    assert any(half in x for x in _sanity(LQFP48, terminal_length=4.5))
    assert not any(half in x for x in _sanity(LQFP48, terminal_length=4.4))
    assert not any(half in x for x in _sanity(SOIC8, lead_span=None))
    assert not any(half in x for x in _sanity(LQFP48, lead_span=None))


def test_sanity_exposed_pad_as_big_as_the_body_is_refused():
    # >= on each side: a pad exactly the body's size in X (a gull-wing's body_width) or in Y is not a pad under it
    big = lambda **ep: [x for x in _sanity(SOIC8, exposed_pad=ep) if "as big as the body" in x]
    assert big(length=3.8, width=4.8) == []
    assert len(big(length=3.9, width=3.0)) == 1
    assert len(big(length=2.4, width=4.9)) == 1


# ---------------------------------------------------------------- footprint_check.check
def _pad(num, x, y, w, h, layers='"F.Cu" "F.Mask" "F.Paste"', kind="smd rect", more=""):
    return f'  (pad "{num}" {kind} (at {x} {y}) (size {w} {h}){more} (layers {layers}))\n'


def _check(*pads, silk_y=None):
    """footprint_check.check on a bare footprint of these pads (and one silk line at silk_y) -> [(level, rule, msg)]."""
    silk = f'  (fp_line (start -0.5 {silk_y}) (end 0.5 {silk_y}) (layer "F.SilkS") (width 0.12))\n' if silk_y else ""
    return RC.FC.check('(footprint "X" (layer "F.Cu")\n' + "".join(pads) + silk + ")\n")


def _said(issues, rule, part):
    return [f"{lvl} {m}" for lvl, r, m in issues if r == rule and part in m]


def test_check_exposed_pad_paste_window_centred_on_the_pad_edge_is_not_inside():
    # a paste window counts when its centre is inside the exposed pad (<), not on its edge: 40% stays 40%
    ep = [_pad("3", 0, 0, 2, 2, '"F.Cu" "F.Mask"'), _pad("1", -2, 0, 0.4, 0.4), _pad("2", 2, 0, 0.4, 0.4)]
    win = lambda x, y, w, h: _pad("", x, y, w, h, '"F.Paste"')
    assert len(_said(_check(*ep, win(0, 0, 1.6, 1.0)), "paste", "covers 40% of its copper")) == 1
    assert len(_said(_check(*ep, win(0, 0, 1.6, 1.0), win(1.0, 0, 0.8, 1.0)), "paste", "covers 40% of its copper")) == 1
    assert len(_said(_check(*ep, win(0, 0, 1.6, 1.0), win(0, 1.0, 0.8, 1.0)), "paste", "covers 40% of its copper")) == 1
    assert _said(_check(*ep, win(0, 0, 1.6, 1.0), win(0.9, 0.9, 0.8, 1.0)), "paste", "covers") == []     # inside: 60%


def test_check_mask_opening_within_eps_of_the_exposed_pad_is_its_opening():
    # a solder-mask-defined land: the mask-only opening may reach EPS (0.005 mm) past the copper on each side (<=),
    # and the paste is then measured against the opening; a wider one is not the pad's opening (against the copper)
    ep = [_pad("3", 0, 0, 2, 2, '"F.Cu"'), _pad("1", -2, 0, 0.4, 0.4), _pad("2", 2, 0, 0.4, 0.4)]
    mask = lambda w, h: _pad("", 0, 0, w, h, '"F.Mask"')
    paste = _pad("", 0, 0, 1.2, 1.0, '"F.Paste"')                     # 1.2 mm2: 60% of the opening, 30% of the copper
    assert _said(_check(*ep, mask(2.01, 1.0), paste), "paste", "covers") == []
    assert _said(_check(*ep, mask(1.0, 2.01), paste), "paste", "covers") == []
    assert len(_said(_check(*ep, mask(2.02, 1.0), paste), "paste", "covers 30% of its copper")) == 1
    assert len(_said(_check(*ep, mask(1.0, 2.02), paste), "paste", "covers 30% of its copper")) == 1


def test_check_pad_exactly_at_the_web_limit_from_a_hole_is_clear():
    # copper nearer an unplated hole than MIN_WEB - EPS (0.195 mm) is reported (<): at 0.195 itself it is clear.
    # (a 0.6 mm hole at 0 and a 0.25 mm pad at 0.62: the gap is exactly 0.195 in floating point too)
    FC = RC.FC
    hole = _pad("", 0, 0, 0.6, 0.6, '"*.Cu" "*.Mask"', kind="np_thru_hole circle", more=" (drill 0.6)")
    gap = FC._hole_gap({"x": 0.0, "y": 0.0, "w": 0.6, "h": 0.6}, {"x": 0.62, "y": 0.0, "w": 0.25, "h": 1.0})
    assert gap == FC.MIN_WEB - FC.EPS, "the fixture must sit exactly on the limit"
    assert _said(_check(hole, _pad("1", 0.62, 0, 0.25, 1.0)), "copper", "from the hole") == []
    near = _said(_check(hole, _pad("1", 0.61, 0, 0.25, 1.0)), "copper", "from the hole")
    assert len(near) == 1 and near[0].startswith("warning pad 1 is 0.185 mm from the hole"), near


def test_check_silk_at_a_tenth_is_let_through_only_on_a_tiny_part():
    # silk 0.15 mm from a pad: allowed where the copper is under 1.7 mm across (pad edge to pad edge, w / 2 each
    # side), a warning on anything wider
    two = lambda c: (_pad("1", -c, 0, 0.6, 0.6), _pad("2", c, 0, 0.6, 0.6))
    assert _said(_check(*two(0.5), silk_y=-0.51), "silk", "from pad") == []                    # 1.6 mm across
    wide = _said(_check(*two(0.575), silk_y=-0.51), "silk", "from pad")                        # 1.75 mm across
    assert len(wide) == 1 and wide[0].startswith("warning silkscreen is 0.150 mm from pad"), wide
    assert _said(_check(*two(0.5), silk_y=-0.44), "silk", "from pad")[0].startswith("error")   # 0.08 mm: never


# ---------------------------------------------------------------- crosscheck.compare
def test_compare_turned_note_only_for_a_second_read_set_aside_by_the_land():
    # the "built as read" tie-break is said only when a second READ is turned AND the read's own land fixes its axes
    # AND the pads stand on the body: not against the table, and not for a gull-wing whose body alone turns
    from ecadlib import crosscheck as CC
    from tests import test_crosscheck_land_axes_1023 as TL
    first = CC.critical(TL.dfn(3.0, 5.0, (1.65, 4.4), 16, 0.5, TL.LTC4060_LAND))
    assert first["land_axes"]
    turned = TL.dfn(5.0, 3.0, (4.4, 1.65), 16, 0.5)
    turned.update(pins_left_right=0, pins_top_bottom=8)                 # the table's rows turn with its body
    c = CC.compare(first, CC.critical(turned), "table")
    assert c["status"] == "agreed" and c["turned"] == ["body", "exposed pad"] and "tie_breaks" not in c, c
    so = {"package_type": "gullwing", "pin_count": 8, "pitch": 1.27, "body_length": {"nom": 4.9},
          "body_width": {"nom": 3.9}, "lead_span": {"nom": 6.0}}
    c = CC.compare(CC.critical(so), CC.critical(dict(so, body_length={"nom": 3.9}, body_width={"nom": 4.9})))
    assert c["status"] == "agreed" and c["turned"] == ["body"] and "tie_breaks" not in c, c


# ---------------------------------------------------------------- stdparts.identify
def _so_read(name, pins, length):
    return {"package_type": "gullwing", "name": name, "pin_count": pins, "pitch": 1.27, "body_length": {"nom": length},
            "body_width": {"nom": 3.9}, "lead_span": {"nom": 6.0}}


def test_identify_a_150_mil_sop_of_8_14_or_16_pins_may_be_the_jedec_soic():
    # a drawing titled SOP with a 3.9 mm body and 8, 14 or 16 pins has JEDEC SOIC as a rival; any other family
    # name (TSSOP) keeps to its own family, whatever the numbers fit
    from ecadlib import stdparts as SP
    for pins, length, want in ((8, 4.9, "SOIC-8_3.9x4.9mm_P1.27mm"), (14, 8.65, "SOIC-14_3.9x8.7mm_P1.27mm"),
                               (16, 9.9, "SOIC-16_3.9x9.9mm_P1.27mm")):
        assert SP.identify(_so_read(f"SOP-{pins}", pins, length))[0] == want, pins
    name, why = SP.identify(_so_read("TSSOP-14", 14, 8.65))
    assert name is None and "no standard package fits" in why, (name, why)
