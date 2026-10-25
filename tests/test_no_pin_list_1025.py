"""A read that gives the package but no pin list (EPM240F100, full run on main 48dc8ac 2026-10-24: Altera's handbook
excerpt has no ball-by-ball pinout, "printed pin-outs are available on the Altera website"; the BGA footprint was
built from the grid and the symbol came out with 0 pins of 100). A part without its pins is held, not built.
The PDF here is SYNTHETIC text.   python3 -m tests.run_all test_no_pin_list_1025"""
import copy

from tests import test_datasheet as TD, test_ds_route as TR
from tests.test_holds_misses_1022 import read_through_librarian


def test_a_read_with_no_pins_is_held_not_built():
    e = copy.deepcopy(TD.GOOD)
    e["pins"] = []
    pdf = TR.pdf_lines([["TESTPART 3 A buck converter", "VQFN-16 3x3 mm"], ["Package outline", "VQFN-16"]])
    a, _ = read_through_librarian(e, pdf)
    assert not (a.get("footprint") or {}).get("file") and not (a.get("symbol") or {}).get("file"), a
    assert any("no pin list" in str(t) for t in a["librarian"]["tried"]), a["librarian"]["tried"]


def test_the_same_read_with_its_pins_builds():
    pdf = TR.pdf_lines([["TESTPART 3 A buck converter", "VQFN-16 3x3 mm"], ["Package outline", "VQFN-16"]])
    a, _ = read_through_librarian(copy.deepcopy(TD.GOOD), pdf)
    assert (a.get("symbol") or {}).get("file"), a["librarian"]["tried"]
