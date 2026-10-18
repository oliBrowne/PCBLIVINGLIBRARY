# Assembly-ready parts (JLCPCB)

ecadlib checks every part that has an LCSC part number against the footprint JLCPCB actually places by, and works out
the rotation JLCPCB's pick-and-place file (CPL) needs. Then it writes JLCPCB's BOM and CPL from your board. Nothing is
guessed silently: a part whose pads don't match is held, and a part whose rotation ecadlib can't prove is listed by name.

This is issue #104. Everything that is specific to one assembler lives in a profile,
[`ecadlib/assembly_profiles/jlcpcb.json`](../ecadlib/assembly_profiles/jlcpcb.json). The geometry
([`ecadlib/assembly.py`](../ecadlib/assembly.py)) and the board side
([`ecadlib/assembly_io.py`](../ecadlib/assembly_io.py)) are the same for every profile.

## What you get

- **A pad verdict per part.** Your footprint's pads are compared with JLCPCB's own footprint for the LCSC number
  (the EasyEDA footprint, which JLCPCB places by):
  - **identical**: every pad within 0.02 mm.
  - **compatible**: small differences that solder fine.
  - **risky**: a pad off its land, a width or spacing that differs a lot. It warns; with `[assembly] strict = true`
    it holds the part.
  - **incompatible**: a different pin count, a different numbering, a mirror image, or pads far off their lands. The
    part is held.
- **A rotation with its evidence.**
  - **verified**: your pads were registered on JLCPCB's footprint, so the turn is measured.
  - **override**: a per-part rule in the profile, with its source.
  - **parameter**: the part's own `Assembly Rotation` parameter (written by an earlier check).
  - **table**: the package family's 0° pose from the profile. Polarized parts on a table rotation always get a review
    item.
  - **unknown**: the board angle goes out uncorrected and the part is listed in the report.
- **Library parameters** on each checked part:
  - `Assembly Profile`
  - `LCSC Part`
  - `Assembly Rotation` (degrees added to the board angle)
  - `Assembly Offset` (JLCPCB's origin in your footprint's frame, mm, x right and y up)
  - `Assembly Check` (the pad verdict)
  - `Assembly Evidence` (the rotation evidence)

  They are measurements ecadlib repeats on every run, so writing or changing them never voids a sign-off.
- **An overlay picture**: your pads (red outline) over JLCPCB's (blue fill) after the turn, with both pin 1s, pin
  names and the verdict. It shows in the web UI's part panel, in the menu's report, and in `--png` / `--overlays`.
- **JLCPCB's BOM and CPL files** for your board, top and bottom, with a report of every part that needs a person.

## Commands

```
ecadlib assembly part R_0402.kicad_mod --lcsc C25744 --png overlay.png   one footprint against JLCPCB's
ecadlib assembly check board.kicad_pcb                                    the preflight: pads, rotation, LCSC number, stock
ecadlib assembly export board.kicad_pcb -o out/                           JLCPCB's BOM + CPL, and the report
ecadlib assembly export "Pick Place for board.csv" --bom bom.csv --footprints lib/ -o out/   the same from Altium
ecadlib assembly profiles                                                 the profiles and where each rule comes from
```

- **Board input**: a KiCad board (`.kicad_pcb`), or Altium's pick-and-place file (File > Assembly Outputs >
  Generates pick and place files, CSV, metric or imperial).
  - An Altium file has no pads, so `--footprints DIR` points at your library's `.kicad_mod` files for the pad check.
  - `--bom` adds LCSC numbers from a BOM CSV that has a Designator column.
- **Origin** (KiCad): `--origin auto` (the default) uses the drill / place origin when the Gerbers use it, else the
  page origin. `aux` and `page` force one.
- **Exit status**: 0 when nothing is held, 1 when something is held, 2 for bad input.
- **Files written by `export`**: `<board>-jlcpcb-bom.csv`, `<board>-jlcpcb-cpl.csv`, `<board>-jlcpcb-assembly.md`
  and `<board>-jlcpcb-assembly.json`, plus `overlays/`.
- **Listed, never guessed**: parts with an unknown rotation, parts without an LCSC number, and parts whose pads are
  risky or incompatible each get their own list in the report and on the terminal.

### Settings (`ecadlib.toml`)

```toml
[assembly]
profile = "jlcpcb"   # or a path to your own profile .json
check = true         # the librarian checks every part with an LCSC number
strict = false       # true: risky pads and unknown rotations hold the part too

[providers]
unofficial = true    # JLCPCB's footprints come from EasyEDA; off, rotations come from the family table only
```

