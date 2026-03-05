"""SilenceRule（静默规则）数据模型.

静默规则在指定时间窗口内抑制匹配的告警，防止已知变更期间产生噪音。
"""

from datetime import datetime

from sqlalchemy import JSON, Index, String
from sqlmodel import Column, Field, SQLModel


class SilenceRule(SQLModel, table=True):
    """告警静默规则表.

    matchers 使用 Label Matcher 语法:
    [{"key": "service", "op": "=", "value": "billing-service"},
     {"key": "severity", "op": "=~", "value": "warning|info"}]
    """

    __tablename__ = "silence_rules"
    __table_args__ = (
        Index("idx_silence_rules_status", "status"),
        {"schema": "problems"},
    )

    id: str = Field(sa_column=Column(String, primary_key=True))
    creator: str = Field(max_length=100)
    source: str = Field(max_length=20, default="manual")  # manual | cicd | auto
    reason: str = Field(max_length=500)
    note: str | None = Field(default=None, max_length=1000)

    # 匹配条件
    matchers: list[dict] = Field(sa_column=Column(JSON, nullable=False, default=[]))

    # 时间窗口
    starts_at: datetime
    ends_at: datetime

    # 状态
    status: str = Field(max_length=20, default="active")  # active | pending | expired
    match_count: int = Field(default=0)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
