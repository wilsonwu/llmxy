from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models import BalanceTx, UsageLog, User
from app.schemas import BalanceTxOut, PaginatedResp, UsageLogOut

router = APIRouter(prefix="/usage", tags=["admin-usage"])


@router.get("/logs", response_model=PaginatedResp)
async def admin_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user_id: Optional[int] = None,
    api_key_id: Optional[int] = None,
    model_id: Optional[int] = None,
    status: Optional[str] = None,
    user_facing_model: Optional[str] = None,
    upstream_model: Optional[str] = None,
    kind: Optional[str] = None,
    resolved_label: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    base = select(UsageLog)
    if user_id is not None:
        base = base.where(UsageLog.user_id == user_id)
    if api_key_id is not None:
        base = base.where(UsageLog.api_key_id == api_key_id)
    if model_id is not None:
        base = base.where(UsageLog.model_id == model_id)
    if status:
        base = base.where(UsageLog.status == status)
    if user_facing_model:
        base = base.where(UsageLog.user_facing_model == user_facing_model)
    if upstream_model:
        base = base.where(UsageLog.upstream_model == upstream_model)
    if kind:
        base = base.where(UsageLog.kind == kind)
    if resolved_label:
        base = base.where(UsageLog.resolved_label == resolved_label)
    if start is not None:
        base = base.where(UsageLog.created_at >= start)
    if end is not None:
        base = base.where(UsageLog.created_at < end)
    base = base.order_by(desc(UsageLog.id))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedResp(
        items=[UsageLogOut.model_validate(r).model_dump() for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/balance-tx", response_model=PaginatedResp)
async def admin_balance_tx(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user_id: Optional[int] = None,
    type: Optional[str] = None,
    ref_id: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    base = select(BalanceTx)
    if user_id is not None:
        base = base.where(BalanceTx.user_id == user_id)
    if type:
        base = base.where(BalanceTx.type == type)
    if ref_id:
        base = base.where(BalanceTx.ref_id == ref_id)
    if start is not None:
        base = base.where(BalanceTx.created_at >= start)
    if end is not None:
        base = base.where(BalanceTx.created_at < end)
    base = base.order_by(desc(BalanceTx.id))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedResp(
        items=[BalanceTxOut.model_validate(r).model_dump() for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
