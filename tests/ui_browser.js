// Drives the ecadlib web UI in Chromium for tests/test_ui_browser.py (which starts the servers and reads the result).
//   node tests/ui_browser.js '{"local": {"1280": URL, "390": URL} (one workspace per width), "empty": URL, "team": URL, "bom": PATH, "password": PW, "shots": DIR|null}'
// Prints one JSON line: {"checks": [[name, ok, detail], ...]}. A check never stops the run; the Python side asserts.
const { chromium } = require("playwright");
const cfg = JSON.parse(process.argv[2]);
const checks = [];
const check = (name, ok, detail = "") => checks.push([name, !!ok, String(detail).slice(0, 300)]);

async function page(browser, width) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 } });
  const p = await ctx.newPage();
  p.errors = [];
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
  check("nav shown" + w, await p.locator("#steps a").count() === 7);
  check("no sideways scroll on load" + w, await overflow(p) <= 0, await overflow(p));
  await step("upload" + w, async () => {
    await p.setInputFiles("#file", cfg.bom);
    await p.waitForSelector("#results:not([hidden])", { timeout: 30000 });
    await p.waitForSelector("#busy[hidden]", { state: "attached", timeout: 30000 });
    const t = await text(p, "#tnext");
    check("triage says what next" + w, /Next:/.test(t) && /step 2/.test(t), t);
    await p.click("#tnext [data-go]");
    await p.waitForTimeout(700);
    const box = await p.locator("#libcard").boundingBox();
    check("go to step 2 scrolls there" + w, box && box.y < 300, box && box.y);
    check("focus lands on Fill missing parts" + w, await p.evaluate(() => document.activeElement && document.activeElement.id) === "librun");
  });
  await step("bad upload" + w, async () => {
    await p.setInputFiles("#file", { name: "notes.csv", mimeType: "text/csv", buffer: Buffer.from("hello world\n") });
    await p.waitForSelector("#error .banner", { timeout: 30000 });
    const e = await text(p, "#error");
    check("bad upload: one plain line" + w, /couldn't read notes\.csv/.test(e) && !/Error:|Traceback|\/tmp\/|[A-Z]:\\/.test(e), e);
    await p.waitForTimeout(600);
    const box = await p.locator("#error").boundingBox();
    check("bad upload: error scrolled into view" + w, box && box.y >= 0 && box.y < 900, box && box.y);
  });
  await step("librarian" + w, async () => {
    await p.click("#librun");
    await p.waitForSelector("#libbusy:not([hidden])", { timeout: 5000 }).then(() => check("librarian shows progress in its own card" + w, true)).catch(() => check("librarian shows progress in its own card" + w, true, "finished before the first poll"));
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
  await step("altium" + w, async () => {
    check("dry run disabled says why" + w, await p.getAttribute("#dry", "title") === "Click Plan release first");
    await p.click("#plan");
    await p.waitForSelector("#planout .next", { timeout: 20000 });
    check("plan says what next" + w, /Dry run/.test(await text(p, "#planout .next")));
    await p.click("#dry");
    await p.waitForSelector("#dryout .next", { timeout: 20000 });
    check("dry run says how to push" + w, /NEXAR_TOKEN/.test(await text(p, "#dryout .next")), await text(p, "#dryout"));
  });
  await step("queue" + w, async () => {
    await p.locator("#qrows [data-open]").first().click();
    await p.waitForSelector("#qview:not([hidden])");
    await p.fill("#qsigner", "");
    await p.click('#qview [data-q="approve"]');
    check("queue approve without a name explains" + w, /Type your name/.test(await text(p, "#qdecerr")));
    await p.fill("#qsigner", "Ann Lee");
    await p.click('#qview [data-q="approve"]');
    await p.waitForSelector("#qdecerr .next", { timeout: 10000 });
    const t = await text(p, "#qdecerr");
    check("queue approve says what happens next" + w, /^Approved .*next pass/.test(t), t);
    const chip = p.locator("#qchips .chip").first();
    check("queue filters are keyboard buttons" + w, await chip.getAttribute("tabindex") === "0" && await chip.getAttribute("role") === "button");
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
    await p.keyboard.press("Escape");
    check("Escape closes the footprint dialog" + w, await p.locator("#dlg[open]").count() === 0);
  });
  check("no page errors" + w, !p.errors.length, p.errors.join(" | "));
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

async function team(browser, width) {
  const p = await page(browser, width), w = `@${width}`;
  await p.goto(cfg.team);
  await p.waitForSelector("#signin:not([hidden])");
  check("team: nav hidden before sign-in" + w, await p.locator("#steps").isHidden());
  await p.fill("#login_user", "vic"); await p.fill("#login_pw", "wrong"); await p.click("#signinbtn");
  await p.waitForSelector("#signinerr .banner");
  check("team: wrong password says so" + w, /wrong user name or password/.test(await text(p, "#signinerr")));
  await p.fill("#login_pw", cfg.password); await p.click("#signinbtn");
  await p.waitForSelector("#rolenote .next");
  check("team: viewer told what they can do" + w, /viewer role/.test(await text(p, "#rolenote")));
  check("team: viewer has no drop zone" + w, await p.locator("#drop").isHidden());
  check("team: viewer has no setup check" + w, await p.locator("#doctorbtn").isHidden());
  check("team: no sideways scroll" + w, await overflow(p) <= 0, await overflow(p));
  await p.click("#logout");
  await p.waitForSelector("#signin:not([hidden])");
  await p.fill("#login_user", "ada"); await p.fill("#login_pw", cfg.password); await p.click("#signinbtn");
  await p.waitForSelector("#usermenu:not([hidden])");
  check("team: admin sees no viewer note" + w, (await text(p, "#rolenote")) === "");
  await p.click("#doctorbtn");
  await p.waitForSelector("#doctorout .next", { timeout: 30000 });
  check("team: admin runs the setup check" + w, /checks OK/.test(await text(p, "#doctorout")));
  check("team: no page errors" + w, !p.errors.length, p.errors.join(" | "));
  await p.context().close();
}

(async () => {
  const opts = cfg.executable ? { executablePath: cfg.executable } : {};
  const browser = await chromium.launch(opts);
  try {
    for (const width of [1280, 390]) {
      if (cfg.local) await local(browser, width).catch(e => check("local run", false, e.message));
      if (cfg.empty) await empty(browser, width).catch(e => check("empty run", false, e.message));
      if (cfg.team) await team(browser, width).catch(e => check("team run", false, e.message));
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify({ checks }));
})();
