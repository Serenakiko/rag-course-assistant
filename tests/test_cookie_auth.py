"""P1-5：前端会话从 localStorage 迁移到 HttpOnly Cookie。

覆盖：
- 登录响应 Set-Cookie 的属性（HttpOnly / SameSite=Lax / Path=/ / Max-Age）；
- 只带 Cookie（不带 Authorization）也能通过鉴权；
- 无凭据请求保持原有 401 文案与 WWW-Authenticate 头；
- 登出删除 Cookie 且幂等，登出后 Cookie 失效；
- 改密后重新签发 Cookie（旧 token_version 已失效，新 Cookie 仍可访问）。
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import settings
from backend.database import get_db
from backend.models import Base, User
from backend.routers import auth
from backend.security import hash_password

ACCOUNT = "24009999"
PASSWORD = "secret1x"
UNAUTHORIZED_DETAIL = "登录状态无效或已过期，请重新登录"


@pytest.fixture()
def api(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")

    def override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # 限流是模块级全局变量，逐个测试清空，避免用例之间互相影响
    auth._reg_log.clear()
    auth._login_fails.clear()
    auth._login_ip_fails.clear()
    monkeypatch.setattr(auth, "_REG_MAX", 1000)
    monkeypatch.setattr(auth, "_LOGIN_MAX_FAILS", 1000)
    monkeypatch.setattr(auth, "_IP_MAX_FAILS", 1000)

    with SessionFactory() as db:
        db.add(
            User(
                account=ACCOUNT,
                password_hash=hash_password(PASSWORD),
                username="Cookie 学生",
                role="student",
            )
        )
        db.commit()

    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        engine.dispose()


def _login(client: TestClient, password: str = PASSWORD):
    return client.post(
        "/api/auth/login",
        json={"account": ACCOUNT, "password": password},
    )


def test_login_sets_httponly_session_cookie(api):
    r = _login(api)
    assert r.status_code == 200, r.text

    body = r.json()
    # 旧客户端依赖 token 字段；新契约同时提供 access_token / token_type
    assert body["token"] == body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["account"] == ACCOUNT

    set_cookie = r.headers.get("set-cookie", "")
    assert set_cookie.startswith("access_token=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie
    assert f"Max-Age={settings.JWT_EXPIRE_HOURS * 3600}" in set_cookie
    # 本地 http 部署不应加 Secure，否则浏览器不会回传 Cookie
    assert "Secure" not in set_cookie
    assert r.cookies.get("access_token") == body["access_token"]


def test_cookie_alone_authenticates(api):
    assert _login(api).status_code == 200

    # 不带 Authorization，只靠 TestClient 自动携带的 Cookie
    r = api.get("/api/auth/me")
    assert r.status_code == 200, r.text
    assert r.json()["account"] == ACCOUNT


def test_bearer_header_still_supported(api):
    token = _login(api).json()["access_token"]

    # 旧客户端不带 Cookie，只发 Authorization 头：鉴权仍走 Bearer 分支
    api.cookies.clear()
    r = api.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["account"] == ACCOUNT


def test_logout_revokes_token_even_with_bearer(api):
    token = _login(api).json()["access_token"]
    assert api.post("/api/auth/logout").status_code == 200

    # 登出把 jti 记入黑名单：即使持有原始 token、绕开 Cookie 也必须 401
    api.cookies.clear()
    r = api.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["detail"] == UNAUTHORIZED_DETAIL

    # 同一用户的另一枚令牌不受影响（只吊销当前这一枚）
    other = _login(api).json()["access_token"]
    api.cookies.clear()
    r2 = api.get("/api/auth/me", headers={"Authorization": f"Bearer {other}"})
    assert r2.status_code == 200, r2.text


def test_no_credentials_returns_original_401(api):
    r = api.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"] == UNAUTHORIZED_DETAIL
    assert r.headers.get("www-authenticate") == "Bearer"


def test_logout_deletes_cookie_and_is_idempotent(api):
    assert _login(api).status_code == 200

    r = api.post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert api.cookies.get("access_token") is None

    # Cookie 已删：后续请求必须 401
    assert api.get("/api/auth/me").status_code == 401

    # 未登录再次登出仍然 200
    assert api.post("/api/auth/logout").status_code == 200


def test_change_password_reissues_cookie(api):
    assert _login(api).status_code == 200

    r = api.post(
        "/api/auth/change-password",
        json={"old_password": PASSWORD, "new_password": "secret22"},
    )
    assert r.status_code == 200, r.text
    set_cookie = r.headers.get("set-cookie", "")
    assert set_cookie.startswith("access_token=")
    assert "HttpOnly" in set_cookie

    # 改密后 token_version +1，旧 Cookie 失效；响应写回的新 Cookie 仍能访问
    me = api.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["account"] == ACCOUNT


def test_https_sets_secure_cookie(monkeypatch):
    """https 部署时 Cookie 必须带 Secure；用 https base_url 走同一段代码路径。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")

    def override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    auth._reg_log.clear()
    auth._login_fails.clear()
    auth._login_ip_fails.clear()
    monkeypatch.setattr(auth, "_REG_MAX", 1000)
    monkeypatch.setattr(auth, "_LOGIN_MAX_FAILS", 1000)
    monkeypatch.setattr(auth, "_IP_MAX_FAILS", 1000)

    with SessionFactory() as db:
        db.add(
            User(
                account="24009998",
                password_hash=hash_password("secret1x"),
                username="TLS 学生",
                role="student",
            )
        )
        db.commit()

    client = TestClient(app, base_url="https://testserver")
    try:
        r = client.post(
            "/api/auth/login",
            json={"account": "24009998", "password": "secret1x"},
        )
        assert r.status_code == 200, r.text
        set_cookie = r.headers.get("set-cookie", "")
        assert "access_token=" in set_cookie
        assert "Secure" in set_cookie, set_cookie
        assert "HttpOnly" in set_cookie
    finally:
        client.close()
        engine.dispose()
