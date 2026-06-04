#!/usr/bin/env python3
"""CLI: Download a single 4kvm.net video.

Usage:
    python scripts/download.py <4kvm_url> [--output DIR] [--auto-recover-ets]

Example:
    python scripts/download.py "https://www.4kvm.net/play/ch46zvt3r"
"""
import argparse
import sys
from pathlib import Path

# Add parent directory to path so we can import py4kvm
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from py4kvm import downloader, extractor, utils


def main():
    parser = argparse.ArgumentParser(
        description="Download a single video from 4kvm.net",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", help="The 4kvm.net play URL")
    parser.add_argument(
        "--output", "-o", default=".",
        help="Output directory (default: current dir)",
    )
    parser.add_argument(
        "--auto-recover-ets", action="store_true",
        help="Attempt automatic recovery of /ets/ base64 path failures (slower)",
    )
    parser.add_argument(
        "--keep-workdir", action="store_true",
        help="Don't delete the work directory on success (for debugging)",
    )
    args = parser.parse_args()

    # Pre-flight check
    missing = utils.check_dependencies()
    if missing:
        print("Missing dependencies:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    # Extract title for filename
    print(f"Fetching page: {args.url}")
    title = extractor.get_title(args.url)
    filename = utils.filename_from_title(title)
    output_path = Path(args.output) / filename
    print(f"Title: {title}")
    print(f"Output: {output_path}")

    # Extract m3u8
    print("Extracting m3u8 URL...")
    m3u8_url = extractor.get_m3u8_url(args.url)
    print(f"  {m3u8_url}")

    # Download
    print("Downloading...")
    stats = downloader.download_episode(
        m3u8_url, output_path, auto_recover_ets=args.auto_recover_ets,
    )

    if not args.keep_workdir:
        # The downloader already cleans up by default
        pass

    # Verify
    info = utils.probe(output_path)
    if info:
        print(f"\n✓ Done: {output_path}")
        print(f"  {info.resolution} {info.codec} {info.duration_minutes:.1f} min "
              f"({info.size/(1024*1024):.0f} MB)")
    else:
        print(f"\n! Done but ffprobe verification failed")
        print(f"  {output_path} ({output_path.stat().st_size/(1024*1024):.0f} MB)")


if __name__ == "__main__":
    main()
