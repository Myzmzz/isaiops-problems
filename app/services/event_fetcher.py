"""事件获取服务.

封装从 isaiops-be 拉取事件的逻辑，提供去重和缓存能力。
"""

import logging
from datetime import datetime

from app.clients.anomaly_client import AnomalyClient

logger = logging.getLogger(__name__)


class EventFetcher:
    """从 isaiops-be 获取异常事件，并跟踪已处理的事件 ID 避免重复聚合."""

    def __init__(self, client: AnomalyClient | None = None):
        self.client = client or AnomalyClient()
        self._processed_event_ids: set[str] = set()
        self._last_poll_time: datetime | None = None

    async def fetch_new_events(self) -> list[dict]:
        """拉取新的 escalated 事件（排除已处理的）.

        Returns:
            未处理过的新事件列表。
        """
        all_events = await self.client.fetch_escalated_events(since=self._last_poll_time)
        self._last_poll_time = datetime.utcnow()

        # 过滤已处理的事件
        new_events = []
        for event in all_events:
            event_id = event.get("id", "")
            if not event_id:
                continue
            if event_id not in self._processed_event_ids:
                new_events.append(event)
                self._processed_event_ids.add(event_id)

        if new_events:
            logger.info(
                "Found %d new events (filtered from %d total)",
                len(new_events),
                len(all_events),
            )

        # 清理过旧的已处理 ID（保留最近 10000 个）
        if len(self._processed_event_ids) > 10000:
            self._processed_event_ids = set(list(self._processed_event_ids)[-5000:])

        return new_events

    def mark_processed(self, event_id: str) -> None:
        """手动标记事件为已处理."""
        self._processed_event_ids.add(event_id)

    @property
    def processed_count(self) -> int:
        """已处理事件总数."""
        return len(self._processed_event_ids)
