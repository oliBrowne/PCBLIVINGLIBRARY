"""Re-review fixes14 (2026-10-26): R-FX14-3."""
from ecadlib import partview as PV


def test_fx14_3_pins_unchecked_reaches_the_panel_label_and_counts():
    """R-RT-2 records pins: unchecked on the plan, but the panel dropped it: the pill was a green 'cross-check: agreed'
    and the counts called the part agreed although no second source compared the symbol's pin names."""
    cc = PV.cross_check({"status": "agreed", "summary": "package agrees with the standard table", "pins": "unchecked"})
    assert cc["pins"] == "unchecked" and "pin names not compared" in cc["label"]
    plain = PV.cross_check({"status": "agreed", "summary": "agrees with a second read"})
    assert "pins" not in plain and plain["label"] == "agrees with a second source"
    held = PV.cross_check({"status": "disagreed", "summary": "", "pins": "unchecked"})
    assert held["status"] == "disagreed" and held["pins"] == "unchecked"


def test_fx14_3_the_ui_pill_is_not_green_when_pins_are_unchecked():
    from pathlib import Path
    html = (Path(PV.__file__).parent / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'p.cross_check.pins === "unchecked"' in html and "cross_check_pins_unchecked" in html


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_fix1026_fx14"]))
