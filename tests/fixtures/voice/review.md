# Library review: board_bom.csv

**7 parts: 2 ready, 3 held, 1 waiting for sign-off, 1 duplicate.**

3 parts are held: fix them first. 1 waiting for sign-off. 1 ready part to double-check.

**Next:**

1. Go through the held parts one at a time with `ecadlib review` in a terminal, or in `ecadlib ui` (Fill the library): **Pick…** a symbol or footprint, or **Release anyway** after you have looked.
2. Ask a reviewer to approve the parts waiting for sign-off (`ecadlib ui`, Sign-off).

## Held: fix these first (3)

Each held part says what is wrong and what to do. It stays out of the library until it is fixed or released anyway.

### CH340C (held)

- There is no symbol for this part yet.  
  **Next:** Pick a symbol from the candidates, or let the datasheet reader draw one.

Symbol from: none · Footprint from: standard parts (SOIC-16_3.9x9.9mm_P1.27mm) · KiCad and Altium: match · Height: 0.45 mm · Datasheet: <https://example.com/ds.pdf>

What was tried: standard parts: footprint Package_SO:SOIC-16; symbol: no standard pinout for this part

### QFN-PART (held)

- The two datasheet reads disagree on pitch (0.5 mm vs 0.65 mm); nothing was written to the library.  
  **Next:** Check the datasheet and pick one, or release anyway.
- There is no symbol for this part yet.  
  **Next:** Pick a symbol from the candidates, or let the datasheet reader draw one.
- There is no footprint for this part yet.  
  **Next:** Pick a footprint from the candidates, or let the datasheet reader build one.

Symbol from: none · Footprint from: none · Cross-check: disagreed (held) · KiCad and Altium: match · Height: 0.45 mm · Datasheet: <https://example.com/ds.pdf>

What was tried: datasheet reader: held, the cross-check disagrees (see datasheet/*.review.md)

### LM358DR (held)

- Symbol pin 9 has no pad on the footprint.  
  **Next:** Pick a footprint that matches the symbol's pin numbers, or release anyway if those pins are not connected.
- The 3D height is 2.1 mm, outside the usual 1.2-1.85 mm for this package.  
  **Next:** Check the height in the datasheet; fix the 3D model, or release anyway if it is right.
- Pad 1 size/rotation differs: KiCad (1.95, 0.6, 0), Altium (1.95, 0.65, 0). It is new since the part was released anyway.  
  **Next:** Fill the library again so both are written from the same footprint; report it if they still differ.

Symbol from: KiCad library (R) · Footprint from: KiCad library (SOIC-8_3.9x4.9mm_P1.27mm) · KiCad and Altium: match · Height: 0.45 mm · Released anyway: Ann 2026-10-16 · Datasheet: <https://example.com/ds.pdf>

## Waiting for sign-off (1)

### NE555DR (waiting for sign-off)

- Waiting for sign-off (1 approval from role librarian).  
  **Next:** Ask a reviewer to approve it in the review queue.

Symbol from: KiCad library (R) · Footprint from: KiCad library (R_0603_1608Metric) · KiCad and Altium: match · Height: 0.45 mm · Datasheet: <https://example.com/ds.pdf>

## Ready, but check before you release (1)

These passed every check; a person should still look at the points below.

### AO3401A (ready)

- The pinout of Q_PMOS_GSD comes from the AO3401A family table, not from this part's datasheet.  
  **Next:** Check pins 1-3 against the datasheet, then approve it or release anyway.

Symbol from: standard parts (Q_PMOS_GSD) · Footprint from: standard parts (SOT-23) · KiCad and Altium: match · Height: 0.45 mm · Datasheet: <https://example.com/ds.pdf>

## Duplicates (1)

### R-DUP (duplicate)

- This part is a duplicate of RES-0603-10k.  
  **Next:** Use RES-0603-10k instead, or keep both on purpose.

Symbol from: KiCad library (R) · Footprint from: KiCad library (R_0603_1608Metric) · KiCad and Altium: match · Height: 0.45 mm · Datasheet: <https://example.com/ds.pdf>

## Ready (2)

| Part | Status | Symbol from | Footprint from | Cross-check | KiCad and Altium |
|---|---|---|---|---|---|
| RES-0603-10k | ready | KiCad library (R) | KiCad library (R_0603_1608Metric) | - | match |
| AO3401A | ready | standard parts (Q_PMOS_GSD) | standard parts (SOT-23) | - | match |

## Details

### What was tried

- x: y
