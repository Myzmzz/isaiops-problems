"""isaiops-be 异常事件 HTTP 客户端.

负责从 isaiops-be 拉取 escalated 异常事件，作为问题聚合的输入源。
"""

import logging
from datetime import datetime

import httpx

from app.config import ANOMALY_BE_URL, HTTP_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class AnomalyClient:
    """isaiops-be 异常检测服务的 HTTP 客户端."""

    def __init__(self, base_url: str = ANOMALY_BE_URL, timeout: int = HTTP_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get_rules(self) -> list[dict]:
        """获取所有已启用的检测规则列表.

        Returns:
            规则列表，每个规则包含 id, name, service, mode, severity 等字段。
        """
        url = f"{self.base_url}/api/anomaly/rules"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params={"enabled": "true"})
                resp.raise_for_status()
                data = resp.json()
                # isaiops-be 返回格式: {"code": 0, "data": {"items": [...]}}
                # 或者直接返回列表（旧格式兼容）
                if isinstance(data, dict):
                    if "data" in data and isinstance(data["data"], dict):
                        return data["data"].get("items", [])
                    if "data" in data and isinstance(data["data"], list):
                        return data["data"]
                if isinstance(data, list):
                    return data
                return []
        except Exception:
            logger.exception("Failed to fetch rules from isaiops-be")
            return []

    async def get_rule_events(
        self,
        rule_id: str,
        status: str = "escalated",
        limit: int = 50,
    ) -> list[dict]:
        """获取指定规则下的异常事件.

        Args:
            rule_id: 规则 ID。
            status: 事件状态过滤（默认 escalated）。
            limit: 返回数量上限。

        Returns:
            事件列表。
        """
        url = f"{self.base_url}/api/anomaly/rules/{rule_id}/events"
        params: dict = {"limit": limit}
        if status:
            params["status"] = status

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    if "data" in data and isinstance(data["data"], dict):
                        return data["data"].get("items", [])
                    if "data" in data and isinstance(data["data"], list):
                        return data["data"]
                if isinstance(data, list):
                    return data
                return []
        except Exception:
            logger.exception("Failed to fetch events for rule %s", rule_id)
            return []

    async def get_event_detail(self, event_id: str) -> dict | None:
        """获取单个事件详情.

        Args:
            event_id: 事件 ID。

        Returns:
            事件详情字典，或 None（如果获取失败）。
        """
        url = f"{self.base_url}/api/anomaly/events/{event_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
                return data if isinstance(data, dict) else None
        except Exception:
            logger.exception("Failed to fetch event detail %s", event_id)
            return None

    async def fetch_escalated_events(self, since: datetime | None = None) -> list[dict]:
        """拉取所有 escalated 事件.

        P0 阶段通过遍历所有规则获取 escalated 事件。
        后续 isaiops-be 新增批量查询接口后可优化为单次请求。

        Args:
            since: 只拉取此时间之后的事件（当前未使用，待 BE 支持）。

        Returns:
            所有 escalated 事件列表。
        """
        all_events: list[dict] = []

        # 获取所有规则
        rules = await self.get_rules()
        logger.info("Fetched %d rules from isaiops-be", len(rules))

        # 遍历每个规则，拉取 escalated 和 active 事件
        for rule in rules:
            rule_id = rule.get("id", "")
            if not rule_id:
                continue

            for status in ("escalated", "active"):
                events = await self.get_rule_events(rule_id, status=status, limit=20)
                for event in events:
                    # 补充规则信息到事件中
                    event.setdefault("rule_id", rule_id)
                    event.setdefault("service", rule.get("service", ""))
                    event.setdefault("severity", rule.get("severity", "warning"))
                    event.setdefault("mode", rule.get("mode", ""))
                    all_events.append(event)

        logger.info("Fetched %d escalated/active events in total", len(all_events))
        return all_events
