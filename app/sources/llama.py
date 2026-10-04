from __future__ import annotations

import re
import time

import httpx

from .. import accounts
from ..proxy import openai_chat
from .base import ChatResult
from .openai_compat import OpenAICompatSource, _v1


_model_aliases: dict[int, dict[str, str]] = {}
_models_cache: dict[int, tuple[float, list[str]]] = {}
_MODELS_TTL = 300.0

_VENDOR_PREFIX = re.compile(
    r"^(?:huihui|bartowski|mradermacher|lmstudio|thebloke|unsloth)[-_.]+", re.IGNORECASE
)
_QUANT_SUFFIX = re.compile(r"[-_.]+i?q\d[a-z0-9_]*$|[-_.]+gguf$", re.IGNORECASE)


def _public_model_id(model: str) -> str:
    """Hide local filesystem details from the public model catalog."""
    name = model.strip()
    if name.lower().endswith(".gguf"):
        name = name.replace("\\", "/").rsplit("/", 1)[-1][:-5]
    name = _VENDOR_PREFIX.sub("", name)
    while True:
        stripped = _QUANT_SUFFIX.sub("", name)
        if stripped == name:
            break
        name = stripped
    return name.lower()


class LlamaSource(OpenAICompatSource):
    type_id = "llama"

    def __init__(self, row, cfg: dict, creds):
        super().__init__(row, cfg, creds)
        self.rotation = "none"

    def _url(self) -> str:
        return _v1(self.cfg.get("base_url") or "http://127.0.0.1:8080")

    def _key(self) -> str:
        if not self.creds:
            return ""
        return accounts.cred_secret(self.creds[0])

    def _verify(self) -> bool:
        if "verify" in self.cfg:
            return bool(self.cfg["verify"])
        return not self._url().lower().startswith("https://")

    def _client(self, timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, verify=self._verify())

    async def models(self) -> list[str]:
        hit = _models_cache.get(self.source_id)
        if hit and time.time() - hit[0] < _MODELS_TTL:
            return list(hit[1])
        default = str(self.cfg.get("model") or "").strip()
        async with self._client(8) as client:
            r = await client.get(self._url() + "/models", headers=self._headers(self._key()))
            raw_names: list[str] = []
            if r.status_code == 200:
                data = r.json()
                raw_names = [m.get("id") for m in data.get("data", []) if m.get("id")]

            aliases: dict[str, str] = {}
            names: list[str] = []
            for raw in raw_names:
                public = _public_model_id(raw)
                if public in aliases and aliases[public] != raw:
                    public = raw
                aliases[public] = raw
                if public not in names:
                    names.append(public)

            # A configured alias is useful only when the upstream has no catalog.
            # Never advertise the historical placeholder as if it were a model.
            if not names and default and default not in ("local", "llama"):
                aliases[default] = default
                names.append(default)
            _model_aliases[self.source_id] = aliases
            n_ctx = None
            cfg_ctx = self.cfg.get("n_ctx") or self.cfg.get("context_window")
            try:
                n_ctx = int(cfg_ctx) if cfg_ctx else None
            except (TypeError, ValueError):
                n_ctx = None
            if not n_ctx:
                try:
                    props_url = self._url()[:-3] + "/props" if self._url().endswith("/v1") else self._url().rstrip("/") + "/props"
                    props = await client.get(props_url, headers=self._headers(self._key()))
                    if props.status_code == 200:
                        payload = props.json()
                        gen = payload.get("default_generation_settings") if isinstance(payload, dict) else None
                        if isinstance(gen, dict):
                            n_ctx = int(gen.get("n_ctx") or 0) or None
                        modalities = payload.get("modalities") if isinstance(payload, dict) else None
                        if isinstance(modalities, dict) and isinstance(modalities.get("vision"), bool):
                            from .. import model_caps

                            for name in names:
                                model_caps.remember(self.type_id, name, modalities["vision"])
                except Exception:
                    n_ctx = n_ctx
            if n_ctx:
                from .. import context as ctxmod

                for name in names:
                    ctxmod.remember_window(self.type_id, name, n_ctx)
            if names:
                _models_cache[self.source_id] = (time.time(), list(names))
            return names

    async def chat(self, body: dict, *, session_id: str | None = None) -> ChatResult:
        cred_id = self.creds[0]["id"] if self.creds else None
        payload = dict(body)
        if not payload.get("model"):
            payload["model"] = self.cfg.get("model") or ""
        payload["model"] = _model_aliases.get(self.source_id, {}).get(
            payload["model"], payload["model"]
        )
        client = self._client(httpx.Timeout(300.0, connect=15.0))
        status, obj, stream, usage, response_headers = await openai_chat(
            client,
            self._url() + "/chat/completions",
            self._headers(self._key()),
            payload,
            close_client=True,
        )
        if stream is not None:
            return ChatResult(
                status=200, stream=stream, credential_id=cred_id, model=payload.get("model", "")
            )
        return ChatResult(
            status=status,
            body=obj,
            usage=usage,
            credential_id=cred_id,
            model=payload.get("model", ""),
            response_headers=response_headers,
        )
