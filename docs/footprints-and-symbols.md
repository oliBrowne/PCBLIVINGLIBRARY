# Footprints, symbols and 3D models

How ecadlib draws land patterns, symbols and STEP models, and how it checks them.

On this page:

* [`footprint_gen.py`: IPC-7351B footprint generator](#footprint_genpy-ipc-7351b-footprint-generator)
* [`stdparts.py`: standard parts without Claude](#stdpartspy-standard-parts-without-claude)
* [`symbol_layout.py`: one pin rule for every IC symbol](#symbol_layoutpy-one-pin-rule-for-every-ic-symbol)
* [`step_gen.py`: package-shaped 3D models (STEP)](#step_genpy-package-shaped-3d-models-step)
* [`visual_check.py`: visual proof against the datasheet](#visual_checkpy-visual-proof-against-the-datasheet)

## `footprint_gen.py`: IPC-7351B footprint generator

```
ecadlib footprint gen ecadlib/package_specs/*.json -o out/footprints/ecadlib.pretty [--density A|B|C]
ecadlib footprint compare <mine.kicad_mod> <reference path or URL>   # every pad; exit 1 when a pad differs or is on one side only
ecadlib check <any.kicad_mod or .pretty> ... # the footprint rule book, works on KiCad's own footprints too
ecadlib check --summary Lib.pretty/         # counts per rule for a whole library
```

* **Package types** (`package_type`, with `lead_type` to pick a different IPC lead family):
  * `chip`: 2-terminal R/C/L/LED. IPC table 3-5 for 0603 and up, table 3-6 below 0603. `inner_span` takes the gap directly when the drawing gives it (LEDs).
  * `molded`: inward-L / molded body: tantalum, SMA/SMB/SMC, SOD-123F (table 3-13). `lead_type: "melf"` for MELF.
  * `gullwing`: SOIC/SOP/TSSOP/MSOP, plus SOT-23/SOD-123 via `pin_positions` and SOT-223/DPAK via `tab`.
  * `qfp`: 4-sided gull-wing. `qfn`: QFN/DFN/SON with an optional exposed pad (`pins_top_bottom: 0` for DFN).
  * `polarized: true` adds KiCad's pin-1 silk bracket and fab chamfer (LED/diode: pin 1 = cathode; tantalum: pin 1 = +).
  * `land`: the maker's recommended land pattern, when the datasheet has one. It replaces the IPC pads.
* **Numbers** follow what KiCad builds its own library with (fillet tables from KiCad's `ipc_7351b.yaml`; see [docs/footprint-rules.md](footprint-rules.md)):
  `Z = Lmin + 2Jt + √(CL²+F²+P²)`, `G = Smax − 2Jh − √(CS²+F²+P²)`, `X = Wmin + 2Js + √(CW²+F²+P²)`,
  RMS tolerance for S, F = 0.10, P = 0.05, rounded to 0.05 mm (0.02 for chips under 0603).
  Opposite gull-wing rows stay ≥ 0.4 mm apart, and pad heels are pulled back to keep 0.2 mm from an exposed pad.
* **Output** (KLC F5/F6): silk 0.12 cut back 0.2 mm from the real rounded pad outline (0.1 mm on tiny parts),
  fab 0.10 with a pin-1 chamfer, courtyard 0.05 on the 0.01 mm grid with 0.25 mm excess (0.15 under 0603),
  roundrect pads, exposed-pad paste split into windows at 65 %. Format `version 20221018` (KiCad 7+; Altium imports it).
* **Checks:** every generated footprint is run through `footprint_check` (copper gaps, silk clearance, fab,
  courtyard, pin 1, paste), and every footprint the release check sees through the
  [footprint rule book](footprint-rulebook.md): naming (KLC and IPC-7351 names checked against the copper),
  part type, origin, pad layers and shapes, annular rings, thermal vias, text, 3D model, courtyard clearance,
  polarity marks, and the pad-to-pin map (an unwired exposed pad, a reversed diode). Spec mistakes, such as lead span and body width swapped, mils instead of mm,
  or more pins than fit, are refused with a plain-language reason.

### Compared with KiCad's official footprints

`tests/test_footprints.py` generates 26 example parts and compares pad 1 and the courtyard with KiCad's library:

| Kind | Parts | Result |
|---|---|---|
| Resistors | R_0402, R_0603, R_0805, R_1206 | identical pads, courtyard and silk |
| Capacitors | C_0805 | identical; C_0402 within 0.03 mm (typical body sizes, not KiCad's) |
| LEDs | LED_0603, LED_0805 | identical, including the cathode bracket |
| Inductor | L_0805 | within 0.3 mm (KiCad uses a different body size for inductors) |
| ICs | SOT-23, SOT-23-5, SOIC-8, SOIC-16, TSSOP-16, LQFP-48, QFN-16, QFN-32 | identical |
| ICs (typical inputs) | MSOP-8, DFN-8, SOT-223 | within 0.15 / 0.3 / 0.6 mm |
| Diodes, tantalum | SMA, SMB, SOD-123, SOD-323, tantalum A | IPC-7351B result; KiCad draws these by hand from the maker's land, so give `land` to match it exactly |

All 26 pass the rule check, as do all 27 KiCad reference footprints.

### Harder footprints: BGAs, connectors and odd shapes (`footprint_adv.py`)

```
ecadlib footprint gen ecadlib/package_specs/advanced/*.json -o out/footprints/ecadlib.pretty
ecadlib footprint-bench [--offline] [--fixture tests/fixtures/kicad_bench.json] [--save-fixture FILE]
```

* `bga` (BGA/LGA/CSP): `rows` x `cols` at `pitch` (`pitch_y`), rows lettered A, B, ... from the top without I O Q S X Z
  (JEDEC; AA after Y), columns numbered from the left. Missing balls as `depopulated` names or ranges (`"E5:J9"`) or as a
  `ball_map` (one string per row, `-` = no ball). Land: the datasheet's `land_diameter`, else IPC-7351B from the nominal
  `ball_diameter` (collapsing balls −25 % from 0.55 mm, −20 % from 0.25 mm, −15 % below; non-collapsing +5 %; LGA =
  terminal). `land_style` NSMD (mask 0.05 mm bigger) or SMD (copper 0.05 mm bigger, mask = land). Courtyard body + 1.0 mm.
  Silk and fab mark the A1 corner.
* `pin_header`: 1 or 2 columns, through-hole (pin 1 at the origin, square pad 1, KiCad's drill/pad per pitch; a
  custom `drill` gets drill + 1.0 mm, at most pitch - 0.5, and a ring under 0.15 mm is refused) or SMD (`land`,
  single rows alternate sides).
* `connector` and `custom`: a `pad_table` copied from the datasheet's land-pattern drawing in the drawing's own terms:
  `unit` (mm, mil, in), `y_axis` (up, down), `view` (top, bottom = mirrored), `origin` (center, pin1, datum +
  `center_at`), then `pads` and `arrays` (N pads at a pitch). Pads can be SMD, plated through-hole (round or slot:
  `drill` + `drill_h`) or NPTH; `MP`/`SH` pads are mounting/shield pads, not pins; `polygon` pads for odd shapes;
  `paste: "grid"` splits a big pad's paste into windows; `thermal_vias`; `keepouts`; `same_net` for stacked pads (USB-C).
  Through-hole pads with only a drill get KiCad's connector rule (drill + 1.0 mm, at most pitch − 0.8 mm across the
  row). The footprint origin is pin 1 for through-hole parts and the centre for SMD (KiCad). Courtyard: the union of
  pads and body, + 0.5 mm, traced as one outline.
* **Table checks** (`check_pad_table`, errors stop the build, warnings go in the report): mils typed as mm, pin count,
  duplicate or skipped numbers, overlapping pads, a pad off the row pitch (misread coordinate), one or two pads that
  break an otherwise symmetric pattern, pin 1 in the middle of a row or not at 0,0 when the drawing's origin is pin 1,
  annular ring.
* **`footprint_check`** now also knows: ball grids (A1 top-left and marked on silk and fab, every ball on the grid,
  land 35–65 % of the pitch, JEDEC row letters); mounting/shield pads (left out of pin-1 and numbering rules);
  dual-row zig-zag numbering; stacked USB-C GND/VBUS pads (a warning); polygon pads (exact gaps); NPTH clearance;
  a courtyard outline that is not a rectangle. All 20 KiCad references below pass it with no errors.
* **3D:** `step_gen` builds BGA bodies with balls (depopulated ones left out) and connector boxes with pins and contacts.
* **Altium:** approval (`Workspace.approve_footprint`, the UI and MCP) writes the `.PcbLib` for every family through
  `altium_lib.footprint_from_spec`: SMD pads as before, through-hole pads with their drills (round, square, slots;
  `kicad_import`'s converter), NPTH holes, mounting/shield pads, KiCad mask margins as manual mask expansion (BGA NSMD),
  tented thermal vias, paste windows on Top Paste, keep-outs, and a polygon pad as a small anchor pad plus regions with
  the exact outline on Top Layer and Top Solder (and Top Paste when it has paste). Altium gives primitives that touch a
  pad the pad's net on the board. `tests/test_altium_tht.py` approves all 41 advanced specs and reads each `.PcbLib`
  back (altium_lib's reader and pyaltiumlib): 2767 pads, 119 plated through-hole, 3 NPTH, 5 slots, 22 paste windows and
  the polygon pad match the `.kicad_mod`. Not checked: none of them has been opened in Altium Designer. The footprint
  preview fits its view to the pads, holes, body and courtyard, so connectors with pin 1 at 0,0 are drawn whole.

`ecadlib footprint-bench` compares every pad (centre, size, drill) and the courtyard with KiCad's official footprint
(fetched from gitlab.com and cached; `tests/fixtures/kicad_bench.json` keeps just the numbers for offline tests). Run
2026-10-14:

| Kind | Parts | Pads within 0.01 mm | Courtyard |
|---|---|---|---|
| BGA | BGA-256 1.0 mm, BGA-100, BGA-324 0.8 mm (rows to V), BGA-144 (centre E5:J9 depopulated), UFBGA-132 (ring missing, ball map) | 956/956 | 5/5 |
| Pin headers | 1x05 / 2x05 2.54 mm, 1x05 2.00 mm, 2x05 1.27 mm, three SMD headers | 55/55 | 5/7 (KiCad's SMD header courtyard also wraps the lead tails, which the spec doesn't give) |
| Connectors | JST XH 2 and 4 pin, JST PH SMD, JST SH SMD, Molex KK 254 (table in mil), Molex PicoBlade (bottom-view drawing from pin 1), USB-C HRO TYPE-C-31-M-12 (datum origin, slots, NPTH, stacked pads) | 50/50 | 7/7 |
| IC | SOT-23-6 | 6/6 | 1/1 |

1067/1067 pads (100 %), 20/20 footprints, 18/20 courtyards, 20/20 clean in `footprint_check`.

Widened 2026-10-15 to 57 footprints, 2942/2942 pads within 0.01 mm, 53/57 courtyards (the four dual-row SMD headers,
as above), 57/57 clean: eight more BGAs (rows A..N, A..Y and AA..AF, BGA-672 with A1 depopulated, a WLCSP-36 at
0.4 mm, BGA-68 with 13 single balls missing, the 2 x 3 x 15 SDRAM grid, maker's lands where IPC differs), QFN-16 and
DFN-8 with thermal vias (custom pad tables; vias may have their own row pitch, `thermal_vias.pitch_y`; KiCad's B.Cu
copy of the exposed pad is not compared), JST PH / XH 6 / GH, a 2x20 and a 1.00 mm header, six SMD headers built
from the land tables alone (single-row headers now have their own table), and 15 IPC chip / SOT / SO / QFP / QFN parts.
`tests/test_footprint_review.py` adds property tests over random BGA / header / connector specs (pad count, no
overlaps, courtyard, symmetry, PcbLib read back = `.kicad_mod` including mask and paste margins).
A PcbLib converted back to KiCad turns an anchor pad with its copper / mask / paste regions into a KiCad custom pad
(`gr_poly` primitives, as KiCad's own library writes them), not net-less copper polygons. Through-hole custom pads
carry their shape on top and bottom copper in Altium; inner layers get the anchor pad and hole (Altium regions cannot
be "all layers"; the connection is the same, only an inner-layer trace to the outer edge of the shape would differ).
`thermal_vias.unit` (mm default, mil, in, or `table`) takes the vias in the pad table's unit. BGA lands from the ball
size follow the IPC-7351B collapsing-ball table (0.75 -> 0.55 ... 0.17 -> 0.15, 0.15 -> 0.13; from the standard's
BGA section as reproduced in maker notes, not re-read online here: ipc.org is blocked). The connector and header
specs carry the datasheet's numbers (pitch, pad sizes, hole sizes, body), which KiCad's library also follows, so these
rows mostly test the frame handling (units, y axis, bottom view, origins), the derived pad sizes and the courtyard; the
BGA lands are computed from the ball size (except UFBGA-132, whose land comes from the ST datasheet). Not checked: real
PDFs through `datasheet.py` (no API key here), and the generated through-hole and BGA files opened in Altium Designer.

Regenerated from today's generator and `ecadlib/package_specs/` (pad 1, mm; `out/footprints/compare_vs_kicad_official.txt`):

| Footprint | ecadlib pad | KiCad pad | Δ size (w / h) | Δ centre | Courtyard ecadlib / KiCad |
|---|---|---|---|---|---|
| R_0603_1608Metric | 0.80×0.95 @0.825 | 0.80×0.95 @0.825 | 0 / 0 | 0 | 2.96×1.46 / 2.96×1.46 |
| C_0402_1005Metric | 0.59×0.60 @0.455 | 0.56×0.62 @0.48 | +0.03 / −0.02 | −0.025 | 1.80×0.90 / 1.82×0.92 |
| R_0805_2012Metric | 1.025×1.40 @0.9125 | 1.025×1.40 @0.9125 | 0 / 0 | 0 | 3.36×1.90 / 3.36×1.90 |
| R_1206_3216Metric | 1.125×1.75 @1.4625 | 1.125×1.75 @1.4625 | 0 / 0 | 0 | 4.56×2.26 / 4.56×2.26 |
| SOIC-8_3.9x4.9mm_P1.27mm | 1.95×0.60 @2.475 | 1.95×0.60 @2.475 | 0 / 0 | 0 | 7.40×5.40 / 7.40×5.40 |
| QFN-32-1EP_5x5mm_P0.5mm | 0.875×0.25 @2.4375 | 0.875×0.25 @2.4375 | 0 / 0 | 0 | 6.26×6.26 / 6.26×6.26 |

Every part matches KiCad exactly except C_0402_1005Metric, and that residual is the example spec's input, not the
IPC maths: the bundled spec uses a generic datasheet outline (terminal 0.25 ± 0.10, width 0.50 ± 0.05), while KiCad
builds its 0402 capacitor from the IPC-SM-782 sizes (terminal 0.10–0.30, width 0.40–0.60). Built from KiCad's sizes
(the `stdparts` table entry) the pads are KiCad's exactly: 0.56 × 0.62 at ±0.48. Older copies of this table showed
0402 pads about 0.1 mm shorter; that was the first prototype's small-chip fillet table (toe 0.10 instead of table
3-6's 0.20), since corrected. Density A gives an 0603 pad of 1.00×1.05 and density C gives 0.60×0.85.

## `stdparts.py`: standard parts without Claude

```
python3 -m ecadlib stdparts resolve "SOIC-8 3.9x4.9mm" | 0603 --kind R | --mpn GRM188R71H104KA93D --kind C [--kicad-mod F] [--json]
python3 -m ecadlib stdparts list [PATTERN]
python3 -m ecadlib stdparts bench [--kicad DIR | --fixture tests/fixtures/stdparts_kicad_numbers.json.gz]
python3 -m ecadlib stdparts coverage BOM [BOM ...]
python3 -m tests.test_stdparts
```

The librarian's `standard` source (default order `kicad, easyeda, standard, datasheet`) builds parts whose package is
fully set by standard data, with no API call. Footprints come only from `package_specs/standard.json.gz`: 618 KiCad
generic packages (510 gullwing / no-lead from the KiCad footprint generator's JEDEC size files, built with
`footprint_gen`'s IPC-7351B equations; 74 R/C/L/D/LED/fuse chips; 31 hand-drawn ones such as SOD-123, SMA/SMB/SMC,
MELF, SOT-223, SOT-89, DPAK/D2PAK, TO-220 vertical, TO-92 inline, stored as KiCad's recommended land as a pad table).
It resolves an exact KiCad name, a chip size (footprint, value, description, or MPN pattern; two sizes that disagree
give nothing), or a named package with its body size (`SOIC-8 3.9x4.9mm`, `SO-8 150mil`, `QFN-32_L5.0-W5.0-P0.50-EP3.5`). It never
guesses, because a wrong footprint skips the datasheet reader: a family name without a body size (`SOIC-8`, `SO-14`,
`TSSOP-20`, `LQFP-100`; only `MSOP-8` has one JEDEC body), a no-lead or `-1EP` name without the exposed-pad size,
`TO-252-2` / `TO-263-2` / DPAK / D2PAK without a library prefix (KiCad also has `_TabPin1`), a name that fits more
than one KiCad footprint (SOT-23-3, bare SOT-223, SC-70), pin-removed variants (`SOIC-8-N7`), power packages and
TO-220 horizontal resolve to nothing, and the next source tries. Symbols: R, C, L, LED and diodes (not TVS: uni- or bidirectional pin names are not certain), and SOT-23 / TO-92 BJTs
and MOSFETs, 1117 LDOs, 78xx/79xx/LM317 for listed MPN families (held until a person checks pins 1-3 and releases the part with
`release-check --allow` or Release anyway). The source is
recorded as `standard:<Lib>:<Name>` / `standard:<symbol>`.

Checks (2026-10-16, kicad-footprints f8440fa, kicad-symbols 9280a93):
* `bench`: 618 / 618 table entries match KiCad's footprint (pads and courtyard, footprint_bench tolerances); the test
  suite repeats it against a numbers-only fixture. Packages where our IPC build differs from KiCad's (fine-pitch
  gullwing with KiCad's 0.15 mm pad gap, SOT-723, PowerDI-123, TO-92L, TO-220 horizontal, R_0508) are left out.
* KiCad symbols as an MPN list (description and MPN only, Footprint property hidden): 1953 of 17050 resolve; 1925
  (98.6%) give the same pads as the symbol's KiCad footprint; all 121 MPN-family pinouts match KiCad's pin numbers.
* The repo's test BOMs: 47 of 66 non-empty lines get a footprint, 32 a footprint and a symbol (before the no-guess rule; see [accuracy test set](development.md#eval-accuracy-test-set-and-scorer-ecadlibeval) for the eval set now). Librarian before/after
  on the same 68 parts: with the KiCad library reachable, nothing changes (KiCad already has those passives); without
  it, parts sent to the datasheet step fall from 66 to 35 (31 complete parts with no Claude call).
* **Not checked:** a live datasheet run (no API key); EasyEDA (blocked).

## `symbol_layout.py`: one pin rule for every IC symbol

The house rule: **inputs left, outputs right, power on top, ground on the bottom** (`power_side = "top"`, the default). The other variant, `power_side = "left"`, leaves the top and bottom edges empty and puts the power pins at the top of the left side and the ground pins at its bottom, so no pin name is ever drawn sideways. Every IC symbol is laid out this way, from a 555 to a 100-pin MCU. The layout is used for datasheet-reader symbols, for EasyEDA IC symbols (the librarian re-lays them out), for `altium_lib.ic_symbol` and `schlib`, and for the KiCad export, which reads those SchLibs back. Symbols taken from the KiCad libraries stay as drawn unless their pin text would overlap in Altium; then they are re-laid out too, with a note. Only plain IC boxes are re-laid out (designator U / IC, one rectangle, every pin on its edge); connectors, transistors, op-amp triangles and gates keep their drawing.

* **Overlapping text.** `symbol_layout.text_problems(sym)` estimates every shown pin name and number as Altium draws it (60 mil per character, 80 mil high, scaled down for a 50 mil pitch) and reports any two that overlap and any name running out of the body. `check` includes it, so `ecadlib symbol` exits 1 on it. The NE555DR from EasyEDA in Oliver's first Altium run is the test case: its VCC and RST names ran down into DISCH and OUT, and GND ran up into THRES and CONT.

* **Groups.** Pins are grouped by name, and by type when the name says nothing. Power (VDD, 3V3, VBAT) and analog supply (VDDA, VREF+) go on top; ground (GND, VSS, AGND, VREF-) and the exposed pad go on the bottom. Reset/boot/enable, clock/oscillator, debug (SWD, JTAG, boot UART such as U0TXD), USB, ADC/DAC and inputs (IN+, TRIG, THRES, FB, SENSE, type input) go on the left. Power out (VOUT, VCAP, 3V3OUT), UART, I2C, SPI, CAN, Ethernet, ports, outputs (OUT, Q, DISCH, PGOOD, SW, type output) and NC go on the right. A pin's type beats a direction guessed from its name. A UART, I2C, SPI, CAN or Ethernet group whose pins are all type input is no bus: those pins go left with the inputs (TPS62133's soft-start input SS/TR is not SPI's SS). Pins with no direction (passive, bidirectional) are `other`: left, moved right only to keep the body square.
* **Names.** Alternate-function names are grouped by their first name (`PA9/USART1_TX` and `PH0-OSC_IN` are port pins). Overbars and active-low marks (`~{RESET}`, `/RESET`, `RESET#`, `RST_N`) are ignored for grouping.
* **Ports.** Each port is its own block, sorted by number (PA0..PA15, then PB0...). Blocks are separated by one empty grid row. Whole ports move between the left and right side to keep the body roughly square. A port is split only when that saves more than 6 rows.
* **Stacking.** By default (`stack_power = "none"`) every power pin gets its own pin end, so Altium draws no junction dot and every pin can be checked against the datasheet. With `stack_power = "stack"`, power pins that share a name (VDD, or VDD_1..VDD_5 numbered 1..n) are stacked: one pin shows its name and number, and the rest sit on the same spot with name and number hidden. Names that only look alike are never stacked: VDD1 / VDD2 (an isolator's two sides), VDD18 / VDD33 and VDD_18 / VDD_33 are different supplies. KiCad gets the rest as hidden passive pins (KLC S4.3). `stack_power = "hide"` makes them hidden pins tied to the net by name instead (Altium HiddenNetName; the KiCad export names those hidden power pins after that net, since KiCad connects them by name). A KiCad library symbol that stacks pins itself (KiCad hides the extra VIN / GND pins on the first one, as in TPS62130 or ESP32-C3) is redrawn by these rules when it is a plain IC box and `stack_power = "none"`; any other drawing is kept and the plan notes the shared pin end for review.
* **Pin grid.** KiCad draws discretes on a 50 mil grid (a diode's pins end 150 mil from its centre). On import each pin whose end is off the 100 mil grid is lengthened outward onto it, so wires snap in Altium; the body is not moved.
* **Grid.** Everything is on the grid: body edges, pin ends and pin pitch (100 mil, which is 2.54 mm in KiCad).
* **Multi-part.** With `split_pins` set, larger parts become multi-part symbols. Part A holds power, ground and NC; the other parts hold the signal blocks in order, at most `unit_pins` pins each.

With `layout_pins` set above 1, parts with fewer pins keep the plain "side per pin" layout byte for byte. `layout=True/False` (or `"layout"` in symbol JSON) forces the grouped layout on or off.

```
ecadlib symbol pins.csv -o out/STM32F103VET6.SchLib --kicad out/STM32F103VET6.kicad_sym --preview out/stm32.svg --list
ecadlib symbol part.SchLib -o tidy.SchLib --split 64          # re-lay out an existing symbol; also .json, LIB.kicad_sym:NAME, kicad:Lib:Name
```

The CSV needs `num` and `name` columns; `type` and `group` (to override the grouping) are optional. Commas, tabs or semicolons separate the columns. A CSV with no pins, or a row with a name but no number, is refused (exit 2) and nothing is written. `--list` prints the groups, and the command exits 1 if any pin is off the grid or overlaps another, or one pin number carries two different signals (the same name twice is a stack). Settings live in the rules file:

```toml
[symbols]
layout_pins = 1         # grouped layout from this many pins (0 = never)
power_side = "top"      # top: power top, ground bottom | left: both on the left side
resistor = "us"         # us: zigzag resistor symbol | iec: box
split_pins = 0          # multi-part above this many pins (0 = never)
unit_pins = 48          # most pins in one signal part
stack_power = "none"    # none (own pin end each) | stack | hide
pin_length = 300        # mil, a multiple of the grid
grid = 100              # 50 or 100 mil
# order = [...]         # group order; sides = { debug = "right" } moves a group
```

`ecadlib init` proposes `pin_length` and `grid` when the IC symbols in your SchLibs use other values. Workspace: `preview_symbol(pins, name, split, stack, save)`; MCP tool `preview_symbol`. `datasheet.py` can call `symbol_layout.arrange_pins(pins, rules)` to show the grouping in its review.

**Checked:** an STM32F103-style 100-pin LQFP, an ESP32-WROOM-32 module and an FT232RL USB bridge (tests/fixtures/symbols). Each was checked for grouping, order, overlapping pins and grid, read back with `read_schlib`, and parsed as KiCad. The previews were inspected by eye. **Not checked:** nothing has been opened in Altium Designer or KiCad yet. Name widths are estimated at 70 mil per character, so bodies may be a little wider than needed. Pins whose names match no rule fall back to "other" and are placed by type.

## `step_gen.py`: package-shaped 3D models (STEP)

```
python3 -m ecadlib.step_gen ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json -o out/SOIC-8.step [--height MM]
python3 -m ecadlib.step_gen QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm -o out/qfn24.step     # from a footprint name
python3 -m ecadlib.step_gen --check out/*.step                                          # validate any STEP file
python3 -m tests.test_step_gen
```

When no KiCad STEP model is embedded (the librarian never embeds KiCad's; a downloaded one only gives the height), `kicad_import` and the librarian embed a generated STEP in the PcbLib instead of extruding a box (`--box` on either command, or `gen_step=False`, keeps the old behaviour; the box is still used when the family is unknown). The generator is pure Python: manifold solid B-reps with planar faces (round parts are 24-sided prisms), AP214, colours per solid (dark body, silver leads, light pin-1 dot or polarity band), units mm. Placement matches KiCad's library models: origin at the footprint origin, z = 0 on the board, pin 1 where the footprint has it (2-terminal parts at -x, dual/quad rows top left, through-hole at 0,0).

Families: chip R/C/L/LED (body + end caps; MELF round), molded tantalum/SMA/SMB/SMC (terminals folded under, polarity band), gull-wing SOIC/SSOP/TSSOP/MSOP/SOT-23(-5/-6)/SC-70/SOT-223 (tab)/SOD-123/323/523 and flat-lead SOT-89, QFP/LQFP/TQFP, QFN/DFN/SON (bottom terminals, exposed pad), LGA by name (`Bosch_LGA-8_2.5x2.5mm_P0.65mm_...`: terminals where the footprint's pads are, either numbering direction), and through-hole vertical pin headers, radial capacitors and axial resistors and diodes. Dimensions come from the package spec (datasheet-reader output, or `package_specs/<name>.json`), else from the footprint name and typical JEDEC/EIA outlines. The height comes from the caller, then `height_max`, then a per-family table that stays inside `release_check.HEIGHTS`.

**EasyEDA / LCSC parts use EasyEDA's own model** (`ecadlib/easyeda_3d.py`). The footprint names its STEP file (the `outline3D` node: uuid, centre, outline, z and rotation); the librarian downloads it, turns it by the node's rotation, centres it on the node's centre (relative to the footprint origin, y up), rests an SMD part on the board (a through-hole part keeps its pins below it), embeds it in the PcbLib and takes the body height from its top. Only 3D points and directions are changed, so the file's units and curved surfaces stay as they were. A model whose outline disagrees with EasyEDA's (a 90 degree turn) or whose centre is off the pads is flagged for review. The footprint record keeps where it came from (`footprint.model`: `easyeda:<uuid>`, its URL and the JLCEDA/EasyEDA credit). When the download fails, the generated model or the box is used as before; `--box` uses the model's outline and height as an extruded box.

`--check` (and the tests) parse the STEP file and check that every `#ref` resolves, that every closed shell is closed (each edge used by two faces in opposite directions), that loops connect, that faces are planar and oriented outward, that each volume is positive and every solid has a colour. The tests also compare the bounding box with the spec and check that each lead sits on the footprint_gen pad with the same number, for all 26 specs and 25 more footprint names. **Not checked:** no STEP kernel is installed here (no OCC, FreeCAD or cadquery), so the files have never been opened in Altium, KiCad or FreeCAD. Shapes are simplified: no pin-1 dimples, rounded edges or radial-cap polarity stripe. The SOT-89 tab and the name-derived dimensions are approximations.

## `visual_check.py`: visual proof against the datasheet

```bash
ecadlib visual-check SOIC-8.kicad_mod --datasheet ds.pdf [--pages 3,7-8] [--symbol PART.SchLib] [-o out/]
ecadlib visual-check PART --workspace W --datasheet ds.pdf        # a part already in the workspace
ecadlib visual-check SPEC.json --datasheet ds.pdf --render-only   # pictures only, nothing sent
```

It draws our footprint and symbol as PNGs, renders the datasheet pages, and asks Claude in one Messages API call
whether they agree. The footprint image is a stated **top view** of the copper lands. Pads are coloured by kind,
with their numbers on them. Pin 1 is blue and has a callout. It also shows the F.Fab body, the courtyard and the
silk, an mm grid with labelled major lines, and a scale bar. Dimension lines give the body size, the pad span and
the pitch. BGAs get row letters and column numbers. The header lists the measured facts: pad count, pitch, where
pin 1 is, and the direction of numbering. Every family the generator builds can be drawn, including slots, polygon
pads with vias, stacked USB-C pads and BGA ball maps. Symbols can be drawn from symbol JSON, `.SchLib`,
`LIB.kicad_sym:NAME` or a pin CSV, one image per part when they don't fit together. Datasheet pages are rendered
by `pdftoppm`. Pages are picked with the datasheet reader's keyword table unless `--pages` is given.

The answer comes back as JSON in a fixed schema, with one check per aspect: pitch, pad count, numbering, pin 1,
top or bottom view, body size, pad size, pin count, pin numbers and pin names. The checking is strict:

* A mismatch on pitch, pad count, numbering, pin 1, view, pin numbers or pin names is an error, so the verdict is
  **fail**.
* An unclear or missing check gives **doubt**. So do a refusal, `max_tokens`, malformed or off-schema output, and
  datasheet text addressed to an AI.
* **pass** needs a real answer in which every required check matches.
* With no API key the verdict is **skipped**, but the pictures are still written.

The call goes through the datasheet reader's `Client`, which handles the key, retries and the request log. The log
never holds the key or the image bytes. Answers are cached by a content hash under `<workspace>/agent/proof_cache/`.
Settings live in `[agent.proof]`: `model`, `effort`, `api_key_env`, `max_cost_usd`, `fail_on`, `max_pages` and
`dpi`. An empty model, effort or key variable falls back to `[datasheet]`. The cap is checked against a worst-case
estimate before anything is sent. The same check is available as `Workspace.visual_check` and as the MCP tool
`visual_check`. Pictures and `proof.json` go to `agent/proofs/<part>/`. Drawing needs Pillow (`pip install
'ecadlib[vision]'`).
