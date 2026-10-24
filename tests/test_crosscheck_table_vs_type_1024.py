"""A read that copies the maker's recommended land as a pad table (custom) and a second read that calls the package a
QFN / gullwing with the same numbers (FDMT800100DC, 2026-10-23 live run: onsemi 507AR Dual Cool 88, 9-pad land, held
as "custom vs qfn"). The type word alone changes nothing built when the table itself fits the numbers both reads agree
on: its rows step at the agreed pitch, it has a pad for every pin, and its thermal pad covers the agreed exposed pad.
A table that does not fit, or a package with no exposed pad to check it by (BCV61's SOT-143), still holds.
python3 -m tests.run_all test_crosscheck_table_vs_type_1024"""
import copy

from ecadlib import crosscheck as CC


def r(mn, nom, mx):
    return {"min": mn, "nom": nom, "max": mx}


FDMT_TABLE = {"unit": "mm", "y_axis": "up", "view": "top", "origin": "datum", "footprint_origin": "center",
              "default_pad": {"type": "smd", "shape": "rect"},
              "arrays": [{"count": 4, "x": -3.0, "y": 0.635, "dx": 2.0, "dy": 0, "first_number": 1, "w": 1.25, "h": 1.27},
                         {"count": 4, "x": 3.0, "y": 8.845, "dx": -2.0, "dy": 0, "first_number": 5, "w": 1.25, "h": 1.15}],
              "pads": [{"number": "9", "x": 0.0, "y": 5.52, "w": 7.0, "h": 5.5, "role": "thermal"}]}


def fdmt(table=FDMT_TABLE, ep=(6.9, 5.34), pitch=2.0):
    return {"package_type": "custom", "name": "TDFNW8 8.3x8.4", "pin_count": 9, "pitch": pitch,
            "body_length": r(8.2, 8.3, 8.4), "body_width": r(8.3, 8.4, 8.5),
            "exposed_pad": {"length": r(ep[0] - 0.1, ep[0], ep[0] + 0.1), "width": r(ep[1] - 0.1, ep[1], ep[1] + 0.1)},
            "pad_table": copy.deepcopy(table)}


def qfn(ep=(6.9, 5.34), pitch=2.0):
    return {"package_type": "qfn", "name": "TDFNW8", "pin_count": 9, "pitch": pitch, "body_length": {"nom": 8.3},
            "body_width": {"nom": 8.4}, "exposed_pad": {"length": {"nom": ep[0]}, "width": {"nom": ep[1]}}}


def read(first, second):
    return CC.against_read(CC.critical(first), CC.critical(second), None, None)


def test_the_makers_land_table_settles_a_qfn_word_when_it_fits_the_agreed_numbers():
    c = read(fdmt(), qfn())
    assert c["status"] == "agreed", c
    assert any("pads are built as the read lists them" in t for t in c.get("tie_breaks", [])), c


def test_a_table_whose_rows_step_at_another_pitch_still_holds():
    t = copy.deepcopy(FDMT_TABLE)
    t["arrays"][0]["dx"] = 2.5
    assert read(fdmt(t), qfn())["status"] == "disagreed"


def test_a_thermal_pad_smaller_than_the_exposed_pad_still_holds():
    t = copy.deepcopy(FDMT_TABLE)
    t["pads"][0].update(w=5.0, h=4.0)
    assert read(fdmt(t), qfn())["status"] == "disagreed"


def test_a_table_missing_a_pin_still_holds():
    t = copy.deepcopy(FDMT_TABLE)
    t["arrays"][1]["count"] = 3
    assert read(fdmt(t), qfn())["status"] == "disagreed"


def test_no_exposed_pad_to_check_the_table_by_still_holds():
    first, second = fdmt(), qfn()
    first.pop("exposed_pad")
    second.pop("exposed_pad")
    first["pad_table"]["pads"] = []
    first["pin_count"] = second["pin_count"] = 8
    assert read(first, second)["status"] == "disagreed"


def test_the_other_way_round_still_holds():
    # the build stands on the first read: a gullwing / qfn first read built by its own builder, a table second
    assert read(qfn(), fdmt())["status"] == "disagreed"
