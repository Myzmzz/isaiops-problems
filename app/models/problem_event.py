"""ProblemEvent（问题-事件关联）数据模型.

记录问题工单与 isaiops-be 异常事件之间的关联关系。
"""

from datetime import datetime

from sqlalchemy import Index, String
from sqlmodel import Column, Field, SQLModel


class ProblemEvent(SQLModel, table=True):
    """问题与异常事件的关联表.

    event_id 引用 isaiops-be 的 Event.id（跨服务外键，不做物理约束）。
    """

    __tablename__ = "problem_events"
    __table_args__ = (
        Index("idx_problem_events_problem_id", "problem_id"),
        Index("idx_problem_events_event_id", "event_id"),
        {"schema": "problems"},
    )

    id: int | None = Field(default=None, primary_key=True)
    problem_id: str = Field(max_length=50, foreign_key="problems.problems.id")
    event_id: str = Field(sa_column=Column(String(50)))

    # 事件快照信息
    event_service: str = Field(max_length=200, default="")
    event_severity: str = Field(max_length=20)
    event_metric: str = Field(max_length=500, default="")
    event_rule_id: str = Field(max_length=50, default="")
    event_value: float = Field(default=0.0)
    event_threshold: float | None = Field(default=None)
    event_detected_at: datetime | None = Field(default=None)
    event_resolved_at: datetime | None = Field(default=None)
    event_status: str = Field(max_length=20, default="active")

    added_at: datetime = Field(default_factory=datetime.utcnow)
