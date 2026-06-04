"""Tests for the utils module — focus on filename derivation and m3u8 parsing."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from py4kvm import utils


class TestFilenameFromTitle(unittest.TestCase):
    """Filename derivation handles the site's various title formats."""

    def test_standard_format(self):
        """The most common format: '剧名 第一季 - 第N集 -4k影视'"""
        self.assertEqual(
            utils.filename_from_title("校园之外 第一季 - 第1集 -4k影视"),
            "校园之外_S01E01.mp4",
        )

    def test_with_colon(self):
        """Some shows use a colon: '剧名: 第X季 - 第Y集 -4k影视'"""
        self.assertEqual(
            utils.filename_from_title("无耻之徒: 第1季 - 第2集 -4k影视"),
            "无耻之徒_S01E02.mp4",
        )

    def test_two_digit_episode(self):
        """Two-digit episode numbers should zero-pad to 2 digits."""
        result = utils.filename_from_title("某剧 第二季 - 第12集 -4k影视")
        self.assertTrue(result.endswith("_S02E12.mp4"))

    def test_no_trailer(self):
        """Without the '-4k影视' trailer should still work."""
        self.assertEqual(
            utils.filename_from_title("校园之外 第一季 - 第5集"),
            "校园之外_S01E05.mp4",
        )

    def test_fallback_keeps_title(self):
        """If no pattern matches, fall back to a sanitized version of the title."""
        result = utils.filename_from_title("Some Random Show")
        # Should not crash, should produce something
        self.assertTrue(result.endswith(".mp4"))
        self.assertGreater(len(result), 4)


class TestM3u8SegmentUrls(unittest.TestCase):
    """m3u8 parsing should skip #EXT* lines and blanks."""

    SAMPLE = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:13
#EXTINF:9.333333,
https://example.com/seg0
#EXTINF:2.750000,
https://example.com/seg1
#EXTINF:3.958333,
https://example.com/seg2
"""

    def test_extracts_urls(self):
        urls = utils.m3u8_segment_urls(self.SAMPLE)
        self.assertEqual(len(urls), 3)
        self.assertEqual(urls[0], "https://example.com/seg0")
        self.assertEqual(urls[2], "https://example.com/seg2")

    def test_skips_blank_lines(self):
        text = "https://a.com/1\n\n\nhttps://a.com/2\n"
        urls = utils.m3u8_segment_urls(text)
        self.assertEqual(urls, ["https://a.com/1", "https://a.com/2"])

    def test_handles_empty(self):
        self.assertEqual(utils.m3u8_segment_urls(""), [])
        self.assertEqual(utils.m3u8_segment_urls("#EXTM3U\n"), [])


class TestCheckDependencies(unittest.TestCase):
    """Dependency check should report what's missing."""

    def test_returns_list(self):
        result = utils.check_dependencies()
        self.assertIsInstance(result, list)


class TestMakeSslContext(unittest.TestCase):
    """SSL context should disable verification (CDN uses various certs)."""

    def test_returns_context(self):
        ctx = utils.make_ssl_context()
        self.assertIsNotNone(ctx)
        # Should be a non-verifying context
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, 0)  # ssl.CERT_NONE


if __name__ == "__main__":
    unittest.main()
