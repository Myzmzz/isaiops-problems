"""Problem（问题工单）数据模型.

一个 Problem 聚合了多个异常事件，比 Alert 更高层次的问题管理单元。
状态机: open -> investigating -> resolved -> closed
"""

from datetime import datetime

from sqlalchemy import JSON, Index, String, Text
from sqlmodel import Column, Field, SQLModel


class Problem(SQLModel, table=True):
    """问题工单表.

    每条记录代表一个聚合后的问题，关联到一个或多个异常事件。
    状态机: open → investigating → resolved → closed
    """

    __tablename__ = "problems"
    __table_args__ = (
        Index("idx_problems_status_severity", "status", "severity"),
        Index("idx_problems_service", "service"),
        Index("idx_problems_created_at", "created_at"),
        Index("idx_problems_assignee_status", "assignee", "status"),
        {"schema": "problems"},
    )

    id: str = Field(sa_column=Column(String, primary_key=True))
    title: str = Field(max_length=500)
    description: str | None = Field(sa_column=Column(Text, nullable=True))

    # 分类
    service: str = Field(max_length=200, default="")
    severity: str = Field(max_length=20)  # critical | warning | info

    # 状态机
    status: str = Field(max_length=20, default="open")  # open | investigating | resolved | closed
    assignee: str | None = Field(default=None, max_length=100)

    # 聚合信息
    event_count: int = Field(default=0)

    # 根因分析
    root_cause_summary: str | None = Field(sa_column=Column(Text, nullable=True))
    affected_services: list[str] | None = Field(
        sa_column=Column(JSON, nullable=True, default=None)
    )

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = Field(default=None)
