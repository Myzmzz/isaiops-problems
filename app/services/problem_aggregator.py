"""问题聚合引擎.

将 isaiops-be 的异常事件聚合为 Problem 工单。
Problem 比 Alert 更高层次，按服务 + 时间窗口将多个事件聚合为一个问题。
作为 AggregationEngine 的扩展，在每次轮询周期中同步运行。
"""

import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.config import AGGREGATION_WINDOW_MINUTES
from app.models.problem import Problem
from app.models.problem_event import ProblemEvent
from app.models.problem_timeline import ProblemTimeline

logger = logging.getLogger(__name__)

# 问题 ID 计数器
_problem_counter: int = 0


def _next_problem_id() -> str:
    """生成下一个问题 ID (PRB-2026-XXXX)."""
    global _problem_counter
    _problem_counter += 1
    now = datetime.utcnow()
    return f"PRB-{now.year}-{_problem_counter:04d}"


def init_counter(session: Session) -> None:
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
    logger.info("Problem aggregator counter initialized to %d", _problem_counter)


def aggregate_event_to_problem(session: Session, event: dict) -> None:
    """将单个异常事件聚合到 Problem 工单.

    逻辑:
    1. 查找同服务的活跃 Problem（open 或 investigating 状态，在聚合窗口内）
    2. 如果找到，追加事件到该 Problem
    3. 如果没找到，创建新 Problem

    Args:
        session: 数据库会话。
        event: 来自 isaiops-be 的事件字典。
    """
    service = event.get("service", "unknown")
    severity = event.get("severity", "warning")
    event_id = event.get("id", "")

    if not event_id:
        return

    # 检查事件是否已关联到某个 Problem
    existing_link = session.exec(
        select(ProblemEvent).where(ProblemEvent.event_id == event_id)
    ).first()
    if existing_link:
        return

    # 查找同服务的活跃 Problem（时间窗口内）
    window_start = datetime.utcnow() - timedelta(minutes=AGGREGATION_WINDOW_MINUTES)
    stmt = select(Problem).where(
        Problem.service == service,
        Problem.status.in_(["open", "investigating"]),
        Problem.updated_at >= window_start,
    )
    existing_problem = session.exec(stmt).first()

    if existing_problem:
        _append_to_problem(session, existing_problem, event)
    else:
        _create_problem(session, event)


def _create_problem(session: Session, event: dict) -> Problem:
    """创建新的 Problem 工单."""
    now = datetime.utcnow()
    problem_id = _next_problem_id()
    service = event.get("service", "unknown")
    severity = event.get("severity", "warning")
    metric = event.get("metric", "")
    rule_name = event.get("rule_name", event.get("ruleName", ""))

    title = f"[{severity.upper()}] {service} — {rule_name or metric}"

    problem = Problem(
        id=problem_id,
        title=title,
        description=f"自动聚合: {service} 服务检测到异常",
        service=service,
        severity=severity,
        status="open",
        event_count=1,
        affected_services=[service],
        source_type="system_generated",
        aggregation_reasons=["同服务", "同时间窗"],
        rca_status="not_started",
        root_cause_status="unknown",
        created_at=now,
        updated_at=now,
    )
    session.add(problem)

    # 关联事件 (首个事件标记为 first)
    _link_event(session, problem_id, event, relation_tag="first")

    # 创建时间线
    timeline = ProblemTimeline(
        problem_id=problem_id,
        action="created",
        actor="system",
        content=f"问题自动创建: {title}",
        timestamp=now,
    )
    session.add(timeline)

    logger.info("Created problem %s for service %s", problem_id, service)
    return problem


def _append_to_problem(session: Session, problem: Problem, event: dict) -> None:
    """将事件追加到已有 Problem 工单."""
    now = datetime.utcnow()
    event_id = event.get("id", "")
    severity = event.get("severity", "warning")

    problem.event_count += 1
    problem.updated_at = now

    # 严重度提升
    severity_order = {"critical": 3, "warning": 2, "info": 1}
    if severity_order.get(severity, 0) > severity_order.get(problem.severity, 0):
        problem.severity = severity

    # 更新受影响服务列表
    event_service = event.get("service", "")
    if event_service:
        affected = problem.affected_services or []
        if event_service not in affected:
            affected.append(event_service)
            problem.affected_services = affected

    session.add(problem)

    # 关联事件 (后续事件标记为 derived)
    _link_event(session, problem.id, event, relation_tag="derived")

    # 记录时间线
    timeline = ProblemTimeline(
        problem_id=problem.id,
        action="event_added",
        actor="system",
        content=f"新事件关联: {event.get('metric', event_id)}",
        timestamp=now,
    )
    session.add(timeline)

    logger.info(
        "Appended event to problem %s (count=%d)", problem.id, problem.event_count
    )


def _link_event(
    session: Session, problem_id: str, event: dict, relation_tag: str = "parallel"
) -> None:
    """创建 Problem-事件关联记录."""
    detected_at_str = event.get("detected_at", event.get("detectedAt", ""))
    try:
        detected_at = datetime.fromisoformat(detected_at_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        detected_at = datetime.utcnow()

    pe = ProblemEvent(
        problem_id=problem_id,
        event_id=event.get("id", ""),
        event_service=event.get("service", ""),
        event_severity=event.get("severity", "warning"),
        event_metric=event.get("metric", ""),
        event_rule_id=event.get("rule_id", ""),
        event_value=float(event.get("current_value", event.get("currentValue", 0))),
        event_threshold=float(event.get("expected_value", event.get("expectedValue", 0))) or None,
        event_detected_at=detected_at,
        event_status=event.get("status", "active"),
        relation_tag=relation_tag,
        added_at=datetime.utcnow(),
    )
    session.add(pe)
