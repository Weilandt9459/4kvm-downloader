"""Batch: Download multiple episodes of a show in one go.

Workflow:
1. Visit any episode's page; scrape all sibling episode links
2. For each episode (in order):
   a. Skip if .mp4 already exists
   b. Extract fresh m3u8 URL via Playwright
   c. Download segments (parallel + curl fallback)
   d. Convert to .mp4
3. Summary report

This module also handles recovery of /ets/ base64-encoded segment URLs.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import downloader, extractor, utils


@dataclass
class BatchResult:
    """Per-episode result from a batch run."""
    episode_num: int
    url: str
    output_path: Path
    success: bool
    skipped: bool = False
    error: Optional[str] = None
    size_mb: float = 0.0
    duration_min: float = 0.0


@dataclass
class BatchSummary:
    """Aggregate results from a batch run."""
    results: list[BatchResult] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed(self) -> list[BatchResult]:
        return [r for r in self.results if not r.success and not r.skipped]

    @property
    def total_size_mb(self) -> float:
        return sum(r.size_mb for r in self.results if r.success)

    def print_report(self) -> None:
        utils.log("=" * 60)
        utils.log("BATCH DOWNLOAD SUMMARY")
        utils.log("=" * 60)
        for r in self.results:
            if r.skipped:
                marker = "SKIP"
            elif r.success:
                marker = "OK  "
            else:
                marker = "FAIL"
            msg = f"  [{marker}] Episode {r.episode_num:02d}"
            if r.success:
                msg += f"  {r.size_mb:.0f} MB  {r.duration_min:.1f} min"
            elif r.error:
                msg += f"  {r.error}"
            utils.log(msg)
        utils.log(f"\n  {self.success_count}/{len(self.results)} succeeded, "
                  f"{self.total_size_mb/1024:.1f} GB total")


def recover_ets_segments(
    missing_indices: list[int],
    urls: list[str],
    seg_dir: Path,
    timeout: int = 30,
) -> set[int]:
    """Attempt to recover /ets/ base64-encoded segment URLs.

    The CDN returns a redirect with the full URL in an HTML `<a href="...">Found</a>`
    body. We extract that and download directly.

    Returns the set of indices that were successfully recovered.
    """
    recovered = set()
    for i in missing_indices:
        url = urls[i]
        if "/ets/" not in url:
            continue
        # Build the full URL
        if url.startswith("/"):
            full_url = f"https://sns-open-qc.xhscdn.com{url}"
        else:
            full_url = url
        try:
            # HEAD to get the redirect target
            r = subprocess.run(
                ["curl", "-sI", "-A", utils.USER_AGENT, "--max-time", str(timeout), full_url],
                capture_output=True, text=True, timeout=timeout + 5,
            )
            # Look for Location header (redirect)
            location_match = re.search(r"^location:\s*(.+)$", r.stdout, re.MULTILINE | re.IGNORECASE)
            if location_match:
                real_url = location_match.group(1).strip()
            else:
                # Look for <a href="..."> in the body
                m = re.search(r'href="([^"]+)"', r.stdout)
                if m:
                    real_url = m.group(1)
                else:
                    continue
            # Download from the real URL
            r = subprocess.run(
                ["curl", "-sS", "-A", utils.USER_AGENT, "--max-time", str(timeout), real_url,
                 "-o", str(seg_dir / f"{i:05d}.ts")],
                capture_output=True, timeout=timeout + 5,
            )
            if r.returncode == 0 and (seg_dir / f"{i:05d}.ts").exists() \
                    and (seg_dir / f"{i:05d}.ts").stat().st_size > 0:
                recovered.add(i)
        except Exception:
            continue
    return recovered


def download_season(
    start_url: str,
    output_dir: str | Path = ".",
    filename_template: str = "{name}_S{season:02d}E{episode:02d}.mp4",
    max_episodes: Optional[int] = None,
    auto_recover_ets: bool = False,
) -> BatchSummary:
    """Download a full season (or partial) starting from any episode's URL.

    Args:
        start_url: Any episode's play URL. Used to discover sibling episodes.
        output_dir: Where to write the .mp4 files.
        filename_template: Format string with {name}, {season}, {episode} placeholders.
        max_episodes: Stop after this many episodes (None = all).
        auto_recover_ets: Try to recover /ets/ base64 path failures automatically.

    Returns:
        BatchSummary with per-episode results.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    utils.log(f"Discovering episodes from: {start_url}")
    episodes = extractor.find_episodes(start_url)
    if not episodes:
        raise RuntimeError(f"No episode links found on {start_url}")
    if max_episodes:
        episodes = episodes[:max_episodes]
    utils.log(f"  Found {len(episodes)} episodes")

    summary = BatchSummary()
    for ep in episodes:
        ep_num = ep["num"]
        ep_url = ep["url"]

        utils.log(f"\n{'=' * 60}\nEPISODE {ep_num}\n{'=' * 60}")

        # Extract title for filename
        try:
            title = extractor.get_title(ep_url)
        except Exception as e:
            utils.log(f"  Failed to get title: {e}")
            summary.results.append(BatchResult(
                episode_num=ep_num, url=ep_url,
                output_path=output_dir, success=False, error=f"title extraction: {e}",
            ))
            continue

        filename = utils.filename_from_title(title)
        if "{name}" in filename_template:
            # Use the formatted filename
            m = re.match(r"^(?P<name>.+?)_S(?P<s>\d+)E(?P<e>\d+)\.mp4$", filename)
            if m:
                filename = filename_template.format(
                    name=m.group("name"),
                    season=int(m.group("s")),
                    episode=int(m.group("e")),
                )
        output_path = output_dir / filename

        # Skip if already exists and is large enough
        if output_path.exists() and output_path.stat().st_size > 100_000_000:
            info = utils.probe(output_path)
            utils.log(f"  Already exists: {output_path}")
            summary.results.append(BatchResult(
                episode_num=ep_num, url=ep_url, output_path=output_path,
                success=True, skipped=True,
                size_mb=info.size / (1024 * 1024) if info else 0,
                duration_min=info.duration_minutes if info else 0,
            ))
            continue

        # Extract m3u8 and download
        try:
            m3u8_url = extractor.get_m3u8_url(ep_url)
            stats = downloader.download_episode(
                m3u8_url, output_path, auto_recover_ets=auto_recover_ets,
            )
            info = utils.probe(output_path)
            summary.results.append(BatchResult(
                episode_num=ep_num, url=ep_url, output_path=output_path,
                success=True,
                size_mb=info.size / (1024 * 1024) if info else 0,
                duration_min=info.duration_minutes if info else 0,
            ))
        except Exception as e:
            utils.log(f"  FAILED: {e}")
            summary.results.append(BatchResult(
                episode_num=ep_num, url=ep_url, output_path=output_path,
                success=False, error=str(e),
            ))

    summary.print_report()
    return summary
