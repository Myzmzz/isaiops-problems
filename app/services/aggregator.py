"""告警聚合引擎.

核心聚合逻辑: 将 isaiops-be 的异常事件聚合为告警工单。
使用 APScheduler 定时轮询，按服务 + 时间窗口进行聚合。
"""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, select

from app.config import AGGREGATION_WINDOW_MINUTES, POLL_INTERVAL_SECONDS
from app.database import engine
from app.models.alert import Alert
from app.models.alert_event import AlertEvent
from app.models.alert_timeline import AlertTimeline
from app.services.event_fetcher import EventFetcher
from app.services.problem_aggregator import (
    aggregate_event_to_problem,
    init_counter as init_problem_counter,
)
from app.services.silence_matcher import is_silenced, update_expired_rules

logger = logging.getLogger(__name__)

# 告警 ID 计数器
_alert_counter: int = 0


def _next_alert_id() -> str:
    """生成下一个告警 ID (ALT-2026-XXXX)."""
    global _alert_counter
    _alert_counter += 1
    now = datetime.utcnow()
    return f"ALT-{now.year}-{_alert_counter:04d}"


def _init_alert_counter(session: Session) -> None:
    """从数据库初始化告警 ID 计数器."""
    global _alert_counter
    stmt = select(Alert).order_by(Alert.created_at.desc()).limit(1)  # type: ignore[arg-type]
    last_alert = session.exec(stmt).first()
    if last_alert:
        # 从最后一个告警 ID 中提取序号
        try:
            parts = last_alert.id.split("-")
            _alert_counter = int(parts[-1])
        except (IndexError, ValueError):
            _alert_counter = 0
    logger.info("Alert counter initialized to %d", _alert_counter)


