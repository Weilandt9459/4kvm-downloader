#!/usr/bin/env python3
"""scripts/download_video.py — Step 3: Download HLS video from 4kvm.net.

Reads from environment variables (set by the Agent or user):
  M3U8_URL    — The signed m3u8 URL from Step 2 (extract_m3u8.js)
  OUTPUT_FILE — The full path where the final .mp4 should be written

Example:
  export M3U8_URL="https://oss.douyinbit.com/m3u8/...m3u8"
  export OUTPUT_FILE="/Users/you/校园之外_S01E01.mp4"
  python3 scripts/download_video.py
"""
import os
import sys
import ssl
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request


# ---------- Configuration ----------

M3U8_URL = os.getenv("M3U8_URL")
OUTPUT_FILE = os.getenv("OUTPUT_FILE")

if not M3U8_URL or not OUTPUT_FILE:
    print("ERROR: Both M3U8_URL and OUTPUT_FILE environment variables are required.", file=sys.stderr)
    print("  Example:", file=sys.stderr)
    print("    export M3U8_URL='https://oss.douyinbit.com/m3u8/...m3u8'", file=sys.stderr)
    print("    export OUTPUT_FILE='/path/to/剧名_S01E01.mp4'", file=sys.stderr)
    print("    python3 scripts/download_video.py", file=sys.stderr)
    sys.exit(1)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(OUTPUT_FILE)), "video_download")
SEGMENTS_DIR = os.path.join(OUTPUT_DIR, "segments")
CLEAN_DIR = os.path.join(OUTPUT_DIR, "clean_segments")

# Allow override of parallelism via PARALLEL_WORKERS env var (default 2)
try:
    PARALLEL_WORKERS = int(os.getenv("PARALLEL_WORKERS", "2"))
except ValueError:
    PARALLEL_WORKERS = 2

# Allow skipping curl fallback via SKIP_CURL_FALLBACK=1
SKIP_CURL_FALLBACK = os.getenv("SKIP_CURL_FALLBACK", "0") == "1"

# ---------- HTTP setup ----------

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

os.makedirs(SEGMENTS_DIR, exist_ok=True)
os.makedirs(CLEAN_DIR, exist_ok=True)


# ---------- Pipeline stages ----------

def download_m3u8():
    print("[1/5] Downloading m3u8 playlist...")
    req = urllib.request.Request(M3U8_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
        content = resp.read().decode("utf-8")
    urls = [l.strip() for l in content.split("\n") if l.strip() and not l.startswith("#")]
    print(f"  Found {len(urls)} segments")
    return urls


def download_segment(args):
    """Download one segment with retries. Returns (idx, success, message)."""
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
                time.sleep(1 * (attempt + 1))
            else:
                return idx, False, str(e)


def download_segments(urls):
    print(f"\n[2/5] Downloading {len(urls)} segments ({PARALLEL_WORKERS} parallel — avoids CDN rate limit)...")
    tasks = [(url, os.path.join(SEGMENTS_DIR, f"{i:05d}.ts"), i + 1, len(urls)) for i, url in enumerate(urls)]
    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
        futures = {ex.submit(download_segment, t): t for t in tasks}
        for f in as_completed(futures):
            _, success, _ = f.result()
            if success:
                ok += 1
            else:
                fail += 1
    print(f"  Phase 1: {ok} ok, {fail} failed")
    return ok, fail


def curl_fallback(urls):
    """Single curl attempt per missing segment. Uses system proxy, bypasses rate limit."""
    if SKIP_CURL_FALLBACK:
        return [i for i, _ in enumerate(urls)
                if not os.path.exists(os.path.join(SEGMENTS_DIR, f"{i:05d}.ts"))
                or os.path.getsize(os.path.join(SEGMENTS_DIR, f"{i:05d}.ts")) == 0]
    missing = [i for i, _ in enumerate(urls)
               if not os.path.exists(os.path.join(SEGMENTS_DIR, f"{i:05d}.ts"))
               or os.path.getsize(os.path.join(SEGMENTS_DIR, f"{i:05d}.ts")) == 0]
    if not missing:
        return []
    print(f"  curl fallback: {len(missing)} missing segments")
    still_missing = []
    for i in missing:
        fp = os.path.join(SEGMENTS_DIR, f"{i:05d}.ts")
        try:
            r = subprocess.run(
                ["curl", "-sS", "-A", HEADERS["User-Agent"], "--max-time", "30",
                 urls[i], "-o", fp],
                capture_output=True, timeout=35,
            )
            if r.returncode != 0 or not os.path.exists(fp) or os.path.getsize(fp) == 0:
                still_missing.append(i)
        except Exception:
            still_missing.append(i)
    return still_missing


def strip_png_wrapper(data):
    """Strip the fake PNG header prepended to each TS segment."""
    iend = data.find(b"IEND")
    if iend == -1:
        return data
    ts_start = iend + 8  # 4 for IEND type + 4 CRC bytes
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
        if not os.path.exists(in_path):
            continue
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
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode == 0:
        size = os.path.getsize(OUTPUT_FILE)
        print(f"  Success! {OUTPUT_FILE} ({size/(1024*1024):.1f} MB)")
    else:
        print(f"  Error: {result.stderr[-500:]}", file=sys.stderr)
        sys.exit(1)


def main():
    urls = download_m3u8()
    ok, fail = download_segments(urls)

    if fail > 0:
        still_missing = curl_fallback(urls)
        if still_missing:
            print(f"\nERROR: {len(still_missing)} segments still missing: {still_missing[:10]}{'...' if len(still_missing) > 10 else ''}", file=sys.stderr)
            print("  Manual recovery needed — see SKILL.md 'Manual recovery for stubborn segments'", file=sys.stderr)
            sys.exit(1)

    clean_segments(urls)
    merged = concatenate(urls)
    convert(merged)
    print("\nDone!")


if __name__ == "__main__":
    main()
