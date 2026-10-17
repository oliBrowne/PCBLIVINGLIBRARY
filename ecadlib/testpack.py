"""A small Altium + KiCad test pack for a person to open in the real tools (no network, no keys).

    ecadlib testpack --out DIR [--name LIB] [--json]

Six representative parts go through the same pipeline a BOM does (librarian -> release check -> exports):

    R1  RC0603FR-0710KL   0603 chip resistor         footprint + symbol from ecadlib's standard parts (stdparts)
    Q1  MMBT3904LT1G      SOT-23 NPN transistor      footprint + symbol (1=B 2=E 3=C) from stdparts
    U1  LM358DR           SOIC-8 dual op amp         footprint from stdparts, symbol from the pack's own library
    U2  TPS62133RGTR      VQFN-16 with exposed pad   footprint from package_specs (pad 17 = EP), symbol from the pack library
    J1  TYPE-C-31-M-12    USB-C receptacle (HRO)     footprint from package_specs/advanced (stacked pads, slots, NPTH)
    J2  61300511121       1x05 2.54 mm THT header    footprint from package_specs/advanced (drilled pads, square pin 1)

DIR gets:
    CHECKLIST.md        what to open in Altium Designer, Altium 365 and KiCad, and what each part must look like
    bom.csv             the BOM the pack was built from
    source/             the pack's own "existing library" (.kicad_sym + .pretty), used like a user's --lib
    library/            the librarian's output: plan.json, review.md, release_check.md, models/ (SchLib, PcbLib, KiCad)
    altium/             a Database Library: testpack.DbLib + .xlsx/.csv + SchLib/ + PcbLib/ (ecadlib dblib-sync)
    kicad/              testpack.kicad_sym, testpack.pretty, testpack.3dshapes, fp-lib-table, sym-lib-table, and the
                        database library testpack.kicad_dbl + testpack.sqlite (ecadlib kicad-export / kicad-dblib)
    testpack.json       what was built, for scripts

The pin maps of U1, U2 and J1 are written from the manufacturers' datasheets (and match KiCad's own symbols for the
same parts); the checklist still asks for a look against the datasheet, as for any library part."""
import argparse, json, sys
from pathlib import Path

from . import footprint_gen as F, sexpr

NAME = "testpack"
DS = {"R1": "https://www.yageo.com/upload/media/product/productsearch/datasheet/rchip/PYu-RC_Group_51_RoHS_L_12.pdf",
      "Q1": "https://www.onsemi.com/pdf/datasheet/mmbt3904lt1-d.pdf",
      "U1": "https://www.ti.com/lit/ds/symlink/lm358.pdf",
      "U2": "https://www.ti.com/lit/ds/symlink/tps62130.pdf",
      "J1": "https://www.lcsc.com/datasheet/C165948.pdf",
      "J2": "https://www.we-online.com/components/products/datasheet/61300511121.pdf"}
