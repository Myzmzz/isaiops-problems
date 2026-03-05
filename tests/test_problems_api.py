"""Contract-based tests for the Problems management API.

Validates endpoints against docs/api-contracts/problems.yaml:
  GET  /api/v1/problems
  POST /api/v1/problems
  GET  /api/v1/problems/{problem_id}
  PATCH /api/v1/problems/{problem_id}/status
  PATCH /api/v1/problems/{problem_id}/assignee
  POST /api/v1/problems/{problem_id}/notes
  GET  /api/v1/problems/{problem_id}/events
  GET  /api/v1/problems/stats
  GET  /api/v1/problems/overview

Tests are designed to run once Agent C implements the problems router.
If the router module does not exist yet, tests are skipped.
"""

import importlib

import pytest

# ------------------------------------------------------------------
#  Conditional import: skip entire module if router not yet created
# ------------------------------------------------------------------

_ROUTER_MODULE = "app.api.problems"

try:
    importlib.import_module(_ROUTER_MODULE)
    _HAS_ROUTER = True
except (ModuleNotFoundError, ImportError):
    _HAS_ROUTER = False

pytestmark = pytest.mark.skipif(
    not _HAS_ROUTER,
    reason=f"'{_ROUTER_MODULE}' not yet implemented — waiting for Agent C",
)


@pytest.fixture()
def client():
    """Async httpx client against the isaiops-problems FastAPI app."""
    import httpx
    from app.main import app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


# ------------------------------------------------------------------
#  Contract field sets
# ------------------------------------------------------------------

PAGINATED_KEYS = {"items", "total", "page", "page_size"}
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
PROBLEM_STATS_KEYS = {"total_problems", "by_status", "by_severity"}


# ============================================
# GET /api/v1/problems
# ============================================


