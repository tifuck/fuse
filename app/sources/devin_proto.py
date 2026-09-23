"""Minimal protobuf + Connect framing for Cognition's Cascade API.

Field numbers match the live Devin CLI / Windsurf client (exa.api_server_pb).
Only the messages Fuse uses are encoded.
"""
from __future__ import annotations

import gzip
import struct
from typing import Any


CONNECT_COMPRESSED = 0x01
CONNECT_END_STREAM = 0x02
MAX_FRAME = 16 * 1024 * 1024

CHAT_SOURCE_USER = 1
CHAT_SOURCE_SYSTEM = 2
CHAT_SOURCE_TOOL = 4
REQUEST_TYPE_CASCADE = 5
PLANNER_DEFAULT = 1
CACHE_EPHEMERAL = 1
STOP_MAX_TOKENS = 3
STOP_FUNCTION_CALL = 10


class ProtoEncoder:
    def __init__(self) -> None:
        self._buf = bytearray()

    def _varint(self, n: int) -> None:
        n = int(n) & 0xFFFFFFFFFFFFFFFF
        while n > 0x7F:
            self._buf.append((n & 0x7F) | 0x80)
            n >>= 7
        self._buf.append(n)

    def _tag(self, field: int, wire: int) -> None:
        self._varint((field << 3) | wire)

    def string(self, field: int, value: str | None) -> None:
        if not value:
            return
        data = value.encode("utf-8")
        self._tag(field, 2)
        self._varint(len(data))
        self._buf.extend(data)

    def uint32(self, field: int, value: int | None) -> None:
        if not value:
            return
        self._tag(field, 0)
        self._varint(value)

    def uint64(self, field: int, value: int | None) -> None:
        if not value:
            return
        self._tag(field, 0)
        self._varint(value)

    def bool(self, field: int, value: bool | None) -> None:
        if not value:
            return
        self._tag(field, 0)
        self._varint(1)

    def double(self, field: int, value: float | None) -> None:
        if not value:
            return
        self._tag(field, 1)
        self._buf.extend(struct.pack("<d", float(value)))

    def message(self, field: int, encode) -> None:
        sub = ProtoEncoder()
        encode(sub)
        data = sub.finish()
        if not data:
            return
        self._tag(field, 2)
        self._varint(len(data))
        self._buf.extend(data)

    def finish(self) -> bytes:
        return bytes(self._buf)


class ProtoDecoder:
    def __init__(self, data: bytes):
        self.bytes = data if isinstance(data, (bytes, bytearray)) else bytes(data)
        self.pos = 0

    def _varint(self) -> int:
        result = 0
        shift = 0
        while self.pos < len(self.bytes):
            byte = self.bytes[self.pos]
            self.pos += 1
            result |= (byte & 0x7F) << shift
            if byte & 0x80 == 0:
                break
            shift += 7
            if shift > 70:
                break
        return result

    def tag(self) -> tuple[int, int]:
        n = self._varint()
        return n >> 3, n & 7

    def string(self) -> str:
        return self.raw().decode("utf-8", errors="replace")

    def raw(self) -> bytes:
        n = self._varint()
        start = self.pos
        self.pos = min(len(self.bytes), start + n)
        return self.bytes[start : self.pos]

    def skip(self, wire: int) -> None:
        if wire == 0:
            self._varint()
        elif wire == 1:
            self.pos = min(len(self.bytes), self.pos + 8)
        elif wire == 2:
            self.raw()
        elif wire == 5:
            self.pos = min(len(self.bytes), self.pos + 4)
        else:
            self.pos = len(self.bytes)

    @property
    def done(self) -> bool:
        return self.pos >= len(self.bytes)

    def sub(self) -> "ProtoDecoder":
        return ProtoDecoder(self.raw())


def maybe_gunzip(data: bytes) -> bytes:
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        return gzip.decompress(data)
    return data


def connect_frame(payload: bytes, *, compressed: bool = True) -> bytes:
    body = gzip.compress(payload, mtime=0) if compressed else payload
    flag = CONNECT_COMPRESSED if compressed else 0
    return bytes([flag]) + struct.pack(">I", len(body)) + body


