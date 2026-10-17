"""Real Altium AD26 runs (review 1018, items a4-a6): what a DbLib part compiles with and where the menu files it.
python3 -m tests.test_review_1018_altium_a4a6

1. the shared Resistor / Capacitor symbol carries no footprint model, whichever source drew it (an EasyEDA symbol
   linked C_0402, so CAP-0805-10uF compiled with DM_Footprint=C_0402): each DbLib row's [Footprint Ref] decides;
2. a part added from the menu (designator U<n>) goes to the table its source's category says (MLCC, MOSFETs, LEDs,
   Crystals, USB Connectors), not to ICs;
3. "Open review queue" lands on the menu's parts with why each is held (the agent queue never holds them);
4. an MPN no source knows is an error ("not found"), not a held part;
5. R / C / L / ferrite / crystal pins are Passive (an EasyEDA pin typed Input made Altium warn about floating inputs)."""
import csv, re
from pathlib import Path

from ecadlib import altium_export as X, altium_lib as AL, altium_menu as AM, librarian as L
from tests.fixtures_lite import lite

ROOT = Path(__file__).parent.parent
BOM = """Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,LCSC Part Number,Description
C1,1,100nF,C_0402_1005Metric,ACME,CAP0402X,C1001,Capacitor 100nF 0402
C2,1,10uF,C_0805_2012Metric,ACME,CAP0805X,C1002,Capacitor 10uF 0805
R1,1,10k,R_0603_1608Metric,ACME,RES0603X,C1003,Resistor 10k 0603
"""


def _easyeda_run(d):
    from tests import test_librarian as TL
    http = TL.FakeHTTP({"C1001": TL.ee_as("CAP0402X", "C1001"), "C1002": TL.ee_as("CAP0805X", "C1002"),
                        "C1003": TL.ee_as("RES0603X", "C1003")})
    items, _ = TL.run(d, http, bom=BOM, sources=["easyeda"])
    return items


@lite
def test_shared_symbol_has_no_footprint_whichever_source_drew_it(tmp_path):
    items = _easyeda_run(tmp_path)
    assert items["CAP-0402-100nF"]["symbol"]["ref"] == items["CAP-0805-10uF"]["symbol"]["ref"] == "Capacitor"
    assert items["CAP-0805-10uF"]["footprint"]["ref"] == "C_0805_2012Metric"
    for name in ("Capacitor", "Resistor"):
        f = next(tmp_path.rglob(f"{name}.SchLib"))
        recs = [r for c in AL.read_schlib(f)[1] for r in c["records"]]
        assert not [r for r in recs if r.get("RECORD") == "45"], (name, "a footprint model on the shared symbol")
        assert not re.search(rb"ModelName=", f.read_bytes())
        recipe = f.with_name(f.name + ".ecadbuild").read_text(encoding="utf-8")
        assert "FOOTPRINTLINK" not in recipe and "\tMPN\t" not in recipe and "\tLCSC\t" not in recipe, recipe


@lite
def test_passive_parts_get_passive_pins(tmp_path):
    items = _easyeda_run(tmp_path)          # the EasyEDA fixture types its pins 1 = input, 2 = output, 3 = bidirectional
    assert items["RES-0603-10k"]["source"]["symbol"].startswith("easyeda:")
    recipe = next(tmp_path.rglob("Resistor.SchLib.ecadbuild")).read_text(encoding="utf-8")
    etypes = {ln.split("\t")[5] for ln in recipe.splitlines() if ln.startswith("PIN\t")}
    assert etypes == {str(AL.ETYPE["passive"])}, etypes
    # the helpers on their own: a crystal / ferrite by folder or designator; an oscillator keeps its pin types
    sym = {"prims": [("pin", {"num": "1", "name": "1", "type": "input"}, "left", 0, 0, 10),
                     ("pin", {"num": "2", "name": "2", "type": "output"}, "right", 0, 0, 10)]}
    assert {pr[1]["type"] for pr in L.passive_pins(sym)["prims"]} == {"passive"}
    osc = {"prims": [("pin", {"num": "1", "name": "OE", "type": "input"}, "left", 0, 0, 10),
                     ("pin", {"num": "3", "name": "OUT", "type": "output"}, "right", 0, 0, 10)]}
    assert L.passive_pins(osc) == osc
    assert L.is_passive_part({"folder": "Components/Crystals"}) and L.is_passive_part({"folder": "ICs"}, {"designator": "FB1"})
    assert not L.is_passive_part({"folder": "Components/ICs"}, {"designator": "U1"})


