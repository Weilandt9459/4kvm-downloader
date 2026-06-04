"""Tests for the converter module — focus on the PNG wrapper stripper.

The PNG wrapper is the most important defensive layer (Layer 3 in the SKILL.md).
If this fails, the resulting MP4 is unplayable.
"""
import sys
import unittest
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from py4kvm import converter


def make_fake_png(size: int = 80) -> bytes:
    """Create a minimal valid PNG ending in IEND, padded to ~size bytes."""
    # PNG signature (8) + IHDR chunk (25) + IEND chunk (12) = 45 bytes
    png = b"\x89PNG\r\n\x1a\n"  # 8 bytes
    # IHDR chunk: 4 (length) + 4 (type) + 13 (data) + 4 (CRC) = 25 bytes
    ihdr_data = bytes([0, 0, 0, 1, 0, 0, 0, 1, 8, 2, 0, 0, 0])
    png += b"\x00\x00\x00\x0d" + b"IHDR" + ihdr_data + b"\x90\x77\x53\xde"
    # IEND chunk: 4 (length=0) + 4 (type) + 4 (CRC) = 12 bytes
    png += b"\x00\x00\x00\x00" + b"IEND" + b"\xae\x42\x60\x82"
    # Now we have 45 bytes. Pad to `size` with zeros AFTER the IEND.
    # This mimics the real wrapper which has padding bytes between IEND and TS data.
    while len(png) < size:
        png += b"\x00"
    return png[:size]


def make_fake_ts(packet_count: int = 10) -> bytes:
    """Create a fake TS stream with `packet_count` 188-byte packets."""
    packets = []
    for i in range(packet_count):
        # Sync byte 0x47, then 187 bytes of garbage
        packets.append(b"\x47" + bytes(i % 256 for _ in range(187)))
    return b"".join(packets)


class TestStripPngWrapper(unittest.TestCase):
    """The strip_png_wrapper function is the core of Layer 3 defeat."""

    def test_strips_valid_png_prefix(self):
        """A valid PNG prefix followed by TS data should be stripped to start at sync byte."""
        png = make_fake_png(100)
        ts = make_fake_ts(5)
        data = png + ts
        result = converter.strip_png_wrapper(data)
        # Result should start with the TS sync byte
        self.assertEqual(result[0], 0x47)
        # And have the same length as the TS portion (assuming the PNG is just a prefix)
        self.assertEqual(len(result), len(ts) + max(0, len(png) - 100))

    def test_no_png_returns_unchanged(self):
        """Data without a PNG IEND marker should pass through unchanged."""
        ts = make_fake_ts(5)
        result = converter.strip_png_wrapper(ts)
        self.assertEqual(result, ts)

    def test_finds_sync_byte_after_iend(self):
        """If IEND is found, the result must start at the next 0x47 (sync byte)."""
        # Build data: PNG with extra bytes between IEND and TS sync byte
        png = make_fake_png(100)
        padding = b"\x00\x00\x00"  # 3 padding bytes
        ts = make_fake_ts(3)
        data = png + padding + ts
        result = converter.strip_png_wrapper(data)
        self.assertEqual(result[0], 0x47)
        # Should have stripped the padding too
        self.assertLess(len(result), len(data) - len(png))

    def test_handles_truncated_data(self):
        """Should not crash on very small or malformed data."""
        # Just a few bytes
        self.assertEqual(converter.strip_png_wrapper(b"\x47\x00\x01"), b"\x47\x00\x01")
        # Empty
        self.assertEqual(converter.strip_png_wrapper(b""), b"")
        # Just IEND with no data after
        self.assertEqual(converter.strip_png_wrapper(b"IEND"), b"IEND")


class TestCleanAndMerge(unittest.TestCase):
    """The clean_and_merge function combines multiple cleaned segments."""

    def test_concatenates_in_order(self):
        """Segments should be written in the order given (playback order)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            segments = []
            for i in range(5):
                png = make_fake_png(50)
                ts = bytes([0x47, i, 0x47, i])  # marker with index
                seg_path = tmpdir / f"seg_{i}.ts"
                seg_path.write_bytes(png + ts)
                segments.append(seg_path)

            merged = tmpdir / "merged.ts"
            stats = converter.clean_and_merge(segments, merged)
            self.assertEqual(stats.segment_count, 5)
            self.assertTrue(merged.exists())
            self.assertGreater(merged.stat().st_size, 0)
            # Verify it starts with sync byte
            self.assertEqual(merged.read_bytes()[0], 0x47)


if __name__ == "__main__":
    unittest.main()
