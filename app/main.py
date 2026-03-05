"""FastAPI 应用入口.

启动流程: 建表 → 初始化聚合引擎 → 定时轮询 isaiops-be
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import alerts, problems, silences, stats
from app.config import CORS_ORIGINS, LOG_LEVEL
from app.database import create_tables
from app.services.aggregator import AggregationEngine

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 全局聚合引擎实例
aggregation_engine = AggregationEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（startup / shutdown）."""
    # --- Startup ---
    logger.info("正在初始化数据库...")
    create_tables()

    # 启动聚合引擎
    aggregation_engine.start()
    logger.info("应用启动完成")

    yield

    # --- Shutdown ---
    aggregation_engine.stop()
    logger.info("应用已关闭")


app = FastAPI(
    title="ISAIOps 问题聚合服务",
    description="将异常事件聚合为告警工单，提供告警生命周期管理",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由 — stats/silences 必须在 alerts /{id} 之前注册，避免被拦截
app.include_router(stats.router)
app.include_router(silences.router)
app.include_router(alerts.router)
app.include_router(problems.router)


@app.get("/healthz")
async def healthz():
    """K8s 健康检查端点."""
    return {"status": "ok"}


@app.get("/")
async def root():
    """服务基本信息."""
    return {"status": "ok", "service": "isaiops-problems"}
