# Anti-scraping layers

The site uses **8 layers of anti-scraping protection**. This reference
documents each layer and the corresponding defeat strategy. See
`../SKILL.md` for the full workflow.

## Layer 1: WASM-signed API

**Mechanism:** `build_play_url()` in `nbmovie_wasm` (a WebAssembly module
loaded by the page) reads DOM meta tags and generates a time-sensitive
signed m3u8 URL.

**Defeat:** A real headless browser (Playwright/Chromium) executes the
WASM in a full browser context, intercepts the resulting `/video/play?p=...`
API call, and extracts the m3u8 URL from the JSON response.

**Why it works:** There's no way to call `build_play_url()` outside the
WASM sandbox. We have to run the actual code in the actual environment.

## Layer 2: Extensionless segments

**Mechanism:** CDN segment URLs have no `.ts` extension, breaking
`ffmpeg`'s HLS parser.

Example URL from m3u8:
```
https://sns-open-qc.xhscdn.com/professionalpc/104101uo320pe9sovku06h6pe180000003809qrnqeqlfo
```

**Defeat:** We don't use `ffmpeg` to download. We extract URLs from the
m3u8 manually, download each segment with Python/curl, then concatenate
and re-mux to MP4 with `ffmpeg -c copy`.

**Why it works:** `ffmpeg -c copy` on a single merged `.ts` file doesn't
care about the original segment URLs.

## Layer 3: PNG-disguised segments

**Mechanism:** Each `.ts` segment has a fake PNG header (~110 bytes)
prepended. The PNG is structurally valid: signature + IHDR + IDAT + IEND
chunks. This defeats naive players that sniff the magic bytes.

**Defeat:** Strip bytes before the first MPEG-TS sync byte (0x47) after
the `IEND` marker.

```python
def strip_png_wrapper(data: bytes) -> bytes:
    iend = data.find(b"IEND")
    if iend == -1:
        return data
    # Skip IEND chunk type (4 bytes) + CRC (4 bytes) = 8 bytes
    ts_start = iend + 8
    if ts_start < len(data) and data[ts_start] == 0x47:
        return data[ts_start:]
    # Fallback: search forward for the next sync byte
    for i in range(iend, min(iend + 200, len(data))):
        if data[i] == 0x47:
            return data[i:]
    return data
```

**Total stripped per episode:** ~50-100 KB across ~600-700 segments.

## Layer 4: No-referrer policy

**Mechanism:** The page sets `referrerPolicy: 'no-referrer'` on the
`<video>` element. The CDN's edge logic rejects requests that include a
`Referer` header.

**Defeat:** Simply omit the `Referer` header. The Python `urllib` default
doesn't include it. The bundled scripts don't either.

```python
HEADERS = {
    "User-Agent": "Mozilla/5.0 (...)",
    # NO Referer header!
}
```

**Why it works:** The CDN's anti-hotlink check looks at `Referer`, not the
`User-Agent`. As long as we don't send it, we're good.

## Layer 5: Cross-domain CDN

**Mechanism:** The m3u8 manifest is served from
`oss.douyinbit.com` (Alibaba OSS / 字节跳动), but the individual segments
are served from `sns-open-qc.xhscdn.com` (Xiaohongshu's CDN, hosted on
Tencent COS). This forces cross-domain access.

**Defeat:** Both domains accept the same cookies / signed URL pattern, so
once we have the m3u8, we can fetch the segments directly. The m3u8
URLs are already absolute (not relative), so no URL resolution needed.

**Why it works:** The site has a content partnership where they serve
m3u8s from one CDN but the actual content from another. The m3u8
contains absolute URLs that work across domains.

## Layer 6: Connection-level rate limiting

**Mechanism:** The CDN (Tencent COS) rate-limits at the **TCP connection
level**, not the request level. Going beyond 2-4 concurrent connections
from the same IP triggers HTTP 404s on specific segments.

**Empirically observed:**
- 2 workers: ~96-99% of segments succeed
- 4 workers: ~90-95% succeed
- 8+ workers: ~50-80% succeed, segments return 404 randomly
- 16+ workers: effectively throttled to single-segment speed

**Defeat:**

1. **Default to 2 parallel workers** in the main download phase.
2. **After parallel**, run a **single-pass curl fallback** for any
   missing segments. `curl` uses the system `HTTPS_PROXY` env var
   which routes through a different connection pool, bypassing the
   rate limit.

**Why it works:** The CDN counts connections per IP. The system proxy
either connects from a different IP or has its own connection pool.
Python `urllib` doesn't pick up the system proxy by default.

## Layer 7: HEAD 404 misdirection

**Mechanism:** The m3u8 CDN (`oss.douyinbit.com`) returns HTTP 404 to
`HEAD` requests, even when `GET` works perfectly.

**Defeat:** Always use `GET` (`curl -s` or `curl -L`) when checking
m3u8 liveness. Never use `curl -I` (HEAD).

```bash
# BAD — returns 404 even when file exists
curl -I https://oss.douyinbit.com/m3u8/abc.m3u8
# → HTTP/2 404

# GOOD — returns 200
curl -s https://oss.douyinbit.com/m3u8/abc.m3u8
# → #EXTM3U
# → #EXT-X-VERSION:3
# → ...
```

**Why it works:** The CDN is configured to deny HEAD on signed URLs
(probably to prevent probing). The 404 on HEAD doesn't reflect actual
availability.

## Layer 8: Base64-encoded relative URLs

**Mechanism:** Some segments (typically ~1-2% of an episode) are served
from URLs that look like:
```
/ets/1780536395-69a826f0fa471518/aHR0cHM6Ly9zbnMtb3Blbi1xYy54aHNjZG4uY29tL3Byb2Zlc3Npb25hbHBjLzEwNDEwMXVvMzIwcGc4MnNsa3UwNmlncnNxMDAwMDAwMDZzMDZhNzRjamV0Mzg
```

This is a Tencent COS redirect. The base64 part decodes to a URL on
`sns-open-qc.xhscdn.com`. The base64 in the m3u8 may be **truncated** —
the actual URL has 4 more characters at the end.

**Defeat:**

1. Construct the full URL: `https://sns-open-qc.xhscdn.com{relative_path}`
2. Send a HEAD request — the CDN returns a 302 redirect to the actual URL
3. The redirect target is in either the `Location` header OR an HTML
   body like `<a href="https://sns-open-qc.xhscdn.com/professionalpc/...">Found</a>`
4. Use that URL to download

**Why it works:** The CDN's redirect response includes the real URL,
which is the actual segment path on the CDN. The `ets/` prefix is just
a redirect gateway.

---

## How the layers compose

These 8 layers are independent. Each one would be enough to stop
a naive scraper. The site stacks them so a single misstep causes failure.

| # | Layer | Without defeat, you get |
|---|-------|------------------------|
| 1 | WASM signing | No m3u8 URL at all (browser-only) |
| 2 | Extensionless | ffmpeg refuses to download |
| 3 | PNG wrapper | ffmpeg sees garbage data, errors |
| 4 | Referrer | CDN returns 403 for all segments |
| 5 | Cross-domain | m3u8 fetch OK but segments fail |
| 6 | Rate limit | 50% of segments timeout/fail |
| 7 | HEAD 404 | CI liveness check falsely reports expired m3u8 |
| 8 | Base64 /ets/ | 1-2% of segments unreachable |

Defeat them all, and you get a clean download.

## See also

- `../SKILL.md` — main workflow
- `../assets/example_output.json` — sample successful run
- Tencent COS rate-limit behavior: see [their docs](https://www.tencentcloud.com/document/product/436/30925)
