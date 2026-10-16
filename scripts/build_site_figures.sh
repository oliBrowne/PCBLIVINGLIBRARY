#!/usr/bin/env sh
# Rebuilds site/hairline.js: line figures from Hairline (MIT, github.com/lucasmarkes/hairline) and our own
# board.js, bundled into one small ES module the landing page loads. Needs git, node and network access.
# Run it after changing scripts/hairline-site/*.js, then commit the new site/hairline.js.
set -eu
REV=37d2e78dcb333ab30c3338e28ed4a690364ea014   # hairline 0.5.1
here=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git clone -q https://github.com/lucasmarkes/hairline.git "$tmp/hl"
git -C "$tmp/hl" checkout -q "$REV"
src="$tmp/hl/packages/hairline/src"
# board.js is written for the hairline-create bench (it reads HL and calls hairline()); the entry supplies both.
awk -v fig="$here/scripts/hairline-site/board.js" '/\/\*BOARD\*\//{while((getline l < fig)>0) print l; next} {print}' \
  "$here/scripts/hairline-site/entry.js" > "$src/ecadlib-site.js"
cd "$tmp" && npm init -y >/dev/null && npm i -s esbuild@0.24.0 >/dev/null
{ printf '/* Hairline figures (c) 2026 Lucas Marques, MIT License (hairline.LICENSE.txt), github.com/lucasmarkes/hairline. Built by scripts/build_site_figures.sh. */\n'
  npx esbuild "$src/ecadlib-site.js" --bundle --minify --format=esm --legal-comments=none; } > "$here/site/hairline.js"
echo "wrote site/hairline.js ($(wc -c < "$here/site/hairline.js") bytes)"
