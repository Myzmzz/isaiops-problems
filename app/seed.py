"""种子数据 — 填充告警、问题、时间线、静默规则.

生成合理的示例数据，用于前端页面展示和功能验证。
数据基于 isaiops-be 中的实际异常事件（train-ticket 微服务）。

用法:
    python -m app.seed          # 本地执行
    # 或在 K8s Pod 内:
    python -m app.seed
"""

import logging
import random
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.database import create_tables, engine
from app.models.alert import Alert
from app.models.alert_event import AlertEvent
from app.models.alert_timeline import AlertTimeline
from app.models.problem import Problem
from app.models.problem_event import ProblemEvent
from app.models.problem_timeline import ProblemTimeline
from app.models.silence_rule import SilenceRule

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 固定随机种子，保证可重复性
random.seed(42)

# =============================================
#  常量 & 参考数据
# =============================================

NOW = datetime.utcnow()

SERVICES = [
    "ts-order-service",
    "ts-travel-service",
    "ts-station-service",
    "ts-config-service",
    "ts-route-service",
    "ts-food-service",
    "ts-cancel-service",
    "ts-auth-service",
    "ts-basic-service",
    "ts-inside-payment-service",
    "ts-contacts-service",
    "ts-execute-service",
]

USERS = ["张三", "李四", "王五", "赵六", "陈七"]

# 来自 isaiops-be 的真实异常事件 ID（用于关联）
REAL_EVENT_IDS = [
    "ANO-20260304022345-005",
    "ANO-20260303232711-007",
    "ANO-20260303192611-019",
    "ANO-20260303191311-015",
    "ANO-20260303153419-019",
    "ANO-20260303145619-019",
    "ANO-20260303130219-019",
    "ANO-20260303125819-019",
    "ANO-20260303125619-020",
    "ANO-20260303125519-019",
    "ANO-20260303091633-015",
    "ANO-20260303065833-011",
    "ANO-20260303052633-019",
    "ANO-20260303040233-0080",
    "ANO-20260303030233-0005",
    "ANO-20260302110233-0055",
    "ANO-20260225070233-0019",
    "ANO-20260227200233-0030",
    "ANO-20260228060233-0085",
    "ANO-20260301130233-0025",
    "ANO-20260301030233-0064",
]

# 检测模式
DETECTION_MODES = ["threshold", "baseline", "change", "outlier", "missing"]

# 指标名称模板
METRICS = [
    "rate(container_resources_cpu_usage_seconds_total{{app_id=\"/k8s/train-ticket/{svc}\"}}[5m])",
    "container_resources_memory_rss_bytes{{app_id=\"/k8s/train-ticket/{svc}\"}}",
    "container_net_latency_seconds{{app_id=\"/k8s/train-ticket/{svc}\"}}",
    "rate(container_net_tcp_bytes_sent_total{{app_id=\"/k8s/train-ticket/{svc}\"}}[5m])",
    "container_restarts_total{{app_id=\"/k8s/train-ticket/{svc}\"}}",
    "rate(container_resources_cpu_throttled_seconds_total{{app_id=\"/k8s/train-ticket/{svc}\"}}[5m])",
]


def _random_time(hours_ago_min: int, hours_ago_max: int) -> datetime:
    """生成 hours_ago_min ~ hours_ago_max 小时前的随机时间."""
    offset = random.uniform(hours_ago_min, hours_ago_max)
    return NOW - timedelta(hours=offset)


def _duration_str(delta: timedelta) -> str:
    """将 timedelta 转为可读字符串."""
    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"


# =============================================
#  生成告警 Alerts
# =============================================