class AggregationEngine:
    """告警聚合引擎.

    每 POLL_INTERVAL_SECONDS 秒:
    1. 从 isaiops-be 拉取新的 escalated 事件
    2. 检查静默规则
    3. 按服务 + 时间窗口聚合到已有或新建的 Alert
    4. 记录时间线
    5. 清理过期静默规则
    """

    def __init__(self) -> None:
        self.fetcher = EventFetcher()
        self.scheduler = AsyncIOScheduler()
        self._running = False

    def start(self) -> None:
        """启动聚合调度器."""
        if self._running:
            return

        # 初始化 ID 计数器
        with Session(engine) as session:
            _init_alert_counter(session)
            init_problem_counter(session)

        self.scheduler.add_job(
            self._poll_and_aggregate,
            "interval",
            seconds=POLL_INTERVAL_SECONDS,
            id="aggregation_job",
            replace_existing=True,
        )
        self.scheduler.start()
        self._running = True
        logger.info(
            "Aggregation engine started (interval=%ds, window=%dmin)",
            POLL_INTERVAL_SECONDS,
            AGGREGATION_WINDOW_MINUTES,
        )

    def stop(self) -> None:
        """停止聚合调度器."""
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("Aggregation engine stopped")

    async def _poll_and_aggregate(self) -> None:
        """一次轮询-聚合周期."""
        try:
            # 拉取新事件
            new_events = await self.fetcher.fetch_new_events()
            if not new_events:
                return

            logger.info("Processing %d new events", len(new_events))

            with Session(engine) as session:
                # 更新过期静默规则
                update_expired_rules(session)

                for event in new_events:
                    self._process_event(session, event)
                    # 同时聚合到 Problem 工单
                    aggregate_event_to_problem(session, event)

                session.commit()

        except Exception:
            logger.exception("Error in aggregation cycle")

    def _process_event(self, session: Session, event: dict) -> None:
        """处理单个异常事件，聚合到告警工单.

        Args:
            session: 数据库会话。
            event: 来自 isaiops-be 的事件字典。
        """
        service = event.get("service", "unknown")
        severity = event.get("severity", "warning")
        metric = event.get("metric", "")
        mode = event.get("mode", "")
        event_id = event.get("id", "")

        # 检查静默规则
        if is_silenced(session, service, severity, metric, mode):
            return

        # 查找同服务的活跃 Alert（时间窗口内）
        window_start = datetime.utcnow() - timedelta(minutes=AGGREGATION_WINDOW_MINUTES)
        stmt = select(Alert).where(
            Alert.service == service,
            Alert.status.in_(["triggered", "acknowledged"]),
            Alert.last_triggered_at >= window_start,
        )
        existing_alert = session.exec(stmt).first()

        # 提取事件分数
        event_score = self._extract_score(event)

        if existing_alert:
            # 追加到已有 Alert
            self._append_to_alert(session, existing_alert, event, event_score)
        else:
            # 创建新 Alert
            self._create_alert(session, event, event_score)

    def _create_alert(self, session: Session, event: dict, score: int) -> Alert:
        """创建新的告警工单.

        Args:
            session: 数据库会话。
            event: 事件字典。
            score: 事件显著性评分。

        Returns:
            新创建的 Alert 对象。
        """
        now = datetime.utcnow()
        alert_id = _next_alert_id()
        service = event.get("service", "unknown")
        severity = event.get("severity", "warning")
        metric = event.get("metric", "")
        rule_name = event.get("rule_name", event.get("ruleName", ""))
        mode = event.get("mode", "")

        title = f"{service} — {rule_name or metric}"

        alert = Alert(
            id=alert_id,
            title=title,
            service=service,
            severity=severity,
            score=score,
            status="triggered",
            anomaly_count=1,
            first_triggered_at=now,
            last_triggered_at=now,
            source_rule_id=event.get("rule_id", ""),
            detection_mode=mode,
            created_at=now,
            updated_at=now,
        )
        session.add(alert)

        # 关联事件
        self._link_event(session, alert_id, event, score)

        # 创建时间线
        timeline = AlertTimeline(
            alert_id=alert_id,
            action="created",
            actor="system",
            detail=f"告警创建: {title} (score={score})",
            created_at=now,
        )
        session.add(timeline)

        logger.info("Created alert %s for service %s (score=%d)", alert_id, service, score)
        return alert

    def _append_to_alert(
        self, session: Session, alert: Alert, event: dict, score: int
    ) -> None:
        """将事件追加到已有告警工单.

        Args:
            session: 数据库会话。
            alert: 已有的 Alert 对象。
            event: 事件字典。
            score: 事件显著性评分。
        """
        now = datetime.utcnow()
        event_id = event.get("id", "")

        # 检查是否已经关联过此事件
        existing = session.exec(
            select(AlertEvent).where(
                AlertEvent.alert_id == alert.id,
                AlertEvent.event_id == event_id,
            )
        ).first()
        if existing:
            return

        # 更新 Alert 聚合信息
        alert.anomaly_count += 1
        alert.last_triggered_at = now
        alert.updated_at = now

        # 严重度提升（critical > warning > info）
        severity_order = {"critical": 3, "warning": 2, "info": 1}
        event_severity = event.get("severity", "warning")
        if severity_order.get(event_severity, 0) > severity_order.get(alert.severity, 0):
            alert.severity = event_severity

        # 分数提升
        if score > alert.score:
            alert.score = score

        session.add(alert)

        # 关联事件
        self._link_event(session, alert.id, event, score)

        # 计算持续时长
        delta = now - alert.first_triggered_at
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        if hours > 0:
            alert.duration = f"{hours}h {minutes}min"
        else:
            alert.duration = f"{minutes}min"

        logger.info(
            "Appended event to alert %s (count=%d, score=%d)",
            alert.id,
            alert.anomaly_count,
            alert.score,
        )

    def _link_event(
        self, session: Session, alert_id: str, event: dict, score: int
    ) -> None:
        """创建告警-事件关联记录.

        Args:
            session: 数据库会话。
            alert_id: 告警 ID。
            event: 事件字典。
            score: 事件评分。
        """
        # 解析检测时间
        detected_at_str = event.get("detected_at", event.get("detectedAt", ""))
        try:
            detected_at = datetime.fromisoformat(detected_at_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            detected_at = datetime.utcnow()

        alert_event = AlertEvent(
            alert_id=alert_id,
            event_id=event.get("id", ""),
            event_severity=event.get("severity", "warning"),
            event_score=score,
            event_detected_at=detected_at,
            event_metric=event.get("metric", ""),
            event_rule_id=event.get("rule_id", ""),
            event_current_value=float(event.get("current_value", event.get("currentValue", 0))),
            event_expected_value=float(
                event.get("expected_value", event.get("expectedValue", 0))
            ),
            event_deviation_percent=float(
                event.get("deviation_percent", event.get("deviationPercent", 0))
            ),
            event_status=event.get("status", "escalated"),
            added_at=datetime.utcnow(),
        )
        session.add(alert_event)

    def _extract_score(self, event: dict) -> int:
        """从事件中提取显著性评分.

        isaiops-be 的评分逻辑:
        - Base: 40
        - Severity bonus: critical=30, warning=20, info=10
        - Deviation bonus: >5σ=+20, >4σ=+15, >3σ=+10

        如果事件中已有 score 字段则直接使用，否则根据 severity 估算。
        """
        # 直接从事件获取
        if "score" in event:
            try:
                return int(event["score"])
            except (ValueError, TypeError):
                pass

        # 根据 severity 估算
        severity = event.get("severity", "warning")
        base_scores = {"critical": 85, "warning": 65, "info": 45}
        return base_scores.get(severity, 50)