JLCPCB's footprints are read from EasyEDA's public part API. That source is unofficial, so it is used only when
`[providers] unofficial = true`. ecadlib never pretends to be a browser. With it off, or with `--offline` and nothing
cached, pads come out **unchecked** and rotations come from the family table, so parts in families without a 0° rule
are listed as unknown.

### In Altium and KiCad

- **Altium**: the ecadlib menu has **Assembly check (JLCPCB)**. It reads the focused project's pick-and-place file
  (generate it first) and the project BOM's LCSC numbers, checks the pads against your library's footprints, and
  writes the files under the workspace's `assembly/<project>/` folder. The counts show in the ecadlib dialog, and
  the report with the overlays opens in your browser.
- **KiCad**: the ecadlib window (the ecadlib toolbar button in the PCB editor) has **Assembly check (JLCPCB)**. It
  reads the open board directly, writes the files into `ecadlib-work/assembly/<board>/` and opens the report on
  request.

### In the librarian

When the librarian fills a BOM, each part with an LCSC number is checked before the release check. The verdict and
parameters are stored on the part:
- Incompatible pads hold the part, with the overlay named in the reason.
- Risky pads are a warning (held with `strict`).
- Review items (such as "polarized part on a table rotation") go to the review queue.

## How it works

### Frames

A footprint is in its own frame: millimetres, +x right, **+y down** (KiCad's, top view). Angles are counter-clockwise
as seen from the top, the same sense as KiCad and JLCPCB's CPL.

### Registration

ecadlib's pads are tried in all 8 orientations (4 turns, each with and without a mirror) against JLCPCB's.
- Pads are paired by number. For two-pin polarized parts, they are paired by role (cathode / anode, + / -) when the
  symbol names the pins, else by the polarity mark, else by ecadlib's rule (cathode or + on pad 1).
- The translation is solved by least squares. When the best turn is more than 0.5° off the 90° grid, a Kabsch fit is
  tried too.
- The best proper turn wins. A mirror image that fits better is a finding (the numbering runs the other way), never
  a rotation.

The result is the turn *c* that carries ours onto theirs, their origin in our frame, the per-pad deltas and the fit
residual. `Assembly Rotation` = −*c* mod 360.

### Placement

- **CPL point (Mid X / Mid Y)**: the board position of JLCPCB's footprint origin, which is your footprint's origin plus
  `Assembly Offset`, turned with the part. Top-view coordinates, mm, +y up, from the board origin.
- **CPL rotation, top**: the board angle + `Assembly Rotation`.
- **CPL rotation, bottom**: the angle as seen from the bottom, + `Assembly Rotation`.
  - KiCad: 180 − the board angle. This was checked against pcbnew's own pad positions on a two-sided test board, and
    matches the kicad-jlcpcb-tools project's check of JLCPCB's placement preview.
  - Altium: −the Rotation. Altium mirrors bottom parts left-right; this is **not yet verified** on a real bottom-side
    order, so every Altium bottom-side part gets a review item until it is.

### The profile

Every rule in `jlcpcb.json` names a source in its `sources` table.
- JLCPCB's CPL and BOM help pages and their KiCad guide.
- kicad-jlcpcb-tools (MIT) for the EasyEDA-footprint method.
- KiCad's place-file exporter and the KiCad library conventions.
- The r/KiCad 0402 rejection (March 2026) for the two-pin spacing tolerance.
- The golden set below.

`ecadlib assembly profiles` prints them. A profile is validated on load: every family and override must cite a source.

Families with a 0° rule today: chip resistors, capacitors and inductors (pad 1 left), chip LEDs (cathode left) and
SOIC (pin 1 bottom-left, numbers running right). The other families have no rule yet, so they need JLCPCB's
footprint (the verified path) or come out unknown. Per-part overrides go in `overrides`:

```json
{"part": "C2286", "rotation": 180, "offset": [0, 0], "source": "jlc-preview", "note": "checked in JLCPCB's placement preview 2026-10-20"}
```

## Results

### Golden set

These are 13 real LCSC parts. For each one, JLCPCB's footprint is recorded in `tests/fixtures/assembly/easyeda/`,
paired with KiCad's library footprint, and the answer was decided by hand from JLCPCB's footprint drawing,
independently of the code. `tests/test_assembly.py::test_golden_set_agreement` checks every one on each test run.

| LCSC | JLCPCB footprint | KiCad footprint | Pads (expected / got) | Correction (expected / got) | Rotation |
| --- | --- | --- | --- | --- | --- |
| C25744 | R0402 | R_0402_1005Metric | risky / risky | 0 / 0 | verified |
| C25804 | R0603 | R_0603_1608Metric | compatible / compatible | 0 / 0 | verified |
| C17414 | R0805 | R_0805_2012Metric | compatible / compatible | 0 / 0 | verified |
| C17902 | R1206 | R_1206_3216Metric | compatible / compatible | 0 / 0 | verified |
| C1525 | C0402 | C_0402_1005Metric | compatible / compatible | 0 / 0 | verified |
| C14663 | C0603 | C_0603_1608Metric | compatible / compatible | 0 / 0 | verified |
| C15850 | C0805 | C_0805_2012Metric | compatible / compatible | 0 / 0 | verified |
| C13585 | C1206 | C_1206_3216Metric | compatible / compatible | 0 / 0 | verified |
| C80165 | L0603 | L_0603_1608Metric | compatible / compatible | 0 / 0 | verified |
| C2286 | LED-SMD_L1.6-W0.8-R-RD | LED_0603_1608Metric | compatible / compatible | 0 / 0 | verified |
| C72043 | LED0603-RD | LED_0603_1608Metric | compatible / compatible | 0 / 0 | verified |
| C5423 | SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL | SOIC-8_3.9x4.9mm_P1.27mm | compatible / compatible | 270 / 270 | verified |
| C7593 | SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL | SOIC-8_3.9x4.9mm_P1.27mm | compatible / compatible | 270 / 270 | verified |

**Agreement: 13 / 13 (100%), all verified.** The two polarized parts are the LEDs.

- The 0402 resistor is risky for the same reason JLCPCB flagged it in the r/KiCad report: KiCad's pads are 1.02 mm
  apart, JLCPCB's 0.87 mm (+18%).
- The SOIC-8 correction of 270° matches the kicad-jlcpcb-tools check of JLCPCB's placement preview.
- The LED C2286 numbers its cathode pad 2; ecadlib pairs the pads by the pin names (K / A), so the cathode still lands
  on the cathode.

The issue's target is 60 or more parts, with 15 or more polarized (diodes, tantalum and electrolytic capacitors,
SOT-23, QFN, crystals). Those parts are being fetched from EasyEDA on a PC outside the build sandbox. Their results,
and the 0° rules for the families they cover, will be added here.

