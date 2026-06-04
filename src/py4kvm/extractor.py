"""Playwright-based m3u8 extractor.

The site uses a WASM module (`nbmovie_wasm`) to sign m3u8 URLs in the browser.
We need a real browser context to execute the WASM and intercept the resulting
`/video/play?p=...` API call which returns the m3u8 URL.

This module wraps the Node.js Playwright scripts (which have first-class support
for browser contexts) and provides a Python interface via subprocess.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import utils

# Path to the bundled JS script (relative to this package)
_EXTRACT_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "extract_m3u8.js"
_FIND_EPISODES_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "find_episodes.js"


@dataclass
class Quality:
    """One quality option from the WASM-signed API response."""
    mtype: str
    bitrate: int
    title: str
    description: str
    isvip: bool
    locked: bool
    url: str  # The m3u8 URL (or "1" for unavailable)


@dataclass
class ExtractionResult:
    """Result of extracting a 4kvm.net page."""
    title: str
    m3u8_url: Optional[str]
    quality_urls: list[Quality]
    episode_links: list[dict]  # [{"num": int, "url": str, "m3u8": str}, ...]


def _run_node_script(script_path: Path, page_url: str) -> dict:
    """Run a Node.js script with the given page URL and return its JSON output.

    The script must be self-contained and output JSON to stdout.
    """
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    # Use a temp file so we can avoid command-line arg length issues and
    # so the script can be run from any cwd.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        out_path = f.name
    try:
        # Write a small wrapper that writes URL to a JSON file the script reads
        # Actually, the simpler approach: pass URL via env var.
        env_overrides = {"PAGE_URL": page_url}
        env = {**__import__("os").environ, **env_overrides}
        result = subprocess.run(
            ["node", str(script_path), page_url],
            capture_output=True, text=True, timeout=120, env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Node script failed (rc={result.returncode}):\n"
                f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
            )
        # Try to parse as JSON; if not, look for JSON in the output
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            # Look for the last JSON object in the output
            m = re.search(r"\{[\s\S]*\}", result.stdout)
            if m:
                return json.loads(m.group(0))
            raise RuntimeError(f"Script did not produce JSON. Output:\n{result.stdout}")
    finally:
        Path(out_path).unlink(missing_ok=True)


def get_title(page_url: str) -> str:
    """Extract the page title from a 4kvm.net play page."""
    script_text = f'''
const {{ chromium }} = require('playwright');
(async () => {{
  const browser = await chromium.launch({{ headless: true }});
  const ctx = await browser.newContext({{
    userAgent: '{utils.USER_AGENT}',
    viewport: {{ width: 1920, height: 1080 }},
  }});
  const page = await ctx.newPage();
  await page.goto(process.env.PAGE_URL, {{ waitUntil: 'networkidle', timeout: 60000 }});
  const title = await page.evaluate(() => document.title);
  console.log(JSON.stringify({{ title }}));
  await browser.close();
}})();
'''
    script_path = Path(tempfile.mkstemp(suffix=".js")[1])
    script_path.write_text(script_text)
    try:
        result = subprocess.run(
            ["node", str(script_path), page_url],
            capture_output=True, text=True, timeout=120,
            env={**__import__("os").environ, "PAGE_URL": page_url},
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to get title: {result.stderr}")
        data = json.loads(result.stdout)
        return data["title"]
    finally:
        script_path.unlink(missing_ok=True)


def get_m3u8_url(page_url: str, quality_preference: str = "1080p") -> str:
    """Extract the m3u8 URL for a 4kvm.net play page.

    Args:
        page_url: The full 4kvm.net play URL, e.g. "https://www.4kvm.net/play/ch46zvt3r"
        quality_preference: Preferred quality. Tries this first, then falls back
            to any non-VIP-locked quality, then the highest bitrate.

    Returns:
        The m3u8 URL (signed, expires after ~1 hour).

    Raises:
        RuntimeError: If no m3u8 URL can be extracted.
    """
    extraction = extract_page(page_url)
    if not extraction.m3u8_url:
        raise RuntimeError(f"No m3u8 URL found in {page_url}. Page title: {extraction.title}")
    return extraction.m3u8_url


def extract_page(page_url: str) -> ExtractionResult:
    """Extract all relevant info from a 4kvm.net play page.

    This runs a headless browser, intercepts the WASM-signed API call,
    captures the m3u8 URL, the quality options, and any sibling episode links
    (useful for batch downloads).
    """
    # Write the extraction script (similar to scripts/extract_m3u8.js but
    # self-contained and outputs JSON to stdout).
    script_text = '''
const { chromium } = require('playwright');
const url = process.env.PAGE_URL;

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1080 },
  });
  const page = await ctx.newPage();

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

  // Scrape episode links
  const links = await page.evaluate(() => {
    const result = [];
    document.querySelectorAll('a[href*="/play/"]').forEach(a => {
      const text = a.textContent.trim();
      const numMatch = text.match(/^\\d+$/);
      result.push({
        href: a.href,
        text: text.slice(0, 100),
        isEpisodeNumber: !!numMatch,
        num: numMatch ? parseInt(numMatch[0], 10) : null,
      });
    });
    return result;
  });

  // Get unique episode links (text is a number)
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
'''
    script_path = Path(tempfile.mkstemp(suffix=".js")[1])
    script_path.write_text(script_text)
    try:
        result = subprocess.run(
            ["node", str(script_path)],
            capture_output=True, text=True, timeout=120,
            env={**__import__("os").environ, "PAGE_URL": page_url},
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Extraction failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
            )
        # Extract JSON between markers
        m = re.search(r"===JSON_START===\s*([\s\S]*?)\s*===JSON_END===", result.stdout)
        if not m:
            raise RuntimeError(f"No JSON found in output:\n{result.stdout}")
        data = json.loads(m.group(1))
        return ExtractionResult(
            title=data["title"],
            m3u8_url=data["m3u8Url"],
            quality_urls=[Quality(**q) for q in data["qualityUrls"]],
            episode_links=data["episodeLinks"],
        )
    finally:
        script_path.unlink(missing_ok=True)


def find_episodes(page_url: str) -> list[dict]:
    """Find all episode URLs from a 4kvm.net play page.

    Returns a sorted list of dicts: [{"num": int, "url": str}, ...]
    """
    return extract_page(page_url).episode_links
