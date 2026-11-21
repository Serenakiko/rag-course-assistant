"""F3 站内通知回归测试。

覆盖：未登录 401、本人隔离、已读幂等、全部已读、管理员发通知、regenerate
触发 answer_ready、管理员置 resolved 触发 feedback_reply、before_id 分页。
全部走 in-memory SQLite + dependency_overrides，不连 Ollama / 真实数据库。
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import get_db
from backend.models import Base, Notification, UnansweredQuestion, User
from backend.routers import auth, notifications, unanswered
from backend.security import hash_password
from backend.seed_data import DEFAULT_ADMIN


def _build_app(session_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(notifications.router, prefix="/api/notifications")
    app.include_router(unanswered.router, prefix="/api/admin/unanswered")

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

    # 注册/登录限流在测试里用大阈值，避免多次注册互相影响
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

    client = TestClient(app)
    try:
        yield client, SessionFactory
    finally:
        client.close()
        engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, account: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"account": account, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _admin_token(client: TestClient) -> str:
    return _login(client, DEFAULT_ADMIN[0], DEFAULT_ADMIN[1])


def _register(client: TestClient, account: str, username: str = "测试学生") -> str:
    r = client.post(
        "/api/auth/register",
        json={"account": account, "password": "secret1x", "username": username},
    )
    assert r.status_code == 200, r.text
    return _login(client, account, "secret1x")


def _user_id(SessionFactory, account: str) -> int:
    with SessionFactory() as db:
        return db.scalar(select(User.id).where(User.account == account))


def _add_notification(SessionFactory, user_id: int, title: str, kind: str = "system") -> int:
    with SessionFactory() as db:
        item = Notification(
            user_id=user_id, kind=kind, title=title, body="正文", link="#/feedback"
        )
        db.add(item)
        db.commit()
        return item.id


# ---------------- 鉴权 ----------------


def test_notifications_require_login(api):
    client, _ = api
    assert client.get("/api/notifications").status_code == 401
    assert client.post("/api/notifications/1/read").status_code == 401
    assert client.post("/api/notifications/read-all").status_code == 401


# ---------------- 本人隔离 ----------------


def test_list_only_returns_own_notifications(api):
    client, SessionFactory = api
    token_a = _register(client, "24000101", "学生A")
    _register(client, "24000102", "学生B")
    uid_a = _user_id(SessionFactory, "24000101")
    uid_b = _user_id(SessionFactory, "24000102")
    _add_notification(SessionFactory, uid_a, "给A的1")
    _add_notification(SessionFactory, uid_a, "给A的2")
    _add_notification(SessionFactory, uid_b, "给B的1")

    data = client.get("/api/notifications", headers=_auth(token_a)).json()
    assert [it["title"] for it in data["items"]] == ["给A的2", "给A的1"]
    assert data["unread"] == 2
    assert data["has_more"] is False
    assert all(it["read"] is False for it in data["items"])
    # 字段契约
    assert set(data["items"][0]) == {
        "id", "kind", "title", "body", "link", "read", "created_at"
    }
    # 未读过滤
    data_unread = client.get(
        "/api/notifications?unread_only=true", headers=_auth(token_a)
    ).json()
    assert len(data_unread["items"]) == 2


# ---------------- 已读 ----------------


def test_mark_read_is_idempotent_and_isolated(api):
    client, SessionFactory = api
    token_a = _register(client, "24000103", "学生A")
    _register(client, "24000104", "学生B")
    uid_a = _user_id(SessionFactory, "24000103")
    uid_b = _user_id(SessionFactory, "24000104")
    nid_a = _add_notification(SessionFactory, uid_a, "A的通知")
    nid_b = _add_notification(SessionFactory, uid_b, "B的通知")

    first = client.post(f"/api/notifications/{nid_a}/read", headers=_auth(token_a))
    assert first.status_code == 200, first.text
    assert first.json() == {"ok": True, "unread": 0}
    # 幂等：再点一次仍是 ok，未读数不变成负数
    second = client.post(f"/api/notifications/{nid_a}/read", headers=_auth(token_a))
    assert second.status_code == 200
    assert second.json() == {"ok": True, "unread": 0}

    with SessionFactory() as db:
        assert db.get(Notification, nid_a).read_at is not None

    # 越权：别人的通知按不存在处理
    assert (
        client.post(f"/api/notifications/{nid_b}/read", headers=_auth(token_a)).status_code
        == 404
    )
    assert (
        client.post("/api/notifications/999999/read", headers=_auth(token_a)).status_code
        == 404
    )


def test_read_all(api):
    client, SessionFactory = api
    token = _register(client, "24000105", "学生A")
    uid = _user_id(SessionFactory, "24000105")
    _add_notification(SessionFactory, uid, "n1")
    _add_notification(SessionFactory, uid, "n2")

    r = client.post("/api/notifications/read-all", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "unread": 0}

    data = client.get("/api/notifications", headers=_auth(token)).json()
    assert data["unread"] == 0
    assert all(it["read"] for it in data["items"])
    # 全部已读不跨用户
    with SessionFactory() as db:
        assert db.scalars(select(Notification)).all()
        assert all(n.read_at is not None for n in db.scalars(select(Notification)).all())


# ---------------- 管理员手动发送 ----------------


def test_admin_can_send_system_notification(api):
    client, SessionFactory = api
    student_token = _register(client, "24000106", "学生A")
    admin_token = _admin_token(client)
    uid = _user_id(SessionFactory, "24000106")

    r = client.post(
        "/api/notifications",
        json={"user_id": uid, "title": "期末考试安排", "body": "下周三上午", "link": "#/workbench"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["ok"] is True

    data = client.get("/api/notifications", headers=_auth(student_token)).json()
    assert data["unread"] == 1
    item = data["items"][0]
    assert item["kind"] == "system"
    assert item["title"] == "期末考试安排"
    assert item["body"] == "下周三上午"
    assert item["link"] == "#/workbench"

    # 学生不能群发
    assert (
        client.post(
            "/api/notifications",
            json={"user_id": uid, "title": "x", "body": "", "link": ""},
            headers=_auth(student_token),
        ).status_code
        == 403
    )
    # 目标用户不存在
    assert (
        client.post(
            "/api/notifications",
            json={"user_id": 999999, "title": "x", "body": "", "link": ""},
            headers=_auth(admin_token),
        ).status_code
        == 404
    )


# ---------------- 分页 ----------------


def test_before_id_pagination(api):
    client, SessionFactory = api
    token = _register(client, "24000107", "学生A")
    uid = _user_id(SessionFactory, "24000107")
    ids = [_add_notification(SessionFactory, uid, f"n{i}") for i in range(25)]

    page1 = client.get("/api/notifications?limit=20", headers=_auth(token)).json()
    assert len(page1["items"]) == 20
    assert page1["has_more"] is True
    assert page1["items"][0]["id"] == ids[-1]

    page2 = client.get(
        f"/api/notifications?limit=20&before_id={page1['items'][-1]['id']}",
        headers=_auth(token),
    ).json()
    assert len(page2["items"]) == 5
    assert page2["has_more"] is False


# ---------------- 业务钩子：补答成功 ----------------


def test_regenerate_creates_answer_ready_notification(api, monkeypatch):
    client, SessionFactory = api
    student_token = _register(client, "24000108", "学生A")
    admin_token = _admin_token(client)
    uid = _user_id(SessionFactory, "24000108")

    with SessionFactory() as db:
        q = UnansweredQuestion(
            question="红黑树怎么插入", status="pending", created_by=uid
        )
        db.add(q)
        db.commit()
        qid = q.id

    async def _fake_generate_answer(question, courses=None):
        return {"answer": "先按二叉查找树插入，再旋转染色。", "recommended": [], "matched": True}

    monkeypatch.setattr(
        "backend.services.rag_service.generate_answer", _fake_generate_answer
    )

    r = client.post(f"/api/admin/unanswered/{qid}/regenerate", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["resolved"] is True

    data = client.get("/api/notifications", headers=_auth(student_token)).json()
    assert data["unread"] == 1
    item = data["items"][0]
    assert item["kind"] == "answer_ready"
    assert "红黑树怎么插入" in item["body"]
    assert "已由管理员补答" in item["body"]
    assert item["link"] == "#/feedback"


def test_regenerate_failure_sends_no_notification(api, monkeypatch):
    client, SessionFactory = api
    student_token = _register(client, "24000109", "学生A")
    admin_token = _admin_token(client)
    uid = _user_id(SessionFactory, "24000109")
    with SessionFactory() as db:
        q = UnansweredQuestion(question="未收录", status="pending", created_by=uid)
        db.add(q)
        db.commit()
        qid = q.id

    async def _fake_generate_answer(question, courses=None):
        return {"answer": "知识库还没有收录", "recommended": [], "matched": False}

    monkeypatch.setattr(
        "backend.services.rag_service.generate_answer", _fake_generate_answer
    )
    r = client.post(f"/api/admin/unanswered/{qid}/regenerate", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert r.json()["resolved"] is False

    data = client.get("/api/notifications", headers=_auth(student_token)).json()
    assert data["items"] == []


# ---------------- 业务钩子：反馈置为已解决 ----------------


def test_resolve_feedback_creates_feedback_reply_notification(api):
    client, SessionFactory = api
    student_token = _register(client, "24000110", "学生A")
    admin_token = _admin_token(client)
    uid = _user_id(SessionFactory, "24000110")
    with SessionFactory() as db:
        q = UnansweredQuestion(
            question="进程和线程区别",
            status="pending",
            answer="进程是资源单位，线程是调度单位。",
            created_by=uid,
        )
        db.add(q)
        db.commit()
        qid = q.id

    r = client.patch(
        f"/api/admin/unanswered/{qid}",
        params={"status": "resolved"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    data = client.get("/api/notifications", headers=_auth(student_token)).json()
    assert data["unread"] == 1
    item = data["items"][0]
    assert item["kind"] == "feedback_reply"
    assert "进程和线程区别" in item["body"]
    assert item["link"] == "#/feedback"

    # 已经是 resolved 再 PATCH 一次不重复发
    again = client.patch(
        f"/api/admin/unanswered/{qid}",
        params={"status": "resolved"},
        headers=_auth(admin_token),
    )
    assert again.status_code == 200
    assert client.get("/api/notifications", headers=_auth(student_token)).json()["unread"] == 1
