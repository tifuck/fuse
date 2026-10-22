from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


# SSE comment a source writes when the model starts thinking and may stay
# silent for minutes (Claude's omitted-display thinking sends no text until
# the block ends). OpenAI-style clients ignore comments; the dashboard's
# provider pacer and status line read it.
THINKING_MARK = b": fuse-thinking"
# SSE comment a source writes for a keep-alive the provider sends only while
# it is still working on this request (Anthropic's `ping`). Generic proxy
# keep-alives must not use it: they can outlive a hung upstream. The pacer
# reads it as "silent, not dead".
ALIVE_MARK = b": fuse-alive"

# Reasoning levels, lowest first.
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def map_effort(effort, efforts) -> str | None:
    """The model's own level nearest the requested one (higher on a tie).
    A level other than none never maps onto none: that would turn thinking off."""
    if not efforts or effort not in EFFORTS:
        return None
    if effort in efforts:
        return effort
    choices = [e for e in efforts if e != "none"]
    if effort == "none" or not choices:
        return None
    want = EFFORTS.index(effort)
    return min(choices, key=lambda e: (abs(EFFORTS.index(e) - want), -EFFORTS.index(e)))


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
    # Live stream hints the source fills while streaming (see UsageStream.hints).
    hints: dict = field(default_factory=dict)


class Source(Protocol):
    type_id: str
    source_id: int
    name: str

    async def models(self) -> list[str]: ...
    async def chat(self, body: dict) -> ChatResult: ...
