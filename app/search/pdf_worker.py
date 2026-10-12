"""Resource-limited PDF extraction subprocess; stdin PDF, stdout UTF-8 text.

argv[1] is the input byte limit (engine.PDF_MAX_BYTES).
"""
from __future__ import annotations

import io
import resource
import sys

MAX_PAGES = 300
MAX_CHARS = 2_000_000


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 16_000_000
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    from pypdf import PdfReader

    raw = sys.stdin.buffer.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("PDF exceeds input limit")
    reader = PdfReader(io.BytesIO(raw), strict=False)
    remaining = MAX_CHARS
    for index, page in enumerate(reader.pages):
        if index >= MAX_PAGES or remaining <= 0:
            break
        text = (page.extract_text() or "")[:remaining]
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n\n")
        remaining -= len(text) + 2


if __name__ == "__main__":
    main()
