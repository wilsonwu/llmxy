from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.internal import translate as translate_module
from app.api.relay import embeddings as embeddings_module
from app.api.relay import images as images_module
from app.api.relay.chat import _load_route as load_api_route
from app.api.v1.admin.routes import _validate_modality
from app.models import Channel, Model, RouteScope, RouteStrategy
from app.schemas import RoutePolicyIn
from app.services.embedding_relay import execute_embedding_relay
from app.services.envoy.ext_proc_server import _load_route as load_envoy_route
from app.services.providers.router import select_route


def _model(mid: int, channel_id: int = 1):
    return SimpleNamespace(id=mid, channel_id=channel_id, enabled=True)


def _channel(cid: int = 1):
    return SimpleNamespace(id=cid, enabled=True)


def test_route_schema_rejects_legacy_fallback_strategy():
    with pytest.raises(ValidationError):
        RoutePolicyIn(user_facing_model="legacy", strategy="fallback")


def test_route_schema_allows_target_model_as_fallback():
    route = RoutePolicyIn(
        user_facing_model="weighted-with-fallback",
        strategy="weighted",
        targets_jsonb=[{"model_id": 1, "weight": 1}],
        fallback_model_id=1,
    )

    assert route.fallback_model_id == 1


def test_route_schema_allows_smart_without_fallback_and_with_unlabeled_default():
    route = RoutePolicyIn(
        user_facing_model="smart-with-default",
        strategy="smart",
        targets_jsonb=[{"model_id": 1, "weight": 1}],
    )

    assert route.fallback_model_id is None
    assert route.targets_jsonb[0].label is None


def test_route_schema_allows_smart_fallback_also_present_in_targets():
    route = RoutePolicyIn(
        user_facing_model="smart-with-fallback",
        strategy="smart",
        targets_jsonb=[{"model_id": 1, "weight": 1, "label": "writing"}],
        fallback_model_id=1,
    )

    assert route.fallback_model_id == 1


@pytest.mark.asyncio
async def test_route_validation_allows_target_model_as_fallback():
    model = SimpleNamespace(id=1, code="model-1", kind="chat")

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [model]

    class FakeDB:
        async def execute(self, _statement):
            return Result()

    route = RoutePolicyIn(
        user_facing_model="weighted-with-fallback",
        strategy="weighted",
        targets_jsonb=[{"model_id": 1, "weight": 1}],
        fallback_model_id=1,
    )

    await _validate_modality(FakeDB(), route)


@pytest.mark.asyncio
async def test_route_validation_rejects_fallback_without_primary_target():
    route = RoutePolicyIn(
        user_facing_model="fallback-only",
        strategy="weighted",
        fallback_model_id=1,
    )

    with pytest.raises(HTTPException, match="at least one target model") as exc_info:
        await _validate_modality(SimpleNamespace(), route)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_embedding_relay_uses_selected_fallback_after_primary_failure(monkeypatch):
    attempts: list[int] = []

    class Adapter:
        async def embeddings(self, channel, _upstream_model, _payload):
            attempts.append(channel.id)
            if channel.id == 1:
                return 500, {"error": "primary failed"}
            return 200, {"data": [], "usage": {"prompt_tokens": 3}}

    monkeypatch.setattr("app.services.embedding_relay.providers.resolve_connector_type", lambda *_: "test")
    monkeypatch.setattr("app.services.embedding_relay.providers.resolve_upstream_protocol", lambda *_: "test")
    monkeypatch.setattr("app.services.embedding_relay.providers.get_connector_adapter", lambda *_: Adapter())
    monkeypatch.setattr("app.services.embedding_relay.providers.connector_supports_protocol", lambda *_: True)
    primary = SimpleNamespace(id=1, upstream_model="primary")
    fallback = SimpleNamespace(id=2, upstream_model="fallback")

    body, selected_model, selected_channel = await execute_embedding_relay(
        [(primary, _channel(1)), (fallback, _channel(2))],
        {"input": "hello"},
    )

    assert attempts == [1, 2]
    assert body["usage"]["prompt_tokens"] == 3
    assert selected_model.id == 2
    assert selected_channel.id == 2


