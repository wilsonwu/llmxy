from __future__ import annotations

from app.core.config import settings
from app.models import Order


class StripeProvider:
    name = "stripe"

    async def create_payment(self, order: Order) -> dict:
        return {
            "channel": "stripe",
            "pay_url": f"https://checkout.stripe.com/c/pay/STUB_{order.id}",
            "qr_code": None,
            "raw": {"note": "stub; configure STRIPE_SECRET_KEY to enable"},
        }

    async def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        # TODO: verify stripe-signature header against STRIPE_WEBHOOK_SECRET
        return True, payload.get("id") or f"stripe-{payload.get('order_id')}"
