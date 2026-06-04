# Architecture & Manual Recovery

This document explains the technical internals of the downloader and how to
recover from the few edge cases that require manual intervention.

## Pipeline overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  4kvm.net play URL                                                  │
│      e.g. https://www.4kvm.net/play/ch46zvt3r                       │
└─────────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  [extractor.py] Playwright (Node.js)                                │
│  • Launch headless Chromium with browser-like UA                    │
│  • Navigate to the play URL                                         │
│  • Intercept the WASM-generated /video/play?p=... API call          │
│  • Capture the m3u8 URL (signed, ~1h TTL)                           │
│  • Scrape sibling episode links from the DOM                        │
└─────────────────────────────────────────────────────────────────────┘
                  │
                  ▼ m3u8 URL
┌─────────────────────────────────────────────────────────────────────┐
│  [downloader.py] Phase 1: parallel download (2 workers)             │
│  • Worker pool fetches segments with 30s timeout, 3 retries          │
│  • 2 workers is the sweet spot — more triggers connection-level     │
│    rate limiting at the Tencent COS CDN (Layer 6)                    │
│  • Typical success rate: 96-99% of segments                         │
└─────────────────────────────────────────────────────────────────────┘
                  │
                  ▼ 1-3% still missing
┌─────────────────────────────────────────────────────────────────────┐
│  [downloader.py] Phase 2: curl single-pass fallback                  │
│  • For each missing segment, one curl attempt with 30s timeout      │
│  • curl uses the system proxy (HTTP_PROXY) which routes through a   │
│    different connection pool, bypassing the rate limit              │
│  • Typical recovery: 1-2% more                                      │
└─────────────────────────────────────────────────────────────────────┘
                  │
                  ▼ <1% still missing (usually /ets/ paths)
┌─────────────────────────────────────────────────────────────────────┐
│  [batch.py] Phase 3 (optional): /ets/ recovery                      │
│  • The /ets/ path format wraps the real URL in base64 + a redirect  │
│  • HEAD the /ets/ URL → 302/Found → extract Location or HTML href   │
│  • Download from the real URL                                       │
│  • Slow (one HEAD per missing segment); not enabled by default      │
└─────────────────────────────────────────────────────────────────────┘
                  │
                  ▼ all segments present
┌─────────────────────────────────────────────────────────────────────┐
│  [converter.py] Strip PNG wrapper                                   │
│  • Each .ts has a ~110-byte fake PNG header prepended               │
│  • Find "IEND" marker → skip 8 bytes (IEND + CRC)                  │
│  • Skip to first sync byte (0x47) — that's the real TS start        │
│  • Total stripped: typically 50-100 KB across 600-700 segments      │
└─────────────────────────────────────────────────────────────────────┘
                  │
                  ▼ clean TS segments
┌─────────────────────────────────────────────────────────────────────┐
│  [converter.py] Concatenate + ffmpeg re-mux                         │
│  • Concatenate all clean .ts files in order                         │
│  • ffmpeg -c copy -bsf:a aac_adtstoasc → .mp4                       │
│  • Stream copy (no re-encode), ~1 minute for 1GB                    │
│  • The aac_adtstoasc bitstream filter converts ADTS AAC (HLS) to    │
│    raw AAC (MP4) — without it, audio playback will fail            │
└─────────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Final .mp4 file (typically 700-1000 MB per 50min episode)          │
└─────────────────────────────────────────────────────────────────────┘
```

## Manual recovery for /ets/ segments

The `/ets/{timestamp}-{hash}/{base64}` path format wraps the real URL.
If the base64 in the m3u8 is truncated, the CDN's HEAD response includes the
full URL in an HTML `<a href="...">Found</a>` body.

### Step-by-step recovery

```bash
# 1. Find the missing segment index from the script output
#    e.g. "Still missing: [483, 559]"

# 2. Look at the m3u8 for that segment's URL
M3U8="https://oss.douyinbit.com/m3u8/.../....m3u8"
awk 'NR==967' "$M3U8"  # line = 2*index + 1

# 3. The URL will be a relative path like:
#    /ets/1780536395-69a826f0fa471518/aHR0cHM6Ly9zbnMtb3Blbi1xYy54aHNjZG4uY29t...

# 4. Prepend the CDN base and curl -v to see the redirect
FULL_URL="https://sns-open-qc.xhscdn.com/ets/..."
curl -v "$FULL_URL"
# → Server: tencent-cos
# → 302 Found
# → Location: https://sns-open-qc.xhscdn.com/professionalpc/...
#   (or the body contains <a href="...">Found</a>)

# 5. Download from the real URL
curl -o /path/to/segments/00483.ts "https://sns-open-qc.xhscdn.com/professionalpc/..."
```

The `batch.py:recover_ets_segments` function automates this for the
`--auto-recover-ets` flag.

## Why 2 workers?

The CDN rate-limits at the **connection level**, not the request level.
This means:
- 2 parallel connections: each gets a fair share
- 4+ connections: some connections start getting 404s on specific segments
- 8+ connections: many segments return 404 simultaneously
- 16+ connections: download effectively throttles to single-segment speed

`curl` works around this because it picks up the system proxy
(`HTTPS_PROXY`/`HTTP_PROXY` env vars), which routes through a different
connection pool on most systems. This is why the fallback is a
single-segment curl pass rather than another parallel batch.

## Why a fake PNG header?

The site uses a PNG wrapper as a low-stakes obfuscation layer:
- It confuses naive `ffmpeg -i playlist.m3u8` pipelines (ffmpeg doesn't
  recognize the format)
- It defeats video players that don't know to skip the header
- It looks like normal image data in casual inspection

The actual TS data starts at the next MPEG-TS sync byte (0x47) after the
PNG's IEND marker. The strip is:
- Find "IEND" in the bytes
- Skip 8 bytes (4 for IEND chunk type + 4 for CRC)
- Find the first 0x47 — that's the start of the first TS packet

## File size breakdown

For a 50-minute 1080p episode (~6 Mbps):
- ~600-700 TS segments, each 2-6 seconds of video
- Each segment: ~1.5-3 MB raw + ~110 bytes PNG header
- Total raw: ~900 MB
- Stripped headers: ~70 KB total
- Final MP4: ~800-1000 MB (depends on GOP structure and audio)
- ffmpeg re-mux speed: ~1 GB/sec (stream copy is essentially free)

## m3u8 lifetime

The signed m3u8 URL is generated client-side by the WASM module each
time the page loads. The signing includes a timestamp, so the URL is
valid for ~1 hour from generation. After that:
- HEAD and GET both return 404 from the m3u8 endpoint
- The individual segment URLs on `sns-open-qc.xhscdn.com` remain valid
  via CDN cache (much longer, possibly days)

This means: if your batch run is interrupted and the m3u8 expires,
re-run `extract_m3u8.js` to get a fresh URL. You don't need to
re-download segments that are already on disk.
