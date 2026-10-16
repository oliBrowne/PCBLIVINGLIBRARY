/**
 * Board: a small PCB with its parts hovering over their land patterns, the way
 * a pick-and-place head holds them before it lets go. The pointer is put on
 * the board, which never moves; each part sinks on its own spring as the
 * pointer comes near its footprint, and seats its leads on the pads inside
 * NEAR. At rest the parts hang at uneven heights with dashed drops to their
 * pads, and U1's pin-1 dot is bright. The slider is the reach, in board units.
 * TURN.deg turns the camera (the landing page drives it from scrolling), and
 * TURN.intro drops the parts in one by one, as a placement run would.
 */
const {
  Cam, clamp, circ, rrect, facing, fit, open, poly, prism, proj, rad, ringAt, rings, seg, unproj, spring, stepS,
  flatDot, mk, place, pointer, put, register, disposer, solid,
} = HL;

const BW = 76, BD = 54, BT = 4, NEAR = 7, LT = 1.2;
const TURN = { deg: 45, wake: new Set(), intro: new Set() };

/** A part: ref, centre, rest lift, body [half x, half y, height, radius], pads [cx, cy, w, d], leads [cx, cy, w, d] and its marks. */
const sq = (n, p, off, w, d) => {
  const out = [];
  for (let i = 0; i < n; i++) {
    const t = (i - (n - 1) / 2) * p;
    out.push([t, off, w, d], [t, -off, w, d], [off, t, d, w], [-off, t, d, w]);
  }
  return out;
};
const row = (n, p, off, w, d) => [-1, 1].flatMap((s) => Array.from({ length: n }, (_, i) => [(i - (n - 1) / 2) * p, s * off, w, d]));
const PARTS = [
  { ref: "U1", x: 0, y: -4, rest: 30, body: [12, 12, 3.5, 1.6], pads: sq(4, 5, 13.5, 2.6, 5).concat([[0, 0, 11, 11]]), leads: [], pin1: [-7.5, 7.5] },
  { ref: "U2", x: 44, y: -24, rest: 38, body: [14, 9.5, 7, 2], pads: row(4, 7, 15.5, 3.8, 7), leads: row(4, 7, 13, 2.4, 6), pin1: [-9.5, 4.5] },
  { ref: "J1", x: -30, y: 46, rest: 10, body: [15, 9, 9, 4.5], pads: row(6, 4.4, -5, 2.6, 7).filter((p) => p[1] < 0).concat([[-17, 2, 3, 8], [17, 2, 3, 8]]), leads: [], port: true },
  { ref: "Y1", x: 46, y: 28, rest: 14, body: [12, 5, 5, 5], pads: [[-11, 0, 6, 7], [11, 0, 6, 7]], leads: [] },
  { ref: "C1", x: -28, y: -32, rest: 54, body: [5, 2.8, 3.5, 0.8], pads: [[-5.5, 0, 4, 6], [5.5, 0, 4, 6]], leads: [], caps: 3 },
  { ref: "R1", x: -8, y: 28, rest: 18, body: [5, 2.8, 3, 0.8], pads: [[-5.5, 0, 4, 6], [5.5, 0, 4, 6]], leads: [], caps: 3 },
  { ref: "Q1", x: -54, y: -6, rest: 42, body: [6.5, 4, 4, 1], pads: [[-4.5, 7.5, 3.5, 5], [4.5, 7.5, 3.5, 5], [0, -7.5, 3.5, 5]], leads: [[-4.5, 6, 2, 4], [4.5, 6, 2, 4], [0, -6, 2, 4]] },
  { ref: "J2", x: -62, y: 26, rest: 26, body: [3.5, 16, 6, 1], pads: [-12, -4, 4, 12].map((y) => [0, y, 5, 5]), leads: [], posts: [-12, -4, 4, 12] },
  { ref: "C2", x: -20, y: 16, rest: 34, body: [3.5, 2, 3, 0.6], pads: [[-4, 0, 3, 4], [4, 0, 3, 4]], leads: [], caps: 2 },
  { ref: "C3", x: 10, y: -32, rest: 46, body: [3.5, 2, 3, 0.6], pads: [[-4, 0, 3, 4], [4, 0, 3, 4]], leads: [], caps: 2 },
  { ref: "D1", x: 60, y: 44, rest: 20, body: [4.5, 2.6, 2.5, 0.6], pads: [[-5, 0, 3.5, 4.5], [5, 0, 3.5, 4.5]], leads: [], band: -2.5 },
];
const FIDS = [[-46, -46], [24, 46]];
const TRACES = [
  [[-16.5, -4], [-22, -4], [-28, -10], [-28, -26]], [[0, 9.5], [0, 18], [-2.5, 20.5], [-2.5, 28]],
  [[16.5, -11.5], [24, -11.5], [33.5, -21], [33.5, -8.5]], [[16.5, 3.5], [24, 3.5], [35, 14.5], [35, 28]],
  [[-54, 1.5], [-54, 12], [-44, 22], [-44, 36]], [[-30, 41], [-30, 34], [-16.5, 20.5], [-16.5, 1]],
];

