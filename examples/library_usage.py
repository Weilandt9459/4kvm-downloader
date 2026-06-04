"""Examples of using py4kvm as a Python library.

Run with: PYTHONPATH=src python examples/library_usage.py
"""
from pathlib import Path
import sys

# Make sure the package is importable when running directly
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from py4kvm import batch, converter, downloader, extractor, utils


def example_single_video():
    """Download a single video."""
    print("=" * 60)
    print("Example 1: Download a single video")
    print("=" * 60)

    url = "https://www.4kvm.net/play/ch46zvt3r"

    # Extract the m3u8 URL
    m3u8 = extractor.get_m3u8_url(url)
    print(f"m3u8: {m3u8}")

    # Derive a filename from the page title
    title = extractor.get_title(url)
    filename = utils.filename_from_title(title)
    print(f"Output: {filename}")

    # Download
    stats = downloader.download_episode(m3u8, filename)
    print(f"Success rate: {stats.success_rate * 100:.1f}%")

    # Verify
    info = utils.probe(filename)
    if info:
        print(f"  {info.resolution} {info.codec} {info.duration_minutes:.1f} min")


def example_inspect_page():
    """Inspect a page's quality options and episode list without downloading."""
    print("\n" + "=" * 60)
    print("Example 2: Inspect a page")
    print("=" * 60)

    url = "https://www.4kvm.net/play/ch46zvt3r"
    result = extractor.extract_page(url)

    print(f"Title: {result.title}")
    print(f"\nQuality options:")
    for q in result.quality_urls:
        status = "🔒 VIP" if q.locked else "✓"
        print(f"  {status} {q.title} ({q.bitrate} kbps) - {q.description}")

    print(f"\nEpisodes found: {len(result.episode_links)}")
    for ep in result.episode_links:
        print(f"  Episode {ep['num']}: {ep['url']}")


def example_download_specific_segments():
    """Advanced: download only specific segments (e.g. for testing)."""
    print("\n" + "=" * 60)
    print("Example 3: Download specific segments")
    print("=" * 60)

    url = "https://www.4kvm.net/play/ch46zvt3r"
    m3u8 = extractor.get_m3u8_url(url)
    urls = downloader.fetch_m3u8(m3u8)
    print(f"Total segments: {len(urls)}")

    # Download just the first 5 to verify everything works
    from pathlib import Path
    seg_dir = Path("/tmp/test_segments")
    seg_dir.mkdir(exist_ok=True)
    test_urls = urls[:5]
    ok, fail = downloader.parallel_download(test_urls, seg_dir, workers=2)
    print(f"Downloaded: {ok} ok, {fail} failed")
    print(f"Files: {sorted(p.name for p in seg_dir.glob('*.ts'))}")


def example_batch():
    """Download a full season."""
    print("\n" + "=" * 60)
    print("Example 4: Download a full season")
    print("=" * 60)

    url = "https://www.4kvm.net/play/ch46zvt3r"
    summary = batch.download_season(url, output_dir="./downloads", max_episodes=3)
    # max_episodes=3 limits to 3 episodes for this demo; remove for full season


def example_manual_recovery():
    """If a few segments persistently fail, recover them via the /ets/ method."""
    print("\n" + "=" * 60)
    print("Example 5: Manual recovery of /ets/ segments")
    print("=" * 60)

    # Suppose these are the missing segment indices
    missing = [114, 525]
    urls = []  # The full list of segment URLs (from m3u8)
    # seg_dir = Path("video_download/segments")

    # recovered = batch.recover_ets_segments(missing, urls, seg_dir)
    # print(f"Recovered: {recovered}")
    print("(skipped — needs a real m3u8 URL list)")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("which", nargs="?", default="1",
                   help="Which example to run (1-5)")
    args = p.parse_args()

    examples = {
        "1": example_single_video,
        "2": example_inspect_page,
        "3": example_download_specific_segments,
        "4": example_batch,
        "5": example_manual_recovery,
    }
    examples.get(args.which, example_single_video)()
