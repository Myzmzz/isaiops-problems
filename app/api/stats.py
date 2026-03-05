"""告警统计 API.

实现 alerts.yaml 契约定义的 AlertStats 接口。
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from app.database import get_session
from app.models.alert import Alert

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/alerts", tags=["alerts-stats"])


def _success(data: object = None) -> dict:
    """构造统一成功响应."""
    return {"code": 0, "message": "success", "data": data}


@router.get("/stats")
async def get_alert_stats(
    time_range: str = Query("7d"),
    session: Session = Depends(get_session),
) -> dict:
    """告警统计 — 返回各维度统计数据和 7 日趋势."""
    # 解析时间范围
    time_ranges = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}
    hours = time_ranges.get(time_range, 168)
    since = datetime.utcnow() - timedelta(hours=hours)

    # 查询范围内的所有告警
    stmt = select(Alert).where(Alert.created_at >= since)
    alerts = session.exec(stmt).all()

    # 总数
    total = len(alerts)

    # 按状态统计
    by_status = {"triggered": 0, "acknowledged": 0, "resolved": 0, "suppressed": 0}
    for alert in alerts:
        if alert.status in by_status:
            by_status[alert.status] += 1

    # 按严重度统计
    by_severity = {"critical": 0, "warning": 0, "info": 0}
    for alert in alerts:
        if alert.severity in by_severity:
            by_severity[alert.severity] += 1

    # 今日新增
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_new = sum(1 for a in alerts if a.created_at >= today_start)

    # 平均修复时间 (MTTR) — 只统计已解决的告警
    resolved_alerts = [a for a in alerts if a.status == "resolved" and a.resolved_at]
    if resolved_alerts:
        total_minutes = sum(
            (a.resolved_at - a.first_triggered_at).total_seconds() / 60
            for a in resolved_alerts
        )
        mttr_minutes = round(total_minutes / len(resolved_alerts), 1)
    else:
        mttr_minutes = 0.0

    # 7 日趋势
    trend_7d = []
    for i in range(7):
        day = datetime.utcnow().date() - timedelta(days=6 - i)
        day_start = datetime(day.year, day.month, day.day)
        day_end = day_start + timedelta(days=1)

        day_alerts = [a for a in alerts if day_start <= a.created_at < day_end]
        trend_7d.append({
            "date": day.isoformat(),
            "critical": sum(1 for a in day_alerts if a.severity == "critical"),
            "warning": sum(1 for a in day_alerts if a.severity == "warning"),
            "info": sum(1 for a in day_alerts if a.severity == "info"),
        })

    return _success({
        "total": total,
        "by_status": by_status,
        "by_severity": by_severity,
        "today_new": today_new,
        "mttr_minutes": mttr_minutes,
        "trend_7d": trend_7d,
    })
