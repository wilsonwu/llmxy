from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.models import Channel, Model, RoutePolicy, RouteStrategy
from app.services import geo
from app.services.providers.smart_embedding import EmbeddingUsage
from app.services.providers.smart_embedding import classify as embed_classify

log = logging.getLogger(__name__)

DEFAULT_TARGET_LABEL = "default"
_ROUND_ROBIN_KEY_PREFIX = "llmxy:route:round_robin"
_local_round_robin_cursors: defaultdict[str, int] = defaultdict(int)


@dataclass
class RouteDecision:
    model: Model
    channel: Channel
    fallback_chain: list[tuple[Model, Channel]]
    chosen_label: Optional[str] = None
    embedding_usage: Optional[EmbeddingUsage] = None


@dataclass
class RuleContext:
    """All signals available to a smart-routing rule evaluator. New routing
    dimensions (header, time-of-day, user_id, plan tier, ...) get added here
    once and become reachable by any rule type without changing the call
    chain."""
    prompt_text: str = ""
    client_ip: Optional[str] = None


async def load_route_resources(
    db: AsyncSession,
    policy: RoutePolicy,
) -> Optional[tuple[dict[int, Model], dict[int, Channel]]]:
    target_ids = list(dict.fromkeys(int(target["model_id"]) for target in (policy.targets_jsonb or [])))
    if not target_ids:
        return None
    fallback_model_id = getattr(policy, "fallback_model_id", None)
    if fallback_model_id is not None and int(fallback_model_id) not in target_ids:
        target_ids.append(int(fallback_model_id))

    models = (await db.execute(select(Model).where(Model.id.in_(target_ids)))).scalars().all()
    models_by_id = {model.id: model for model in models}
    channel_ids = {model.channel_id for model in models}
    channels = (await db.execute(select(Channel).where(Channel.id.in_(channel_ids)))).scalars().all()
    return models_by_id, {channel.id: channel for channel in channels}


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


def _target_label(target: dict) -> str:
    label = target.get("label")
    if label is None:
        return DEFAULT_TARGET_LABEL
    normalized = str(label).strip()
    return normalized or DEFAULT_TARGET_LABEL


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


def _apply_rules(rules: list[dict], ctx: RuleContext) -> Optional[str]:
    """Walk rules in order, dispatch to the registered evaluator for each
    rule type, return the first matched label or None. Adding a new rule
    type = add a function + register it in `_RULE_EVALUATORS`."""
    if not rules:
        return None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rtype = rule.get("type")
        ev = _RULE_EVALUATORS.get(rtype or "")
        if ev is None:
            continue
        try:
            label = ev(rule, ctx)
            if label:
                return label
        except Exception as e:
            log.warning("smart rule eval failed (%r): %s", rule, e)
    return None


# ---- evaluators -----------------------------------------------------------

def _eval_preset(rule: dict, ctx: RuleContext) -> Optional[str]:
    pid = rule.get("id")
    label = rule.get("label")
    pred = PRESET_PREDICATES.get(pid or "")
    if pred and label and pred(ctx.prompt_text):
        return label
    return None


def _eval_tokens(rule: dict, ctx: RuleContext) -> Optional[str]:
    threshold = int(rule.get("threshold") or 0)
    gt_label = rule.get("gt_label")
    lte_label = rule.get("lte_label")
    tk = _approx_token_count(ctx.prompt_text)
    if tk > threshold and gt_label:
        return gt_label
    if tk <= threshold and lte_label:
        return lte_label
    return None


def _eval_keyword(rule: dict, ctx: RuleContext) -> Optional[str]:
    pattern = rule.get("pattern") or ""
    label = rule.get("label")
    if pattern and label and re.search(pattern, ctx.prompt_text, re.IGNORECASE):
        return label
    return None


def _eval_code_block(rule: dict, ctx: RuleContext) -> Optional[str]:
    label = rule.get("label")
    if label and _CODE_FENCE_RE.search(ctx.prompt_text):
        return label
    return None


def _eval_geo(rule: dict, ctx: RuleContext) -> Optional[str]:
    """Match by country code of the client IP. Silently no-ops when the
    GeoIP DB is unconfigured or the IP isn't resolvable — the route then
    continues to the next rule, then the implicit default target label."""
    if not ctx.client_ip:
        log.info("geo rule: skipped (no client_ip)")
        return None
    label = rule.get("label")
    raw = rule.get("countries") or []
    if not label or not raw:
        log.info("geo rule: skipped (incomplete rule label=%r countries=%r)", label, raw)
        return None
    wanted = {str(c).upper() for c in raw if c}
    cc = geo.lookup_country(ctx.client_ip)
    log.info(
        "geo rule: ip=%s resolved=%s wanted=%s label=%s -> %s",
        ctx.client_ip, cc, sorted(wanted), label,
        "MATCH" if cc and cc.upper() in wanted else "miss",
    )
    if cc and cc.upper() in wanted:
        return label
    return None


RuleEvaluator = Callable[[dict, RuleContext], Optional[str]]
_RULE_EVALUATORS: dict[str, RuleEvaluator] = {
    "preset": _eval_preset,
    "tokens": _eval_tokens,
    "keyword": _eval_keyword,
    "code_block": _eval_code_block,
    "geo": _eval_geo,
}


