from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx

from app.core.config import settings
from app.models import Channel, Model, RoutePolicy, RouteStrategy

log = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    model: Model
    channel: Channel
    fallback_chain: list[tuple[Model, Channel]]


def _build_target_chain(
    policy: RoutePolicy,
    models_by_id: dict[int, Model],
    channels_by_id: dict[int, Channel],
) -> list[tuple[Model, Channel]]:
    targets = policy.targets_jsonb or []
    pairs: list[tuple[Model, Channel, dict]] = []
    for t in targets:
        m = models_by_id.get(int(t["model_id"]))
        if not m or not m.enabled:
            continue
        c = channels_by_id.get(m.channel_id)
        if not c or not c.enabled:
            continue
        pairs.append((m, c, t))

    if policy.strategy == RouteStrategy.fallback:
        pairs.sort(key=lambda x: int(x[2].get("fallback_order", 0)))
        return [(m, c) for m, c, _ in pairs]

    if policy.strategy == RouteStrategy.weighted:
        weights = [max(int(t.get("weight", 1)), 0) for _, _, t in pairs]
        if sum(weights) <= 0:
            return [(m, c) for m, c, _ in pairs]
        ordered: list[tuple[Model, Channel]] = []
        remaining = list(zip(pairs, weights))
        while remaining:
            total = sum(w for _, w in remaining)
            r = random.uniform(0, total)
            acc = 0.0
            for i, ((m, c, _), w) in enumerate(remaining):
                acc += w
                if r <= acc:
                    ordered.append((m, c))
                    remaining.pop(i)
                    break
        return ordered

    # smart: order doesn't matter here; pick_smart will choose
    return [(m, c) for m, c, _ in pairs]


def select_route(
    policy: RoutePolicy,
    models_by_id: dict[int, Model],
    channels_by_id: dict[int, Channel],
) -> Optional[RouteDecision]:
    chain = _build_target_chain(policy, models_by_id, channels_by_id)
    if not chain:
        return None
    m, c = chain[0]
    return RouteDecision(model=m, channel=c, fallback_chain=chain[1:])


def _headers(channel: Channel) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if channel.api_key_enc:
        h["Authorization"] = f"Bearer {channel.api_key_enc}"
    return h


async def pick_smart(
    channel: Channel,
    candidate_upstream_models: list[str],
    payload: dict,
) -> Optional[str]:
    """Ask Higress AI Gateway's model-router to pick a model.

    Returns the chosen upstream model name. Falls back to first candidate on failure.
    The exact endpoint depends on the Higress plugin; we POST to /v1/route as a convention.
    """
    if not candidate_upstream_models:
        return None
    url = f"{channel.base_url.rstrip('/')}/v1/route"
    body = {
        "candidates": candidate_upstream_models,
        "messages": payload.get("messages"),
    }
    try:
        async with httpx.AsyncClient(timeout=settings.HIGRESS_TIMEOUT) as cli:
            r = await cli.post(url, json=body, headers=_headers(channel))
            if r.status_code == 200:
                data = r.json()
                m = data.get("model")
                if m in candidate_upstream_models:
                    return m
    except Exception as e:
        log.warning("higress smart route failed: %s", e)
    return candidate_upstream_models[0]


async def forward_chat(
    channel: Channel,
    upstream_model: str,
    payload: dict,
    stream: bool,
) -> tuple[int, dict | AsyncIterator[bytes]]:
    """Forward to Higress's OpenAI-compatible /v1/chat/completions endpoint.

    Returns (status_code, body). body is dict for non-stream, async-iter for stream.
    """
    url = f"{channel.base_url.rstrip('/')}/v1/chat/completions"
    body = dict(payload)
    body["model"] = upstream_model
    headers = _headers(channel)

    if not stream:
        async with httpx.AsyncClient(timeout=settings.HIGRESS_TIMEOUT) as cli:
            r = await cli.post(url, json=body, headers=headers)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"error": {"message": r.text}}

    body["stream"] = True

    async def _streamer() -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=None) as cli:
            async with cli.stream("POST", url, json=body, headers=headers) as r:
                async for chunk in r.aiter_raw():
                    yield chunk

    return 200, _streamer()


async def forward_embeddings(channel: Channel, upstream_model: str, payload: dict) -> tuple[int, dict]:
    url = f"{channel.base_url.rstrip('/')}/v1/embeddings"
    body = dict(payload)
    body["model"] = upstream_model
    async with httpx.AsyncClient(timeout=settings.HIGRESS_TIMEOUT) as cli:
        r = await cli.post(url, json=body, headers=_headers(channel))
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"error": {"message": r.text}}


def parse_usage_from_chunk(chunk: bytes) -> Optional[dict]:
    """Parse SSE chunk and return usage dict if found (final chunk of OpenAI stream)."""
    try:
        for line in chunk.decode("utf-8", errors="ignore").splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            obj = json.loads(data)
            if isinstance(obj, dict) and obj.get("usage"):
                return obj["usage"]
    except Exception:
        pass
    return None
