"""ProblemTimeline（问题时间线）数据模型.

记录问题工单的每个状态变更和操作事件，形成完整的处理时间线。
"""

from datetime import datetime

from sqlalchemy import Index, Text
from sqlmodel import Column, Field, SQLModel


class ProblemTimeline(SQLModel, table=True):
    """问题处理时间线表.

    action 类型:
    - created: 问题创建
    - status_changed: 状态变更
    - assigned: 分配给某人
    - commented: 添加备注
    - event_added: 新事件关联
    - resolved: 解决
    - closed: 关闭
    """

    __tablename__ = "problem_timeline"
    __table_args__ = (
        Index("idx_problem_timeline_problem_id", "problem_id"),
        {"schema": "problems"},
    )

    id: int | None = Field(default=None, primary_key=True)
    problem_id: str = Field(max_length=50, foreign_key="problems.problems.id")
    action: str = Field(max_length=30)
    actor: str | None = Field(default=None, max_length=100)
    content: str | None = Field(sa_column=Column(Text, nullable=True))
    old_value: str | None = Field(default=None, max_length=200)
    new_value: str | None = Field(default=None, max_length=200)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
