from __future__ import annotations

from app.core.config import settings
from app.models import Order


class AlipayProvider:
    name = "alipay"

    async def create_payment(self, order: Order) -> dict:
        # TODO: integrate real alipay-sdk
        return {
            "channel": "alipay",
            "pay_url": f"{settings.API_PUBLIC_URL}/api/v1/payments/alipay/mock-pay?order_id={order.id}",
            "qr_code": None,
            "raw": {"note": "stub; configure ALIPAY_* env to enable"},
        }

    async def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        # TODO: verify alipay signature
        return True, payload.get("trade_no") or f"alipay-{payload.get('order_id')}"