def _create_alerts(session: Session) -> list[Alert]:
    """创建 25 个告警，覆盖各状态和严重度."""
    alerts: list[Alert] = []

    alert_configs = [
        # (severity, status, service_idx, hours_ago, assignee_idx, mode)
        # --- Critical 告警 (8) ---
        ("critical", "triggered", 0, 2, None, "threshold"),
        ("critical", "triggered", 1, 5, None, "baseline"),
        ("critical", "acknowledged", 2, 8, 0, "change"),
        ("critical", "acknowledged", 4, 12, 1, "outlier"),
        ("critical", "resolved", 0, 48, 0, "threshold"),
        ("critical", "resolved", 3, 36, 2, "baseline"),
        ("critical", "resolved", 5, 72, 1, "change"),
        ("critical", "suppressed", 6, 24, None, "outlier"),
        # --- Warning 告警 (10) ---
        ("warning", "triggered", 7, 3, None, "baseline"),
        ("warning", "triggered", 8, 6, None, "threshold"),
        ("warning", "triggered", 9, 10, None, "change"),
        ("warning", "acknowledged", 10, 15, 2, "outlier"),
        ("warning", "acknowledged", 11, 20, 3, "baseline"),
        ("warning", "resolved", 1, 60, 0, "threshold"),
        ("warning", "resolved", 2, 50, 4, "change"),
        ("warning", "resolved", 5, 96, 1, "baseline"),
        ("warning", "suppressed", 3, 18, None, "missing"),
        ("warning", "suppressed", 8, 30, None, "threshold"),
        # --- Info 告警 (7) ---
        ("info", "triggered", 0, 1, None, "missing"),
        ("info", "triggered", 4, 4, None, "baseline"),
        ("info", "acknowledged", 6, 9, 4, "change"),
        ("info", "resolved", 7, 40, 3, "missing"),
        ("info", "resolved", 9, 55, 2, "baseline"),
        ("info", "resolved", 10, 80, 0, "threshold"),
        ("info", "suppressed", 11, 22, None, "outlier"),
    ]

    rule_ids = [f"rule-{i:03d}" for i in range(1, 26)]

    for idx, (severity, status, svc_idx, hours_ago, assignee_idx, mode) in enumerate(alert_configs):
        svc = SERVICES[svc_idx % len(SERVICES)]
        created = _random_time(hours_ago, hours_ago + 2)
        first_triggered = created - timedelta(minutes=random.randint(5, 30))
        last_triggered = created + timedelta(minutes=random.randint(0, 60))

        score = {
            "critical": random.randint(70, 100),
            "warning": random.randint(40, 69),
            "info": random.randint(10, 39),
        }[severity]

        assignee = USERS[assignee_idx] if assignee_idx is not None else None
        acknowledged_at = None
        resolved_at = None
        duration = None

        if status in ("acknowledged", "resolved", "suppressed"):
            acknowledged_at = created + timedelta(minutes=random.randint(5, 120))

        if status == "resolved":
            resolved_at = acknowledged_at + timedelta(minutes=random.randint(30, 600)) if acknowledged_at else created + timedelta(hours=random.randint(1, 12))
            duration = _duration_str(resolved_at - first_triggered)

        metric = random.choice(METRICS).format(svc=svc)
        # 告警标题
        metric_label = {
            "threshold": "固定阈值",
            "baseline": "动态基线",
            "change": "突变检测",
            "outlier": "离群检测",
            "missing": "数据缺失",
        }[mode]

        severity_label = {"critical": "严重", "warning": "警告", "info": "信息"}[severity]

        alert = Alert(
            id=f"ALT-{idx + 1:03d}",
            title=f"[{severity_label}] {svc} {metric_label}异常",
            description=f"{svc} 的 {metric_label} 检测到异常，当前值偏离预期 {random.uniform(20, 80):.1f}%",
            service=svc,
            severity=severity,
            score=score,
            status=status,
            assignee=assignee,
            anomaly_count=random.randint(1, 5),
            first_triggered_at=first_triggered,
            last_triggered_at=last_triggered,
            duration=duration,
            source_rule_id=rule_ids[idx % len(rule_ids)],
            detection_mode=mode,
            tags={"source": "auto", "cluster": "train-ticket"},
            created_at=created,
            updated_at=resolved_at or acknowledged_at or created,
            acknowledged_at=acknowledged_at,
            resolved_at=resolved_at,
        )
        session.add(alert)
        alerts.append(alert)

    session.flush()
    logger.info("创建了 %d 个告警", len(alerts))
    return alerts


# =============================================
#  生成告警事件关联 AlertEvent
# =============================================


def _create_alert_events(session: Session, alerts: list[Alert]) -> None:
    """为每个告警创建 1-3 个事件关联."""
    event_idx = 0
    for alert in alerts:
        count = random.randint(1, min(3, len(REAL_EVENT_IDS)))
        for _ in range(count):
            event_id = REAL_EVENT_IDS[event_idx % len(REAL_EVENT_IDS)]
            event_idx += 1

            ae = AlertEvent(
                alert_id=alert.id,
                event_id=event_id,
                event_severity=alert.severity,
                event_score=alert.score - random.randint(0, 10),
                event_detected_at=alert.first_triggered_at + timedelta(minutes=random.randint(0, 5)),
                event_metric=random.choice(METRICS).format(svc=alert.service),
                event_rule_id=alert.source_rule_id or "",
                event_current_value=round(random.uniform(50, 200), 2),
                event_expected_value=round(random.uniform(20, 80), 2),
                event_deviation_percent=round(random.uniform(-80, 80), 2),
                event_status="escalated" if alert.status in ("triggered", "acknowledged") else "resolved",
            )
            session.add(ae)

    session.flush()
    logger.info("创建了告警事件关联")


