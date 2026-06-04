// find_episodes.js — Standalone script to list all episode links on a play page.
// Run with: node scripts/find_episodes.js <4kvm_url>

const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2] || process.env.PAGE_URL;
  if (!url) {
    console.error('Usage: node find_episodes.js <4kvm_url>');
    process.exit(1);
  }

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  });
  const page = await context.newPage();

  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(2000);

  const episodes = await page.evaluate(() => {
    const result = [];
    const seen = new Set();
    document.querySelectorAll('a[href*="/play/"]').forEach(a => {
      const text = a.textContent.trim();
      const numMatch = text.match(/^\d+$/);
      if (numMatch && !seen.has(numMatch[0])) {
        seen.add(numMatch[0]);
        result.push({ num: parseInt(numMatch[0], 10), url: a.href });
      }
    });
    return result.sort((a, b) => a.num - b.num);
  });

  console.log(JSON.stringify(episodes, null, 2));
  await browser.close();
})();
