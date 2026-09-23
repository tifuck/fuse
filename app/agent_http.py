"""Public connector endpoints: install script, binaries, agent WebSocket. No cookies."""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse

from .config import ROOT
from .hosts import _CODE_RE, public_origin
from .security import client_ip, limiter
from . import agent_hub

router = APIRouter()
logger = logging.getLogger(__name__)

DIST = ROOT / "agent" / "dist"
VERSION_FILE = ROOT / "agent" / "VERSION"
BINARIES = {
    "linux-amd64": "fuse-agent-linux-amd64",
    "linux-arm64": "fuse-agent-linux-arm64",
    "windows-amd64": "fuse-agent-windows-amd64.exe",
    "windows-arm64": "fuse-agent-windows-arm64.exe",
}
_SAFE_BIN = re.compile(
    r"^fuse-agent-(linux-(amd64|arm64)|windows-(amd64|arm64)\.exe)(\.sha256)?$"
)


def agent_version() -> str:
    try:
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
    return text.splitlines()[0].strip() or "0.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _origin() -> str:
    # Never derive the install base from the request Host header: a spoofed
    # Host would bake an attacker origin into the installer. Config only.
    return public_origin()


def manifest_payload() -> dict:
    origin = _origin()
    binaries = {}
    for key, name in BINARIES.items():
        path = DIST / name
        if not path.is_file():
            continue
        binaries[key] = {
            "path": f"/agent/dist/{name}",
            "url": f"{origin}/agent/dist/{name}" if origin else f"/agent/dist/{name}",
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
    return {"version": agent_version(), "binaries": binaries}


def install_script(code: str = "") -> str:
    origin = _origin()
    baked = code if _CODE_RE.match(code or "") else ""
    return f"""#!/bin/sh
set -eu
# Fuse connector installer. HTTPS only. Pairing code is single-use.
BASE="{origin}"
CODE="${{1:-{baked}}}"
FOREGROUND=0
for arg in "$@"; do
  case "$arg" in
    --foreground|--once) FOREGROUND=1 ;;
    fuse_*) CODE="$arg" ;;
  esac
done
if [ -z "$BASE" ]; then
  echo "fuse-agent: missing public URL" >&2
  exit 1
fi
if [ -z "$CODE" ]; then
  echo "usage: curl -fsSL $BASE/install.sh | sh -s -- fuse_XXXX" >&2
  echo "   or: curl -fsSL $BASE/i/fuse_XXXX | sh" >&2
  exit 1
fi
command -v curl >/dev/null 2>&1 || {{ echo "fuse-agent: curl is required" >&2; exit 1; }}
ARCH=$(uname -m)
case "$ARCH" in
  x86_64|amd64) SKU=linux-amd64 ;;
  aarch64|arm64) SKU=linux-arm64 ;;
  *) echo "fuse-agent: unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
NAME="fuse-agent-$SKU"
TMP="${{TMPDIR:-/tmp}}/fuse-agent.$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT INT HUP
echo "Downloading $NAME from $BASE ..."
curl -fsSL "$BASE/agent/dist/$NAME" -o "$TMP/fuse-agent"
SUM=$(curl -fsSL "$BASE/agent/dist/$NAME.sha256" 2>/dev/null || true)
if [ -z "$SUM" ]; then
  echo "fuse-agent: checksum unavailable" >&2
  exit 1
fi
EXPECT=$(echo "$SUM" | awk '{{print $1}}')
GOT=$(command -v sha256sum >/dev/null 2>&1 && sha256sum "$TMP/fuse-agent" | awk '{{print $1}}' || openssl dgst -sha256 "$TMP/fuse-agent" | awk '{{print $NF}}')
if [ -z "$EXPECT" ] || [ "$EXPECT" != "$GOT" ]; then
  echo "fuse-agent: checksum mismatch" >&2
  exit 1
fi
chmod 755 "$TMP/fuse-agent"
if [ "$FOREGROUND" = "1" ]; then
  exec "$TMP/fuse-agent" run --url "$BASE" --code "$CODE"
fi
exec "$TMP/fuse-agent" install --url "$BASE" --code "$CODE"
"""


def install_ps1(code: str = "") -> str:
    origin = _origin()
    baked = code if _CODE_RE.match(code or "") else ""
    return f"""# Fuse connector installer. HTTPS only. Pairing code is single-use.
$ErrorActionPreference = 'Stop'
$Base = '{origin}'
$Code = '{baked}'
foreach ($arg in $args) {{
  if ($arg -like 'fuse_*') {{ $Code = [string]$arg }}
}}
if (-not $Base) {{
  Write-Error 'fuse-agent: missing public URL'
  exit 1
}}
if (-not $Code) {{
  Write-Error "usage: irm $Base/install.ps1 | iex"
  Write-Error "   or: irm $Base/i/fuse_XXXX.ps1 | iex"
  exit 1
}}
$Arch = $env:PROCESSOR_ARCHITEW6432
if (-not $Arch) {{ $Arch = $env:PROCESSOR_ARCHITECTURE }}
switch ($Arch) {{
  'AMD64' {{ $Sku = 'windows-amd64' }}
  'ARM64' {{ $Sku = 'windows-arm64' }}
  default {{
    Write-Error "fuse-agent: unsupported architecture: $Arch"
    exit 1
  }}
}}
$Name = "fuse-agent-$Sku.exe"
$Tmp = Join-Path $env:TEMP ("fuse-agent-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Tmp | Out-Null
try {{
  $Exe = Join-Path $Tmp 'fuse-agent.exe'
  Write-Host "Downloading $Name from $Base ..."
  Invoke-WebRequest -UseBasicParsing -Uri "$Base/agent/dist/$Name" -OutFile $Exe
  try {{
    $SumResp = Invoke-WebRequest -UseBasicParsing -Uri "$Base/agent/dist/$Name.sha256"
  }} catch {{
    Write-Error 'fuse-agent: checksum unavailable'
    exit 1
  }}
  $Expect = (($SumResp.Content.ToString() -split '\\s+')[0]).ToLowerInvariant()
  if (-not $Expect) {{
    Write-Error 'fuse-agent: checksum unavailable'
    exit 1
  }}
  $Got = (Get-FileHash -Algorithm SHA256 -Path $Exe).Hash.ToLowerInvariant()
  if ($Expect -ne $Got) {{
    Write-Error 'fuse-agent: checksum mismatch'
    exit 1
  }}
  & $Exe install --url $Base --code $Code
  if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {{ exit $LASTEXITCODE }}
}} finally {{
  Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}}
"""


@router.get("/agent/v1/manifest")
async def manifest(request: Request):
    if not limiter.allow(f"agent-manifest:{client_ip(request)}", 30, 60):
        raise HTTPException(429, "Too many requests")
    return JSONResponse(manifest_payload(), headers={"Cache-Control": "no-store"})


@router.get("/agent/dist/{name}")
async def dist_file(name: str, request: Request):
    if not _SAFE_BIN.match(name):
        raise HTTPException(404, "not found")
    # Generous: whole fleets behind one NAT IP update at once.
    if not limiter.allow(f"agent-dist:{client_ip(request)}", 120, 60):
        raise HTTPException(429, "Too many requests")
    path = DIST / name
    if name.endswith(".sha256"):
        bin_path = DIST / name[:-7]
        if not bin_path.is_file():
            raise HTTPException(404, "not found")
        digest = _sha256(bin_path)
        return PlainTextResponse(f"{digest}  {bin_path.name}\n", media_type="text/plain")
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def _require_origin() -> str:
    origin = _origin()
    if not origin:
        raise HTTPException(503, "PUBLIC_BASE_URL is not configured")
    return origin


@router.get("/install.sh")
async def install_sh(request: Request):
    _require_origin()
    if not limiter.allow(f"agent-install:{client_ip(request)}", 30, 60):
        raise HTTPException(429, "Too many requests")
    return PlainTextResponse(install_script(""), media_type="text/plain; charset=utf-8")


@router.get("/install.ps1")
async def install_ps1_route(request: Request):
    _require_origin()
    if not limiter.allow(f"agent-install:{client_ip(request)}", 30, 60):
        raise HTTPException(429, "Too many requests")
    return PlainTextResponse(install_ps1(""), media_type="text/plain; charset=utf-8")


@router.get("/i/{code}")
async def install_with_code(code: str, request: Request):
    _require_origin()
    if not limiter.allow(f"agent-install:{client_ip(request)}", 30, 60):
        raise HTTPException(429, "Too many requests")
    raw = code or ""
    ps1 = False
    if raw.endswith(".ps1"):
        ps1 = True
        raw = raw[:-4]
    if not _CODE_RE.match(raw):
        raise HTTPException(404, "not found")
    body = install_ps1(raw) if ps1 else install_script(raw)
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")


@router.websocket("/agent/v1/connect")
async def connect(ws: WebSocket):
    ip = ""
    if ws.client:
        ip = ws.client.host or ""
    if ip and not limiter.allow(f"agent-ws:{ip}", 30, 60):
        await ws.close(code=4429)
        return
    await ws.accept()
    try:
        await agent_hub.handle_socket(ws)
    except Exception:
        logger.exception("agent socket failed")
        try:
            await ws.close()
        except Exception:
            pass
