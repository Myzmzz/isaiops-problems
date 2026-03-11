"""问题管理 API.

实现完整的 Problem CRUD、状态变更、分配、备注、事件归并、
推荐归并、RCA、事件映射等接口。
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from app.database import get_session
from app.models.problem import Problem
from app.models.problem_event import ProblemEvent
from app.models.problem_note import ProblemNote
from app.models.problem_timeline import ProblemTimeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/problems", tags=["problems"])

# 问题 ID 计数器
_problem_counter: int = 0


def _next_problem_id() -> str:
    """生成下一个问题 ID (PRB-2026-XXXX)."""
    global _problem_counter
    _problem_counter += 1
    now = datetime.utcnow()
    return f"PRB-{now.year}-{_problem_counter:04d}"


def init_problem_counter(session: Session) -> None:
    """从数据库初始化问题 ID 计数器."""
    global _problem_counter
    stmt = select(Problem).order_by(Problem.created_at.desc()).limit(1)  # type: ignore[arg-type]
    last = session.exec(stmt).first()
    if last:
        try:
            parts = last.id.split("-")
            _problem_counter = int(parts[-1])
        except (IndexError, ValueError):
            _problem_counter = 0
    logger.info("Problem counter initialized to %d", _problem_counter)


# =============================================
#  请求/响应 Schema
# =============================================


class ProblemCreateRequest(BaseModel):
    """创建问题请求."""

    title: str
    description: str | None = None
    severity: str  # critical | warning | info
    service: str | None = None
    assignee: str | None = None
    event_ids: list[str] | None = None


class StatusUpdateRequest(BaseModel):
    """状态变更请求."""

    status: str  # open | investigating | resolved | closed
    comment: str | None = None


class AssigneeUpdateRequest(BaseModel):
    """分配负责人请求."""

    assignee: str


class NoteRequest(BaseModel):
    """添加备注请求."""

    content: str
    author: str | None = None
    type: str | None = "general"  # observation | suspected_root_cause | action | general


class MergeEventsRequest(BaseModel):
    """归并事件到问题请求."""

    event_ids: list[str]


# =============================================
#  辅助函数
# =============================================


def _success(data: object = None) -> dict:
    """构造统一成功响应."""
    return {"code": 0, "message": "success", "data": data}


def _format_duration(start: datetime, end: datetime | None = None) -> str:
    """格式化持续时间为可读字符串."""
    if end is None:
        end = datetime.utcnow()
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "0s"

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}min")
    if not parts:
        parts.append(f"{total_seconds}s")
    return " ".join(parts)


def _problem_to_summary(p: Problem, session: Session | None = None) -> dict:
    """将 Problem 模型转为列表摘要字典（含增强字段）."""
    now = datetime.utcnow()

    # 计算 duration
    end_time = p.resolved_at if p.resolved_at else now
    duration = _format_duration(p.created_at, end_time)

    # isOngoing
    is_ongoing = p.resolved_at is None and p.status not in ("resolved", "closed")

    # 计算 ongoingEventCount 和 impactScope
    ongoing_event_count = 0
    impact_services: set[str] = set()
    if session:
        events = session.exec(
            select(ProblemEvent).where(ProblemEvent.problem_id == p.id)
        ).all()
        ongoing_event_count = sum(
            1 for e in events if e.event_status in ("active", "escalated")
        )
        for e in events:
            if e.event_service:
                impact_services.add(e.event_service)

    service_count = len(impact_services) if impact_services else (1 if p.service else 0)
    impact_scope = f"{service_count} 个服务" if service_count > 0 else ""

    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "severity": p.severity,
        "status": p.status,
        "service": p.service,
        "assignee": p.assignee,
        "event_count": p.event_count,
        "ongoingEventCount": ongoing_event_count,
        "duration": duration,
        "isOngoing": is_ongoing,
        "lastActiveAt": p.updated_at.isoformat() if p.updated_at else None,
        "rcaStatus": p.rca_status,
        "rootCauseStatus": p.root_cause_status,
        "impactScope": impact_scope,
        "changeCount": 0,  # 后续对接 isaiops-be changes 查询
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
    }


def _event_to_dict(pe: ProblemEvent) -> dict:
    """将 ProblemEvent 转为 API 响应字典."""
    # 计算 duration
    now = datetime.utcnow()
    if pe.event_detected_at:
        end = pe.event_resolved_at if pe.event_resolved_at else now
        duration = _format_duration(pe.event_detected_at, end)
    else:
        duration = ""

    return {
        "eventId": pe.event_id,
        "ruleId": pe.event_rule_id,
        "metric": pe.event_metric,
        "targetName": pe.event_service,
        "severity": pe.event_severity,
        "currentValue": pe.event_value,
        "threshold": pe.event_threshold,
        "triggeredAt": pe.event_detected_at.isoformat() if pe.event_detected_at else None,
        "resolvedAt": pe.event_resolved_at.isoformat() if pe.event_resolved_at else None,
        "status": pe.event_status,
        "duration": duration,
        "relationTag": pe.relation_tag,
    }


def _timeline_to_dict(tl: ProblemTimeline) -> dict:
    """将时间线条目转为 API 响应字典."""
    # 映射 action 到 type
    type_map = {
        "created": "system",
        "event_added": "system",
        "status_changed": "manual",
        "assigned": "manual",
        "commented": "manual",
        "resolved": "recovery",
        "closed": "manual",
    }
    return {
        "id": tl.id,
        "time": tl.timestamp.isoformat() if tl.timestamp else None,
        "type": type_map.get(tl.action, "system"),
        "actor": tl.actor,
        "content": tl.content,
        "note": tl.old_value if tl.old_value and tl.new_value else None,
        "action": tl.action,
        "oldValue": tl.old_value,
        "newValue": tl.new_value,
    }


def _note_to_dict(note: ProblemNote) -> dict:
    """将备注转为 API 响应字典."""
    return {
        "id": note.id,
        "author": note.author,
        "createdAt": note.created_at.isoformat() if note.created_at else None,
        "type": note.type,
        "content": note.content,
    }


def _compute_overview(session: Session, since: datetime) -> dict:
    """计算问题概览统计（含增强字段）."""
    stmt = select(Problem).where(Problem.created_at >= since)
    problems = session.exec(stmt).all()

    total = len(problems)
    by_status = {"open": 0, "investigating": 0, "resolved": 0, "closed": 0}
    by_severity = {"critical": 0, "warning": 0, "info": 0}
    ongoing = 0
    rca_initiated = 0
    root_cause_confirmed = 0

    for p in problems:
        if p.status in by_status:
            by_status[p.status] += 1
        if p.severity in by_severity:
            by_severity[p.severity] += 1
        if p.status in ("open", "investigating"):
            ongoing += 1
        if p.rca_status in ("running", "completed"):
            rca_initiated += 1
        if p.root_cause_status == "confirmed":
            root_cause_confirmed += 1

    resolved = [p for p in problems if p.status == "resolved" and p.resolved_at]
    avg_resolution = 0.0
    if resolved:
        total_hours = sum(
            (p.resolved_at - p.created_at).total_seconds() / 3600 for p in resolved
        )
        avg_resolution = round(total_hours / len(resolved), 1)

    return {
        "total_problems": total,
        "open_problems": by_status["open"],
        "investigating_problems": by_status["investigating"],
        "resolved_problems": by_status["resolved"],
        "closed_problems": by_status["closed"],
        "critical_count": by_severity["critical"],
        "warning_count": by_severity["warning"],
        "avg_resolution_time_hours": avg_resolution,
        "ongoing": ongoing,
        "rcaInitiated": rca_initiated,
        "rootCauseConfirmed": root_cause_confirmed,
    }


# =============================================
#  API 端点
# =============================================


@router.get("/event-mappings")
async def get_event_mappings(
    event_ids: str = Query(..., description="逗号分隔的事件 ID 列表"),
    session: Session = Depends(get_session),
) -> dict:
    """批量查询事件→问题映射.

    供 isaiops-be 调用，返回每个事件关联的问题信息。
    GET /api/v1/problems/event-mappings?event_ids=ANO-001,ANO-002
    """
    ids = [eid.strip() for eid in event_ids.split(",") if eid.strip()]
    if not ids:
        return _success({"mappings": {}})

    # 查询所有匹配的 problem_events
    stmt = select(ProblemEvent).where(ProblemEvent.event_id.in_(ids))  # type: ignore[attr-defined]
    pes = session.exec(stmt).all()

    # 收集 problem_ids 并批量查询 Problem
    problem_ids = {pe.problem_id for pe in pes}
    problems_map: dict[str, Problem] = {}
    if problem_ids:
        problems = session.exec(
            select(Problem).where(Problem.id.in_(problem_ids))  # type: ignore[attr-defined]
        ).all()
        problems_map = {p.id: p for p in problems}

    # 构建映射
    mappings: dict[str, dict] = {}
    for pe in pes:
        p = problems_map.get(pe.problem_id)
        if p:
            mappings[pe.event_id] = {
                "problemId": p.id,
                "problemTitle": p.title,
                "problemStatus": p.status,
            }

    return _success({"mappings": mappings})


@router.get("/recommendations")
async def get_recommendations(
    event_id: str = Query(..., description="事件 ID"),
    event_service: str = Query("", description="事件所属服务"),
    event_severity: str = Query("", description="事件严重级别"),
    session: Session = Depends(get_session),
) -> dict:
    """推荐可归并的问题列表.

    基于规则匹配: 同服务 + 未关闭 + 24h 内。
    GET /api/v1/problems/recommendations?event_id=ANO-001&event_service=ts-order-service
    """
    now = datetime.utcnow()
    window = now - timedelta(hours=24)

    # 候选: 同服务 + 未关闭 + 24h 内创建
    stmt = select(Problem).where(
        Problem.status.in_(["open", "investigating"]),
        Problem.created_at >= window,
    )
    if event_service:
        stmt = stmt.where(Problem.service == event_service)

    candidates = session.exec(stmt).all()

    recommendations = []
    for p in candidates:
        # 检查事件是否已关联
        existing = session.exec(
            select(ProblemEvent).where(
                ProblemEvent.problem_id == p.id,
                ProblemEvent.event_id == event_id,
            )
        ).first()
        if existing:
            continue

        # 打分
        confidence = 0
        reasons: list[str] = []

        # 同服务 +40
        if event_service and p.service == event_service:
            confidence += 40
            reasons.append("同服务")

        # 时间窗口打分
        age = (now - p.created_at).total_seconds() / 3600
        if age <= 1:
            confidence += 30
            reasons.append("1h 内")
        elif age <= 2:
            confidence += 15
            reasons.append("2h 内")
        else:
            confidence += 5
            reasons.append("24h 内")

        # 同严重级别 +15
        if event_severity and p.severity == event_severity:
            confidence += 15
            reasons.append("同严重级别")

        recommendations.append({
            "problemId": p.id,
            "problemTitle": p.title,
            "confidence": min(confidence, 100),
            "reasons": reasons,
            "status": p.status,
            "severity": p.severity,
            "eventCount": p.event_count,
            "service": p.service,
        })

    # 按 confidence 降序
    recommendations.sort(key=lambda x: x["confidence"], reverse=True)

    return _success({"recommendations": recommendations[:3]})


@router.get("/stats")
async def get_problem_stats(
    time_range: str = Query("7d"),
    session: Session = Depends(get_session),
) -> dict:
    """问题统计 — 返回各维度统计数据和 7 日趋势."""
    time_ranges = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}
    hours = time_ranges.get(time_range, 168)
    since = datetime.utcnow() - timedelta(hours=hours)

    stmt = select(Problem).where(Problem.created_at >= since)
    problems = session.exec(stmt).all()

    total = len(problems)
    by_status = {"open": 0, "investigating": 0, "resolved": 0, "closed": 0}
    by_severity = {"critical": 0, "warning": 0, "info": 0}

    for p in problems:
        if p.status in by_status:
            by_status[p.status] += 1
        if p.severity in by_severity:
            by_severity[p.severity] += 1

    resolved = [p for p in problems if p.status == "resolved" and p.resolved_at]
    mttr_hours = 0.0
    if resolved:
        total_hours = sum(
            (p.resolved_at - p.created_at).total_seconds() / 3600 for p in resolved
        )
        mttr_hours = round(total_hours / len(resolved), 1)

    # 7 日趋势
    trend_7d = []
    for i in range(7):
        day = datetime.utcnow().date() - timedelta(days=6 - i)
        day_start = datetime(day.year, day.month, day.day)
        day_end = day_start + timedelta(days=1)

        day_problems = [p for p in problems if day_start <= p.created_at < day_end]
        day_resolved = [
            p for p in problems
            if p.resolved_at and day_start <= p.resolved_at < day_end
        ]
        trend_7d.append({
            "date": day.isoformat(),
            "created": len(day_problems),
            "resolved": len(day_resolved),
        })

    return _success({
        "total_problems": total,
        "by_status": by_status,
        "by_severity": by_severity,
        "mttr_hours": mttr_hours,
        "trend_7d": trend_7d,
    })


@router.get("/overview")
async def get_problem_overview(
    time_range: str = Query("7d"),
    session: Session = Depends(get_session),
) -> dict:
    """问题概览 — 含趋势和统计摘要."""
    time_ranges = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}
    hours = time_ranges.get(time_range, 168)
    since = datetime.utcnow() - timedelta(hours=hours)

    overview = _compute_overview(session, since)

    # 7 日趋势
    trend_7d = []
    for i in range(7):
        day = datetime.utcnow().date() - timedelta(days=6 - i)
        day_start = datetime(day.year, day.month, day.day)
        day_end = day_start + timedelta(days=1)

        day_stmt = select(func.count()).select_from(Problem).where(
            Problem.created_at >= day_start, Problem.created_at < day_end
        )
        day_count = session.exec(day_stmt).one()

        resolved_stmt = select(func.count()).select_from(Problem).where(
            Problem.resolved_at >= day_start,  # type: ignore[arg-type]
            Problem.resolved_at < day_end,  # type: ignore[arg-type]
        )
        resolved_count = session.exec(resolved_stmt).one()

        trend_7d.append({
            "date": day.isoformat(),
            "created": day_count,
            "resolved": resolved_count,
        })

    overview["trend_7d"] = trend_7d

    return _success(overview)


@router.get("")
async def list_problems(
    status: str | None = Query(None),
    severity: str | None = Query(None),
    service: str | None = Query(None),
    assignee: str | None = Query(None),
    time_range: str = Query("7d"),
    search: str | None = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict:
    """问题列表 — 支持多维度筛选、排序和分页（含增强字段）."""
    stmt = select(Problem)

    if status:
        stmt = stmt.where(Problem.status == status)
    if severity:
        stmt = stmt.where(Problem.severity == severity)
    if service:
        stmt = stmt.where(Problem.service == service)
    if assignee:
        stmt = stmt.where(Problem.assignee == assignee)

    # 时间范围
    time_ranges = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}
    hours = time_ranges.get(time_range, 168)
    since = datetime.utcnow() - timedelta(hours=hours)
    stmt = stmt.where(Problem.created_at >= since)

    # 搜索
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            col(Problem.title).ilike(pattern)
            | col(Problem.service).ilike(pattern)
            | col(Problem.id).ilike(pattern)
        )

    # 总数
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = session.exec(count_stmt).one()

    # 概览统计
    overview = _compute_overview(session, since)

    # 排序
    sort_columns = {
        "created_at": Problem.created_at,
        "updated_at": Problem.updated_at,
        "severity": Problem.severity,
        "event_count": Problem.event_count,
    }
    sort_col = sort_columns.get(sort_by, Problem.created_at)
    if sort_order == "asc":
        stmt = stmt.order_by(sort_col.asc())  # type: ignore[union-attr]
    else:
        stmt = stmt.order_by(sort_col.desc())  # type: ignore[union-attr]

    # 分页
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)

    problems = session.exec(stmt).all()

    return _success({
        "overview": overview,
        "items": [_problem_to_summary(p, session) for p in problems],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.post("")
async def create_problem(
    body: ProblemCreateRequest,
    session: Session = Depends(get_session),
) -> dict:
    """手动创建问题."""
    init_problem_counter(session)

    now = datetime.utcnow()
    problem_id = _next_problem_id()

    problem = Problem(
        id=problem_id,
        title=body.title,
        description=body.description,
        severity=body.severity,
        service=body.service or "",
        status="open",
        assignee=body.assignee,
        event_count=0,
        source_type="manual",
        rca_status="not_started",
        root_cause_status="unknown",
        created_at=now,
        updated_at=now,
    )
    session.add(problem)

    # 创建时间线
    timeline = ProblemTimeline(
        problem_id=problem_id,
        action="created",
        actor="user",
        content=f"问题创建: {body.title}",
        timestamp=now,
    )
    session.add(timeline)

    # 关联事件（如果提供）
    if body.event_ids:
        for i, event_id in enumerate(body.event_ids):
            pe = ProblemEvent(
                problem_id=problem_id,
                event_id=event_id,
                event_severity=body.severity,
                event_service=body.service or "",
                relation_tag="first" if i == 0 else "parallel",
                added_at=now,
            )
            session.add(pe)
            problem.event_count += 1

        # 记录事件关联时间线
        event_timeline = ProblemTimeline(
            problem_id=problem_id,
            action="event_added",
            actor="user",
            content=f"关联 {len(body.event_ids)} 个异常事件",
            timestamp=now,
        )
        session.add(event_timeline)

    session.add(problem)
    session.commit()
    session.refresh(problem)

    result = _problem_to_summary(problem, session)
    logger.info("Created problem %s: %s", problem_id, body.title)
    return _success(result)


@router.get("/{problem_id}")
async def get_problem_detail(
    problem_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """问题详情 — 包含关联事件、时间线、备注、RCA、调查信息."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    # 关联事件
    events = session.exec(
        select(ProblemEvent)
        .where(ProblemEvent.problem_id == problem_id)
        .order_by(ProblemEvent.event_detected_at.asc())  # type: ignore[arg-type]
    ).all()

    # 时间线
    timeline = session.exec(
        select(ProblemTimeline)
        .where(ProblemTimeline.problem_id == problem_id)
        .order_by(ProblemTimeline.timestamp.asc())  # type: ignore[arg-type]
    ).all()

    # 备注
    notes = session.exec(
        select(ProblemNote)
        .where(ProblemNote.problem_id == problem_id)
        .order_by(ProblemNote.created_at.desc())  # type: ignore[arg-type]
    ).all()

    # 基础摘要
    result = _problem_to_summary(problem, session)

    # 问题来源信息
    first_event = events[0] if events else None
    result["sourceType"] = problem.source_type
    result["aggregationWindow"] = ""
    if events:
        first_time = events[0].event_detected_at
        last_time = events[-1].event_detected_at
        if first_time and last_time:
            result["aggregationWindow"] = (
                f"{first_time.strftime('%Y-%m-%d %H:%M')} ~ "
                f"{last_time.strftime('%Y-%m-%d %H:%M') if problem.resolved_at else '至今'}"
            )
    result["aggregationReasons"] = problem.aggregation_reasons or []
    result["firstEventId"] = first_event.event_id if first_event else None
    result["firstEventTitle"] = (
        f"{first_event.event_service} {first_event.event_metric}" if first_event else None
    )

    # 关联事件
    result["relatedEvents"] = [_event_to_dict(e) for e in events]

    # 时间线
    result["timeline"] = [_timeline_to_dict(t) for t in timeline]

    # 备注
    result["notes"] = [_note_to_dict(n) for n in notes]

    # RCA 信息
    result["rca"] = {
        "rcaId": f"RCA-{problem_id}" if problem.rca_status != "not_started" else None,
        "status": problem.rca_status,
        "recommendedRootCause": problem.suspected_root_cause,
        "confidence": problem.rca_confidence,
        "analyzedAt": problem.rca_analyzed_at.isoformat() if problem.rca_analyzed_at else None,
    }

    # 调查信息
    impact_services: set[str] = set()
    for e in events:
        if e.event_service:
            impact_services.add(e.event_service)

    result["investigation"] = {
        "suspectedRootCause": problem.suspected_root_cause,
        "confidence": problem.rca_confidence,
        "conclusionStatus": problem.root_cause_status,
        "updatedAt": problem.updated_at.isoformat() if problem.updated_at else None,
        "updatedBy": problem.assignee,
        "impactedServiceCount": len(impact_services),
        "impactedObjectCount": len(events),
        "criticalPathAffected": any(e.event_severity == "critical" for e in events),
        "impactWindow": result.get("aggregationWindow", ""),
    }

    result["root_cause_summary"] = problem.root_cause_summary
    result["affected_services"] = problem.affected_services or []

    return _success(result)


