"""Full review 2026-11-10, group 2: a QFN / DFN with no pin split was laid out a quarter per side whatever its pin
count or body: 6 pins became 1+2+1+2 round four sides, with no warning."""
import copy, glob, json
from ecadlib import footprint_gen as F

QFN16 = json.load(open(glob.glob("ecadlib/package_specs/**/QFN-16-1EP_3x3mm*.json", recursive=True)[0]))


def errs(**kw):
    s = copy.deepcopy(QFN16)
    s.update(kw)
    return F.sanity({k: v for k, v in s.items() if v is not None})


def test_a_qfn_with_no_split_is_built_only_when_a_quarter_per_side_is_certain():
    assert errs() == []                                                        # 16 pins, square: 4 per side
    for n in (6, 10, 14):
        e = errs(pin_count=n, name=f"UQFN-{n}", exposed_pad=None)
        assert any("would be a guess" in x for x in e), (n, e)
    e = errs(pin_count=12, body_length=4.0, name="QFN-12_3x4mm", exposed_pad=None)   # 12 pins on 3 x 4 mm: 2+4, not 3+3
    assert any("not square" in x for x in e), e


def test_a_split_or_listed_sides_is_built_as_given():
    assert not any("guess" in x for x in errs(pin_count=6, pins_left_right=3, pins_top_bottom=0, name="X2SON-6",
                                                 exposed_pad=None))
    assert not any("guess" in x for x in errs(pin_count=10, pins_left_right=3, pins_top_bottom=2, name="QFN-10",
                                                 exposed_pad=None))


def test_every_bundled_spec_still_builds():
    for f in glob.glob("ecadlib/package_specs/**/*.json", recursive=True):
        spec = json.load(open(f))
        assert not any("guess" in x for x in F.sanity(spec)), f


# ---------------------------------------------------------------- numbering
N = lambda v: {"nom": v}
SO = dict(name="S", package_type="gullwing", lead_span={"min": 5.8, "max": 6.2}, body_width=N(3.9),
          terminal_width={"min": 0.31, "max": 0.51}, terminal_length={"min": 0.4, "max": 1.27})


def soic(n, right):
    """A SOIC-n with the left row 1..n/2 top to bottom and the right row's numbers as given, top to bottom."""
    h = n // 2
    off = lambda i: (i - (h - 1) / 2) * 1.27
    pos = [{"number": str(i + 1), "side": "L", "offset": off(i)} for i in range(h)] + \
          [{"number": str(x), "side": "R", "offset": off(i)} for i, x in enumerate(right)]
    return dict(SO, pin_count=n, body_length=N(1.27 * h - 0.2), pin_positions=pos)


def test_a_whole_row_numbered_backwards_is_held_on_a_generated_footprint():
    from ecadlib import release_check as RC
    names = {str(i): f"P{i}" for i in range(1, 17)}
    wrong, _ = F.build(soic(16, range(9, 17)))                                 # 9-16 going down the right side
    h = RC.numbering_hold(wrong, "datasheet:https://example.com/x.pdf", names)
    assert h and "pads 9-16 are numbered the other way round" in h, h
    assert RC.numbering_hold(wrong, "kicad:Package_SO", names) is None        # a library footprint keeps its numbering
    right, _ = F.build(soic(16, range(16, 8, -1)))
    assert RC.numbering_hold(right, "datasheet:https://example.com/x.pdf", names) is None


def test_a_pad_number_listed_twice_with_another_missing_is_not_built():
    spec = soic(8, (8, 7, 6, 5))
    assert F.sanity(spec) == []
    spec["pin_positions"][3]["number"] = "3"                                   # 1, 2, 3, 3: no pad 4
    e = F.sanity(spec)
    assert any("lists pad 3 twice and has no pad 4" in x for x in e), e
