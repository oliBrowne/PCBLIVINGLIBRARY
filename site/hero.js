// The landing page's hero: one line of a BOM opened up into the six things ecadlib builds from it, scroll by scroll,
// then turned into the Altium panel. The footprint and symbol are ecadlib's own output for TLC5951DAP (hero-data.js,
// made by scripts/build_site_hero.py); the BOM, the source lines and the panel are an example run.
import { FOOTPRINT as FP, SYMBOL as SYM } from "./hero-data.js";

const root = document.getElementById('xh-scene');
const $ = s => document.querySelector(s);
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const ease = t => 1 - Math.pow(1 - t, 3);
const back = t => { const c = 1.4; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };   // a small overshoot
const inout = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const smooth = t => t * t * (3 - 2 * t);
const span = (p, a, b) => clamp((p - a) / (b - a));
const lerp = (a, b, t) => a + (b - a) * t;
const VB = '-7.5 -9.5 15 19';
const MONO = 'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"';
const html = s => { const t = document.createElement('template'); t.innerHTML = s.trim(); return t.content.firstChild; };

(function grain() {
  try {
    const c = document.createElement('canvas'), g = c.getContext('2d');
    c.width = c.height = 180;
    const im = g.createImageData(180, 180);
    for (let i = 0; i < im.data.length; i += 4) { const v = Math.random() * 255; im.data[i] = im.data[i + 1] = im.data[i + 2] = v; im.data[i + 3] = 255; }
    g.putImageData(im, 0, 0);
    root.style.setProperty('--xh-noise', `url(${c.toDataURL()})`);
  } catch (e) { /* the scene reads fine without grain */ }
})();

// ---------- geometry from the generators
const pads = FP.pads.filter(p => p.n !== '39'), ep = FP.pads.find(p => p.n === '39'), pin1 = pads.find(p => p.n === '1');
const rr = (p, extra = '') => { const r = Math.min(p.w, p.h) * .25; return `<rect x="${p.x - p.w / 2}" y="${p.y - p.h / 2}" width="${p.w}" height="${p.h}" rx="${r}" ${extra}/>`; };
const txt = (x, y, s, size, fill, extra = '') => `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" ${MONO} ${extra}>${s}</text>`;
// a marker's place on its card, in card pixels (the card is 15 x 19 units, 20 px each)
const at = (x, y) => [150 + x * 20, 190 + y * 20];

// ---------- the six layers, bottom to top, in the order ecadlib works
// each has: build(el) -> its contents; update(el, r, t) where r = 0 (not done yet) .. 1 (done)
const BOM_ROWS = [['C1–C8', 'GRM188R71H104KA93D', 8, 'found'], ['R1–R4', 'RC0603FR-0710KL', 4, 'found'], ['U1', 'LM1117MPX-3.3/NOPB', 1, 'found'],
  ['U4', 'TLC5951DAP', 1, '···'], ['J1', 'B2B-PH-K-S(LF)(SN)', 1, 'found'], ['J2', 'DF13-6P-1.25DSA', 1, 'held']];
const SRC = [['KiCad libs', 'Driver_LED:TLC5951DAP'], ['Nexar', 'TLC5951DAP · Texas Instruments'], ['DigiKey', 'TLC5951DAP · datasheet'],
  ['Mouser', 'TLC5951DAP · datasheet'], ['datasheet', 'ti.com/lit/ds/symlink/tlc5951.pdf']];
const GLYPHS = ' .:-=+*#%@/\\|<>[]{}0123456789';
const CHECKS = ['copper gaps', 'silk clear of pads', 'pad 1 marked', 'courtyard', 'land = maker', 'PowerPAD mask'];
const LIB = [['Part number', 'TLC5951DAP'], ['Manufacturer', 'Texas Instruments'], ['Description', '24-channel 12-bit PWM LED driver, HTSSOP-38'],
  ['Symbol', 'TLC5951DAP · 39 pins'], ['Footprint', 'HTSSOP-38-1EP_6.1x12.5mm_P0.65mm'], ['3D model', 'HTSSOP-38-1EP.step'],
  ['Datasheet', 'ti.com/…/tlc5951.pdf']];