# designator, value, BOM footprint, manufacturer, MPN, description
PARTS = [
    ("R1", "10k", "R_0603_1608Metric", "Yageo", "RC0603FR-0710KL", "Resistor 10k 1% 0.1W 0603"),
    ("Q1", "MMBT3904", "SOT-23", "onsemi", "MMBT3904LT1G", "NPN transistor 40V 200mA SOT-23"),
    ("U1", "LM358DR", "SOIC-8_3.9x4.9mm_P1.27mm", "Texas Instruments", "LM358DR", "Dual operational amplifier SOIC-8"),
    ("U2", "TPS62133RGTR", "VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm", "Texas Instruments", "TPS62133RGTR",
     "3-17V 3A step-down converter 3.3V VQFN-16 3x3mm with exposed pad"),
    ("J1", "USB-C", "USB_C_Receptacle_HRO_TYPE-C-31-M-12", "Korean Hroparts Elec", "TYPE-C-31-M-12",
     "USB 2.0 Type-C receptacle, 16 pins, SMD with THT shell pegs"),
    ("J2", "Conn_01x05", "PinHeader_1x05_P2.54mm_Vertical", "Wurth Elektronik", "61300511121",
     "Pin header 1x05 2.54mm vertical, through hole"),
]
# the pack's own symbols (name -> pins: num, name, electrical type, side); footprints of J1 / J2 go in source/ too
SYMBOLS = {
    "LM358DR": ("U", "SOIC-8_3.9x4.9mm_P1.27mm", [
        ("3", "+IN_A", "input", "L"), ("2", "-IN_A", "input", "L"), ("5", "+IN_B", "input", "L"), ("6", "-IN_B", "input", "L"),
        ("8", "V+", "power_in", "L"), ("1", "OUT_A", "output", "R"), ("7", "OUT_B", "output", "R"), ("4", "V-", "power_in", "R")]),
    "TPS62133RGTR": ("U", "VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm", [
        ("11", "PVIN", "power_in", "L"), ("12", "PVIN", "power_in", "L"), ("10", "AVIN", "power_in", "L"),
        ("13", "EN", "input", "L"), ("9", "SS/TR", "input", "L"), ("7", "FSW", "input", "L"), ("8", "DEF", "input", "L"),
        ("6", "AGND", "power_in", "L"), ("1", "SW", "output", "R"), ("2", "SW", "output", "R"), ("3", "SW", "output", "R"),
        ("14", "VOS", "input", "R"), ("5", "FB", "input", "R"), ("4", "PG", "open_collector", "R"),
        ("15", "PGND", "power_in", "R"), ("16", "PGND", "power_in", "R"), ("17", "EP", "power_in", "R")]),
    "TYPE-C-31-M-12": ("J", "USB_C_Receptacle_HRO_TYPE-C-31-M-12", [
        ("A4", "VBUS", "power_in", "L"), ("A9", "VBUS", "power_in", "L"), ("B4", "VBUS", "power_in", "L"),
        ("B9", "VBUS", "power_in", "L"), ("A5", "CC1", "bidirectional", "L"), ("B5", "CC2", "bidirectional", "L"),
        ("A6", "D+", "bidirectional", "L"), ("B6", "D+", "bidirectional", "L"), ("A7", "D-", "bidirectional", "L"),
        ("B7", "D-", "bidirectional", "L"), ("A8", "SBU1", "bidirectional", "R"), ("B8", "SBU2", "bidirectional", "R"),
        ("A1", "GND", "power_in", "R"), ("A12", "GND", "power_in", "R"), ("B1", "GND", "power_in", "R"),
        ("B12", "GND", "power_in", "R"), ("SH", "SHIELD", "passive", "R")]),
    "61300511121": ("J", "PinHeader_1x05_P2.54mm_Vertical", [(str(i), f"Pin_{i}", "passive", "L") for i in range(1, 6)]),
}
WIN_DIR = r"C:\ecadlib\testpack"            # where the checklist asks for the altium folder (the .DbLib's Excel path)
LOCAL_FP = ("USB_C_Receptacle_HRO_TYPE-C-31-M-12", "PinHeader_1x05_P2.54mm_Vertical", "VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm")
# what a person must see, per part (CHECKLIST.md)
EXPECT = {   # checked against the generated .kicad_mod by tests/test_testpack.py
    "RES-0603-10k": "2 pads 0.8 x 0.95 mm, centres 1.65 mm apart; symbol: 2-pin resistor",
    "MMBT3904LT1G": "3 pads 1.475 x 0.6 mm: 1 and 2 on the left 1.9 mm apart, 3 alone on the right, rows 1.875 mm apart; "
                    "symbol pin 1 = B, 2 = E, 3 = C",
    "LM358DR": "8 pads 1.95 x 0.6 mm in two rows of 4, 1.27 mm pitch, rows 4.95 mm apart (centres), pin 1 top-left; "
               "symbol: 8 pins, 1 = OUT_A, 4 = V-, 8 = V+",
    "TPS62133RGTR": "16 pads 0.835 x 0.25 mm, 0.5 mm pitch, 4 per side, pin 1 top of the left side; pad 17 = exposed pad "
                    "1.68 x 1.68 mm in the centre with 4 paste windows; symbol pin 17 = EP",
    "TYPE-C-31-M-12": "16 numbered SMD pads on 12 positions (A1/B12, A4/B9, A9/B4, A12/B1 stacked), 4 shell pads SH with "
                      "slotted drills, 2 unplated 0.65 mm holes; symbol has every A/B pin and SHIELD",
    "61300511121": "5 through-hole pads 1.7 mm, 1.0 mm drill, 2.54 mm pitch in a line; pad 1 square; symbol pins 1-5",
}