@router.patch("/{problem_id}/status")
async def update_problem_status(
    problem_id: str,
    body: StatusUpdateRequest,
    session: Session = Depends(get_session),
) -> dict:
    """更新问题状态."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    valid_transitions = {
        "open": ["investigating", "resolved", "closed"],
        "investigating": ["open", "resolved", "closed"],
        "resolved": ["open", "closed"],
        "closed": ["open"],
    }
    allowed = valid_transitions.get(problem.status, [])
    if body.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"不允许从 {problem.status} 转换到 {body.status}",
        )

    now = datetime.utcnow()
    old_status = problem.status
    problem.status = body.status
    problem.updated_at = now

    if body.status == "resolved":
        problem.resolved_at = now
    elif body.status == "open" and old_status == "resolved":
        problem.resolved_at = None

    session.add(problem)

    # 记录时间线
    content = f"状态变更: {old_status} -> {body.status}"
    if body.comment:
        content += f" ({body.comment})"

    timeline = ProblemTimeline(
        problem_id=problem_id,
        action="status_changed" if body.status not in ("resolved", "closed") else body.status,
        actor="user",
        content=content,
        old_value=old_status,
        new_value=body.status,
        timestamp=now,
    )
    session.add(timeline)
    session.commit()

    session.refresh(problem)
    return _success(_problem_to_summary(problem, session))


@router.patch("/{problem_id}/assignee")
async def update_problem_assignee(
    problem_id: str,
    body: AssigneeUpdateRequest,
    session: Session = Depends(get_session),
) -> dict:
    """分配问题负责人."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    now = datetime.utcnow()
    old_assignee = problem.assignee or ""
    problem.assignee = body.assignee
    problem.updated_at = now
    session.add(problem)

    timeline = ProblemTimeline(
        problem_id=problem_id,
        action="assigned",
        actor="user",
        content=f"分配: {old_assignee or '无'} -> {body.assignee}",
        old_value=old_assignee,
        new_value=body.assignee,
        timestamp=now,
    )
    session.add(timeline)
    session.commit()

    session.refresh(problem)
    return _success(_problem_to_summary(problem, session))