# =============================================
#  生成告警时间线 AlertTimeline
# =============================================


def _create_alert_timeline(session: Session, alerts: list[Alert]) -> None:
    """为每个告警创建处理时间线."""
    for alert in alerts:
        # 创建事件
        session.add(AlertTimeline(
            alert_id=alert.id,
            action="created",
            actor="system",
            detail=f"异常事件自动聚合为告警: {alert.title}",
            created_at=alert.created_at,
        ))

        # 升级事件
        session.add(AlertTimeline(
            alert_id=alert.id,
            action="escalated",
            actor="system",
            detail=f"异常评分 {alert.score} 超过阈值，自动升级为告警",
            created_at=alert.created_at + timedelta(seconds=30),
        ))

        if alert.status in ("acknowledged", "resolved"):
            session.add(AlertTimeline(
                alert_id=alert.id,
                action="acknowledged",
                actor=alert.assignee or "system",
                detail=f"告警已确认，开始排查",
                created_at=alert.acknowledged_at or alert.created_at + timedelta(minutes=10),
            ))

        if alert.assignee:
            session.add(AlertTimeline(
                alert_id=alert.id,
                action="assigned",
                actor="system",
                detail=f"分配给 {alert.assignee}",
                created_at=alert.created_at + timedelta(minutes=random.randint(5, 15)),
            ))

        if alert.status == "resolved":
            session.add(AlertTimeline(
                alert_id=alert.id,
                action="resolved",
                actor=alert.assignee or "system",
                detail="告警已解决，问题根因已定位并修复",
                created_at=alert.resolved_at or alert.created_at + timedelta(hours=2),
            ))

        if alert.status == "suppressed":
            session.add(AlertTimeline(
                alert_id=alert.id,
                action="suppressed",
                actor="system",
                detail="告警被静默规则匹配，自动抑制",
                created_at=alert.created_at + timedelta(minutes=random.randint(1, 5)),
            ))

    session.flush()
    logger.info("创建了告警时间线")


# =============================================
#  生成问题 Problems
# =============================================


def _create_problems(session: Session) -> list[Problem]:
    """创建 12 个问题工单，覆盖各状态和严重度."""
    problems: list[Problem] = []

    problem_configs = [
        # (title, severity, status, service, hours_ago, assignee_idx, root_cause)
        (
            "ts-order-service CPU 持续高负载",
            "critical", "open", "ts-order-service", 3, 0,
            None,
        ),
        (
            "ts-travel-service 内存泄漏疑似",
            "critical", "investigating", "ts-travel-service", 8, 1,
            "内存使用持续增长，疑似存在对象缓存未释放问题",
        ),
        (
            "ts-station-service 网络延迟突增",
            "critical", "investigating", "ts-station-service", 14, 0,
            "DNS 解析延迟异常，可能与 CoreDNS 配置有关",
        ),
        (
            "ts-route-service CPU 离群检测触发",
            "critical", "resolved", "ts-route-service", 48, 2,
            "Pod 调度到低性能节点导致 CPU 使用率相对偏高，已通过 nodeAffinity 修复",
        ),
        (
            "ts-config-service 内存基线偏离",
            "warning", "open", "ts-config-service", 6, None,
            None,
        ),
        (
            "ts-food-service 网络流量异常",
            "warning", "open", "ts-food-service", 10, 3,
            None,
        ),
        (
            "ts-cancel-service 网络发送字节异常",
            "warning", "investigating", "ts-cancel-service", 20, 4,
            "批量退票请求导致网络流量临时增大",
        ),
        (
            "ts-auth-service 数据缺失告警",
            "info", "resolved", "ts-auth-service", 72, 1,
            "Mongo exporter 短暂断连导致数据缺失，已自动恢复",
        ),
        (
            "多服务 CPU 同时异常",
            "critical", "resolved", "ts-order-service", 96, 0,
            "节点 tcse-flexusx-01 CPU 硬件故障导致多个服务受影响，已迁移 Pod",
        ),
        (
            "ts-inside-payment-service 响应变慢",
            "warning", "resolved", "ts-inside-payment-service", 120, 2,
            "数据库连接池耗尽，增大 max_connections 后恢复",
        ),
        (
            "ts-basic-service 重启次数异常",
            "warning", "closed", "ts-basic-service", 144, 3,
            "OOM Kill 导致频繁重启，已调整 memory limit",
        ),
        (
            "ts-contacts-service 健康检查超时",
            "info", "closed", "ts-contacts-service", 168, 4,
            "readinessProbe 超时设置过短，已调整为 5s",
        ),
    ]

    for idx, (title, severity, status, service, hours_ago, assignee_idx, root_cause) in enumerate(problem_configs):
        created = _random_time(hours_ago, hours_ago + 2)
        assignee = USERS[assignee_idx] if assignee_idx is not None else None

        resolved_at = None
        if status in ("resolved", "closed"):
            resolved_at = created + timedelta(hours=random.randint(2, 24))

        affected = [service]
        if idx in (8,):  # 多服务影响
            affected = ["ts-order-service", "ts-travel-service", "ts-station-service"]

        problem = Problem(
            id=f"PRB-2026-{idx + 1:04d}",
            title=title,
            description=f"检测到 {service} 存在异常，涉及多个检测规则，需要排查",
            service=service,
            severity=severity,
            status=status,
            assignee=assignee,
            event_count=random.randint(1, 6),
            root_cause_summary=root_cause,
            affected_services=affected,
            created_at=created,
            updated_at=resolved_at or created + timedelta(minutes=random.randint(10, 120)),
            resolved_at=resolved_at,
        )
        session.add(problem)
        problems.append(problem)

    session.flush()
    logger.info("创建了 %d 个问题", len(problems))
    return problems