# ---------------------------------------------------------------- the pack's own KiCad library (source/)
def kicad_symbol(name, ref, footprint, pins, desc=""):
    """One KiCad symbol: rectangle body, pins left (L) and right (R) on a 100 mil grid."""
    q = sexpr.quote
    left, right = [p for p in pins if p[3] == "L"], [p for p in pins if p[3] == "R"]
    rows = max(len(left), len(right), 1)
    top, bot = 2.54 * ((rows + 1) // 2), 2.54 * ((rows + 1) // 2 - rows - 1)
    w = 7.62 if max([len(p[1]) for p in pins] or [1]) < 6 else 10.16
    font = "(effects (font (size 1.27 1.27)))"
    prop = lambda k, v, y, hide=False: (f'    (property {q(k)} {q(v)} (at 0 {y:g} 0) (effects (font (size 1.27 1.27))'
                                        f'{" hide" if hide else ""}))')
    out = [f"  (symbol {q(name)} (in_bom yes) (on_board yes)",
           prop("Reference", ref, top + 1.27), prop("Value", name, bot - 1.27), prop("Footprint", f"{NAME}:{footprint}", 0, True),
           prop("Datasheet", "~", 0, True), prop("Description", desc, 0, True),
           f'    (symbol "{name}_0_1" (rectangle (start {-w:g} {top:g}) (end {w:g} {bot:g}) (stroke (width 0.254) (type default)) '
           f'(fill (type background))))', f'    (symbol "{name}_1_1"']
    for side, ps, x, rot in (("L", left, -w - 2.54, 0), ("R", right, w + 2.54, 180)):
        for i, (num, pname, typ, _) in enumerate(ps):
            y = round(top - 2.54 * (i + 1), 2)
            out.append(f"      (pin {typ} line (at {x:g} {y:g} {rot}) (length 2.54) (name {q(pname)} {font}) "
                       f"(number {q(num)} {font}))")
    out.append("    ))")
    return "\n".join(out)


def write_source(d):
    """source/testpack.kicad_sym and source/testpack.pretty: the pack's 'existing library'."""
    from .footprint_bench import spec_path
    d = Path(d)
    pretty = d / f"{NAME}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    for n in LOCAL_FP:
        text, _ = F.build(json.loads(spec_path(n).read_text(encoding="utf-8")))
        (pretty / f"{n}.kicad_mod").write_text(text, encoding="utf-8", newline="\n")
    syms = [kicad_symbol(n, ref, fp, pins, next((p[5] for p in PARTS if p[4] == n), "")) for n, (ref, fp, pins) in SYMBOLS.items()]
    text = '(kicad_symbol_lib (version 20220914) (generator "ecadlib_testpack")\n' + "\n".join(syms) + "\n)\n"
    sexpr.parse(text)
    (d / f"{NAME}.kicad_sym").write_text(text, encoding="utf-8", newline="\n")
    return d


def write_bom(path):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Qty", "Value", "Footprint", "LibRef", "Manufacturer", "Mfr. Part #", "Description", "Datasheet"])
        for des, val, fp, mfr, mpn, desc in PARTS:
            w.writerow([des, 1, val, fp, mpn if mpn in SYMBOLS else "", mfr, mpn, desc, DS[des]])
    return Path(path)


# ---------------------------------------------------------------- build
def build(out, name=NAME, log=print):
    """Write the whole pack into `out`. Returns the summary dict (also testpack.json)."""
    from . import altium_dblib_sync as DS_, kicad_dblib as KD, kicad_export as X, librarian as L, parts_export as PE
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    src = write_source(out / "source")
    bom = write_bom(out / "bom.csv")
    quiet = lambda *a, **k: None
    items = L.run(str(bom), [str(src)], out / "library", ["standard"], None, False, [], True, out / "library" / "cache",
                  0.0, [], log=quiet, gen_step=True)
    plan = out / "library" / "plan.json"
    held = {i["name"]: i.get("hold_reasons") for i in items if i["status"] != "ready"}
    kx = X.export(plan, out / "kicad", name, libs=[str(src)], log=quiet)
    kd = KD.build(plan, out / "kicad", name, log=quiet)
    ad = DS_.sync(plan, out / "altium", name=name, win_dir=WIN_DIR)
    PE.write(PE.rows(plan, name), out / "library" / "parts.csv", "csv")
    from . import parity as PY                 # both libraries read back and compared, part by part and row by row
    py = PY.check_plan(plan, out / "kicad", name, out / "altium")
    PY.write_report(py, out / "parity")
    parts = []
    for it in items:
        parts.append({"part": it["name"], "status": it["status"], "symbol": (it.get("symbol") or {}).get("ref"),
                      "footprint": (it.get("footprint") or {}).get("ref"), "source": it.get("source"),
                      "expect": EXPECT.get(it["name"], ""), "hold_reasons": it.get("hold_reasons") or []})
    tables = sorted({str(i.get("folder") or "").rstrip("/").split("/")[-1] for i in items if i["status"] == "ready"})
    summary = {"name": name, "tables": tables, "parts": parts, "held": held, "kicad": {"symbols": kx.get("symbols"),
               "footprints": sorted(kx.get("footprints") or {})}, "kicad_dblib": kd.get("dblib") if isinstance(kd, dict) else None,
               "altium": {"dblib": str(Path(ad["dblib"]).relative_to(out).as_posix()) if ad.get("dblib") else None},
               "parity": {**py["counts"], "database_rows": py.get("database_rows"), "report": "parity.md"}}
    (out / "testpack.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out / "CHECKLIST.md").write_text(checklist(summary), encoding="utf-8")
    return summary


def checklist(s):
    n, win, tables = s["name"], WIN_DIR, ", ".join(s.get("tables") or [])
    rows = "\n".join(f"| {p['part']} | {p['footprint']} | {p['symbol']} | {p['expect']} |" for p in s["parts"])
    held = "".join(f"\n* **{k}** was held by the release check: {'; '.join(v or [])}. It is left out of the exports; "
                   f"tell us." for k, v in s["held"].items())
    return f"""# ecadlib test pack: what to open and what you should see

This folder was written by `ecadlib testpack` with no network and no API keys. Nothing in it has been opened in
Altium Designer, Altium 365 or KiCad by ecadlib's own tests (see the end of this file for what was checked); please go
through the steps below and report anything that does not match (a screenshot and the step number are enough).

## The six parts

| Part | Footprint | Symbol | What it must look like |
|---|---|---|---|
{rows}
{held}

Check every part against its datasheet link (the Datasheet parameter) as you would any new library part.

## 1. Altium Designer: the Database Library (`altium/`)

1. Copy the contents of the `altium` folder to `{win}` on the Windows PC (the .DbLib's Excel connection points there;
   elsewhere, Altium asks for the data source: point it at `{n}.xlsx` next to the .DbLib, Excel / Microsoft.ACE OLE DB
   provider, and click **Connect**).
2. Open `{win}\\{n}.DbLib`.
   * You should see one table per category ({tables}), one row per part.
   * Library Path / Footprint Path are relative: the `SchLib\\` and `PcbLib\\` folders next to the .DbLib.
3. In the **Components** panel pick the library, then each part:
   * the symbol preview draws, with the pin numbers listed in the table above;
   * the footprint preview draws (2D and 3D), and the 3D body sits on the pads at a sensible height;
   * the parameters Manufacturer, Manufacturer Part Number and the Datasheet link (ComponentLink2URL) are filled.
4. Place every part on a new schematic, **Design > Update PCB Document** into a new PCB: every footprint arrives with
   no "footprint not found" message. Run **Tools > Design Rule Check**: no violations between pads of one part,
   except on the USB-C receptacle if its stacked pads (A1/B12 GND, A4/B9 VBUS ...) are left on different nets.
5. Open each `.PcbLib` in `altium/PcbLib/` directly: one footprint each, pads as in the table; the courtyard is on
   Mechanical 15 and the assembly outline on Mechanical 13 (ecadlib's default layers).
6. Open each `.SchLib` in `altium/SchLib/` directly: one symbol each; pin names as in the table.

## 2. Altium 365

Nothing was sent to Altium 365. To try it: in Altium Designer, with the workspace signed in, open each `.SchLib` /
`.PcbLib`, then **Right-click > Release** (or **File > Save to Server**) into a test folder, then make a component
from them in the Component Editor. Or, with a Nexar token, from a shell:
`ecadlib altium365 push library/plan.json --workspace <your workspace URL> --dry-run` (shows the exact requests;
remove --dry-run to release into the workspace for real, into Components/<category>).

## 3. KiCad 8 or later (`kicad/`)

The KiCad files are in KiCad 8 format: KiCad 7 refuses to open them (it will say the library is from a newer version).

1. Create an empty KiCad project, then copy everything in `kicad/` into the project folder. The project now has
   `{n}` in both library tables (**Preferences > Manage Symbol / Footprint Libraries > Project Specific Libraries**),
   with paths `${{KIPRJMOD}}/{n}.kicad_sym` and `${{KIPRJMOD}}/{n}.pretty`.
2. Symbol editor: library `{n}` lists the six parts; each opens and draws; **Inspect > Symbol Checker**: no errors.
3. Footprint editor: library `{n}` lists the footprints; open each: pads as in the table above, courtyard on F.Courtyard,
   **Inspect > Footprint Checker**: note anything it lists. Expected for the USB-C receptacle only: its stacked pads
   (A1/B12, A4/B9, A9/B4, A12/B1) overlap and its four shell pads share the number SH, as in KiCad's own footprint.
4. **3D viewer** (footprint editor or the board): every footprint has a 3D model from `{n}.3dshapes/`, the same one the
   Altium footprint carries (ecadlib's generated STEP; for the USB-C receptacle its Altium extruded body as a STEP).
5. In the schematic, place all six parts, assign footprints (already set), **Update PCB from Schematic**: nothing
   missing. In the PCB, **Inspect > Design Rules Checker** with *Test for parity between PCB and schematic*: no
   library errors ("footprint not found in library" or "doesn't match library").
6. Database library (optional; needs an SQLite ODBC driver, see `ecadlib kicad-dblib -h`): the `{n}-db` row in the
   symbol table opens `{n}.kicad_dbl`; the parts appear grouped by category with Value, Manufacturer and MPN columns.

## What ecadlib itself checked before writing this pack

* the release check passed for every part not listed as held above (pins match pads, footprint checks, 3D height,
  fields, files read back);
* KiCad / Altium parity (`ecadlib parity`, report `parity.md`): {s.get('parity', {}).get('match', '?')} of {len(s['parts'])} parts
  read back the same from the KiCad and the Altium files (pads, drills, paste, courtyard, 3D model, pins, fields), and
  the KiCad database rows match the DbLib rows;
* `tests/test_e2e.py` (run in ecadlib's own test suite) loads a pack like this one with KiCad 7's kicad-cli and pcbnew
  (after converting to KiCad 7 syntax), places every footprint through the library table and runs DRC, and reads every
  .PcbLib / .SchLib back with pyaltiumlib and ecadlib's own reader. That is all file-level: the real Altium checks are
  the steps above.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib testpack",
                                 description="Write a small Altium + KiCad test pack (6 representative parts) and a CHECKLIST.md "
                                             "saying what to open in Altium Designer / Altium 365 / KiCad and what to see.",
                                 epilog="example: ecadlib testpack --out testpack   (then open testpack/CHECKLIST.md)")
    ap.add_argument("--out", "-o", default="ecadlib_testpack", help="folder to write (default %(default)s)")
    ap.add_argument("--name", default=NAME, help="library name in the pack (default %(default)s)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    a = ap.parse_args(argv)
    s = build(a.out, a.name)
    if a.json:
        print(json.dumps(s, indent=2))
        return 0 if not s["held"] else 2
    for p in s["parts"]:
        print(f"  {p['status']:<6} {p['part']:<18} fp={p['footprint']:<40} sym={p['symbol']}")
    print(f"{len(s['parts'])} parts, {len(s['held'])} held -> {Path(a.out) / 'CHECKLIST.md'} "
          f"(Altium: {Path(a.out) / 'altium'}, KiCad: {Path(a.out) / 'kicad'})")
    return 0 if not s["held"] else 2


if __name__ == "__main__":
    sys.exit(main())
