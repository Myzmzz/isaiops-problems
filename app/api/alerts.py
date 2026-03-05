"""告警管理 API.

实现 alerts.yaml 契约定义的告警 CRUD、状态变更、分配、批量操作、备注接口。
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from app.database import get_session
from app.models.alert import Alert
from app.models.alert_event import AlertEvent
from app.models.alert_timeline import AlertTimeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


# =============================================
#  请求/响应 Schema
# =============================================


class StatusUpdateRequest(BaseModel):
    """状态变更请求."""

    status: str  # acknowledged | resolved | suppressed
    note: str | None = None


class AssigneeUpdateRequest(BaseModel):
    """分配负责人请求."""

    assignee: str


class BatchActionRequest(BaseModel):
    """批量操作请求."""

    action: str  # acknowledge | resolve | silence | assign
    alert_ids: list[str]
    params: dict | None = None


class NoteRequest(BaseModel):
    """添加备注请求."""

    content: str
    author: str | None = None


# =============================================
#  辅助函数
# =============================================


def _success(data: object = None) -> dict:
    """构造统一成功响应."""
    return {"code": 0, "message": "success", "data": data}


def _error(code: int, message: str) -> dict:
    """构造统一错误响应."""
    return {"code": code, "message": message, "data": None}


def _alert_to_dict(alert: Alert) -> dict:
    """将 Alert 模型转为 API 响应字典."""
    return {
        "id": alert.id,
        "title": alert.title,
        "description": alert.description,
        "service": alert.service,
        "severity": alert.severity,
        "score": alert.score,
        "status": alert.status,
        "assignee": alert.assignee,
        "anomaly_count": alert.anomaly_count,
        "first_triggered_at": alert.first_triggered_at.isoformat() if alert.first_triggered_at else None,
        "last_triggered_at": alert.last_triggered_at.isoformat() if alert.last_triggered_at else None,
        "duration": alert.duration,
        "detection_mode": alert.detection_mode,
        "source_rule_id": alert.source_rule_id,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "updated_at": alert.updated_at.isoformat() if alert.updated_at else None,
    }


def _event_to_dict(ae: AlertEvent) -> dict:
    """将 AlertEvent 关联记录转为 API 响应字典."""
    return {
        "event_id": ae.event_id,
        "rule_id": ae.event_rule_id,
        "metric": ae.event_metric,
        "severity": ae.event_severity,
        "score": ae.event_score,
        "detected_at": ae.event_detected_at.isoformat() if ae.event_detected_at else None,
        "current_value": ae.event_current_value,
        "expected_value": ae.event_expected_value,
        "deviation_percent": ae.event_deviation_percent,
        "status": ae.event_status,
    }


def _timeline_to_dict(tl: AlertTimeline) -> dict:
    """将时间线条目转为 API 响应字典."""
    return {
        "id": tl.id,
        "action": tl.action,
        "actor": tl.actor,
        "detail": tl.detail,
        "created_at": tl.created_at.isoformat() if tl.created_at else None,
    }


# =============================================
#  API 端点
# =============================================


@router.get("")
async def list_alerts(
    status: str | None = Query(None),
    severity: str | None = Query(None),
    service: str | None = Query(None),
    assignee: str | None = Query(None),
    time_range: str = Query("24h"),
    search: str | None = Query(None),
    sort_by: str = Query("last_triggered_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict:
    """告警列表 — 支持多维度筛选、排序和分页."""
    stmt = select(Alert)

    # 状态筛选
    if status:
        stmt = stmt.where(Alert.status == status)

    # 严重度筛选
    if severity:
        stmt = stmt.where(Alert.severity == severity)

    # 服务筛选
    if service:
        stmt = stmt.where(Alert.service == service)

    # 负责人筛选
    if assignee:
        stmt = stmt.where(Alert.assignee == assignee)

    # 时间范围
    time_ranges = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}
    hours = time_ranges.get(time_range, 24)
    since = datetime.utcnow() - timedelta(hours=hours)
    stmt = stmt.where(Alert.created_at >= since)

    # 搜索（标题、描述、服务名）
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            col(Alert.title).ilike(pattern)
            | col(Alert.service).ilike(pattern)
            | col(Alert.id).ilike(pattern)
        )

    # 总数（在排序和分页之前）
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = session.exec(count_stmt).one()

    # 排序
    sort_columns = {
        "score": Alert.score,
        "severity": Alert.severity,
        "created_at": Alert.created_at,
        "last_triggered_at": Alert.last_triggered_at,
    }
    sort_col = sort_columns.get(sort_by, Alert.last_triggered_at)
    if sort_order == "asc":
        stmt = stmt.order_by(sort_col.asc())  # type: ignore[union-attr]
    else:
        stmt = stmt.order_by(sort_col.desc())  # type: ignore[union-attr]

    # 分页
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)

    alerts = session.exec(stmt).all()

    return _success({
        "items": [_alert_to_dict(a) for a in alerts],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/{alert_id}")
async def get_alert_detail(
    alert_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """告警详情 — 包含关联事件和处理时间线."""
    alert = session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"告警 {alert_id} 不存在")

    # 获取关联事件
    events_stmt = (
        select(AlertEvent)
        .where(AlertEvent.alert_id == alert_id)
        .order_by(AlertEvent.event_detected_at.desc())  # type: ignore[arg-type]
    )
    events = session.exec(events_stmt).all()

    # 获取时间线
    timeline_stmt = (
        select(AlertTimeline)
        .where(AlertTimeline.alert_id == alert_id)
        .order_by(AlertTimeline.created_at.asc())  # type: ignore[arg-type]
    )
    timeline = session.exec(timeline_stmt).all()

    return _success({
        "alert": _alert_to_dict(alert),
        "events": [_event_to_dict(e) for e in events],
        "timeline": [_timeline_to_dict(t) for t in timeline],
    })


@router.patch("/{alert_id}/status")
async def update_alert_status(
    alert_id: str,
    body: StatusUpdateRequest,
    session: Session = Depends(get_session),
) -> dict:
    """更新告警状态 — 支持 acknowledged / resolved / suppressed."""
    alert = session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"告警 {alert_id} 不存在")

    valid_transitions = {
        "triggered": ["acknowledged", "resolved", "suppressed"],
        "acknowledged": ["resolved", "suppressed"],
        "suppressed": ["triggered"],
    }
    allowed = valid_transitions.get(alert.status, [])
    if body.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"不允许从 {alert.status} 转换到 {body.status}",
        )

    now = datetime.utcnow()
    old_status = alert.status
    alert.status = body.status
    alert.updated_at = now

    if body.status == "acknowledged":
        alert.acknowledged_at = now
    elif body.status == "resolved":
        alert.resolved_at = now
        # 计算持续时长
        delta = now - alert.first_triggered_at
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        alert.duration = f"{hours}h {minutes}min" if hours > 0 else f"{minutes}min"

    session.add(alert)

    # 记录时间线
    detail = f"状态变更: {old_status} → {body.status}"
    if body.note:
        detail += f" (备注: {body.note})"
    timeline = AlertTimeline(
        alert_id=alert_id,
        action=body.status,
        actor="user",
        detail=detail,
        created_at=now,
    )
    session.add(timeline)
    session.commit()

    session.refresh(alert)
    return _success(_alert_to_dict(alert))


@router.patch("/{alert_id}/assignee")
async def update_alert_assignee(
    alert_id: str,
    body: AssigneeUpdateRequest,
    session: Session = Depends(get_session),
) -> dict:
    """分配告警负责人."""
    alert = session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"告警 {alert_id} 不存在")

    now = datetime.utcnow()
    old_assignee = alert.assignee or "无"
    alert.assignee = body.assignee
    alert.updated_at = now
    session.add(alert)

    # 记录时间线
    timeline = AlertTimeline(
        alert_id=alert_id,
        action="assigned",
        actor="user",
        detail=f"分配: {old_assignee} → {body.assignee}",
        created_at=now,
    )
    session.add(timeline)
    session.commit()

    session.refresh(alert)
    return _success(_alert_to_dict(alert))


@router.post("/batch")
async def batch_action(
    body: BatchActionRequest,
    session: Session = Depends(get_session),
) -> dict:
    """批量操作告警 — 支持 acknowledge / resolve / silence / assign."""
    if not body.alert_ids:
        raise HTTPException(status_code=400, detail="alert_ids 不能为空")

    now = datetime.utcnow()
    affected = 0

    for alert_id in body.alert_ids:
        alert = session.get(Alert, alert_id)
        if not alert:
            continue

        if body.action == "acknowledge":
            if alert.status == "triggered":
                alert.status = "acknowledged"
                alert.acknowledged_at = now
                affected += 1
        elif body.action == "resolve":
            if alert.status in ("triggered", "acknowledged"):
                alert.status = "resolved"
                alert.resolved_at = now
                affected += 1
        elif body.action == "silence":
            if alert.status in ("triggered", "acknowledged"):
                alert.status = "suppressed"
                affected += 1
        elif body.action == "assign":
            assignee = (body.params or {}).get("assignee", "")
            if assignee:
                alert.assignee = assignee
                affected += 1

        alert.updated_at = now
        session.add(alert)

        # 记录时间线
        note = (body.params or {}).get("note", "")
        detail = f"批量操作: {body.action}"
        if note:
            detail += f" (备注: {note})"
        timeline = AlertTimeline(
            alert_id=alert_id,
            action=body.action if body.action != "assign" else "assigned",
            actor="user",
            detail=detail,
            created_at=now,
        )
        session.add(timeline)

    session.commit()
    return _success({"affected": affected})


@router.post("/{alert_id}/notes")
async def add_note(
    alert_id: str,
    body: NoteRequest,
    session: Session = Depends(get_session),
) -> dict:
    """添加告警备注."""
    alert = session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"告警 {alert_id} 不存在")

    now = datetime.utcnow()
    timeline = AlertTimeline(
        alert_id=alert_id,
        action="commented",
        actor=body.author or "user",
        detail=body.content,
        created_at=now,
    )
    session.add(timeline)

    alert.updated_at = now
    session.add(alert)
    session.commit()

    session.refresh(timeline)
    return _success(_timeline_to_dict(timeline))
