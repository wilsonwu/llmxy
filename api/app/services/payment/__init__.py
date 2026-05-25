from __future__ import annotations

from app.services.payment.alipay import AlipayProvider
from app.services.payment.base import PaymentProvider
from app.services.payment.stripe import StripeProvider
from app.services.payment.wechat import WechatProvider

_REGISTRY: dict[str, PaymentProvider] = {
    "alipay": AlipayProvider(),
    "wechat": WechatProvider(),
    "stripe": StripeProvider(),
}


def get_provider(channel: str) -> PaymentProvider | None:
    return _REGISTRY.get(channel)