def iter_connect_frames(buf: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """Split a Connect stream buffer into (flag, payload) frames. Remainder is unparsed."""
    frames: list[tuple[int, bytes]] = []
    pos = 0
    while pos + 5 <= len(buf):
        flag = buf[pos]
        length = struct.unpack(">I", buf[pos + 1 : pos + 5])[0]
        if length > MAX_FRAME:
            raise ValueError("connect frame too large")
        if pos + 5 + length > len(buf):
            break
        payload = buf[pos + 5 : pos + 5 + length]
        if flag & CONNECT_COMPRESSED:
            payload = gzip.decompress(payload)
        frames.append((flag, payload))
        pos += 5 + length
    return frames, buf[pos:]


def encode_metadata(e: ProtoEncoder, meta: dict) -> None:
    e.string(1, meta.get("ide_name") or "devin")
    e.string(7, meta.get("ide_version") or "")
    e.string(12, meta.get("extension_name") or "cli")
    e.string(2, meta.get("extension_version") or "")
    e.string(3, meta.get("api_key") or "")
    e.string(4, meta.get("locale") or "en")
    e.string(21, meta.get("user_jwt") or "")
    # Field 28 is the agent/client name ("chisel" = Devin CLI). The chat
    # backend gates newer Cascade SKUs (glm-5-2-none, MODEL_GPT_5_2_*) on it:
    # without this field those requests fail with "Your Windsurf version is
    # out of date" / "an internal error occurred" before any inference.
    # Verified by bisection against the live CLI's wire requests (2026-09-15).
    e.string(28, meta.get("agent_name") or "")


def encode_get_user_jwt(meta: dict) -> bytes:
    enc = ProtoEncoder()
    enc.message(1, lambda e: encode_metadata(e, meta))
    return enc.finish()


def decode_get_user_jwt(data: bytes) -> dict:
    d = ProtoDecoder(maybe_gunzip(data))
    out = {"user_jwt": "", "custom_api_server_url": ""}
    while not d.done:
        field, wire = d.tag()
        if wire != 2:
            d.skip(wire)
            continue
        if field == 1:
            out["user_jwt"] = d.string()
        elif field == 2:
            out["custom_api_server_url"] = d.string()
        else:
            d.skip(wire)
    return out


def encode_unary_metadata(meta: dict) -> bytes:
    return encode_get_user_jwt(meta)


def _encode_tool_call(e: ProtoEncoder, tc: dict) -> None:
    e.string(1, tc.get("id") or "")
    e.string(2, tc.get("name") or "")
    e.string(3, tc.get("arguments_json") or "")
    e.string(4, tc.get("invalid_json") or "")
    e.bool(6, bool(tc.get("is_custom_tool_call", True)))


def _encode_image(e: ProtoEncoder, img: dict) -> None:
    e.string(1, img.get("base64") or "")
    e.string(2, img.get("mime") or "")


def _encode_prompt(e: ProtoEncoder, p: dict) -> None:
    e.string(1, p.get("message_id") or "")
    e.uint32(2, int(p.get("source") or 0))
    e.string(3, p.get("prompt") or "")
    for tc in p.get("tool_calls") or []:
        e.message(6, lambda se, item=tc: _encode_tool_call(se, item))
    e.string(7, p.get("tool_call_id") or "")
    e.bool(9, bool(p.get("tool_result_is_error")))
    for img in p.get("images") or []:
        e.message(10, lambda se, item=img: _encode_image(se, item))
    e.string(11, p.get("thinking") or "")
    e.string(12, p.get("signature") or "")
    e.string(18, p.get("signature_type") or "")


def _encode_tool_def(e: ProtoEncoder, t: dict) -> None:
    e.string(1, t.get("name") or "")
    e.string(2, t.get("description") or "")
    e.string(3, t.get("json_schema") or "")
    e.bool(12, bool(t.get("strict")))


def _encode_configuration(e: ProtoEncoder, cfg: dict) -> None:
    e.uint64(1, int(cfg.get("num_completions") or 1))
    e.uint64(2, int(cfg.get("max_tokens") or 0))
    e.uint64(3, int(cfg.get("max_newlines") or 0))
    e.double(5, float(cfg.get("temperature") or 0))
    e.double(6, float(cfg.get("first_temperature") or 0))
    e.uint64(7, int(cfg.get("top_k") or 0))
    e.double(8, float(cfg.get("top_p") or 0))
    for stop in cfg.get("stop_patterns") or []:
        e.string(9, stop)
    e.double(11, float(cfg.get("fim_eot") or 0))


def decode_configuration(data: bytes) -> dict:
    d = ProtoDecoder(maybe_gunzip(data))
    cfg: dict = {"num_completions": 0, "max_tokens": 0, "max_newlines": 0, "top_k": 0}
    while not d.done:
        field, wire = d.tag()
        if field == 1 and wire == 0:
            cfg["num_completions"] = d._varint()
        elif field == 2 and wire == 0:
            cfg["max_tokens"] = d._varint()
        elif field == 3 and wire == 0:
            cfg["max_newlines"] = d._varint()
        elif field == 7 and wire == 0:
            cfg["top_k"] = d._varint()
        else:
            d.skip(wire)
    return cfg


def encode_get_chat_message(req: dict) -> bytes:
    enc = ProtoEncoder()
    enc.message(1, lambda e: encode_metadata(e, req.get("metadata") or {}))
    enc.string(2, req.get("prompt") or "")
    for p in req.get("prompts") or []:
        enc.message(3, lambda e, item=p: _encode_prompt(e, item))
    enc.string(21, req.get("model_uid") or "")
    enc.uint32(7, REQUEST_TYPE_CASCADE)
    enc.message(8, lambda e: _encode_configuration(e, req.get("configuration") or {}))
    for t in req.get("tools") or []:
        enc.message(10, lambda e, item=t: _encode_tool_def(e, item))
    enc.bool(11, bool(req.get("disable_parallel_tool_calls", True)))
    choice = req.get("tool_choice") or {"option_name": "auto"}
    enc.message(
        12,
        lambda e: (
            e.string(1, choice.get("option_name") or ""),
            e.string(2, choice.get("tool_name") or ""),
        ),
    )
    enc.message(13, lambda e: e.uint32(1, CACHE_EPHEMERAL))
    enc.string(16, req.get("cascade_id") or "")
    enc.uint32(20, PLANNER_DEFAULT)
    enc.string(22, req.get("execution_id") or "")
    return enc.finish()


def _decode_tool_call(d: ProtoDecoder) -> dict:
    tc = {
        "id": "",
        "name": "",
        "arguments_json": "",
        "invalid_json": "",
        "is_custom_tool_call": False,
    }
    while not d.done:
        field, wire = d.tag()
        if field == 1 and wire == 2:
            tc["id"] = d.string()
        elif field == 2 and wire == 2:
            tc["name"] = d.string()
        elif field == 3 and wire == 2:
            # Long argument strings are streamed in chunks: the same field
            # occurs once per chunk inside one message. Concatenate — keeping
            # only the last chunk produced tail fragments like '100"}'.
            tc["arguments_json"] += d.string()
        elif field == 4 and wire == 2:
            tc["invalid_json"] += d.string()
        elif field == 5 and wire == 2:
            d.string()
        elif field == 6 and wire == 0:
            tc["is_custom_tool_call"] = d._varint() != 0
        else:
            d.skip(wire)
    if not tc["arguments_json"] and tc["invalid_json"]:
        tc["arguments_json"] = tc["invalid_json"]
    return tc


def _decode_usage(d: ProtoDecoder) -> dict:
    u = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_write_tokens": 0,
        "cached_tokens": 0,
        "model_uid": "",
    }
    while not d.done:
        field, wire = d.tag()
        if field == 2 and wire == 0:
            u["prompt_tokens"] = d._varint()
        elif field == 3 and wire == 0:
            u["completion_tokens"] = d._varint()
        elif field == 4 and wire == 0:
            u["cache_write_tokens"] = d._varint()
        elif field == 5 and wire == 0:
            u["cached_tokens"] = d._varint()
        elif field == 9 and wire == 2:
            u["model_uid"] = d.string()
        else:
            d.skip(wire)
    u["total_tokens"] = u["prompt_tokens"] + u["completion_tokens"]
    return u


