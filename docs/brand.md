# ecadlib brand

## The mark

**Sprout Trace, bold weight.** A copper trace grows out of a via and branches into two leaves: a PCB library that
keeps growing. It is drawn once, in `ecadlib/brand.py`, and everything else is generated from that file:

| Where | What | Made by |
|---|---|---|
| Website favicon | `site/favicon.svg` (ink flips for dark browser tabs) | `python scripts/build_brand.py` |
| Website and web UI header | the mark inline, ink = text colour, leaves = `--copper` | pasted from `brand.mark_svg_body()` |
| Brand kit | `site/brand/` (SVG on light, on dark, one colour; PNG tiles at 64 and 256) | `python scripts/build_brand.py` |
| KiCad toolbar button and PCM list icon | the mark on a dark tile, 24 and 64 px | `kicad_menu.icon_png()` |
| Windows installer, Start menu, .exe | `ecadlib.ico`, every size drawn separately | `installer/windows/make_icon.py` |

`tests/test_brand.py` fails if a generated file no longer matches the drawing.

The tile version (dark rounded square, light trace, copper leaves) is for places whose background we don't control,
such as toolbars, the taskbar and app lists. Everywhere else the mark sits directly on the page.

## Colour

The product stays monochrome: one ground colour, ink for text and lines. **Copper is an accent only.**

| Token | Light | Dark |
|---|---|---|
| `--copper` | `#c2622d` | `#e8834a` |
| ink (`--text`) | `#232327` | `#d0d6e0` (mark on dark: `#ececf0`) |

Use copper for the mark's leaves, keyboard focus rings, one of the decorative side traces in each website section
(drawn at `--trace-cu`, copper mixed 45% into the page colour, with its signal dot in full copper) and at most one
small highlight per view. Don't use it for
large fills, body text, buttons or status colours (ok, warning and error keep their own colours).
Where only one colour is allowed (print, engraving), use the one-colour SVG.

## Don'ts

- Don't recolour the trace in copper or the leaves in ink, except in the one-colour version.
- Don't redraw the mark by hand or stretch it. Change `ecadlib/brand.py` and regenerate.
- Don't place it next to Altium or KiCad logos in a way that suggests it is their product.
