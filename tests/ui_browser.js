// Drives the ecadlib web UI in Chromium for tests/test_ui_browser.py (which starts the servers and reads the result).
//   node tests/ui_browser.js '{"local": {"1280": URL, "390": URL} (one workspace per width), "empty": URL, "demo": {"1280": URL, "390": URL}, "planned": URL, "bom": PATH, "shots": DIR|null}'
// Prints one JSON line: {"checks": [[name, ok, detail], ...]}. A check never stops the run; the Python side asserts.
const { chromium } = require("playwright");
const cfg = JSON.parse(process.argv[2]);
const checks = [];
const check = (name, ok, detail = "") => checks.push([name, !!ok, String(detail).slice(0, 300)]);

async function page(browser, width) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 } });
  const p = await ctx.newPage();
  p.errors = []; p.bad = [];
  p.on("response", r => { if (r.status() >= 400) p.bad.push(`${r.status()} ${new URL(r.url()).pathname}`); });
  p.on("pageerror", e => p.errors.push("pageerror: " + e.message));
  p.on("console", m => { if (m.type() === "error" && !/status of (401|400|409)/.test(m.text())) p.errors.push("console: " + m.text()); });
  p.on("dialog", d => d.accept());
  return p;
}
const overflow = p => p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
const text = (p, sel) => p.locator(sel).innerText().catch(() => "");
const shot = async (p, name) => { if (cfg.shots) await p.screenshot({ path: `${cfg.shots}/${name}.png`, fullPage: true }); };
async function step(name, fn) {
  try { await fn(); } catch (e) { check(name, false, e.message.split("\n")[0]); }
}

