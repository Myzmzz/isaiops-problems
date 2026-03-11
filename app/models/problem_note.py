"""ProblemNote（问题备注）数据模型.

独立的备注表，支持不同类型的备注（观察、疑似根因、操作记录等）。
与 ProblemTimeline 不同，Note 是结构化的长文本备注，而 Timeline 是操作记录。
"""

from datetime import datetime

from sqlalchemy import Index, Text
from sqlmodel import Column, Field, SQLModel


class ProblemNote(SQLModel, table=True):
    """问题备注表.

    type 类型:
    - observation: 观察记录
    - suspected_root_cause: 疑似根因
    - action: 操作/处置记录
    - general: 通用备注
    """

    __tablename__ = "problem_notes"
    __table_args__ = (
        Index("idx_problem_notes_problem_id", "problem_id"),
        {"schema": "problems"},
    )

    id: int | None = Field(default=None, primary_key=True)
    problem_id: str = Field(max_length=50, foreign_key="problems.problems.id")
    author: str = Field(max_length=100, default="user")
    type: str = Field(max_length=30, default="general")  # observation | suspected_root_cause | action | general
    content: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)
