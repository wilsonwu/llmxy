"""Image-generation protocol registry.

Image generation APIs vary far more than chat: OpenAI / Azure expose
`/images/generations`, Gemini uses Imagen `predict`, and third parties
(Stability, Flux, Replicate, ...) each have their own request/response shape.
So the *protocol* used to talk to an upstream image model is NOT necessarily
the same as the channel's chat `provider_type` — it is selected per image
model via `Model.upstream_protocol`.

Each registered adapter exposes `images(channel, upstream_model, payload)` and
is responsible for translating the incoming OpenAI-shape payload to the
upstream request and the upstream response back to the OpenAI image shape
(`{"created": ..., "data": [{"b64_json"|"url": ...}]}`) so the client-facing
contract stays stable regardless of protocol.
"""
from __future__ import annotations

from typing import Protocol

from app.models import Channel
from app.services.providers.azure_openai import AzureOpenAIAdapter
from app.services.providers.gemini import GeminiAdapter
from app.services.providers.openai import OpenAIAdapter


class ImageAdapter(Protocol):
    async def images(
        self, channel: Channel, upstream_model: str, payload: dict
    ) -> tuple[int, dict]:
        ...


# Keyed by protocol name, intentionally independent of the chat provider
# registry. Adding a new protocol (e.g. "stability", "flux") only requires a
# new adapter implementing `images()` plus one entry here.
_IMAGE_REGISTRY: dict[str, ImageAdapter] = {
    "openai": OpenAIAdapter(),      # OpenAI /v1/images/generations (dall-e, gpt-image)
    "azure": AzureOpenAIAdapter(),  # Azure OpenAI images (image preview api-version)
    "gemini": GeminiAdapter(),      # Imagen predict (translation pending)
}


def get_image_adapter(protocol: str) -> ImageAdapter | None:
    return _IMAGE_REGISTRY.get((protocol or "").lower())


SUPPORTED_IMAGE_PROTOCOLS = list(_IMAGE_REGISTRY.keys())
