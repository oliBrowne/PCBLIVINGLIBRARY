// 04 Workflow, v2: an unseen hand adds TLC5951DAP (the hero's part) in Altium Designer and in KiCad.
// The symbol and footprint are ecadlib's own generator output (hero-data.js); the ecadlib window copies the real
// Altium script form and KiCad plugin window: same header, rows, buttons and words. Sources, timings and costs are
// an example run.
import { FOOTPRINT, SYMBOL } from "./hero-data.js";

const root = document.querySelector("[data-wk]");
const $ = (s, el = root) => el.querySelector(s), $$ = (s, el = root) => [...el.querySelectorAll(s)];
const fitBox = $(".wk-fit"), win = $(".wk-win"), cam = $(".wk-cam"), cap = $(".wk-cap"), stepsEl = $(".wk-steps"), playBtn = $(".wk-play");
const W = 1040, H = 620;
const still = matchMedia("(prefers-reduced-motion: reduce)");
const S = { scene: "altium", run: 0, fast: false, paused: false, visible: false, fit: 1, camS: 1 };
const MARK = `<svg viewBox="0 0 32 32" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round"><path d="M16 23.5L16 13.5"/><path d="M16 20L10 14"/><path d="M16 15L22 9"/><circle cx="16" cy="26.8" r="2.75" stroke-width="2.5"/></g><path fill="var(--copper, #e8834a)" d="M8.58 13.91 L7.32 13.64 L6.22 13.22 L5.26 12.66 L4.44 11.97 L3.76 11.18 L3.21 10.30 L2.78 9.34 L2.47 8.33 L2.28 7.28 L2.19 6.20 L2.20 5.12 L3.28 5.12 L4.34 5.23 L5.36 5.46 L6.33 5.82 L7.23 6.32 L8.03 6.95 L8.74 7.73 L9.31 8.66 L9.74 9.74 L10.01 10.99 L10.11 12.41 L10.00 14.00Z"/><path fill="var(--copper, #e8834a)" d="M22.02 7.74 L22.18 6.58 L22.48 5.54 L22.90 4.61 L23.45 3.79 L24.10 3.09 L24.85 2.50 L25.70 2.03 L26.62 1.68 L27.62 1.44 L28.68 1.32 L29.80 1.32 L29.80 2.49 L29.66 3.59 L29.41 4.61 L29.03 5.55 L28.54 6.40 L27.93 7.14 L27.20 7.77 L26.37 8.29 L25.43 8.68 L24.39 8.93 L23.24 9.04 L22.00 9.00Z"/></svg>`;

/* ---------------- timing ---------------- */
class Stop extends Error {}
const wait = (ms) => new Promise((ok, no) => {
  const run = S.run;
  if (S.fast) return ok();
  const tick = (left) => {
    if (run !== S.run) return no(new Stop());
    if (S.paused || !S.visible) return setTimeout(() => tick(left), 150);
    if (left <= 0) return ok();
    setTimeout(() => tick(left - 40), 40);
  };
  tick(ms);
});

/* ---------------- fit, camera, hand ---------------- */
new ResizeObserver(() => { S.fit = fitBox.clientWidth / W; win.style.setProperty("--fit", S.fit); }).observe(fitBox);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
// where an element is in the window's own 1040 x 620 pixels, whatever the fit and the camera are doing right now
function box(el) {
  const c = cam.getBoundingClientRect(), k = c.width / W, r = el.getBoundingClientRect();
  return { x: (r.left - c.left) / k, y: (r.top - c.top) / k, w: r.width / k, h: r.height / k, cx: (r.left - c.left + r.width / 2) / k, cy: (r.top - c.top + r.height / 2) / k };
}
async function camTo(fx, fy, s, ms = 1100) {
  if (S.fit < .62) s = Math.min(2.4, Math.max(s * 1.6, 2));     // a phone: always close in on the action
  const tx = clamp(W / 2 - fx * s, W - W * s, 0), ty = clamp(H / 2 - fy * s, H - H * s, 0);
  cam.style.transform = `translate(${tx}px, ${ty}px) scale(${s})`;
  S.camS = s;
  await wait(ms);
}
const camOn = (el, s, dx = 0, dy = 0) => { const b = box(el); return camTo(b.cx + dx, b.cy + dy, s); };
const camWide = () => camTo(W / 2, H / 2, 1);
const hand = $(".wk-cur");
let handAt = [700, 520];
function moveHand(x, y) { handAt = [x, y]; hand.style.transform = `translate(${x}px, ${y}px)`; }
async function point(el, dx = 0, dy = 0) { const b = box(el); moveHand(b.cx + dx, b.cy + dy); await wait(950); }
async function click(el, dx = 0, dy = 0) {
  await point(el, dx, dy);
  if (!S.fast) { const r = document.createElement("span"); r.className = "wk-rip"; r.style.left = `${handAt[0]}px`; r.style.top = `${handAt[1]}px`; cam.appendChild(r); setTimeout(() => r.remove(), 650); }
  el.classList.add("press"); await wait(170); el.classList.remove("press");
}
async function type(el, text) { el.classList.add("focus"); for (let i = 1; i <= text.length; i++) { el.textContent = text.slice(0, i); await wait(60); } await wait(250); el.classList.remove("focus"); }
let capTimer;
function say(text) {
  clearTimeout(capTimer);
  if (!text) { cap.classList.remove("on"); return; }
  cap.innerHTML = `<i></i>${text}`; cap.classList.add("on");
}

/* ---------------- menus ---------------- */
function openMenu(anchor, items, right = false) {
  closeMenus(false);
  const m = document.createElement("div"); m.className = "wk-menu";
  m.innerHTML = items.map((t) => (t === "-" ? '<div class="sep"></div>' : `<div>${t}</div>`)).join("");
  const a = box(anchor);
  m.style.left = `${right ? a.x + a.w - 4 : a.x}px`; m.style.top = `${right ? a.y - 4 : a.y + a.h + 1}px`;
  $(".wk-app").appendChild(m);
  anchor.classList.add("hot");
  return m;
}
function closeMenus(all = true) { $$(".wk-menu").forEach((m) => m.remove()); if (all) $$(".hot").forEach((e) => e.classList.remove("hot")); }
async function menuPick(anchor, items, pick) {
  await click(anchor); const m = openMenu(anchor, items); await wait(350);
  const it = [...m.children].find((d) => d.textContent === pick);
  it.classList.add("hot"); await click(it); closeMenus();
}