def test_menu_parts_go_to_their_category_table():
    cases = [("Capacitors", "16V 100nF X7R ±10% 0402 Multilayer Ceramic Capacitors MLCC - SMD/SMT ROHS", ""),
             ("Capacitors", "CAP CER 0.1UF 16V X7R 0402", ""),
             ("Transistors", "30V 5.8A 1.4W 28mΩ@10V,5.8A 1.45V@250uA 1 N-Channel SOT-23 MOSFETs ROHS", ""),
             ("Transistors", "MOSFETs, SOT-23", "SOT-23"),                 # an IC package does not stop a MOSFET
             ("Transistors", "MOSFET N-CH 30V 5.7A SOT23-3", ""),
             ("Diodes", "Red 0603 Light Emitting Diodes (LED) ROHS", ""), ("Diodes", "LED RED CLEAR 0603 SMD", ""),
             ("Crystals", "12MHz SMD3225-4P Crystals ROHS", ""), ("Crystals", "CRYSTAL 12.0000MHZ 20PF SMD", ""),
             ("Connectors", "USB 3.1 1 Type-C SMD USB Connectors ROHS", ""),
             ("Connectors", "CONN RCPT USB2.0 TYPEC 16POS SMD RA", ""), ("Resistors", "RES 10K OHM 1% 1/10W 0603", ""),
             (None, "MOSFET driver", ""), (None, "Half-Bridge Gate Driver for MOSFETs, SOIC-8", ""),
             (None, "LED driver IC, constant current", "SOT-23-6"), (None, "Crystal oscillator 12MHz 3.3V", ""),
             (None, "LDO regulator, stable with ceramic output capacitors", "SOT-223"),
             (None, "Precision Matched Resistors", "SOT-23")]
    for want, desc, pkg in cases:
        assert X.kind_of([desc], [pkg]) == want, (desc, X.kind_of([desc], [pkg]))
    # the menu's lines: designator U<n>, Value = MPN, the description only from the triage's source
    import pandas as pd
    from ecadlib import altium365 as A365, altium_dblib_sync as S
    mpns = {"CL05B104KO5NNNC": "16V 100nF X7R ±10% 0402 Multilayer Ceramic Capacitors MLCC - SMD/SMT ROHS",
            "AO3400A": "30V 5.8A 1.4W 28mΩ@10V,5.8A 1.45V@250uA 1 N-Channel SOT-23 MOSFETs ROHS",
            "KT-0603R": "Red 0603 Light Emitting Diodes (LED) ROHS", "X322512MSB4SI": "12MHz SMD3225-4P Crystals ROHS",
            "TYPE-C-31-M-12": "USB 3.1 1 Type-C SMD USB Connectors ROHS", "STM32F103C8T6": "ARM Cortex-M3 MCU 64KB Flash"}
    lines = [{"designator": f"U{i}", "mpn": m, "value": m} for i, m in enumerate(mpns, 1)]
    tri = pd.DataFrame([{"mpn": m, "package": "", "local_footprint": "", "src_description": d} for m, d in mpns.items()], dtype=str)
    got = {i["name"]: S.table_of(i) for i in A365.plan(lines, tri)}
    assert got == {"CL05B104KO5NNNC": "Capacitors", "AO3400A": "Transistors", "KT-0603R": "Diodes",
                   "X322512MSB4SI": "Crystals", "TYPE-C-31-M-12": "Connectors", "STM32F103C8T6": "ICs"}, got


