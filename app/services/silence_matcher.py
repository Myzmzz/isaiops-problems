"""静默规则匹配器.

检查告警是否匹配任何活跃的静默规则，匹配则跳过告警创建。
"""

import logging
import re
from datetime import datetime

from sqlmodel import Session, select

from app.models.silence_rule import SilenceRule

logger = logging.getLogger(__name__)


def is_silenced(
    session: Session,
    service: str,
    severity: str,
    metric: str = "",
    detection_mode: str = "",
) -> bool:
    """检查给定属性是否被当前活跃的静默规则匹配.

    Args:
        session: 数据库会话。
        service: 服务名。
        severity: 严重度。
        metric: 指标名（可选）。
        detection_mode: 检测模式（可选）。

    Returns:
        如果匹配到活跃静默规则则返回 True。
    """
    now = datetime.utcnow()

    # 查询所有活跃的静默规则
    stmt = select(SilenceRule).where(
        SilenceRule.status == "active",
        SilenceRule.starts_at <= now,
        SilenceRule.ends_at >= now,
    )
    rules = session.exec(stmt).all()

    attrs = {
        "service": service,
        "severity": severity,
        "metric": metric,
        "detection_mode": detection_mode,
    }

    for rule in rules:
        if _matches_all(rule.matchers, attrs):
            # 更新匹配计数
            rule.match_count += 1
            rule.updated_at = now
            session.add(rule)
            session.commit()
            logger.info("Alert silenced by rule %s (service=%s)", rule.id, service)
            return True

    return False


def _matches_all(matchers: list[dict], attrs: dict[str, str]) -> bool:
    """检查所有 matcher 是否都匹配.

    Args:
        matchers: 匹配条件列表。
        attrs: 告警属性字典。

    Returns:
        所有 matcher 都匹配返回 True。
    """
    if not matchers:
        return False

    for matcher in matchers:
        key = matcher.get("key", "")
        op = matcher.get("op", "=")
        value = matcher.get("value", "")

        actual = attrs.get(key, "")

        if not _match_single(actual, op, value):
            return False

    return True


def _match_single(actual: str, op: str, expected: str) -> bool:
    """执行单个匹配操作.

    支持的操作符:
    - =  精确匹配
    - != 不等于
    - =~ 正则匹配
    - !~ 正则不匹配
    """
    if op == "=":
        return actual == expected
    elif op == "!=":
        return actual != expected
    elif op == "=~":
        try:
            return bool(re.search(expected, actual))
        except re.error:
            logger.warning("Invalid regex in silence matcher: %s", expected)
            return False
    elif op == "!~":
        try:
            return not bool(re.search(expected, actual))
        except re.error:
            logger.warning("Invalid regex in silence matcher: %s", expected)
            return True
    else:
        logger.warning("Unknown matcher operator: %s", op)
        return False


def update_expired_rules(session: Session) -> int:
    """将已过期的静默规则标记为 expired.

    Returns:
        更新的规则数量。
    """
    now = datetime.utcnow()
    stmt = select(SilenceRule).where(
        SilenceRule.status.in_(["active", "pending"]),
        SilenceRule.ends_at < now,
    )
    expired_rules = session.exec(stmt).all()

    count = 0
    for rule in expired_rules:
        rule.status = "expired"
        rule.updated_at = now
        session.add(rule)
        count += 1

    if count > 0:
        session.commit()
        logger.info("Marked %d silence rules as expired", count)

    return count
