// scripts/get_title.js — Step 1: Extract page title from a 4kvm.net play page.
//
// Usage:
//   node scripts/get_title.js <4kvm_url>
//
// Outputs JSON to stdout: {"title": "..."}
//
// Used to derive the output filename, e.g. "剧名 第一季 - 第1集 -4k影视"
// → "剧名_S01E01.mp4"

const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2] || process.env.PAGE_URL;
  if (!url) {
    console.error('Usage: node get_title.js <4kvm_url>');
    process.exit(1);
  }

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1080 },
  });
  const page = await context.newPage();
  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  const title = await page.evaluate(() => document.title);
  console.log(JSON.stringify({ title }, null, 2));
  await browser.close();
})();
