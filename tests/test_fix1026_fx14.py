"""Re-review fixes14 (2026-10-26): R-FX14-3."""
from ecadlib import partview as PV
from tests.fixtures_lite import lite


def test_fx14_3_pins_unchecked_reaches_the_panel_label_and_counts():
    """R-RT-2 records pins: unchecked on the plan, but the panel dropped it: the pill was a green 'cross-check: agreed'
    and the counts called the part agreed although no second source compared the symbol's pin names."""
    cc = PV.cross_check({"status": "agreed", "summary": "package agrees with the standard table", "pins": "unchecked"})
    assert cc["pins"] == "unchecked" and "pin names not compared" in cc["label"]
    plain = PV.cross_check({"status": "agreed", "summary": "agrees with a second read"})
    assert "pins" not in plain and plain["label"] == "agrees with a second source"
    held = PV.cross_check({"status": "disagreed", "summary": "", "pins": "unchecked"})
    assert held["status"] == "disagreed" and held["pins"] == "unchecked"


@lite
def test_fx14_3_the_ui_pill_is_not_green_when_pins_are_unchecked(tmp_path):
    """Behaviour, not a grep of the page (R-FX18-6): the web UI server's /api/parts for a plan whose agreed part has
    pins: unchecked gives that card pins and the "pin names not compared" label and counts it, a fully agreed part
    neither; and the page reads exactly those fields for its pill and count."""
    import json
    from pathlib import Path
    from ecadlib.app import Workspace
    from tests.test_ui import serve
    ws = Workspace(tmp_path, [], providers=[], offline=True)
    (tmp_path / "bom.csv").write_text("Ref,MPN\nU1,X\nU2,Y\n")
    out = ws._librarian_dir(ws._bom_path("bom.csv"))
    out.mkdir(parents=True)
    plan = [{"name": n, "status": "ready", "footprint": {"file": "f"}, "symbol": {"file": "s"},
             "source": {"symbol": "datasheet", "footprint": "datasheet"}, "librarian": {"tried": [], "review": []},
             "cross_check": dict({"status": "agreed", "summary": "standard:SOIC-8 agrees (pitch)"}, **extra)}
            for n, extra in (("A", {"pins": "unchecked"}), ("B", {}))]
    (out / "plan.json").write_text(json.dumps(plan))
    srv, call = serve(ws)
    try:
        st, cards = call("GET", "/api/parts?bom=bom.csv&pictures=0")
    finally:
        srv.shutdown()
        srv.server_close()
    assert st == 200, cards
    by = {p["part"]: p["cross_check"] for p in cards["parts"]}
    assert by["A"]["pins"] == "unchecked" and "pin names not compared" in by["A"]["label"], by
    assert "pins" not in by["B"] and by["B"]["label"] == "agrees with a second source", by
    assert cards["counts"]["cross_check_pins_unchecked"] == 1, cards["counts"]
    html = (Path(PV.__file__).parent / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "p.cross_check.pins" in html and "c.cross_check_pins_unchecked" in html


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_fix1026_fx14"]))