@pytest.mark.asyncio
async def test_direct_embedding_bills_successful_fallback(monkeypatch):
    primary = SimpleNamespace(id=1, channel_id=1, enabled=True, upstream_model="primary")
    fallback = SimpleNamespace(id=2, channel_id=2, enabled=True, upstream_model="fallback")
    primary_channel = _channel(1)
    fallback_channel = _channel(2)
    decision = SimpleNamespace(
        model=primary,
        channel=primary_channel,
        fallback_chain=[(fallback, fallback_channel)],
        chosen_label=None,
        embedding_usage=None,
    )

    async def has_quota_stub(*_args):
        return True, ""

    async def load_route_stub(*_args, **_kwargs):
        return SimpleNamespace(), {1: primary, 2: fallback}, {1: primary_channel, 2: fallback_channel}

    async def select_route_stub(*_args, **kwargs):
        assert kwargs["client_ip"] == "203.0.113.10"
        return decision

    async def relay_stub(candidates, _payload):
        assert [model.id for model, _ in candidates] == [1, 2]
        return {"data": [], "usage": {"prompt_tokens": 4}}, fallback, fallback_channel

    charged: list[int] = []

    def cost_stub(model, prompt_tokens, completion_tokens):
        assert (model.id, prompt_tokens, completion_tokens) == (2, 4, 0)
        return 7

    async def charge_stub(*_args, **_kwargs):
        charged.append(_args[3])

    async def record_smart_stub(*_args, **_kwargs):
        return None

    monkeypatch.setattr(embeddings_module, "has_quota", has_quota_stub)
    monkeypatch.setattr(embeddings_module, "_load_route", load_route_stub)
    monkeypatch.setattr(embeddings_module.providers, "select_route", select_route_stub)
    monkeypatch.setattr(embeddings_module, "execute_embedding_relay", relay_stub)
    monkeypatch.setattr(embeddings_module, "calc_cost_cents", cost_stub)
    monkeypatch.setattr(embeddings_module, "charge_user", charge_stub)
    monkeypatch.setattr(embeddings_module, "_record_smart_usage", record_smart_stub)

    class Request:
        def __init__(self):
            self.headers = {"x-forwarded-for": "203.0.113.10, 10.0.0.1"}
            self.client = None

        async def json(self):
            return {"model": "public-model", "input": "hello"}

    class DB:
        def __init__(self):
            self.info = {}
            self.rows = []

        def add(self, row):
            self.rows.append(row)

        async def commit(self):
            return None

    db = DB()
    response = await embeddings_module.embeddings(
        Request(),
        (SimpleNamespace(id=9), SimpleNamespace(id=8)),
        db,
    )

    assert response.status_code == 200
    assert charged == [7]
    assert db.rows[0].model_id == 2
    assert db.rows[0].upstream_model == "fallback"


