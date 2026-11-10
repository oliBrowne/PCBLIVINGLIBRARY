"""House rule: inputs left, outputs right. A pin typed as a driver (output, open collector / emitter, tri-state) in a
group drawn on the left (reset, clock, debug, usb, adc ...) goes to the right side with its group's name; the group's
other pins stay left."""
from ecadlib import symbol_layout as SL

PINS = [{"num": "1", "name": "OSC_IN", "type": "input"}, {"num": "2", "name": "OSC_OUT", "type": "output"},
        {"num": "3", "name": "NRST", "type": "input"}, {"num": "4", "name": "RESET_OUT", "type": "open_collector"},
        {"num": "5", "name": "SWDIO", "type": "io"}, {"num": "6", "name": "SWO", "type": "tristate"},
        {"num": "7", "name": "TDO", "type": "open_emitter"}, {"num": "8", "name": "VDD", "type": "power"},
        {"num": "9", "name": "GND", "type": "power"}]


def test_driver_pins_of_left_groups_go_right():
    side = {p["name"]: (p["group"], p["side"]) for p in SL.arrange_pins(PINS)}
    assert side["OSC_IN"] == ("clock", "left") and side["OSC_OUT"] == ("clock", "right"), side
    assert side["NRST"] == ("reset", "left") and side["RESET_OUT"][1] == "right", side
    assert side["SWDIO"][1] == "left" and side["SWO"][1] == "right" and side["TDO"][1] == "right", side


def test_driver_pins_right_in_the_drawn_symbol():
    sym = SL.build("X", PINS)
    at = {pr[1]["name"]: pr[2] for pr in sym["prims"] if pr[0] == "pin"}
    assert at["OSC_OUT"] == at["SWO"] == at["TDO"] == at["RESET_OUT"] == "right", at
    assert at["OSC_IN"] == at["NRST"] == "left", at
