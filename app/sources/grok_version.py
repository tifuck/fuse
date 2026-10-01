"""Weekly, unauthenticated stable-channel checks used by the official installer."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time

import httpx

from ..config import ROOT, settings

logger = logging.getLogger(__name__)
WEEK = 7 * 24 * 60 * 60
RETRY = 60 * 60
URLS = (
    "https://x.ai/cli/stable",
    "https://storage.googleapis.com/grok-build-public-artifacts/cli/stable",
)
CACHE_PATH = ROOT / "data" / "grok-client-version.json"
_version: str | None = None
_next_check = 0.0
_loaded = False
_lock = asyncio.Lock()


def _valid(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", value) is not None


def _load() -> None:
    global _loaded, _version, _next_check
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(CACHE_PATH.read_text())
        if not isinstance(data, dict) or not _valid(data.get("version")):
            return
        _version = data["version"]
        checked = float(data.get("checked_at", 0))
        now = time.time()
        if math.isfinite(checked) and 0 < checked <= now:
            _next_check = checked + WEEK
    except (OSError, ValueError, TypeError):
        pass


def current_version() -> str:
    if not settings.grok_client_version_auto_refresh:
        return settings.grok_client_version
    _load()
    return _version or settings.grok_client_version


async def refresh_due() -> None:
    global _version, _next_check
    if not settings.grok_client_version_auto_refresh:
        return
    _load()
    async with _lock:
        now = time.time()
        if now < _next_check:
            return
        _next_check = now + RETRY
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for url in URLS:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    version = response.text.strip()
                    if not _valid(version):
                        raise ValueError("invalid stable version")
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning("Grok version check failed at %s: %s", url, exc)
                    continue
                previous = current_version()
                _version = version
                # Atomic replacement keeps the previous cache intact on failure.
                try:
                    temp = CACHE_PATH.with_suffix(".tmp")
                    temp.write_text(json.dumps({"version": version, "checked_at": now}) + "\n")
                    temp.replace(CACHE_PATH)
                except OSError:
                    logger.exception("Could not persist Grok client version")
                else:
                    _next_check = now + WEEK
                if previous != version:
                    logger.info("Grok client version updated: %s -> %s", previous, version)
                return
