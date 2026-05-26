from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models import Channel, Plan, RoutePolicy, RouteStrategy, Subscription, User, UserRole, Model
from app.services.billing import grant_subscription


async def seed() -> None:
    async with AsyncSessionLocal() as db:  # type: AsyncSession
        # admin
        existing = (await db.execute(select(User).where(User.email == settings.SEED_ADMIN_EMAIL))).scalar_one_or_none()
        if not existing:
            db.add(
                User(
                    email=settings.SEED_ADMIN_EMAIL,
                    password_hash=hash_password(settings.SEED_ADMIN_PASSWORD),
                    role=UserRole.admin,
                    balance_cents=10_000_00,
                )
            )

        # demo plan
        plan = (await db.execute(select(Plan).where(Plan.code == "free"))).scalar_one_or_none()
        if not plan:
            db.add(Plan(code="free", name="Free trial", plan_type="one_time", price_cents=0, quota_cents=1_00, duration_days=30))
        elif plan.plan_type != "one_time":
            # Coerce legacy seeded rows (default was recurring) to one-time.
            plan.plan_type = "one_time"

        # demo channel
        ch = (await db.execute(select(Channel).where(Channel.name == "default-openai"))).scalar_one_or_none()
        if not ch:
            ch = Channel(
                name="default-openai",
                provider_type="openai",
                base_url="https://api.openai.com/v1",
                api_key_enc=None,  # admin must set the real key
                enabled=True,
            )
            db.add(ch)
            await db.flush()

        # demo model
        m = (await db.execute(select(Model).where(Model.code == "gpt-4o-mini"))).scalar_one_or_none()
        if not m:
            m = Model(
                code="gpt-4o-mini",
                display_name="GPT-4o mini",
                channel_id=ch.id,
                upstream_model="gpt-4o-mini",
                prompt_rate=1500,  # micro-cents / 1K tokens => $0.00015 / 1K
                completion_rate=6000,
                enabled=True,
            )
            db.add(m)
            await db.flush()

        # demo route policy
        rp = (await db.execute(select(RoutePolicy).where(RoutePolicy.user_facing_model == "gpt-4o-mini"))).scalar_one_or_none()
        if not rp:
            db.add(
                RoutePolicy(
                    user_facing_model="gpt-4o-mini",
                    strategy=RouteStrategy.weighted,
                    targets_jsonb=[{"model_id": m.id, "weight": 1, "fallback_order": 0}],
                    enabled=True,
                )
            )

        await db.commit()

        # Backfill: grant free trial to any user that has no active subscription.
        free = (await db.execute(select(Plan).where(Plan.code == "free", Plan.active.is_(True)))).scalar_one_or_none()
        if free and (free.quota_cents or 0) > 0:
            now = datetime.now(timezone.utc)
            users = (await db.execute(select(User))).scalars().all()
            for u in users:
                has_active = (
                    await db.execute(
                        select(Subscription.id).where(
                            Subscription.user_id == u.id,
                            Subscription.status == "active",
                            Subscription.current_period_end > now,
                            Subscription.remaining_cents > 0,
                        )
                    )
                ).first()
                if not has_active:
                    await grant_subscription(db, u, free, ref_id="seed-backfill")
            await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
