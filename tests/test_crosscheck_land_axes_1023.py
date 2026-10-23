"""A second read that gives the same body and exposed pad with length and width the other way round (D x E printed
5 x 3 for a DFN read 3 x 5) is set aside only when the first read carries the maker's own land, and that land runs
the read's way: the terminal rows sit at the body edge the read calls its length, and the land's exposed pad
(ep_across x ep_along) is the read's. Then the pads cannot be built turned. Data: the 2026-10-23 live run, where
ENS210 and LTC4060EDHC were held this way and build right as read. LTC2942-1 (no land) still holds.
python3 -m tests.run_all test_crosscheck_land_axes_1023"""
from ecadlib import crosscheck as CC


def r(mn, nom, mx):
    return {"min": mn, "nom": nom, "max": mx}


def dfn(L, W, ep, n, pitch, land=None):
    s = {"package_type": "qfn", "name": "DFN", "pin_count": n, "pitch": pitch, "body_length": r(L - 0.1, L, L + 0.1),
         "body_width": r(W - 0.1, W, W + 0.1), "terminal_length": r(0.3, 0.4, 0.5), "terminal_width": r(0.2, 0.25, 0.3),
         "exposed_pad": {"length": r(ep[0] - 0.1, ep[0], ep[0] + 0.1), "width": r(ep[1] - 0.1, ep[1], ep[1] + 0.1)},
         "pins_left_right": n // 2, "pins_top_bottom": 0}
    if land:
        s["land"] = land
    return s


def nominal(L, W, ep, n, pitch):
    return {"package_type": "qfn", "name": "x", "pin_count": n, "pitch": pitch, "body_length": {"nom": L},
            "body_width": {"nom": W}, "exposed_pad": {"length": {"nom": ep[0]}, "width": {"nom": ep[1]}}}


def read(first, second):
    return CC.against_read(CC.critical(first), CC.critical(second), None, None)


LTC4060_LAND = {"pad_length": 0.65, "pad_width": 0.25, "pad_center": 1.425, "ep_across": 1.65, "ep_along": 4.4}
ENS210_LAND = {"pad_length": 0.6, "pad_width": 0.4, "pad_center": 0.95, "ep_across": 0.7, "ep_along": 1.6}


def test_a_turned_second_read_agrees_when_the_reads_own_land_runs_its_way():
    first = dfn(3.0, 5.0, (1.65, 4.4), 16, 0.5, LTC4060_LAND)          # LTC4060EDHC
    c = read(first, nominal(5.0, 3.0, (4.4, 1.65), 16, 0.5))
    assert c["status"] == "agreed", c
    assert set(c["turned"]) == {"body", "exposed pad"}
    assert any("land" in t for t in c.get("tie_breaks", [])), c


def test_an_exposed_pad_turned_in_a_square_body_agrees_when_the_land_runs_the_reads_way():
    first = dfn(2.0, 2.0, (0.7, 1.6), 4, 0.95, ENS210_LAND)            # ENS210
    c = read(first, nominal(2.0, 2.0, (1.6, 0.7), 4, 0.95))
    assert c["status"] == "agreed", c
    assert c["turned"] == ["exposed pad"]


def test_no_land_still_holds():
    first = dfn(2.0, 3.0, (1.35, 1.65), 6, 0.5)                         # LTC2942-1: built turned when released
    assert read(first, nominal(3.0, 2.0, (1.65, 1.35), 6, 0.5))["status"] == "disagreed"
    first = dfn(2.0, 2.0, (0.7, 1.6), 4, 0.95)
    assert read(first, nominal(2.0, 2.0, (1.6, 0.7), 4, 0.95))["status"] == "disagreed"


def test_a_land_that_runs_the_other_way_still_holds():
    # the land's rows sit at the read's width, not its length: the read's axes are the turned ones
    wrong = dict(LTC4060_LAND, pad_center=2.425)
    assert read(dfn(3.0, 5.0, (1.65, 4.4), 16, 0.5, wrong), nominal(5.0, 3.0, (4.4, 1.65), 16, 0.5))["status"] == "disagreed"
    # the land's exposed pad is the other way round from the read's
    wrong = dict(LTC4060_LAND, ep_across=4.4, ep_along=1.65)
    assert read(dfn(3.0, 5.0, (1.65, 4.4), 16, 0.5, wrong), nominal(5.0, 3.0, (4.4, 1.65), 16, 0.5))["status"] == "disagreed"
    wrong = dict(ENS210_LAND, ep_across=1.6, ep_along=0.7)
    assert read(dfn(2.0, 2.0, (0.7, 1.6), 4, 0.95, wrong), nominal(2.0, 2.0, (1.6, 0.7), 4, 0.95))["status"] == "disagreed"


def test_a_land_that_cannot_tell_the_axes_still_holds():
    # square body and a square land pad: nothing in the land says which way the read runs
    land = {"pad_length": 0.6, "pad_width": 0.4, "pad_center": 0.95, "ep_across": 1.0, "ep_along": 1.0}
    assert read(dfn(2.0, 2.0, (0.7, 1.6), 4, 0.95, land), nominal(2.0, 2.0, (1.6, 0.7), 4, 0.95))["status"] == "disagreed"


def test_any_other_difference_still_holds_with_the_land():
    first = dfn(3.0, 5.0, (1.65, 4.4), 16, 0.5, LTC4060_LAND)
    assert read(first, nominal(5.0, 3.0, (4.4, 1.65), 16, 0.65))["status"] == "disagreed"          # pitch
    assert read(first, nominal(5.0, 3.0, (4.4, 1.65), 14, 0.5))["status"] == "disagreed"           # pin count
    assert read(first, nominal(5.0, 3.0, (4.0, 1.65), 16, 0.5))["status"] == "disagreed"           # pad size


def test_a_quad_qfn_is_not_turned_by_its_land():
    # pins on four sides: a turn moves pins between rows, the land cannot vouch for that
    first = dfn(3.0, 5.0, (1.65, 4.4), 16, 0.5, LTC4060_LAND)
    first.update(pins_left_right=5, pins_top_bottom=3)
    assert read(first, nominal(5.0, 3.0, (4.4, 1.65), 16, 0.5))["status"] == "disagreed"
