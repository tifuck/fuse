#!/usr/bin/env python3
"""Refresh the real-page snapshots in tests/fixtures/sites.

Manual tool — never run by the test suite. Downloads each MANIFEST.json url
to its snapshot file so fixtures can be updated (or new pages added) for
future versions. After refreshing, re-run the scrubbing suite and update
must_contain / must_not_contain markers if the pages changed:

    ./venv/bin/python tests/fixtures/sites/fetch_fixtures.py
    ./venv/bin/python -m unittest discover -s tests -p "test_real_*.py" -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

DIR = Path(__file__).resolve().parent
MANIFEST = json.loads((DIR / "MANIFEST.json").read_text(encoding="utf-8"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html, text/plain, application/json;q=0.8",
    "Accept-Language": "en",
}


def main() -> int:
    failures = 0
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        for entry in MANIFEST["fixtures"]:
            path = DIR / entry["file"]
            try:
                resp = client.get(entry["url"])
                resp.raise_for_status()
                path.write_bytes(resp.content)
                print(f"ok      {entry['name']:26s} {len(resp.content):>8d} bytes  {entry['url']}")
            except Exception as exc:
                failures += 1
                print(f"FAILED  {entry['name']:26s} {type(exc).__name__}: {exc}")
    print("\nNext: update markers if pages changed, then run the real-page suites.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
