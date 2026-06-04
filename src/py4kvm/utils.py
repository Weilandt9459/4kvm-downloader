"""Utilities: HTTP helpers, ffprobe wrapper, filename derivation."""
from __future__ import annotations

import json
import re
import shutil
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    # Critical: no Referer header — Layer 4 anti-referrer
}


def make_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that doesn't verify certificates (CDN uses various certs)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_url(url: str, timeout: int = 30) -> bytes:
    """GET a URL and return the body. Uses the no-Referer policy (Layer 4)."""
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    ctx = make_ssl_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def fetch_text(url: str, timeout: int = 30) -> str:
    """GET a URL and return the body decoded as UTF-8."""
    return fetch_url(url, timeout).decode("utf-8")


def filename_from_title(title: str) -> str:
    """Convert a page title like '剧名 第一季 - 第1集 -4k影视' to '剧名_S01E01.mp4'.

    Handles several formats observed in the wild:
      - "剧名 第一季 - 第1集 -4k影视"
      - "剧名: 第1季 - 第2集 -4k影视"
      - "剧名 第一季 第3集 -4k影视"
      - "剧名 第5集" (no season → defaults to S01)

    The site uses both ASCII digits ("第1集") and Chinese numerals ("第一集").
    """
    # Strip the '-4k影视' suffix
    title = re.sub(r"-4k影视\s*$", "", title).strip()

    # Convert Chinese numerals to Arabic digits (一→1, 二→2, etc.) for the
    # season/episode match. We do a simple char-by-char replacement; compound
    # forms like 二十 are rare in episode numbers but if present, 十 won't
    # translate (and \d won't match it) — those need manual rename.
    chinese_to_arabic = {
        "零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
        "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
    }
    normalized = "".join(chinese_to_arabic.get(c, c) for c in title)

    season = None
    episode = None
    season_m = re.search(r"第(\d+)季", normalized)
    if season_m:
        season = int(season_m.group(1))
    episode_m = re.search(r"第(\d+)集", normalized)
    if episode_m:
        episode = int(episode_m.group(1))

    if episode is not None:
        # Use the position from the ORIGINAL (un-normalized) title for the
        # name boundary, so we don't cut the wrong character.
        first_match = season_m or episode_m
        # Calculate equivalent position in original title
        original_pos = sum(
            len(c.encode("utf-8")) if i < first_match.start() else 0
            for i, c in enumerate(normalized)
        )
        # The above is approximate; simpler: find the 第 in original title
        # that matches what the regex matched.
        original_die = title.find("第", max(0, first_match.start() - 2))
        if original_die == -1:
            original_die = first_match.start()
        name = title[:original_die].strip().rstrip(" :-_")
        if season is None:
            season = 1
        return f"{name}_S{season:02d}E{episode:02d}.mp4"

    # Fallback: just sanitize the title
    safe = re.sub(r"[^\w\s\-一-鿿]", "", title).strip()
    return f"{safe}.mp4"


def m3u8_segment_urls(m3u8_text: str) -> list[str]:
    """Parse an m3u8 manifest and return the list of segment URLs.

    Skips #EXT* lines and empty lines. Relative URLs are resolved against
    the m3u8 base URL (caller must pre-resolve if needed)."""
    urls = []
    for line in m3u8_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


@dataclass
class VideoInfo:
    """Result from ffprobe."""
    width: int
    height: int
    codec: str
    duration: float  # seconds
    size: int        # bytes

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def duration_minutes(self) -> float:
        return self.duration / 60


def probe(path: str | Path) -> Optional[VideoInfo]:
    """Run ffprobe and return a VideoInfo, or None if probe fails.

    Requires `ffprobe` in PATH."""
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found in PATH — install with `brew install ffmpeg`")
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not data.get("streams") or not data.get("format"):
        return None
    stream = data["streams"][0]
    fmt = data["format"]
    return VideoInfo(
        width=stream.get("width", 0),
        height=stream.get("height", 0),
        codec=stream.get("codec_name", "unknown"),
        duration=float(fmt.get("duration", 0)),
        size=int(fmt.get("size", 0)),
    )


def log(msg: str) -> None:
    """Log a message with a timestamp, flushed immediately (for background runs)."""
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def check_dependencies() -> list[str]:
    """Return a list of missing dependencies (empty if all OK)."""
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg (install: brew install ffmpeg)")
    if not shutil.which("ffprobe"):
        missing.append("ffprobe (install: brew install ffmpeg)")
    if not shutil.which("node"):
        missing.append("node (install: https://nodejs.org)")
    if not shutil.which("curl"):
        missing.append("curl")
    try:
        import playwright  # noqa: F401
    except ImportError:
        missing.append("playwright (install: pip install playwright && playwright install chromium)")
    return missing
