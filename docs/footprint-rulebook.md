# The footprint rule book

What a footprint in this library has to contain, what it has to look like, how its pads connect to the symbol,
and what it is called. Every rule here is checked by code: `ecadlib check` runs all of them on any `.kicad_mod`
(ecadlib's own, KiCad's, or one converted from EasyEDA or a datasheet), and the release check holds a part back
before it reaches Altium 365 when one of the hard rules fails.

```
ecadlib check path/to/Part.kicad_mod            every finding, per file
ecadlib check --summary path/to/Lib.pretty/     counts per rule (use it on a whole library)
ecadlib check --geometry-only ...               only the copper / silk / courtyard geometry rules
```

The numbers behind the land patterns themselves (IPC-7351B fillets, tolerances, rounding) are in
[footprint-rules.md](footprint-rules.md). This book is everything around the pads.

Levels: an **error** means the board would be wrong or the part could not be built (a short, an unsoldered pad, a
reversed diode, a name that lies about the copper). A **warning** is a convention the best libraries follow; it is
reported, never silently fixed. Rule ids in backticks are what the checker prints.

## Where the rules come from

| Source | What was taken from it |
|---|---|
| KiCad Library Conventions (KLC), footprint sections F1-F9, `gitlab.com/kicad/libraries/klc` (master, read 2026-10-16) | Naming (F2, F3), pin 1 and orientation (F4.2), connected copper (F4.3), thermal pads (F4.4), silk / fab / courtyard (F5.1-F5.4), SMD and THT pad rules (F6, F7), part type (F6.1, F7.1, F8.1), metadata and 3D models (F9) |
| KLC naming data, `data/fpnaming/*.yaml` | The field order of every name family (chip, gull-wing, no-lead, BGA, connectors, tantalum, DIP) |
| KiCad's official footprint library, `gitlab.com/kicad/libraries/kicad-footprints` at f8440fa (2026-10-10) | The best public examples: 4,976 footprints in 20 libraries were run through the checker to calibrate it (below), and 24 of them, one per family, are the golden examples in `tests/fixtures/kicad_golden/` |
| IPC-7351B through KiCad's footprint generator (see footprint-rules.md) | Land sizes, courtyard excess per density, roundrect radius |
| IPC-7351 land pattern naming (Hausherr, PCB Libraries) | The names Altium's IPC Compliant Footprint Wizard writes: `RESC1608X55N`, `SOIC127P600X175-8N`, `QFN50P500X500X100-33N`, `BGA256C100P16X16_1700X1700X200` |
| Altium documentation: "Creating a PCB Footprint" and the knowledge-base article on mechanical layers in PCB libraries | Origin, designators as the pin map, Top Layer / Multi-Layer pads, the IPC wizard's layer use (assembly Mechanical 13, courtyard Mechanical 15, 3D body Mechanical 13), `.Designator` on the assembly layer |
| Eric Bogatin's public material (see the Bogatin section) | Why exposed pads and ground pins must be wired, why polarity marks matter for bring-up |

The Celestial Altium Library was not used: its licence forbids redistribution, and nothing here is derived from it.

## Naming

A footprint name has to say what the copper does, so that a person picking it in Altium or KiCad gets the right
part. ecadlib reads two schemas and checks every field it can measure.

### KiCad / KLC names (F2, F3)

`<package>-<pins>[-<n>EP]_<X>x<Y>[x<Z>]mm[_<modifiers>]_P<pitch>mm[_<options>]`

| Family | Example | Fields checked |
|---|---|---|
| Chip (F3.1, F3.3) | `R_0603_1608Metric`, `C_0805_2012Metric`, `LED_0603_1608Metric` | 2 pads; the imperial code names that metric code (0603 is 1608, not 1005); the fab body is the metric size |
| Gull-wing (F3.4) | `SOIC-8_3.9x4.9mm_P1.27mm`, `TSSOP-20_4.4x6.5mm_P0.65mm` | pin count = uniquely numbered pads; pitch is a real pad step; body = fab outline |
| No-lead (F3.4) | `QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm` | as gull-wing, plus the exposed-pad count and the EP size against its copper |
| Package names with their own number | `SOT-23-5`, `TO-220-3_Vertical`, `SOD-123` | `SOT-23` is the package, `-5` the pin count; a tab numbered after the leads is allowed (`TO-252-3_TabPin4`) |
| Missing pins (F2.4) | `MSOP-12-16_3x4mm_P0.5mm` | 12 pads, numbered up to 16 |
| BGA (F3.4) | `BGA-256_17.0x17.0mm_Layout16x16_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD` | ball count, the column x row grid (by extent, so depopulated grids count), ball pitch, pad diameter |
| Connectors (F3.6) | `PinHeader_2x05_P2.54mm_Vertical`, `JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal` | rows x pins = pin count, `-nMP` = mounting pads, pitch (also for alternating and staggered rows) |
| Modifiers | `_ThermalVias`, `_HandSolder`, `_Pad1.5x1mm`, `_Mask2.4x3.1mm` | `_ThermalVias` only when there are vias in the exposed pad, and vias only with `_ThermalVias` (F4.4) |