# =============================================
#  生成问题事件关联 ProblemEvent
# =============================================


def _create_problem_events(session: Session, problems: list[Problem]) -> None:
    """为每个问题创建 1-4 个事件关联."""
    event_idx = 0
    for problem in problems:
        count = random.randint(1, min(4, len(REAL_EVENT_IDS)))
        for _ in range(count):
            event_id = REAL_EVENT_IDS[event_idx % len(REAL_EVENT_IDS)]
            event_idx += 1

            pe = ProblemEvent(
                problem_id=problem.id,
                event_id=event_id,
                event_service=problem.service,
                event_severity=problem.severity,
                event_metric=random.choice(METRICS).format(svc=problem.service),
                event_rule_id=f"rule-{random.randint(1, 23):03d}",
                event_value=round(random.uniform(50, 200), 2),
                event_threshold=round(random.uniform(20, 80), 2),
                event_detected_at=problem.created_at - timedelta(minutes=random.randint(5, 60)),
                event_resolved_at=problem.resolved_at if problem.status in ("resolved", "closed") else None,
                event_status="resolved" if problem.status in ("resolved", "closed") else "active",
                added_at=problem.created_at,
            )
            session.add(pe)

    session.flush()
    logger.info("创建了问题事件关联")


# =============================================
#  生成问题时间线 ProblemTimeline
# =============================================


def _create_problem_timeline(session: Session, problems: list[Problem]) -> None:
    """为每个问题创建处理时间线."""
    for problem in problems:
        # 创建
        session.add(ProblemTimeline(
            problem_id=problem.id,
            action="created",
            actor="system",
            content=f"问题自动创建: {problem.title}",
            timestamp=problem.created_at,
        ))

        # 事件关联
        session.add(ProblemTimeline(
            problem_id=problem.id,
            action="event_added",
            actor="system",
            content=f"自动关联 {problem.event_count} 个异常事件",
            timestamp=problem.created_at + timedelta(seconds=10),
        ))

        if problem.assignee:
            session.add(ProblemTimeline(
                problem_id=problem.id,
                action="assigned",
                actor="system",
                content=f"分配给 {problem.assignee}",
                old_value="",
                new_value=problem.assignee,
                timestamp=problem.created_at + timedelta(minutes=random.randint(5, 30)),
            ))

        if problem.status in ("investigating", "resolved", "closed"):
            session.add(ProblemTimeline(
                problem_id=problem.id,
                action="status_changed",
                actor=problem.assignee or "system",
                content="开始调查，正在分析异常原因",
                old_value="open",
                new_value="investigating",
                timestamp=problem.created_at + timedelta(minutes=random.randint(10, 60)),
            ))

        if problem.status in ("resolved", "closed"):
            session.add(ProblemTimeline(
                problem_id=problem.id,
                action="commented",
                actor=problem.assignee or "system",
                content=problem.root_cause_summary or "问题已定位并修复",
                timestamp=problem.resolved_at - timedelta(minutes=random.randint(10, 30)) if problem.resolved_at else problem.created_at + timedelta(hours=2),
            ))

            session.add(ProblemTimeline(
                problem_id=problem.id,
                action="resolved",
                actor=problem.assignee or "system",
                content="问题已解决",
                old_value="investigating",
                new_value="resolved",
                timestamp=problem.resolved_at or problem.created_at + timedelta(hours=3),
            ))

        if problem.status == "closed":
            closed_at = (problem.resolved_at or problem.created_at) + timedelta(hours=random.randint(1, 24))
            session.add(ProblemTimeline(
                problem_id=problem.id,
                action="status_changed",
                actor=problem.assignee or "system",
                content="确认修复有效，关闭问题",
                old_value="resolved",
                new_value="closed",
                timestamp=closed_at,
            ))

    session.flush()
    logger.info("创建了问题时间线")


