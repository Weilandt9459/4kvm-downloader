"""py4kvm: A robust downloader for 4kvm.net videos.

Defeats 8 layers of anti-scraping protection:
1. WASM-signed API
2. Extensionless segments
3. PNG-disguised segments
4. No-referrer policy
5. Cross-domain CDN
6. Connection-level rate limiting
7. HEAD 404 misdirection
8. Base64-encoded relative URLs

Public API:
    - downloader: Per-episode segment download
    - extractor:  Playwright-based m3u8 extraction
    - converter:  PNG strip + ffmpeg wrapper
    - batch:      Full-season orchestration
    - utils:      ffprobe wrapper
"""

from . import batch, converter, downloader, extractor, utils

__version__ = "1.0.0"
__all__ = ["batch", "converter", "downloader", "extractor", "utils", "__version__"]