def decode_get_chat_message(data: bytes) -> dict:
    d = ProtoDecoder(maybe_gunzip(data))
    out: dict[str, Any] = {
        "message_id": "",
        "delta_text": "",
        "stop_reason": 0,
        "tool_calls": [],
        "usage": None,
        "delta_thinking": "",
        "delta_signature": "",
        "delta_signature_type": "",
        "output_id": "",
        "credit_cost": 0,
        "actual_model_uid": "",
    }
    while not d.done:
        field, wire = d.tag()
        if field == 1 and wire == 2:
            out["message_id"] = d.string()
        elif field == 3 and wire == 2:
            # Chunked deltas concatenate (see _decode_tool_call).
            out["delta_text"] += d.string()
        elif field == 5 and wire == 0:
            out["stop_reason"] = d._varint()
        elif field == 6 and wire == 2:
            out["tool_calls"].append(_decode_tool_call(d.sub()))
        elif field == 7 and wire == 2:
            out["usage"] = _decode_usage(d.sub())
        elif field == 9 and wire == 2:
            out["delta_thinking"] += d.string()
        elif field == 10 and wire == 2:
            out["delta_signature"] += d.string()
        elif field == 14 and wire == 0:
            out["credit_cost"] = d._varint()
        elif field == 15 and wire == 2:
            out["output_id"] = d.string()
        elif field == 21 and wire == 2:
            out["delta_signature_type"] = d.string()
        elif field == 23 and wire == 2:
            out["actual_model_uid"] = d.string()
        else:
            d.skip(wire)
    return out


def _collect_strings(d: ProtoDecoder, acc: list[str]) -> None:
    while not d.done:
        field, wire = d.tag()
        if wire == 2:
            raw = d.raw()
            if raw and all(b < 0x80 or b >= 0xC0 for b in raw[:8]):
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    _collect_strings(ProtoDecoder(raw), acc)
                    continue
                if "\x00" not in text and text.isprintable():
                    acc.append(text)
                else:
                    _collect_strings(ProtoDecoder(raw), acc)
            else:
                _collect_strings(ProtoDecoder(raw), acc)
        else:
            d.skip(wire)


