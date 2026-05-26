"""Embedding-based smart routing classifier.

Pipeline: prompt → embedding → cosine sim vs cached exemplar centroids → label.

- Exemplar embeddings are computed lazily on first use and cached in Redis
  (`llmxy:smart:exemp:{policy_id}:v{N}`), invalidated by bumping
  `RoutePolicy.smart_embedding_version` on update.
- Prompt embeddings are cached by (model_id, prompt_hash) for 24h to absorb
  hot-path repetition.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

from app.models import Channel, Model, RoutePolicy

log = logging.getLogger(__name__)

EXEMPLAR_TTL = 7 * 24 * 3600  # 7d; bumped version invalidates anyway
PROMPT_TTL = 24 * 3600
PROMPT_HASH_LIMIT = 4096  # only first N chars contribute to the cache key


@dataclass
class EmbeddingUsage:
    model: Model
    channel: Channel
    upstream_model: str
    prompt_tokens: int
    latency_ms: int
    status: str  # "ok" | "error" | "cache"


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return -1.0
    return dot / (na * nb)


def _prompt_cache_key(model_id: int, text: str) -> str:
    h = hashlib.sha256(text[:PROMPT_HASH_LIMIT].encode("utf-8", errors="ignore")).hexdigest()
    return f"llmxy:smart:pemb:{model_id}:{h}"


def _exemplar_cache_key(policy_id: int, version: int) -> str:
    return f"llmxy:smart:exemp:{policy_id}:v{version}"


async def _embed_one(
    model: Model, channel: Channel, text: str
) -> tuple[Optional[list[float]], int, str]:
    """Returns (vector_or_None, prompt_tokens, status)."""
    from app.services import providers as _p

    adapter = _p.get_adapter(channel.provider_type)
    if not adapter:
        return None, 0, "no adapter"
    try:
        status, body = await adapter.embeddings(
            channel, model.upstream_model,
            {"input": text[:PROMPT_HASH_LIMIT]},
        )
    except Exception as e:
        log.warning("smart embed call failed: %s", e)
        return None, 0, f"exception: {e!r}"
    if status != 200 or not isinstance(body, dict):
        return None, 0, f"upstream status={status}"
    data = body.get("data") or []
    if not data or not isinstance(data[0], dict):
        return None, 0, "no data"
    vec = data[0].get("embedding")
    if not isinstance(vec, list):
        return None, 0, "no embedding"
    pt = ((body.get("usage") or {}).get("prompt_tokens", 0)) or 0
    return vec, int(pt), "ok"


async def _embed_batch(
    model: Model, channel: Channel, texts: list[str]
) -> tuple[list[Optional[list[float]]], int]:
    """Best-effort batch (one-shot OpenAI-style call). Falls back to per-item on error."""
    from app.services import providers as _p

    adapter = _p.get_adapter(channel.provider_type)
    if not adapter or not texts:
        return [None] * len(texts), 0
    try:
        status, body = await adapter.embeddings(
            channel, model.upstream_model,
            {"input": [t[:PROMPT_HASH_LIMIT] for t in texts]},
        )
        if status == 200 and isinstance(body, dict):
            data = body.get("data") or []
            vecs: list[Optional[list[float]]] = [None] * len(texts)
            for item in data:
                if not isinstance(item, dict):
                    continue
                idx = item.get("index", 0)
                vec = item.get("embedding")
                if isinstance(vec, list) and 0 <= idx < len(texts):
                    vecs[idx] = vec
            pt = ((body.get("usage") or {}).get("prompt_tokens", 0)) or 0
            if any(v is not None for v in vecs):
                return vecs, int(pt)
    except Exception as e:
        log.warning("smart embed batch failed (%s); falling back per-item", e)

    out: list[Optional[list[float]]] = []
    total_pt = 0
    for t in texts:
        v, pt, _ = await _embed_one(model, channel, t)
        out.append(v)
        total_pt += pt
    return out, total_pt


async def _load_or_build_exemplars(
    policy: RoutePolicy, model: Model, channel: Channel
) -> dict[str, list[list[float]]]:
    """Return {label: [vec, ...]}; compute & cache on miss."""
    from app.core.redis import get_redis

    try:
        r = get_redis()
        cached = await r.get(_exemplar_cache_key(policy.id, policy.smart_embedding_version or 0))
        if cached:
            data = json.loads(cached)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, list) and v}
    except Exception as e:
        log.debug("exemplar cache read skipped: %s", e)
        r = None

    items = policy.smart_exemplars_jsonb or []
    pairs = [(str(it.get("label", "")).strip(), str(it.get("text", "")).strip())
             for it in items if isinstance(it, dict)]
    pairs = [(lbl, txt) for lbl, txt in pairs if lbl and txt]
    if not pairs:
        return {}

    vecs, _pt = await _embed_batch(model, channel, [t for _, t in pairs])
    out: dict[str, list[list[float]]] = {}
    for (lbl, _txt), v in zip(pairs, vecs):
        if v is None:
            continue
        out.setdefault(lbl, []).append(v)

    if out and r is not None:
        try:
            await r.set(
                _exemplar_cache_key(policy.id, policy.smart_embedding_version or 0),
                json.dumps(out),
                ex=EXEMPLAR_TTL,
            )
        except Exception:
            pass
    return out


async def _embed_prompt_cached(
    model: Model, channel: Channel, text: str
) -> tuple[Optional[list[float]], EmbeddingUsage]:
    """Returns (vector, usage). usage.status='cache' on hit (no tokens, no latency)."""
    started = time.time()
    from app.core.redis import get_redis

    key = _prompt_cache_key(model.id, text)
    try:
        r = get_redis()
        cached = await r.get(key)
        if cached:
            vec = json.loads(cached)
            if isinstance(vec, list):
                return vec, EmbeddingUsage(
                    model=model, channel=channel, upstream_model=model.upstream_model,
                    prompt_tokens=0, latency_ms=0, status="cache",
                )
    except Exception as e:
        log.debug("prompt embed cache read skipped: %s", e)
        r = None

    vec, pt, status = await _embed_one(model, channel, text)
    latency_ms = int((time.time() - started) * 1000)
    if vec is not None and r is not None:
        try:
            await r.set(key, json.dumps(vec), ex=PROMPT_TTL)
        except Exception:
            pass
    return vec, EmbeddingUsage(
        model=model, channel=channel, upstream_model=model.upstream_model,
        prompt_tokens=pt, latency_ms=latency_ms,
        status="ok" if vec is not None else "error",
    )


async def classify(
    policy: RoutePolicy,
    embed_model: Model,
    embed_channel: Channel,
    prompt_text: str,
    *,
    allowed_labels: Optional[set[str]] = None,
) -> tuple[Optional[str], float, Optional[EmbeddingUsage]]:
    """Return (label_or_None, best_score_0to1, usage).

    label is None when no exemplars resolved or best score < policy threshold;
    caller falls back to smart_default_label.
    """
    text = (prompt_text or "").strip()
    if not text:
        return None, 0.0, None

    exemplars = await _load_or_build_exemplars(policy, embed_model, embed_channel)
    if allowed_labels:
        exemplars = {k: v for k, v in exemplars.items() if k in allowed_labels}
    if not exemplars:
        return None, 0.0, None

    vec, usage = await _embed_prompt_cached(embed_model, embed_channel, text)
    if vec is None:
        return None, 0.0, usage

    best_label: Optional[str] = None
    best_score = -1.0
    for lbl, lvecs in exemplars.items():
        for ev in lvecs:
            s = _cos(vec, ev)
            if s > best_score:
                best_score = s
                best_label = lbl

    threshold = (policy.smart_score_threshold or 55) / 100.0
    if best_score < threshold:
        return None, max(best_score, 0.0), usage
    return best_label, best_score, usage