@pytest.mark.asyncio
async def test_direct_image_records_classifier_with_relay_transaction(monkeypatch):
    model = SimpleNamespace(id=1, channel_id=1, enabled=True, kind="image", code="image-model")
    channel = _channel(1)
    decision = SimpleNamespace(
        model=model,
        channel=channel,
        fallback_chain=[],
        chosen_label="creative",
        embedding_usage=SimpleNamespace(prompt_tokens=3),
    )
    recorded: list[str] = []

    async def has_quota_stub(*_args):
        return True, ""

    async def rate_limit_stub(*_args, **_kwargs):
        return True

    async def user_rpm_stub(*_args):
        return 60

    async def load_route_stub(*_args, **_kwargs):
        return SimpleNamespace(), {1: model}, {1: channel}

    async def select_route_stub(*_args, **kwargs):
        assert kwargs["client_ip"] == "198.51.100.7"
        return decision

    async def relay_stub(db, **kwargs):
        assert kwargs["request_id"].startswith("req-")
        assert kwargs["resolved_label"] == "creative"
        db.rows.append("relay")
        return 200, {"data": [{"url": "https://example.test/image.png"}]}

    async def record_smart_stub(db, *_args, **_kwargs):
        assert db.rows == ["relay"]
        recorded.append("classifier")

    monkeypatch.setattr(images_module, "has_quota", has_quota_stub)
    monkeypatch.setattr(images_module, "rate_limit", rate_limit_stub)
    monkeypatch.setattr(images_module, "user_rpm", user_rpm_stub)
    monkeypatch.setattr(images_module, "_load_route", load_route_stub)
    monkeypatch.setattr(images_module.providers, "select_route", select_route_stub)
    monkeypatch.setattr(images_module, "execute_image_relay", relay_stub)
    monkeypatch.setattr(images_module, "_record_smart_usage", record_smart_stub)

    class Request:
        def __init__(self):
            self.headers = {"x-real-ip": "198.51.100.7"}
            self.client = None

        async def json(self):
            return {"model": "public-image", "prompt": "draw a skyline"}

    class DB:
        def __init__(self):
            self.info = {}
            self.rows: list[str] = []
            self.commits = 0

        async def commit(self):
            self.commits += 1

    db = DB()
    response = await images_module.images_generations(
        Request(),
        (SimpleNamespace(id=9), SimpleNamespace(id=8)),
        db,
    )

    assert response.status_code == 200
    assert recorded == ["classifier"]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_envoy_embedding_translator_consumes_fallback_chain(monkeypatch):
    models = {
        1: SimpleNamespace(id=1, enabled=True, kind="embedding", upstream_model="primary"),
        2: SimpleNamespace(id=2, enabled=True, kind="embedding", upstream_model="fallback"),
    }
    channels = {1: _channel(1), 2: _channel(2)}

    class DB:
        async def get(self, model_type, ident):
            return models.get(ident) if model_type is Model else channels.get(ident)

    class SessionContext:
        async def __aenter__(self):
            return DB()

        async def __aexit__(self, *_args):
            return None

    captured: list[int] = []
    billed: list[int] = []

    async def relay_stub(candidates, _payload):
        captured.extend(model.id for model, _ in candidates)
        return {"data": [], "usage": {"prompt_tokens": 4}}, models[2], channels[2]

    async def billing_context_stub(*_args, **_kwargs):
        assert _kwargs["model_id"] == "2"
        return SimpleNamespace(id=8), SimpleNamespace(id=9), models[2]

    async def bill_stub(*_args, **kwargs):
        assert kwargs["model"].id == 2
        assert kwargs["prompt_tokens"] == 4
        billed.append(kwargs["model"].id)

    class Request:
        async def json(self):
            return {"model": "public-model", "input": "hello"}

    monkeypatch.setattr("app.db.session.AsyncSessionLocal", lambda: SessionContext())
    monkeypatch.setattr(translate_module, "execute_embedding_relay", relay_stub)
    monkeypatch.setattr(translate_module, "_billing_context", billing_context_stub)
    monkeypatch.setattr(translate_module, "_bill_chat_relay", bill_stub)

    response = await translate_module.embeddings(
        Request(),
        x_llmxy_channel_id="1",
        x_llmxy_model_id="1",
        x_llmxy_user_id="8",
        x_llmxy_api_key_id="9",
        x_llmxy_user_facing_model="public-model",
        x_llmxy_upstream_model="primary",
        x_llmxy_upstream_protocol="openai.embeddings",
        x_llmxy_connector_type="openai",
        x_llmxy_embedding_chain="1:1,2:2",
        x_llmxy_request_id="req-test",
        x_llmxy_resolved_label=None,
        x_llmxy_classifier_model_id=None,
        x_llmxy_classifier_upstream_model=None,
        x_llmxy_classifier_prompt_tokens=None,
        x_llmxy_classifier_latency_ms=None,
        x_llmxy_classifier_status=None,
    )

    assert response.status_code == 200
    assert captured == [1, 2]
    assert billed == [2]


@pytest.mark.asyncio
async def test_envoy_image_translator_deduplicates_chain_and_records_classifier(monkeypatch):
    models = {
        1: SimpleNamespace(id=1, enabled=False, kind="image", upstream_model="disabled", code="disabled"),
        2: SimpleNamespace(id=2, enabled=True, kind="image", upstream_model="fallback", code="fallback"),
    }
    channels = {1: _channel(1), 2: _channel(2)}
    user = SimpleNamespace(id=8)
    api_key = SimpleNamespace(id=9)

    class DB:
        def __init__(self):
            self.info = {}
            self.commits = 0

        async def get(self, model_type, ident):
            if model_type is Model:
                return models.get(ident)
            if model_type is Channel:
                return channels.get(ident)
            if model_type.__name__ == "User":
                return user
            return api_key

        async def commit(self):
            self.commits += 1

    db = DB()

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    captured: list[int] = []
    classifier_request_ids: list[str] = []

    async def relay_stub(_db, **kwargs):
        captured.extend(model.id for model, _ in kwargs["candidates"])
        assert kwargs["request_id"] == "req-image"
        assert kwargs["resolved_label"] == "creative"
        return 200, {"data": [{"url": "https://example.test/image.png"}]}

    async def classifier_stub(_db, **kwargs):
        classifier_request_ids.append(kwargs["request_id"])
        assert kwargs["cls_model_id"] == "77"

    class Request:
        async def json(self):
            return {"model": "public-image", "prompt": "draw a skyline"}

    monkeypatch.setattr("app.db.session.AsyncSessionLocal", lambda: SessionContext())
    monkeypatch.setattr(translate_module, "execute_image_relay", relay_stub)
    monkeypatch.setattr(translate_module, "_record_classifier_from_headers", classifier_stub)

    response = await translate_module.images_generations(
        Request(),
        x_llmxy_channel_id="1",
        x_llmxy_model_id="1",
        x_llmxy_user_id="8",
        x_llmxy_api_key_id="9",
        x_llmxy_user_facing_model="public-image",
        x_llmxy_image_chain="1:1,1:1,2:2",
        x_llmxy_request_id="req-image",
        x_llmxy_resolved_label="creative",
        x_llmxy_classifier_model_id="77",
        x_llmxy_classifier_upstream_model="embedding-model",
        x_llmxy_classifier_prompt_tokens="3",
        x_llmxy_classifier_latency_ms="5",
        x_llmxy_classifier_status="ok",
    )

    assert response.status_code == 200
    assert captured == [2]
    assert classifier_request_ids == ["req-image"]
    assert db.commits == 1


