"""Site-wide speech to text: one OpenAI-compatible /v1/audio/transcriptions
server (Speaches, whisper.cpp's server, OpenAI, ...) shared by every user.

Nothing here runs unless SPEECH_ENABLED is set. The admin then points it at
the server on /admin/sources#speech; that row is a `sources` row of type
"speech", which the model router ignores, and its key is an encrypted
`credentials` row like llama's. Audio is forwarded and never stored."""

from __future__ import annotations

import io
import json
import time
import wave

import httpx

from . import accounts, db
from .config import settings
from .crypto import encrypt
from .sources.openai_compat import _v1

TYPE = "speech"
# One request's audio. The browser cuts longer dictation at pauses.
MAX_SECONDS = 30
ASR_TASK = "automatic-speech-recognition"


class SpeechError(Exception):
    pass


def _row():
    return db.fetchone("SELECT * FROM sources WHERE type=?", (TYPE,))


def _cfg(row) -> dict:
    try:
        cfg = json.loads((row or {}).get("config") or "{}")
    except (TypeError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _key(source_id: int) -> str:
    cred = db.fetchone("SELECT secret FROM credentials WHERE source_id=? AND disabled=0", (source_id,))
    return accounts.cred_secret(cred) if cred else ""


def ensure_source() -> int:
    row = _row()
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            (TYPE, "Speech to text", 0, "{}", db.now()),
        )
        return db.last_id(conn)


def _config() -> dict | None:
    """The live backend, or None while speech is off or not set up."""
    if not settings.speech_enabled:
        return None
    row = _row()
    cfg = _cfg(row)
    if not row or not row["enabled"] or not cfg.get("base_url") or not cfg.get("model"):
        return None
    return {**cfg, "key": _key(row["id"])}


def available() -> bool:
    return _config() is not None


def admin_view() -> dict:
    sid = ensure_source()
    row = db.fetchone("SELECT * FROM sources WHERE id=?", (sid,))
    return {
        "id": sid,
        "enabled": bool(row["enabled"]),
        "cfg": _cfg(row),
        "key_set": bool(db.fetchone("SELECT id FROM credentials WHERE source_id=?", (sid,))),
    }


def save(*, base_url: str, model: str, api_key: str, verify: bool, enabled: bool) -> None:
    sid = ensure_source()
    cfg = {"base_url": base_url, "model": model, "verify": 1 if verify else 0}
    with db.tx() as conn:
        conn.execute("UPDATE sources SET config=?, enabled=? WHERE id=?", (json.dumps(cfg), 1 if enabled else 0, sid))
        if api_key:
            conn.execute("DELETE FROM credentials WHERE source_id=?", (sid,))
            conn.execute(
                """INSERT INTO credentials (source_id, label, secret, extra, week_start, donated_by, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid, "speech", encrypt(api_key), encrypt("{}"), db.iso_week_start(), None, db.now()),
            )


def check_wav(body: bytes) -> None:
    """Reject anything but a short mono 16-bit PCM WAV before it is forwarded."""
    try:
        with wave.open(io.BytesIO(body)) as w:
            ok = w.getnchannels() == 1 and w.getsampwidth() == 2 and 8000 <= w.getframerate() <= 48000
            seconds = w.getnframes() / w.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError):
        raise ValueError("Audio must be a mono 16-bit WAV.") from None
    if not ok:
        raise ValueError("Audio must be a mono 16-bit WAV.")
    if seconds > MAX_SECONDS:
        raise ValueError(f"Audio is longer than {MAX_SECONDS} s.")


def silence_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return buf.getvalue()


def _client(cfg: dict, timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, verify=bool(cfg.get("verify", 1)))


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


async def _post(cfg: dict, key: str, audio: bytes) -> str:
    async with _client(cfg, 30) as client:
        r = await client.post(
            _v1(cfg["base_url"]) + "/audio/transcriptions",
            headers=_headers(key),
            data={"model": cfg["model"], "response_format": "json"},
            files={"file": ("audio.wav", audio, "audio/wav")},
        )
    if r.status_code != 200:
        raise SpeechError(f"speech server returned HTTP {r.status_code}")
    try:
        return str(r.json().get("text") or "").strip()
    except (ValueError, AttributeError):
        raise SpeechError("speech server returned no JSON text") from None


async def transcribe(audio: bytes) -> str:
    cfg = _config()
    if cfg is None:
        raise SpeechError("speech to text is off")
    return await _post(cfg, cfg["key"], audio)


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail[:240], "skip": False}


async def probe() -> dict:
    """The admin Test button: reach the server, find the model, time a request."""
    started = time.monotonic()
    row = _row()
    cfg = _cfg(row)
    if not row or not cfg.get("base_url"):
        return {"ok": False, "error": "Save a base URL first.", "checks": []}
    key = _key(row["id"])
    checks = []
    try:
        async with _client(cfg, 10) as client:
            r = await client.get(_v1(cfg["base_url"]) + "/models", headers=_headers(key))
        data = r.json().get("data") if r.status_code == 200 else None
        ids = [
            m["id"] for m in data or []
            if isinstance(m, dict) and m.get("id") and m.get("task", ASR_TASK) == ASR_TASK
        ]
        checks.append(_check("Server", r.status_code == 200, ", ".join(ids) or f"HTTP {r.status_code}"))
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        checks.append(_check("Server", False, type(exc).__name__))
        ids = []
    model = cfg.get("model") or ""
    checks.append(_check("Model", bool(model) and model in ids, model or "not set"))
    if checks[0]["ok"] and model:
        t0 = time.monotonic()
        try:
            await _post(cfg, key, silence_wav())
            checks.append(_check("Transcribe", True, f"1 s of audio in {round((time.monotonic() - t0) * 1000)} ms"))
        except (SpeechError, httpx.HTTPError) as exc:
            checks.append(_check("Transcribe", False, str(exc) or type(exc).__name__))
    return {
        "ok": all(c["ok"] for c in checks),
        "ms": round((time.monotonic() - started) * 1000),
        "checks": checks,
    }
