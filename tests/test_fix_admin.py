"""针对审查报告 P1-8 / P1-4 / P1-3 / P2-8 / P2-9 / P1-7 / P2-14 / P2-15 的回归测试。

全部用 in-memory SQLite + dependency_overrides，不依赖 Ollama / 真实数据库；
嵌入模型、RAG 生成、向量清理都在测试里 monkeypatch 掉。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.constants import SYSTEM_ADMIN_ACCOUNT
from backend.database import get_db
from backend.models import (
    Base,
    Conversation,
    Document,
    KnowledgeBase,
    KnowledgePoint,
    Message,
    QuestionVector,
    UnansweredQuestion,
    User,
    VectorCleanupTask,
)
from backend.routers import auth, knowledge, stats, unanswered, users
from backend.security import hash_password
from backend.seed_data import DEFAULT_ADMIN


def _build_app(session_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(knowledge.router, prefix="/api/knowledge")
    app.include_router(users.router, prefix="/api")
    app.include_router(stats.router, prefix="/api/stats")
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

    auth._reg_log.clear()
    auth._login_fails.clear()
    auth._login_ip_fails.clear()
    monkeypatch.setattr(auth, "_REG_MAX", 1000)
    monkeypatch.setattr(auth, "_LOGIN_MAX_FAILS", 1000)
    monkeypatch.setattr(auth, "_IP_MAX_FAILS", 1000)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr("backend.config.settings.UPLOAD_DIR", upload_dir)
    # 后台任务用真实 SessionLocal 会写到工作目录的 sqlite 文件，测试里换成内存库
    monkeypatch.setattr(knowledge, "SessionLocal", SessionFactory)

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


# ---------------- P2-15 avatar 白名单 ----------------


def test_avatar_accepts_emoji_and_static_path(api):
    client, _ = api
    token = _register(client, "24000021")
    me = client.get("/api/auth/me", headers=_auth(token)).json()

    for value in ("🎓", "👍🏻", "👨‍👩‍👧‍👦", f"/static/avatars/u{me['id']}_a.png", ""):
        r = client.put("/api/auth/profile", json={"avatar": value}, headers=_auth(token))
        assert r.status_code == 200, (value, r.text)
        assert r.json()["avatar"] == value


@pytest.mark.parametrize(
    "value",
    [
        "hello",
        "🎓🎓",
        "http://evil.example/x.png",
        "javascript:alert(1)",
        "<img src=x onerror=alert(1)>",
        "/static/avatars/../../etc/passwd",
        "/static/avatars/u1_../a.png",
        "x" * 256,
    ],
)
def test_avatar_rejects_non_whitelisted(api, value):
    client, _ = api
    token = _register(client, "24000022")
    r = client.put("/api/auth/profile", json={"avatar": value}, headers=_auth(token))
    assert r.status_code == 400, r.text


# ---------------- P1-4 聚类 GET 只读 / POST 计算 ----------------


def _seed_questions(SessionFactory, texts: list[str]) -> None:
    with SessionFactory() as db:
        for i, text in enumerate(texts):
            db.add(
                UnansweredQuestion(
                    question=text, course="数据结构", status="pending", display_no=i + 1
                )
            )
        db.commit()


def test_clusters_get_is_read_only(api, monkeypatch):
    client, SessionFactory = api
    _seed_questions(SessionFactory, ["红黑树怎么插入", "红黑树的插入过程"])
    admin = _admin_token(client)

    def _boom(*args, **kwargs):
        raise AssertionError("GET /clusters 不应调用嵌入模型")

    monkeypatch.setattr(unanswered, "get_embeddings", _boom)

    r = client.get("/api/admin/unanswered/clusters?status=pending", headers=_auth(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_questions"] == 2
    assert data["method"] == "bigram"
    assert data["clusters"] == data["items"] and data["clusters"]
    # 纯读：不应写入向量缓存
    with SessionFactory() as db:
        assert db.scalar(select(func.count(QuestionVector.id))) == 0


def test_clusters_post_embeds_and_caches(api, monkeypatch):
    client, SessionFactory = api
    _seed_questions(SessionFactory, ["红黑树怎么插入", "红黑树的插入过程"])
    admin = _admin_token(client)

    fake = SimpleNamespace(
        embed_documents=lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
    )
    monkeypatch.setattr(unanswered, "get_embeddings", lambda: fake)

    r = client.post("/api/admin/unanswered/clusters", headers=_auth(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["method"] == "vector"
    assert "degraded" not in data
    assert data["generated_at"]
    assert len(data["clusters"]) == 1
    assert data["clusters"][0]["count"] == 2
    with SessionFactory() as db:
        assert db.scalar(select(func.count(QuestionVector.id))) == 2


def test_clusters_post_degrades_when_embedding_fails(api, monkeypatch):
    client, SessionFactory = api
    _seed_questions(SessionFactory, ["栈和队列的区别"])
    admin = _admin_token(client)

    def _boom(texts):
        raise RuntimeError("model down")

    monkeypatch.setattr(
        unanswered, "get_embeddings", lambda: SimpleNamespace(embed_documents=_boom)
    )

    r = client.post("/api/admin/unanswered/clusters", headers=_auth(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["degraded"] is True
    assert data["clusters"] == []
    assert data["total_questions"] == 1


# ---------------- P1-8 regenerate 写回原会话 ----------------


def test_regenerate_writes_assistant_message_back(api, monkeypatch):
    client, SessionFactory = api
    _register(client, "24000023")
    admin = _admin_token(client)

    with SessionFactory() as db:
        student = db.scalar(select(User).where(User.account == "24000023"))
        conv = Conversation(user_id=student.id, title="红黑树")
        db.add(conv)
        db.flush()
        q = UnansweredQuestion(
            question="红黑树怎么插入",
            course="数据结构",
            status="pending",
            created_by=student.id,
            conversation_id=conv.id,
        )
        db.add(q)
        db.commit()
        qid, conv_id = q.id, conv.id

    async def _fake_generate_answer(question, courses=None):
        return {"answer": "先按二叉查找树插入，再旋转染色。", "recommended": [], "matched": True}

    monkeypatch.setattr(
        "backend.services.rag_service.generate_answer", _fake_generate_answer
    )
    monkeypatch.setattr(
        unanswered,
        "_collect_sources",
        lambda question, courses: [
            {"source_type": "document", "doc_id": 7, "title": "红黑树.pdf"}
        ],
    )

    r = client.post(
        f"/api/admin/unanswered/{qid}/regenerate", headers=_auth(admin)
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["resolved"] is True
    assert data["written_back"] is True
    assert isinstance(data["message_id"], int)

    with SessionFactory() as db:
        rec = db.get(UnansweredQuestion, qid)
        assert rec.status == "resolved"
        assert rec.message_id == data["message_id"]
        msg = db.get(Message, data["message_id"])
        assert msg.conversation_id == conv_id
        assert msg.role == "assistant"
        assert msg.content.startswith("先按二叉查找树插入")
        assert json.loads(msg.sources)[0]["doc_id"] == 7


def test_regenerate_skips_write_back_without_conversation(api, monkeypatch):
    client, SessionFactory = api
    _register(client, "24000024")
    admin = _admin_token(client)
    with SessionFactory() as db:
        student = db.scalar(select(User).where(User.account == "24000024"))
        q = UnansweredQuestion(
            question="旧记录没有会话", status="pending", created_by=student.id
        )
        db.add(q)
        db.commit()
        qid = q.id

    async def _fake_generate_answer(question, courses=None):
        return {"answer": "答案", "recommended": [], "matched": True}

    monkeypatch.setattr(
        "backend.services.rag_service.generate_answer", _fake_generate_answer
    )

    r = client.post(f"/api/admin/unanswered/{qid}/regenerate", headers=_auth(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["written_back"] is False
    assert data["message_id"] is None
    with SessionFactory() as db:
        assert db.scalar(select(func.count(Message.id))) == 0


# ---------------- F2/R1 知识库写操作仅管理员 ----------------


def test_student_can_only_read_knowledge(api):
    client, SessionFactory = api
    student = _register(client, "24000025")
    admin = _admin_token(client)

    # 学生只读
    assert client.get("/api/knowledge/bases", headers=_auth(student)).status_code == 200
    # 学生不能建库 / 改库 / 删库 / 动知识点
    assert (
        client.post(
            "/api/knowledge/bases", json={"name": "学生库", "description": ""},
            headers=_auth(student),
        ).status_code
        == 403
    )
    r = client.post(
        "/api/knowledge/bases", json={"name": "管理员库", "description": ""},
        headers=_auth(admin),
    )
    assert r.status_code == 200, r.text
    base_id = r.json()["id"]
    assert (
        client.put(
            f"/api/knowledge/bases/{base_id}",
            json={"name": "改名", "description": ""},
            headers=_auth(student),
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/api/knowledge/bases/{base_id}", headers=_auth(student)).status_code
        == 403
    )
    assert (
        client.post(
            "/api/knowledge/points",
            json={"title": "t", "content": "c", "course": "管理员库"},
            headers=_auth(student),
        ).status_code
        == 403
    )


# ---------------- P1-3 改名先落持久任务 ----------------


def test_rename_persists_rebuild_task(api, monkeypatch):
    client, SessionFactory = api
    admin = _admin_token(client)
    r = client.post(
        "/api/knowledge/bases", json={"name": "旧库名", "description": ""},
        headers=_auth(admin),
    )
    assert r.status_code == 200, r.text
    base_id = r.json()["id"]

    r = client.put(
        f"/api/knowledge/bases/{base_id}",
        json={"name": "新库名", "description": ""},
        headers=_auth(admin),
    )
    assert r.status_code == 200, r.text

    with SessionFactory() as db:
        task = db.scalar(
            select(VectorCleanupTask).where(
                VectorCleanupTask.kind == "knowledge_base_rebuild"
            )
        )
        assert task is not None
        assert task.course_name == "新库名"
        assert task.knowledge_base_id
        # 空库没有需要重建的向量，后台任务应已收尾
        assert task.status == "done"
        assert db.get(KnowledgeBase, base_id).name == "新库名"


# ---------------- P2-9 删库只入队，不在请求线程清向量 ----------------


def test_remove_base_dispatches_cleanup_in_background(api, monkeypatch):
    client, SessionFactory = api
    admin = _admin_token(client)
    r = client.post(
        "/api/knowledge/bases", json={"name": "待删库", "description": ""},
        headers=_auth(admin),
    )
    assert r.status_code == 200, r.text
    base_id = r.json()["id"]
    vector_key = r.json()["vector_key"]

    calls: list[tuple] = []
    monkeypatch.setattr(
        knowledge, "_run_base_cleanup", lambda *args: calls.append(args)
    )
    monkeypatch.setattr(
        knowledge,
        "snapshot_base",
        lambda db, base, uid: SimpleNamespace(id=1, payload="{}"),
    )

    r = client.delete(f"/api/knowledge/bases/{base_id}", headers=_auth(admin))
    assert r.status_code == 200, r.text
    # 接口只负责把清理任务交给后台，不在请求线程里同步删向量
    assert len(calls) == 1
    assert calls[0][1] == vector_key
    with SessionFactory() as db:
        task = db.scalar(
            select(VectorCleanupTask).where(
                VectorCleanupTask.knowledge_base_id == vector_key,
                VectorCleanupTask.status == "pending",
            )
        )
        assert task is not None, "清理任务必须已落库，重启后由 reconcile 继续"


def test_remove_base_sync_path_when_no_background(api, monkeypatch):
    """兼容单元测试直接调用：background 为 None 时同步清理。"""
    client, SessionFactory = api
    admin = _admin_token(client)
    r = client.post(
        "/api/knowledge/bases", json={"name": "同步库", "description": ""},
        headers=_auth(admin),
    )
    base_id = r.json()["id"]
    vector_key = r.json()["vector_key"]

    cleaned: list[str] = []
    monkeypatch.setattr(
        knowledge,
        "delete_knowledge_base_vectors",
        lambda key, course: cleaned.append(key),
    )
    monkeypatch.setattr(
        knowledge,
        "snapshot_base",
        lambda db, base, uid: SimpleNamespace(id=1, payload="{}"),
    )
    with SessionFactory() as db:
        admin_obj = db.scalar(select(User).where(User.account == SYSTEM_ADMIN_ACCOUNT))
        knowledge.remove_base(base_id, admin_obj, db)
        task = db.scalar(
            select(VectorCleanupTask).where(
                VectorCleanupTask.knowledge_base_id == vector_key
            )
        )
        assert cleaned == [vector_key]
        assert task is not None and task.status == "done"


# ---------------- P1-7 删用户转交文档/知识点 ----------------


def test_delete_user_transfers_documents_and_points(api):
    client, SessionFactory = api
    admin = _admin_token(client)
    _register(client, "24000026")
    with SessionFactory() as db:
        target = db.scalar(select(User).where(User.account == "24000026"))
        heir = db.scalar(select(User).where(User.account == SYSTEM_ADMIN_ACCOUNT))
        doc = Document(
            filename="a.pdf", file_type="pdf", course="数据结构", uploaded_by=target.id
        )
        point = KnowledgePoint(
            title="t", content="c", course="数据结构", created_by=target.id, updated_by=target.id
        )
        db.add_all([doc, point])
        db.commit()
        target_id, heir_id = target.id, heir.id

    r = client.delete(f"/api/admin/users/{target_id}", headers=_auth(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["transferred_docs"] == 1
    assert data["transferred_points"] == 2  # created_by + updated_by 各 1

    with SessionFactory() as db:
        assert db.get(User, target_id) is None
        assert db.scalar(select(Document.uploaded_by)) == heir_id
        assert db.scalar(select(KnowledgePoint.created_by)) == heir_id
        assert db.scalar(select(KnowledgePoint.updated_by)) == heir_id


def test_delete_user_returns_409_on_db_failure(api, monkeypatch):
    client, SessionFactory = api
    admin = _admin_token(client)
    _register(client, "24000027")
    with SessionFactory() as db:
        target = db.scalar(select(User).where(User.account == "24000027"))
        target_id = target.id

    # 让 delete 抛错，验证被包装成 409 而不是 500
    from sqlalchemy.orm import Session

    def _boom(*args, **kwargs):
        raise RuntimeError("locked")

    monkeypatch.setattr(Session, "delete", _boom)
    r = client.delete(f"/api/admin/users/{target_id}", headers=_auth(admin))
    assert r.status_code == 409, r.text
    with SessionFactory() as db:
        assert db.get(User, target_id) is not None


# ---------------- P2-14 doc-usage 只扫窗口内回答 ----------------


def test_doc_usage_filters_by_window_and_limits(api):
    client, SessionFactory = api
    admin = _admin_token(client)
    now = datetime.now(timezone.utc)
    with SessionFactory() as db:
        student = db.scalar(select(User).where(User.account == SYSTEM_ADMIN_ACCOUNT))
        conv = Conversation(user_id=student.id)
        db.add(conv)
        db.flush()
        db.add_all(
            [
                Document(id=1, filename="近期.pdf", file_type="pdf", course="c", status="ready"),
                Document(id=2, filename="很久以前.pdf", file_type="pdf", course="c", status="ready"),
            ]
        )
        db.add_all(
            [
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="a",
                    sources=json.dumps([{"source_type": "document", "doc_id": 1, "title": "近期.pdf"}]),
                    created_at=now - timedelta(days=1),
                ),
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="b",
                    sources=json.dumps([{"source_type": "document", "doc_id": 2, "title": "很久以前.pdf"}]),
                    created_at=now - timedelta(days=200),
                ),
            ]
        )
        db.commit()

    r = client.get("/api/stats/admin/doc-usage?days=30", headers=_auth(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["window_days"] == 30
    assert data["scanned_messages"] == 1
    assert [x["title"] for x in data["ranked"]] == ["近期.pdf"]
    # 窗口外的引用不参与统计，因此旧文档在窗口内算「未被引用」
    assert {x["title"] for x in data["dead_docs"]} == {"很久以前"}
    assert data["truncated"] is False
