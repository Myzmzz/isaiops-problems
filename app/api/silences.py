"""静默规则管理 API.

实现 alerts.yaml 契约定义的静默规则 CRUD 接口。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.models.silence_rule import SilenceRule

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/alerts/silences", tags=["silences"])

# 静默规则 ID 计数器
_silence_counter: int = 0


def _next_silence_id() -> str:
    """生成下一个静默规则 ID (SIL-XXX)."""
    global _silence_counter
    _silence_counter += 1
    return f"SIL-{_silence_counter:03d}"


def _init_silence_counter(session: Session) -> None:
    """从数据库初始化计数器."""
    global _silence_counter
    stmt = select(SilenceRule).order_by(SilenceRule.created_at.desc()).limit(1)  # type: ignore[arg-type]
    last_rule = session.exec(stmt).first()
    if last_rule:
        try:
            parts = last_rule.id.split("-")
            _silence_counter = int(parts[-1])
        except (IndexError, ValueError):
            _silence_counter = 0


# =============================================
#  请求 Schema
# =============================================


class CreateSilenceRequest(BaseModel):
    """创建静默规则请求."""

    matchers: list[dict]
    starts_at: datetime
    ends_at: datetime
    reason: str
    note: str | None = None
    creator: str = "user"
    source: str = "manual"


# =============================================
#  辅助函数
# =============================================


def _success(data: object = None) -> dict:
    """构造统一成功响应."""
    return {"code": 0, "message": "success", "data": data}


def _silence_to_dict(rule: SilenceRule) -> dict:
    """将 SilenceRule 模型转为 API 响应字典."""
    return {
        "id": rule.id,
        "creator": rule.creator,
        "source": rule.source,
        "reason": rule.reason,
        "note": rule.note,
        "matchers": rule.matchers,
        "starts_at": rule.starts_at.isoformat() if rule.starts_at else None,
        "ends_at": rule.ends_at.isoformat() if rule.ends_at else None,
        "status": rule.status,
        "match_count": rule.match_count,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
    }


# =============================================
#  API 端点
# =============================================


@router.get("")
async def list_silences(
    status: str | None = Query(None),
    session: Session = Depends(get_session),
) -> dict:
    """静默规则列表."""
    # 初始化计数器
    _init_silence_counter(session)

    stmt = select(SilenceRule).order_by(SilenceRule.created_at.desc())  # type: ignore[arg-type]

    if status:
        stmt = stmt.where(SilenceRule.status == status)

    rules = session.exec(stmt).all()
    return _success({"items": [_silence_to_dict(r) for r in rules]})


@router.post("")
async def create_silence(
    body: CreateSilenceRequest,
    session: Session = Depends(get_session),
) -> dict:
    """创建静默规则."""
    # 初始化计数器
    _init_silence_counter(session)

    # 参数校验
    if not body.matchers:
        raise HTTPException(status_code=400, detail="matchers 不能为空")
    if body.ends_at <= body.starts_at:
        raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")

    # 验证 matcher 格式
    valid_ops = {"=", "!=", "=~", "!~"}
    for matcher in body.matchers:
        if not matcher.get("key") or not matcher.get("value"):
            raise HTTPException(status_code=400, detail="matcher 必须包含 key 和 value")
        if matcher.get("op", "=") not in valid_ops:
            raise HTTPException(status_code=400, detail=f"不支持的操作符: {matcher.get('op')}")

    now = datetime.utcnow()
    # 确定初始状态
    if body.starts_at > now:
        status = "pending"
    elif body.ends_at < now:
        status = "expired"
    else:
        status = "active"

    rule = SilenceRule(
        id=_next_silence_id(),
        creator=body.creator,
        source=body.source,
        reason=body.reason,
        note=body.note,
        matchers=body.matchers,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        status=status,
        match_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)

    logger.info("Created silence rule %s (reason=%s)", rule.id, rule.reason)
    return _success(_silence_to_dict(rule))


@router.delete("/{silence_id}")
async def delete_silence(
    silence_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """删除静默规则."""
    rule = session.get(SilenceRule, silence_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"静默规则 {silence_id} 不存在")

    session.delete(rule)
    session.commit()

    logger.info("Deleted silence rule %s", silence_id)
    return _success(None)
