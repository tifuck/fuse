from __future__ import annotations

import asyncio
import json
import time
import uuid
import httpx

from .. import accounts, context_meter, model_caps
from ..config import ROOT
from ..proxy import UsageStream, usage_from_obj
from .base import ChatResult
from .key_pool import KeyPool
from . import ollama_access
from .pool_session import bind, lookup

_pools: dict[int, KeyPool] = {}
_tags_cache: dict[int, tuple[float, list[str]]] = {}
_TAGS_TTL = 300.0


class UnsupportedAttachment(ValueError):
    pass


async def stop_pools() -> None:
    for pool in list(_pools.values()):
        await pool.stop_sweeper()
        await asyncio.to_thread(pool.save_state)


def _flatten(content, images_out: list) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts = []
    for part in content:
        if not isinstance(part, dict):
            parts.append(str(part))
            continue
        if part.get("type") == "text" and part.get("text"):
            parts.append(part["text"])
        elif part.get("type") == "image_url":
            iu = part.get("image_url")
            url = iu.get("url", "") if isinstance(iu, dict) else (iu or "")
            if url.startswith("data:") and ";base64," in url:
                images_out.append(url.split(",", 1)[1])
            else:
                raise UnsupportedAttachment("Ollama Cloud supports only base64 data-URI images")
        elif part.get("type") in ("file", "input_file"):
            raise UnsupportedAttachment("Ollama Cloud does not support file attachments")
    return "\n".join(parts)


def to_ollama(messages: list) -> list:
    out = []
    for msg in messages:
        role = msg.get("role", "user")
        images: list[str] = []
        content = _flatten(msg.get("content"), images)
        item = {"role": role, "content": content}
        if role == "assistant" and msg.get("tool_calls"):
            calls = []
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                call = {"function": {"name": fn.get("name", ""), "arguments": args or {}}}
                if tc.get("id"):
                    call["id"] = str(tc["id"])
                calls.append(call)
            item["tool_calls"] = calls
        if role == "tool":
            item = {"role": "tool", "content": content}
            if msg.get("tool_call_id"):
                item["tool_call_id"] = str(msg["tool_call_id"])
        images = [*(msg.get("images") or []), *images]
        if images:
            item["images"] = images
        out.append(item)
    return out


def to_openai_tools(calls: list, include_index=False, *, state: dict | None = None) -> list:
    out = []
    if state is None:
        state = {}
    for tc in calls:
        fn = tc.get("function", {})
        args = fn.get("arguments")
        args_str = args if isinstance(args, str) else json.dumps(args or {})
        raw_id = tc.get("id")
        native_index = fn.get("index", tc.get("index"))
        if isinstance(native_index, bool) or not isinstance(native_index, int) or native_index < 0:
            native_index = None
        identity = ("index", native_index) if native_index is not None else (
            ("id", raw_id) if isinstance(raw_id, str) and raw_id.strip() else ("call", len(state))
        )
        previous = state.get(identity)
        index = previous[0] if previous else (native_index if native_index is not None else max(
            (value[0] for value in state.values()), default=-1
        ) + 1)
        call_id = previous[1] if previous else (
            raw_id if isinstance(raw_id, str) and raw_id.strip() else f"ollama_{index}"
        )
        state[identity] = (index, call_id)
        call = {
            "id": call_id,
            "type": "function",
            "function": {"name": fn.get("name", ""), "arguments": args_str},
        }
        if include_index:
            call["index"] = index
            if previous:
                call.pop("id")
                call.pop("type")
                call["function"].pop("name")
        out.append(call)
    return out