@lite
def test_review_queue_opens_the_menus_held_parts(tmp_path):
    from tests import test_altium_menu as TM
    from ecadlib import agent_queue as AQ
    menu, cfg = TM._setup(tmp_path / "m")
    assert AM.queue_anchor(cfg["workspace"]) == AM.QUEUE_ANCHOR            # nothing added from the menu yet
    r = AM.run_job(TM._job(menu, {"action": "add-part", "mpn": "NE555DR", "manufacturer": "Texas Instruments"}))
    assert r["status"] == "held" and "review queue" in r["fix"], r
    assert AQ.ws_list(cfg["workspace"])["items"] == []                    # the agent's queue: never these parts
    assert AM.queue_anchor(cfg["workspace"]) == "#review=uploads/altium-menu/parts.csv"
    opened = []
    out = AM.open_queue(cfg, opener=opened.append, probe=lambda p: ("ecadlib", str(cfg["workspace"])), launcher=None)
    assert out["status"] == "ok" and opened == [f"http://127.0.0.1:{cfg['port']}/#review=uploads/altium-menu/parts.csv"]
    # what the page shows for that BOM: the held part and why
    ws = AM._workspace(cfg)
    parts = ws.library_parts(AM.MENU_BOM, False)
    p = next(x for x in parts["parts"] if x["part"] == "NE555DR")
    assert p["status"] == "held" and parts["counts"]["held"] == 1
    assert any("no symbol" in str(w.get("text", "")).lower() for w in p["reasons"]), p["reasons"]
    page = (ROOT / "ecadlib" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "async function openReview()" in page and "#review=" in page and ".then(openReview)" in page


@lite
def test_mpn_no_source_knows_is_an_error_not_held(tmp_path):
    held = {"status": "held", "symbol": {"ref": "X"}, "footprint": {"ref": "X"}}
    cols = ["line", "mpn", "status", "local_symbol", "local_footprint", "src", "src_mpn", "src_description", "notes"]

    def tri(**row):
        f = tmp_path / "triage.csv"
        with f.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, cols)
            w.writeheader()
            w.writerow({"line": "1", "mpn": "XYZ-NOT-A-PART-123", **row})
        return f
    asked = tri(status="needs_footprint_generation", notes="jlcpcb: no exact MPN match; not configured: nexar")
    assert AM.not_found(asked, "xyz-not-a-part-123", held)
    assert AM.not_found(tri(status="needs_footprint_generation", src="nexar"), "XYZ-NOT-A-PART-123", held)
    # offline / a source unreachable / nobody asked / something found: still held, the menu cannot tell
    assert not AM.not_found(tri(status="unverified (source unreachable)", notes="jlcpcb_search: offline"), "XYZ-NOT-A-PART-123", held)
    assert not AM.not_found(tri(status="needs_footprint_generation", notes="not configured: nexar"), "XYZ-NOT-A-PART-123", held)
    assert not AM.not_found(tri(status="needs_footprint_generation", src="nexar", src_mpn="XYZ"), "XYZ-NOT-A-PART-123", held)
    assert not AM.not_found(asked, "XYZ-NOT-A-PART-123", {**held, "symbol": {"ref": "X", "file": "m/X.SchLib"}})
    assert not AM.not_found(asked, "XYZ-NOT-A-PART-123", {**held, "status": "ready"})
    # offline end to end it stays held (the sources were not asked)
    from tests import test_altium_menu as TM
    menu, cfg = TM._setup(tmp_path / "m")
    r = AM.run_job(TM._job(menu, {"action": "add-part", "mpn": "XYZ-NOT-A-PART-123"}))
    assert r["status"] == "held", r


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_review_1018_altium_a4a6"]))
