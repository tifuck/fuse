from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass
class ChatResult:
    status: int
    body: dict | None = None
    stream: AsyncIterator[bytes] | None = None
    usage: dict = field(default_factory=dict)
    credential_id: int | None = None
    model: str = ""
    # One entry per failed/retried upstream attempt: {credential_id, status,
    # usage}. Populated by pool sources so api.py can account quota that was
    # possibly consumed before the failure.
    attempts: list[dict] = field(default_factory=list)
    # Response headers that are safe to expose to the caller. Sources must not
    # place upstream authentication or identifying headers here.
    response_headers: dict[str, str] = field(default_factory=dict)


class Source(Protocol):
    type_id: str
    source_id: int
    name: str

    async def models(self) -> list[str]: ...
    async def chat(self, body: dict) -> ChatResult: ...
