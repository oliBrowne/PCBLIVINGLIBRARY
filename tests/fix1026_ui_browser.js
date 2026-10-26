// Release anyway in real Chromium (tests/test_fix1026_ui.py): the name box is required, and the toast follows the
// part's real status. argv[2]: JSON {url, part, executable}. Prints one JSON line {checks: [[name, ok, detail]]}.
const { chromium } = require("playwright");
const cfg = JSON.parse(process.argv[2]);
(async () => {
  const checks = [], check = (n, ok, d) => checks.push([n, !!ok, d === undefined ? "" : String(d)]);
  const b = await chromium.launch(cfg.executable ? { executablePath: cfg.executable } : {});
  try {
    const p = await b.newPage(), sent = [];
    p.on("request", r => { if (r.url().endsWith("/api/release-check")) sent.push(r.postData()); });
    await p.goto(cfg.url);
    await p.locator("[data-open]").first().click();
    await p.waitForSelector(`#plist button.pname[data-openp="${cfg.part}"]`, { timeout: 20000 });
    await p.click(`#plist button.pname[data-openp="${cfg.part}"]`);
    await p.waitForSelector(`[data-allow="${cfg.part}"]`);
    await p.fill("#signer", "");
    await p.click(`[data-allow="${cfg.part}"]`);
    await p.waitForTimeout(500);
    const err = await p.locator("#perr").innerText();
    check("release anyway without a name asks for it", /Type your name/.test(err), err);
    check("release anyway without a name sends nothing", sent.length === 0, sent.join(" | "));
    await p.fill("#signer", "Ann Lee");
    await p.click(`[data-allow="${cfg.part}"]`);
    await p.waitForFunction(() => [...document.querySelectorAll("#toast .t span")].some(s => /anyway/.test(s.textContent)),
                            null, { timeout: 15000 });
    const toasts = await p.evaluate(() => [...document.querySelectorAll("#toast .t span")].map(s => s.textContent).join(" | "));
    check("the release carried the typed name", sent.length === 1 && JSON.parse(sent[0]).user === "Ann Lee", sent.join(" | "));
    check("toast for a part awaiting sign-off says so", /waits for sign-off/.test(toasts) && !/next push/.test(toasts), toasts);
  } catch (e) {
    check("browser run", false, e.stack || e);
  } finally {
    await b.close();
  }
  console.log(JSON.stringify({ checks }));
})();