class OllamaSource:
    type_id = "ollama"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        # Pin kind is per source: two Ollama sources with the same scoped
        # session id must not share a pin and swap each other's sticky key.
        self.pin_kind = f"ollama:{self.source_id}"
        self.cfg = cfg
        self.creds = list(creds)
        self.base = (cfg.get("base_url") or "https://ollama.com/api").rstrip("/")
        if self.source_id not in _pools:
            pool = KeyPool(
                keys_file="",
                state_file=str(ROOT / "data" / f"ollama-{self.source_id}.json"),
            )
            pool.load_from_list([accounts.cred_secret(c) for c in creds])
            _pools[self.source_id] = pool
        else:
            _pools[self.source_id].load_from_list([accounts.cred_secret(c) for c in creds])
        self.pool = _pools[self.source_id]
        self._cred_by_key = {accounts.cred_secret(c): c for c in creds}
        self._refresh_task = None
        self._maybe_start_sweeper()

    def _maybe_start_sweeper(self) -> None:
        try:
            import asyncio

            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.pool.start_sweeper()

    def key_views(self, creds=None) -> list[dict]:
        """Admin-facing status for each stored key. Never includes the raw secret."""
        rows = list(creds) if creds is not None else self.creds
        out = []
        for c in rows:
            secret = accounts.cred_secret(c)
            st = self.pool.states.get(secret)
            if st:
                st.refresh_state()
                state = st.state
                reason = st.disabled_reason or ""
                available_in = st.available_in()
                last_error = st.last_error_type or ""
                last_status = st.last_status
                last_model = st.last_model or ""
                last_body = st.last_error_body or ""
            else:
                state = "disabled" if c["disabled"] else "active"
                reason = ""
                available_in = 0.0
                last_error = ""
                last_status = 0
                last_model = ""
                last_body = ""
            prefix = secret[:8] + "…" if secret else ""
            out.append(
                {
                    "id": c["id"],
                    "label": c["label"],
                    "prefix": prefix,
                    "state": state,
                    "disabled_reason": reason,
                    "available_in": available_in,
                    "last_error": last_error,
                    "last_status": last_status,
                    "last_model": last_model,
                    "last_error_body": last_body,
                    "last_error_label": ollama_access.label_for(last_error),
                }
            )
        return out

    async def _tags(self) -> list[str]:
        if not self.creds:
            return []
        hit = _tags_cache.get(self.source_id)
        if hit and time.time() - hit[0] < _TAGS_TTL:
            return list(hit[1])
        async with httpx.AsyncClient(timeout=20) as client:
            key = accounts.cred_secret(self.creds[0])
            r = await client.get(self.base + "/tags", headers={"Authorization": f"Bearer {key}"})
            if r.status_code != 200:
                raise ValueError(f"ollama tags http {r.status_code}")
            names = [
                m["name"]
                for m in r.json().get("models", [])
                if isinstance(m, dict) and m.get("name")
            ]
            if not names:
                # Never cache an empty catalog: a blip would blank the model
                # list for a full TTL instead of falling back to cfg["model"].
                raise ValueError("ollama catalog returned no models")

            slots = asyncio.Semaphore(8)

            async def capability(name):
                try:
                    async with slots:
                        response = await client.post(self.base + "/show", headers={"Authorization": f"Bearer {key}"}, json={"model": name})
                    if response.status_code == 200:
                        data = response.json()
                        caps = data.get("capabilities")
                        if isinstance(caps, list):
                            model_caps.remember(self.type_id, name, "vision" in caps)
                        info = data.get("model_info") or {}
                        params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
                        ctx = next((v for k, v in info.items() if k.endswith(".context_length")), 0) or params.get("num_ctx", 0)
                        if ctx:
                            context_meter.remember_window(self.type_id, name, ctx)
                except (httpx.HTTPError, ValueError):
                    pass

            await asyncio.gather(*(capability(name) for name in names))
        _tags_cache[self.source_id] = (time.time(), names)
        return names

    def _schedule_access_refresh(self, names: list[str]) -> None:
        pending = ollama_access.pending(self.source_id, names)
        if not pending:
            return
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = self._refresh_task
        if task is not None and not task.done():
            return
        self._refresh_task = loop.create_task(self._refresh_access(pending))

    async def _refresh_access(self, names: list[str]) -> None:
        keys: list[str] = []
        for c in self.creds:
            try:
                key = accounts.cred_secret(c)
            except Exception:
                continue
            if key and key not in keys:
                keys.append(key)
        if not keys:
            return
        # The access map gates the whole source, but keys can have different
        # plan tiers: probe each key and mark a model free when any of them
        # can call it. Probing only creds[0] hid models other keys serve and
        # listed models only a paid key could call.
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
            for name in names:
                gated: tuple[int, str] | None = None
                other: tuple[int, str] | None = None
                try:
                    for key in keys:
                        r = await client.post(
                            self.base + "/chat",
                            headers={"Authorization": f"Bearer {key}"},
                            json={
                                "model": name,
                                "messages": [{"role": "user", "content": "ping"}],
                                "stream": False,
                                "num_predict": 1,
                            },
                        )
                        body = r.text[:300]
                        if r.status_code == 200:
                            ollama_access.note(self.source_id, name, ollama_access.FREE, status=200)
                            gated = None
                            break
                        if ollama_access.is_gated_error(r.status_code, body):
                            gated = gated or (r.status_code, body)
                        else:
                            other = other or (r.status_code, body)
                    else:
                        if gated is not None:
                            ollama_access.note(
                                self.source_id, name, ollama_access.GATED, status=gated[0], error=gated[1]
                            )
                        elif other is not None:
                            ollama_access.note(
                                self.source_id, name, ollama_access.UNKNOWN, status=other[0], error=other[1]
                            )
                except Exception as e:
                    ollama_access.note(
                        self.source_id, name, ollama_access.UNKNOWN, status=0, error=type(e).__name__
                    )

    async def models(self) -> list[str]:
        try:
            names = await self._tags()
        except Exception:
            names = [self.cfg["model"]] if self.cfg.get("model") else []
        free = ollama_access.filter_free(self.source_id, names)
        self._schedule_access_refresh(names)
        return free

    def _payload(self, body: dict) -> dict:
        payload = {
            "model": body.get("model") or self.cfg.get("model") or "",
            "messages": to_ollama(body.get("messages") or []),
            "stream": bool(body.get("stream", False)),
        }
        options = {}
        if body.get("max_tokens") is not None:
            options["num_predict"] = body["max_tokens"]
        if body.get("temperature") is not None:
            options["temperature"] = body["temperature"]
        if body.get("top_p") is not None:
            options["top_p"] = body["top_p"]
        if body.get("stop") is not None:
            options["stop"] = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
        if options:
            payload["options"] = options
        if body.get("tools") is not None:
            payload["tools"] = body["tools"]
        if "think" in body:
            payload["think"] = body["think"]
        else:
            effort = body.get("reasoning_effort")
            reasoning = body.get("reasoning")
            if effort is None and isinstance(reasoning, dict):
                effort = reasoning.get("effort")
            if isinstance(effort, str):
                payload["think"] = (
                    False
                    if effort == "none"
                    else "low"
                    if effort == "minimal"
                    else "max"
                    if effort == "xhigh"
                    else effort
                )
            elif isinstance(body.get("thinking"), dict):
                payload["think"] = body["thinking"].get("type") != "disabled"
        return payload

    async def chat(self, body: dict, *, session_id: str | None = None) -> ChatResult:
        if not self.creds:
            return ChatResult(status=503, body={"error": "No Ollama keys configured"})
        stream = bool(body.get("stream", False))
        try:
            payload = self._payload(body)
        except UnsupportedAttachment as exc:
            return ChatResult(status=400, body={"error": {"message": str(exc)}})
        payload["stream"] = stream
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
        prefer_key = None
        pinned_cid = None
        if self.cfg.get("sticky_sessions", True) and session_id:
            pinned_cid = lookup(self.pin_kind, session_id)
            if pinned_cid:
                for c in self.creds:
                    if c["id"] == pinned_cid:
                        prefer_key = accounts.cred_secret(c)
                        break
        try:
            response, key_state, _ = await self.pool.send_request(
                client, endpoint=self.base + "/chat", payload=payload, prefer_key=prefer_key
            )
        except httpx.TimeoutException as exc:
            await client.aclose()
            return ChatResult(
                status=504,
                body={"error": {"message": f"upstream timeout ({type(exc).__name__})"}},
                model=payload["model"],
            )
        except (httpx.TransportError, ConnectionError) as exc:
            await client.aclose()
            return ChatResult(
                status=502,
                body={"error": {"message": f"upstream transport failure ({type(exc).__name__})"}},
                model=payload["model"],
            )
        stream_returned = False
        try:
            cred = self._cred_by_key.get(key_state.key)
            cid = cred["id"] if cred else None
            model = payload["model"]
            if response.status_code != 200:
                safe_headers = {}
                if response.headers.get("retry-after"):
                    safe_headers["Retry-After"] = response.headers["retry-after"][:128]
                try:
                    err = response.json()
                except Exception:
                    err = {"error": {"message": "upstream returned a non-JSON error"}}
                if not isinstance(err, dict):
                    err = {"error": {"message": "upstream returned an invalid error schema"}}
                text = response.text if hasattr(response, "text") else json.dumps(err)
                retry_attempts = response.extensions.get("fuse_attempts") or []
                await response.aclose()
                if ollama_access.is_gated_error(response.status_code, text if isinstance(text, str) else str(err)):
                    ollama_access.note(
                        self.source_id, model, ollama_access.GATED, status=response.status_code, error=str(err)[:200]
                    )
                    result = ChatResult(
                        status=403,
                        body={"error": "This model requires a paid Ollama Cloud plan"},
                        credential_id=cid,
                        model=model,
                        response_headers=safe_headers,
                    )
                    result.attempts = retry_attempts
                    return result
                status = 502 if response.status_code == 401 else response.status_code
                if status == 502 and response.status_code == 401:
                    err = {"error": {"message": "Ollama rejected the configured API key"}}
                result = ChatResult(
                    status=status,
                    body=err,
                    credential_id=cid,
                    model=model,
                    response_headers=safe_headers,
                )
                result.attempts = retry_attempts
                return result
            ollama_access.note(self.source_id, model, ollama_access.FREE, status=200)
            sticky = bool(self.cfg.get("sticky_sessions", True) and session_id)
            if stream:
                stream_returned = True
                collected: dict = {}
                inner = _stream(response, model, client, collected)

                async def _bind_on_finish():
                    try:
                        async for chunk in inner:
                            yield chunk
                    finally:
                        close = getattr(inner, "aclose", None)
                        if close is not None:
                            await close()
                        if sticky and collected.get("stream_status") == "complete":
                            bind(self.pin_kind, session_id, cid)

                gen = UsageStream(_bind_on_finish(), collected)
                return ChatResult(
                    status=200,
                    stream=gen,
                    credential_id=cid,
                    model=model,
                )
            try:
                await response.aread()
                raw = response.json()
            finally:
                await response.aclose()
            if not isinstance(raw, dict) or not isinstance(raw.get("message"), dict):
                return ChatResult(
                    status=502,
                    body={"error": {"message": "upstream returned an invalid JSON schema"}},
                    credential_id=cid,
                    model=model,
                )
            msg = raw.get("message", {})
            choice = {
                "index": 0,
                "message": {"role": msg.get("role", "assistant"), "content": msg.get("content", "")},
                "finish_reason": "stop",
            }
            if isinstance(msg.get("thinking"), str) and msg["thinking"]:
                choice["message"]["reasoning_content"] = msg["thinking"]
            if msg.get("tool_calls"):
                if not isinstance(msg["tool_calls"], list) or not all(
                    isinstance(call, dict) for call in msg["tool_calls"]
                ):
                    return ChatResult(
                        status=502,
                        body={"error": {"message": "upstream returned invalid tool calls"}},
                        credential_id=cid,
                        model=model,
                    )
                choice["message"]["tool_calls"] = to_openai_tools(msg["tool_calls"])
                choice["finish_reason"] = "tool_calls"
            obj = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(uuid.uuid4().int % 10**10),
                "model": model,
                "choices": [choice],
                "usage": {
                    "prompt_tokens": raw.get("prompt_eval_count", 0),
                    "completion_tokens": raw.get("eval_count", 0),
                },
            }
            usage = usage_from_obj(obj)
            # Ollama's native API exposes no cached-token breakdown: the
            # zeros are placeholders and cache_usage_status stays "unknown"
            # rather than a reported zero.
            if sticky:
                bind(self.pin_kind, session_id, cid)
            return ChatResult(status=200, body=obj, usage=usage, credential_id=cid, model=model)
        finally:
            if not stream_returned:
                await client.aclose()


