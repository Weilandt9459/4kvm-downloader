---
name: 4kvm-downloader
description: Download videos from 4kvm.net by automating the full pipeline — extract m3u8 via Playwright, download segments, strip PNG wrappers, and convert to MP4. Trigger when user provides a 4kvm.net URL or asks to download from 4kvm.
---

# 4kvm.net Video Downloader

Automatically download videos from 4kvm.net, defeating 8 layers of anti-scraping protection.

## Anti-scraping layers

| Layer | Mechanism | Defeat |
|-------|-----------|--------|
| 1. WASM-signed API | `build_play_url()` in `nbmovie_wasm` reads DOM meta tags and generates time-sensitive signed URLs | Headless browser (Playwright) executes WASM in real browser context |
| 2. Extensionless segments | CDN URLs have no `.ts` extension, breaking ffmpeg HLS parser | Manual URL extraction from m3u8 |
| 3. PNG-disguised segments | Each `.ts` segment has a fake PNG header (~110 bytes) prepended | Strip bytes before IEND marker + 8 bytes |
| 4. No-referrer policy | `referrerPolicy: 'no-referrer'` on video element; CDN rejects requests with Referer header | Omit Referer header from segment downloads |
| 5. Cross-domain CDN | m3u8 on `oss.douyinbit.com`, segments on `sns-open-qc.xhscdn.com` (Tencent COS) | Follow redirect chain |
| 6. Connection-level rate limiting | CDN throttles when >2-4 parallel connections hit from same IP | Use 2 workers, fall back to curl (uses system proxy) for failures |
| 7. HEAD 404 misdirection | CDN returns HTTP 404 to HEAD requests even when GET works | Use GET only for m3u8 liveness checks |
| 8. Base64-encoded relative URLs | Some segments have `/ets/{hash}/{base64}` paths; base64 may be truncated in m3u8 | Use the full URL from the CDN's redirect response, not the truncated base64 |

## Prerequisites

Before running, ensure these are available (install if missing):

1. **Node.js + Playwright**: `npm install playwright && npx playwright install chromium`
2. **Python 3** with stdlib only (urllib, ssl, subprocess)
3. **ffmpeg** in PATH: `brew install ffmpeg`

## Workflow

When the user provides a 4kvm.net URL (e.g. `https://www.4kvm.net/play/ch0xz51yd`), follow these steps IN ORDER. Do NOT skip steps or hardcode values — always generate scripts with the actual URL and output paths.

### Step 1: Extract page title and episode info

Create and run a Playwright script to get the video title (used for naming the output file).

```js
// get_title.js — auto-generated
const { chromium } = require('playwright');
(async () => {
  const PAGE_URL = '{{USER_URL}}';
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1080 },
  });
  const page = await context.newPage();
  await page.goto(PAGE_URL, { waitUntil: 'networkidle', timeout: 60000 });
  const title = await page.evaluate(() => document.title);
  console.log('TITLE:', title);
  await browser.close();
})();
```

From the title (format: `剧名: 第X季 - 第Y集 -4k影视`), derive a filename like `剧名_S0XE0Y.mp4`.

### Step 2: Extract the m3u8 URL

Create and run this Playwright script with `PAGE_URL` set to the user's URL. This script intercepts the WASM-generated API call that returns quality URLs, and also captures any direct m3u8 loads.

```js
// extract_m3u8.js — auto-generated
const { chromium } = require('playwright');

(async () => {
  const PAGE_URL = '{{USER_URL}}';

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1080 },
  });
  const page = await context.newPage();

  let m3u8Url = null;
  let qualityUrls = [];

  page.on('response', async (response) => {
    const url = response.url();
    // Catch the WASM-generated API call that returns quality URLs
    if (url.includes('/video/play?p=')) {
      try {
        const body = await response.json();
        if (body.code === 200 && body.data) {
          qualityUrls = body.data.quality_urls || [];
          console.log('Quality URLs:', JSON.stringify(qualityUrls, null, 2));
        }
      } catch (e) {}
    }
    // Also catch direct m3u8 loads
    if (url.includes('.m3u8')) {
      m3u8Url = url;
      console.log('M3U8 URL:', url);
    }
  });

  await page.goto(PAGE_URL, { waitUntil: 'networkidle', timeout: 60000 });

  // Wait for WASM-signed API call + m3u8 load to complete.
  // The PAGE_URL already points to a specific episode — the page auto-loads
  // the correct video, so we must NOT click any episode link (doing so would
  // switch to a different episode).
  await page.waitForTimeout(5000);

  // Fallback: check page state
  const pageData = await page.evaluate(() => {
    if (window.artPlayerInstance && window.artPlayerInstance.qualityUrls) {
      return { qualityUrls: window.artPlayerInstance.qualityUrls };
    }
    return null;
  });

  if (pageData && pageData.qualityUrls) {
    console.log('Page qualityUrls:', JSON.stringify(pageData.qualityUrls, null, 2));
  }

  await browser.close();
})();
```

Run: `node extract_m3u8.js`

