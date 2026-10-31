# ecadlib landing page

A static page about ecadlib: what it does, the footprints it draws, bring-your-own-model, and how to start. No build
step and no framework; open it through any static server.

```
python3 -m http.server -d site 8000      # then http://localhost:8000
```

(Opening `index.html` straight from disk does not load `footprints.json`, because browsers block `fetch` on `file://`.)

## Files

* `index.html`, `site.css`, `site.js`: the page. Same look as the web UI (Hairline: one ground colour, 1px lines, mono
  labels, ease-out motion), light and dark, and it holds still under `prefers-reduced-motion`.
* `hero.js`, `hero.css`: the first screen. Scrolling opens one BOM line (TLC5951DAP) into the six things ecadlib
  builds from it (BOM and search, datasheet, symbol, footprint, 3D model, library record), then turns it into the
  Altium panel. Light or dark with the site's theme: each colour in `hero.css` is a `light-dark()` pair, with the dark
  set repeated at the end for browsers without it (a test keeps the two in step); the footprint stays the PCB editor's
  dark screen in both. Its own layout on phones; one still frame under `prefers-reduced-motion`; a plain first screen
  without JavaScript. Every class, id and custom property is prefixed `xh-` so it never meets `site.css`
  (`tests/test_site.py` checks). The footprint's pad colour is one switch: `data-pads="editor"` (the PCB editor's red)
  or `"copper"` on the hero's `<section>` in `index.html`.
* `hero-data.js`: the TLC5951DAP footprint and symbol the hero draws, made by ecadlib's own `footprint_gen` and
  `symbol_layout`. Rebuild after changing either: `python3 scripts/build_site_hero.py` (a test fails while it is stale).
  The BOM, the source lines and the Altium panel in the hero are an example run, and the page says so.
* `hairline.js`: the line figures. Six come from [Hairline](https://github.com/lucasmarkes/hairline) (MIT,
  `hairline.LICENSE.txt`); the old hero's board (`scripts/hairline-site/board.js`, ecadlib's own) is still bundled
  but no longer on the page. Rebuild after changing a figure: `sh scripts/build_site_figures.sh`.
* `footprints.json`: six land patterns made by ecadlib's own IPC-7351B generator from `ecadlib/package_specs`, which
  the page draws like Altium's PCB editor. Rebuild after changing the generator: `python3 scripts/build_site_footprints.py`.

## Publishing

`.github/workflows/pages.yml` publishes this folder to GitHub Pages, but only when it is run by hand: set
Settings > Pages > Source to "GitHub Actions", then Actions > pages > Run workflow. Any other static host works too
(the folder is the whole site).

Vercel: `vercel.json` at the repository root tells Vercel to serve `site/` as plain static files (no install, no
build), so importing the repository at vercel.com/new needs no settings changed. Once the project is linked, every
push to `main` updates the live page and every pull request gets its own preview link.
