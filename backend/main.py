"""FastAPI 应用入口：路由装配、静态托管、生命周期。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import SessionLocal, init_db
from .routers import auth, chat, documents, knowledge, recycle, stats, unanswered, users
from .seed import init_seed
from .services.recycle import purge_expired

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _guard_default_admin() -> None:
    """默认管理员还在用 111111 时的处理。

    只监听本机（默认配置）时记一条警告就够了——这是本地课程项目的取舍。
    一旦把 HOST 放开到 0.0.0.0 让局域网访问，一个已知密码的管理员账号就是
    后门（登录限流只拦暴力破解，不拦已知密码），这种情况直接拒绝启动。
    """
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
    if admin is None or not verify_password("111111", admin.password_hash):
        return
    if settings.HOST not in LOOPBACK_HOSTS:
        raise RuntimeError(
            f"管理员账号 {admin.username} 仍在使用默认密码 111111，而 HOST={settings.HOST} "
            "允许局域网访问，服务拒绝启动。\n"
            "先改密码再放开监听：\n"
            "  1) 把 .env 里的 HOST 改回 127.0.0.1 并启动服务；\n"
            "  2) 用 23040107 / 111111 登录，在「个人中心」改掉密码；\n"
            "  3) 再把 HOST 改成 0.0.0.0 启动。"
        )
    logger.warning(
        "管理员账号 %s 仍在使用默认密码 111111，请立即登录后修改", admin.username
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # 回收站超过保留期的内容顺手清掉（含 data/trash 下的源文件）
    with SessionLocal() as _db:
        removed = purge_expired(_db)
    if removed:
        logger.info("回收站清理：%s 条超过保留期", removed)
    init_seed()
    _guard_default_admin()
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
def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


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