async def _stream(resp: httpx.Response, model: str, client: httpx.AsyncClient | None = None, collected: dict | None = None):
    request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(uuid.uuid4().int % 10**10)
    first = True
    saw_tools = False
    tool_state: dict = {}
    usage = {}
    completed = False
    try:
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("upstream sent malformed stream JSON") from exc
            if not isinstance(chunk, dict):
                raise ValueError("upstream sent an invalid stream object")
            if chunk.get("error"):
                raise ValueError("upstream reported a stream error")
            message = chunk.get("message", {})
            delta = {"content": message.get("content", "")}
            if isinstance(message.get("thinking"), str) and message["thinking"]:
                delta["reasoning_content"] = message["thinking"]
            if message.get("tool_calls"):
                saw_tools = True
                delta["tool_calls"] = to_openai_tools(message["tool_calls"], include_index=True, state=tool_state)
            if first:
                first = False
                delta["role"] = "assistant"
            payload = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            if chunk.get("done"):
                if delta.get("content") or delta.get("reasoning_content") or delta.get("tool_calls"):
                    yield f"data: {json.dumps(payload)}\n\n".encode()
                usage = {
                    "prompt_tokens": chunk.get("prompt_eval_count", 0),
                    "completion_tokens": chunk.get("eval_count", 0),
                }
                finish = "tool_calls" if saw_tools else "stop"
                final = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                }
                yield f"data: {json.dumps(final)}\n\n".encode()
                usage_chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [],
                    "usage": usage,
                }
                yield f"data: {json.dumps(usage_chunk)}\n\n".encode()
                yield b"data: [DONE]\n\n"
                if collected is not None:
                    collected.clear()
                    collected.update(usage_from_obj({"usage": usage}))
                    collected["usage_status"] = "complete"
                    collected["stream_status"] = "complete"
                completed = True
                return
            yield f"data: {json.dumps(payload)}\n\n".encode()
    finally:
        if collected is not None and not completed:
            collected["stream_status"] = "failed"
        await resp.aclose()
        if client is not None:
            await client.aclose()
