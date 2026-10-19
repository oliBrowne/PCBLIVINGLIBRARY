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
  symbol names the pins, else by the polarity mark, else by ecadlib's rule (cathode or + on pad 1). The polarity
  mark is read from the filled silk shapes first (JLCPCB's tantalum band, an electrolytic's '+'), then the silk
  lines, then the fab layer.
- Two of JLCPCB's numbers are one pin when its own symbol gives them the same name (SOT-223's tab 4 and pin 2, both
  VOUT), or when one land is numbered for two pins ("A1B12" on a USB-C, where KiCad stacks A1 and B12).
- KiCad's custom pads (SOT-89's lead and tab) are read as their whole copper. A copper pad lying wholly inside a
  bigger pad with the same number (the paste windows on KiCad's TO-252 tab) is part of that pad.
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

Families with a 0° rule today (each from the golden set, with its count in `measured`):
- chip resistors, capacitors and inductors: pad 1 left
- chip LEDs, SOD / MELF and SMA / SMB diodes: cathode left
- SOIC, TSSOP / MSOP, QFP, SOT-23-6: pin 1 bottom-left, numbers running right
- SOT-23: pin 1 bottom-right, numbers running up
- SOT-89: pin 1 bottom-right, pin 2 (the tab) to its left
- TO-252: pin 1 top-left, the tab to the right

The other families have no rule, so they need JLCPCB's footprint (the verified path) or come out unknown. Per-part overrides go in `overrides`:

```json
{"part": "C2286", "rotation": 180, "offset": [0, 0], "source": "jlc-preview", "note": "checked in JLCPCB's placement preview 2026-10-20"}
```

## Results

### Golden set

These are 79 real LCSC parts, 25 of them polarized: resistors, capacitors and ferrites, chip LEDs, SOD / SMA / SMB /
MELF diodes, tantalum and electrolytic capacitors, SOT-23 / -5 / -6, SOT-223, SOT-89, TO-252, SOIC, TSSOP, QFN, QFP,
a crystal, a WS2812B and a USB-C receptacle. For each one, JLCPCB's footprint is recorded in
`tests/fixtures/assembly/easyeda/` and paired with KiCad 7.0.11's library footprint. The answer was decided by hand,
independently of the code:
- **Rotation**: a separate script tried the four turns of JLCPCB's pads onto KiCad's, paired by number, or for a
  polarized two-pin part by the cathode / + end. That end came from JLCPCB's pin names, or, where JLCPCB's pins carry
  no names (the tantalum and electrolytic capacitors), from its drawing read by eye (the band, the '+', the chamfered
  corners).
- **Pads**: the measured lands against the tolerances in the profile.

`golden.json` says how each answer was decided. `tests/test_assembly.py::test_golden_set_agreement` checks every one
on each test run.

| LCSC | Part | JLCPCB footprint | KiCad footprint | Polarized | Pads (expected / got) | Correction (expected / got) | Rotation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C25744 | 0402WGF1002TCE | R0402 | R_0402_1005Metric |  | risky / risky | 0 / 0 | verified |
| C25804 | 0603WAF1002T5E | R0603 | R_0603_1608Metric |  | compatible / compatible | 0 / 0 | verified |
| C17414 | 0805W8F1002T5E | R0805 | R_0805_2012Metric |  | compatible / compatible | 0 / 0 | verified |
| C17902 | 1206W4F1002T5E | R1206 | R_1206_3216Metric |  | compatible / compatible | 0 / 0 | verified |
| C1525 | CL05B104KO5NNNC | C0402 | C_0402_1005Metric |  | compatible / compatible | 0 / 0 | verified |
| C14663 | CC0603KRX7R9BB104 | C0603 | C_0603_1608Metric |  | compatible / compatible | 0 / 0 | verified |
| C15850 | CL21A106KAYNNNE | C0805 | C_0805_2012Metric |  | compatible / compatible | 0 / 0 | verified |
| C13585 | CL31A106KBHNNNE | C1206 | C_1206_3216Metric |  | compatible / compatible | 0 / 0 | verified |
| C80165 | BLM18PG221SN1D | L0603 | L_0603_1608Metric |  | compatible / compatible | 0 / 0 | verified |
| C2286 | KT-0603R | LED-SMD_L1.6-W0.8-R-RD | LED_0603_1608Metric | yes | compatible / compatible | 0 / 0 | verified |
| C72043 | 19-217/GHC-YR1S2/3T | LED0603-RD | LED_0603_1608Metric | yes | compatible / compatible | 0 / 0 | verified |
| C5423 | LM358DR | SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL | SOIC-8_3.9x4.9mm_P1.27mm |  | compatible / compatible | 270 / 270 | verified |
| C7593 | NE555DR | SOIC-8_L4.9-W3.9-P1.27-LS6.0-BL | SOIC-8_3.9x4.9mm_P1.27mm |  | compatible / compatible | 270 / 270 | verified |
| C72041 | 19-217/BHC-ZL1M2RY/3T | LED0603-FD_BLUE | LED_0603_1608Metric | yes | compatible / compatible | 0 / 0 | verified |
| C131244 | 17-21SURC/S530-A2/TR8 | LED0805-R-RD-1 | LED_0805_2012Metric | yes | compatible / compatible | 0 / 0 | verified |
| C38117 | SWPA4030S100MT | IND-SMD_L4.0-W4.0 | L_Sunlord_SWPA4030S |  | risky / risky | 0 / 0 | verified |
| C2297 | KT-0805G | LED0805-R-RD | LED_0805_2012Metric | yes | compatible / compatible | 0 / 0 | verified |
| C49018 | KT-1206R | LED1206-R-RD_RED | LED_1206_3216Metric | yes | compatible / compatible | 0 / 0 | verified |
| C965793 | XL-1005UGC | LED0402-RD_GREEN | LED_0402_1005Metric | yes | compatible / compatible | 0 / 0 | verified |
| C114586 | WS2812B-B/W | LED-SMD_4P-L5.0-W5.0-BL_TC5050RGB | LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm |  | compatible / compatible | 0 / 0 | verified |
| C8678 | SS34 | SMA_L4.3-W2.6-LS5.2-RD | D_SMA | yes | compatible / compatible | 0 / 0 | verified |
| C2480 | SS14 | SMA_L4.2-W2.6-LS5.0-RD_1 | D_SMA | yes | compatible / compatible | 0 / 0 | verified |
| C95872 | M7 | SMA_L4.3-W2.6-LS5.2-RD | D_SMA | yes | compatible / compatible | 0 / 0 | verified |
| C10204 | SMBJ5.0A | SMB_L4.6-W3.6-LS5.3-RD | D_SMB | yes | compatible / compatible | 0 / 0 | verified |
| C10758 | SMAJ5.0A | SMA_L4.3-W2.6-LS5.2-RD | D_SMA | yes | compatible / compatible | 0 / 0 | verified |
| C22452 | SS54 | SMA_L4.4-W2.8-LS5.4-R-RD | D_SMA | yes | risky / risky | 0 / 0 | verified |
| C2099 | 1N4148W | SOD-123_L2.8-W1.8-LS3.7-RD | D_SOD-123 | yes | compatible / compatible | 0 / 0 | verified |
| C2128 | 1N4148WS | SOD-323_L1.8-W1.3-LS2.5-RD | D_SOD-323 | yes | risky / risky | 0 / 0 | verified |
| C2112 | BZT52C3V3 | SOD-123_L2.8-W1.8-LS3.7-RD | D_SOD-123 | yes | compatible / compatible | 0 / 0 | verified |
| C8598 | B5819W SL | SOD-123_L2.7-W1.6-LS3.7-RD-1 | D_SOD-123 | yes | risky / risky | 0 / 0 | verified |
| C145179 | RB521S30T1G | SOD-523_L1.2-W0.8-LS1.6-RD | D_SOD-523 | yes | risky / risky | 0 / 0 | verified |
| C2827694 | PESD5V0S1BA | SOD-323_L1.7-W1.3-LS2.5-BI | D_SOD-323 |  | risky / risky | 0 / 0 | verified |
| C9808 | LL4148 | LL-34_L3.5-W1.5-RD | D_MiniMELF | yes | compatible / compatible | 0 / 0 | verified |
| C2135 | BAT54C | SOT-23-3_L2.9-W1.6-P1.90-LS2.8-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C2500 | BAV99,215 | SOT-23-3_L2.9-W1.6-P1.90-LS2.8-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C20917 | AO3400A | SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C15127 | AO3401A | SOT-23_L2.9-W1.3-P1.90-LS2.4-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C8545 | 2N7002 | SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C20526 | MMBT3904(RANGE:100-300) | SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C2143 | MMBT3906 2A(RANGE:100-300) | SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C2146 | S8050 J3Y(RANGE:200-350) | SOT-23-3_L3.0-W1.7-P0.95-LS2.9-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C181087 | SI2302 | SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C52895 | BSS138 | SOT-23-3_L2.9-W1.6-P1.90-LS2.8-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C5446 | XC6206P332MR-G | SOT-23-3_L2.9-W1.6-P1.90-LS2.8-BR | SOT-23 |  | compatible / compatible | 180 / 180 | verified |
| C7519 | USBLC6-2SC6 | SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL | SOT-23-6 |  | compatible / compatible | 270 / 270 | verified |
| C84817 | MT3608 | SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL | SOT-23-6 |  | compatible / compatible | 270 / 270 | verified |
| C82942 | ME6211C33M5G-N | SOT-23-5_L3.0-W1.7-P0.95-LS2.8-BL | SOT-23-5 |  | compatible / compatible | 270 / 270 | verified |
| C47773 | RT9013-33GB | SOT-23-5_L3.0-W1.7-P0.95-LS2.8-BL | SOT-23-5 |  | compatible / compatible | 270 / 270 | verified |
| C7666 | SN74LVC1G08DBVR | SOT-23-5_L3.0-W1.7-P0.95-LS2.8-BR | SOT-23-5 |  | compatible / compatible | 180 / 180 | verified |
| C6186 | AMS1117-3.3 | SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR | SOT-223-3_TabPin2 |  | risky / risky | 180 / 180 | verified |
| C21583 | HT7333-A | SOT-89-3_L4.5-W2.5-P1.50-LS4.2-BR | SOT-89-3 |  | risky / risky | 180 / 180 | verified |
| C2079 | CJ78L05 | SOT-89-3_L4.5-W2.5-P1.50-LS4.2-BR | SOT-89-3 |  | risky / risky | 180 / 180 | verified |
| C9070 | CJ78M05 | TO-252-3_L6.5-W5.8-P4.58-TL | TO-252-2 |  | risky / risky | 0 / 0 | verified |
| C99124 | AOD4184A | TO-252-2_L6.6-W6.1-P4.57-LS9.9-TL-CW | TO-252-2 |  | compatible / compatible | 0 / 0 | verified |
| C84681 | CH340C | SOP-16_L10.0-W3.9-P1.27-LS6.0-BL | SOIC-16_3.9x9.9mm_P1.27mm |  | compatible / compatible | 270 / 270 | verified |
| C97521 | W25Q128JVSIQ | SOIC-8_L5.3-W5.3-P1.27-LS8.0-BL | SOIC-8_5.23x5.23mm_P1.27mm |  | risky / risky | 270 / 270 | verified |
| C5947 | 74HC595D,118 | SOIC-16_L9.9-W3.9-P1.27-LS6.0-BL | SOIC-16_3.9x9.9mm_P1.27mm |  | compatible / compatible | 270 / 270 | verified |
| C382139 | TP4056 | ESOP-8_L4.9-W3.9-P1.27-LS6.0-BL-EP | SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm |  | compatible / compatible | 270 / 270 | verified |
| C181692 | IP5306 | ESOP-8_L4.9-W3.9-P1.27-LS6.0-BL-EP | SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm |  | compatible / compatible | 270 / 270 | verified |
| C17206 | TXS0108EPWR | TSSOP-20_L6.5-W4.4-P0.65-LS6.4-BL | TSSOP-20_4.4x6.5mm_P0.65mm |  | compatible / compatible | 270 / 270 | verified |
| C6821 | SN74HC14PWR | TSSOP-14_L5.0-W4.4-P0.65-LS6.4-BL | TSSOP-14_4.4x5mm_P0.65mm |  | compatible / compatible | 270 / 270 | verified |
| C529330 | STM32G030F6P6TR | TSSOP-20_L6.5-W4.4-P0.65-LS6.4-BL | TSSOP-20_4.4x6.5mm_P0.65mm |  | compatible / compatible | 270 / 270 | verified |
| C105242 | LMV358IDGKR | VSSOP-8_L3.0-W3.0-P0.65-LS5.0-BL | MSOP-8_3x3mm_P0.65mm |  | compatible / compatible | 270 / 270 | verified |
| C2040 | RP2040 | LQFN-56_L7.0-W7.0-P0.4-EP | QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm |  | compatible / compatible | 0 / 0 | verified |
| C8734 | STM32F103C8T6 | LQFP-48_L7.0-W7.0-P0.50-LS9.0-BL | LQFP-48_7x7mm_P0.5mm |  | compatible / compatible | 270 / 270 | verified |
| C32843 | W5500 | LQFP-48_L7.0-W7.0-P0.50-LS9.0-BL | LQFP-48_7x7mm_P0.5mm |  | compatible / compatible | 270 / 270 | verified |
| C14877 | ATMEGA328P-AU | TQFP-32_L7.0-W7.0-P0.80-LS9.0-BL | TQFP-32_7x7mm_P0.8mm |  | compatible / compatible | 270 / 270 | verified |
| C2858418 | CH9102F | QFN-24_L4.0-W4.0-P0.50-BL-EP2.5 | QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm |  | compatible / compatible | 270 / 270 | verified |
| C1550553 | CP2102N-A02-GQFN28 | QFN-28_L5.0-W5.0-P0.50-TL-EP3.3 | QFN-28-1EP_5x5mm_P0.5mm_EP3.35x3.35mm |  | compatible / compatible | 0 / 0 | verified |
| C220692 | ATSAMD21E18A-MU | QFN-32_L5.0-W5.0-P0.50-TL-EP3.6 | QFN-32-1EP_5x5mm_P0.5mm_EP3.65x3.65mm |  | compatible / compatible | 0 / 0 | verified |
| C2838500 | ESP32-C3 | QFN-32_L5.0-W5.0-P0.50-TL-EP3.7 | QFN-32-1EP_5x5mm_P0.5mm_EP3.7x3.7mm |  | compatible / compatible | 0 / 0 | verified |
| C7171 | TAJA106K016RNJ | CAP-SMD_L3.2-W1.6-RD-C7171 | CP_EIA-3216-18_Kemet-A | yes | compatible / compatible | 0 / 0 | verified |
| C7195 | TAJB107M006RNJ | CAP-SMD_L3.5-W2.8-R-RD | CP_EIA-3528-21_Kemet-B | yes | risky / risky | 180 / 180 | verified |
| C11376 | TAJC226K016RNJ | CASE-C_6032 | CP_EIA-6032-28_Kemet-C | yes | risky / risky | 180 / 180 | verified |
| C37308 | RVT1C101M0605 | CAP-SMD_BD6.3-L6.6-W6.6-LS7.2-FD | CP_Elec_6.3x5.4 | yes | risky / risky | 0 / 0 | verified |
| C3338 | RVT1E101M0607 | CAP-SMD_BD6.3-L6.6-W6.6-LS7.2-R-RD | CP_Elec_6.3x7.7 | yes | risky / risky | 180 / 180 | verified |
| C3340 | RVT1V221M0810 | CAP-SMD_BD8.0-L8.3-W8.3-LS9.1-FD | CP_Elec_8x10 | yes | risky / risky | 0 / 0 | verified |
| C9002 | X322512MSB4SI | CRYSTAL-SMD_4P-L3.2-W2.5-BL | Crystal_SMD_3225-4Pin_3.2x2.5mm |  | identical / identical | 0 / 0 | verified |
| C165948 | TYPE-C-31-M-12 | USB-C_SMD-TYPE-C-31-M-12_1 | USB_C_Receptacle_HRO_TYPE-C-31-M-12 |  | compatible / compatible | 0 / 0 | verified |

**Agreement: 79 / 79 (100%), all verified.** 25 parts are polarized: 7 LEDs, 12 diodes, 3 tantalum and 3 electrolytic
capacitors. Pads: 61 compatible, 17 risky, 1 identical. Corrections: 0° on 42 parts, 180° on 18, 270° on 19.

- The 0402 resistor is risky for the same reason JLCPCB flagged it in the r/KiCad report: KiCad's pads are 1.02 mm
  apart, JLCPCB's 0.87 mm (+18%).
- KiCad's own footprints are risky against JLCPCB's on 17 parts. The common causes are lands that are wider than
  JLCPCB's (SOT-223, SOT-89, electrolytics), smaller (SOD-323, the 4030 inductor), or pads spaced differently (SS54
  on SMA, Kemet B and C tantalum).