**Select the best quality** from the output. Prefer 1080p, then 720p, etc. Extract the `url` field from the chosen quality entry. This is the m3u8 URL for Step 3.

### Step 3: Generate and run the download script

Create a Python script with the m3u8 URL from Step 2 and the output filename from Step 1. Write it to the user's current working directory.

**IMPORTANT variable substitutions:**
- `M3U8_URL` = the m3u8 URL from Step 2
- `OUTPUT_FILE` = derived filename (e.g. `/Users/oly/Desktop/爬虫/无耻之徒_S01E02.mp4`)
- `OUTPUT_DIR` = same directory as OUTPUT_FILE + `/video_download` subdirectory

```python
#!/usr/bin/env python3
"""download_video.py — Download HLS video from 4kvm.net"""
import os, sys, ssl, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

M3U8_URL = "{{M3U8_URL_FROM_STEP_2}}"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_download")
SEGMENTS_DIR = os.path.join(OUTPUT_DIR, "segments")
CLEAN_DIR = os.path.join(OUTPUT_DIR, "clean_segments")
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "{{OUTPUT_FILENAME}}")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

os.makedirs(SEGMENTS_DIR, exist_ok=True)
os.makedirs(CLEAN_DIR, exist_ok=True)

def download_m3u8():
    print("[1/5] Downloading m3u8 playlist...")
    req = urllib.request.Request(M3U8_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
        content = resp.read().decode("utf-8")
    urls = [l.strip() for l in content.split("\n") if l.strip() and not l.startswith("#")]
    print(f"  Found {len(urls)} segments")
    return urls

def download_segment(args):
    url, filepath, idx, total = args
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return idx, True, "exists"
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                data = resp.read()
            with open(filepath, "wb") as f:
                f.write(data)
            return idx, True, "OK"
        except Exception as e:
            if attempt < 4:
                import time; time.sleep(1 * (attempt + 1))
            else:
                return idx, False, str(e)

def download_segments(urls):
    """Use 2 workers — more triggers CDN rate limiting (layer 6).
    Track failed segments; recover them with single curl attempts (bypasses via system proxy)."""
    print(f"\n[2/5] Downloading {len(urls)} segments (2 parallel — avoids CDN rate limit)...")
    tasks = [(url, os.path.join(SEGMENTS_DIR, f"{i:05d}.ts"), i+1, len(urls)) for i, url in enumerate(urls)]
    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(download_segment, t): t for t in tasks}
        for f in as_completed(futures):
            _, success, _ = f.result()
            if success: ok += 1
            else: fail += 1
    print(f"  Phase 1: {ok} downloaded, {fail} failed")
    return ok, fail

def curl_fallback(urls, seg_dir):
    """Single curl attempt per missing segment. curl uses the system proxy, which
    bypasses the connection-level rate limiting that hits Python urllib."""
    missing = []
    for i, url in enumerate(urls):
        fp = os.path.join(seg_dir, f"{i:05d}.ts")
        if not os.path.exists(fp) or os.path.getsize(fp) == 0:
            missing.append((i, url))
    if not missing:
        return []
    print(f"  Curl fallback: {len(missing)} missing segments")
    still_missing = []
    for i, url in missing:
        fp = os.path.join(seg_dir, f"{i:05d}.ts")
        r = subprocess.run(
            ["curl", "-sS", "-A", HEADERS["User-Agent"], "--max-time", "30", url, "-o", fp],
            capture_output=True, timeout=35,
        )
        if r.returncode != 0 or not os.path.exists(fp) or os.path.getsize(fp) == 0:
            still_missing.append(i)
    return still_missing

def strip_png_wrapper(data):
    """Strip fake PNG header prepended to TS segments."""
    iend = data.find(b"IEND")
    if iend == -1:
        return data
    ts_start = iend + 8  # 4 for "IEND" type + 4 CRC bytes
    if ts_start < len(data) and data[ts_start] == 0x47:
        return data[ts_start:]
    for i in range(iend, min(iend + 200, len(data))):
        if data[i] == 0x47:
            return data[i:]
    return data

def clean_segments(urls):
    print(f"\n[3/5] Stripping PNG wrappers...")
    total_orig, total_clean = 0, 0
    for i in range(len(urls)):
        in_path = os.path.join(SEGMENTS_DIR, f"{i:05d}.ts")
        out_path = os.path.join(CLEAN_DIR, f"{i:05d}.ts")
        if not os.path.exists(in_path): continue
        with open(in_path, "rb") as f:
            data = f.read()
        total_orig += len(data)
        clean_data = strip_png_wrapper(data)
        total_clean += len(clean_data)
        with open(out_path, "wb") as f:
            f.write(clean_data)
    print(f"  Clean: {total_clean/(1024*1024):.1f} MB (stripped {(total_orig-total_clean)/1024:.1f} KB)")

def concatenate(urls):
    merged = os.path.join(OUTPUT_DIR, "merged.ts")
    print(f"\n[4/5] Concatenating...")
    total = 0
    with open(merged, "wb") as out:
        for i in range(len(urls)):
            path = os.path.join(CLEAN_DIR, f"{i:05d}.ts")
            if os.path.exists(path):
                with open(path, "rb") as f:
                    data = f.read()
                    out.write(data)
                    total += len(data)
    print(f"  Merged: {total/(1024*1024):.1f} MB")
    return merged

def convert(merged_ts):
    print(f"\n[5/5] Converting to MP4...")
    result = subprocess.run(
        ["ffmpeg", "-i", merged_ts, "-c", "copy", "-bsf:a", "aac_adtstoasc", OUTPUT_FILE, "-y"],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode == 0:
        print(f"  Success! {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE)/(1024*1024):.1f} MB)")
    else:
        print(f"  Error: {result.stderr[-500:]}")
        sys.exit(1)

def main():
    urls = download_m3u8()
    ok, fail = download_segments(urls)
    if fail > 0:
        missing = curl_fallback(urls, SEGMENTS_DIR)
        if missing:
            print(f"  ERROR: segments {missing} still missing after curl fallback")
            print(f"  These may need manual recovery (see 'Manual recovery' section below)")
            sys.exit(1)
    clean_segments(urls)
    merged = concatenate(urls)
    convert(merged)
    print("\nDone!")

if __name__ == "__main__":
    main()
```

