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
    """Provider adapters consume and produce the internal chat contract.

    Chat input is OpenAI chat-completions shape. Chat output is OpenAI-shape
    JSON or OpenAI-format SSE bytes. Client-facing protocol endpoints are
    responsible for converting into and out of this contract.
    """

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

    async def images(
        self,
        channel: Channel,
        upstream_model: str,
        payload: dict,
    ) -> tuple[int, dict]:
        """Text-to-image. Returns (status_code, OpenAI-shape body).
        Adapters that don't support image generation should return
        (501, {"error": {...}}). Timeouts should surface as status 504."""
        ...