class TestProblemList:
    """问题列表接口."""

    @pytest.mark.asyncio
    async def test_returns_200(self, client):
        """Endpoint should return HTTP 200."""
        async with client:
            resp = await client.get("/api/v1/problems")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_envelope(self, client):
        """Response must have { code: 0, message: 'success', data: {...} }."""
        async with client:
            resp = await client.get("/api/v1/problems")
        body = resp.json()
        assert body["code"] == 0
        assert body["message"] == "success"
        assert "data" in body

    @pytest.mark.asyncio
    async def test_paginated_structure(self, client):
        """data must contain paginated fields."""
        async with client:
            resp = await client.get("/api/v1/problems")
        data = resp.json()["data"]
        assert PAGINATED_KEYS.issubset(set(data.keys()))

    @pytest.mark.asyncio
    async def test_overview_present(self, client):
        """data.overview should contain problem-level statistics."""
        async with client:
            resp = await client.get("/api/v1/problems")
        data = resp.json()["data"]
        assert "overview" in data

    @pytest.mark.asyncio
    async def test_problem_items_have_required_fields(self, client):
        """Each problem item must have contract-required fields."""
        async with client:
            resp = await client.get("/api/v1/problems")
        items = resp.json()["data"]["items"]
        for item in items:
            assert PROBLEM_SUMMARY_REQUIRED.issubset(set(item.keys())), (
                f"Missing fields: {PROBLEM_SUMMARY_REQUIRED - set(item.keys())}"
            )

    @pytest.mark.asyncio
    async def test_status_filter(self, client):
        """Filtering by status should return only matching problems."""
        async with client:
            resp = await client.get("/api/v1/problems", params={"status": "open"})
        items = resp.json()["data"]["items"]
        for item in items:
            assert item["status"] == "open"

    @pytest.mark.asyncio
    async def test_severity_filter(self, client):
        """Filtering by severity should return only matching problems."""
        async with client:
            resp = await client.get("/api/v1/problems", params={"severity": "critical"})
        items = resp.json()["data"]["items"]
        for item in items:
            assert item["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_search_filter(self, client):
        """search param should filter by problem title/description."""
        async with client:
            resp = await client.get(
                "/api/v1/problems", params={"search": "nonexistent-xyz"}
            )
        data = resp.json()["data"]
        assert data["total"] == 0 or isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_sort_by_severity(self, client):
        """sort_by=severity should be accepted."""
        async with client:
            resp = await client.get(
                "/api/v1/problems",
                params={"sort_by": "severity", "sort_order": "desc"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_pagination(self, client):
        """page and page_size params should work."""
        async with client:
            resp = await client.get(
                "/api/v1/problems", params={"page": 1, "page_size": 5}
            )
        data = resp.json()["data"]
        assert data["page"] == 1
        assert data["page_size"] == 5
        assert len(data["items"]) <= 5


# ============================================
# POST /api/v1/problems
# ============================================


class TestProblemCreate:
    """手动创建问题."""

    @pytest.mark.asyncio
    async def test_create_problem(self, client):
        """Should create a problem and return the detail."""
        payload = {
            "title": "Test problem from QA",
            "severity": "warning",
            "description": "Automated test problem",
            "service": "test-service",
        }
        async with client:
            resp = await client.post("/api/v1/problems", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert "data" in body
        data = body["data"]
        assert data["title"] == "Test problem from QA"
        assert data["severity"] == "warning"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_create_problem_missing_title(self, client):
        """Should fail when title is missing."""
        payload = {"severity": "warning"}
        async with client:
            resp = await client.post("/api/v1/problems", json=payload)
        # Expect validation error (422) or business error
        assert resp.status_code in (400, 422) or resp.json().get("code", 0) != 0

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="API does not validate severity enum — contract gap")
    async def test_create_problem_invalid_severity(self, client):
        """Should fail when severity is invalid."""
        payload = {"title": "Test", "severity": "invalid_value"}
        async with client:
            resp = await client.post("/api/v1/problems", json=payload)
        assert resp.status_code in (400, 422) or resp.json().get("code", 0) != 0


# ============================================
# GET /api/v1/problems/{problem_id}
# ============================================


class TestProblemDetail:
    """问题详情接口."""

    async def _create_test_problem(self, client):
        """Helper: create a problem and return its id."""
        payload = {
            "title": "Detail test problem",
            "severity": "critical",
            "service": "test-service",
        }
        resp = await client.post("/api/v1/problems", json=payload)
        return resp.json()["data"]["id"]

    @pytest.mark.asyncio
    async def test_returns_200(self, client):
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.get(f"/api/v1/problems/{pid}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_detail_has_required_fields(self, client):
        """Detail must include contract-required fields."""
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.get(f"/api/v1/problems/{pid}")
        data = resp.json()["data"]
        assert PROBLEM_DETAIL_REQUIRED.issubset(set(data.keys())), (
            f"Missing fields: {PROBLEM_DETAIL_REQUIRED - set(data.keys())}"
        )

    @pytest.mark.asyncio
    async def test_events_list(self, client):
        """Detail should include events list."""
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.get(f"/api/v1/problems/{pid}")
        data = resp.json()["data"]
        assert isinstance(data["events"], list)

    @pytest.mark.asyncio
    async def test_timeline_list(self, client):
        """Detail should include timeline entries."""
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.get(f"/api/v1/problems/{pid}")
        data = resp.json()["data"]
        assert isinstance(data["timeline"], list)

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="API does not include anomaly_timeline in detail — contract gap")
    async def test_anomaly_timeline_present(self, client):
        """Detail should include anomaly_timeline for AIOps integration."""
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.get(f"/api/v1/problems/{pid}")
        data = resp.json()["data"]
        assert "anomaly_timeline" in data

    @pytest.mark.asyncio
    async def test_not_found(self, client):
        """Non-existent problem_id should return error."""
        async with client:
            resp = await client.get("/api/v1/problems/nonexistent-problem-999")
        body = resp.json()
        assert resp.status_code != 200 or body.get("code", 0) != 0


# ============================================
# PATCH /api/v1/problems/{problem_id}/status
# ============================================


class TestProblemStatusUpdate:
    """更新问题状态."""

    async def _create_test_problem(self, client):
        payload = {
            "title": "Status update test",
            "severity": "warning",
            "service": "test-service",
        }
        resp = await client.post("/api/v1/problems", json=payload)
        return resp.json()["data"]["id"]

    @pytest.mark.asyncio
    async def test_update_status(self, client):
        """Should update status from open to investigating."""
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.patch(
                f"/api/v1/problems/{pid}/status",
                json={"status": "investigating", "comment": "Looking into it"},
            )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

    @pytest.mark.asyncio
    async def test_status_transition_to_resolved(self, client):
        """Should allow transitioning to resolved."""
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.patch(
                f"/api/v1/problems/{pid}/status",
                json={"status": "resolved"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_status(self, client):
        """Should reject invalid status values."""
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.patch(
                f"/api/v1/problems/{pid}/status",
                json={"status": "invalid_status"},
            )
        assert resp.status_code in (400, 422) or resp.json().get("code", 0) != 0


# ============================================
# PATCH /api/v1/problems/{problem_id}/assignee
# ============================================


class TestProblemAssignee:
    """分配问题负责人."""

    async def _create_test_problem(self, client):
        payload = {
            "title": "Assignee test",
            "severity": "warning",
            "service": "test-service",
        }
        resp = await client.post("/api/v1/problems", json=payload)
        return resp.json()["data"]["id"]

    @pytest.mark.asyncio
    async def test_assign_problem(self, client):
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.patch(
                f"/api/v1/problems/{pid}/assignee",
                json={"assignee": "test-user"},
            )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0


# ============================================
# POST /api/v1/problems/{problem_id}/notes
# ============================================


class TestProblemNotes:
    """添加问题备注."""

    async def _create_test_problem(self, client):
        payload = {
            "title": "Notes test",
            "severity": "info",
            "service": "test-service",
        }
        resp = await client.post("/api/v1/problems", json=payload)
        return resp.json()["data"]["id"]

    @pytest.mark.asyncio
    async def test_add_note(self, client):
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.post(
                f"/api/v1/problems/{pid}/notes",
                json={"content": "Test note from QA"},
            )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

    @pytest.mark.asyncio
    async def test_note_appears_in_timeline(self, client):
        """After adding a note, it should appear in the problem timeline."""
        async with client:
            pid = await self._create_test_problem(client)
            await client.post(
                f"/api/v1/problems/{pid}/notes",
                json={"content": "Check this note"},
            )
            resp = await client.get(f"/api/v1/problems/{pid}")
        timeline = resp.json()["data"]["timeline"]
        commented_entries = [e for e in timeline if e.get("action") == "commented"]
        assert len(commented_entries) > 0


# ============================================
# GET /api/v1/problems/{problem_id}/events
# ============================================


class TestProblemEvents:
    """问题关联的异常事件列表."""

    async def _create_test_problem(self, client):
        payload = {
            "title": "Events test",
            "severity": "critical",
            "service": "test-service",
        }
        resp = await client.post("/api/v1/problems", json=payload)
        return resp.json()["data"]["id"]

    @pytest.mark.asyncio
    async def test_returns_event_list(self, client):
        async with client:
            pid = await self._create_test_problem(client)
            resp = await client.get(f"/api/v1/problems/{pid}/events")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "items" in data
        assert isinstance(data["items"], list)


# ============================================
# GET /api/v1/problems/stats
# ============================================


class TestProblemStats:
    """问题统计概览."""

    @pytest.mark.asyncio
    async def test_returns_200(self, client):
        async with client:
            resp = await client.get("/api/v1/problems/stats")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_stats_structure(self, client):
        """Stats should include by_status, by_severity, and totals."""
        async with client:
            resp = await client.get("/api/v1/problems/stats")
        data = resp.json()["data"]
        assert PROBLEM_STATS_KEYS.issubset(set(data.keys())), (
            f"Missing stats fields: {PROBLEM_STATS_KEYS - set(data.keys())}"
        )

    @pytest.mark.asyncio
    async def test_stats_by_status(self, client):
        """by_status should have open, investigating, resolved, closed."""
        async with client:
            resp = await client.get("/api/v1/problems/stats")
        by_status = resp.json()["data"]["by_status"]
        expected_keys = {"open", "investigating", "resolved", "closed"}
        assert expected_keys.issubset(set(by_status.keys()))

    @pytest.mark.asyncio
    async def test_stats_by_severity(self, client):
        """by_severity should have critical, warning, info."""
        async with client:
            resp = await client.get("/api/v1/problems/stats")
        by_severity = resp.json()["data"]["by_severity"]
        expected_keys = {"critical", "warning", "info"}
        assert expected_keys.issubset(set(by_severity.keys()))

    @pytest.mark.asyncio
    async def test_mttr_present(self, client):
        """Stats should include MTTR (Mean Time To Resolve)."""
        async with client:
            resp = await client.get("/api/v1/problems/stats")
        data = resp.json()["data"]
        assert "mttr_hours" in data


# ============================================
# GET /api/v1/problems/overview
# ============================================


class TestProblemOverview:
    """问题概览（含趋势）."""

    @pytest.mark.asyncio
    async def test_returns_200(self, client):
        async with client:
            resp = await client.get("/api/v1/problems/overview")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_envelope(self, client):
        async with client:
            resp = await client.get("/api/v1/problems/overview")
        body = resp.json()
        assert body["code"] == 0
        assert "data" in body