### Manual recovery for stubborn segments

A small number of segments (typically 1-3%) consistently fail via both Python urllib and curl.
**For most segments**: just `curl` the URL from the m3u8 directly with a 30s timeout.
**For base64-encoded `/ets/` paths** (Layer 8): the URL in the m3u8 may be a relative path like
`/ets/{timestamp}-{hash}/{base64}`. The base64 can be truncated; instead:

1. Construct the full URL: `https://sns-open-qc.xhscdn.com{relative_path}`
2. Use `curl -v` to see the CDN's redirect target — it returns a `<a href="...">Found</a>` HTML
   page when the base64 is correct
3. Extract the full URL from the `href` attribute and `curl` that directly
4. The full URL is on `sns-open-qc.xhscdn.com/professionalpc/...` (not the `/ets/` path)

Run: `python3 download_video.py`

### Step 4: Verify the output

```bash
ffprobe -v quiet -print_format json -show_format -show_streams "{{OUTPUT_FILE}}" | python3 -c "import json,sys; d=json.load(sys.stdin); s=d['streams'][0]; print(f'Resolution: {s[\"width\"]}x{s[\"height\"]}'); print(f'Codec: {s[\"codec_name\"]}'); print(f'Duration: {float(d[\"format\"][\"duration\"])/60:.1f} min'); print(f'Size: {int(d[\"format\"][\"size\"])/1024/1024:.1f} MB')"
```

Expected: 1080p H.264 (1920x1080 or 1920x960 for 2:1 aspect ratio content), duration ~50 min, AAC audio.

### Step 5: Cleanup temp files

After successful download and verification, delete temporary files to save disk:

```bash
rm -rf video_download/
rm -f extract_m3u8.js get_title.js download_video.py
```

## Important notes

- **The m3u8 URL IS time-sensitive** (signed URL, expires ~1 hour after extraction). The m3u8 itself returns 404 once expired, but the individual segment URLs on `sns-open-qc.xhscdn.com` remain valid via CDN cache. If you get HTTP 404 from the m3u8, re-run Step 2 to get a fresh one.
- **HEAD requests return 404** on the m3u8 CDN even when GET works. Use `curl -s` (GET) not `curl -I` (HEAD) to check m3u8 liveness.
- **Use 2-4 parallel workers max** — 8+ workers triggers connection-level rate limiting (HTTP 404s on specific segments). curl uses the system proxy and bypasses the rate limit that hits Python urllib; use it as fallback.
- Segment downloads require **NO Referer header** — this is critical, the CDN rejects requests with Referer
- The PNG wrapper is always a small (~110 byte) fake PNG image before the TS data starts at sync byte 0x47
- This site uses Tencent COS CDN (`sns-open-qc.xhscdn.com` / `oss.douyinbit.com`) which has generous cache
- Each 4kvm.net URL maps to a specific episode — the page auto-loads the correct video. To download a different episode, use that episode's own URL. Do NOT click episode links in Step 2, as that switches away from the intended episode
- Output files can exceed 1 GB for 1080p content — ensure sufficient disk space

## Batch mode (downloading multiple episodes)

To download several episodes in one run (e.g. an entire season):

1. Visit any episode's page in Playwright; scrape the episode links from the DOM (they're `<a href="/play/{id}">` elements with the episode number as text)
2. Loop over episodes, running Steps 1-2 for each to get a fresh m3u8 URL (URLs are unique per fetch)
3. Use the script above as a template wrapped in a per-episode loop
4. **Important**: skip the m3u8 fetch if the .mp4 output file already exists (idempotent re-runs)
5. After parallel+curl for one episode, ~1-3% of segments may still fail. Recover these manually as described in the "Manual recovery" section before converting to MP4
6. Each episode of a ~50min show is ~700-1000 MB; a full season (~8 episodes) is 6-8 GB
