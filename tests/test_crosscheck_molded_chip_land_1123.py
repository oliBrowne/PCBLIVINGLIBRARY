"""A two-terminal read with the maker's whole lead land (pad_length, pad_width, pad_center) builds its two pads from that
land, whether it is called molded or chip (footprint_gen._apply_land replaces the IPC pads). A second read that calls
the same part chip where the first said molded (or the other way round) then changes nothing built, as long as the pin
count and the body agree. Free 1000-part replay 2026-11-23: 11 power inductors and tantalums (NRS4018, XFL4020,
VLS4012, T520B...) held only on "molded vs chip". Without the land, or with a body that disagrees, it still holds.
python3 -m tests.run_all test_crosscheck_molded_chip_land_1123"""
from ecadlib import crosscheck as CC


def r(mn, nom, mx):
    return {"min": mn, "nom": nom, "max": mx}


def first(t="molded", land=True):
    s = {"package_type": t, "name": "NRS4018", "pin_count": 2, "body_length": r(3.9, 4.0, 4.1),
         "body_width": r(3.9, 4.0, 4.1), "terminal_length": r(0.9, 1.0, 1.1), "terminal_width": r(3.4, 3.5, 3.6)}
    if land:
        s["land"] = {"pad_length": 1.2, "pad_width": 3.7, "pad_center": 1.4}
    return s


def second(t="chip", body=4.0):
    return {"package_type": t, "name": "NRS4018", "pin_count": 2, "body_length": {"nom": body},
            "body_width": {"nom": 4.0}}


def read(a, b):
    return CC.against_read(CC.critical(a), CC.critical(b), None, None)


def test_molded_read_with_the_makers_land_settles_a_chip_word():
    c = read(first(), second())
    assert c["status"] == "agreed", c
    assert any("maker's land" in t for t in c.get("tie_breaks", [])), c


def test_polarized_chip_read_with_the_makers_land_settles_a_molded_word():
    assert read(dict(first("chip"), polarized=True), second("molded"))["status"] == "agreed"


def test_without_the_land_the_type_still_holds():
    assert read(first(land=False), second())["status"] == "disagreed"


def test_a_body_that_disagrees_still_holds():
    assert read(first(), second(body=5.0))["status"] == "disagreed"


def test_a_gullwing_word_still_holds():
    assert read(first(), second("gullwing"))["status"] == "disagreed"


def _res(tie):
    cc = {"status": "agreed", "checks": [{"status": "agreed", "source": "second read", "via": "assistant",
                                          "against": "second read (your assistant)", "tie_breaks": [tie]}]}
    return {"cross_check": cc, "symbol": {"pins": []},
            "needs_review": [f"cross-check agreed once a difference was set aside: {tie}; check it on the drawing"]}


def test_the_chip_molded_word_set_aside_does_not_hold_the_release():
    from ecadlib import librarian as L
    tie = read(first(), second())["tie_breaks"][0]
    assert L.read_confirmed(_res(tie), {}, ["footprint"])


def test_any_other_difference_set_aside_still_holds_the_release():
    from ecadlib import librarian as L
    assert not L.read_confirmed(_res("package type read custom, other source connector: a word only, the pads are "
                                     "built as the read lists them"), {}, ["footprint"])


def test_a_body_under_1_55_mm_still_holds():
    # footprint_gen.family: a chip that small is chip_small, with a smaller courtyard than molded
    a = dict(first(), body_length=r(0.9, 1.0, 1.1), body_width=r(0.5, 0.6, 0.7))
    b = dict(second(body=1.0), body_width={"nom": 0.6})
    assert read(a, b)["status"] == "disagreed"


def test_an_unpolarized_chip_the_other_read_calls_molded_still_holds():
    # the molded word may be the only sign of a tantalum's or a diode's polarity, which the read alone gives
    assert read(first("chip"), second("molded"))["status"] == "disagreed"
    assert read(dict(first("chip"), polarized=True), second("molded"))["status"] == "agreed"


def test_a_body_turned_90_degrees_still_holds():
    a = dict(first(), body_length=r(5.9, 6.0, 6.1), body_width=r(3.9, 4.0, 4.1))
    b = dict(second(body=4.0), body_width={"nom": 6.0})
    assert read(a, b)["status"] == "disagreed"


def test_only_the_whole_line_is_exempt():
    from ecadlib import librarian as L
    tie = read(first(), second())["tie_breaks"][0]
    res = _res(tie)
    res["needs_review"] = [f"datasheet from your folder: x; set aside: {tie}; check it on the drawing"]
    assert not L.read_confirmed(res, {}, ["footprint"])
