"""HTTP 集成测试：用 TestClient 走完整的认证、权限、文档和会话链路。

这些用例不依赖 Ollama / 真实数据库：
- 每个测试用独立的 in-memory SQLite（StaticPool 共享同一连接）；
- 通过 dependency_overrides 替换 get_db；
- 文档上传的后台解析和向量清理被 monkeypatch 掉。
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
from backend.models import Base, Conversation, Message, User
from backend.routers import (
    auth,
    chat,
    documents,
    knowledge,
    recycle,
    stats,
    unanswered,
    users,
)
from backend.security import hash_password
from backend.seed_data import DEFAULT_ADMIN


def _build_app(session_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(knowledge.router, prefix="/api/knowledge")
    app.include_router(documents.router, prefix="/api/documents")
    app.include_router(users.router, prefix="/api")
    app.include_router(stats.router, prefix="/api/stats")
    app.include_router(unanswered.router, prefix="/api/admin/unanswered")
    app.include_router(recycle.router, prefix="/api/admin/recycle")

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture()
def api(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    app = _build_app(SessionFactory)

    # 限流状态是模块级全局变量，逐个测试清空，避免互相影响
    auth._reg_log.clear()
    auth._login_fails.clear()
    auth._login_ip_fails.clear()
    monkeypatch.setattr(auth, "_REG_MAX", 1000)
    monkeypatch.setattr(auth, "_LOGIN_MAX_FAILS", 1000)
    monkeypatch.setattr(auth, "_IP_MAX_FAILS", 1000)

    # 上传目录放到临时目录，测试结束自动清理
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(settings, "UPLOAD_DIR", upload_dir)
    # recycle.TRASH_DIR 是模块常量，只在 import 时算过一次；不 patch 的话删除文档
    # 会写真实 data/trash/，在 Windows 上还容易撞上 PermissionError。
    from backend.services import recycle

    trash_dir = tmp_path / "trash"
    trash_dir.mkdir()
    monkeypatch.setattr(recycle, "TRASH_DIR", trash_dir)

    with SessionFactory() as db:
        admin = User(
            account=DEFAULT_ADMIN[0],
            password_hash=hash_password(DEFAULT_ADMIN[1]),
            username=DEFAULT_ADMIN[2],
            role="admin",
        )
        db.add(admin)
        db.commit()

    client = TestClient(app)
    try:
        yield client, SessionFactory
    finally:
        client.close()
        engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, account: str, password: str = "secret1x") -> str:
    r = client.post("/api/auth/login", json={"account": account, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _register(client: TestClient, account: str, username: str = "测试学生") -> str:
    r = client.post(
        "/api/auth/register",
        json={"account": account, "password": "secret1x", "username": username},
    )
    assert r.status_code == 200, r.text
    return _login(client, account)


def test_register_login_and_me(api):
    client, _ = api
    token = _register(client, "24000001", "接口学生")

    r = client.get("/api/auth/me", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["account"] == "24000001"
    assert r.json()["role"] == "student"


def _assert_forbidden(r, what: str) -> None:
    """403 不只是状态码：错误体要是给人看的中文原因，不能漏内部细节。"""
    assert r.status_code == 403, f"{what}: {r.text}"
    body = r.json()
    assert body["detail"] == "当前角色无权执行此操作", body
    assert "Traceback" not in r.text and "sqlalchemy" not in r.text.lower()


def test_student_cannot_access_admin_endpoints(api):
    client, _ = api
    student = _register(client, "24000002")
    for path in ("/api/admin/users", "/api/admin/recycle", "/api/admin/unanswered/stats"):
        _assert_forbidden(client.get(path, headers=_auth(student)), path)
    # 越权请求不能留下任何副作用：用户表仍然只有种子管理员 + 刚注册的学生
    admin = _login(client, DEFAULT_ADMIN[0], DEFAULT_ADMIN[1])
    users = client.get("/api/admin/users", headers=_auth(admin)).json()
    items = users["items"] if isinstance(users, dict) else users
    assert {u["account"] for u in items} == {DEFAULT_ADMIN[0], "24000002"}

    admin = _login(client, DEFAULT_ADMIN[0], DEFAULT_ADMIN[1])
    assert client.get("/api/admin/users", headers=_auth(admin)).status_code == 200
    assert client.get("/api/admin/recycle", headers=_auth(admin)).status_code == 200
    assert client.get("/api/admin/unanswered/stats", headers=_auth(admin)).status_code == 200


def test_knowledge_base_visibility(api):
    client, _ = api
    alice = _register(client, "24000003", "学生A")
    bob = _register(client, "24000004", "学生B")

    # 学生只读：建库/改名/删库都应收敛到管理员（报告 F2/R1）
    r = client.post(
        "/api/knowledge/bases",
        json={"name": "学生不该建的库", "description": ""},
        headers=_auth(alice),
    )
    assert r.status_code == 403, r.text

    admin = _login(client, DEFAULT_ADMIN[0], DEFAULT_ADMIN[1])
    for name in ("A的私有库", "B的私有库"):
        r = client.post(
            "/api/knowledge/bases",
            json={"name": name, "description": ""},
            headers=_auth(admin),
        )
        assert r.status_code == 200, r.text

    base_id = client.get("/api/knowledge/bases", headers=_auth(admin)).json()[0]["id"]
    before = client.get("/api/knowledge/bases", headers=_auth(admin)).json()
    _assert_forbidden(
        client.put(
            f"/api/knowledge/bases/{base_id}",
            json={"name": "学生改不动", "description": ""},
            headers=_auth(alice),
        ),
        "学生改库名",
    )
    _assert_forbidden(
        client.delete(f"/api/knowledge/bases/{base_id}", headers=_auth(alice)),
        "学生删库",
    )
    # 越权被拒后数据必须原样：名字没变、库还在
    after = client.get("/api/knowledge/bases", headers=_auth(admin)).json()
    assert after == before
    assert after[0]["name"] != "学生改不动"

    # 管理员建的课程库，学生可只读浏览
    alice_names = {b["name"] for b in client.get("/api/knowledge/bases", headers=_auth(alice)).json()}
    assert {"A的私有库", "B的私有库"} <= alice_names
    bob_names = {b["name"] for b in client.get("/api/knowledge/bases", headers=_auth(bob)).json()}
    assert {"A的私有库", "B的私有库"} <= bob_names


def test_document_upload_list_and_delete(api, monkeypatch):
    client, _ = api
    student = _register(client, "24000005", "上传学生")
    admin = _login(client, DEFAULT_ADMIN[0], DEFAULT_ADMIN[1])
    r = client.post(
        "/api/knowledge/bases",
        json={"name": "上传测试库", "description": ""},
        headers=_auth(admin),
    )
    assert r.status_code == 200, r.text

    # 后台解析和向量清理不在这里测；上传链路只关心 DB 记录与文件落盘
    monkeypatch.setattr(documents, "process_document", lambda *args, **kwargs: None)
    from backend.services import vector_reconcile

    monkeypatch.setattr(vector_reconcile, "delete_document", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        vector_reconcile, "delete_knowledge_point", lambda *args, **kwargs: True
    )

    # 学生上传应收敛到管理员（报告 F2/R1）
    r = client.post(
        "/api/documents/upload",
        headers=_auth(student),
        data={"course": "上传测试库"},
        files={"file": ("note.txt", b"hello world", "text/plain")},
    )
    assert r.status_code == 403, r.text

    r = client.post(
        "/api/documents/upload",
        headers=_auth(admin),
        data={"course": "上传测试库"},
        files={"file": ("note.txt", b"hello world", "text/plain")},
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["filename"] == "note.txt"
    assert doc["course"] == "上传测试库"

    r = client.get("/api/documents", headers=_auth(admin), params={"limit": 10, "offset": 0})
    assert r.status_code == 200, r.text
    assert any(item["id"] == doc["id"] for item in r.json()["items"])

    # 删除改为进回收站（报告 U3），返回回收条目 id
    r = client.delete(f"/api/documents/{doc['id']}", headers=_auth(admin))
    assert r.status_code == 200, r.text
    assert r.json().get("recycled") is True
    assert r.json().get("item_id")


def test_chat_session_lifecycle_and_feedback(api):
    client, SessionFactory = api
    token = _register(client, "24000006", "会话学生")

    r = client.post("/api/chat/sessions", json={"title": "测试会话"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    conv_id = r.json()["id"]

    r = client.patch(
        f"/api/chat/sessions/{conv_id}",
        json={"title": "改名后的会话"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "改名后的会话"

    with SessionFactory() as db:
        msg = Message(
            conversation_id=conv_id,
            role="assistant",
            content="测试回答",
            sources="[]",
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        msg_id = msg.id

    r = client.post(
        f"/api/chat/messages/{msg_id}/feedback",
        json={"feedback": "up"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["feedback"] == "up"
    with SessionFactory() as db:
        assert db.get(Message, msg_id).feedback == "up"

    r = client.delete(f"/api/chat/sessions/{conv_id}", headers=_auth(token))
    assert r.status_code == 200, r.text
    with SessionFactory() as db:
        assert db.get(Conversation, conv_id) is None