/* ---------------- ecadlib's window ---------------- */
const dlg = () => $(".wk-dlg");
function openDlg(tool, title) {
  const d = dlg();
  d.className = `wk-dlg ${tool}`; d.hidden = false;
  d.innerHTML = `<div class="os">ecadlib<b>✕</b></div><div class="hd">${MARK}<span class="ttl"></span><b>✕</b></div><div class="bd"></div>`;
  page(title);
  return d;
}
function page(title) {
  const d = dlg();
  $(".ttl", d).textContent = title === "ecadlib" ? "ecadlib" : `ecadlib · ${title}`;
  $(".bd", d).innerHTML = `<div class="big"></div><div class="slot"></div><ul class="rows"></ul><div class="notes"></div><div class="status"></div><div class="meter" hidden><i></i></div><div class="btns"></div>`;
}
const D = (s) => $(s, dlg());
const big = (t) => { D(".big").textContent = t; };
const row = (a, b, ok = false) => D(".rows").insertAdjacentHTML("beforeend", `<li class="${ok ? "ok" : ""}"><span>${a}</span><span>${b}</span></li>`);
async function rows(list, ms = 520) { for (const [a, b, ok] of list) { row(a, b, ok); await wait(ms); } }
const note = (t, ok = false) => D(".notes").insertAdjacentHTML("beforeend", `<div class="note${ok ? " ok" : ""}">${t}</div>`);
const status = (t) => { D(".status").textContent = t; };
function meter(p) { const m = D(".meter"); m.hidden = p === null; m.classList.toggle("swing", p === "swing"); if (typeof p === "number") $("i", m).style.width = `${p * 100}%`; else $("i", m).style.width = ""; }
function buttons(primary, secondary = "", busy = false) {
  D(".btns").innerHTML = `${secondary ? `<span class="s">${secondary}</span>` : ""}<span class="p${busy ? " busy" : ""}">${primary}</span>`;
}
async function closeDlg() { const d = dlg(); d.classList.add("out"); await wait(300); d.hidden = true; d.classList.remove("out"); }
async function seconds(label, from, to, step = 3, ms = 260) { for (let s = from; s <= to; s += step) { status(`${label} (${s} s)`); meter(Math.min(1, (s - from) / (to - from))); await wait(ms); } }

/* ---------------- drawing helpers ---------------- */
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
const ico = {
  doc: "M4 2h6l3 3v9H4zM10 2v3h3", open: "M2 5h4l1 1h7v7H2z", save: "M3 3h9l2 2v8H3zM5 3v3h6V3M5 13V9h6v4", print: "M4 6V2h8v4M3 6h10v6H3zM5 10h6v4H5z",
  undo: "M5 4L2 7l3 3M2 7h8a3 3 0 010 6H8", redo: "M11 4l3 3-3 3M14 7H6a3 3 0 000 6h2", find: "M7 3a4 4 0 110 8 4 4 0 010-8zM10 10l4 4",
  zin: "M7 3a4 4 0 110 8 4 4 0 010-8zM10 10l4 4M5 7h4M7 5v4", zout: "M7 3a4 4 0 110 8 4 4 0 010-8zM10 10l4 4M5 7h4", fit: "M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4",
  rot: "M12 5a5 5 0 10 1 5M12 2v3h-3", flip: "M8 2v12M3 4l3 4-3 4zM13 4l-3 4 3 4z", lock: "M4 7h8v7H4zM6 7V5a2 2 0 014 0v2",
  net: "M2 4h5v8H2zM9 4h5v8H9zM7 8h2", drc: "M3 8l3 3 7-7", cube: "M8 2l5 3v6l-5 3-5-3V5zM3 5l5 3 5-3M8 8v6", py: "M3 3h10v10H3zM5 6l2 2-2 2M8 10h3",
  grid: "M2 2h12v12H2zM6 2v12M10 2v12M2 6h12M2 10h12", mm: "M2 12V5M2 6l2-1 2 1v6M6 6l2-1 2 1v6", cur: "M8 2v12M2 8h12", rat: "M2 13L14 3M2 3l4 10",
  sel: "M4 2v11l3-3 2 4 2-1-2-4h4z", route: "M2 13l4-4h4l4-4", via: "M8 4a4 4 0 110 8 4 4 0 010-8zM8 6.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3",
  zone: "M3 3h10v10H3zM3 8l5-5M3 13L13 3M8 13l5-5", line: "M3 13L13 3", arc: "M3 13a10 10 0 0110-10", rect: "M3 4h10v8H3z", circ: "M8 3a5 5 0 110 10 5 5 0 010-10z",
  text: "M3 3h10M8 3v10", dim: "M2 6v6M14 6v6M2 9h12", del: "M4 4l8 8M12 4l-8 8", fp: "M5 3h6v10H5zM3 5h2M3 8h2M3 11h2M11 5h2M11 8h2M11 11h2",
  wire: "M2 12h5V4h7", bus: "M2 11h5V5h7M2 13h7V7h5", pwr: "M8 3v6M4 9h8M5.5 11h5M7 13h2", label: "M3 4h7l3 4-3 4H3z",
};
const tbi = (k) => (k === "|" ? '<b class="tsep"></b>' : `<i class="tbi"><svg viewBox="0 0 16 16"><path d="${ico[k]}"/></svg></i>`);
const chip = (n) => `<svg viewBox="0 0 26 22"><rect x="7" y="3" width="12" height="16" rx="1.5"/><path d="${Array.from({ length: n }, (_, i) => `M3 ${6 + i * 3.4}H7M19 ${6 + i * 3.4}H23`).join("")}"/></svg>`;
const prow = (name, desc, n = 2, isNew = false) => `<div class="prow${isNew ? " new" : ""}">${chip(n)}<div><b>${name}</b><span>${desc}</span></div></div>`;
const pad = (n) => FOOTPRINT.pads.find((p) => p.n === String(n));