@router.post("/{problem_id}/notes")
async def add_note(
    problem_id: str,
    body: NoteRequest,
    session: Session = Depends(get_session),
) -> dict:
    """添加问题备注（存入 problem_notes 表）."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    now = datetime.utcnow()

    # 存入独立备注表
    note = ProblemNote(
        problem_id=problem_id,
        author=body.author or "user",
        type=body.type or "general",
        content=body.content,
        created_at=now,
    )
    session.add(note)

    # 同时记录时间线
    timeline = ProblemTimeline(
        problem_id=problem_id,
        action="commented",
        actor=body.author or "user",
        content=body.content,
        timestamp=now,
    )
    session.add(timeline)

    problem.updated_at = now
    session.add(problem)
    session.commit()

    session.refresh(note)
    return _success(_note_to_dict(note))


@router.post("/{problem_id}/events")
async def merge_events_to_problem(
    problem_id: str,
    body: MergeEventsRequest,
    session: Session = Depends(get_session),
) -> dict:
    """归并事件到已有问题."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    if problem.status in ("resolved", "closed"):
        raise HTTPException(
            status_code=400,
            detail=f"问题 {problem_id} 已 {problem.status}，不可归并新事件",
        )

    now = datetime.utcnow()
    merged = 0

    for event_id in body.event_ids:
        # 检查是否已关联
        existing = session.exec(
            select(ProblemEvent).where(
                ProblemEvent.problem_id == problem_id,
                ProblemEvent.event_id == event_id,
            )
        ).first()
        if existing:
            continue

        pe = ProblemEvent(
            problem_id=problem_id,
            event_id=event_id,
            event_severity=problem.severity,
            event_service=problem.service,
            relation_tag="parallel",
            added_at=now,
        )
        session.add(pe)
        problem.event_count += 1
        merged += 1

    if merged > 0:
        problem.updated_at = now
        session.add(problem)

        # 记录时间线
        timeline = ProblemTimeline(
            problem_id=problem_id,
            action="event_added",
            actor="user",
            content=f"手动归并 {merged} 个异常事件",
            timestamp=now,
        )
        session.add(timeline)

    session.commit()
    session.refresh(problem)

    return _success({
        "merged": merged,
        "problemId": problem_id,
        "eventCount": problem.event_count,
    })


