from types import SimpleNamespace

import pytest

from app.models import Channel, Model, RouteStrategy
from app.services.providers.router import select_route


def _model(mid: int, channel_id: int = 1):
    return SimpleNamespace(id=mid, channel_id=channel_id, enabled=True)


def _channel(cid: int = 1):
    return SimpleNamespace(id=cid, enabled=True)


@pytest.mark.asyncio
async def test_smart_route_fallback_stays_with_chosen_label(monkeypatch):
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: 0.1)
    policy = SimpleNamespace(
        strategy=RouteStrategy.smart,
        targets_jsonb=[
            {"model_id": 1, "label": "code", "weight": 1, "fallback_order": 0},
            {"model_id": 2, "label": "writing", "weight": 1, "fallback_order": 1},
            {"model_id": 3, "label": "writing", "weight": 1, "fallback_order": 2},
            {"model_id": 4, "label": "default", "weight": 1, "fallback_order": 3},
        ],
        smart_rules_jsonb=[{"type": "keyword", "pattern": "draft|polish", "label": "writing"}],
        smart_default_label="default",
        smart_embedding_model_id=None,
    )
    models = {i: _model(i) for i in range(1, 5)}
    channels = {1: _channel(1)}

    decision = await select_route(
        policy,
        models,
        channels,
        prompt_text="Please draft a launch announcement.",
    )

    assert decision is not None
    assert decision.chosen_label == "writing"
    assert decision.model.id == 2
    assert [m.id for m, _ in decision.fallback_chain] == [3]


@pytest.mark.asyncio
async def test_smart_route_load_balances_same_label_by_weight(monkeypatch):
    draws = iter([5.0, 0.0])
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: next(draws))
    policy = SimpleNamespace(
        strategy=RouteStrategy.smart,
        targets_jsonb=[
            {"model_id": 1, "label": "code", "weight": 1, "fallback_order": 0},
            {"model_id": 2, "label": "writing", "weight": 1, "fallback_order": 1},
            {"model_id": 3, "label": "writing", "weight": 9, "fallback_order": 2},
            {"model_id": 4, "label": "default", "weight": 1, "fallback_order": 3},
        ],
        smart_rules_jsonb=[{"type": "keyword", "pattern": "draft|polish", "label": "writing"}],
        smart_default_label="default",
        smart_embedding_model_id=None,
    )
    models = {i: _model(i) for i in range(1, 5)}
    channels = {1: _channel(1)}

    decision = await select_route(
        policy,
        models,
        channels,
        prompt_text="Please draft a launch announcement.",
    )

    assert decision is not None
    assert decision.chosen_label == "writing"
    assert decision.model.id == 3
    assert [m.id for m, _ in decision.fallback_chain] == [2]


@pytest.mark.asyncio
async def test_smart_embedding_label_load_balances_same_label_by_weight(monkeypatch):
    draws = iter([5.0, 0.0])
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: next(draws))

    async def classify_stub(*_args, **_kwargs):
        assert _kwargs["allowed_labels"] == {"writing", "default"}
        return "writing", 0.95, None

    class FakeDB:
        async def get(self, cls, ident):
            if cls is Model:
                return SimpleNamespace(id=ident, channel_id=99, enabled=True, kind="embedding")
            if cls is Channel:
                return SimpleNamespace(id=ident, enabled=True)
            return None

    monkeypatch.setattr("app.services.providers.router.embed_classify", classify_stub)
    policy = SimpleNamespace(
        strategy=RouteStrategy.smart,
        targets_jsonb=[
            {"model_id": 2, "label": "writing", "weight": 1, "fallback_order": 0},
            {"model_id": 3, "label": "writing", "weight": 9, "fallback_order": 1},
            {"model_id": 4, "label": "default", "weight": 1, "fallback_order": 2},
        ],
        smart_rules_jsonb=[],
        smart_default_label="default",
        smart_embedding_model_id=99,
    )
    models = {i: _model(i) for i in range(2, 5)}
    channels = {1: _channel(1)}

    decision = await select_route(
        policy,
        models,
        channels,
        prompt_text="Please polish this article.",
        db=FakeDB(),
    )

    assert decision is not None
    assert decision.chosen_label == "writing"
    assert decision.model.id == 3
    assert [m.id for m, _ in decision.fallback_chain] == [2]


@pytest.mark.asyncio
async def test_smart_route_default_label_does_not_fallback_to_other_labels():
    policy = SimpleNamespace(
        strategy=RouteStrategy.smart,
        targets_jsonb=[
            {"model_id": 1, "label": "code", "weight": 1, "fallback_order": 0},
            {"model_id": 2, "label": "writing", "weight": 1, "fallback_order": 1},
            {"model_id": 3, "label": "default", "weight": 1, "fallback_order": 2},
        ],
        smart_rules_jsonb=[{"type": "keyword", "pattern": "never-match", "label": "writing"}],
        smart_default_label="default",
        smart_embedding_model_id=None,
    )
    models = {i: _model(i) for i in range(1, 4)}
    channels = {1: _channel(1)}

    decision = await select_route(policy, models, channels, prompt_text="hello")

    assert decision is not None
    assert decision.chosen_label == "default"
    assert decision.model.id == 3
    assert decision.fallback_chain == []