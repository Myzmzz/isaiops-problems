"""Shared test fixtures for isaiops-problems.

Provides:
  - SQLite in-memory database for testing (no PostgreSQL needed)
  - FastAPI dependency override for get_session
  - Common contract field sets
"""

import pytest
from sqlalchemy import event as sa_event
from sqlmodel import Session, SQLModel, create_engine

# ------------------------------------------------------------------
#  SQLite test database setup
# ------------------------------------------------------------------

# Use SQLite in-memory. ATTACH a "problems" database to simulate
# the PostgreSQL "problems" schema used by the models.
TEST_DATABASE_URL = "sqlite://"

_test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@sa_event.listens_for(_test_engine, "connect")
def _on_connect(dbapi_conn, connection_record):
    """Attach a 'problems' schema alias so that SQLite can resolve
    table references like problems.problems, problems.problem_events, etc."""
    dbapi_conn.execute("ATTACH DATABASE ':memory:' AS problems")


@pytest.fixture(autouse=True)
def setup_test_db():
    """Create all tables before each test, drop after."""
    # Import models so SQLModel.metadata is populated
    import app.models.problem  # noqa: F401
    import app.models.problem_event  # noqa: F401
    import app.models.problem_timeline  # noqa: F401

    SQLModel.metadata.create_all(_test_engine)
    yield
    SQLModel.metadata.drop_all(_test_engine)


def _get_test_session():
    """Override for app.database.get_session."""
    with Session(_test_engine) as session:
        yield session


@pytest.fixture(autouse=True)
def override_db_dependency():
    """Override the FastAPI get_session dependency with test session."""
    from app.database import get_session
    from app.main import app

    app.dependency_overrides[get_session] = _get_test_session
    yield
    app.dependency_overrides.clear()


# ------------------------------------------------------------------
#  Contract field sets (from problems.yaml & common.yaml)
# ------------------------------------------------------------------

API_RESPONSE_FIELDS = {"code", "message", "data"}
PAGINATED_DATA_FIELDS = {"items", "total", "page", "page_size"}

PROBLEM_SUMMARY_REQUIRED = {"id", "title", "severity", "status"}
PROBLEM_DETAIL_REQUIRED = {
    "id",
    "title",
    "severity",
    "status",
    "events",
    "timeline",
}
PROBLEM_OVERVIEW_KEYS = {
    "total_problems",
    "open_problems",
    "investigating_problems",
    "resolved_problems",
}
TIMELINE_ENTRY_REQUIRED = {"action", "timestamp"}
PROBLEM_EVENT_REQUIRED = {"event_id", "metric", "severity", "detected_at"}
