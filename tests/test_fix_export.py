"""F7 导出与分享回归测试。

与 test_api.py 同一套 in-memory SQLite + dependency_overrides；不调用任何模型，
所有会话 / 消息 / 知识库直接写库，因此不依赖 Ollama 或向量库。
"""
from __future__ import annotations

import json
import os
from datetime import timedelta

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import get_db
from backend.models import (
    Base,
    Conversation,
    KnowledgeBase,
    KnowledgePoint,
    Message,
    ShareLink,
    User,
    utcnow,
)
from backend.routers import auth, export
from backend.security import hash_password
from backend.seed_data import DEFAULT_ADMIN

PASSWORD = "secret1x"


def _build_app(session_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(export.router)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture()
def api(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    auth._reg_log.clear()
    auth._login_fails.clear()
    auth._login_ip_fails.clear()
    monkeypatch.setattr(auth, "_REG_MAX", 1000)
    monkeypatch.setattr(auth, "_LOGIN_MAX_FAILS", 1000)
    monkeypatch.setattr(auth, "_IP_MAX_FAILS", 1000)

    with SessionFactory() as db:
        db.add(
            User(
                account=DEFAULT_ADMIN[0],
                password_hash=hash_password(DEFAULT_ADMIN[1]),
                username=DEFAULT_ADMIN[2],
                role="admin",
            )
        )
        db.commit()

    client = TestClient(_build_app(SessionFactory))
    try:
        yield client, SessionFactory
    finally:
        client.close()
        engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, account: str, username: str) -> tuple[str, int]:
    r = client.post(
        "/api/auth/register",
        json={"account": account, "password": PASSWORD, "username": username},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/auth/login", json={"account": account, "password": PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["token"], int(body["user"]["id"])


def _admin_login(client: TestClient) -> str:
    r = client.post(
        "/api/auth/login",
        json={"account": DEFAULT_ADMIN[0], "password": DEFAULT_ADMIN[1]},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _seed_conversation(
    SessionFactory,
    user_id: int,
    *,
    title: str = "第一讲：向量检索",
    with_feedback: bool = True,
) -> tuple[int, list[int]]:
    sources = [
        {
            "source_type": "knowledge_point",
            "doc_id": 7,
            "point_id": 3,
            "chunk_id": "point:3",
            "title": "余弦相似度",
            "course": "信息检索",
            "section": "相似度计算",
            "page": 12,
            "content": "余弦相似度衡量两个向量方向的一致性。",
        }
    ]
    with SessionFactory() as db:
        conv = Conversation(user_id=user_id, title=title)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        q = Message(conversation_id=conv.id, role="user", content="什么是余弦相似度？")
        a = Message(
            conversation_id=conv.id,
            role="assistant",
            content="余弦相似度用夹角余弦衡量向量相似程度。",
            sources=json.dumps(sources, ensure_ascii=False),
            feedback="up" if with_feedback else "",
            feedback_reason="解释清楚" if with_feedback else "",
        )
        db.add_all([q, a])
        db.commit()
        db.refresh(q)
        db.refresh(a)
        return conv.id, [q.id, a.id]


def _seed_knowledge_base(SessionFactory, admin_id: int) -> tuple[int, list[int]]:
    with SessionFactory() as db:
        base = KnowledgeBase(name="信息检索", description="课程知识库", created_by=admin_id)
        db.add(base)
        db.commit()
        db.refresh(base)
        p1 = KnowledgePoint(
            title="余弦相似度",
            content="余弦相似度衡量两个向量方向的一致性。",
            course=base.name,
            tags="向量,相似度",
            created_by=admin_id,
        )
        p2 = KnowledgePoint(
            title="倒排索引",
            content="倒排索引从词项映射到文档列表。",
            course=base.name,
            created_by=admin_id,
        )
        db.add_all([p1, p2])
        db.commit()
        db.refresh(p1)
        db.refresh(p2)
        return base.id, [p1.id, p2.id]


# ---------------------------------------------------------------- 导出


def test_export_markdown_permission_and_headers(api):
    client, SessionFactory = api
    _owner_token, owner_id = _register(client, "24100001", "所有者")
    other_token, _other_id = _register(client, "24100002", "别人")
    admin_token = _admin_login(client)
    conv_id, _ = _seed_conversation(SessionFactory, owner_id)

    r = client.get(f"/api/export/conversations/{conv_id}.md", headers=_auth(other_token))
    assert r.status_code == 404, r.text

    client.cookies.clear()  # 前面的登录会留下 Cookie，这里要验证真正的未登录
    r = client.get(f"/api/export/conversations/{conv_id}.md")
    assert r.status_code == 401, r.text

    r = client.get(f"/api/export/conversations/{conv_id}.md", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert "第一讲" in r.text
    assert "学生" in r.text and "助教" in r.text


def test_export_markdown_contains_sources_and_feedback(api):
    client, SessionFactory = api
    owner_token, owner_id = _register(client, "24100003", "所有者")
    conv_id, _ = _seed_conversation(SessionFactory, owner_id)

    r = client.get(f"/api/export/conversations/{conv_id}.md", headers=_auth(owner_token))
    assert r.status_code == 200, r.text
    body = r.text
    assert "引用来源" in body
    assert "余弦相似度" in body
    assert "信息检索" in body
    assert "第 12 页" in body
    assert "反馈" in body and "有帮助" in body and "解释清楚" in body
    # 用户与助手消息都按中文角色逐条输出
    assert body.count("## 学生") == 1
    assert body.count("## 助教") == 1


def test_export_json_structure(api):
    client, SessionFactory = api
    owner_token, owner_id = _register(client, "24100004", "所有者")
    conv_id, _ = _seed_conversation(SessionFactory, owner_id)

    r = client.get(f"/api/export/conversations/{conv_id}.json", headers=_auth(owner_token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["kind"] == "conversation"
    conv = data["conversation"]
    assert conv["id"] == conv_id
    assert conv["title"] == "第一讲：向量检索"
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assistant = conv["messages"][1]
    assert assistant["sources"][0]["title"] == "余弦相似度"
    assert assistant["feedback"] == "up"


def test_knowledge_export_requires_admin(api):
    client, SessionFactory = api
    student_token, _sid = _register(client, "24100005", "学生")
    admin_token = _admin_login(client)
    with SessionFactory() as db:
        admin = db.scalar(select(User).where(User.account == DEFAULT_ADMIN[0]))
        admin_id = admin.id
    base_id, _ = _seed_knowledge_base(SessionFactory, admin_id)

    r = client.get(f"/api/export/knowledge/{base_id}.md", headers=_auth(student_token))
    assert r.status_code == 403, r.text

    r = client.get(f"/api/export/knowledge/{base_id}.md", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert "知识库：信息检索" in r.text
    assert "## 余弦相似度" in r.text
    assert "## 倒排索引" in r.text
    assert "倒排索引从词项映射到文档列表。" in r.text


# ---------------------------------------------------------------- 分享


def test_create_share_then_anonymous_read(api):
    client, SessionFactory = api
    owner_token, owner_id = _register(client, "24100006", "所有者")
    conv_id, _ = _seed_conversation(SessionFactory, owner_id)

    r = client.post(
        "/api/share",
        json={"kind": "conversation", "id": conv_id, "expires_hours": 2},
        headers=_auth(owner_token),
    )
    assert r.status_code == 200, r.text
    created = r.json()
    token = created["token"]
    assert created["url"] == f"/share/{token}"
    assert created["expires_at"]
    assert len(token) >= 40

    # 不带任何 Authorization / Cookie，也不应写库
    client.cookies.clear()
    r = client.get(f"/api/share/{token}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["kind"] == "conversation"
    conv = data["conversation"]
    assert conv["title"] == "第一讲：向量检索"
    assert conv["messages"][1]["content"].startswith("余弦相似度")
    assert conv["messages"][1]["sources"][0]["title"] == "余弦相似度"
    # 内部字段不外泄
    flat = json.dumps(data, ensure_ascii=False)
    assert "feedback" not in flat
    assert "user_id" not in flat
    assert "created_by" not in flat

    # 分享页 HTML 免登录可打开
    r = client.get(f"/share/{token}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert "分享" in r.text


def test_share_list_and_revoke(api):
    client, SessionFactory = api
    owner_token, owner_id = _register(client, "24100007", "所有者")
    conv_id, _ = _seed_conversation(SessionFactory, owner_id)
    token = client.post(
        "/api/share", json={"kind": "conversation", "id": conv_id}, headers=_auth(owner_token)
    ).json()["token"]

    r = client.get("/api/share", headers=_auth(owner_token))
    assert r.status_code == 200, r.text
    listing = r.json()
    assert listing["total"] == 1
    assert listing["items"][0]["token"] == token
    assert listing["items"][0]["target_title"] == "第一讲：向量检索"
    assert listing["items"][0]["expired"] is False

    r = client.delete(f"/api/share/{token}", headers=_auth(owner_token))
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] is True

    r = client.get(f"/api/share/{token}")
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "分享链接不存在或已失效"


def test_expired_share_returns_404(api):
    client, SessionFactory = api
    owner_token, owner_id = _register(client, "24100008", "所有者")
    conv_id, _ = _seed_conversation(SessionFactory, owner_id)
    token = client.post(
        "/api/share", json={"kind": "conversation", "id": conv_id}, headers=_auth(owner_token)
    ).json()["token"]

    with SessionFactory() as db:
        link = db.scalar(select(ShareLink).where(ShareLink.token == token))
        link.expires_at = utcnow() - timedelta(minutes=5)
        db.commit()

    r = client.get(f"/api/share/{token}")
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "分享链接不存在或已失效"


def test_knowledge_share_requires_admin(api):
    client, SessionFactory = api
    student_token, _sid = _register(client, "24100009", "学生")
    admin_token = _admin_login(client)
    with SessionFactory() as db:
        admin = db.scalar(select(User).where(User.account == DEFAULT_ADMIN[0]))
        admin_id = admin.id
    base_id, _ = _seed_knowledge_base(SessionFactory, admin_id)

    r = client.post(
        "/api/share",
        json={"kind": "knowledge_base", "id": base_id},
        headers=_auth(student_token),
    )
    assert r.status_code == 403, r.text

    r = client.post(
        "/api/share",
        json={"kind": "knowledge_base", "id": base_id, "expires_hours": 24},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    r = client.get(f"/api/share/{token}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["kind"] == "knowledge_base"
    kb = data["knowledge_base"]
    assert kb["name"] == "信息检索"
    assert kb["point_count"] == 2
    assert [p["title"] for p in kb["points"]] == ["余弦相似度", "倒排索引"]
    flat = json.dumps(data, ensure_ascii=False)
    assert "created_by" not in flat


def test_share_expiry_bounds_and_foreign_conversation(api):
    client, SessionFactory = api
    owner_token, owner_id = _register(client, "24100010", "所有者")
    other_token, _other_id = _register(client, "24100011", "别人")
    conv_id, _ = _seed_conversation(SessionFactory, owner_id)

    r = client.post(
        "/api/share",
        json={"kind": "conversation", "id": conv_id},
        headers=_auth(other_token),
    )
    assert r.status_code == 403, r.text

    r = client.post(
        "/api/share",
        json={"kind": "conversation", "id": conv_id, "expires_hours": 721},
        headers=_auth(owner_token),
    )
    assert r.status_code == 422, r.text
