from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from app.models import Channel


@dataclass
class ChatResult:
    status: int
    body: dict | None = None                       # for non-stream
    stream: AsyncIterator[bytes] | None = None     # OpenAI-format SSE bytes
    prompt_tokens: int = 0                         # for non-stream; stream sets via parse_usage
    completion_tokens: int = 0


class ProviderAdapter(Protocol):
    name: str

    async def chat(
        self,
        channel: Channel,
        upstream_model: str,
        payload: dict,
        stream: bool,
    ) -> ChatResult: ...

    async def embeddings(
        self,
        channel: Channel,
        upstream_model: str,
        payload: dict,
    ) -> tuple[int, dict]: ...