### Other tests (`tests/test_assembly.py`, 22 tests)

- **Synthetic poses**: 12 footprint shapes × 4 turns × 3 offsets, with and without noise, and off-grid turns. The turn
  and offset come back exactly, and noise stays within tolerance.
- **Real mismatches**:
  - KiCad vs JLCPCB 0402 is risky.
  - A SOT-23 with pin 1 in the other corner is incompatible.
  - An LED with the cathode on the other side is turned 180° by its pin names, not by its pad numbers.
  - Renumbered or mirrored pads are incompatible, never a rotation.
- **Board reader**: the KiCad reader matches KiCad's own position file (`kicad-cli pcb export pos`) on a 16-part
  two-sided board built by pcbnew (`tests/fixtures/assembly/make_boards.py`), including excluded parts and the
  drill / page origin.
- **Round trip**: JLCPCB's footprint placed at the CPL point and rotation lands on the board's own pads (pcbnew's
  positions) within 0.25 mm, top and bottom.
- **Altium**: an Altium-format pick-and-place file of the same board (metric and imperial, with parameter columns and
  a BOM) gives the same CPL.
- **Providers**: with `[providers] unofficial = false`, nothing is fetched and the result degrades to the table.
- **Fixtures only**: all recorded; no test touches the network.

## Limits

- **Altium bottom side** is unverified (see Placement). Check bottom-side parts in JLCPCB's placement preview until a
  real order confirms it.
- **Families without a 0° rule** (diodes in SOD/SMA, tantalum, electrolytic, SOT-23/223/89, TSSOP, QFN, QFP, crystals)
  need JLCPCB's footprint to get a rotation. Without it, they are listed as unknown.
- **Stock** comes from the same EasyEDA answer (`result.lcsc.stock`), so it is as fresh as the cache.
- **JLCPCB's own preview is the last word.** Run your CPL through JLCPCB's placement preview before you pay. If it
  disagrees with ecadlib, add an override to your profile and please report it.

## Credits

- JLCPCB's footprints come from the JLCEDA / EasyEDA Official Library
  ([lceda.cn](https://lceda.cn/), [easyeda.com](https://easyeda.com)).
- The method of aligning KiCad footprints onto them follows
  [kicad-jlcpcb-tools](https://github.com/Bouni/kicad-jlcpcb-tools) (MIT).
- The test footprints are KiCad's library (CC-BY-SA 4.0 with the KiCad library exception).
