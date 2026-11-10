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
