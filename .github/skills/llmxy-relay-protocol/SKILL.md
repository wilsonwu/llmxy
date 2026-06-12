---
name: llmxy-relay-protocol
description: "Use when adding or changing llmxy relay endpoints, OpenAI-compatible protocol handling, provider adapters, connector_type/provider_type behavior, SSE streaming, Responses API, embeddings, images, Anthropic, Gemini, Azure OpenAI, smart routing, or route policy behavior."
argument-hint: "relay/provider change goal"
---
# llmxy Relay Protocol Skill

Use this skill for changes that affect how client requests enter `/v1/*`, how routes choose upstream models, how provider adapters call upstreams, or how upstream responses are converted back to client-facing protocols.

## Workflow

1. Identify the client-facing protocol and endpoint.
   - OpenAI chat: `api/app/api/relay/chat.py`.
   - OpenAI Responses: `api/app/api/relay/responses.py` and `api/app/services/protocols/openai_responses.py`.
   - Embeddings/images/models/Anthropic compatibility: matching files in `api/app/api/relay/`.
   - Route exposure is controlled by `RoutePolicy.modality` and `RoutePolicy.exposed_protocols`.

2. Trace route selection before touching adapters.
   - `_load_route` rejects disabled, private, wrong-modality, and wrong-protocol routes.
   - `select_route` handles `weighted`, `fallback`, and `smart` strategies.
   - Smart rules may pick a target label by preset, token count, keyword, code block, geo, or embedding classifier. Fallback chains stay within the chosen label when possible.

3. Resolve protocol and connector separately.
   - Effective semantic protocol comes from `Model.upstream_protocol` or `Channel.provider_type`.
   - Connector comes from `Channel.connector_type` and connector aliases.
   - Update `api/app/services/providers/registry.py` when adding connector support or protocol support.
   - Do not treat Azure as a separate semantic chat protocol; it is an OpenAI semantic protocol with an `azure_openai` connector.

4. Implement or update the provider adapter.
   - Provider adapters live in `api/app/services/providers/`.
   - Preserve the adapter contract from `api/app/services/providers/base.py`.
   - Non-stream results must return a body plus prompt/completion token counts where available.
   - Stream results consumed by OpenAI-compatible clients must be OpenAI SSE chunks, with final usage when possible.
   - Normalize `max_tokens` and `max_completion_tokens` through existing compatibility helpers instead of hand-writing per-route hacks.

5. Preserve billing and usage logging.
   - Relay success must charge with `calc_cost_cents`, write `UsageLog.kind == "relay"`, include request ID, latency, resolved smart label, and commit once after successful billing/logging.
   - If smart embedding classification ran, record classifier usage through the existing helper so it shares the relay request ID.
   - Avoid charging failed upstream attempts. Only the successful candidate should produce the relay charge/log row.

6. Consider Envoy compatibility.
   - API-direct may call adapters directly. Envoy may route non-OpenAI protocols through internal translator endpoints so usage stays observable in OpenAI-shaped streams.
   - If a protocol change affects stream shape, verify whether Envoy Lua/ext_proc/ALS usage extraction or internal translator behavior also needs an update.

7. Validate with focused tests.
   - Protocol normalization and connector behavior: `cd api && pytest -q tests/test_openai_compat.py`.
   - Smart routing behavior: `cd api && pytest -q tests/test_smart_routing.py`.
   - Billing impact: `cd api && pytest -q tests/test_billing.py`.
   - For broader backend confidence: `cd api && pytest -q`.

## Done Criteria

- The route rejects unsupported modality/protocol combinations.
- Protocol and connector choices remain separately testable.
- Streaming and non-streaming responses preserve client-facing wire compatibility.
- Usage and billing happen exactly once for successful requests.
- Tests cover any new protocol, connector, fallback, or token-field compatibility behavior.
