"""Resource-limited PDF extraction subprocess; stdin PDF, stdout UTF-8 text."""
from __future__ import annotations

import io
import resource
import sys


def main() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    from pypdf import PdfReader

    raw = sys.stdin.buffer.read(512_001)
    if len(raw) > 512_000:
        raise ValueError("PDF exceeds input limit")
    reader = PdfReader(io.BytesIO(raw), strict=False)
    remaining = 2_000_000
    for index, page in enumerate(reader.pages):
        if index >= 100 or remaining <= 0:
            break
        text = (page.extract_text() or "")[:remaining]
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n\n")
        remaining -= len(text) + 2


if __name__ == "__main__":
    main()
