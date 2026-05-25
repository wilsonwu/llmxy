from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Optional

from app.models import Channel, Model, RoutePolicy, RouteStrategy


@dataclass
class RouteDecision:
    model: Model
    channel: Channel
    fallback_chain: list[tuple[Model, Channel]]


def _ordered_targets(
    policy: RoutePolicy,
    models_by_id: dict[int, Model],
    channels_by_id: dict[int, Channel],
) -> list[tuple[Model, Channel, dict]]:
    out: list[tuple[Model, Channel, dict]] = []
    for t in policy.targets_jsonb or []:
        m = models_by_id.get(int(t["model_id"]))
        if not m or not m.enabled:
            continue
        c = channels_by_id.get(m.channel_id)
        if not c or not c.enabled:
            continue
        out.append((m, c, t))
    return out


def select_route(
    policy: RoutePolicy,
    models_by_id: dict[int, Model],
    channels_by_id: dict[int, Channel],
) -> Optional[RouteDecision]:
    pairs = _ordered_targets(policy, models_by_id, channels_by_id)
    if not pairs:
        return None

    if policy.strategy == RouteStrategy.fallback:
        pairs.sort(key=lambda x: int(x[2].get("fallback_order", 0)))
        chain = [(m, c) for m, c, _ in pairs]
        return RouteDecision(model=chain[0][0], channel=chain[0][1], fallback_chain=chain[1:])

    # weighted (smart degrades to weighted for now)
    weights = [max(int(t.get("weight", 1)), 0) for _, _, t in pairs]
    if sum(weights) <= 0:
        chain = [(m, c) for m, c, _ in pairs]
    else:
        chain = []
        remaining = list(zip(pairs, weights))
        while remaining:
            total = sum(w for _, w in remaining)
            r = random.uniform(0, total)
            acc = 0.0
            for i, ((m, c, _), w) in enumerate(remaining):
                acc += w
                if r <= acc:
                    chain.append((m, c))
                    remaining.pop(i)
                    break
    return RouteDecision(model=chain[0][0], channel=chain[0][1], fallback_chain=chain[1:])


def parse_usage_from_chunk(chunk: bytes) -> Optional[dict]:
    """Parse SSE chunk for OpenAI-format usage."""
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