// TLC5951DAP's symbol from ecadlib's symbol layout, drawn the way Altium draws a schematic part
function symbolSvg(bx, by, k) {
  const [, , w, h] = SYMBOL.rect;
  const X = (x) => bx + x * k, Y = (y) => by - y * k;
  let g = `<rect class="body dr" style="--l:${2 * (w + -h) * k + 20}" x="${bx}" y="${by}" width="${w * k}" height="${-h * k}"/>`;
  SYMBOL.pins.forEach((p, i) => {
    const x = X(p.x), y = Y(p.y), L = p.len * k;
    const [x2, y2] = p.side === "left" ? [x - L, y] : p.side === "right" ? [x + L, y] : p.side === "top" ? [x, y - L] : [x, y + L];
    g += `<path class="pin dr" style="--i:${i};--l:${L + 2}" d="M${x2} ${y2}L${x} ${y}"/>`;
    if (p.side === "left") g += `<text class="pinnm fi" style="--i:${i}" x="${x + 4}" y="${y + 2.6}">${p.name}</text><text class="pinno fi" style="--i:${i}" x="${x - L / 2}" y="${y - 2}" text-anchor="middle">${p.n}</text>`;
    else if (p.side === "right") g += `<text class="pinnm fi" style="--i:${i}" x="${x - 4}" y="${y + 2.6}" text-anchor="end">${p.name}</text><text class="pinno fi" style="--i:${i}" x="${x + L / 2}" y="${y - 2}" text-anchor="middle">${p.n}</text>`;
    else if (p.side === "top") g += `<text class="pinnm fi" style="--i:${i}" x="${x}" y="${y + 10}" text-anchor="middle">${p.name}</text><text class="pinno fi" style="--i:${i}" x="${x + 3}" y="${y - L / 2}">${p.n}</text>`;
    else g += `<text class="pinnm fi" style="--i:${i}" x="${x}" y="${y - 4}" text-anchor="middle">${p.name}</text><text class="pinno fi" style="--i:${i}" x="${x + 3}" y="${y + L / 2 + 3}">${p.n}</text>`;
  });
  g += `<text class="des fi" style="--i:20" x="${bx}" y="${by - 5}">U2</text><text class="cmt fi" style="--i:20" x="${bx + 30}" y="${by - 5}">TLC5951DAP</text>`;
  return g;
}
// TLC5951DAP's footprint from ecadlib's footprint generator, at k px per mm around (cx, cy)
function footprintSvg(cx, cy, k, tool) {
  const R = (r, cls, i) => `<rect class="${cls}" style="--i:${i}" x="${cx + (r.x - r.w / 2) * k}" y="${cy + (r.y - r.h / 2) * k}" width="${r.w * k}" height="${r.h * k}" rx="${tool === "kicad" && Math.min(r.w, r.h) < 1 ? Math.min(r.w, r.h) * k * .25 : 0}"/>`;
  const P = (pts) => pts.map(([x, y], i) => `${i ? "L" : "M"}${cx + x * k} ${cy + y * k}`).join("");
  const [x1, y1, x2, y2] = FOOTPRINT.crt;
  let g = `<rect class="crt fi" x="${cx + x1 * k}" y="${cy + y1 * k}" width="${(x2 - x1) * k}" height="${(y2 - y1) * k}"/>`;
  g += `<path class="fab dr" style="--l:300" d="${P(FOOTPRINT.fab)}Z"/>`;
  g += FOOTPRINT.pads.map((p, i) => R(p, "cu pd", p.n === "39" ? 40 : i)).join("");
  g += FOOTPRINT.silk.map((s) => `<path class="silk dr" style="--l:20;--i:30" d="${P(s)}"/>`).join("");
  g += `<path class="silkf fi" style="--i:30" d="${P(FOOTPRINT.pin1)}Z"/>`;
  g += `<text class="ref fi" style="--i:34" x="${cx}" y="${cy + y1 * k - 6}" text-anchor="middle">U2</text>`;
  return g;
}
const at = (cx, cy, k, n) => { const p = pad(n); return [cx + p.x * k, cy + p.y * k]; };
// a quad-flat (LQFP-32, 0.8 mm) and an 0603, for the parts already on the board
function lqfp(cx, cy, k, cls = "cu") {
  let g = "";
  for (let i = 0; i < 8; i++) {
    const o = (-2.8 + i * .8) * k, e = 4.25 * k, a = 1.5 * k, b = .55 * k;
    g += `<rect class="${cls}" x="${cx - e - a / 2}" y="${cy + o - b / 2}" width="${a}" height="${b}"/><rect class="${cls}" x="${cx + e - a / 2}" y="${cy - o - b / 2}" width="${a}" height="${b}"/>`;
    g += `<rect class="${cls}" x="${cx + o - b / 2}" y="${cy + e - a / 2}" width="${b}" height="${a}"/><rect class="${cls}" x="${cx - o - b / 2}" y="${cy - e - a / 2}" width="${b}" height="${a}"/>`;
  }
  const s = 3.6 * k;
  return g + `<path class="silk" d="M${cx - s} ${cy - s + 6}V${cy - s}H${cx - s + 6}M${cx + s - 6} ${cy - s}H${cx + s}V${cy - s + 6}M${cx + s} ${cy + s - 6}V${cy + s}H${cx + s - 6}M${cx - s + 6} ${cy + s}H${cx - s}V${cy + s - 6}"/><circle class="silkf" cx="${cx - s - 4}" cy="${cy - s - 4}" r="2"/>`;
}
const lqfpPad = (cx, cy, k, side, i) => { const o = (-2.8 + i * .8) * k, e = 4.25 * k; return side === "R" ? [cx + e, cy - o] : side === "L" ? [cx - e, cy + o] : side === "T" ? [cx - o, cy - e] : [cx + o, cy + e]; };
function r0603(cx, cy, k, ref, vertical = false) {
  const [w, h, d] = [.95, .9, .8];
  const pa = vertical ? [[cx - h * k / 2, cy - d * k - w * k / 2, h * k, w * k], [cx - h * k / 2, cy + d * k - w * k / 2, h * k, w * k]] : [[cx - d * k - w * k / 2, cy - h * k / 2, w * k, h * k], [cx + d * k - w * k / 2, cy - h * k / 2, w * k, h * k]];
  return pa.map(([x, y, a, b]) => `<rect class="cu" x="${x}" y="${y}" width="${a}" height="${b}" rx="2"/>`).join("") + `<text class="ref" x="${cx + (vertical ? 14 : 0)}" y="${cy + (vertical ? 3 : -14)}" text-anchor="${vertical ? "start" : "middle"}">${ref}</text>`;
}

