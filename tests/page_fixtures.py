"""Loader for real-page fixtures in tests/fixtures/sites.

Fixtures are static downloads of real websites. Tests never fetch the network;
pipeline tests serve these files through a fake httpx client instead.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-pagefix-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

SITES_DIR = Path(__file__).resolve().parent / "fixtures" / "sites"
MANIFEST_PATH = SITES_DIR / "MANIFEST.json"

with MANIFEST_PATH.open(encoding="utf-8") as fh:
    MANIFEST = json.load(fh)

FIXTURES: list[dict] = MANIFEST["fixtures"]
BY_NAME: dict[str, dict] = {f["name"]: f for f in FIXTURES}

# Signatures that must never appear in extracted text of any fixture.
# HTML/script/CSS plumbing is noise for the model no matter the page.
GLOBAL_JUNK = [
    "<script",
    "<style",
    "</html>",
    "<meta ",
    "<!--",
    "@media",
    "@font-face",
    "!important",
    "display:none",
    "background-color",
    "stylesheet",
    "application/ld+json",
    "document.createElement",
    "addEventListener",
    "window.dataLayer",
    "=>{",
    "}catch(",
]

# Raw markup leakage. Skipped for fixtures whose legit code samples may show
# HTML attribute syntax inside <pre><code> blocks.
MARKUP_JUNK = [
    "<div",
    'class="',
    'href="',
    "</p>",
]


def raw_page(name: str) -> str:
    entry = BY_NAME[name]
    return (SITES_DIR / entry["file"]).read_text(encoding="utf-8", errors="replace")


def extract_of(name: str):
    from app.search.extract import extract_page

    entry = BY_NAME[name]
    raw = raw_page(name)
    return extract_page(raw, content_type=entry["content_type"], url=entry["url"])