# =============================================
#  生成静默规则 SilenceRule
# =============================================


def _create_silence_rules(session: Session) -> None:
    """创建 3 个静默规则."""
    rules = [
        SilenceRule(
            id="SIL-001",
            creator="张三",
            source="manual",
            reason="ts-food-service 计划内维护，临时静默告警",
            note="预计维护窗口 2 小时",
            matchers=[
                {"key": "service", "op": "=", "value": "ts-food-service"},
                {"key": "severity", "op": "=~", "value": "warning|info"},
            ],
            starts_at=NOW - timedelta(hours=1),
            ends_at=NOW + timedelta(hours=1),
            status="active",
            match_count=3,
            created_at=NOW - timedelta(hours=2),
            updated_at=NOW - timedelta(hours=1),
        ),
        SilenceRule(
            id="SIL-002",
            creator="CI/CD",
            source="cicd",
            reason="ts-order-service 版本发布 v2.3.1",
            note="发布期间自动静默，发布完成后失效",
            matchers=[
                {"key": "service", "op": "=", "value": "ts-order-service"},
            ],
            starts_at=NOW + timedelta(hours=2),
            ends_at=NOW + timedelta(hours=4),
            status="pending",
            match_count=0,
            created_at=NOW - timedelta(hours=1),
            updated_at=NOW - timedelta(hours=1),
        ),
        SilenceRule(
            id="SIL-003",
            creator="李四",
            source="manual",
            reason="已知 ts-basic-service 重启问题，修复中不再告警",
            note=None,
            matchers=[
                {"key": "service", "op": "=", "value": "ts-basic-service"},
                {"key": "severity", "op": "=", "value": "warning"},
            ],
            starts_at=NOW - timedelta(hours=48),
            ends_at=NOW - timedelta(hours=24),
            status="expired",
            match_count=7,
            created_at=NOW - timedelta(hours=50),
            updated_at=NOW - timedelta(hours=24),
        ),
    ]

    for rule in rules:
        session.add(rule)

    session.flush()
    logger.info("创建了 %d 个静默规则", len(rules))


# =============================================
#  主函数
# =============================================


def seed() -> None:
    """执行种子数据填充."""
    logger.info("开始填充种子数据...")

    # 确保表存在
    create_tables()

    with Session(engine) as session:
        # 检查是否已有数据
        existing_alerts = session.exec(select(Alert).limit(1)).first()
        if existing_alerts:
            logger.info("数据库已有告警数据，清空后重新填充...")
            # 按外键依赖顺序删除
            session.exec(select(AlertTimeline)).all()  # noqa: just to load
            from sqlalchemy import delete
            session.execute(delete(AlertTimeline))
            session.execute(delete(AlertEvent))
            session.execute(delete(Alert))
            session.execute(delete(ProblemTimeline))
            session.execute(delete(ProblemEvent))
            session.execute(delete(Problem))
            session.execute(delete(SilenceRule))
            session.commit()
            logger.info("已清空所有数据")

        # 生成数据
        alerts = _create_alerts(session)
        _create_alert_events(session, alerts)
        _create_alert_timeline(session, alerts)

        problems = _create_problems(session)
        _create_problem_events(session, problems)
        _create_problem_timeline(session, problems)

        _create_silence_rules(session)

        session.commit()

    logger.info("种子数据填充完成!")
    logger.info("  - 告警: 25 条")
    logger.info("  - 问题: 12 条")
    logger.info("  - 静默规则: 3 条")


if __name__ == "__main__":
    seed()
