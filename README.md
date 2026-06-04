# 4kvm-downloader

[![CI](https://github.com/yay0128/4kvm-downloader/actions/workflows/test.yml/badge.svg)](https://github.com/yay0128/4kvm-downloader/actions/workflows/test.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![FFmpeg](https://img.shields.io/badge/depends-ffmpeg-green.svg)](https://ffmpeg.org/)

A robust downloader for [4kvm.net](https://www.4kvm.net) that defeats 8 layers of anti-scraping
protection used by the site's Tencent COS CDN. Provides both a Python library and a CLI for
single videos and full-season batch downloads.

## Features

- 🎬 **Single video and batch (multi-episode) downloads** with one command
- 🛡️ **8 anti-scraping layers defeated** — see [How it works](#how-it-works)
- 🔁 **Self-healing downloads** — segments that fail via parallel downloads are retried
  with `curl` (uses system proxy, bypasses the connection-level rate limit)
- 📦 **Self-contained** — no `ffmpeg` HLS parsing needed; we strip PNG wrappers and
  re-mux manually
- 🐍 **Library + CLI** — use as a Python module or from the command line

## Installation

### Prerequisites

| Tool | Install | Purpose |
|------|---------|---------|
| Python 3.9+ | [python.org](https://python.org) | Downloader runtime |
| Node.js 18+ | [nodejs.org](https://nodejs.org) | Playwright (WASM execution) |
| FFmpeg | `brew install ffmpeg` | Final `.ts` → `.mp4` conversion |
| Chromium | `npx playwright install chromium` | Headless browser |

### From source

```bash
git clone https://github.com/your-username/4kvm-downloader.git
cd 4kvm-downloader
pip install -r requirements.txt
npx playwright install chromium
```

## Quick start

### Download a single video

```bash
python scripts/download.py "https://www.4kvm.net/play/ch46zvt3r"
```

Output: `校园之外_S01E01.mp4` (~1 GB for 50min 1080p) in the current directory.

### Download a full season

```bash
python scripts/batch_download.py "https://www.4kvm.net/play/ch46zvt3r"
```

This will:
1. Visit the page, scrape all episode links
2. For each episode: extract fresh m3u8 → download segments → convert to MP4
3. Skip episodes that already exist on disk (idempotent re-runs)

### As a Python library

```python
from py4kvm import downloader, extractor

# Single video
m3u8_url = extractor.get_m3u8_url("https://www.4kvm.net/play/ch46zvt3r")
downloader.download_episode(m3u8_url, output_path="校园之外_S01E01.mp4")

# Verify
from py4kvm.utils import probe
info = probe("校园之外_S01E01.mp4")
print(f"{info.width}x{info.height} {info.codec} {info.duration/60:.1f}min")
```

## How it works

The site uses 8 layers of protection; we defeat each one:

| # | Layer | Mechanism | Defeat |
|---|-------|-----------|--------|
| 1 | WASM-signed API | `build_play_url()` in `nbmovie_wasm` reads DOM meta tags and generates time-sensitive signed URLs | Headless browser (Playwright) executes WASM in real browser context |
| 2 | Extensionless segments | CDN URLs have no `.ts` extension, breaking ffmpeg HLS parser | Manual URL extraction from m3u8 |
| 3 | PNG-disguised segments | Each `.ts` segment has a fake PNG header (~110 bytes) prepended | Strip bytes before IEND marker + 8 bytes |
| 4 | No-referrer policy | `referrerPolicy: 'no-referrer'` on video element; CDN rejects requests with Referer header | Omit Referer header from segment downloads |
| 5 | Cross-domain CDN | m3u8 on `oss.douyinbit.com`, segments on `sns-open-qc.xhscdn.com` (Tencent COS) | Follow redirect chain |
| 6 | Connection-level rate limiting | CDN throttles when >2-4 parallel connections hit from same IP | Use 2 workers, fall back to curl (uses system proxy) for failures |
| 7 | HEAD 404 misdirection | CDN returns HTTP 404 to HEAD requests even when GET works | Use GET only for m3u8 liveness checks |
| 8 | Base64-encoded relative URLs | Some segments have `/ets/{hash}/{base64}` paths; base64 may be truncated in m3u8 | Use the full URL from the CDN's redirect response |

## Pipeline

```
4kvm.net URL
   │
   ▼
[Playwright] Open page, intercept /video/play?p= API
   │
   ▼
[m3u8 URL]  ─────────────┐
   │                      │  (m3u8 expires in ~1h, re-extract if 404)
   ▼                      │
[2-worker parallel download]── 96-99% of segments
   │
   ▼ (1-3% still missing)
[curl single-pass] ────── +1-2% via system proxy
   │
   ▼ (rare, <1%)
[Manual recovery] ─────── /ets/ base64 paths, truncated URLs
   │
   ▼
[Strip PNG wrapper] (find IEND, jump to sync byte 0x47)
   │
   ▼
[Concatenate to .ts] → [ffmpeg copy → .mp4]
```

## Project layout

```
4kvm-downloader/
├── README.md                # This file
├── LICENSE                  # MIT
├── requirements.txt         # Python deps
├── package.json             # Node deps (for Playwright scripts)
├── .gitignore
├── src/py4kvm/              # Library code
│   ├── __init__.py
│   ├── downloader.py        # Segment download + recovery
│   ├── extractor.py         # Playwright m3u8 extraction
│   ├── converter.py         # PNG strip + ffmpeg wrapper
│   ├── batch.py             # Full-season orchestration
│   └── utils.py             # ffprobe wrapper, helpers
├── scripts/                 # CLI entry points
│   ├── download.py          # Single video
│   ├── batch_download.py    # Full season
│   └── find_episodes.js     # Episode link scraper
├── examples/                # Usage examples
│   └── library_usage.py
├── tests/                   # Unit tests
│   ├── test_converter.py
│   ├── test_utils.py
│   └── fixtures/            # Sample PNG-wrapped TS for testing
└── docs/
    └── architecture.md      # Detailed design notes
```

## Troubleshooting

### "HTTP 404" on the m3u8 URL

The signed m3u8 URL expires after ~1 hour. Re-run the command — `extract_m3u8.js` will
fetch a fresh one. The underlying segment URLs are cached longer, so already-downloaded
segments don't need to be re-fetched.

### A few segments consistently fail (~1-3%)

The CDN rate-limits specific segment paths from your IP. The bundled `curl_fallback`
recovers most of these. For the rare persistent failures (typically segments with the
`/ets/{base64}` path format), see the **Manual recovery** workflow in
[docs/architecture.md](docs/architecture.md).

### "ffmpeg: command not found"

Install via `brew install ffmpeg` (macOS) or `apt install ffmpeg` (Linux).

### "playwright: command not found" / Chromium not installed

```bash
pip install playwright
playwright install chromium
```

## Performance

| Show (50min, 1080p) | Segments | Total size | Wall time (single) |
|---------------------|----------|------------|---------------------|
| Per episode         | ~600-700 | ~800 MB    | ~5 min              |
| Full season (8 ep)  | ~5,000   | ~6.7 GB    | ~40-50 min          |

(Wall times assume 2 parallel workers + curl fallback; faster networks benefit less from
extra workers because of CDN rate limiting.)

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This tool is for personal use downloading content you have legitimate access to. Respect
copyright and the site's terms of service. The author is not responsible for misuse.
