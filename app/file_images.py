"""Bounded raster decoding in a disposable, resource-limited interpreter."""
from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
import sys
import weakref

MAX_SOURCE = 20 * 1024 * 1024
MAX_OUTPUT = 5 * 1024 * 1024
# An opaque PNG past this size is also tried as JPEG (see _decode).
JPEG_FROM = 256 * 1024
MAX_PIXELS = 40_000_000
DECODE_CONCURRENCY = 3
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".ico", ".apng"}

# One interpreter per decode, each holding up to 1 GiB of address space: cap
# how many may exist at once (per event loop — the semaphore binds to its loop).
_decode_gates: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _decode_gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _decode_gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(DECODE_CONCURRENCY)
        _decode_gates[loop] = gate
    return gate


def image_magic(data: bytes) -> bool:
    return (data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+", b"\x00\x00\x01\x00"))
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


async def decode_image(data: bytes, frame: int = 0, *, max_side: int = 2048, max_bytes: int = MAX_OUTPUT) -> dict:
    if len(data) > MAX_SOURCE:
        raise ValueError("image source exceeds 20 MiB")
    if not image_magic(data):
        raise ValueError("unsupported image format (AVIF and PDF are not supported)")
    if type(frame) is not int or frame < 0:
        raise ValueError("frame must be a zero-based non-negative integer")
    async with _decode_gate():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).resolve()), str(frame), str(int(max_side)), str(int(max_bytes)),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(data), 15)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
    if proc.returncode or len(stdout) > max(MAX_OUTPUT, max_bytes) * 2:
        raise ValueError("image decoding failed or exceeded resource limits")
    result = json.loads(stdout)
    if "error" in result:
        raise ValueError(result["error"])
    return result


def _decode(data: bytes, frame: int, max_side: int = 2048, max_bytes: int = MAX_OUTPUT) -> dict:
    import warnings
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    with Image.open(io.BytesIO(data)) as source:
        fmt = source.format
        if fmt not in {"PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF", "ICO"}:
            raise ValueError("unsupported image format")
        frames = getattr(source, "n_frames", 1)
        if frame >= frames:
            raise ValueError("frame is outside the image's frame range")
        if source.width * source.height > MAX_PIXELS:
            raise ValueError("image exceeds 40 megapixels")
        # Sequential seeks preserve GIF/APNG disposal and blend composition.
        for index in range(frame + 1):
            source.seek(index)
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("image exceeds 40 megapixels")
            source.load()
        oriented = ImageOps.exif_transpose(source)
        width, height = oriented.size
        oriented.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
        converted = oriented.convert(mode)
        clean = Image.new(mode, converted.size)
        clean.paste(converted)
        output = io.BytesIO()
        clean.save(output, format="PNG")
        mime = "image/png"
        # Rendered art and photos barely compress as PNG (a 1280×720 game
        # frame is ~1.5 MB) and the transcript resends every image each tool
        # round. Pixels, not bytes, set a model's image tokens, so a JPEG
        # without chroma subsampling costs the same for a quarter of the
        # bytes. Flat UI and text stay PNG, which is smaller there anyway.
        if output.tell() > JPEG_FROM and (mode == "RGB" or clean.getchannel("A").getextrema()[0] == 255):
            jpeg = io.BytesIO()
            clean.convert("RGB").save(jpeg, format="JPEG", quality=90, subsampling=0)
            if jpeg.tell() * 2 <= output.tell():
                output, mime = jpeg, "image/jpeg"
        if output.tell() > max_bytes:
            background = Image.new("RGB", clean.size, "white")
            background.paste(clean, mask=clean.getchannel("A") if mode == "RGBA" else None)
            output = io.BytesIO()
            background.save(output, format="JPEG", quality=85)
            mime = "image/jpeg"
            # Shrink by a fifth until it fits, as Cursor's SDK does for tool images.
            while output.tell() > max_bytes and min(background.size) > 16:
                background = background.resize(
                    (max(8, round(background.width * 0.8)), max(8, round(background.height * 0.8))),
                    Image.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                background.save(output, format="JPEG", quality=85)
            clean = background
        if output.tell() > max_bytes:
            raise ValueError("decoded image exceeds the size limit")
        return {"mime": mime, "data": base64.b64encode(output.getvalue()).decode("ascii"),
                "width": clean.width, "height": clean.height,
                "original_width": width, "original_height": height,
                "format": "APNG" if fmt == "PNG" and frames > 1 else fmt,
                "frame": frame, "frames": frames}


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        payload = sys.stdin.buffer.read(MAX_SOURCE + 1)
        if len(payload) > MAX_SOURCE:
            raise ValueError("image source exceeds 20 MiB")
        bounds = [int(v) for v in sys.argv[2:4]]
        result = _decode(payload, int(sys.argv[1]), *bounds)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "invalid image or image exceeds decoding limits"
        result = {"error": message}
    sys.stdout.write(json.dumps(result))