/* ================= Altium Designer ================= */
const ALT = {
  bx: 470, by: 70, k: 1.3,
  chrome() {
    return `<div class="wk-app altium">
      <div class="tbar"><i class="ic"></i><span class="t">Board.PrjPcb - Altium Designer</span><span class="ctl"><b>—</b><b>☐</b><b>✕</b></span></div>
      <div class="mb">${["File", "Edit", "View", "Project", "Place", "Design", "Tools", "Reports", "ecadlib", "Window", "Help"].map((m) => `<span class="${m === "ecadlib" ? "ours" : ""}">${m}</span>`).join("")}</div>
      <div class="dt"><span class="on" data-doc="sch"><i></i>Board.SchDoc</span><span data-doc="pcb"><i class="pcb"></i>Board.PcbDoc</span></div>
      <div class="mid"><div class="cv"><svg viewBox="0 0 790 508" class="alt-sch">${this.sheet()}</svg>
        <div class="layertabs">${[["Top Layer", "#ff0000", 1], ["Bottom Layer", "#0000ff"], ["Mechanical 1", "#c000c0"], ["Top Overlay", "#ffff00"], ["Top Solder", "#800080"], ["Multi-Layer", "#c0c0c0"]].map(([n, c, on]) => `<span class="${on ? "on" : ""}"><i style="background:${c}"></i>${n}</span>`).join("")}</div></div>
        <div class="pn"><h4>Components <span>≡</span></h4><div class="fld">ecadlib.DbLib <span>▾</span></div><div class="fld q">Search</div><div class="cols"><span>Design Item ID</span><span>Footprint</span></div>
          <div class="list">${prow("STM32G031K8T6", "MCU, LQFP-32", 6)}${prow("RC0603FR-072K4L", "2.4k 1% 0603")}${prow("GRM188R71C104KA01D", "100n 16V 0603")}${prow("B4B-PH-K-S", "Header, 4 pin", 3)}</div></div></div>
      <div class="sb"><span>X:1250mil Y:880mil</span><span>Grid:100mil</span><span>Hotspot Snap</span><span class="sp"></span><span>Panels</span></div>
    </div>`;
  },
  sheet() {
    let gr = "";
    for (let x = 24; x <= 766; x += 10) gr += `M${x} 22V486`;
    for (let y = 22; y <= 486; y += 10) gr += `M24 ${y}H766`;
    const zx = [1, 2, 3, 4].map((n, i) => `<text class="zt" x="${24 + 742 / 8 + i * 742 / 4}" y="19.5" text-anchor="middle">${n}</text>`).join("");
    const zy = ["A", "B", "C", "D"].map((l, i) => `<text class="zt" x="19" y="${22 + 464 / 8 + i * 464 / 4 + 2}" text-anchor="middle">${l}</text>`).join("");
    const { bx, by, k } = this, tip = bx - 30 * k, Y = (n) => by + n * 10 * k;      // U2's left pin ends, and the y of a pin 10*n down
    const u1 = (y, name, num, right = true) => right ? `<path class="pin" d="M250 ${y}H276"/><text class="pinnm" x="246" y="${y + 2.6}" text-anchor="end">${name}</text><text class="pinno" x="263" y="${y - 2}" text-anchor="middle">${num}</text>`
      : `<path class="pin" d="M144 ${y}H170"/><text class="pinnm" x="174" y="${y + 2.6}">${name}</text><text class="pinno" x="157" y="${y - 2}" text-anchor="middle">${num}</text>`;
    return `<rect class="sheet" x="14" y="12" width="762" height="484"/><path class="grid" d="${gr}"/><rect class="zone" x="24" y="22" width="742" height="464"/>${zx}${zy}
      <path class="zone" d="M${24 + 742 / 4} 12V22M${24 + 742 / 2} 12V22M${24 + 742 * .75} 12V22M14 ${22 + 116}H24M14 ${22 + 232}H24M14 ${22 + 348}H24"/>
      <g><rect class="tblk" x="596" y="440" width="170" height="46"/><path class="zone" d="M596 455H766M596 470H766M690 455V486"/>
        <text class="zt" x="600" y="451">Title  Board.SchDoc · LED driver</text><text class="zt" x="600" y="466">Size  A4</text><text class="zt" x="694" y="466">Number  1</text><text class="zt" x="600" y="481">Sheet 1 of 1</text><text class="zt" x="694" y="481">Rev  A</text></g>
      <g class="abar"><rect x="318" y="28" width="132" height="18" rx="2"/>${["sel", "wire", "bus", "pwr", "label", "fp", "text"].map((kk, i) => `<g transform="translate(${325 + i * 17.5} 31) scale(.75)"><path d="${ico[kk]}"/></g>`).join("")}</g>
      <g><rect class="body" x="170" y="${Y(0)}" width="80" height="${Y(12) - Y(0)}"/>
        ${u1(Y(1), "PA7", 17)}${u1(Y(2), "PA5", 15)}${u1(Y(3), "PA4", 14)}${u1(Y(4), "PA8", 18)}${u1(Y(9), "PB0", 21)}
        ${u1(Y(1), "VDD", 4, false)}${u1(Y(3), "NRST", 6, false)}${u1(Y(5), "SWDIO", 23, false)}${u1(Y(6), "SWCLK", 24, false)}${u1(Y(11), "VSS", 5, false)}
        <text class="des" x="170" y="${Y(0) - 5}">U1</text><text class="cmt" x="170" y="${Y(12) + 11}">STM32G031K8T6</text></g>
      <path class="wire" d="M276 ${Y(1)}H${tip}M276 ${Y(2)}H${tip}M276 ${Y(3)}H${tip}M276 ${Y(4)}H${tip}M${tip - 13} ${Y(4)}V${Y(6)}H${tip}M${tip - 13} ${Y(5)}H${tip}"/>
      <circle class="junc" cx="${tip - 13}" cy="${Y(4)}" r="2"/><circle class="junc" cx="${tip - 13}" cy="${Y(5)}" r="2"/>
      <text class="netl" x="292" y="${Y(1) - 2.5}">GSSIN</text><text class="netl" x="292" y="${Y(2) - 2.5}">GSSCK</text><text class="netl" x="292" y="${Y(3) - 2.5}">GSLAT</text><text class="netl" x="292" y="${Y(4) - 2.5}">GSCLK</text>
      <path class="wire" d="M276 ${Y(9)}H330"/><text class="netl" x="292" y="${Y(9) - 2.5}">XBLNK</text><path class="wire" d="M${tip} ${Y(8)}H${tip - 14}"/><text class="netl" x="${tip - 15}" y="${Y(8) - 2.5}" text-anchor="end">XBLNK</text>
      <path class="wire" d="M${tip} ${Y(7)}H400V205M400 235V262"/><rect class="body" x="395" y="205" width="10" height="30"/><text class="des" x="390" y="217" text-anchor="end">R1</text><text class="cmt" x="390" y="228" text-anchor="end">2.4k</text>
      <path class="pwr" d="M391 262H409M394 266H406M397.5 270H402.5"/>
      <path class="wire" d="M144 ${Y(1)}H110V52"/><path class="pwr" d="M102 52H118"/><text class="netl" x="110" y="47" text-anchor="middle">+3V3</text>
      <path class="wire" d="M144 ${Y(11)}H110V${Y(11) + 22}"/><path class="pwr" d="M101 ${Y(11) + 22}H119M104 ${Y(11) + 26}H116M107.5 ${Y(11) + 30}H112.5"/>
      <g class="wk-new" id="wk-sym">${symbolSvg(bx, by, k)}</g>`;
  },
  pcb() {
    const k = 15, U = [430, 250], M = [200, 250];
    let g = "";
    for (let x = 0; x <= 790; x += 19) g += `M${x} 0V508`;
    for (let y = 0; y <= 508; y += 19) g += `M0 ${y}H790`;
    const hdr = [0, 1, 2, 3].map((i) => `<circle class="mlpad" cx="660" cy="${190 + i * 40}" r="10"/><circle class="hole" cx="660" cy="${190 + i * 40}" r="5"/>`).join("");
    const m = (side, i) => lqfpPad(M[0], M[1], k, side, i);
    const tr = (a, b, via) => `<path class="top" d="M${a[0]} ${a[1]}H${(a[0] + b[0]) / 2}L${b[0] - 20} ${b[1]}H${b[0]}"/>`;
    const rat = [[1, m("R", 0)], [2, m("R", 1)], [3, m("R", 2)], [4, m("R", 3)], [5, m("R", 3)], [6, m("R", 3)], [36, m("R", 6)]]
      .map(([n, b]) => { const a = at(U[0], U[1], k, n); return `<path class="rat" d="M${a[0]} ${a[1]}L${b[0]} ${b[1]}"/>`; }).join("")
      + (() => { const a = at(U[0], U[1], k, 34); return `<path class="rat" d="M${a[0]} ${a[1]}L560 ${375}"/>`; })()
      + (() => { const a = at(U[0], U[1], k, 35); return `<path class="rat" d="M${a[0]} ${a[1]}L560 ${125}"/>`; })()
      + (() => { const a = at(U[0], U[1], k, 39); return `<path class="rat" d="M${a[0]} ${a[1]}L660 310"/>`; })();
    return `<rect width="790" height="508" fill="#000"/><path d="${g}" stroke="#151515" stroke-width="1" fill="none"/>
      <rect class="board" x="60" y="40" width="660" height="420" rx="12"/>
      ${lqfp(M[0], M[1], k, "top")}<text class="ref" x="${M[0]}" y="${M[1] - 98}" text-anchor="middle">U1</text>
      ${r0603(560, 375, k, "R1", true).replaceAll('class="cu"', 'class="top"')}${r0603(560, 125, k, "C1", true).replaceAll('class="cu"', 'class="top"')}
      ${hdr}<text class="ref" x="660" y="168" text-anchor="middle">J1</text>
      <path class="topt" d="M${m("L", 0)[0]} ${m("L", 0)[1]}H110V430H620L660 390V310"/><path class="bott" d="M${m("B", 2)[0]} ${m("B", 2)[1]}V410H600L630 380V230H660"/><circle class="via" cx="${m("B", 2)[0]}" cy="410" r="6"/><circle class="hole" cx="${m("B", 2)[0]}" cy="410" r="2.6"/>
      <path class="topt" d="M${m("T", 1)[0]} ${m("T", 1)[1]}V80H620L660 120V190"/>
      <g class="wk-new" id="wk-pcb">${footprintSvg(U[0], U[1], k, "altium")}${rat}</g>`;
  },
  steps: [
    ["Check the project's BOM", "ecadlib › Check this project BOM reads the open project and finds what your library is missing.", 10.8],
    ["See the cost, then say yes", "Free sources go first. When only the datasheet will do, you see the model and the price, and nothing is sent until you click.", 7],
    ["Built and checked", "The datasheet reader and the footprint generator draw it, the release check passes, and Altium builds the library itself.", 10.2],
    ["Place it like any part", "TLC5951DAP is in Components with its symbol and footprint. Its pins land on your wires, and Update PCB brings the land pattern.", 15.2],
  ],
  async play(k) {
    const app = $(".wk-app"), ours = $(".mb .ours"), menu = ["Add part from MPN...", "Check this project BOM", "-", "Open review queue"];
    if (k === 0) {
      say(""); await wait(1200);
      await camOn(ours, 1.9, 60, 40);
      await menuPick(ours, menu, "Check this project BOM");
      openDlg("altium", "Check this project BOM"); await camTo(W / 2, H / 2, 1.42);
      big("Checking..."); meter("swing"); status("reading Board.PrjPcb"); buttons("Working...", "Keep working", true);
      await wait(900); status("24 lines, looking each one up");
      await rows([["already in your library", "23", true], ["needs a footprint", "1"]], 650);
      big("24 lines"); meter(1); status(""); note("TLC5951DAP: no footprint in your libraries."); buttons("Fill the library now", "Later");
      await wait(700);
      const p = D(".btns .p"); p.classList.add("hot"); await click(p);
    } else if (k === 1) {
      page("Fill the library"); big("Filling..."); meter("swing"); buttons("Working...", "Keep working", true);
      status("TLC5951DAP: KiCad library"); await rows([["KiCad library", "no match"]], 700);
      status("TLC5951DAP: EasyEDA"); await rows([["EasyEDA", "no footprint"]], 700);
      big("Held"); meter(null); status("");
      await rows([["symbol", "none yet"], ["footprint", "none yet"], ["release check", "held"]], 380);
      note("AI step: 1 part needs the datasheet read by AI (claude-opus-5-5): about $0.27, at most $0.60. Nothing is sent until you click.");
      buttons("Read with AI (about $0.27)", "Not now");
      await camOn(D(".notes"), 1.5, 0, -10);
      say("You see the model and the price first. Nothing is sent until you click.");
      await wait(1600);
      const p = D(".btns .p"); p.classList.add("hot"); await click(p);
    } else if (k === 2) {
      say("");
      page("Fill the library"); big("Reading..."); meter(0); buttons("Working...", "Keep working", true);
      await camTo(W / 2, H / 2, 1.42);
      await seconds("reading the TLC5951DAP datasheet", 3, 39, 3, 200);
      big("Ready"); meter(1); status("");
      await rows([["AI estimate", "about $0.27"], ["AI cost", "$0.24 (1 read)", true], ["symbol", "datasheet reader", true], ["footprint", "footprint generator", true], ["release check", "ready", true]], 420);
      status("Altium is busy building and installing the library"); meter("swing"); buttons("Installing...", "", true);
      await wait(1700); status("ecadlib is done"); meter(1); buttons("Done");
      await wait(600); await click(D(".btns .p")); await closeDlg();
    } else {
      $(".pn .list").insertAdjacentHTML("afterbegin", prow("TLC5951DAP", "24-ch LED driver, HTSSOP-38", 6, true));
      const r = $(".prow.new");
      await camOn(r, 1.55, -140, 80);
      await point(r);
      const g = document.createElement("div"); g.className = "wk-ghost"; g.textContent = "TLC5951DAP"; cam.appendChild(g);
      const b = box(r); g.style.transform = `translate(${b.x}px, ${b.y + 6}px)`; await wait(80);
      const dest = [this.bx + 91, this.by + 175];
      await camTo(560, 250, 1.22, 0);
      g.style.transform = `translate(${dest[0] - 60}px, ${dest[1] - 12}px)`; moveHand(dest[0], dest[1]); await wait(1100);
      g.style.opacity = "0"; $("#wk-sym").classList.add("on"); say("Its pins land on the wires already drawn.");
      await wait(400); g.remove(); await wait(2200);
      const design = $$(".mb > span")[5];
      await camOn(design, 1.8, 120, 40);
      await menuPick(design, ["Update Schematics in Board.PrjPcb", "Update PCB Document Board.PcbDoc", "-", "Rules...", "Board Shape ▸"], "Update PCB Document Board.PcbDoc");
      const tab = $('.dt [data-doc="pcb"]'); await click(tab);
      $('.dt [data-doc="sch"]').classList.remove("on"); tab.classList.add("on");
      const svg = $(".alt-sch"); svg.innerHTML = this.pcb(); svg.classList.add("wk-swap", "alt-pcb"); $(".layertabs").classList.add("on");
      $(".sb").innerHTML = "<span>X:4625mil Y:3020mil</span><span>Grid:5mil</span><span>(Hotspot Snap)</span><span class=\"sp\"></span><span>Panels</span>";
      moveHand(760, 470);
      await camTo(430, 255, 1.3, 600);
      $("#wk-pcb").classList.add("on"); say("The land pattern TI recommends, PowerPAD and all.");
      await wait(3200);
    }
  },
};

