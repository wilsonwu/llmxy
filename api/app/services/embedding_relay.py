from __future__ import annotations

import logging

from app.models import Channel, Model
from app.services import providers

log = logging.getLogger(__name__)


class EmbeddingRelayError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.status_code = 502


async def execute_embedding_relay(
    candidates: list[tuple[Model, Channel]],
    payload: dict,
) -> tuple[dict, Model, Channel]:
    last_error: object = "no upstream"
    for model, channel in candidates:
        connector = providers.resolve_connector_type(model, channel)
        protocol = providers.resolve_upstream_protocol(model, channel)
        adapter = providers.get_connector_adapter(connector)
        if not adapter:
            last_error = f"no connector adapter for {connector}"
            continue
        if not providers.connector_supports_protocol(connector, protocol):
            last_error = f"connector {connector} does not support protocol {protocol}"
            continue
        try:
            status_code, body = await adapter.embeddings(channel, model.upstream_model, payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("embedding adapter error for model %s: %s", model.id, exc)
            last_error = str(exc)
            continue
        if status_code == 200 and isinstance(body, dict):
            return body, model, channel
        last_error = body
    raise EmbeddingRelayError(f"all upstreams failed: {last_error}")