### IPC-7351 names (what Altium's wizard writes)

Dimensions in hundredths of a millimetre (chip size codes in tenths), density suffix `N` / `M` / `L`.

| Family | Example | Reads as | Fields checked |
|---|---|---|---|
| Chip | `RESC1608X55N` | resistor chip, 1.6 x 0.8 mm body, 0.55 mm high, nominal | 2 pads |
| Gull-wing | `SOIC127P600X175-8N` | 1.27 mm pitch, 6.00 mm lead span, 1.75 mm high, 8 pins | pin count, pitch |
| Quad gull-wing | `QFP50P900X900X160-48N` | 0.5 mm pitch, 9 x 9 mm span | pin count, pitch |
| No-lead | `QFN50P500X500X100-33N` | 0.5 mm pitch, 5 x 5 mm body, 1 mm high, 33 pins **including the thermal pad** | pins + EP, pitch, body |
| BGA | `BGA256C100P16X16_1700X1700X200` | 256 collapsing balls, 1.0 mm pitch, 16 x 16, 17 x 17 x 2 mm | ball count, grid, pitch |

### `name`

Error when the name's pin count, EP count, MP count, pitch (off by more than 0.11 mm), chip code pair, BGA
grid, ball count or pad size disagrees with the copper. Warning for a body size that differs from the fab outline,
an EP size that differs from its copper (except `_Mask` parts, whose EP is mask-defined), an exposed pad the name
does not mention, and a `_ThermalVias` mismatch. Held by the release check (ecadlib's own and converted parts).

### `meta`

Error: the footprint name is not its file name. KiCad loads a footprint by file name and Altium links it by name,
so the two tools would point at different footprints. Warnings: Value is not the name, Reference is not `REF**`,
no description, no keywords, a name with spaces or characters a file name cannot carry (KLC F9.1).

## Part type and origin

### `attr`

An SMD footprint must have the SMD type, or it is left out of the pick-and-place file and the assembler never
places it (error, KLC F6.1). A real SMD part excluded from the position files is an error too. Through-hole parts
should have the through-hole type (F7.1); "exclude from BOM" and "not in schematic" are warnings (F6.1, F8.1:
only for mounting holes, fiducials, test points, logos and other non-parts).

### `origin`

SMD: the origin is the body centre, because pick-and-place machines place by it (F6.2). Warning when both the
land and the fab body centres are more than 3 % of the part (at least 0.05 mm) away. Through-hole: the origin is
pin 1 (F7.2). Connectors, horizontal TO packages and parts whose datasheet gives a datum are exempt; KiCad's
TO-252 / TO-263 put the origin on the lead row, and ecadlib matches them.

## Pads

### `layers`

| Pad | Must have | Must not have |
|---|---|---|
| SMD | its copper layer, mask opening, paste (or paste windows / a same-numbered pad that carries it) | a second copper layer, silkscreen |
| Through-hole | all copper layers, both masks | paste (warning: only for pin-in-paste), silkscreen |
| Thermal via | all copper | (may be tented: no mask) |
| B.Cu heat spreader under thermal vias | B.Cu, same number as the EP | B.Mask (KLC F4.4 keeps it masked) |

A pad with no mask opening is covered by solder mask; one with no paste is not soldered by the stencil: both are
errors (F6.3, F7.4). A mask-defined pad is recognised by a separate mask aperture over it.

### `padshape`

SMD pads are rounded rectangles with a corner radius of 25 % of the short side, at most 0.25 mm (KLC F6.3 quoting
IPC-7351C). Sharp rectangles are a warning except under a trimmed heel. On polarized through-hole parts pin 1 is
the rectangular (or rounded) pad and every other pin is round or oval (F7.3); non-polarized parts keep all pads
the same.

### `tht`

Annular ring at least 0.15 mm (KLC F7.5, IPC-2221): a warning below it, an error below 0.05 mm (the ring is gone
after drill wander). A zero drill is an error. The finished hole is the maximum lead plus 0.2 mm (F7.6); slots
keep 0.2 mm around the maximum lead (F7.7). The lead size is in the datasheet, so those two are applied by the
generator from the spec, not guessed by the checker.

### `vias`

Thermal vias carry the exposed pad's number: connected copper shares one number (F4.3), or KiCad and Altium
treat the via as a separate net (error). Drill 0.2-0.3 mm unless the datasheet says otherwise, no paste over a
via of 0.3 mm or more (solder wicks into it), and the `_ThermalVias` suffix (F4.4) (warnings).

### `copper`

(footprint_check) No two pads overlap unless they are one net (stacked USB-C GND / VBUS, a SOT-223 tab). At least
0.2 mm of solder-mask web between pads: warning below it, error below 0.1 mm. Copper keeps 0.2 mm from NPTH
holes. Pads on different copper layers (an F.Cu lead and a B.Cu heat spreader) do not short.

### `paste`

(footprint_check) An exposed pad's paste windows cover 50-80 % of its copper (65 % default, KLC F6.3).

## Drawing layers

### `silk`

(footprint_check) Silk line 0.10-0.15 mm (0.12 nominal), at least 0.2 mm from any exposed copper (0.1 mm on tiny
parts, where KiCad does the same), never under an SMD body, never over a pad (KLC F5.1).

### `fab`

(footprint_check) A simplified body outline on F.Fab at the nominal body size, 0.10 mm line (KLC F5.2).

### `courtyard`

(footprint_check) A closed outline on F.CrtYd that encloses every pad and the body, 0.05 mm line, corners on
the 0.01 mm grid (KLC F5.3).

### `margin`

The courtyard clearance around pads and body: 0.25 mm by default, 0.15 mm for parts under 1.5 mm, 0.5 mm for
connectors, crystals and canned capacitors, 1.0 mm for BGAs (KLC F5.3), 0.10 mm for IPC density C. Warning when
smaller (BGAs and connectors below 0.25 mm: KiCad's own small WLCSPs and JST headers use less).

### `text`

The reference `REF**` on F.SilkS at 1.00 mm, 0.15 mm stroke (F5.1). The value and a `${REFERENCE}` on F.Fab at
0.5-1.0 mm with a stroke of about 15 % (F5.2): the assembly drawing needs the designator inside the outline.

### `polarity`

Pin 1 / polarity is visible on the silkscreen and on the fab layer of every polarized part (ICs, diodes, LEDs,
polarized capacitors, connectors): a filled chevron or bracket on silk (F5.1), a chamfered corner of 1 mm or 25 %
of the body on fab (F5.2). The checker tests it without knowing the style: a layer that looks the same turned 180
degrees about the origin cannot show where pin 1 is. Resistors, non-polarized capacitors and inductors are exempt.

### `pin1`

(footprint_check) A pad 1 (or A1) exists, it is the top-left pad (F4.2: two-terminal and inline parts pin 1 left),
and pads count counter-clockwise from it seen from the top; dual-row connectors may zig-zag.

### `bga`

(footprint_check) Ball A1 top-left and marked on silk and fab, every ball on the pitch grid, land 35-65 % of the
pitch, JEDEC row letters (no I, O, Q, S, X, Z).

### `graphics`

No duplicate lines, arcs, circles or polygons on one layer (F5.4).

## 3D model

### `model`

Every real part references a 3D model, even before the model exists (KLC F9.3). The file is named after the
footprint (a `_ThermalVias` or `_HandSolder` variant shares the plain one), it is a STEP (VRML is a warning:
Altium cannot use it), scale 1 1 1 and rotation 0 0 0, and the path is portable (`${KICADn_3DMODEL_DIR}`,
`${KIPRJMOD}` or a library variable; an absolute path is an error). ecadlib's generator leaves the reference to
the export: `kicad-export` writes `${KIPRJMOD}/<lib>.3dshapes/<name>.step` next to the library and the PcbLib
carries a 3D body, so a raw generator footprint gets this as a warning only.

## Connectivity

The footprint is only right if its pads are the symbol's pins. With a symbol (`symbol_pins`), and in the release
check with the part's SchLib:

### `connect`

Every symbol pin has a pad with the same number, and every numbered pad has a pin; mounting and shield pads (MP,
SH, MH, unnumbered, NPTH) need none. An exposed pad without a pin is an error of its own: it would float, and it is
the part's heat path and usually its ground return. When the symbol calls the pin `EP` or `PAD` while the
footprint numbers it 33, the message says so. (The release check's `pins` rule makes the same comparison on the
Altium files.)

### `pinmap`

Polarity in the pin map. KiCad's diode and LED symbols put the cathode on pin 1 and KiCad's D_ / LED_ footprints
mark pad 1 as the cathode; a symbol with the anode on pin 1 places the part backwards. Polarized capacitors: + on
pin 1. This holds every part in the release check, KiCad library footprints included, because the pairing is ours.

## Altium

### `altium`

`check_pcblib` reads the footprint back from the .PcbLib and checks the Altium side: SMD pads on Top Layer
(Bottom Layer for bottom-side), plated through-hole pads on Multi-Layer, a courtyard on its mechanical layer, an
assembly outline, a `.Designator` string, a 3D body or a height, a pin 1 pad, and a name Windows file names can
carry. `ecadlib parity` makes sure the PcbLib is the .kicad_mod, pad for pad, so every rule above holds in Altium too.

| KiCad | Altium (ecadlib default, Altium IPC wizard convention) |
|---|---|
| F.Cu SMD pad | Top Layer pad, paste and solder-mask expansion from rules |
| `*.Cu` through-hole pad | Multi-Layer pad |
| F.SilkS | Top Overlay |
| F.Fab | Mechanical 13 (Assembly Top) |
| F.CrtYd | Mechanical 15 (Courtyard Top) |
| 3D model | 3D body on Mechanical 13 (embedded STEP or extruded outline) |
| Reference | `.Designator` on Top Overlay |
| Value | `.Comment` on Mechanical 13 |

The layers are set in `ecadlib.toml` `[layers]` for libraries that use other mechanical layers.

## Bogatin: what a footprint owes the layout

Oliver asked for Eric Bogatin's *Practical Guide to Prototype Breadboard and PCB Design* (Artech House, 2021) as a
source. The book itself was not available here, so this section rests on his public material: the Signal
Integrity Journal page for the book and its companion material, his OnTrack podcast episodes ("Seven Design
Guidelines to Break Bad Habits from Using Open-Source Designs", "Pathological Design Features") and the decoupling
measurements in the same literature. Most of his rules are about the board, not the footprint: a continuous
return plane under every signal, no splits or copper fill that break the return path, short low-inductance
connections from decoupling capacitors to the planes, and probing with short ground loops. A footprint cannot
satisfy those on its own, but it can make them possible or impossible:

| His point | What the footprint must do | Checked by |
|---|---|---|
| Engineer every return path; ground bounce comes from return current with nowhere good to go | Every ground and exposed pad is a numbered pad mapped to a symbol pin, never floating copper | `connect` (EP without a pin is an error) |
| Low loop inductance to the planes | Thermal vias in the EP share its number, so they connect; 0.2-0.3 mm vias; capacitor lands at the IPC size (no oversized lands that push vias away) | `vias`, the IPC-7351B land rules |
| Prototype boards are debugged by people with probes | Pin 1 and polarity readable on silk after assembly, designators on the assembly drawing | `polarity`, `text` |
| Interconnects are not transparent | Pad numbers are the schematic's pin numbers, one to one | `connect`, `pinmap` |

Not confirmed from the book itself: any specific numbers it gives for pad-to-via distance, preferred chip sizes
for hand-assembled prototypes, or test-point footprints. They are not checked until they can be read from the text.

## Calibration on the official KiCad library

The checker was run on 4,976 official footprints (20 libraries: chips, diodes, LEDs, tantalum, SO, DFN/QFN, QFP,
SOT, BGA, DIP, TO, crystals, pin headers, JST, USB). The new rules in this book raise an error on 25 of them
(0.5 %); on the six families KiCad builds with its generator (Resistor_SMD, Capacitor_SMD, Package_SO,
Package_QFP, Package_DFN_QFN, Package_BGA) 17 of 1,687 (1.0 %), and each one was read: they are deliberate KiCad
exceptions (TO-220F-5 rings at 0.04 mm forced by a 1.7 mm pitch, DSBGA "Level B/C" parts without paste,
`SOT-223-6_TabPin3` with five pads), not checker noise. The test `test_corpus` repeats this whenever
`ECADLIB_KICAD_FOOTPRINTS` points at a checkout. Writing the book also fixed footprint_check: an F.Cu lead and a
B.Cu heat spreader no longer count as overlapping copper (88 false overlap findings, most on KiCad's ThermalVias parts).

## Known deviations in ecadlib's own footprints

Every footprint ecadlib generates (all package specs at densities A, B and C and the standard table) passes the
book with no errors (`test_ecadlib_footprints_follow_the_rulebook`). The warnings that remain are deliberate and
listed in `OURS_WARNINGS` in the test: the 3D model reference is added at export; fine-pitch lands below a
0.2 mm mask web (the fab decides, as for KiCad's); SMD pin-header lands are sharp rectangles, as KiCad's are;
TO-252 / TO-263 origins on the lead row, as KiCad's; KiCad-size courtyards on small WLCSPs.

## Where it runs

* `ecadlib check` (any footprint), and `--summary` for a library.
* The release check (`ecadlib release-check`): rule-book errors in `name`, `layers`, `attr`, `tht`, `vias` hold
  ecadlib's own and converted parts; KiCad library parts get them as warnings; `pinmap` holds every part.
* `tests/test_footprint_rulebook.py`: the golden examples, one mutant per defect, ecadlib's footprints, the
  Altium read-back and the release gate.
