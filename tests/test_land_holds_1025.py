"""Two parts built wrong in the 2026-10-25 full run (main 48dc8ac) are held instead.
- AQY282GS (Panasonic SOP-4): the datasheet heads its land "Recommended mounting pad" (0.8 x 1.2 mm pads, 6 mm apart);
  no land heading matched it, so the read put the land in its notes and IPC pads 1.6 x 0.55 at +-3.15 were built.
- OPA569DWP (TI SO-20 PowerPAD): the thermal land was read as the body's E1 / D maximum (7.59 x 12.95 under a
  7.4-7.6 x 12.6-13.0 body); copper as large as the body reaches the leads' pads, so no read can mean it.
The page text here is SYNTHETIC.   python3 -m tests.run_all test_land_holds_1025"""
from ecadlib import datasheet as D, ds_route as DR

MOUNTING = ["Recommended mounting pad (Unit: mm)", "0.8", "1.2", "2.54", "6.0", "Tolerance: +-0.1"]


def test_a_recommended_mounting_pad_heading_with_sizes_is_a_land_page():
    assert DR.prints_land("\n".join(MOUNTING))
    assert DR.land_pages(["SOP 4-pin PhotoMOS", "\n".join(MOUNTING)], [1, 2]) == [2]
    for head in ("RECOMMENDED MOUNTING PADS", "Recommended PCB mounting pad"):
        assert DR.prints_land("\n".join([head] + MOUNTING[1:])), head
    # a mounting hole, or a pointer to the mounting pad, is not the land
    assert not DR.prints_land("\n".join(["Mounting hole", "3.2", "6.0"]))
    assert not DR.prints_land("For the recommended mounting pad, see page 12. 0.8 1.2 2.54")


def so20(ep_across, ep_along):
    return {"package_type": "gullwing", "name": "SO-20-1EP", "pin_count": 20, "pitch": 1.27,
            "body_width": {"min": 7.4, "max": 7.6}, "body_length": {"min": 12.6, "max": 13.0},
            "exposed_pad": {"length": {"min": 3.4, "max": 3.81}, "width": {"min": 4.0, "max": 4.32}},
            "land": {"ep_across": ep_across, "ep_along": ep_along}}


def test_a_thermal_land_as_large_as_the_body_is_held():
    line = D.thermal_land_body_sized(so20(7.59, 12.95))
    assert line and line.startswith("no footprint:") and "7.59" in line and "body" in line, line
    assert D.thermal_land_body_sized(so20(12.95, 7.59))            # either way round
    assert D.thermal_land_body_sized(dict(so20(6.0, 13.0), package_type="qfn"))


def test_a_thermal_land_inside_the_body_builds():
    assert D.thermal_land_body_sized(so20(6.045, 12.09)) is None    # KiCad's (TI's) SO-20 PowerPAD land
    assert D.thermal_land_body_sized(so20(None, None)) is None
    assert D.thermal_land_body_sized({k: v for k, v in so20(7.59, 12.95).items() if k != "body_width"}) is None
    assert D.thermal_land_body_sized(dict(so20(7.59, 12.95), land=None)) is None
