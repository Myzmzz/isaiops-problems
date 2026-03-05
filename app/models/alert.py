"""Alert（告警工单）数据模型.

一个 Alert 聚合了一个或多个来自 isaiops-be 的异常事件。
"""

from datetime import datetime

from sqlalchemy import JSON, Index, String
from sqlmodel import Column, Field, SQLModel


class Alert(SQLModel, table=True):
    """告警工单表.

    每条记录代表一个聚合后的问题工单，关联到一个或多个异常事件。
    状态机: triggered → acknowledged → resolved
                ↓              ↓
            suppressed     (re-trigger)
    """

    __tablename__ = "alerts"
    __table_args__ = (
        Index("idx_alerts_status_severity", "status", "severity"),
        Index("idx_alerts_service", "service"),
        Index("idx_alerts_created_at", "created_at"),
        Index("idx_alerts_assignee_status", "assignee", "status"),
        {"schema": "problems"},
    )

    id: str = Field(sa_column=Column(String, primary_key=True))
    title: str = Field(max_length=500)
    description: str | None = Field(default=None, max_length=2000)

    # 分类
    service: str = Field(max_length=100)
    severity: str = Field(max_length=20)  # critical | warning | info
    score: int = Field(default=0)  # 显著性评分 0-100

    # 状态机
    status: str = Field(max_length=20)  # triggered | acknowledged | resolved | suppressed
    assignee: str | None = Field(default=None, max_length=100)
    acknowledged_at: datetime | None = Field(default=None)
    resolved_at: datetime | None = Field(default=None)

    # 聚合信息
    anomaly_count: int = Field(default=1)
    first_triggered_at: datetime
    last_triggered_at: datetime
    duration: str | None = Field(default=None, max_length=50)

    # 元数据
    source_rule_id: str | None = Field(default=None, max_length=20)
    detection_mode: str | None = Field(default=None, max_length=20)
    tags: dict | None = Field(sa_column=Column(JSON, nullable=True, default=None))

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
