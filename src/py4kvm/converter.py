"""Converter: Strip PNG wrappers and re-mux to MP4.

The site prepends a fake PNG header (~110 bytes) to each TS segment. The
header is structurally a valid PNG ending with the IEND chunk + 4-byte CRC.
The real TS data starts at the first sync byte (0x47) after the IEND.

After stripping, we concatenate all clean segments into a single .ts file
and re-mux to MP4 with ffmpeg's stream copy (no re-encoding, fast).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from . import utils


# Sync byte for MPEG-TS — every TS packet starts with 0x47
TS_SYNC_BYTE = 0x47

# PNG IEND marker: end-of-image chunk
PNG_IEND = b"IEND"


@dataclass
class ConversionStats:
    """Statistics from a clean+merge+convert run."""
    segment_count: int
    total_bytes: int
    output_size: int
    output_path: Path
    png_stripped_bytes: int = 0  # How much was removed across all segments


def strip_png_wrapper(data: bytes) -> bytes:
    """Strip the fake PNG header prepended to each TS segment.

    The header ends with `IEND` + 4-byte CRC (8 bytes). The real TS data
    starts at the first 0x47 byte at or after that position.
    """
    iend = data.find(PNG_IEND)
    if iend == -1:
        # No PNG header found — assume raw TS
        return data
    # Skip IEND (4 bytes) + CRC (4 bytes) = 8 bytes
    ts_start = iend + 8
    if ts_start < len(data) and data[ts_start] == TS_SYNC_BYTE:
        return data[ts_start:]
    # Search forward for the next sync byte
    for i in range(iend, min(iend + 200, len(data))):
        if data[i] == TS_SYNC_BYTE:
            return data[i:]
    # Fallback: return as-is and hope ffmpeg can sort it out
    return data


def strip_png_in_place(segment_path: Path, output_path: Optional[Path] = None) -> int:
    """Read a segment file, strip the PNG header, write to output.

    Returns the number of bytes stripped.
    """
    data = segment_path.read_bytes()
    clean = strip_png_wrapper(data)
    out = output_path or segment_path
    out.write_bytes(clean)
    return len(data) - len(clean)


def clean_and_merge(
    segment_paths: Iterable[Path],
    merged_path: Path,
    progress: bool = False,
) -> ConversionStats:
    """Strip PNG wrappers from all segments and concatenate to a single .ts.

    Args:
        segment_paths: Iterable of segment files in playback order.
        merged_path: Where to write the merged .ts file.
        progress: Whether to log progress.

    Returns:
        ConversionStats with the total size and bytes stripped.
    """
    total = 0
    stripped_total = 0
    count = 0
    with merged_path.open("wb") as out:
        for seg in segment_paths:
            data = seg.read_bytes()
            clean = strip_png_wrapper(data)
            out.write(clean)
            total += len(clean)
            stripped_total += len(data) - len(clean)
            count += 1
            if progress and count % 100 == 0:
                utils.log(f"  Cleaned {count} segments, {total/(1024*1024):.1f} MB so far")
    return ConversionStats(
        segment_count=count,
        total_bytes=total,
        output_size=total,
        output_path=merged_path,
        png_stripped_bytes=stripped_total,
    )


def to_mp4(
    merged_ts: Path,
    output_mp4: Path,
    audio_bsf: bool = True,
) -> ConversionStats:
    """Re-mux a merged .ts file to .mp4 with ffmpeg stream copy.

    Stream copy is fast (no re-encoding). The aac_adtstoasc bitstream filter
    is needed because raw HLS uses ADTS AAC framing while MP4 expects
    raw AAC. Without it, audio playback will fail.

    Returns:
        ConversionStats with the output size.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found in PATH — install with `brew install ffmpeg`")
    cmd = ["ffmpeg", "-i", str(merged_ts), "-c", "copy"]
    if audio_bsf:
        cmd.extend(["-bsf:a", "aac_adtstoasc"])
    cmd.extend([str(output_mp4), "-y"])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (rc={result.returncode}):\n{result.stderr[-1000:]}"
        )
    size = output_mp4.stat().st_size
    return ConversionStats(
        segment_count=0,  # Not known at this layer
        total_bytes=merged_ts.stat().st_size,
        output_size=size,
        output_path=output_mp4,
    )
