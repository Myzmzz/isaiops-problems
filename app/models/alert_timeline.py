"""AlertTimeline（告警时间线）数据模型.

记录告警工单的每个状态变更和操作事件，形成完整的处理时间线。
"""

from datetime import datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class AlertTimeline(SQLModel, table=True):
    """告警处理时间线表.

    action 类型:
    - created: 告警创建
    - escalated: 异常升级为告警
    - acknowledged: 人工确认
    - assigned: 分配给某人
    - commented: 添加备注
    - resolved: 解决
    - suppressed: 抑制
    """

    __tablename__ = "alert_timeline"
    __table_args__ = (
        Index("idx_alert_timeline_alert_id", "alert_id"),
        {"schema": "problems"},
    )

    id: int | None = Field(default=None, primary_key=True)
    alert_id: str = Field(max_length=20, foreign_key="problems.alerts.id")
    action: str = Field(max_length=20)
    actor: str | None = Field(default=None, max_length=100)  # system | 用户名
    detail: str | None = Field(default=None, max_length=1000)
    created_at: datetime = Field(default_factory=datetime.utcnow)