/* ================= KiCad ================= */
const KI = {
  chrome() {
    const top = ["doc", "open", "save", "|", "print", "|", "undo", "redo", "|", "find", "|", "zin", "zout", "fit", "|", "rot", "flip", "lock", "|", "net", "|", "drc", "|", "cube", "py"].map(tbi).join("");
    const left = ["grid", "mm", "cur", "rat", "|", "zone"].map(tbi).join(""), right = ["sel", "rat", "|", "fp", "route", "via", "zone", "|", "line", "arc", "rect", "circ", "text", "dim", "|", "del"].map(tbi).join("");
    return `<div class="wk-app kicad">
      <div class="tbar"><i class="ic"></i><span class="t">board — PCB Editor</span><span class="ctl"><b>—</b><b>☐</b><b>✕</b></span></div>
      <div class="mb">${["File", "Edit", "View", "Place", "Route", "Inspect", "Tools", "Preferences", "Help"].map((m) => `<span>${m}</span>`).join("")}</div>
      <div class="tl">${top}<span class="layer"><i></i>F.Cu (PgUp)</span><b class="tsep"></b><i class="ecbtn" title="ecadlib">${MARK}</i></div>
      <div class="mid"><div class="cv"><div class="vt">${left}</div><div class="board"><svg viewBox="0 0 730 508" class="ki-pcb">${this.board()}</svg></div><div class="vt">${right}</div></div>
        <div class="pn"><h4>Appearance</h4><div class="ptabs"><span class="on">Layers</span><span>Objects</span><span>Nets</span></div>
          <div class="lay">${[["F.Cu", "#c83434", 1], ["B.Cu", "#4d7fc4"], ["F.Paste", "#b4dbd2"], ["F.Silkscreen", "#f2eda1"], ["F.Mask", "#d864ff"], ["F.Courtyard", "#ff26e2"], ["F.Fab", "#afafaf"], ["Edge.Cuts", "#d0d2cd"], ["User.Drawings", "#c2c2c2"]].map(([n, c, on]) => `<div class="${on ? "on" : ""}"><i style="background:${c}"></i>${n}<span>◉</span></div>`).join("")}</div></div></div>
      <div class="sb"><span>Z 2.44</span><span>X 120.6500  Y 84.4500</span><span>dx 0.0000  dy 0.0000  dist 0.0000</span><span>grid X 0.2500  Y 0.2500</span><span class="sp"></span><span>mm</span></div>
    </div>`;
  },
  U: [365, 250], k: 16,
  board() {
    const k = this.k, U = this.U, M = [175, 250];
    const hole = (x, y) => `<circle class="thpad" cx="${x}" cy="${y}" r="9"/><circle class="hole" cx="${x}" cy="${y}" r="5.5"/>`;
    const m = (side, i) => lqfpPad(M[0], M[1], k, side, i);
    const J = (i) => [600, 196 + i * 40];
    const jh = [0, 1, 2, 3].map((i) => `<circle class="cu" cx="${J(i)[0]}" cy="${J(i)[1]}" r="10"/><circle class="hole" cx="${J(i)[0]}" cy="${J(i)[1]}" r="5"/>`).join("") + `<rect class="silk" x="582" y="176" width="36" height="160" rx="2"/><text class="ref" x="600" y="168" text-anchor="middle">J1</text>`;
    const rat = [[1, m("R", 0)], [2, m("R", 1)], [3, m("R", 2)], [4, m("R", 3)], [36, m("R", 6)]].map(([n, b]) => { const a = at(U[0], U[1], k, n); return `<path class="rat" d="M${a[0]} ${a[1]}L${b[0]} ${b[1]}"/>`; }).join("")
      + (() => { const a = at(U[0], U[1], k, 34); return `<path class="rat" d="M${a[0]} ${a[1]}L${505} ${386}"/>`; })()
      + (() => { const a = at(U[0], U[1], k, 35); return `<path class="rat" d="M${a[0]} ${a[1]}L${505} ${110}"/>`; })()
      + (() => { const a = at(U[0], U[1], k, 39); return `<path class="rat" d="M${a[0]} ${a[1]}L${J(3)[0]} ${J(3)[1]}"/>`; })();
    return `<rect class="edge" x="60" y="40" width="610" height="430" rx="14"/>${hole(84, 64)}${hole(646, 64)}${hole(84, 446)}${hole(646, 446)}
      ${lqfp(M[0], M[1], k)}<text class="ref" x="${M[0]}" y="${M[1] - 84}" text-anchor="middle">U1</text><text class="fabt" x="${M[0]}" y="${M[1] + 3}" text-anchor="middle">STM32G031K8T6</text>
      ${r0603(505, 386, k, "R1", true)}${r0603(505, 110, k, "C1", true)}${jh}
      <path class="trk" d="M${m("T", 1)[0]} ${m("T", 1)[1]}V96H440L470 66H${J(0)[0] - 30}L${J(0)[0]} ${J(0)[1] - 30}V${J(0)[1]}"/>
      <path class="trk" d="M${m("L", 0)[0]} ${m("L", 0)[1]}H96V430H${J(3)[0]}V${J(3)[1]}"/>
      <path class="btrk" d="M${m("B", 2)[0]} ${m("B", 2)[1]}V372H470V${J(1)[1] + 20}L${J(1)[0] - 20} ${J(1)[1]}H${J(1)[0]}"/><circle class="via" cx="${m("B", 2)[0]}" cy="372" r="5"/><circle class="vhole" cx="${m("B", 2)[0]}" cy="372" r="2.3"/>
      <g class="wk-new" id="wk-fp">${footprintSvg(U[0], U[1], k, "kicad")}${rat}</g>`;
  },
  steps: [
    ["Press the ecadlib button", "Added once with ecadlib kicad-install or the setup page, it sits on the PCB editor's toolbar.", 7],
    ["Type the part number", "Add part from MPN asks for the manufacturer part number, and the maker if you know it.", 8.5],
    ["See the cost, then say yes", "Free sources first. The datasheet read shows its model and price, and nothing is sent until you click.", 15],
    ["Placed on your board", "The footprint lands in the middle of the board with its ratsnest, and the project's library tables list the part.", 6.5],
  ],
  async play(k) {
    if (k === 0) {
      say(""); await wait(1200);
      const b = $(".ecbtn");
      await camOn(b, 2.1, -120, 40);
      await point(b); b.classList.add("hot"); await wait(300); await click(b); b.classList.remove("hot");
      openDlg("kicad", "ecadlib");
      D(".slot").innerHTML = `<div class="ask">What would you like to do?</div>` + [["Add part from MPN…", "look a part up and place its footprint"], ["Check project BOM", "which schematic parts the library already has"], ["Open review queue", "pick symbols and footprints for held parts"]].map(([a, h]) => `<span class="flat">${a}</span><small>${h}</small>`).join("");
      buttons("Close");
      await camTo(W / 2, H / 2, 1.38);
      await wait(700);
    } else if (k === 1) {
      const f = $$(".flat", dlg())[0]; f.classList.add("hot"); await click(f);
      page("Add part from MPN");
      D(".slot").innerHTML = `<label>Manufacturer part number</label><div class="in" id="wk-in1"></div><label>Manufacturer (optional)</label><div class="in" id="wk-in2"></div>`;
      buttons("OK", "Cancel");
      await camTo(W / 2, H / 2 - 20, 1.6);
      await point(D("#wk-in1")); await type(D("#wk-in1"), "TLC5951DAP");
      await point(D("#wk-in2")); await type(D("#wk-in2"), "Texas Instruments");
      await click(D(".btns .p"));
    } else if (k === 2) {
      page("Add part from MPN"); big("TLC5951DAP"); meter("swing"); buttons("Working…", "", true);
      await camTo(W / 2, H / 2, 1.42);
      status("looking up TLC5951DAP"); await rows([["KiCad library", "no match"], ["EasyEDA", "no footprint"]], 700);
      big("Held"); meter(1); status("");
      await rows([["symbol", "none yet"], ["footprint", "none yet"], ["release check", "held"]], 360);
      note("AI step: 1 part needs the datasheet read by AI (claude-opus-5-5): about $0.27, at most $0.60. Nothing is sent until you click.");
      buttons("Read with AI (about $0.27)", "Not now");
      await camOn(D(".notes"), 1.5, 0, -10);
      say("You see the model and the price first. Nothing is sent until you click.");
      await wait(1600);
      const p = D(".btns .p"); p.classList.add("hot"); await click(p);
      say("");
      page("Read with AI"); big("Reading…"); meter(0); buttons("Working…", "", true);
      await camTo(W / 2, H / 2, 1.42);
      await seconds("reading the TLC5951DAP datasheet", 3, 39, 3, 200);
      page("Read with AI"); big("Ready"); meter(1);
      await rows([["AI estimate", "about $0.27"], ["AI cost", "$0.24 (1 read)", true], ["symbol", "datasheet reader", true], ["footprint", "footprint generator", true], ["release check", "ready", true]], 380);
      note("Placed U2 (ecadlib:HTSSOP-38-1EP_6.1x12.5mm_P0.65mm) in the middle of the board.", true);
      note("The project's library tables now list the ecadlib library: reopen the project so the symbol chooser shows it.");
      buttons("Done", "Add another");
      await wait(1200);
    } else {
      await click(D(".btns .p")); await closeDlg();
      moveHand(this.U[0] + 150, this.U[1] + 190);
      await camTo(this.U[0] + 60, this.U[1] + 88, 1.38);
      $("#wk-fp").classList.add("on"); say("Placed in the middle of the board, with its ratsnest.");
      await wait(3600);
    }
  },
};

