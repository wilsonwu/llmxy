---
name: llmxy-billing-quota
description: "Use when changing llmxy billing, token rates, image pricing, wallets, subscriptions, plans, orders, payment callbacks, API key quotas, quota_cache, api_key_cache, UsageLog, BalanceTx, or Envoy ALS charge ingestion."
argument-hint: "billing/quota change goal"
---
# llmxy Billing And Quota Skill

Use this skill for changes that can affect user balances, subscription quota, API key limits, usage logs, payment state, or the Redis hot-path quota mirror.

## Money And Usage Units

1. Balances, order amounts, subscription quota, API key quotas, and `UsageLog.cost_cents` are integer cents.
2. Token model rates are micro-cents per 1K tokens. Always use `calc_cost_cents(model, prompt_tokens, completion_tokens)`.
3. Image pricing may use `pricing_jsonb` with per-image micro-cent tiers or token-mode output token estimates. Use existing helpers such as `quote_image_cost_cents`.
4. Keep rounding conservative with `ceil` so usage never rounds down to zero when a positive billable cost exists.

## Charge And Refund Flow

1. Use `has_quota` or the hot-path equivalent before expensive work.
2. Use `charge_user` for authoritative PG deductions. It drains active subscriptions first, then wallet balance, increments API key usage, and writes one consume `BalanceTx`.
3. Use `refund_to_sources` when reversing a known prior charge so funds return to the same sources where possible.
4. Attach a stable `ref_id`, usually the relay request ID or order ID, so `UsageLog` and `BalanceTx` rows can be correlated.
5. Do not mutate `Subscription.remaining_cents` manually around the bulk update in `charge_user`; the existing code avoids double-deduction from SQLAlchemy session synchronization.

## Cache And Consistency Rules

1. PostgreSQL is authoritative. Redis is a cache or write-through mirror.
2. For normal write paths where exact Redis mirroring is unnecessary, mark `db.info["_quota_invalidate_uids"]` and let the post-commit hook invalidate quota state.
3. For hot paths that know the exact committed delta, call `quota_cache.apply_charge`, `apply_topup`, `apply_grant`, `apply_renew`, `apply_sub_expire`, or `apply_window_roll` after the PG commit.
4. If Redis mirror writes fail, invalidate user quota so the next request rehydrates from PG.
5. Invalidate or refresh `api_key_cache` when API key status, expiry, quota fields, user status, or plan RPM inputs change.

## API-direct Versus Envoy

1. API-direct relay endpoints charge and log in the request path after the upstream succeeds.
2. Envoy does auth/route checks through ext_proc and bills asynchronously from ALS usage events. Do not add a second charge in the translator path for the same Envoy request.
3. ALS billing should write the same core facts as API-direct: user, API key, model, public model, upstream model, prompt/completion tokens, cost, request ID, latency/status, and resolved smart label where available.

## Plans, Orders, And Payments

1. Keep seed data idempotent in `api/app/scripts/seed.py`.
2. Recurring plans renew by period and refill subscription quota; one-time plans are fixed duration and may have purchase caps.
3. Payment providers implement the abstraction in `api/app/services/payment/base.py`; callbacks enter through `POST /api/v1/payments/{channel}/callback`.
4. Payment callback handling must be idempotent. A paid order should not top up or grant quota twice if the provider retries the callback.

## Validation Checklist

1. Run `cd api && pytest -q tests/test_billing.py` for formula and billing behavior touched by the change.
2. Run relay or smart-routing tests when charges depend on provider usage, streaming final chunks, or smart classifier usage.
3. For schema changes, create and run an Alembic migration: `cd api && alembic upgrade head`.
4. For payment or quota changes that frontends expose, type-check the touched app with `npx tsc --noEmit --incremental false`.

## Done Criteria

- Cost units are not mixed between cents and micro-cents.
- The authoritative PG transaction is correct before Redis is updated.
- Cache invalidation or write-through mirroring is explicit.
- Retries, failed upstream attempts, and payment callback retries cannot double-charge users.
- Usage logs and balance transactions remain traceable by request ID or order ID.