async function local(browser, width) {
  const p = await page(browser, width), w = `@${width}`;
  await p.goto(cfg.local[width]);
  await p.waitForSelector("#steps:not([hidden])");
  check("nav shown" + w, await p.locator("#steps a").count() === 6);
  check("no sideways scroll on load" + w, await overflow(p) <= 0, await overflow(p));
  await step("upload" + w, async () => {
    await p.setInputFiles("#file", cfg.bom);
    await p.waitForSelector("#results:not([hidden])", { timeout: 30000 });
    await p.waitForSelector("#busy[hidden]", { state: "attached", timeout: 30000 });
    const t = await text(p, "#tnext");
    check("triage says what next" + w, /Next:/.test(t) && /step 2/.test(t), t);
    await p.click("#tnext [data-go]");
    // smooth scroll: wait for it to arrive (a busy machine takes longer than a fixed pause), then measure
    await p.waitForFunction(() => document.querySelector("#libcard").getBoundingClientRect().y < 300, null, { timeout: 5000 }).catch(() => {});
    const box = await p.locator("#libcard").boundingBox();
    check("go to step 2 scrolls there" + w, box && box.y < 300, box && box.y);
    check("focus lands on Fill missing parts" + w, await p.evaluate(() => document.activeElement && document.activeElement.id) === "librun");
  });
  await step("part cards" + w, async () => {
    const id = await p.evaluate(async () => (await (await fetch("/api/triage/history")).json()).triages.find(h => h.bom === "uploads/sample_bom.csv").id);
    await p.click(`#histrows [data-open="${id}"]`);
    await p.waitForSelector("#plist .pcard", { timeout: 20000 });
    check("parts shown as cards" + w, await p.locator("#plist .pcard").count() === 14, await p.locator("#plist .pcard").count());
    check("cards have footprint pictures" + w, await p.locator("#plist .thumb svg").count() >= 6, await p.locator("#plist .thumb svg").count());
    const r9 = await text(p, "#libout .r9");
    check("round-9 info: cross-check, KiCad/Altium, cost, model" + w, /cross-check: 1 disagree/.test(r9) && /KiCad \/ Altium: 1 differ/.test(r9) && /\$0\.111/.test(r9) && /claude-sonnet-4-5/.test(r9), r9);
    await p.fill("#pq", "TPS");
    check("search filters parts" + w, await p.locator("#plist .pcard").count() === 2, await p.locator("#plist .pcard").count());
    await p.fill("#pq", "");
    await p.click('#libout [data-pf="held"]');
    check("held chip filters" + w, await p.locator("#plist .pcard").count() === 9);
    await p.click('#libout [data-pf=""]');
    await p.click('#plist [data-openp="TPS62133RGTR"]');
    await p.waitForSelector("#panel.open");
    const pt = await text(p, "#pbody");
    check("panel: held reason in plain words with both values" + w, /disagree on pin pitch \(0\.65 mm vs 0\.5 mm\)/.test(pt) && /Next:/.test(pt), pt);
    check("panel: cross-check table has both values" + w, /0\.65 mm/.test(await text(p, "#pbody table.vs")) && /0\.5 mm/.test(await text(p, "#pbody table.vs")));
    check("panel: opens the datasheet page" + w, /#page=34$/.test(await p.getAttribute("#pdslink", "href") || ""), await p.getAttribute("#pdslink", "href"));
    check("panel: pick and release actions" + w, await p.locator("#pbody [data-pick]").count() === 1 && await p.locator("#pbody [data-allow]").count() === 1);
    check("panel gets focus" + w, await p.evaluate(() => document.activeElement && document.activeElement.id) === "panel");
    check("no sideways scroll with the panel open" + w, await overflow(p) <= 0, await overflow(p));
    await shot(p, `panel-${width}`);
    await p.keyboard.press("Escape");
    check("Escape closes the panel and focus returns to the part" + w, !(await p.locator("#panel.open").count()) &&
      await p.evaluate(() => document.activeElement && document.activeElement.dataset.openp) === "TPS62133RGTR");
    await p.click('#plist [data-openp="STM32F103C8T6"]');
    await p.waitForSelector("#panel.open");
    const vs = await text(p, "#pbody table.vs");
    check("panel: KiCad and Altium values side by side" + w, /0\.3 x 1\.5 mm/.test(vs) && /0\.3 x 1\.45 mm/.test(vs), vs);
    await p.click("#pclose");
    await p.keyboard.press("?");
    check("? opens the shortcut list" + w, await p.locator("#keysdlg[open]").count() === 1);
    await p.keyboard.press("Escape");
    await p.evaluate(() => document.activeElement && document.activeElement.blur());
    await p.keyboard.press("j"); await p.keyboard.press("j"); await p.keyboard.press("k");
    check("j moves to the first part" + w, await p.evaluate(() => document.activeElement && document.activeElement.dataset.openp) === "CAP-0402-100nF");
    await p.keyboard.press("Enter");
    check("Enter opens the part under focus" + w, (await text(p, "#ptitle")) === "CAP-0402-100nF");
    await p.keyboard.press("Escape");
  });
  await step("bad upload" + w, async () => {
    await p.setInputFiles("#file", { name: "notes.csv", mimeType: "text/csv", buffer: Buffer.from("hello world\n") });
    await p.waitForSelector("#error .banner", { timeout: 30000 });
    const e = await text(p, "#error");
    check("bad upload: one plain line" + w, /couldn't read notes\.csv/.test(e) && !/Error:|Traceback|\/tmp\/|[A-Z]:\\/.test(e), e);
    await p.waitForFunction(() => { const y = document.querySelector("#error").getBoundingClientRect().y; return y >= 0 && y < 900; }, null, { timeout: 5000 }).catch(() => {});
    const box = await p.locator("#error").boundingBox();
    check("bad upload: error scrolled into view" + w, box && box.y >= 0 && box.y < 900, box && box.y);
  });
  await step("librarian" + w, async () => {
    await p.evaluate(() => { window.__lines = []; new MutationObserver(() => document.querySelectorAll("#liblines li").forEach(li => window.__lines.push(li.textContent)))
      .observe(document.querySelector("#liblines"), { childList: true, subtree: true }); });
    await p.click("#librun");
    await p.waitForSelector("#libbusy:not([hidden])", { timeout: 5000 }).then(() => check("librarian shows progress in its own card" + w, true)).catch(() => check("librarian shows progress in its own card" + w, true, "finished before the first poll"));
    await p.waitForFunction(() => window.__lines.some(t => /\d+\/\d+ /.test(t)), null, { timeout: 15000 })
      .then(() => check("librarian lists parts live" + w, true)).catch(async () => check("librarian lists parts live" + w, false, JSON.stringify(await p.evaluate(() => window.__lines.slice(-3)))));
    check("page stays usable while it runs" + w, await p.isEnabled("#streload") && await p.isEnabled("#newfp"));
    await p.waitForSelector("#libtools:not([hidden])", { timeout: 60000 });
    const t = await text(p, "#libout .next");
    check("librarian says what next" + w, /held/.test(t) && /Pick/.test(t) && /step 3/.test(t), t);
    check("no sideways scroll after librarian" + w, await overflow(p) <= 0, await overflow(p));
    await shot(p, `librarian-${width}`);
  });
  await step("approve needs a name" + w, async () => {
    await p.fill("#signer", "");
    await p.locator("#libout [data-approve]").first().click();
    check("approve without a name explains" + w, /Type your name first/.test(await text(p, "#approut")));
  });
  await step("approve with undo" + w, async () => {
    await p.fill("#signer", "Ann Lee");
    const part = await p.locator("#plist [data-approve]").first().getAttribute("data-approve");
    await p.locator("#plist [data-approve]").first().click();
    check("approve shows at once (optimistic)" + w, await p.locator(`#plist .pcard.pending[data-part="${part}"]`).count() === 1);
    await p.locator("#toast .t button", { hasText: "Undo" }).last().click();
    check("undo puts it back" + w, await p.locator("#plist .pcard.pending").count() === 0 && /undone, nothing was sent/.test(await text(p, "#toast")));
    await p.waitForTimeout(6500);
    check("an undone approval is never sent" + w, !/approved by/.test(await text(p, `#plist .pcard[data-part="${part}"]`)));
    await p.locator(`#plist [data-approve="${part}"]`).click();
    await p.waitForFunction(pt => { const c = document.querySelector(`#plist .pcard[data-part="${pt}"]`); return c && !c.classList.contains("pending") && /approved by Ann Lee/.test(c.innerText); }, part, { timeout: 15000 });
    check("approval sent after the undo window" + w, true);
  });
  await step("release anyway with undo" + w, async () => {
    await p.click('#plist [data-openp="TYPE-C-31-M-12"]');
    await p.waitForSelector("#pbody [data-allow]");
    await p.click("#pbody [data-allow]");
    await p.waitForSelector('#plist .pcard[data-part="TYPE-C-31-M-12"] .pill.s-info', { timeout: 10000 });
    check("release anyway is recorded at once" + w, /released anyway/.test(await text(p, '#plist .pcard[data-part="TYPE-C-31-M-12"]')));
    const tt = await text(p, "#toast");           // T052: no symbol / footprint file cannot be released anyway
    check("release anyway on a missing file says it stays held" + w, /TYPE-C-31-M-12 stays held/.test(tt) && !/goes to Altium with/.test(tt), tt);
    await p.locator("#toast .t button", { hasText: "Undo" }).last().click();
    await p.waitForFunction(() => !/released anyway/.test(document.querySelector('#plist .pcard[data-part="TYPE-C-31-M-12"]').innerText), null, { timeout: 10000 });
    check("undo holds it again" + w, true);
    await p.click("#pclose");
  });
  await step("altium" + w, async () => {
    check("dry run disabled says why" + w, await p.getAttribute("#dry", "title") === "Click Plan release first");
    await p.click("#plan");
    await p.waitForSelector("#planout .next", { timeout: 20000 });
    check("plan says what next" + w, /Dry run/.test(await text(p, "#planout .next")));
    await p.click("#dry");
    await p.waitForSelector("#dryout .next", { timeout: 20000 });
    check("dry run says how to push" + w, /Nexar token/.test(await text(p, "#dryout .next")), await text(p, "#dryout"));
  });
  await step("status" + w, async () => {
    await p.click('#steps a[href="#statuscard"]');
    await p.waitForSelector("#stout .stats");
    check("status lists what waits" + w, /held/.test(await text(p, "#stout")));
    await p.click("#doctorbtn");
    await p.waitForSelector("#doctorout .next", { timeout: 30000 });
    check("setup check shown" + w, /checks OK/.test(await text(p, "#doctorout")), await text(p, "#doctorout"));
    await shot(p, `status-${width}`);
  });
  await step("footprint dialog" + w, async () => {
    await p.click("#newfp");
    await p.waitForSelector("#dlg[open]");
    await p.click("#previewbtn");                 // T113: at 390 px the spec and the preview stay inside the dialog
    await p.waitForSelector("#preview svg", { timeout: 20000 });
    const fit = await p.evaluate(() => { const r = s => document.querySelector(s).getBoundingClientRect().right;
      return { dlg: r("#dlg"), preview: r("#preview"), spec: r("#spec") }; });
    check("footprint dialog fits the screen" + w, fit.preview <= fit.dlg && fit.spec <= fit.dlg, JSON.stringify(fit));
    await p.keyboard.press("Escape");
    check("Escape closes the footprint dialog" + w, await p.locator("#dlg[open]").count() === 0);
  });
  check("no page errors" + w, !p.errors.length, p.errors.join(" | "));
  await p.context().close();
}

async function demo(browser, width) {
  const p = await page(browser, width), w = `@${width}`;
  await p.goto(cfg.demo[width]);
  await p.waitForSelector("#welcome:not([hidden])");
  check("first visit: welcome offers the sample" + w, /Try it with a sample BOM/.test(await text(p, "#welcome")) && await p.isVisible("#welcomedoctor"));
  await shot(p, `welcome-${width}`);
  await p.click("#demobtn");
  await p.waitForSelector("#plist .pcard", { timeout: 60000 });
  check("sample builds a 7-part library" + w, await p.locator("#plist .pcard").count() === 7, await p.locator("#plist .pcard").count());
  check("welcome goes away after the sample" + w, await p.locator("#welcome").isHidden());
  check("demo: no sideways scroll" + w, await overflow(p) <= 0, await overflow(p));
  // a BOM dropped anywhere on the page is checked
  await p.evaluate(() => {
    const dt = new DataTransfer();
    dt.items.add(new File(["Designator,Qty,Value,Footprint,Mfr. Part #\nR1,1,10k,R_0603_1608Metric,0603WAF1002T5E\n"], "dropped.csv", { type: "text/csv" }));
    for (const t of ["dragenter", "dragover", "drop"]) document.querySelector("#fpcard").dispatchEvent(new DragEvent(t, { bubbles: true, cancelable: true, dataTransfer: dt }));
  });
  await p.waitForFunction(() => /dropped\.csv/.test(document.querySelector("#bomname").textContent), null, { timeout: 30000 });
  check("drop anywhere checks the BOM" + w, true);
  check("drop overlay hidden again" + w, !(await p.locator("#dropall.on").count()));
  await p.waitForLoadState("networkidle");
  check("a new BOM asks for no plan it has not got (no 400)" + w, !p.bad.length, p.bad.join(" | "));   // T112
  check("demo: no page errors" + w, !p.errors.length, p.errors.join(" | "));
  await p.context().close();
}

// contrast of text on its background in both themes, labels on every control, reduced motion
async function a11y(browser) {
  const ratio = `(() => {
    const lum = c => { const k = /^color\\(srgb/.test(c) ? 1 : 255; const [r, g, b] = c.match(/[\\d.]+/g).slice(0, 3).map(Number).map(v => { v /= k; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * r + .7152 * g + .0722 * b; };
    const bg = el => { for (; el; el = el.parentElement) { const c = getComputedStyle(el).backgroundColor; if (!/rgba\\(0, 0, 0, 0\\)|transparent/.test(c)) return c; } return "rgb(255,255,255)"; };
    const host = document.createElement("div"); document.querySelector("#libcard").appendChild(host);
    host.innerHTML = '<span class="pill s-ok">a</span><span class="pill s-warn">a</span><span class="pill s-bad">a</span><span class="pill s-info">a</span><span class="pill s-off">a</span><span class="muted">a</span><div class="next s-warn">a</div><button class="primary">a</button><table class="vs"><tr><td class="diff">a</td></tr></table>';
    const out = [...host.querySelectorAll("span, div, button, td")].map(e => { const a = lum(getComputedStyle(e).color), b = lum(bg(e)); return [e.className, (Math.max(a, b) + .05) / (Math.min(a, b) + .05)]; });
    host.remove(); return out; })()`;
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: scheme, reducedMotion: "reduce" });
    const p = await ctx.newPage();
    await p.goto(cfg.local["1280"]);
    await p.waitForSelector("#steps:not([hidden])");
    const bad = (await p.evaluate(ratio)).filter(([, r]) => r < 4.5);
    check(`contrast >= 4.5:1 (${scheme})`, !bad.length, JSON.stringify(bad));
    const unlabeled = await p.evaluate(() => [...document.querySelectorAll("button, input, select, textarea")].filter(e => {
      if (e.type === "hidden" || e.type === "file" || e.closest("[hidden]")) return false;
      const lab = e.getAttribute("aria-label") || (e.labels && e.labels.length && e.labels[0].textContent.trim()) || e.textContent.trim() || e.placeholder;
      return !lab; }).map(e => e.id || e.outerHTML.slice(0, 60)));
    check(`every control has a label (${scheme})`, !unlabeled.length, unlabeled.join(", "));
    check(`reduced motion: no panel animation (${scheme})`, parseFloat(await p.evaluate(() => getComputedStyle(document.querySelector("#panel")).transitionDuration)) < 0.02);
    await p.keyboard.press("Tab");
    check(`first Tab lands on Skip to content (${scheme})`, (await p.evaluate(() => document.activeElement.textContent)) === "Skip to content");
    await ctx.close();
  }
}

