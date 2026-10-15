# Footprint rules and where the numbers come from

Researched 2026-10-13. Units are mm. Densities A/B/C are IPC most/nominal/least (KiCad's M/N/L).
`ecadlib/footprint_gen.py` and `ecadlib/footprint_check.py` implement what is below.

## Sources

| Source | Used for |
|---|---|
| KiCad footprint generator, `gitlab.com/kicad/libraries/kicad-footprint-generator` (HEAD a903a695, 2026-10-10): `src/kilibs/config/ipc_configs/ipc_7351b.yaml`, `ipc7351B_2terminal.yaml`, `global_configs/config_KLCv3.0.yaml`, `data/SMD_2terminal_chip_molded/`, `data/package/gullwing/`, `data/package/no_lead/` | fillet tables, tolerances, rounding, silk/courtyard settings, body sizes for the reference parts |
| KiCad Library Conventions (KLC), git source `gitlab.com/kicad/libraries/klc` (klc.kicad.org itself was blocked from the sandbox) | silk, fab, courtyard, pin-1, pad and paste rules |
| KiCad official footprints, `gitlab.com/kicad/libraries/kicad-footprints` | reference pads, courtyards and silk in `tests/test_footprints.py` |
| IPC-7351 (2005) text; Tom Hausherr, *The CAD Library* (CADlib.pdf); PCB Libraries forum | MELF table, cross-checks. The IPC-7351B text itself could not be fetched. |

## Fillet goals (B / nominal density)

Toe sets the outer edge of the land (Z), heel the inner edge (G), side the width (X).

| Family | Toe | Heel | Side | Courtyard | Round-off | Source |
|---|---|---|---|---|---|---|
| Chip ≥ 0603 | 0.35 | 0 | 0 | 0.25 | 0.05 | 7351B table 3-5 (KiCad) |
| Chip < 0603 | 0.20 | 0 | 0 | 0.15 | 0.02 | 7351B table 3-6 (KiCad) |
| Gull-wing, pitch ≥ 0.625 | 0.35 | 0.35 | 0.03 | 0.25 | 0.05 | 7351B table 3-2 (KiCad) |
| Gull-wing, pitch < 0.625 | 0.35 | 0.35 | −0.02 | 0.25 | 0.05 | 7351B table 3-3 (KiCad) |
| QFN / DFN / SON | 0.30 | 0 | −0.04 | 0.25 | 0.05 | 7351B table 3-15 (KiCad) |
| Inward L / molded (tantalum, SMA, SOD-123F) | 0.50 outer | 0.15 inner | −0.05 | 0.25 | 0.05 | 7351B table 3-13 |
| MELF | 0.40 | 0.10 | 0.05 | 0.25 | 0.05 | IPC-7351 (2005) table 3-7 |
| Outward flat lead | 0.20 | 0 | 0 | 0.15 | 0.05 | IPC-7352 (KiCad) |

A and C values for each family are in `FILLETS`. Notes:
* KiCad builds its tantalum footprints with IPC-7352 values, not 7351B, so our tantalum pads differ from KiCad's on purpose.
* SOD-123, SOD-323, SMA/SMB and SOT-223 in KiCad are hand-drawn from manufacturers' recommended lands. When the datasheet gives a land, put it in the spec's `land` and it is used as is.
* J-lead (PLCC/SOJ) and concave chip arrays are not generated yet; the published values conflict between sources.

## Calculation details that matter for matching KiCad

* F = 0.10, P = 0.05. S uses the RMS tolerance `sqrt(CL² + 2·CT²)`.
* Z, G and X are rounded to the nearest round-off step (not up/down).
* Opposite gull-wing rows are never closer than 0.4 mm (G ≥ 0.4). With an exposed pad, G ≥ EP + 0.4, so every pad keeps 0.2 mm from the EP (KLC F6.3).
* Pad width is also capped at pitch − 0.2 so a solder-mask web remains.
* Courtyard = max(pads, nominal body) + excess, rounded outward to 0.01.

## What a reviewer checks (KLC)

* Line widths: silk 0.12 (0.10–0.15), fab 0.10, courtyard 0.05.
* Silk is 0.11 outside the body, and its line edge stays 0.2 from the real (rounded) pad outline. On tiny parts where that leaves nothing, KiCad uses 0.1.
* Pin 1 at top-left; two-terminal parts have pin 1 on the left. ICs get a filled silk triangle at pin 1 and a fab chamfer of min(1, 25 % of the body).
* LEDs and diodes: pin 1 is the cathode (KiCad's D and LED symbols). Polarized capacitors: pin 1 is +. The silk bracket closes on the pin-1 side.
* Exposed pad: copper + mask only; paste in separate windows covering 50–80 % (65 % default). Thermal vias 0.2–0.3 drill, no paste over vias ≥ 0.3.
* Reference text 0.7 above the courtyard, value 0.7 below, `${REFERENCE}` on the fab layer.
