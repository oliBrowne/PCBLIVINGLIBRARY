// Retakes the web UI pictures in docs/ from offline workspaces, so they follow the UI. Not ui-triage.png: an offline
// triage has no part sources to show, so that one is taken by hand from a run with sources.
//   python3 -m tests.ui_fixture /tmp/uifx --port 8767 &      # serves the workspace (sample BOM, librarian run)
//   ecadlib demo --out /tmp/demo && ecadlib ui --workspace /tmp/demo --offline --no-browser --port 8768 &
//   node scripts/ui_screenshots.js http://127.0.0.1:8767/ http://127.0.0.1:8768/ docs
// Needs node, Playwright and Chromium (the same as tests/test_ui_browser.py).
const { chromium } = require("playwright");
const [fixture, demo, out = "docs"] = process.argv.slice(2);

async function open(browser, url, width = 1280, height = 900) {
  const p = await browser.newPage({ viewport: { width, height } });
  p.on("dialog", d => d.accept());
  await p.goto(url);
  await p.waitForSelector("#steps:not([hidden])");
  await p.addStyleTag({ content: "*, *::before, *::after { animation: none !important; transition: none !important; }" });
  return p;
}
const settle = p => p.waitForTimeout(700);

(async () => {
  const browser = await chromium.launch();

  // the fixture workspace: open its triaged BOM, which fills steps 2 and 3
  const p = await open(browser, fixture);
  await p.click('#histrows button:has-text("Open")');
  await p.waitForSelector('#plist [data-openp="TPS62133RGTR"]', { timeout: 30000 });
  await settle(p);

  await p.click('#plist [data-openp="TPS62133RGTR"]');           // a held part: why, what next, the cross-check
  await p.waitForSelector("#panel.open");
  await settle(p);
  await p.screenshot({ path: `${out}/ui-held-part.png` });

  await p.click('#pbody [data-pick]');                             // Pick a symbol or footprint...
  await p.waitForSelector("#pickdlg[open]");
  await p.waitForTimeout(1500);
  await p.screenshot({ path: `${out}/ui-resolve.png` });
  await p.keyboard.press("Escape");
  await p.keyboard.press("Escape");
  await settle(p);

  await p.fill("#signer", "Ann Lee <ann@example.com>");            // sign-off and duplicates
  await p.click("#apprstatus");
  await p.click("#dedupe");
  await p.waitForTimeout(2500);
  await p.locator("#libtools").screenshot({ path: `${out}/ui-signoff.png` });

  await p.click("#plan");                                          // step 3: plan the Altium 365 release
  await p.waitForTimeout(2500);
  await p.locator("#export").screenshot({ path: `${out}/ui-altium.png` });

  await p.click("#newfp");                                         // the footprint generator
  await p.waitForSelector("#dlg[open]");
  const ex = await p.$$eval("#example option", os => os.map(o => o.value).find(v => /SOIC-8/.test(v)) || os[1].value);
  await p.selectOption("#example", ex);
  await p.click("#previewbtn");
  await p.waitForTimeout(2000);
  await p.locator("#dlg").screenshot({ path: `${out}/ui-footprint.png` });

  // the demo workspace (ecadlib demo): step 2 as a new user first sees it
  const d = await open(browser, demo);
  await d.waitForSelector("#plist .pcard");
  await settle(d);
  await d.locator("#libcard").screenshot({ path: `${out}/ui-librarian.png` });

  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
