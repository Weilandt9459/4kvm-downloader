// scripts/extract_m3u8.js — Step 2: Extract m3u8 URL from a 4kvm.net play page.
//
// Usage:
//   node scripts/extract_m3u8.js <4kvm_url>
//
// Outputs JSON between markers:
//   ===JSON_START===
//   {
//     "title": "...",
//     "m3u8Url": "...",
//     "qualityUrls": [...],
//     "episodeLinks": [{"num": N, "url": "..."}]
//   }
//   ===JSON_END===
//
// The m3u8 URL is the one to pass to `scripts/download_video.py` via $M3U8_URL.

const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2] || process.env.PAGE_URL;
  if (!url) {
    console.error('Usage: node extract_m3u8.js <4kvm_url>');
    process.exit(1);
  }

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1080 },
  });
  const page = await context.newPage();

  let m3u8Url = null;
  let qualityUrls = [];

  page.on('response', async (response) => {
    const responseUrl = response.url();
    try {
      if (responseUrl.includes('/video/play?p=')) {
        const body = await response.json();
        if (body.code === 200 && body.data) {
          qualityUrls = body.data.quality_urls || [];
        }
      }
      if (responseUrl.includes('.m3u8')) m3u8Url = responseUrl;
    } catch (e) {}
  });

  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(4000);

  const title = await page.evaluate(() => document.title);

  // Scrape sibling episode links (for batch mode)
  const links = await page.evaluate(() => {
    const result = [];
    document.querySelectorAll('a[href*="/play/"]').forEach(a => {
      const text = a.textContent.trim();
      const numMatch = text.match(/^\d+$/);
      result.push({
        href: a.href,
        text: text.slice(0, 100),
        isEpisodeNumber: !!numMatch,
        num: numMatch ? parseInt(numMatch[0], 10) : null,
      });
    });
    return result;
  });

  const seen = new Set();
  const episodes = [];
  for (const link of links) {
    if (link.isEpisodeNumber && !seen.has(link.num)) {
      seen.add(link.num);
      episodes.push({ num: link.num, url: link.href });
    }
  }
  episodes.sort((a, b) => a.num - b.num);

  const result = {
    title,
    m3u8Url,
    qualityUrls: qualityUrls.map(q => ({
      mtype: q.mtype,
      bitrate: q.bitrate,
      title: q.title,
      description: q.description,
      isvip: q.isvip,
      locked: q.locked,
      url: q.url,
    })),
    episodeLinks: episodes,
  };

  console.log('===JSON_START===');
  console.log(JSON.stringify(result, null, 2));
  console.log('===JSON_END===');

  await browser.close();
})();
