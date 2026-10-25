"""Free fixes from the 2026-10-24 run's held parts (the road to 88 of 110): a maker land refused because a tab
package's L was taken for a tip-to-tip lead span (C3D08060G), and a pad table refused because its exposed pad is
numbered pin_count + 1 as the read is asked to number it (FDMT800100DC). Values are the reads' own, as saved."""
from ecadlib import footprint_adv as FA, footprint_gen as FG


def _to263(**extra):
    return dict({"name": "TO-263-2", "package_type": "gullwing", "pin_count": 2, "pitch": 2.54,
                 "lead_span": {"min": 14.73, "max": 15.75}, "terminal_length": {"min": 2.29, "max": 2.79},
                 "terminal_width": {"min": 0.71, "max": 0.94}, "body_length": {"min": 10.04, "max": 10.28},
                 "body_width": {"min": 8.89, "max": 9.40},
                 "land": {"pad_length": 3.56, "pad_width": 1.54, "pad_center": 4.8825}}, **extra)


def test_a_tab_package_land_is_not_held_to_a_tip_to_tip_span():
    tab = {"number": "3", "side": "R", "width": {"min": 7.535, "max": 8.425}, "length": {"min": 6.48, "max": 6.88}}
    assert FG.land_short(_to263(tab=tab)) == []
    # with no tab read, L is a lead span and the short pads are still caught
    assert any("short of the lead tip" in x for x in FG.land_short(_to263()))
    # a pad narrower than the lead is caught on a tab package too
    narrow = _to263(tab=tab)
    narrow["land"] = dict(narrow["land"], pad_width=0.5)
    assert any("narrower than terminal_width" in x for x in FG.land_short(narrow))


def _fdmt(ep_w=7.0, ep_h=5.5, role=None, n=8):
    pads = [{"number": str(i), "x": -3.0, "y": 3.0 - 2.0 * (i - 1), "w": 1.25, "h": 1.27} for i in range(1, 5)]
    pads += [{"number": str(i), "x": 3.0, "y": -3.0 + 2.0 * (i - 5), "w": 1.25, "h": 1.15} for i in range(5, 9)]
    pads.append(dict({"number": "9", "x": 1.2, "y": 0, "w": ep_w, "h": ep_h}, **({"role": role} if role else {})))
    return {"name": "PQFN-8", "package_type": "custom", "pin_count": n, "pitch": 2.0,
            "pad_table": {"unit": "mm", "origin": "center", "y_axis": "up", "pads": pads}}


def _count_errors(spec):
    return [c["message"] for c in FA.check_pad_table(spec) if c["rule"] == "count" and c["level"] == "error"]


def test_the_exposed_pad_numbered_after_the_pins_is_not_one_pin_too_many():
    assert _count_errors(_fdmt()) == []                               # onsemi 507AR: pins 1-8, drain pad 9
    assert _count_errors(_fdmt(ep_w=1.25, ep_h=1.2)) != []            # a ninth pad the size of a pin is a pin
    assert _count_errors(_fdmt(ep_w=1.25, ep_h=1.2, role="thermal")) == []   # said to be the thermal pad
    assert _count_errors(_fdmt(n=7)) != []                            # two too many is still wrong
