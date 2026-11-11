"""FastAPI 应用入口：路由装配、静态托管、生命周期。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import settings
from .database import SessionLocal, init_db
from .routers import auth, chat, documents, knowledge, recycle, stats, unanswered, users
from .seed import init_seed
from .services.admin_security import rotate_insecure_admin_passwords
from .services.recycle import purge_expired
from .services.vector_reconcile import retry_pending_vector_cleanups

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _guard_default_admin() -> None:
    """轮换遗留默认密码，不依赖 HOST 是否被隧道隐藏。"""
    from .database import SessionLocal as CurrentSessionLocal

    with CurrentSessionLocal() as db:
        rotate_insecure_admin_passwords(db)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # 清理回收站超过保留期的内容（含 data/trash 下的源文件）
    with SessionLocal() as _db:
        removed = purge_expired(_db)
    if removed:
        logger.info("回收站清理：%s 条超过保留期", removed)
    init_seed()
    _guard_default_admin()
    retried = retry_pending_vector_cleanups()
    if retried:
        logger.info("向量清理任务已完成：%s 条", retried)
    logger.info("数据库初始化完成：%s", settings.DB_PATH)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# 只放行本机页面跨端口访问；通配符会让任何网站都能拿着浏览器里的令牌调这套接口
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 路由
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(chat.router, prefix="/api/chat", tags=["对话"])
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["知识点"])
app.include_router(documents.router, prefix="/api/documents", tags=["文档"])
app.include_router(users.router, prefix="/api", tags=["用户管理"])
app.include_router(stats.router, prefix="/api/stats", tags=["统计"])
app.include_router(unanswered.router, prefix="/api/admin/unanswered", tags=["未命中问题"])
app.include_router(recycle.router, prefix="/api/admin/recycle", tags=["回收站"])


@app.get("/api/health", tags=["系统"], summary="健康检查")
def health() -> JSONResponse:
    """探活：数据库不可用时返回 503；向量库不可用只降级标注，不阻断服务。"""
    checks = {"database": "ok", "vector_store": "ok"}
    db_ok = True
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        logger.exception("健康检查失败：数据库不可用")
        checks["database"] = "error"
        db_ok = False
    try:
        from .services.vector_store import get_collection

        get_collection().count()
    except Exception as exc:  # noqa: BLE001
        checks["vector_store"] = "unavailable"
        logger.warning("健康检查：向量库不可用（%s）", exc)
    payload = {
        "status": "ok" if db_ok else "degraded",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "checks": checks,
    }
    return JSONResponse(payload, status_code=200 if db_ok else 503)


# 前端静态资源
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

# 静态资源一律 no-cache：浏览器每次回源校验（StaticFiles 带 ETag，命中就是 304）。
# 不加的话，改了 js/css 但 app.html 里的 ?v= 忘了加一，浏览器会继续跑旧代码——
# 表现就是「代码明明改了，页面还是老行为」。
@app.middleware("http")
async def static_no_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")