@router.post("/{problem_id}/rca")
async def trigger_rca(
    problem_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """发起根因分析."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    if problem.rca_status == "running":
        raise HTTPException(status_code=400, detail="RCA 正在运行中，请勿重复发起")

    problem.rca_status = "running"
    problem.updated_at = datetime.utcnow()
    session.add(problem)

    # 记录时间线
    timeline = ProblemTimeline(
        problem_id=problem_id,
        action="commented",
        actor="user",
        content="发起根因分析任务",
        timestamp=datetime.utcnow(),
    )
    session.add(timeline)
    session.commit()

    # 异步执行 RCA (在后台运行)
    import asyncio
    asyncio.create_task(_run_rca_async(problem_id))

    return _success({"status": "running"})


@router.get("/{problem_id}/rca")
async def get_rca(
    problem_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """获取根因分析结果."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    return _success({
        "status": problem.rca_status,
        "recommendedRootCause": problem.suspected_root_cause,
        "confidence": problem.rca_confidence,
        "analyzedAt": problem.rca_analyzed_at.isoformat() if problem.rca_analyzed_at else None,
    })


@router.get("/{problem_id}/events")
async def get_problem_events(
    problem_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """问题关联的异常事件列表."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    events = session.exec(
        select(ProblemEvent)
        .where(ProblemEvent.problem_id == problem_id)
        .order_by(ProblemEvent.event_detected_at.desc())  # type: ignore[arg-type]
    ).all()

    return _success({"items": [_event_to_dict(e) for e in events]})


# =============================================
#  RCA 异步执行
# =============================================


async def _run_rca_async(problem_id: str) -> None:
    """异步执行 RCA 分析 (调用 DeepSeek API)."""
    import httpx

    from app.database import engine as db_engine

    try:
        with Session(db_engine) as session:
            problem = session.get(Problem, problem_id)
            if not problem:
                return

            # 收集上下文
            events = session.exec(
                select(ProblemEvent).where(ProblemEvent.problem_id == problem_id)
            ).all()
            timeline = session.exec(
                select(ProblemTimeline).where(ProblemTimeline.problem_id == problem_id)
            ).all()
            notes = session.exec(
                select(ProblemNote).where(ProblemNote.problem_id == problem_id)
            ).all()

            # 构建上下文文本
            context_parts = [
                f"问题: {problem.title}",
                f"服务: {problem.service}",
                f"严重级别: {problem.severity}",
                f"状态: {problem.status}",
                f"事件数: {problem.event_count}",
                "",
                "关联事件:",
            ]
            for e in events:
                context_parts.append(
                    f"  - {e.event_service} {e.event_metric} "
                    f"(当前值={e.event_value}, 严重级别={e.event_severity})"
                )

            if notes:
                context_parts.append("")
                context_parts.append("备注:")
                for n in notes:
                    context_parts.append(f"  - [{n.type}] {n.content}")

            context = "\n".join(context_parts)

            # 调用 DeepSeek
            prompt = (
                "你是一个 AIOps 根因分析专家。根据以下问题上下文，分析可能的根因。\n"
                "请给出：\n"
                "1. 最可能的根因（一句话概述）\n"
                "2. 置信度（0-100 的整数）\n"
                "3. 分析依据\n\n"
                f"上下文:\n{context}\n\n"
                "请严格按以下 JSON 格式返回：\n"
                '{"root_cause": "根因描述", "confidence": 75, "reasoning": "分析依据"}'
            )

            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        "https://api.deepseek.com/chat/completions",
                        headers={
                            "Authorization": "Bearer sk-c4e18be15f4646858ace474b9e6490f9",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "deepseek-chat",
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.3,
                        },
                    )
                    resp.raise_for_status()
                    result = resp.json()

                content = result["choices"][0]["message"]["content"]

                # 解析 JSON 结果
                import json
                # 尝试提取 JSON
                json_str = content
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0].strip()

                try:
                    rca_result = json.loads(json_str)
                except json.JSONDecodeError:
                    rca_result = {
                        "root_cause": content[:500],
                        "confidence": 50,
                    }

                now = datetime.utcnow()
                problem.suspected_root_cause = rca_result.get("root_cause", content[:500])
                problem.rca_confidence = int(rca_result.get("confidence", 50))
                problem.rca_status = "completed"
                problem.root_cause_status = "suspected"
                problem.rca_analyzed_at = now
                problem.updated_at = now

                # 记录时间线
                rca_timeline = ProblemTimeline(
                    problem_id=problem_id,
                    action="commented",
                    actor="RCA 引擎",
                    content=f"RCA 完成: {problem.suspected_root_cause[:100]}... (置信度 {problem.rca_confidence}%)",
                    timestamp=now,
                )
                session.add(rca_timeline)

            except Exception as e:
                logger.error("DeepSeek API call failed: %s", e)
                problem.rca_status = "failed"
                problem.updated_at = datetime.utcnow()

            session.add(problem)
            session.commit()

    except Exception:
        logger.exception("RCA async execution failed for problem %s", problem_id)
