#!/usr/bin/env sh
# Rebuilds ecadlib/ui/static/hairline.js: a few line figures from Hairline (MIT, github.com/lucasmarkes/hairline)
# and our own seat.js, bundled into one small ES module the web UI loads. Needs git, node and network access.
# Run it after changing scripts/hairline/*.js, then commit the new static/hairline.js.
set -eu
REV=37d2e78dcb333ab30c3338e28ed4a690364ea014   # hairline 0.5.1
here=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git clone -q https://github.com/lucasmarkes/hairline.git "$tmp/hl"
git -C "$tmp/hl" checkout -q "$REV"
src="$tmp/hl/packages/hairline/src"
# seat.js is written for the hairline-create bench (it reads HL and calls hairline()); the entry supplies both.
awk -v seat="$here/scripts/hairline/seat.js" '/\/\*SEAT\*\//{while((getline l < seat)>0) print l; next} {print}' \
  "$here/scripts/hairline/entry.js" > "$src/ecadlib.js"
cd "$tmp" && npm init -y >/dev/null && npm i -s esbuild@0.24.0 >/dev/null
{ printf '/* Hairline figures (c) 2026 Lucas Marques, MIT License, github.com/lucasmarkes/hairline. Built by scripts/build_ui_figures.sh. */\n'
  npx esbuild "$src/ecadlib.js" --bundle --minify --format=esm --legal-comments=none; } > "$here/ecadlib/ui/static/hairline.js"
echo "wrote ecadlib/ui/static/hairline.js ($(wc -c < "$here/ecadlib/ui/static/hairline.js") bytes)"