# ---------------------------------------------------------------------------
# Smart strategy
# ---------------------------------------------------------------------------
async def _smart_pick(
    policy: RoutePolicy,
    pairs: list[tuple[Model, Channel, dict]],
    ctx: RuleContext,
    db: Optional[AsyncSession],
) -> tuple[Optional[tuple[Model, Channel]], Optional[str], Optional[EmbeddingUsage]]:
    """Return (primary, chosen_label, embedding_usage).

    Mode is mutually exclusive: if `smart_embedding_model_id` is set the
    classifier is the sole decider (rules are ignored even if present);
    otherwise the rule list decides. Unmatched requests use targets without
    an explicit label, which belong to the implicit `default` label.
    """
    labels = [_target_label(t) for _, _, t in pairs]
    unique_labels = list(dict.fromkeys(labels))

    chosen_label: Optional[str] = None
    embedding_usage: Optional[EmbeddingUsage] = None
    if unique_labels:
        if policy.smart_embedding_model_id:
            if ctx.prompt_text and db is not None:
                em = await db.get(Model, int(policy.smart_embedding_model_id))
                if em and em.enabled and em.kind == "embedding":
                    ec = await db.get(Channel, em.channel_id)
                    if ec and ec.enabled:
                        chosen_label, _score, embedding_usage = await embed_classify(
                            policy, em, ec, ctx.prompt_text,
                            allowed_labels=set(unique_labels),
                        )
        else:
            chosen_label = _apply_rules(policy.smart_rules_jsonb or [], ctx)
    if chosen_label:
        chosen_label = str(chosen_label).strip() or None

    if chosen_label:
        matched = [(m, c, t) for m, c, t in pairs if _target_label(t) == chosen_label]
        if matched:
            return _weighted_pick(matched), chosen_label, embedding_usage

    default_pairs = [(m, c, t) for m, c, t in pairs if _target_label(t) == DEFAULT_TARGET_LABEL]
    if default_pairs:
        return _weighted_pick(default_pairs), DEFAULT_TARGET_LABEL, embedding_usage

    return None, chosen_label, embedding_usage


def _weighted_pick(pairs: list[tuple[Model, Channel, dict]]) -> Optional[tuple[Model, Channel]]:
    weights = [max(int(t.get("weight", 1)), 0) for _, _, t in pairs]
    total = sum(weights)
    if total <= 0:
        return (pairs[0][0], pairs[0][1]) if pairs else None
    draw = random.uniform(0, total)
    accumulated = 0.0
    for (model, channel, _), weight in zip(pairs, weights):
        accumulated += weight
        if draw <= accumulated:
            return model, channel
    return pairs[-1][0], pairs[-1][1]


async def _round_robin_pick(
    policy: RoutePolicy,
    pairs: list[tuple[Model, Channel, dict]],
) -> Optional[tuple[Model, Channel]]:
    # ext_proc must resolve a concrete model before Envoy enters the translator,
    # so the shared cursor gives Envoy and API-direct the same rotation.
    unique_pairs: list[tuple[Model, Channel, dict]] = []
    seen_model_ids: set[int] = set()
    for pair in pairs:
        model_id = pair[0].id
        if model_id in seen_model_ids:
            continue
        seen_model_ids.add(model_id)
        unique_pairs.append(pair)
    if not unique_pairs:
        return None

    policy_key = str(getattr(policy, "id", None) or getattr(policy, "user_facing_model", "unknown"))
    cursor_key = f"{_ROUND_ROBIN_KEY_PREFIX}:{policy_key}"
    try:
        cursor = int(await asyncio.wait_for(get_redis().incr(cursor_key), timeout=0.25)) - 1
    except Exception as exc:  # noqa: BLE001
        cursor = _local_round_robin_cursors[cursor_key]
        _local_round_robin_cursors[cursor_key] = cursor + 1
        log.warning("round-robin Redis cursor unavailable for route %s: %s", policy_key, exc)

    model, channel, _ = unique_pairs[cursor % len(unique_pairs)]
    return model, channel


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------
async def select_route(
    policy: RoutePolicy,
    models_by_id: dict[int, Model],
    channels_by_id: dict[int, Channel],
    *,
    prompt_text: str | None = None,
    client_ip: str | None = None,
    db: AsyncSession | None = None,
) -> Optional[RouteDecision]:
    pairs = _ordered_targets(policy, models_by_id, channels_by_id)

    chosen_label: Optional[str] = None
    embedding_usage: Optional[EmbeddingUsage] = None
    if policy.strategy == RouteStrategy.smart:
        ctx = RuleContext(prompt_text=prompt_text or "", client_ip=client_ip)
        primary, chosen_label, embedding_usage = await _smart_pick(policy, pairs, ctx, db)
    elif policy.strategy == RouteStrategy.round_robin:
        primary = await _round_robin_pick(policy, pairs)
    else:
        primary = _weighted_pick(pairs)

    chain = [primary] if primary else []
    fallback_model_id = getattr(policy, "fallback_model_id", None)
    if fallback_model_id and (not primary or int(fallback_model_id) != primary[0].id):
        fallback_model = models_by_id.get(int(fallback_model_id))
        fallback_channel = channels_by_id.get(fallback_model.channel_id) if fallback_model else None
        if fallback_model and fallback_model.enabled and fallback_channel and fallback_channel.enabled:
            chain.append((fallback_model, fallback_channel))

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