function mount({ stage, svg, read }, value) {
  const bag = disposer();
  let reach = value, over = null;
  const C = Cam(TURN.deg, 0.5, 1.72), az = spring(TURN.deg, { eps: 0.05 });
  const g = mk("g", {}, svg);
  const board = solid(g), traces = mk("path", { class: "nf lo" }, g), holes = mk("path", { class: "nf lo" }, g), silk = mk("path", { class: "nf lo" }, g);
  const fids = FIDS.map(() => flatDot(g, C, 1.1, "dot off"));
  const vias = TRACES.map(() => flatDot(g, C, 1.3, "dot off"));
  const [br, bi] = rings(-BW, -BD, BW, BD, 8, 2);
  const parts = PARTS.map((d) => {
    const pads = d.pads.map(([x, y, w, h]) => ({ rr: rings(d.x + x - w / 2, d.y + y - h / 2, d.x + x + w / 2, d.y + y + h / 2, 0.8, 0.4), s: solid(g) }));
    return { d, pads, sp: spring(d.rest, { eps: 0.03 }) };
  });
  const guides = mk("path", { class: "dash lo nf" }, g);
  parts.forEach((p) => {
    const { d } = p, [hx, hy, , r] = d.body;
    p.g = mk("g", {}, g);
    p.leads = d.leads.map(([x, y, w, h]) => ({ y, rr: rings(d.x + x - w / 2, d.y + y - h / 2, d.x + x + w / 2, d.y + y + h / 2, 0.6, 0.3), s: null }));
    p.leads.filter((l) => l.y < 0).forEach((l) => { l.s = solid(p.g); });
    p.rr = rings(d.x - hx, d.y - hy, d.x + hx, d.y + hy, r, Math.min(1.4, hy * 0.35));
    p.body = solid(p.g);
    p.leads.filter((l) => l.y >= 0).forEach((l) => { l.s = solid(p.g); });
    p.posts = (d.posts || []).map((y) => ({ rr: rings(d.x - 1, d.y + y - 1, d.x + 1, d.y + y + 1, 0.4, 0.3), s: solid(p.g) }));
    const xs = d.pads.flatMap(([x, , w]) => [x - w / 2, x + w / 2]).concat([-hx, hx]), ys = d.pads.flatMap(([, y, , h]) => [y - h / 2, y + h / 2]).concat([-d.body[1], d.body[1]]);
    p.silk = [d.x + Math.min(...xs) - 2, d.y + Math.min(...ys) - 2, d.x + Math.max(...xs) + 2, d.y + Math.max(...ys) + 2];
    p.mark = mk("path", { class: "nf lo" }, p.g);
    if (d.pin1) p.dot = flatDot(p.g, C, d.ref === "U1" ? 1.4 : 1, d.ref === "U1" ? "dot" : "dot m");
  });
  const U1 = parts[0];

  let P, front, key = "";
  function draw() {
    C.az = rad(az.x);
    fit(C, [[-BW, -BD, -BT], [BW, BD, -BT], [BW, -BD, -BT], [-BW, BD, -BT], [0, 0, 62]], 200, 160);
    P = proj(C); front = facing(C);
    put(board, prism(P, front, br, bi, -BT, 0));
    traces.setAttribute("d", TRACES.map((t) => open(t.map(([x, y]) => P(x, y, 0)))).join(""));
    holes.setAttribute("d", [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) => poly(ringAt(P, circ(3.2, 24).map((q) => ({ ...q, u: q.u + sx * (BW - 8), v: q.v + sy * (BD - 8) })), 0))).join(""));
    TRACES.forEach((t, i) => place(vias[i], P(...t[t.length - 1], 0)));
    FIDS.forEach((f, i) => place(fids[i], P(f[0], f[1], 0)));
    silk.setAttribute("d", parts.map(({ silk: [a, b, e, f] }) => poly([P(a, b, 0), P(e, b, 0), P(e, f, 0), P(a, f, 0)])).join("")
      + FIDS.map(([x, y]) => poly(ringAt(P, circ(3, 20).map((q) => ({ ...q, u: q.u + x, v: q.v + y })), 0))).join(""));
    for (const p of parts) for (const pd of p.pads) put(pd.s, prism(P, front, pd.rr[0], pd.rr[1], 0, 0.8));
    const s = Math.sin(C.az), c = Math.cos(C.az), order = parts.slice().sort((a, b) => a.d.x * s + a.d.y * c - (b.d.x * s + b.d.y * c));
    const k = order.map((p) => p.d.ref).join();
    if (k !== key) { key = k; order.forEach((p) => { if (!(p.wait > 0)) g.appendChild(p.g); }); }
    const drops = [];
    for (const p of parts) {
      const { d } = p, h = Math.max(0, p.sp.x), z = 0.8 + h, [, , bh] = d.body;
      for (const l of p.leads) put(l.s, prism(P, front, l.rr[0], l.rr[1], z, z + LT));
      for (const q of p.posts) put(q.s, prism(P, front, q.rr[0], q.rr[1], z, z + bh + 5));
      put(p.body, prism(P, front, p.rr[0], p.rr[1], z + (p.leads.length ? 0.6 : 0), z + bh));
      const top = z + bh, [hx, hy] = d.body;
      let m = "";
      if (d.caps) m = [-1, 1].map((sx) => seg(P(d.x + sx * (hx - d.caps), d.y - hy, top), P(d.x + sx * (hx - d.caps), d.y + hy, top))).join("");
      if (d.band) m = seg(P(d.x + d.band, d.y - hy, top), P(d.x + d.band, d.y + hy, top));
      if (d.port) m = poly(rrect(-9, 2.2, 9, 6.8, 2.3, 4).map((q) => P(d.x + q.u, d.y + hy, z + q.v)));
      p.mark.setAttribute("d", m);
      if (p.dot) place(p.dot, P(d.x + d.pin1[0], d.y + d.pin1[1], top));
      if (h > 1.5 && !(p.wait > 0)) for (const [x, y] of d.pads.slice(0, 4)) drops.push(seg(P(d.x + x, d.y + y, 0.8), P(d.x + x, d.y + y, z)));
    }
    guides.setAttribute("d", drops.join(""));
  }

  function retarget() {
    let best = null, bd = 1e9;
    for (const p of parts) {
      const dist = over ? Math.hypot(over[0] - p.d.x, over[1] - p.d.y) : 1e9;
      p.sp.t = p.d.rest * clamp((dist - NEAR) / reach, 0, 1);
      if (dist < bd) { bd = dist; best = p; }
    }
    const on = over && bd < reach ? best : null;
    parts.forEach((p) => p.body.sil.classList.toggle("hi", p === on));
    U1.dot.setAttribute("class", on ? "dot m" : "dot");
    read.textContent = !on ? "rest" : on.sp.t < 0.5 ? `${on.d.ref} seated` : `${on.d.ref} · ${on.sp.t.toFixed(0)}`;
    loop.wake();
  }

  const loop = register(stage, (dt) => {
    let m = false;
    az.t = TURN.deg;
    if (stepS(az, dt)) m = true;
    for (const p of parts) {
      if (p.wait > 0 && (p.wait -= dt) <= 0) key = "";
      if (p.wait > 0) m = true;
      else if (stepS(p.sp, dt)) m = true;
    }
    draw();
    return m;
  });
  const turned = () => loop.wake();
  const intro = () => {
    parts.forEach((p, i) => { p.wait = 0.9 + i * 0.22; p.sp.x = p.d.rest + 44; p.sp.v = 0; p.g.remove(); });
    key = ""; loop.wake();
  };
  TURN.wake.add(turned); TURN.intro.add(intro);
  bag.add(() => { TURN.wake.delete(turned); TURN.intro.delete(intro); });
  bag.add(loop.unregister);
  draw();
  retarget();

  bag.add(pointer(stage, {
    move: (p) => { over = unproj(C, p[0], p[1], 0); retarget(); },
    leave: () => { over = null; retarget(); },
  }));
  bag.add(() => svg.replaceChildren());
  return { set: (v) => { reach = v; retarget(); }, destroy: bag.dispose };
}

hairline({
  name: "board",
  means: "Parts hover over their pads on a small board; bring the pointer near one and it seats its leads, its neighbours sinking after it.",
  rules: [1, 3, 5, 6],
  range: [24, 44, 70],
  tour: [[205, 161], [108, 173], [283, 176], null],
  mount,
});
