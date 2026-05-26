from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Model, RoutePolicy, RouteStrategy
from app.services.providers.smart_embedding import EmbeddingUsage, classify as embed_classify

log = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    model: Model
    channel: Channel
    fallback_chain: list[tuple[Model, Channel]]
    chosen_label: Optional[str] = None
    embedding_usage: Optional[EmbeddingUsage] = None


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


# ---------------------------------------------------------------------------
# Prompt extraction
# ---------------------------------------------------------------------------
def extract_prompt_text(payload: dict | None) -> str:
    """Best-effort: pull a representative prompt string out of an OpenAI-style payload."""
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("messages"), list):
        parts: list[str] = []
        for msg in payload["messages"]:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):  # OpenAI multimodal: list of {type, text}
                for piece in content:
                    if isinstance(piece, dict) and isinstance(piece.get("text"), str):
                        parts.append(piece["text"])
        return "\n".join(parts)
    inp = payload.get("input")
    if isinstance(inp, str):
        return inp
    if isinstance(inp, list):
        return "\n".join(x for x in inp if isinstance(x, str))
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        return prompt
    return ""


# ---------------------------------------------------------------------------
# Smart-mode rules
# ---------------------------------------------------------------------------
_CODE_FENCE_RE = re.compile(r"```")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _approx_token_count(text: str) -> int:
    return max(1, len(text) // 4)


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    total = cjk + latin
    return cjk / total if total else 0.0


PRESET_PREDICATES: dict[str, callable] = {
    "code_block": lambda t: bool(_CODE_FENCE_RE.search(t)),
    "long_prompt": lambda t: _approx_token_count(t) > 800,
    "short_prompt": lambda t: _approx_token_count(t) <= 80,
    "translate": lambda t: bool(re.search(r"translate|translation|翻译|翻成|译成", t, re.I)),
    "math": lambda t: bool(
        re.search(r"calculate|solve|equation|integral|derivative|prove|计算|求解|证明|方程|积分|导数|\\frac|\\int|\\sum", t, re.I)
    ),
    "reasoning": lambda t: bool(
        re.search(r"step[- ]by[- ]step|think step|reason through|chain of thought|逐步|推理|思考过程", t, re.I)
    ),
    "summarize": lambda t: bool(re.search(r"summari[sz]e|summary|tl;?dr|总结|概括|摘要", t, re.I)),
    "creative": lambda t: bool(re.search(r"story|poem|novel|creative|fiction|故事|诗|小说|剧本|续写", t, re.I)),
    "chinese": lambda t: _cjk_ratio(t) >= 0.3,
    "english": lambda t: _cjk_ratio(t) < 0.05 and bool(_LATIN_RE.search(t)),
}


def _apply_rules(rules: list[dict], prompt_text: str) -> Optional[str]:
    """Walk rules in order; return first matched label or None."""
    if not rules:
        return None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rtype = rule.get("type")
        label = rule.get("label")
        try:
            if rtype == "preset":
                pid = rule.get("id")
                pred = PRESET_PREDICATES.get(pid or "")
                if pred and label and pred(prompt_text):
                    return label
            elif rtype == "tokens":
                threshold = int(rule.get("threshold") or 0)
                gt_label = rule.get("gt_label")
                lte_label = rule.get("lte_label")
                tk = _approx_token_count(prompt_text)
                if tk > threshold and gt_label:
                    return gt_label
                if tk <= threshold and lte_label:
                    return lte_label
            elif rtype == "keyword":
                pattern = rule.get("pattern") or ""
                if pattern and label and re.search(pattern, prompt_text, re.IGNORECASE):
                    return label
            elif rtype == "code_block":
                if label and _CODE_FENCE_RE.search(prompt_text):
                    return label
        except Exception as e:
            log.warning("smart rule eval failed (%r): %s", rule, e)
    return None


# ---------------------------------------------------------------------------
# Smart strategy
# ---------------------------------------------------------------------------
async def _smart_pick(
    policy: RoutePolicy,
    pairs: list[tuple[Model, Channel, dict]],
    prompt_text: str,
    db: Optional[AsyncSession],
) -> tuple[list[tuple[Model, Channel]], Optional[str], Optional[EmbeddingUsage]]:
    """Return (ordered_chain, chosen_label, embedding_usage).

    Pipeline: rules → embedding classifier → smart_default_label.
    """
    labels = [str(t.get("label")) for _, _, t in pairs if t.get("label")]
    unique_labels = list(dict.fromkeys(labels))

    chosen_label: Optional[str] = None
    embedding_usage: Optional[EmbeddingUsage] = None
    if unique_labels and prompt_text:
        chosen_label = _apply_rules(policy.smart_rules_jsonb or [], prompt_text)
        if not chosen_label and policy.smart_embedding_model_id and db is not None:
            em = await db.get(Model, int(policy.smart_embedding_model_id))
            if em and em.enabled and em.kind == "embedding":
                ec = await db.get(Channel, em.channel_id)
                if ec and ec.enabled:
                    chosen_label, _score, embedding_usage = await embed_classify(
                        policy, em, ec, prompt_text,
                        allowed_labels=set(unique_labels),
                    )
    if not chosen_label:
        chosen_label = policy.smart_default_label

    head: Optional[tuple[Model, Channel]] = None
    rest: list[tuple[Model, Channel]] = []
    if chosen_label:
        for m, c, t in pairs:
            if head is None and t.get("label") == chosen_label:
                head = (m, c)
            else:
                rest.append((m, c))
    if head is None:
        return _weighted_order(pairs), chosen_label, embedding_usage
    return [head] + rest, chosen_label, embedding_usage


def _weighted_order(pairs: list[tuple[Model, Channel, dict]]) -> list[tuple[Model, Channel]]:
    weights = [max(int(t.get("weight", 1)), 0) for _, _, t in pairs]
    if sum(weights) <= 0:
        return [(m, c) for m, c, _ in pairs]
    chain: list[tuple[Model, Channel]] = []
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
    return chain


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------
async def select_route(
    policy: RoutePolicy,
    models_by_id: dict[int, Model],
    channels_by_id: dict[int, Channel],
    *,
    prompt_text: str | None = None,
    db: AsyncSession | None = None,
) -> Optional[RouteDecision]:
    pairs = _ordered_targets(policy, models_by_id, channels_by_id)
    if not pairs:
        return None

    chosen_label: Optional[str] = None
    embedding_usage: Optional[EmbeddingUsage] = None
    if policy.strategy == RouteStrategy.fallback:
        pairs.sort(key=lambda x: int(x[2].get("fallback_order", 0)))
        chain = [(m, c) for m, c, _ in pairs]
    elif policy.strategy == RouteStrategy.smart:
        chain, chosen_label, embedding_usage = await _smart_pick(policy, pairs, prompt_text or "", db)
    else:
        chain = _weighted_order(pairs)

    if not chain:
        return None
    return RouteDecision(
        model=chain[0][0],
        channel=chain[0][1],
        fallback_chain=chain[1:],
        chosen_label=chosen_label,
        embedding_usage=embedding_usage,
    )


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