@pytest.mark.parametrize("loader", [load_api_route, load_envoy_route])
@pytest.mark.asyncio
async def test_route_loaders_include_fallback_outside_targets(loader):
    policy = SimpleNamespace(
        enabled=True,
        scope=RouteScope.public,
        targets_jsonb=[{"model_id": 1, "weight": 1}],
        fallback_model_id=2,
    )
    models = [_model(1, 1), _model(2, 2)]
    channels = [_channel(1), _channel(2)]

    class Result:
        def __init__(self, *, one=None, rows=None):
            self.one = one
            self.rows = rows or []

        def scalar_one_or_none(self):
            return self.one

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class FakeDB:
        def __init__(self):
            self.call_count = 0

        async def execute(self, statement):
            self.call_count += 1
            if self.call_count == 1:
                return Result(one=policy)
            if self.call_count == 2:
                selected_ids = next(
                    value for value in statement.compile().params.values() if isinstance(value, list)
                )
                assert set(selected_ids) == {1, 2}
                return Result(rows=models)
            return Result(rows=channels)

    loaded_policy, models_by_id, channels_by_id = await loader(FakeDB(), "public-model")

    assert loaded_policy is policy
    assert set(models_by_id) == {1, 2}
    assert set(channels_by_id) == {1, 2}


@pytest.mark.asyncio
async def test_weighted_route_without_fallback_selects_only_primary(monkeypatch):
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: 0.1)
    policy = SimpleNamespace(
        strategy=RouteStrategy.weighted,
        targets_jsonb=[
            {"model_id": 1, "weight": 1},
            {"model_id": 2, "weight": 1},
        ],
        fallback_model_id=None,
    )
    models = {i: _model(i) for i in range(1, 3)}
    channels = {1: _channel(1)}

    decision = await select_route(policy, models, channels)

    assert decision is not None
    assert decision.model.id == 1
    assert decision.fallback_chain == []


@pytest.mark.asyncio
async def test_weighted_route_with_fallback_uses_selected_model_only(monkeypatch):
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: 0.1)
    policy = SimpleNamespace(
        strategy=RouteStrategy.weighted,
        targets_jsonb=[
            {"model_id": 1, "weight": 1},
            {"model_id": 2, "weight": 1},
        ],
        fallback_model_id=3,
    )
    models = {i: _model(i) for i in range(1, 4)}
    channels = {1: _channel(1)}

    decision = await select_route(policy, models, channels)

    assert decision is not None
    assert decision.model.id == 1
    assert [m.id for m, _ in decision.fallback_chain] == [3]


@pytest.mark.asyncio
async def test_weighted_route_allows_fallback_also_present_in_targets(monkeypatch):
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: 0.1)
    policy = SimpleNamespace(
        strategy=RouteStrategy.weighted,
        targets_jsonb=[
            {"model_id": 1, "weight": 1},
            {"model_id": 2, "weight": 1},
        ],
        fallback_model_id=2,
    )
    models = {i: _model(i) for i in range(1, 3)}
    channels = {1: _channel(1)}

    decision = await select_route(policy, models, channels)

    assert decision is not None
    assert decision.model.id == 1
    assert [m.id for m, _ in decision.fallback_chain] == [2]