// a workspace with a library plan but no BOM check (`ecadlib demo`, `ecadlib librarian`) opens that plan (T051)
async function planned(browser, width) {
  const p = await page(browser, width), w = `@${width}`;
  await p.goto(cfg.planned);
  await p.waitForSelector("#plist .pcard", { timeout: 30000 });
  check("planned: step 2 opens on the plan's held part" + w, await p.isVisible("#libcard") &&
        await p.locator('#plist [data-openp="AO3401A"]').count() === 1, await text(p, "#plist"));
  await p.click('#libout [data-pf=""]');
  check("planned: all 7 parts of the plan" + w, await p.locator("#plist .pcard").count() === 7, await p.locator("#plist .pcard").count());
  check("planned: no page errors" + w, !p.errors.length && !p.bad.length, p.errors.concat(p.bad).join(" | "));
  await p.context().close();
}

async function empty(browser, width) {
  const p = await page(browser, width), w = `@${width}`;
  await p.goto(cfg.empty);
  await p.waitForSelector("#stout .stats");
  check("empty: Check again is disabled with a reason" + w, await p.isDisabled("#rerun") && /No BOM/.test(await p.getAttribute("#rerun", "title")));
  check("empty: status says to drop a BOM" + w, /drop one in step 1/.test(await text(p, "#stout")));
  await p.click('#steps a[href="#libcard"]');
  check("empty: step 2 link explains it needs a BOM" + w, /Check a BOM first/.test(await text(p, "#toast")));
  check("empty: no page errors" + w, !p.errors.length, p.errors.join(" | "));
  await p.context().close();
}

(async () => {
  const opts = cfg.executable ? { executablePath: cfg.executable } : {};
  const browser = await chromium.launch(opts);
  try {
    for (const width of [1280, 390]) {
      if (cfg.local) await local(browser, width).catch(e => check("local run", false, e.message));
      if (cfg.empty) await empty(browser, width).catch(e => check("empty run", false, e.message));
      if (cfg.demo) await demo(browser, width).catch(e => check("demo run", false, e.message));
      if (cfg.planned) await planned(browser, width).catch(e => check("planned run", false, e.message));
    }
    await a11y(browser).catch(e => check("a11y run", false, e.message));
  } finally { await browser.close(); }
  console.log(JSON.stringify({ checks }));
})();
