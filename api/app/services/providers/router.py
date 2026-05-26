from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Model, RoutePolicy, RouteStrategy

log = logging.getLogger(__name__)

CLASSIFY_CACHE_TTL = 3600  # 1h
CLASSIFY_PROMPT_LIMIT = 4096  # only hash first N chars for cache key + classifier input


@dataclass
class ClassifierUsage:
    model: Model
    channel: Channel
    upstream_model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str  # "ok" | "error"


@dataclass
class RouteDecision:
    model: Model
    channel: Channel
    fallback_chain: list[tuple[Model, Channel]]
    chosen_label: Optional[str] = None
    classifier_usage: Optional[ClassifierUsage] = None


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


# Built-in preset predicates: id -> (description, predicate(text) -> bool).
# Keeps admin UI free of regex. Length thresholds use char-count/4 ≈ token count.
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
# Classifier
# ---------------------------------------------------------------------------
def _cache_key(classifier_model_id: int, labels: list[str], prompt_text: str) -> str:
    h = hashlib.sha256()
    h.update(str(classifier_model_id).encode())
    h.update(b"|")
    h.update("\x1f".join(sorted(labels)).encode())
    h.update(b"|")
    h.update(prompt_text[:CLASSIFY_PROMPT_LIMIT].encode("utf-8", errors="ignore"))
    return f"llmxy:smart:cls:{h.hexdigest()}"


def _build_classifier_messages(labels: list[str], prompt_text: str, hint: Optional[str] = None) -> list[dict]:
    label_list = ", ".join(labels)
    system = (
        "You are a routing classifier. Read the user request below and answer with "
        f"EXACTLY one of these labels: {label_list}. "
        "Reply with the label only — no punctuation, no explanation."
    )
    if hint:
        system += f"\nRouting guidance: {hint.strip()}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt_text[:CLASSIFY_PROMPT_LIMIT]},
    ]


async def _classify(
    classifier_model: Model,
    classifier_channel: Channel,
    labels: list[str],
    prompt_text: str,
    hint: Optional[str] = None,
) -> tuple[Optional[str], Optional[ClassifierUsage]]:
    """Call the classifier model; return (matched_label_or_None, usage_or_None).

    Usage is recorded for billing; None only when no call was attempted.
    """
    if not labels or not prompt_text.strip():
        return None, None
    import time as _time
    started = _time.time()
    try:
        from app.services import providers as _p

        adapter = _p.get_adapter(classifier_channel.provider_type)
        if not adapter:
            return None, None
        body = {
            "model": classifier_model.upstream_model,
            "messages": _build_classifier_messages(labels, prompt_text, hint),
            "max_completion_tokens": 16,
        }
        result = await adapter.chat(classifier_channel, classifier_model.upstream_model, body, stream=False)
        latency_ms = int((_time.time() - started) * 1000)
        pt = getattr(result, "prompt_tokens", 0) or 0
        ct = getattr(result, "completion_tokens", 0) or 0
        ok = result.status == 200 and bool(result.body)
        if not ok:
            msg = (
                f"smart classifier upstream non-200 status={result.status} "
                f"model={classifier_model.upstream_model} body={str(result.body)[:1000]}"
            )
            log.warning(msg)
            try:
                with open("/tmp/llmxy-classifier.log", "a") as _f:
                    _f.write(f"[{_time.strftime('%H:%M:%S', _time.localtime())}] {msg}\n")
            except Exception:
                pass
        usage = ClassifierUsage(
            model=classifier_model,
            channel=classifier_channel,
            upstream_model=classifier_model.upstream_model,
            prompt_tokens=pt,
            completion_tokens=ct,
            latency_ms=latency_ms,
            status="ok" if ok else "error",
        )
        if not ok:
            return None, usage
        choices = (result.body or {}).get("choices") or []
        if not choices:
            return None, usage
        content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "", content)[:64].lower()
        for lbl in labels:
            if lbl.lower() == cleaned or lbl.lower() == content.lower():
                return lbl, usage
        for lbl in labels:
            if lbl.lower() in content.lower():
                return lbl, usage
        return None, usage
    except Exception as e:
        log.warning("smart classifier call failed: %s", e)
        try:
            with open("/tmp/llmxy-classifier.log", "a") as _f:
                _f.write(f"[{_time.strftime('%H:%M:%S', _time.localtime())}] exception: {e!r}\n")
        except Exception:
            pass
        latency_ms = int((_time.time() - started) * 1000)
        return None, ClassifierUsage(
            model=classifier_model,
            channel=classifier_channel,
            upstream_model=classifier_model.upstream_model,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            status="error",
        )


async def _classify_cached(
    classifier_model: Model,
    classifier_channel: Channel,
    labels: list[str],
    prompt_text: str,
    hint: Optional[str] = None,
) -> tuple[Optional[str], Optional[ClassifierUsage]]:
    """Returns (label, usage). usage is None on cache hit (no call made)."""
    key = _cache_key(classifier_model.id, labels, prompt_text)
    try:
        from app.core.redis import get_redis

        r = get_redis()
        cached = await r.get(key)
        if cached:
            return (cached if cached in labels else None), None
    except Exception as e:
        log.debug("smart classify cache read skipped: %s", e)
        r = None

    label, usage = await _classify(classifier_model, classifier_channel, labels, prompt_text, hint)
    if label and r is not None:
        try:
            await r.set(key, label, ex=CLASSIFY_CACHE_TTL)
        except Exception:
            pass
    return label, usage


# ---------------------------------------------------------------------------
# Smart strategy
# ---------------------------------------------------------------------------
async def _smart_pick(
    policy: RoutePolicy,
    pairs: list[tuple[Model, Channel, dict]],
    prompt_text: str,
    db: Optional[AsyncSession],
) -> tuple[list[tuple[Model, Channel]], Optional[str], Optional[ClassifierUsage]]:
    """Return (ordered_chain, chosen_label, classifier_usage)."""
    labels = [str(t.get("label")) for _, _, t in pairs if t.get("label")]
    unique_labels = list(dict.fromkeys(labels))

    chosen_label: Optional[str] = None
    classifier_usage: Optional[ClassifierUsage] = None
    if unique_labels and prompt_text:
        chosen_label = _apply_rules(policy.smart_rules_jsonb or [], prompt_text)
        if not chosen_label and policy.smart_classifier_model_id and db is not None:
            cm = await db.get(Model, int(policy.smart_classifier_model_id))
            if cm and cm.enabled:
                cc = await db.get(Channel, cm.channel_id)
                if cc and cc.enabled:
                    chosen_label, classifier_usage = await _classify_cached(
                        cm, cc, unique_labels, prompt_text,
                        getattr(policy, "smart_classifier_hint", None),
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
        return _weighted_order(pairs), chosen_label, classifier_usage
    return [head] + rest, chosen_label, classifier_usage


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
    classifier_usage: Optional[ClassifierUsage] = None
    if policy.strategy == RouteStrategy.fallback:
        pairs.sort(key=lambda x: int(x[2].get("fallback_order", 0)))
        chain = [(m, c) for m, c, _ in pairs]
    elif policy.strategy == RouteStrategy.smart:
        chain, chosen_label, classifier_usage = await _smart_pick(policy, pairs, prompt_text or "", db)
    else:
        chain = _weighted_order(pairs)

    if not chain:
        return None
    return RouteDecision(
        model=chain[0][0],
        channel=chain[0][1],
        fallback_chain=chain[1:],
        chosen_label=chosen_label,
        classifier_usage=classifier_usage,
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
