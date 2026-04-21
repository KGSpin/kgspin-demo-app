"""Network-safety utilities for landers (VP Security Mandate 4.1).

Every outbound download enforces a maximum body-size limit so a
malicious or misbehaving source can't fill the local disk. The
current default is 50 MiB per artifact — enough for a full SEC 10-K
(typically 5-15 MiB) with a 3x safety margin, small enough that an
accidental full-feed dump is caught quickly.
"""

from __future__ import annotations


class DownloadTooLargeError(Exception):
    """Raised when an in-progress download exceeds ``MAX_ARTIFACT_BYTES``.

    Landers catch this, abort the download, do NOT write the partial
    file to disk, and exit with a non-zero status + structured stderr.
    """

    def __init__(self, source_url: str, limit_bytes: int) -> None:
        self.source_url = source_url
        self.limit_bytes = limit_bytes
        super().__init__(
            f"Download from {source_url} exceeded {limit_bytes:,} bytes; aborted."
        )


MAX_ARTIFACT_BYTES: int = 50 * 1024 * 1024  # 50 MiB

# Chunk size for streaming reads. Kept small so the size check fires
# promptly on oversized responses, rather than reading multi-MiB
# buffers past the limit.
STREAM_CHUNK_BYTES: int = 64 * 1024  # 64 KiB