const LAYERS = [
  { key: 'bom', name: 'BOM', theme: 'dark', mark: [24, 142],
    build: el => {
      // the BOM, then the search for the one line ecadlib has to build: noise settles into answers from each source
      el.insertAdjacentHTML('beforeend', `<div class="xh-file">led-driver-board.csv</div>
        <div class="xh-tbl"><div class="xh-r xh-h"><span>Ref</span><span>MPN</span><span>Qty</span><span class="xh-st" style="opacity:1">Status</span></div>
        ${BOM_ROWS.map(r => `<div class="xh-r${r[1] === 'TLC5951DAP' ? ' xh-me' : ''}"><span>${r[0]}</span><span>${r[1]}</span><span>${r[2]}</span><span class="xh-st">${r[3]}</span></div>`).join('')}</div>
        <div class="xh-scan"></div>
        <div class="xh-srch"><pre></pre><div class="xh-q">Searching · U4 TLC5951DAP</div><div class="xh-res">${SRC.map(s => `<div><b>${s[0]}</b><span></span></div>`).join('')}</div></div>`);
      el._st = [...el.querySelectorAll('.xh-r:not(.xh-h) .xh-st')]; el._me = el.querySelector('.xh-r.xh-me .xh-st');
      el._pre = el.querySelector('pre'); el._out = [...el.querySelectorAll('.xh-res span')]; el._q = el.querySelector('.xh-q'); el._tick = -1;
      el._field = Array.from({ length: 12 }, () => Array.from({ length: 44 }, () => GLYPHS[Math.random() * GLYPHS.length | 0]));
    },
    update: (el, r, t) => {
      const tick = Math.floor(t * 12);
      if (tick === el._tick && el._r === r) return;
      el._tick = tick; el._r = r;
      el._st.forEach((s, i) => { s.style.opacity = clamp(span(r, 0, .3) * BOM_ROWS.length - i); });
      for (let k = 0; k < 30; k++) { const row = el._field[Math.random() * 12 | 0]; row[Math.random() * 44 | 0] = GLYPHS[Math.random() * GLYPHS.length | 0]; }
      el._pre.textContent = el._field.map(a => a.join('')).join('\n');
      const sr = span(r, .3, .92);
      el._out.forEach((o, i) => {
        const s = SRC[i][1], done = clamp(sr * SRC.length - i);
        o.parentNode.style.opacity = done > 0 ? 1 : .25;
        o.textContent = [...s].map((ch, j) => j / s.length < done ? ch : (done > 0 && ch !== ' ' ? GLYPHS[Math.random() * GLYPHS.length | 0] : ' ')).join('');
      });
      const found = sr >= 1;
      el._q.textContent = found ? 'U4 TLC5951DAP · build from datasheet' : 'Searching · U4 TLC5951DAP';
      el._me.textContent = found ? 'draw' : '···';
    } },

  { key: 'ds', name: 'Datasheet', theme: 'dark', mark: at(0, 7.3),
    build: el => {
      // the maker's package drawing: the outline draws itself, the leads land, then each dimension opens out from
      // its middle while its number counts up, and a copper box marks the number as ecadlib reads it
      const INK = 'var(--xh-ds-ink)', SOFT = 'var(--xh-ds-soft)';
      const dim = (x1, y1, x2, y2, val, tx, ty, rot = 0) => {
        const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
        return `<g class="xh-dm" data-c="${cx} ${cy}" data-v="${val}" data-h="${y1 === y2 ? 1 : 0}"><g class="xh-dl"><line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${INK}" stroke-width=".035" marker-start="url(#xh-ar)" marker-end="url(#xh-ar)"/></g>
          ${val ? txt(tx, ty, val.toFixed(2), .5, INK, `class="xh-dv" text-anchor="middle" font-weight="600" ${rot ? `transform="rotate(${rot} ${tx} ${ty})"` : ''}`) : ''}</g>`;
      };
      const ext = (x1, y1, x2, y2) => `<line class="xh-ex" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${SOFT}" stroke-width=".02"/>`;
      el.insertAdjacentHTML('beforeend', `<svg viewBox="${VB}" aria-hidden="true">
        <defs><marker id="xh-ar" viewBox="0 0 4 4" refX="2" refY="2" markerWidth="2.6" markerHeight="2.6" orient="auto-start-reverse"><path d="M0 0L4 2L0 4z" fill="${INK}"/></marker>
          <pattern id="xh-mm" width="1" height="1" patternUnits="userSpaceOnUse"><path d="M1 0H0V1" fill="none" stroke="var(--xh-ds-grid)" stroke-width=".03"/></pattern></defs>
        <rect x="-7" y="-8.2" width="14" height="16.8" fill="url(#xh-mm)"/>
        <rect class="xh-ol" x="-3.05" y="-6.25" width="6.1" height="12.5" fill="#1d1a15" stroke="${INK}" stroke-width=".06" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1"/>
        <g class="xh-ld" fill="#26221b" stroke="${INK}" stroke-width=".03">${pads.map(p => `<rect x="${p.x < 0 ? -4.05 : 3.05}" y="${p.y - .12}" width="1" height=".24"/>`).join('')}</g>
        <rect class="xh-pp" x="-1.52" y="-3.18" width="3.04" height="6.35" fill="none" stroke="${INK}" stroke-width=".035" stroke-dasharray=".2 .12"/>
        <circle class="xh-pp" cx="-2.3" cy="-5.5" r=".26" fill="none" stroke="${INK}" stroke-width=".03"/>
        <g class="xh-exs">${ext(-4.05, 6.4, -4.05, 7.6)}${ext(4.05, 6.4, 4.05, 7.6)}${ext(3.2, -6.25, 5.5, -6.25)}${ext(3.2, 6.25, 5.5, 6.25)}${ext(-4.2, -5.85, -6, -5.85)}${ext(-4.2, -5.2, -6, -5.2)}</g>
        ${dim(-4.05, 7.3, 4.05, 7.3, 8.10, 0, 6.95)}${dim(5.2, -6.25, 5.2, 6.25, 12.50, 5.85, 0, 90)}${dim(-5.6, -5.85, -5.6, -5.2, 0.65, -6.35, -4.55)}
        ${txt(-6.8, -7.5, 'HTSSOP-38 · POWERPAD', .52, INK, 'font-weight="700"')}
        ${txt(-6.8, 8.35, 'MECHANICAL DATA · MM', .52, SOFT)}${txt(6.8, 8.35, 'TI', .52, SOFT, 'text-anchor="end"')}
        <g class="xh-reads" fill="none" stroke="var(--copper)" stroke-width=".05">
          <rect x="-1.3" y="6.35" width="2.6" height=".85" opacity="0"/><rect x="5.3" y="-1.3" width=".85" height="2.6" opacity="0"/><rect x="-7.05" y="-5.15" width="1.6" height=".8" opacity="0"/></g>
      </svg><div class="xh-band"></div>`);
      el._ol = el.querySelector('.xh-ol'); el._ld = [...el.querySelectorAll('.xh-ld rect')]; el._pp = [...el.querySelectorAll('.xh-pp')];
      el._ex = el.querySelector('.xh-exs'); el._reads = [...el.querySelectorAll('.xh-reads rect')];
      el._dims = [...el.querySelectorAll('.xh-dm')].map(g => ({ g, dl: g.querySelector('.xh-dl'), dv: g.querySelector('.xh-dv'), c: g.dataset.c.split(' ').map(Number), v: +g.dataset.v, h: g.dataset.h === '1' }));
      el._last = -1;
    },
    update: (el, r) => {
      const key = Math.round(r * 500); if (key === el._last) return; el._last = key;
      el._ol.setAttribute('stroke-dashoffset', (1 - ease(span(r, 0, .3))).toFixed(3));
      el._ld.forEach((d, i) => d.setAttribute('opacity', clamp(span(r, .12, .45) * 20 - (i % 19))));
      el._pp.forEach(d => d.setAttribute('opacity', span(r, .38, .5)));
      el._ex.setAttribute('opacity', span(r, .4, .5));
      el._dims.forEach((d, i) => {
        const k = ease(span(r, .45 + i * .14, .66 + i * .14)), [cx, cy] = d.c;
        d.g.setAttribute('opacity', k > 0 ? 1 : 0);
        d.dl.setAttribute('transform', `translate(${cx} ${cy}) scale(${d.h ? Math.max(k, .001) : 1} ${d.h ? 1 : Math.max(k, .001)}) translate(${-cx} ${-cy})`);
        if (d.dv) d.dv.textContent = (d.v * k).toFixed(2);
      });
      el._reads.forEach((d, i) => { const a = span(r, .62 + i * .14, .7 + i * .14); d.setAttribute('opacity', (a * (1 - span(r, .93, 1))).toFixed(3)); });
    } },

  { key: 'sym', name: 'Symbol', theme: 'dark', mark: null,
    build: el => {
      // symbol_layout units are 10 mil: the body is 140 x 270, pins 30 long; fit it to the card. The body draws,
      // each pin draws out and its name types in, then signals run along a few pins while the layer is out
      const [x0, y0, w, hgt] = SYM.rect, s = .043, cx = (w / 2), cy = (hgt / 2);
      const X = x => ((x - cx) * s).toFixed(3), Y = y => ((-(y - cy)) * s + .6).toFixed(3);
      const ends = [];
      const pinEl = p => {
        const dx = p.side === 'left' ? -p.len : p.side === 'right' ? p.len : 0, dy = p.side === 'top' ? p.len : p.side === 'bottom' ? -p.len : 0;
        const x2 = p.x + dx, y2 = p.y + dy, vert = p.side === 'top' || p.side === 'bottom';
        const nx = p.side === 'left' ? p.x + 4 : p.side === 'right' ? p.x - 4 : p.x, ny = p.side === 'top' ? p.y - 5 : p.side === 'bottom' ? p.y + 5 : p.y;
        const anchor = p.side === 'left' ? 'start' : p.side === 'right' ? 'end' : vert && p.side === 'top' ? 'end' : 'start';
        ends.push([+X(p.x), +Y(p.y), +X(x2), +Y(y2), p.side]);
        return `<g class="xh-pin"><line x1="${X(p.x)}" y1="${Y(p.y)}" x2="${X(x2)}" y2="${Y(y2)}" stroke="var(--xh-sch-pin)" stroke-width=".06" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1"/>
          <circle cx="${X(x2)}" cy="${Y(y2)}" r=".07" fill="none" stroke="var(--xh-sch-pin)" stroke-width=".03" opacity="0"/>
          <text x="${X(nx)}" y="${Y(ny)}" font-size=".4" fill="var(--xh-sch-name)" ${MONO} text-anchor="${anchor}" dominant-baseline="central" ${vert ? `transform="rotate(-90 ${X(nx)} ${Y(ny)})"` : ''}></text></g>`;
      };
      el.insertAdjacentHTML('beforeend', `<svg viewBox="${VB}" aria-hidden="true">
        <defs><pattern id="xh-sg" width=".5" height=".5" patternUnits="userSpaceOnUse"><circle cx="0" cy="0" r=".025" fill="var(--xh-sch-grid)"/></pattern></defs>
        <rect x="-7.1" y="-8.4" width="14.2" height="17.5" fill="url(#xh-sg)"/>
        <g stroke="#24324a" stroke-width=".03" fill="none"><rect x="-7.1" y="-8.4" width="14.2" height="17.5"/><rect x="-6.7" y="-8" width="13.4" height="16.7"/></g>
        <g font-size=".46" fill="#4d5d73" ${MONO} text-anchor="middle">${[...'1234'].map((n, i) => `<text x="${-5 + i * 3.35}" y="-8.07">${n}</text>`).join('')}${[...'ABCD'].map((n, i) => `<text x="-6.9" y="${-5.5 + i * 4.2}">${n}</text>`).join('')}</g>
        <rect class="xh-bd" x="${X(x0)}" y="${Y(y0)}" width="${(w * s).toFixed(3)}" height="${(Math.abs(hgt) * s).toFixed(3)}" fill="#0f1826" stroke="var(--xh-sch-ink)" stroke-width=".07" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1"/>
        ${SYM.pins.map(pinEl).join('')}
        <g class="xh-lb">${txt(0, -6.7, 'U4', .55, 'var(--xh-sch-ink)', 'text-anchor="middle" font-weight="700"')}${txt(0, 7.9, 'TLC5951DAP', .5, 'var(--xh-sch-name)', 'text-anchor="middle"')}</g>
        <g class="xh-sigs"></g>
      </svg>`);
      el._bd = el.querySelector('.xh-bd'); el._lb = el.querySelector('.xh-lb');
      el._pins = [...el.querySelectorAll('.xh-pin')].map((g, i) => ({ ln: g.children[0], dot: g.children[1], tx: g.children[2], name: SYM.pins[i].name }));
      // a few signals: into two inputs, out of three outputs
      const L = ends.map((e, i) => [e, i]).filter(([e]) => e[4] === 'left'), R = ends.map((e, i) => [e, i]).filter(([e]) => e[4] === 'right');
      el._sig = [L[1], L[Math.floor(L.length / 2)], R[2], R[Math.floor(R.length / 2)], R[R.length - 3]].filter(Boolean).map(([e], j) => {
        const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        c.setAttribute('r', '.12'); c.setAttribute('class', 'xh-sig'); c.setAttribute('opacity', '0');
        el.querySelector('.xh-sigs').appendChild(c);
        return { c, e, j, inn: e[4] === 'left' };
      });
      const p0 = SYM.pins[0];
      el._mark = at(+X(p0.x - p0.len), +Y(p0.y));
      el._last = -1;
    },
    update: (el, r, t) => {
      // signals move with time once the symbol is drawn
      el._sig.forEach(s => {
        const ph = (t * .55 + s.j * .37) % 1, u = s.inn ? 1 - ph : ph, [x1, y1, x2, y2] = s.e;
        s.c.setAttribute('cx', lerp(x1, x2, u).toFixed(3)); s.c.setAttribute('cy', lerp(y1, y2, u).toFixed(3));
        s.c.setAttribute('opacity', (span(r, .9, 1) * Math.sin(ph * Math.PI)).toFixed(3));
      });
      const key = Math.round(r * 500); if (key === el._last) return; el._last = key;
      el._bd.setAttribute('stroke-dashoffset', (1 - ease(span(r, 0, .25))).toFixed(3));
      el._lb.setAttribute('opacity', span(r, .15, .3));
      const n = el._pins.length;
      el._pins.forEach((p, i) => {
        const k = span(r, .2 + i / n * .6, .3 + i / n * .6);
        p.ln.setAttribute('stroke-dashoffset', (1 - k).toFixed(3));
        p.dot.setAttribute('opacity', k >= 1 ? 1 : 0);
        p.tx.textContent = p.name.slice(0, Math.round(p.name.length * span(k, .3, 1)));
      });
    } },

  { key: 'fp', name: 'Footprint', theme: 'dark', mark: at(pin1.x, pin1.y),
    build: el => {
      el.insertAdjacentHTML('beforeend', `<svg viewBox="${VB}" aria-hidden="true">
        <defs><pattern id="xh-pg" width="1" height="1" patternUnits="userSpaceOnUse"><circle cx="0" cy="0" r=".03" fill="#2b2b2b"/></pattern></defs>
        <rect x="-7.5" y="-9.5" width="15" height="19" fill="url(#xh-pg)"/>
        <rect class="xh-pd" x="${-ep.w / 2}" y="${-ep.h / 2}" width="${ep.w}" height="${ep.h}" fill="var(--xh-pcb-top)" fill-opacity=".38" stroke="var(--xh-pcb-top)" stroke-width=".08" style="transform-box:fill-box;transform-origin:center"/>
        <g fill="var(--xh-pcb-top)">${pads.map(p => rr(p, 'class="xh-pd" style="transform-box:fill-box;transform-origin:center"')).join('')}</g>
        <g fill="var(--xh-pcb-paste)" opacity=".9">${FP.paste.map(p => rr(p, 'class="xh-ps" style="transform-box:fill-box;transform-origin:center"')).join('')}</g>
        <polygon class="xh-fab" points="${FP.fab.map(p => p.join(',')).join(' ')}" fill="none" stroke="var(--xh-pcb-fab)" stroke-width=".07" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1"/>
        <rect class="xh-crt" x="${FP.crt[0]}" y="${FP.crt[1]}" width="${FP.crt[2] - FP.crt[0]}" height="${FP.crt[3] - FP.crt[1]}" fill="none" stroke="var(--xh-pcb-crtyd)" stroke-width=".06" stroke-dasharray=".3 .18"/>
        <g class="xh-silk" stroke="var(--xh-pcb-silk)" stroke-width=".14" stroke-linecap="round">${FP.silk.map(l => `<line x1="${l[0][0]}" y1="${l[0][1]}" x2="${l[1][0]}" y2="${l[1][1]}" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1"/>`).join('')}
          <polygon points="${FP.pin1.map(p => p.join(',')).join(' ')}" fill="var(--xh-pcb-silk)" stroke="none"/>
          ${txt(0, -7.35, 'U4', .8, 'var(--xh-pcb-silk)', 'text-anchor="middle" font-weight="700" stroke="none"')}</g>
        ${txt(-7, 8.95, 'TOP · PASTE · SILK · CRTYD', .52, '#8a8c95')}
      </svg><div class="xh-sweep"></div>`);
      // pads land in pin order (pad 1 first, then the PowerPAD), paste stamps after, then the outlines draw
      const all = [...el.querySelectorAll('.xh-pd')];
      el._pads = all.slice(1).concat(all.slice(0, 1)); el._paste = [...el.querySelectorAll('.xh-ps')];
      el._fab = el.querySelector('.xh-fab'); el._crt = el.querySelector('.xh-crt');
      el._silk = [...el.querySelectorAll('.xh-silk line')]; el._silkRest = [...el.querySelectorAll('.xh-silk polygon, .xh-silk text')];
    },
    update: (el, r) => {
      const n = el._pads.length;
      el._pads.forEach((p, i) => { const k = clamp(r * 1.6 * (n + 8) / 8 - i / 8); p.style.transform = `scale(${k <= 0 ? 0 : back(k)})`; });
      el._paste.forEach((p, i) => { const k = clamp((r - .55) * 2.4 * 15 / 3 - i / 3); p.style.transform = `scale(${k <= 0 ? 0 : back(k)})`; });
      el._fab.setAttribute('stroke-dashoffset', (1 - span(r, .3, .6)).toFixed(3));
      el._silk.forEach(l => l.setAttribute('stroke-dashoffset', (1 - span(r, .5, .8)).toFixed(3)));
      el._silkRest.forEach(e => e.setAttribute('opacity', span(r, .7, .85)));
      el._crt.setAttribute('opacity', span(r, .8, .95));
    } },

  { key: '3d', name: '3D model', theme: 'dark', mark: at(-2.25, -5.45),
    build: el => {
      el.insertAdjacentHTML('beforeend', `<svg viewBox="${VB}" aria-hidden="true">
        <defs><linearGradient id="xh-body" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3d3e45"/><stop offset=".55" stop-color="#1c1d21"/><stop offset="1" stop-color="#0f1013"/></linearGradient>
          <linearGradient id="xh-lead" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#7d7f87"/><stop offset=".35" stop-color="#f1f2f5"/><stop offset=".6" stop-color="#a0a2aa"/><stop offset="1" stop-color="#d0d1d6"/></linearGradient>
          <filter id="xh-mold" x="0" y="0" width="1" height="1"><feTurbulence type="fractalNoise" baseFrequency="3.2" numOctaves="2" seed="4"/><feColorMatrix type="saturate" values="0"/><feComponentTransfer><feFuncA type="table" tableValues="0 .18"/></feComponentTransfer><feComposite in2="SourceGraphic" operator="in"/></filter></defs>
        <ellipse cx=".4" cy=".8" rx="5.6" ry="7.6" fill="rgba(0,0,0,.5)" filter="blur(.6px)"/>
        <g class="xh-solid">
          <g>${pads.map(p => `<rect x="${p.x < 0 ? -4.1 : 3.05}" y="${p.y - .13}" width="1.05" height=".26" rx=".05" fill="url(#xh-lead)"/>`).join('')}</g>
          <rect x="-3.05" y="-6.25" width="6.1" height="12.5" rx=".2" fill="url(#xh-body)"/>
          <rect x="-3.05" y="-6.25" width="6.1" height="12.5" rx=".2" fill="#fff" filter="url(#xh-mold)"/>
          <rect x="-2.88" y="-6.08" width="5.76" height="12.16" rx=".14" fill="none" stroke="rgba(255,255,255,.12)" stroke-width=".05"/>
          <circle cx="-2.25" cy="-5.45" r=".3" fill="#0b0c0e" stroke="rgba(255,255,255,.1)" stroke-width=".03"/>
          <g ${MONO} fill="#b3b5bc" text-anchor="middle" transform="rotate(-90)"><text x="0" y="-.35" font-size=".95" font-weight="700">TLC5951</text><text x="0" y=".6" font-size=".55" fill="#7c7e86">TI · DAP</text></g>
        </g>
        <g class="xh-wire" fill="none" stroke="#c9cbd2" stroke-width=".03">
          <rect x="-3.05" y="-6.25" width="6.1" height="12.5" pathLength="1"/><rect x="-2.6" y="-5.8" width="5.2" height="11.6" pathLength="1"/>
          ${[-4, -2, 0, 2, 4].map(y => `<line x1="-3.05" y1="${y}" x2="3.05" y2="${y}" pathLength="1"/>`).join('')}<line x1="0" y1="-6.25" x2="0" y2="6.25" pathLength="1"/>
          ${pads.map(p => `<rect x="${p.x < 0 ? -4.1 : 3.05}" y="${p.y - .13}" width="1.05" height=".26" pathLength="1"/>`).join('')}</g>
        ${txt(-7, 8.95, 'STEP · 6.1 × 12.5 MM', .52, '#9a9ca4')}
      </svg><div class="xh-sheen"></div>`);
      el._solid = el.querySelector('.xh-solid'); el._wire = el.querySelector('.xh-wire'); el._wl = [...el._wire.children]; el._last = -1;
      el._wl.forEach(w => w.setAttribute('stroke-dasharray', '1'));
    },
    update: (el, r) => {
      const key = Math.round(r * 400); if (key === el._last) return; el._last = key;
      const n = el._wl.length;
      el._wl.forEach((w, i) => w.setAttribute('stroke-dashoffset', (1 - span(r, i / n * .35, .15 + i / n * .35)).toFixed(3)));
      el._solid.setAttribute('opacity', smooth(span(r, .45, .85)));
      el._wire.setAttribute('opacity', 1 - smooth(span(r, .7, .95)));
    } },

  { key: 'lib', name: 'Library', theme: 'dark', mark: [8, 205],
    build: el => {
      // the finished record: its fields fill, the rulebook ticks through, then it is released
      el.insertAdjacentHTML('beforeend', `<div class="xh-bar"><svg class="xh-logo" aria-hidden="true"><use href="#mark"/></svg></div>
        <div class="xh-f">${LIB.map(f => `<div><b>${f[0]}</b><span></span></div>`).join('')}<div><b>Status</b><span><em class="xh-pill"><i></i><s>Draft</s></em></span></div></div>
        <div class="xh-ck"><div class="xh-q">Checks</div><ul>${CHECKS.map(c => `<li>${c}</li>`).join('')}</ul></div>
        <div class="xh-sum">0 problems<small>ready to release</small></div>`);
      el.querySelector('.xh-f').lastElementChild.querySelector('s').style.textDecoration = 'none';
      el._f = [...el.querySelectorAll('.xh-f span')].slice(0, LIB.length); el._pill = el.querySelector('.xh-pill'); el._word = el.querySelector('.xh-pill s');
      el._ck = [...el.querySelectorAll('.xh-ck li')]; el._sum = el.querySelector('.xh-sum'); el._last = -1;
    },
    update: (el, r) => {
      const key = Math.round(r * 300); if (key === el._last) return; el._last = key;
      el._f.forEach((s, i) => { const v = LIB[i][1]; s.textContent = v.slice(0, Math.round(v.length * clamp(span(r, 0, .42) * LIB.length - i))); });
      el._ck.forEach((c, i) => c.classList.toggle('xh-on', span(r, .42, .8) * CHECKS.length > i + .5));
      el._sum.style.opacity = span(r, .8, .88);
      const on = r > .93; el._pill.classList.toggle('xh-on', on); el._word.textContent = on ? 'Released · Altium 365' : 'Draft';
    } },
];

const NOTES = [
  ['BOM in', 'It starts from one line of your BOM and looks for a part you can trust.', [['line', 'U4 · TLC5951DAP · ×1'], ['looked in', 'KiCad libs, Nexar, DigiKey, Mouser'], ['found', 'symbol, maker datasheet']]],
  ["Read the maker's drawing", 'The datasheet is the truth. Every number comes from it.', [['package', 'HTSSOP-38 PowerPAD'], ['body', '6.1 × 12.5 mm'], ['span', '8.10 mm · pitch 0.65']]],
  ['Lay out the symbol', 'Grouped by function, the way you would draw it.', [['pins', '38 + PowerPAD'], ['left', 'inputs'], ['right', 'outputs'], ['power', 'VCC top, GND bottom']]],
  ['Build the footprint', 'From the land pattern the maker recommends.', [['pads', '38 × 1.80 × 0.30 mm'], ['PowerPAD', '5.20 × 12.50 mm'], ['mask open', '3.39 × 6.35 mm'], ['paste', '3 × 5 windows, 65 %']]],
  ['Make the 3D model', 'A STEP body that matches the package.', [['body', '6.1 × 12.5 mm'], ['leads', '38 gull-wing'], ['pin 1', 'dimple at the corner']]],
  ['Check it, release it', 'Checked against the rulebook, then into your own library with a real description.', [['rulebook', '0 problems'], ['to', 'Altium 365 or KiCad'], ['in doubt', 'held for you, never built']]],
];

const stack = $('#xh-stack');
const els = LAYERS.map((L, i) => {
  const el = document.createElement('div');
  el.className = `xh-layer xh-L-${L.key}`;
  el.innerHTML = `<div class="xh-lname"><em>${String(i + 1).padStart(2, '0')}</em>${L.name}</div>`;
  L.build(el);
  const m = L.mark || el._mark;
  const mk = html(`<div class="xh-mk" style="left:${m[0]}px;top:${m[1]}px"></div>`);
  el.appendChild(mk); el._mk = mk;
  el._shade = el.appendChild(html('<div class="xh-shade"></div>'));
  stack.appendChild(el); return el;
});
const N = els.length, mid = (N - 1) / 2;
// small per-layer offsets so every edge shows
const OFF = els.map((_, i) => [Math.sin(i * 2.3) * 10, Math.cos(i * 1.7) * 8, Math.sin(i * 1.3) * 1.6]);

// one note card for the whole walk: the card stays, only its text changes over
const callout = html(`<div class="xh-callout">${NOTES.map((n, i) => `<div class="xh-ct"><b><i>${String(i + 1).padStart(2, '0')} / ${String(N).padStart(2, '0')}</i>${n[0]}</b><p>${n[1]}</p>${n[2].map(r => `<div class="xh-row"><span class="xh-k">${r[0]}</span>${r[1]}</div>`).join('')}</div>`).join('')}</div>`);
$('#xh-callouts').appendChild(callout);
const cts = [...callout.children];
const leaders = $('#xh-leaders');
const rows = [...document.querySelectorAll('.xh-panel .xh-prow')];
const beats = [...document.querySelectorAll('#xh-beats span')];
const reduce = matchMedia('(prefers-reduced-motion: reduce)'), narrow = matchMedia('(max-width: 760px) and (min-height: 521px), (max-width: 760px) and (orientation: portrait)');   // the same line as hero.css
const scene = root, stage = $('#xh-stage'), rig = $('#xh-rig'), panel = $('#xh-panel'), shadow = $('#xh-shadow'), end = $('#xh-end'), intro = $('#xh-intro');

// the timeline, as fractions of the pinned scroll
const T = { explode: [.05, .19], steps: [.2, .66], fan: [.65, .77], morph: [.69, .78], rows: .755, out: [.84, .885], end: [.875, .94] };
const STEP = (T.steps[1] - T.steps[0]) / N;

function progress() {
  // reduced motion holds one still frame beside the headline: on a wide screen every layer built and apart, none
  // pulled out; on a phone, where the headline sits under the stack, the closed part
  if (reduce.matches) return narrow.matches ? 0 : T.steps[1] + .006;
  const r = scene.getBoundingClientRect();
  return clamp(-r.top / (r.height - innerHeight));
}

function render() {
  const p = progress(), t = reduce.matches ? 0 : performance.now() / 1000;
  const W = innerWidth, H = innerHeight, phone = narrow.matches;
  const navH = parseFloat(root.style.getPropertyValue('--xh-nav')) || 0;   // the header sits over the stage
  // desktop: the opened stack (about 300 px of card plus the gaps at a ~0.83 tilt) must fit between the header
  // and the step bar, so the card is sized for a wide gap; the stack also opens along a diagonal for more spread.
  // phones: the stack gets the space between the header and the note card, with a much wider gap than desktop,
  // since a phone has height to spare and little width
  const G = N - 1, room = H - 240;
  const scale = phone ? Math.min(W / 640, H / 1300) : clamp(Math.min(W / 1440 * 1.25, room / (300 + G * 96 * .83)), .55, 1.5);
  const gap = phone ? clamp((H - 390 - 280 * scale) / (G * .76 * scale), 64, 140) : clamp((room / scale - 300) / (G * .83), 50, 110);
  const diag = phone ? 12 : 34;

  const fan = reduce.matches ? 0 : inout(span(p, ...T.fan));
  const walk = span(p, ...T.steps);
  const lift = span(p, ...T.explode);
  // phones move the camera down ahead of the peel, so the top layer never runs up under the header
  const lead = ease(span(p, T.explode[0], T.explode[0] + .09));
  const gone = smooth(span(p, ...T.out));
  // after the turn the stack behind the panel keeps easing round, so the scroll never goes dead
  const settle = smooth(span(p, T.fan[1] - .02, T.out[1]));
  const drift = Math.sin(t * .35) * 2 * (1 - fan * .6);

  // the step position: rests on each layer long enough to read its note, then glides to the next, so one layer
  // slides home while the next slides out and there is never a moment with no layer out (raw = i mid step i)
  const raw = (p - T.steps[0]) / STEP - .5, fl = Math.floor(raw);
  const fc = clamp(fl + smooth(span(raw - fl, .3, .7)), 0, N - 1);
  // how much "a layer is out" at all: eases in before step 1 and out after step 8
  const A = smooth(span(p, T.steps[0] - .03, T.steps[0] + STEP * .3)) * (1 - smooth(span(p, T.steps[1] - STEP * .35, T.steps[1] + .005)));
  const focus = i => A * clamp(1 - Math.abs(i - fc));

  // camera: a slow orbit the whole way, then the stack turns to face you behind the panel
  const rx = lerp(lerp(56, 48, walk), phone ? 10 : 6, fan), ry = lerp(0, phone ? -10 : -16, fan) - settle * 7;
  const rz = lerp(lerp(-36, -22, walk) + drift, phone ? -4 : 0, fan) + settle * 3;
  const shiftX = phone ? lerp(lerp(0, 28, lift), 34, fan) : lerp(Math.min(190, W * .1 + 50), Math.min(260, W * .18), fan);
  // the camera follows the step that is on a little, so the stack never has to leave the screen
  const ic = lerp(mid, fc, A);
  const follow = (ic - mid) * gap * Math.sin(rx * Math.PI / 180) * scale * (phone ? .12 : .2) * lift * (1 - fan);
  // phones: the closed part sits between the header and the headline (higher still on a short phone, where the
  // headline is tall for the screen); the opened stack is centred between the header and the note card (H - 190
  // tall, so 95 px above centre)
  const top0 = phone ? Math.min(-H * .18, (navH + intro.offsetTop - H) / 2) : 0;
  const shiftY = (phone ? lerp(lerp(top0, -95, lead), -H * .18, fan) : lerp(lerp(0, -H * .025, lift), -30, fan)) + follow;
  rig.style.transform = `translate(${shiftX}px, ${shiftY}px) scale(${scale * lerp(1, phone ? .62 : .78, fan)}) rotateX(${rx}deg) rotateY(${ry}deg) rotateZ(${rz}deg)`;
  shadow.style.transform = `translate(${shiftX}px, ${shiftY}px) scale(${scale * lerp(.9, 1.35, lift)})`;
  shadow.style.opacity = (1 - fan) * lerp(.7, 1, lift);
  stage.style.setProperty('--xh-gx', `${50 + shiftX / W * 100}%`);

  els.forEach((el, i) => {
    const k = N - 1 - i;                                   // 0 for the top layer
    // the layers peel off from the top one at a time, with a little overshoot
    const peel = back(span(p, T.explode[0] + k * .011, T.explode[0] + .07 + k * .011));
    const z0 = lerp((i - mid) * 9, (i - mid) * gap, peel);           // a hair apart at rest, so every material shows at the edge
    const bob = Math.sin(t * .9 + i * .8) * 4 * peel * (1 - fan);
    const f = focus(i);
    const [ox, oy, orot] = OFF[i];
    // the layer whose step is on slides out of its own slot like a drawer and lifts a little toward you, but never
    // past the layer above it, so the order of the stack never changes
    const slide = phone ? 104 : 170, up = phone ? gap * .3 : 24;
    const x = lerp(ox * peel + (i - mid) * diag * peel - f * slide, k * (phone ? 24 : 40), fan);
    const y = lerp(oy * peel + f * (phone ? 16 : 24), -k * (phone ? 18 : 22), fan);
    const z = lerp(z0 + bob + f * up, -k * 70, fan);
    el.style.transform = `translate3d(${x}px, ${y}px, ${z}px) rotateZ(${orot * peel * (1 - fan)}deg)`;
    // layers out of focus darken (an opaque shade, so nothing shows through), never fade
    el._shade.style.opacity = (A * (phone ? .36 : .28) * (1 - f / Math.max(A, 1e-6))).toFixed(3);
    el.style.opacity = (1 - fan * (k > 5 ? .45 : 0)) * (1 - gone);
    el.style.filter = fan > .02 && k > 1 ? `blur(${(fan * (k - 1) * .45).toFixed(2)}px)` : '';
    // what is built on each layer: all of it at rest, rewinds as the stack opens, then each draws itself in while
    // it is out, tied to the scroll so it plays backward too
    const rewind = 1 - span(p, T.explode[0] + .02, T.explode[1]);
    const built = p >= T.steps[1] ? 1 : Math.max(rewind, ease(span(raw, i - .6, i + .25)));
    LAYERS[i].update(el, built, t);
    el._mk.style.opacity = f.toFixed(3);
  });

  // intro copy holds, then gives way
  const introOut = reduce.matches ? 0 : span(p, .04, .12);
  intro.style.opacity = 1 - introOut;
  intro.style.transform = `translateY(${-introOut * 24}px)`;
  intro.style.visibility = introOut >= 1 ? 'hidden' : '';             // its links can't be clicked or tabbed to once gone
  intro.style.pointerEvents = introOut > .5 ? 'none' : '';
  $('#xh-hint').style.opacity = 1 - span(p, .01, .04);

  // the note card: on for the whole walk; the outgoing text lifts away as the next one rises in
  const sr = stage.getBoundingClientRect();
  const a0 = Math.floor(fc), a1 = Math.min(a0 + 1, N - 1), fr = fc - a0;
  callout.style.opacity = A.toFixed(3);
  cts.forEach((c, i) => {
    const o = i === a0 ? 1 - smooth(span(fr, 0, .6)) : i === a1 && a1 !== a0 ? smooth(span(fr, .4, 1)) : 0;
    c.style.opacity = o.toFixed(3);
    c.style.transform = `translateY(${((i === a0 ? -1 : 1) * 10 * (1 - o)).toFixed(1)}px)`;
    c.style.visibility = o > 0 ? '' : 'hidden';
  });
  let path = '';
  if (A > .005) {
    // the leader's far end glides from this layer's marker to the next one's
    const mp = i => { const m = els[i]._mk.getBoundingClientRect(); return [m.left + m.width / 2 - sr.left, m.top + m.height / 2 - sr.top]; };
    const [m0x, m0y] = mp(a0), [m1x, m1y] = mp(a1), mx = lerp(m0x, m1x, fr), my = lerp(m0y, m1y, fr);
    let ex, ey, kx;
    if (phone) {
      const box = callout.getBoundingClientRect();
      ex = clamp(mx, box.left - sr.left + 20, box.right - sr.left - 20); ey = box.top - sr.top; kx = ex;
    } else {
      const w = callout.offsetWidth, h = callout.offsetHeight;
      const cx = Math.max(16, W / 2 + shiftX - 330 * scale - w - 40), cy = clamp(H * .4 - h / 2, 80, H - h - 70);
      callout.style.left = cx + 'px'; callout.style.top = cy + 'px';
      ex = cx + w + 14; ey = cy + 11; kx = ex + 40;
    }
    const d = phone ? `M${ex} ${ey}L${mx} ${my}` : `M${ex} ${ey}H${kx}L${mx} ${my}`;
    const len = Math.abs(kx - ex) + Math.hypot(mx - kx, my - ey);
    path = `<path d="${d}" stroke-dasharray="${len}" stroke-dashoffset="${len * (1 - A)}" opacity="${A}"/>
      <circle class="xh-dot" cx="${ex}" cy="${ey}" r="2.2" opacity="${A}"/><circle class="xh-ring" cx="${mx}" cy="${my}" r="${8 + 8 * (1 - A)}" opacity="${A * .8}"/>`;
  }
  leaders.innerHTML = path;

  // the top card of the turned stack lifts off and becomes the panel: the panel starts on the card's place and
  // size, grows to its own, and the card fades as the panel takes over; then the panel fills one line at a time
  const pin = inout(span(p, ...T.morph));
  const pw = panel.offsetWidth, ph = panel.offsetHeight;
  const pcx = panel.offsetLeft + pw / 2, pcy = panel.offsetTop + ph / 2;
  const card = els[N - 1].getBoundingClientRect();
  const ccx = card.left + card.width / 2 - sr.left, ccy = card.top + card.height / 2 - sr.top;
  const s0 = Math.min(1, card.width / pw * .9);
  const drift2 = settle * (phone ? -8 : -14);          // the panel keeps drifting up a touch while it fills
  // it rests between the header and the step bar (a phone: above the bar, where hero.css puts it; wider: centred),
  // and on a screen too short for it (a phone on its side) it shrinks to fit there and moves off the centre
  const top = navH + 12, bottom = H - (phone ? 60 : 56);
  const fit = Math.min(1, (bottom - top) / ph), hh = ph * fit / 2;
  const rest = clamp((phone ? pcy : H / 2) + drift2, top + hh, Math.max(top + hh, bottom - hh));
  panel.style.transformOrigin = '50% 50%';
  panel.style.opacity = (smooth(span(pin, 0, .3)) * (1 - gone)).toFixed(3);
  panel.style.transform = `translate(${(ccx - pcx) * (1 - pin)}px, ${lerp(ccy + drift2, rest, pin) - pcy}px) perspective(1400px) rotateY(${lerp(phone ? -10 : -16, 0, pin)}deg) scale(${lerp(s0, fit, pin)})`;
  els[N - 1].style.opacity = +els[N - 1].style.opacity * (1 - smooth(span(pin, .05, .45)));
  rows.forEach((r, i) => r.classList.toggle('xh-on', p > T.rows + i * .016));

  // the closing card
  const e = smooth(span(p, ...T.end));
  end.style.opacity = e; end.classList.toggle('xh-live', e > .5);
  end.style.visibility = e > 0 ? 'visible' : '';                     // hidden (hero.css) until it fades in
  end.inert = e <= .5;                                                // and out of the tab order until it is there
  rig.style.visibility = e > .99 ? 'hidden' : '';
  panel.style.visibility = e > .99 || +panel.style.opacity === 0 ? 'hidden' : '';

  const marks = [0, T.steps[0], T.fan[0], T.out[0], 1.0001];
  beats.forEach((b, i) => { const f = span(p, marks[i], marks[i + 1]); b.classList.toggle('xh-on', f > 0 && f < 1 || (i === 3 && f >= 1)); b.style.setProperty('--xh-f', f.toFixed(3)); });
}

// the site's nav goes dark while it sits over the scene
const nav = document.querySelector('.nav');
const navOver = () => { const r = scene.getBoundingClientRect(), h = nav?.offsetHeight || 0; nav?.classList.toggle('xh-over', r.top <= h + 1 && r.bottom > h); };

// the scene only draws while it is on screen, so the rest of the page scrolls on an idle main thread
let running = false, onScreen = true;
function loop() { if (!running) return; render(); navOver(); requestAnimationFrame(loop); }
const start = () => { if (!running && onScreen && !reduce.matches) { running = true; requestAnimationFrame(loop); } };
root.classList.add('xh-js');                           // without this script the hero is a plain, still first screen
// the scene starts under the nav, so its first screen is the whole window (the nav is dark over it)
const tuck = () => root.style.setProperty('--xh-nav', `${nav?.offsetHeight || 0}px`);
tuck(); addEventListener('resize', tuck);
if (reduce.matches) {
  scene.style.height = 'auto'; stage.style.position = 'relative';
  render(); navOver();
  addEventListener('resize', render);
  addEventListener('scroll', navOver, { passive: true });
} else {
  new IntersectionObserver((es) => {
    onScreen = es[es.length - 1].isIntersecting;
    if (onScreen) start(); else { running = false; nav?.classList.remove('xh-over'); }
  }).observe(scene);
  render(); navOver(); start();
}
reduce.addEventListener?.('change', () => location.reload());