- The SOIC-8 correction of 270° matches the kicad-jlcpcb-tools check of JLCPCB's placement preview. Every SOIC, TSSOP,
  QFP and SOT-23-6 in the set needs 270°, and every SOT-23 needs 180°.
- The same package does not always get the same turn. JLCPCB draws SN74LVC1G08's SOT-23-5 with pin 1 bottom-right
  (180°) but the regulators' SOT-23-5 bottom-left (270°). It draws one QFN-24 bottom-left (270°) but four other QFNs
  top-left (0°), and it draws tantalum and electrolytic + ends on either side. This is why ecadlib checks each part
  against JLCPCB's own footprint.
- Some LEDs (C2286, C131244, C2297, C49018) and SS54 (C22452) number the cathode pad 2. ecadlib pairs the pads by
  the pin names (K / A), so the cathode still lands on the cathode.
- JLCPCB numbers SOT-223's tab 4, while KiCad numbers it 2. JLCPCB's own symbol names both VOUT, so they are one pin,
  not a renumbering. JLCPCB's USB-C numbers each stacked land for both pins ("A1B12"), where KiCad stacks two pads.

The 0° rule of each family in `jlcpcb.json` comes from these parts. It is set only where every golden-set part of
the family agrees, and `measured` gives the count. `test_family_table_matches_every_golden_part` checks that each rule
gives the golden answer on every part of its family.

