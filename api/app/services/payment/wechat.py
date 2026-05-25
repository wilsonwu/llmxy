from __future__ import annotations

from app.core.config import settings
from app.models import Order


class WechatProvider:
    name = "wechat"

    async def create_payment(self, order: Order) -> dict:
        return {
            "channel": "wechat",
            "pay_url": None,
            "qr_code": f"weixin://wxpay/bizpayurl?pr=STUB_{order.id}",
            "raw": {"note": "stub; configure WECHAT_* env to enable"},
        }

    async def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        return True, payload.get("transaction_id") or f"wechat-{payload.get('order_id')}"
