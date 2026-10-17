"""FastAPI 应用入口：路由装配、静态托管、生命周期。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .deps import get_current_user
from .models import User
from .routers import auth, chat, documents, knowledge, stats, unanswered, users
from .seed import init_seed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _warn_default_admin() -> None:
    """默认管理员还在用 111111 时提醒一次（本地课程项目，不改默认密码但要提示）。"""
    from .database import SessionLocal
    from .models import User
    from .security import verify_password

    with SessionLocal() as db:
        admin = (
            db.query(User)
            .filter(User.role == "admin")
            .order_by(User.id)
            .first()
        )
    if admin is not None and verify_password("111111", admin.password_hash):
        logger.warning(
            "管理员账号 %s 仍在使用默认密码 111111，请立即登录后修改", admin.username
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    init_seed()
    _warn_default_admin()
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


@app.get("/api/health", tags=["系统"], summary="健康检查")
def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/api/llm/status", tags=["系统"], summary="大模型连接状态")
def llm_status(user: User = Depends(get_current_user)) -> dict:
    from .services.llm_service import check_llm_connection

    ok, message = check_llm_connection()
    return {"ok": ok, "message": message}


# 前端静态资源
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")