### Other tests (`tests/test_assembly.py`, 30 tests in all)

- **Synthetic poses**: 12 footprint shapes × 4 turns × 3 offsets, with and without noise, and off-grid turns. The turn
  and offset come back exactly, and noise stays within tolerance.
- **Real mismatches**:
  - KiCad vs JLCPCB 0402 is risky.
  - A SOT-23 with pin 1 in the other corner is incompatible.
  - An LED with the cathode on the other side is turned 180° by its pin names, not by its pad numbers.
  - Renumbered or mirrored pads are incompatible, never a rotation. CC1 and DP1 swapped on the USB-C, or SOT-223's
    tab named a different pin, is caught.
  - The wrong footprint for the part is caught: SOT-23 lands for a SOT-23-5 regulator are incompatible, and SOD-123
    lands for an SMA diode are risky.
  - Polarity is read from JLCPCB's filled silk marks when its pins carry no names. Without the marks, the turn is
    never guessed.
  - A bidirectional TVS (package ...-BI) is not treated as polarized.
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
- **Families without a 0° rule** need JLCPCB's footprint to get a rotation; without it, they are listed as unknown.
  Some have no rule because JLCPCB draws them both ways: tantalum, electrolytic, SOT-23-5 and QFN. Others have only one
  golden-set part so far: SOT-223 and the 4-pad crystal. A table rotation is only a fallback; a polarized part on one
  always gets a review item.
- **Stock** comes from the same EasyEDA answer (`result.lcsc.stock`), so it is as fresh as the cache.
- **JLCPCB's own preview is the last word.** Run your CPL through JLCPCB's placement preview before you pay. If it
  disagrees with ecadlib, add an override to your profile and please report it.

## Credits

- JLCPCB's footprints come from the JLCEDA / EasyEDA Official Library
  ([lceda.cn](https://lceda.cn/), [easyeda.com](https://easyeda.com)).
- The method of aligning KiCad footprints onto them follows
  [kicad-jlcpcb-tools](https://github.com/Bouni/kicad-jlcpcb-tools) (MIT).
- The test footprints are KiCad's library (CC-BY-SA 4.0 with the KiCad library exception).
