"""SQLModel engine 和 session 管理."""

from collections.abc import Generator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import DATABASE_URL, DB_SCHEMA

engine = create_engine(DATABASE_URL, echo=False)


def create_tables() -> None:
    """创建 schema（如果不存在）并建表."""
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
        conn.commit()
    SQLModel.metadata.create_all(engine)


def drop_all() -> None:
    """删除所有表（开发/测试用）."""
    SQLModel.metadata.drop_all(engine)


def get_session() -> Generator[Session, None, None]:
    """获取数据库 Session（用作 FastAPI Depends）."""
    with Session(engine) as session:
        yield session