const SCENES = { altium: ALT, kicad: KI };

/* ---------------- running it ---------------- */
function setScene(name) {
  const sc = SCENES[name];
  S.scene = name;
  $$(".wk-top [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.scene === name)));
  $$(".wk-app, .wk-menu, .wk-ghost, .wk-rip").forEach((e) => e.remove());
  cam.insertAdjacentHTML("afterbegin", sc.chrome());
  const d = dlg(); d.hidden = true; d.className = "wk-dlg";
  cam.style.transition = "none"; cam.style.transform = "none"; S.camS = 1; void cam.offsetWidth; cam.style.transition = "";
  hand.style.transition = "none"; moveHand(720, 520); void hand.offsetWidth; hand.style.transition = "";
  say("");
  win.setAttribute("aria-label", name === "altium" ? "Altium Designer: checking the project BOM, approving the datasheet read, and placing TLC5951DAP" : "KiCad: adding TLC5951DAP from the ecadlib button and placing its footprint");
  stepsEl.innerHTML = sc.steps.map(([h, p, d], k) => `<li data-k="${k}" style="--d:${d}s"><div class="bar"><i></i></div><p class="n">${String(k + 1).padStart(2, "0")}</p><h3>${h}</h3><p>${p}</p></li>`).join("");
}
function markStep(k) { $$("li", stepsEl).forEach((li, i) => { li.classList.toggle("on", i === k); li.classList.toggle("done", i < k); }); }
async function runScene(name, from = 0) {
  const run = ++S.run;
  setScene(name);
  try {
    if (from > 0) { S.fast = true; root.classList.add("wk-fast"); for (let k = 0; k < from; k++) await SCENES[name].play(k); S.fast = false; root.classList.remove("wk-fast"); }
    for (let k = from; k < 4; k++) { markStep(k); await SCENES[name].play(k); await wait(400); }
    markStep(4); say(""); await camWide(); await wait(2200);
    if (run === S.run) runScene(name === "altium" ? "kicad" : "altium");
  } catch (e) { S.fast = false; root.classList.remove("wk-fast"); if (!(e instanceof Stop)) throw e; }
}
async function stillScene(name) {
  ++S.run; setScene(name); S.fast = true; root.classList.add("wk-fast");
  for (let k = 0; k < 4; k++) await SCENES[name].play(k);
  S.fast = false; say(""); cam.style.transform = "none"; markStep(4);
}
$$(".wk-top [role=tab]").forEach((b) => b.addEventListener("click", () => (still.matches ? stillScene(b.dataset.scene) : runScene(b.dataset.scene))));
stepsEl.addEventListener("click", (e) => { const li = e.target.closest("li"); if (li && !still.matches) runScene(S.scene, +li.dataset.k); });
playBtn.addEventListener("click", () => { S.paused = !S.paused; root.classList.toggle("paused", S.paused); playBtn.textContent = S.paused ? "play" : "pause"; playBtn.setAttribute("aria-pressed", String(!S.paused)); });
if (still.matches) { playBtn.hidden = true; root.classList.add("in", "seen"); stillScene("altium"); }
else {
  setScene("altium");
  new IntersectionObserver(([e]) => {
    S.visible = e.isIntersecting; root.classList.toggle("seen", S.visible);
    if (S.visible) root.classList.add("in");
    if (S.visible && !S.started) { S.started = true; setTimeout(() => runScene("altium"), 700); }
  }, { threshold: 0.35 }).observe(fitBox);
}
