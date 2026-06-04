"""Downloader: Fetch TS segments from a 4kvm.net m3u8 URL with self-healing.

Three-stage recovery:
1. Parallel download (2 workers) — gets 96-99% of segments
2. Single-pass curl fallback — recovers most rate-limited segments (uses system proxy)
3. Returns any still-missing segment indices for manual recovery

Why 2 workers?  The CDN rate-limits per-IP at the connection level. Going
beyond 2-4 concurrent connections triggers HTTP 404s on specific segments.
`curl` works around this because it uses the system proxy (HTTP_PROXY) which
routes through a different connection pool.
"""
from __future__ import annotations

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import utils


@dataclass
class DownloadStats:
    """Statistics from a download run."""
    total: int
    parallel_ok: int = 0
    parallel_failed: int = 0
    curl_recovered: int = 0
    still_missing: list[int] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.parallel_ok + self.curl_recovered) / self.total


def fetch_m3u8(m3u8_url: str) -> list[str]:
    """Fetch the m3u8 manifest and return the list of segment URLs.

    Raises FileNotFoundError if the m3u8 returns 404 (expired).
    """
    try:
        text = utils.fetch_text(m3u8_url, timeout=30)
    except Exception as e:
        if "404" in str(e) or "Not Found" in str(e):
            raise FileNotFoundError(
                f"m3u8 URL expired (404): {m3u8_url}\n"
                "Re-extract via Playwright (m3u8 URLs are valid for ~1 hour)."
            ) from e
        raise
    return utils.m3u8_segment_urls(text)


def _download_one(url: str, filepath: Path) -> bool:
    """Download a single segment, with up to 3 retries. Returns True on success."""
    if filepath.exists() and filepath.stat().st_size > 0:
        return True  # Already downloaded
    for attempt in range(3):
        try:
            data = utils.fetch_url(url, timeout=30)
            filepath.write_bytes(data)
            return True
        except Exception:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    return False


def _curl_one(url: str, filepath: Path, timeout: int = 30) -> bool:
    """Download a single segment via curl (uses system proxy, bypasses rate limit)."""
    try:
        r = subprocess.run(
            ["curl", "-sS", "-A", utils.USER_AGENT, "--max-time", str(timeout), url, "-o", str(filepath)],
            capture_output=True, timeout=timeout + 5,
        )
        return r.returncode == 0 and filepath.exists() and filepath.stat().st_size > 0
    except Exception:
        return False


def parallel_download(
    urls: list[str],
    seg_dir: Path,
    workers: int = 2,
) -> tuple[int, int]:
    """Download segments in parallel. Returns (success_count, fail_count).

    Args:
        urls: List of segment URLs (in order).
        seg_dir: Where to write segments (named 00000.ts, 00001.ts, ...).
        workers: Number of parallel workers. 2-4 is the sweet spot; more
            triggers connection-level rate limiting.
    """
    seg_dir.mkdir(parents=True, exist_ok=True)
    tasks = [(url, seg_dir / f"{i:05d}.ts") for i, url in enumerate(urls)]
    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_download_one, url, fp): (i, fp) for i, (url, fp) in enumerate(tasks)}
        for f in as_completed(futures):
            if f.result():
                ok += 1
            else:
                fail += 1
    return ok, fail


def curl_fallback(
    urls: list[str],
    seg_dir: Path,
) -> list[int]:
    """For segments still missing after parallel_download, try one curl pass.

    Returns the list of segment indices that are STILL missing after this.
    These are typically the persistent /ets/ base64 path failures that need
    manual recovery (see docs/architecture.md).
    """
    missing = [i for i, _ in enumerate(urls) if not (seg_dir / f"{i:05d}.ts").exists()
               or (seg_dir / f"{i:05d}.ts").stat().st_size == 0]
    if not missing:
        return []
    utils.log(f"  curl fallback: {len(missing)} missing segments")
    still_missing = []
    for i in missing:
        if _curl_one(urls[i], seg_dir / f"{i:05d}.ts"):
            continue
        still_missing.append(i)
    if still_missing:
        utils.log(f"  After curl: {len(still_missing)} still missing — manual recovery needed")
    else:
        utils.log(f"  After curl: all recovered")
    return still_missing


def download_episode(
    m3u8_url: str,
    output_path: str | Path,
    work_dir: Optional[Path] = None,
    auto_recover_ets: bool = False,
) -> DownloadStats:
    """High-level: download a full episode from a m3u8 URL to an MP4 file.

    Steps:
        1. Fetch m3u8
        2. Parallel download segments (2 workers)
        3. curl fallback for missing
        4. (Optional) Attempt to recover /ets/ base64 paths
        5. Strip PNG wrappers, concatenate to .ts
        6. ffmpeg re-mux to .mp4

    Args:
        m3u8_url: The signed m3u8 URL.
        output_path: Where to write the final .mp4.
        work_dir: Where to store intermediate files. Defaults to a sibling
            directory of output_path.
        auto_recover_ets: If True, attempt to recover /ets/ base64 paths by
            extracting the full URL from the redirect response. This is slow
            (one HEAD per missing segment) and may not always work.

    Returns:
        DownloadStats with the run statistics.

    Raises:
        FileNotFoundError: If the m3u8 URL returns 404 (expired).
        RuntimeError: If segments are still missing after all recovery attempts.
    """
    from . import converter  # Avoid circular import

    output_path = Path(output_path)
    work_dir = Path(work_dir) if work_dir else output_path.parent / "video_download"
    seg_dir = work_dir / "segments"
    merged = work_dir / "merged.ts"

    seg_dir.mkdir(parents=True, exist_ok=True)

    utils.log(f"Fetching m3u8...")
    urls = fetch_m3u8(m3u8_url)
    utils.log(f"  {len(urls)} segments")

    utils.log(f"Phase 1: parallel download (2 workers)...")
    ok, fail = parallel_download(urls, seg_dir, workers=2)
    utils.log(f"  {ok} ok, {fail} failed")

    utils.log(f"Phase 2: curl fallback...")
    missing = curl_fallback(urls, seg_dir)

    if missing and auto_recover_ets:
        utils.log(f"Phase 3: ets recovery for {len(missing)} segments...")
        from .batch import recover_ets_segments
        recovered = recover_ets_segments(missing, urls, seg_dir)
        missing = [i for i in missing if i not in recovered]
        utils.log(f"  Recovered {len(recovered)}, still missing: {len(missing)}")

    stats = DownloadStats(
        total=len(urls),
        parallel_ok=ok,
        parallel_failed=fail,
        curl_recovered=fail - len(missing),
        still_missing=missing,
    )

    if missing:
        raise RuntimeError(
            f"Cannot proceed: {len(missing)} segments missing: {missing[:10]}{'...' if len(missing) > 10 else ''}\n"
            f"Manual recovery needed — see docs/architecture.md"
        )

    utils.log(f"Cleaning + merging {len(urls)} segments...")
    segment_paths = [seg_dir / f"{i:05d}.ts" for i in range(len(urls))]
    conv_stats = converter.clean_and_merge(segment_paths, merged, progress=True)
    utils.log(f"  Merged: {conv_stats.total_bytes/(1024*1024):.1f} MB "
              f"(stripped {conv_stats.png_stripped_bytes/1024:.1f} KB of PNG headers)")

    utils.log(f"Converting to MP4: {output_path}")
    converter.to_mp4(merged, output_path)
    utils.log(f"  Done: {output_path} ({output_path.stat().st_size/(1024*1024):.1f} MB)")

    # Cleanup work dir on success
    import shutil
    shutil.rmtree(work_dir, ignore_errors=True)

    return stats
