from __future__ import annotations

from typing import Protocol

from app.models import Order


class PaymentProvider(Protocol):
    name: str

    async def create_payment(self, order: Order) -> dict: ...

    async def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        """Returns (ok, provider_order_id)."""
        ...
