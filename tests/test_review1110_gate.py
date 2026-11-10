"""Full review 2026-11-10, release check: a check that could not run is a hold, never a pass.
A .kicad_mod the footprint checker crashed on (a pad with no (size ...)) was a warning; the rule book and the
KiCad / Altium comparison were then skipped and the part came out with no failures, so every publisher shipped it. For
a generated footprint the same file made check_item raise, which stopped the run for every part."""
import re, tempfile

from ecadlib import footprint_rules as FR, parity as PY, release_check as RC
from tests.test_release_check import SOIC, make

BROKEN = re.sub(r'(\(pad "?1"? smd \w+ \(at [^)]*\)) \(size [^)]*\)', r"\1", SOIC, count=1)


def _check(source, kmod=SOIC, patch=None):
    with tempfile.TemporaryDirectory() as d:
        it = make(d, source=source)
        if kmod is not SOIC:
            (RC.Path(d) / it["footprint"]["kicad_mod"]).write_text(kmod)
        old = []
        for obj, name, fn in patch or ():
            old.append((obj, name, getattr(obj, name)))
            setattr(obj, name, fn)
        try:
            f, w, _ = RC.check_item(it, d)
            probs = RC.publish_problems(it, d)
        finally:
            for obj, name, fn in old:
                setattr(obj, name, fn)
        return f, w, probs


def _boom(*a, **k):
    raise RuntimeError("boom")


def test_the_fixture_really_lost_a_pad_size():
    assert BROKEN != SOIC and BROKEN.count("(size") == SOIC.count("(size") - 1


def test_a_footprint_the_checker_cannot_read_is_held_for_a_local_library_part():
    f, w, probs = _check("local:ecadlib:SOIC-8", BROKEN)
    assert any(c == "files" and "could not be checked" in m for c, m in f), (f, w)
    assert probs, "a part whose footprint could not be checked must not publish"


def test_a_generated_footprint_the_checker_cannot_read_holds_that_part_and_does_not_raise():
    f, w, probs = _check("datasheet:SOIC-8", BROKEN)           # used to raise TypeError out of check_item
    assert any(c == "files" and "could not be checked" in m for c, m in f), (f, w)
    assert probs


def test_a_rule_book_crash_is_a_hold():
    f, w, probs = _check("local:ecadlib:SOIC-8", patch=[(FR, "check", _boom)])
    assert any(c == "files" and "could not be checked" in m and "rule book" in m for c, m in f), (f, w)
    assert probs


def test_a_parity_crash_is_a_hold():
    f, w, probs = _check("local:ecadlib:SOIC-8", patch=[(PY, "release_diffs", _boom)])
    assert any(c == "files" and "could not be checked" in m and "KiCad / Altium" in m for c, m in f), (f, w)
    assert probs


def test_a_good_part_still_passes():
    f, w, probs = _check("local:ecadlib:SOIC-8")
    assert not any("could not be checked" in str(x) for x in f + w), (f, w)