@pytest.mark.asyncio
async def test_weighted_route_does_not_retry_fallback_selected_as_primary(monkeypatch):
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: 1.5)
    policy = SimpleNamespace(
        strategy=RouteStrategy.weighted,
        targets_jsonb=[
            {"model_id": 1, "weight": 1},
            {"model_id": 2, "weight": 1},
        ],
        fallback_model_id=2,
    )
    models = {i: _model(i) for i in range(1, 3)}
    channels = {1: _channel(1)}

    decision = await select_route(policy, models, channels)

    assert decision is not None
    assert decision.model.id == 2
    assert decision.fallback_chain == []


@pytest.mark.asyncio
async def test_smart_route_uses_only_explicit_fallback_after_labeled_primary(monkeypatch):
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: 0.1)
    policy = SimpleNamespace(
        strategy=RouteStrategy.smart,
        targets_jsonb=[
            {"model_id": 1, "label": "code", "weight": 1},
            {"model_id": 2, "label": "writing", "weight": 1},
            {"model_id": 3, "label": "writing", "weight": 1},
        ],
        fallback_model_id=4,
        smart_rules_jsonb=[{"type": "keyword", "pattern": "draft|polish", "label": "writing"}],
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
    assert [m.id for m, _ in decision.fallback_chain] == [4]


@pytest.mark.asyncio
async def test_smart_route_load_balances_same_label_by_weight(monkeypatch):
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: 5.0)
    policy = SimpleNamespace(
        strategy=RouteStrategy.smart,
        targets_jsonb=[
            {"model_id": 1, "label": "code", "weight": 1},
            {"model_id": 2, "label": "writing", "weight": 1},
            {"model_id": 3, "label": "writing", "weight": 9},
        ],
        fallback_model_id=4,
        smart_rules_jsonb=[{"type": "keyword", "pattern": "draft|polish", "label": "writing"}],
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
    assert [m.id for m, _ in decision.fallback_chain] == [4]


@pytest.mark.asyncio
async def test_smart_embedding_label_load_balances_same_label_by_weight(monkeypatch):
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: 5.0)

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
            {"model_id": 2, "label": "writing", "weight": 1},
            {"model_id": 3, "label": "writing", "weight": 9},
            {"model_id": 4, "weight": 1},
        ],
        fallback_model_id=None,
        smart_rules_jsonb=[],
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
    assert decision.fallback_chain == []


@pytest.mark.asyncio
async def test_smart_route_without_matching_label_uses_unlabeled_default():
    policy = SimpleNamespace(
        strategy=RouteStrategy.smart,
        targets_jsonb=[
            {"model_id": 1, "label": "code", "weight": 1},
            {"model_id": 2, "label": "writing", "weight": 1},
            {"model_id": 3, "weight": 1},
        ],
        fallback_model_id=None,
        smart_rules_jsonb=[{"type": "keyword", "pattern": "never-match", "label": "writing"}],
        smart_embedding_model_id=None,
    )
    models = {i: _model(i) for i in range(1, 4)}
    channels = {1: _channel(1)}

    decision = await select_route(policy, models, channels, prompt_text="hello")

    assert decision is not None
    assert decision.chosen_label == "default"
    assert decision.model.id == 3
    assert decision.fallback_chain == []


@pytest.mark.asyncio
async def test_smart_default_primary_uses_optional_explicit_fallback():
    policy = SimpleNamespace(
        strategy=RouteStrategy.smart,
        targets_jsonb=[{"model_id": 1, "weight": 1}],
        fallback_model_id=2,
        smart_rules_jsonb=[],
        smart_embedding_model_id=None,
    )
    models = {i: _model(i) for i in range(1, 3)}
    channels = {1: _channel(1)}

    decision = await select_route(policy, models, channels, prompt_text="hello")

    assert decision is not None
    assert decision.chosen_label == "default"
    assert decision.model.id == 1
    assert [model.id for model, _ in decision.fallback_chain] == [2]


@pytest.mark.asyncio
async def test_smart_route_does_not_retry_fallback_selected_as_primary(monkeypatch):
    monkeypatch.setattr("app.services.providers.router.random.uniform", lambda _start, _end: 0.1)
    policy = SimpleNamespace(
        strategy=RouteStrategy.smart,
        targets_jsonb=[{"model_id": 1, "label": "writing", "weight": 1}],
        fallback_model_id=1,
        smart_rules_jsonb=[{"type": "keyword", "pattern": "draft", "label": "writing"}],
        smart_embedding_model_id=None,
    )
    models = {1: _model(1)}
    channels = {1: _channel(1)}

    decision = await select_route(policy, models, channels, prompt_text="draft this")

    assert decision is not None
    assert decision.model.id == 1
    assert decision.fallback_chain == []