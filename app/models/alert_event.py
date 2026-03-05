"""AlertEvent（告警-事件关联）数据模型.

记录告警工单与 isaiops-be 异常事件之间的多对多关系。
"""

from datetime import datetime

from sqlalchemy import Index, String
from sqlmodel import Column, Field, SQLModel


class AlertEvent(SQLModel, table=True):
    """告警与异常事件的关联表.

    每条记录表示一个异常事件被聚合到了某个告警工单中。
    event_id 引用 isaiops-be 的 Event.id（跨服务外键，不做物理约束）。
    """

    __tablename__ = "alert_events"
    __table_args__ = (
        Index("idx_alert_events_alert_id", "alert_id"),
        Index("idx_alert_events_event_id", "event_id"),
        {"schema": "problems"},
    )

    id: int | None = Field(default=None, primary_key=True)
    alert_id: str = Field(max_length=20, foreign_key="problems.alerts.id")
    event_id: str = Field(sa_column=Column(String(50)))  # isaiops-be Event ID

    # 事件快照信息（聚合时缓存，避免跨服务查询）
    event_severity: str = Field(max_length=20)
    event_score: int = Field(default=0)
    event_detected_at: datetime
    event_metric: str = Field(max_length=500)
    event_rule_id: str = Field(max_length=20, default="")
    event_current_value: float = Field(default=0.0)
    event_expected_value: float = Field(default=0.0)
    event_deviation_percent: float = Field(default=0.0)
    event_status: str = Field(max_length=20, default="escalated")

    added_at: datetime = Field(default_factory=datetime.utcnow)
