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
* `hairline.js`: the line figures. Six come from [Hairline](https://github.com/lucasmarkes/hairline) (MIT,
  `hairline.LICENSE.txt`); the hero's board is ecadlib's own (`scripts/hairline-site/board.js`, written and checked
  with Hairline's `hairline-create` skill). Rebuild after changing the figure: `sh scripts/build_site_figures.sh`.
* `footprints.json`: six land patterns made by ecadlib's own IPC-7351B generator from `ecadlib/package_specs`, which
  the page draws like Altium's PCB editor. Rebuild after changing the generator: `python3 scripts/build_site_footprints.py`.

## Publishing

`.github/workflows/pages.yml` publishes this folder to GitHub Pages, but only when it is run by hand: set
Settings > Pages > Source to "GitHub Actions", then Actions > pages > Run workflow. Any other static host works too
(the folder is the whole site).
