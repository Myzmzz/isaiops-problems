"""问题管理 API.

实现 problems.yaml 契约定义的 Problem CRUD、状态变更、分配、备注接口。
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from app.database import get_session
from app.models.problem import Problem
from app.models.problem_event import ProblemEvent
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


# =============================================
#  辅助函数
# =============================================


def _success(data: object = None) -> dict:
    """构造统一成功响应."""
    return {"code": 0, "message": "success", "data": data}


def _problem_to_summary(p: Problem) -> dict:
    """将 Problem 模型转为列表摘要字典."""
    duration_hours = None
    if p.resolved_at and p.created_at:
        delta = p.resolved_at - p.created_at
        duration_hours = round(delta.total_seconds() / 3600, 1)

    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "severity": p.severity,
        "status": p.status,
        "service": p.service,
        "assignee": p.assignee,
        "event_count": p.event_count,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
        "duration_hours": duration_hours,
    }


def _event_to_dict(pe: ProblemEvent) -> dict:
    """将 ProblemEvent 转为 API 响应字典."""
    return {
        "event_id": pe.event_id,
        "rule_id": pe.event_rule_id,
        "metric": pe.event_metric,
        "service": pe.event_service,
        "severity": pe.event_severity,
        "value": pe.event_value,
        "threshold": pe.event_threshold,
        "detected_at": pe.event_detected_at.isoformat() if pe.event_detected_at else None,
        "resolved_at": pe.event_resolved_at.isoformat() if pe.event_resolved_at else None,
        "status": pe.event_status,
    }


def _timeline_to_dict(tl: ProblemTimeline) -> dict:
    """将时间线条目转为 API 响应字典."""
    return {
        "id": tl.id,
        "action": tl.action,
        "actor": tl.actor,
        "content": tl.content,
        "old_value": tl.old_value,
        "new_value": tl.new_value,
        "timestamp": tl.timestamp.isoformat() if tl.timestamp else None,
    }


def _compute_overview(session: Session, since: datetime) -> dict:
    """计算问题概览统计."""
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
    }


# =============================================
#  API 端点
# =============================================


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
    """问题列表 — 支持多维度筛选、排序和分页."""
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
        "items": [_problem_to_summary(p) for p in problems],
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
        for event_id in body.event_ids:
            pe = ProblemEvent(
                problem_id=problem_id,
                event_id=event_id,
                event_severity=body.severity,
                event_service=body.service or "",
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

    # 获取事件和时间线
    events = session.exec(
        select(ProblemEvent).where(ProblemEvent.problem_id == problem_id)
    ).all()
    timeline_entries = session.exec(
        select(ProblemTimeline)
        .where(ProblemTimeline.problem_id == problem_id)
        .order_by(ProblemTimeline.timestamp.asc())  # type: ignore[arg-type]
    ).all()

    result = _problem_to_summary(problem)
    result["events"] = [_event_to_dict(e) for e in events]
    result["timeline"] = [_timeline_to_dict(t) for t in timeline_entries]
    result["root_cause_summary"] = problem.root_cause_summary
    result["affected_services"] = problem.affected_services or []

    logger.info("Created problem %s: %s", problem_id, body.title)
    return _success(result)


@router.get("/{problem_id}")
async def get_problem_detail(
    problem_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """问题详情 — 包含关联事件和处理时间线."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    events = session.exec(
        select(ProblemEvent)
        .where(ProblemEvent.problem_id == problem_id)
        .order_by(ProblemEvent.event_detected_at.desc())  # type: ignore[arg-type]
    ).all()

    timeline = session.exec(
        select(ProblemTimeline)
        .where(ProblemTimeline.problem_id == problem_id)
        .order_by(ProblemTimeline.timestamp.asc())  # type: ignore[arg-type]
    ).all()

    result = _problem_to_summary(problem)
    result["events"] = [_event_to_dict(e) for e in events]
    result["timeline"] = [_timeline_to_dict(t) for t in timeline]
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
    return _success(_problem_to_summary(problem))


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
    return _success(_problem_to_summary(problem))


@router.post("/{problem_id}/notes")
async def add_note(
    problem_id: str,
    body: NoteRequest,
    session: Session = Depends(get_session),
) -> dict:
    """添加问题备注."""
    problem = session.get(Problem, problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail=f"问题 {problem_id} 不存在")

    now = datetime.utcnow()
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

    session.refresh(timeline)
    return _success(_timeline_to_dict(timeline))


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