def decode_user_status(data: bytes) -> dict:
    """Best-effort parse of GetUserStatus. Schema churns; strings are enough to label the account."""
    d = ProtoDecoder(maybe_gunzip(data))
    strings: list[str] = []
    pro = False
    name = ""
    email = ""
    plan = ""
    while not d.done:
        field, wire = d.tag()
        if field == 1 and wire == 2:
            sub = d.sub()
            while not sub.done:
                sf, sw = sub.tag()
                if sf == 1 and sw == 0:
                    pro = sub._varint() != 0
                elif sf == 3 and sw == 2:
                    name = sub.string()
                else:
                    if sw == 2:
                        raw = sub.raw()
                        try:
                            text = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            _collect_strings(ProtoDecoder(raw), strings)
                            continue
                        if "@" in text and "." in text and len(text) < 320:
                            email = text
                        elif text.isprintable() and "\x00" not in text:
                            strings.append(text)
                    else:
                        sub.skip(sw)
        elif field == 2 and wire == 2:
            _collect_strings(d.sub(), strings)
        else:
            d.skip(wire)
    for text in strings:
        low = text.lower()
        if "@" in text and "." in text and not email:
            email = text
        if low in ("pro", "max", "free", "teams", "enterprise") and not plan:
            plan = text.title()
        if not name and text and " " in text and len(text) < 80 and "@" not in text:
            name = text
    if not plan and pro:
        plan = "Pro"
    return {"pro": pro, "name": name, "email": email, "plan_type": plan, "strings": strings}


def decode_cli_model_configs(data: bytes) -> list[dict]:
    d = ProtoDecoder(maybe_gunzip(data))
    models: list[dict] = []
    while not d.done:
        field, wire = d.tag()
        if field == 1 and wire == 2:
            rec = _decode_client_model(d.sub())
            if rec:
                models.append(rec)
        else:
            d.skip(wire)
    return models


_EFFORT_DIMS = ("effort", "reasoning effort")


def _decode_family_metadata(d: ProtoDecoder) -> tuple[str, str]:
    """ModelFamilyMetadata: family label + this row's variant dimensions.

    Each catalog row repeats the family metadata with only its own variant
    filled in: `glm-5-2` lists Effort=["High"], `glm-5-2-1m` lists
    Effort=["High"] plus 1M Context. The effort label is authoritative —
    uid suffixes and display labels are derived views of the same field.
    """
    family = ""
    effort = ""
    while not d.done:
        field, wire = d.tag()
        if field == 1 and wire == 2:
            family = d.string()
        elif field == 2 and wire == 2:
            dim = d.sub()
            dname = ""
            labels: list[str] = []
            while not dim.done:
                df, dw = dim.tag()
                if df == 1 and dw == 2:
                    dname = dim.string()
                elif df == 2 and dw == 2:
                    ent = dim.sub()
                    while not ent.done:
                        ef, ew = ent.tag()
                        if ef == 2 and ew == 2:
                            labels.append(ent.string())
                        else:
                            ent.skip(ew)
                else:
                    dim.skip(dw)
            if dname.strip().lower() in _EFFORT_DIMS and labels:
                effort = labels[-1]
        else:
            d.skip(wire)
    return family, effort


def _decode_promo_until(d: ProtoDecoder) -> int:
    """PromoStatus {status, expiry {seconds}} — 0 when absent."""
    while not d.done:
        field, wire = d.tag()
        if field == 2 and wire == 2:
            sub = d.sub()
            while not sub.done:
                sf, sw = sub.tag()
                if sf == 1 and sw == 0:
                    return int(sub._varint())
                sub.skip(sw)
        else:
            d.skip(wire)
    return 0


def _decode_client_model(d: ProtoDecoder) -> dict | None:
    label = ""
    uid = ""
    max_tokens = 0
    family = ""
    effort = ""
    promo_until = 0
    while not d.done:
        field, wire = d.tag()
        if field == 1 and wire == 2:
            label = d.string()
        elif field == 18 and wire == 0:
            max_tokens = d._varint()
        elif field == 19 and wire == 2:
            promo_until = _decode_promo_until(d.sub())
        elif field == 22 and wire == 2:
            uid = d.string()
        elif field == 30 and wire == 2:
            family, effort = _decode_family_metadata(d.sub())
        else:
            d.skip(wire)
    uid = (uid or "").strip()
    if not uid:
        return None
    return {
        "id": uid,
        "name": (label or uid).strip(),
        "max_tokens": max_tokens or 64_000,
        "family": family.strip(),
        "effort": effort.strip(),
        "promo_until": promo_until,
    }
