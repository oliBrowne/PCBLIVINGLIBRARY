/**
 * Seat: an SOIC-8 chip hovering over its land pattern on a board. The pointer
 * is put on the board, which never moves; the nearer it is to the footprint,
 * the lower the chip sits, on one spring, until its leads land on their pads.
 * At rest the chip floats above its pads, with dashed drops from
 * each lead's tip to its pad, and pin 1's pad is bright; seated, pin 1's lead is. The slider is the lift.
 *
 * The pattern: scrub. A spring for the lift, read from the pointer's distance
 * to the footprint's centre on the ground plane, never from the chip on screen.
 */
const {
  Cam, clamp, facing, fit, prism, proj, rings, unproj, spring, stepS, seg,
  flatDot, mk, place, pointer, put, register, disposer, solid,
} = HL;

const PITCH = 12.7, ROW = 27, PW = 6, PL = 14, PT = 1;   // pad pitch, pad row offset, pad width and length, pad thickness
const BX = 24.5, BY = 19.5, BH = 15, LW = 4.4, LT = 2.4;  // body half sizes and height, lead width and thickness
const TIP = 31, NEAR = 26, FAR = 120;                      // lead tip offset; the pointer seats the chip inside NEAR, lifts it beyond FAR
const XS = [-1.5, -0.5, 0.5, 1.5].map((k) => k * PITCH);

function mount({ stage, svg, read }, value) {
  const bag = disposer();
  let lift = value;
  const C = Cam(45, 0.5, 1.95);
  fit(C, [[-64, -50, -5], [64, 50, -5], [64, -50, -5], [-64, 50, -5], [0, 0, 40 + BH + 6]], 200, 166);
  const P = proj(C), front = facing(C);
  const g = mk("g", {}, svg);

  const [br, bi] = rings(-64, -50, 64, 50, 9, 2);
  put(solid(g), prism(P, front, br, bi, -5, 0));

  // Pads, then their guides, painted far to near; pin 1 is the near row's first pad.
  const pads = [];
  for (const side of [-1, 1]) for (const x of XS) pads.push({ x, y: side * ROW, side });
  pads.sort((a, b) => a.x + a.y - (b.x + b.y));
  const pin1 = pads.find((p) => p.side > 0 && p.x === XS[0]);
  const guides = mk("path", { class: "dash lo nf" }, g);
  for (const p of pads) {
    const [r, i] = rings(p.x - PW / 2, p.y - PL / 2, p.x + PW / 2, p.y + PL / 2, 1.2, 0.6);
    const s = solid(g);
    put(s, prism(P, front, r, i, 0, PT));
    if (p === pin1) pin1.s = s;
  }

  // The chip: far leads, body, near leads. Each lead is a flat foot from the body's side to its tip.
  const chip = mk("g", {}, g);
  const lead = (x, side) => {
    const y0 = side < 0 ? -TIP : BY - 2, y1 = side < 0 ? -BY + 2 : TIP;
    return { x, side, rr: rings(x - LW / 2, y0, x + LW / 2, y1, 0.9, 0.5), s: null };
  };
  const far = XS.map((x) => lead(x, -1)), near = XS.map((x) => lead(x, 1));
  far.forEach((l) => { l.s = solid(chip); });
  const [kr, ki] = rings(-BX, -BY, BX, BY, 3.4, 1.6);
  const body = solid(chip);
  const dot = flatDot(chip, C, 2.2, "dot m");
  near.forEach((l) => { l.s = solid(chip); });
  const leads = far.concat(near);

  const sp = spring(lift * 0.8, { eps: 0.02 });
  let drawn = NaN;
  function draw() {
    const h = Math.max(0, sp.x);
    if (h === drawn) return;
    drawn = h;
    const z = () => PT + h;
    for (const l of leads) put(l.s, prism(P, front, l.rr[0], l.rr[1], z(), z() + LT));
    put(body, prism(P, front, kr, ki, z() + 0.5, z() + BH));
    place(dot, P(XS[0] + 3, BY - 6, z() + BH));
    const seated = h < 1.5;                             // seated, the bright moves from pin 1's pad to pin 1's lead
    pin1.s.sil.classList.toggle("hi", !seated);
    near[0].s.sil.classList.toggle("hi", seated);
    guides.setAttribute("d", h < 1.5 ? "" : leads.map((l) => seg(P(l.x, l.side * (TIP - 3), PT), P(l.x, l.side * (TIP - 3), z()))).join(""));
  }

  const loop = register(stage, (dt) => { const m = stepS(sp, dt); draw(); return m; });
  bag.add(loop.unregister);
  draw();

  let over = null;
  function retarget() {
    if (!over) { sp.t = lift * 0.8; read.textContent = "rest"; }
    else {
      const d = Math.hypot(over[0], over[1] * 1.25);
      const u = clamp((d - NEAR) / (FAR - NEAR), 0, 1);
      sp.t = lift * u;
      read.textContent = u === 0 ? "seated" : `lift ${(lift * u).toFixed(1)}`;
    }
    loop.wake();
  }
  retarget();

  bag.add(pointer(stage, {
    move: (p) => { over = unproj(C, p[0], p[1], 0); retarget(); },
    leave: () => { over = null; retarget(); },
  }));
  bag.add(() => svg.replaceChildren());

  return {
    set: (v) => { lift = v; drawn = NaN; retarget(); },
    destroy: bag.dispose,
  };
}

hairline({
  name: "seat",
  means: "A chip floats over its footprint; bring the pointer in and it seats its leads on the pads.",
  rules: [1, 5, 6, 9],
  range: [18, 30, 40],
  tour: [[110, 120], [200, 150], [300, 200], null],
  mount,
});
