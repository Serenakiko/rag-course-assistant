"""chat.py / rag_service.py 本轮修复的回归测试（P0-3 / P0-4 / P1-1 / P2-1 / P2-2 / P2-3 / F4）。

和其他 HTTP 测试一样用 in-memory SQLite + dependency_overrides，并把
chat.SessionLocal 指向同一个 session factory，这样后台线程里新建的 Session
也落在这个测试库里。
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import get_db
from backend.models import Base, Conversation, Message, UnansweredQuestion, User
from backend.routers import auth, chat
from backend.security import hash_password

PASSWORD = "secret1x"


def _build_app(session_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(chat.router, prefix="/api/chat")

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
    # 生产库的唯一索引由 database.init_db 的增量迁移创建，in-memory 测试库要自己补上，
    # 否则 _record_unmatched 的 ON CONFLICT 目标不存在。
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_unanswered_question_user "
                "ON unanswered_questions (question, created_by)"
            )
        )
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    # 后台线程 / to_thread 里走的是模块级 SessionLocal，必须指到同一个库
    monkeypatch.setattr(chat, "SessionLocal", SessionFactory)
    chat._user_ask_state.clear()
    auth._reg_log.clear()
    auth._login_fails.clear()
    auth._login_ip_fails.clear()

    client = TestClient(_build_app(SessionFactory))
    try:
        yield client, SessionFactory
    finally:
        client.close()
        engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, account: str) -> str:
    r = client.post(
        "/api/auth/register",
        json={"account": account, "password": PASSWORD, "username": "测试学生"},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/auth/login", json={"account": account, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _seed_conversation(SessionFactory, account: str, count: int) -> tuple[int, list[int]]:
    """建一个会话和若干条消息，返回 (conversation_id, message ids 升序)。"""
    with SessionFactory() as db:
        user = db.scalar(select(User).where(User.account == account))
        conv = Conversation(user_id=user.id, title="分页测试")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        ids = []
        for i in range(count):
            m = Message(
                conversation_id=conv.id,
                role="assistant" if i % 2 else "user",
                content=f"消息 {i}",
            )
            db.add(m)
            db.flush()
            ids.append(m.id)
        db.commit()
        return conv.id, ids


# ---------------------------------------------------------------- P2-3 分页


def test_messages_without_params_keeps_legacy_list(api):
    client, SessionFactory = api
    token = _register(client, "24100001")
    conv_id, _ = _seed_conversation(SessionFactory, "24100001", 3)

    r = client.get(f"/api/chat/sessions/{conv_id}/messages", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 3
    assert [m["content"] for m in body] == ["消息 0", "消息 1", "消息 2"]


def test_messages_pagination_with_before_id(api):
    client, SessionFactory = api
    token = _register(client, "24100002")
    conv_id, all_ids = _seed_conversation(SessionFactory, "24100002", 60)

    r = client.get(
        f"/api/chat/sessions/{conv_id}/messages",
        params={"limit": 20},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    page = r.json()
    assert set(page) == {"items", "has_more"}
    assert page["has_more"] is True
    assert len(page["items"]) == 20
    assert [m["id"] for m in page["items"]] == all_ids[-20:]
    # items 内部仍按 id 升序
    assert [m["id"] for m in page["items"]] == sorted(m["id"] for m in page["items"])

    r = client.get(
        f"/api/chat/sessions/{conv_id}/messages",
        params={"limit": 20, "before_id": page["items"][0]["id"]},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    older = r.json()
    assert older["has_more"] is True
    assert [m["id"] for m in older["items"]] == all_ids[-40:-20]

    # 一直翻到最早一页：has_more=False
    r = client.get(
        f"/api/chat/sessions/{conv_id}/messages",
        params={"limit": 200, "before_id": all_ids[20]},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["has_more"] is False
    assert len(first["items"]) == 20
    assert first["items"][0]["id"] == all_ids[0]


def test_messages_pagination_limit_is_clamped(api):
    client, SessionFactory = api
    token = _register(client, "24100003")
    conv_id, all_ids = _seed_conversation(SessionFactory, "24100003", 5)

    r = client.get(
        f"/api/chat/sessions/{conv_id}/messages",
        params={"limit": 999},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 5
    assert r.json()["has_more"] is False


# ---------------------------------------------------------------- F4 反馈原因


def test_feedback_accepts_value_and_truncates_reason(api):
    client, SessionFactory = api
    token = _register(client, "24100004")
    conv_id, ids = _seed_conversation(SessionFactory, "24100004", 2)
    assistant_id = ids[1]

    reason = "重点不突出" * 100  # 500 字，应截断到 200
    r = client.post(
        f"/api/chat/messages/{assistant_id}/feedback",
        json={"value": "down", "reason": reason},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["feedback"] == "down"
    assert len(r.json()["reason"]) == 200

    with SessionFactory() as db:
        msg = db.get(Message, assistant_id)
        assert msg.feedback == "down"
        assert msg.feedback_reason == reason[:200]


def test_feedback_keeps_legacy_feedback_field_and_clears_reason(api):
    client, SessionFactory = api
    token = _register(client, "24100005")
    conv_id, ids = _seed_conversation(SessionFactory, "24100005", 2)
    assistant_id = ids[1]

    # 旧前端仍发 {"feedback": "up"}，必须继续可用
    r = client.post(
        f"/api/chat/messages/{assistant_id}/feedback",
        json={"feedback": "up"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["feedback"] == "up"

    r = client.post(
        f"/api/chat/messages/{assistant_id}/feedback",
        json={"value": "down", "reason": "答非所问"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    # 取消评价时原因一并清空
    r = client.post(
        f"/api/chat/messages/{assistant_id}/feedback",
        json={"value": ""},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["feedback"] == ""
    with SessionFactory() as db:
        msg = db.get(Message, assistant_id)
        assert msg.feedback == ""
        assert msg.feedback_reason == ""

    r = client.post(
        f"/api/chat/messages/{assistant_id}/feedback",
        json={"value": "meh"},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------- P0-4 落库失败


def test_save_answer_ex_reports_failure():
    class _BrokenSession:
        def scalar(self, *args, **kwargs):
            raise RuntimeError("database is locked")

        def rollback(self):
            pass

        def close(self):
            pass

    original = chat.SessionLocal
    chat.SessionLocal = _BrokenSession
    try:
        assert chat._save_answer_ex(1, None, "回答", "[]", 0) == (None, True)
        # 兼容封装仍返回 None
        assert chat._save_answer(1, None, "回答", "[]", 0) is None
    finally:
        chat.SessionLocal = original


def _fake_stream_factory(events, closed: list[bool] | None = None):
    async def _stream(question, history=None, courses=None, still_wanted=None):
        try:
            for event in events:
                yield event
        finally:
            if closed is not None:
                closed.append(True)

    return _stream


def test_ask_emits_save_failed_when_persist_fails(api, monkeypatch):
    client, _ = api
    token = _register(client, "24100006")

    from backend.services import rag_service

    monkeypatch.setattr(
        rag_service,
        "stream_answer",
        _fake_stream_factory(
            [
                {"type": "token", "content": "这是答案"},
                {"type": "sources", "sources": []},
            ]
        ),
    )
    monkeypatch.setattr(chat, "_save_answer_ex", lambda *a, **k: (None, True))

    r = client.post("/api/chat/ask", json={"message": "什么是红黑树"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.text
    assert '"type": "save_failed"' in body
    assert "回答已生成，但保存失败，请重试" in body
    assert '"type": "saved"' not in body


def test_ask_emits_saved_on_success(api, monkeypatch):
    client, SessionFactory = api
    token = _register(client, "24100007")

    from backend.services import rag_service

    monkeypatch.setattr(
        rag_service,
        "stream_answer",
        _fake_stream_factory(
            [
                {"type": "token", "content": "这是答案"},
                {"type": "sources", "sources": []},
            ]
        ),
    )

    r = client.post("/api/chat/ask", json={"message": "什么是红黑树"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert '"type": "saved"' in r.text
    assert "save_failed" not in r.text
    with SessionFactory() as db:
        answers = db.scalars(select(Message).where(Message.role == "assistant")).all()
        assert [m.content for m in answers] == ["这是答案"]


# ---------------------------------------------------------------- P1-1 异常文案


def test_ask_hides_exception_detail(api, monkeypatch):
    client, _ = api
    token = _register(client, "24100008")

    async def _boom(question, history=None, courses=None, still_wanted=None):
        raise RuntimeError("D:/secret/path.db is locked")
        yield  # pragma: no cover

    from backend.services import rag_service

    monkeypatch.setattr(rag_service, "stream_answer", _boom)

    r = client.post("/api/chat/ask", json={"message": "什么是红黑树"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert "服务暂时不可用，请稍后重试" in r.text
    assert "secret" not in r.text
    assert "服务异常" not in r.text


# ---------------------------------------------------------------- P2-2 生成器释放


def test_ask_closes_inner_generator_on_error_event(api, monkeypatch):
    client, _ = api
    token = _register(client, "24100009")

    closed: list[bool] = []
    from backend.services import rag_service

    monkeypatch.setattr(
        rag_service,
        "stream_answer",
        _fake_stream_factory([{"type": "error", "message": "模型暂时不可用"}], closed),
    )

    r = client.post("/api/chat/ask", json={"message": "什么是红黑树"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert closed == [True]
    # 出错后不留半截回答
    assert '"type": "saved"' not in r.text


# ------------------------------------------------- P1-8 未命中记录关联会话和消息


def test_record_unmatched_stores_conversation_and_message(api):
    _, SessionFactory = api
    _register_for_unmatched = None  # noqa: F841  仅为表明测试不依赖 HTTP 注册
    with SessionFactory() as db:
        user = User(
            account="24100010",
            password_hash=hash_password(PASSWORD),
            username="未命中学生",
            role="student",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        conv = Conversation(user_id=user.id, title="未命中会话")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        question = Message(conversation_id=conv.id, role="user", content="什么是B+树")
        db.add(question)
        db.commit()
        db.refresh(question)
        user_id, conv_id, question_id = user.id, conv.id, question.id

    chat._record_unmatched(
        "什么是B+树",
        user_id,
        {"course": "数据结构", "recommended": ["树与二叉树"]},
        conv_id,
        question_id,
    )

    with SessionFactory() as db:
        row = db.scalar(select(UnansweredQuestion).where(UnansweredQuestion.question == "什么是B+树"))
        assert row is not None
        assert row.conversation_id == conv_id
        assert row.message_id == question_id
        assert row.status == "pending"


# ------------------------------------------- P0-3 准备阶段使用独立线程 / 独立 Session


def test_prepare_ask_runs_off_event_loop_with_own_session(api, monkeypatch):
    import asyncio
    import threading

    _, SessionFactory = api
    seen: dict = {}

    def factory():
        # 用独立 Session（而不是请求线程的 db）是 P0-3 的关键
        seen["thread"] = threading.current_thread().name
        return SessionFactory()

    monkeypatch.setattr(chat, "SessionLocal", factory)
    monkeypatch.setattr(
        chat,
        "_prepare_ask_body",
        lambda payload, user, db: (1, 1, None, [], []),
    )

    with SessionFactory() as db:
        user = User(
            account="24100011",
            password_hash=hash_password(PASSWORD),
            username="线程学生",
            role="student",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id

    result = asyncio.run(
        chat._prepare_ask(chat.AskRequest(message="线程测试"), _UserStub(user_id))
    )
    assert result == (1, 1, None, [], [])
    assert seen["thread"] != threading.current_thread().name


class _UserStub:
    id = 0

    def __init__(self, user_id: int):
        self.id = user_id
