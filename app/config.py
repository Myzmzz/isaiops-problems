"""应用配置项.

所有配置优先从环境变量读取，便于 K8s 部署时通过 ConfigMap/Secret 覆盖。
"""

import json
import os

# =============================================
#  数据库
# =============================================

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://root:Zmzzmz010627!@localhost:5432/isaiops",
)

DB_SCHEMA: str = os.getenv("DB_SCHEMA", "problems")

# =============================================
#  上游服务 (isaiops-be)
# =============================================

# isaiops-be 的基础 URL，用于拉取 escalated 事件
ANOMALY_BE_URL: str = os.getenv(
    "ANOMALY_BE_URL",
    "http://isaiops-be.aiops.svc.cluster.local:8000",
)

# 事件轮询间隔（秒）
POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

# HTTP 请求超时（秒）
HTTP_TIMEOUT_SECONDS: int = int(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))

# =============================================
#  聚合参数
# =============================================

# 同服务事件聚合时间窗口（分钟）
AGGREGATION_WINDOW_MINUTES: int = int(os.getenv("AGGREGATION_WINDOW_MINUTES", "15"))

# 事件 escalated 的最低分数阈值
ESCALATION_SCORE_THRESHOLD: int = int(os.getenv("ESCALATION_SCORE_THRESHOLD", "80"))

# =============================================
#  日志
# =============================================

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# =============================================
#  CORS
# =============================================

_cors_env = os.getenv("CORS_ORIGINS", "")
if _cors_env:
    CORS_ORIGINS: list[str] = json.loads(_cors_env)
else:
    CORS_ORIGINS = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ]
