"""Image-generation protocol registry.

Image generation APIs vary far more than chat: OpenAI-compatible endpoints and
Azure OpenAI share the OpenAI semantic protocol, but use different URL/auth
connectors; Gemini uses a different semantic protocol and connector.

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
from app.services.protocols.ids import GEMINI_IMAGES, OPENAI_IMAGES


class ImageAdapter(Protocol):
    async def images(
        self, channel: Channel, upstream_model: str, payload: dict
    ) -> tuple[int, dict]:
        ...


# Keyed by connector name. Adding a new connector (e.g. OpenAI-compatible via a
# third-party gateway, Bedrock Anthropic, Stability, Flux) only requires a new
# adapter implementing `images()` plus one entry here.
_IMAGE_REGISTRY: dict[str, ImageAdapter] = {
    "openai": OpenAIAdapter(),        # OpenAI /v1/images/generations (dall-e, gpt-image)
    "azure_openai": AzureOpenAIAdapter(),  # Azure OpenAI images (image preview api-version)
    "gemini": GeminiAdapter(),        # Imagen predict (translation pending)
}


def get_image_adapter(protocol: str) -> ImageAdapter | None:
    from app.services.providers.registry import normalize_connector

    return _IMAGE_REGISTRY.get(normalize_connector(protocol))


SUPPORTED_IMAGE_CONNECTORS = list(_IMAGE_REGISTRY.keys())
SUPPORTED_IMAGE_PROTOCOLS = [OPENAI_IMAGES, GEMINI_IMAGES]
