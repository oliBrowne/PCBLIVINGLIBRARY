// Security review 2026-11-23 (tests/test_security1123_local_page.py) in real Chromium: the page opens only with a
// launcher's link, works under its Content-Security-Policy, and markup from a triage report file is shown as text.
// argv[2]: JSON {url, setup, plain, executable}. Prints one JSON line {checks: [[name, ok, detail]]}.
const { chromium } = require("playwright");
const cfg = JSON.parse(process.argv[2]);
(async () => {
  const checks = [], check = (n, ok, d) => checks.push([n, !!ok, d === undefined ? "" : String(d)]);
  const b = await chromium.launch(cfg.executable ? { executablePath: cfg.executable } : {});
  try {
    const other = await (await b.newContext()).newPage();          // a browser ecadlib did not open: no key
    const r0 = await other.goto(cfg.plain);
    await other.waitForSelector("#nokey", { timeout: 20000 });
    check("a browser without the key gets the 'open it from ecadlib' notice", r0.status() === 200 && /Open this page from ecadlib/.test(await other.locator("#nokey").innerText()));
    const ctx = await b.newContext(), p = await ctx.newPage(), errors = [];
    p.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
    p.on("pageerror", e => errors.push(String(e)));
    await p.goto(cfg.url);
    check("the link's key is gone from the address", p.url() === cfg.plain, p.url());
    await p.waitForSelector("#histrows [data-open]", { timeout: 20000 });
    check("the page's script ran under the policy and drew the workspace", /workspace/.test(await p.locator("#meta").innerText()));
    await p.locator("#histrows [data-open]").first().click();
    await p.waitForSelector("#rows tr td", { timeout: 20000 });
    const cell = await p.locator("#rows tr td").first().innerText(), chips = await p.locator("#chips").innerText();
    check("the report's line number is shown as text", cell.includes("<img src=x"), cell);
    check("the report's count is shown as text", chips.includes("<img src=x"), chips);
    check("no element was made from the report", await p.locator("#rows img, #chips img").count() === 0);
    await p.waitForTimeout(300);
    check("its handler did not run", await p.evaluate(() => window.PWNED === undefined));
    // were a value to slip through unescaped, the policy still stops its handler
    await p.evaluate(() => { document.querySelector("#rows").innerHTML = '<tr><td><img src=x onerror="window.PWNED=1"></td></tr>'; });
    await p.waitForTimeout(500);
    check("an inline handler does not run on this page (Content-Security-Policy)", await p.evaluate(() => window.PWNED === undefined));
    await p.reload();
    await p.waitForSelector("#histrows [data-open]", { timeout: 20000 });
    check("a reload keeps working (the tab's own key, no key in the address)", p.url() === cfg.plain);
    check("the page set no cookie (every 127.0.0.1 port would get it)", (await ctx.cookies()).length === 0 && await p.evaluate(() => document.cookie === ""));
    const [dl] = await Promise.all([p.waitForEvent("download", { timeout: 20000 }), p.evaluate(() => {
      const a = document.createElement("a"); a.href = "/api/file?path=" + encodeURIComponent("reports/triage_shared.json"); a.textContent = "f";
      document.body.appendChild(a); a.click(); })]);
    check("a file link downloads with the tab's key", dl.suggestedFilename() === "triage_shared.json", dl.suggestedFilename());
    const plainGet = await ctx.request.get(new URL("/api/file?path=reports%2Ftriage_shared.json", cfg.plain).href);
    check("the same file without the key is refused", plainGet.status() === 401);
    const again = await (await ctx.newPage()).goto(cfg.url);
    check("the same link a second time opens nothing", again.status() === 401);
    const thief = await (await b.newContext()).newPage();
    check("the used link opens nothing in another browser", (await thief.goto(cfg.url)).status() === 401);
    const s = await (await b.newContext()).newPage();
    await s.goto(cfg.setup);
    await s.waitForSelector("#c-done button", { timeout: 20000 });
    await s.waitForFunction(() => document.querySelector("#providers") && document.querySelector("#providers").children.length > 0, null, { timeout: 20000 });
    await Promise.all([s.waitForURL(cfg.plain, { timeout: 20000 }), s.click("#c-done button")]);
    check("the setup page's Open ecadlib button opens the main page", s.url() === cfg.plain, s.url());
    const real = errors.filter(e => !/Content Security Policy|Content-Security-Policy|ERR_FAILED|Failed to load resource/.test(e));
    check("no script error on the page", real.length === 0, real.join(" | "));
  } catch (e) {
    check("browser run", false, e.stack || e);
  } finally {
    await b.close();
  }
  console.log(JSON.stringify({ checks }));
})();
