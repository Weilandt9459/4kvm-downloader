#!/usr/bin/env python3
"""CLI: Download all episodes of a 4kvm.net show in one go.

Usage:
    python scripts/batch_download.py <4kvm_url> [--output DIR] [--max N] [--auto-recover-ets]

Example:
    python scripts/batch_download.py "https://www.4kvm.net/play/ch46zvt3r"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from py4kvm import batch, utils


def main():
    parser = argparse.ArgumentParser(
        description="Download all episodes of a 4kvm.net show",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", help="Any episode's 4kvm.net play URL (used to find siblings)")
    parser.add_argument(
        "--output", "-o", default=".",
        help="Output directory (default: current dir)",
    )
    parser.add_argument(
        "--max", "-n", type=int, default=None,
        help="Max number of episodes to download (default: all)",
    )
    parser.add_argument(
        "--auto-recover-ets", action="store_true",
        help="Attempt automatic recovery of /ets/ base64 path failures (slower)",
    )
    args = parser.parse_args()

    # Pre-flight check
    missing = utils.check_dependencies()
    if missing:
        print("Missing dependencies:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    summary = batch.download_season(
        start_url=args.url,
        output_dir=args.output,
        max_episodes=args.max,
        auto_recover_ets=args.auto_recover_ets,
    )

    # Exit code: 0 if all succeeded, 1 if any failed
    sys.exit(0 if not summary.failed else 1)


if __name__ == "__main__":
    main()
