// Screenshot site/index.html with Playwright (Chromium). Uses any installed `playwright` package
// (NODE_PATH may point at an existing node_modules).   node scripts/v2/screenshot.cjs
const { chromium } = require("playwright");
const path = require("path");
(async () => {
  const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  await page.goto("file://" + path.resolve(__dirname, "../../site/index.html"));
  await page.screenshot({ path: path.resolve(__dirname, "../../docs/img/demo.png"), fullPage: false });
  await page.screenshot({ path: path.resolve(__dirname, "../../docs/img/demo_full.png"), fullPage: true });
  await browser.close();
})();
