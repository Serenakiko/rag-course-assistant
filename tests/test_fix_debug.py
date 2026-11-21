"""F9 检索调试页的回归测试。

不连 Ollama / Chroma：embeddings 与向量库都用 fake 替换，但走真实的路由、
真实的 vector_store._search_pipeline（候选去重、rerank、阈值门控都真跑）。
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import get_db
from backend.models import Base, User
from backend.routers import auth, retrieval_debug
from backend.security import hash_password
from backend.services import vector_store

PASSWORD = "secret1x"
COURSES = {"操作系统", "计算机网络"}


def _build_app(session_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(retrieval_debug.router, prefix="/api/admin/retrieval")

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
    with SessionFactory() as db:
        db.add(
            User(
                account="23040101",
                password_hash=hash_password("admin-pw"),
                username="系统管理员",
                role="admin",
            )
        )
        db.commit()
    auth._reg_log.clear()
    auth._login_fails.clear()
    auth._login_ip_fails.clear()
    monkeypatch.setattr(auth, "_REG_MAX", 1000)
    monkeypatch.setattr(auth, "_LOGIN_MAX_FAILS", 1000)
    monkeypatch.setattr(auth, "_IP_MAX_FAILS", 1000)

    client = TestClient(_build_app(SessionFactory))
    try:
        yield client, SessionFactory
    finally:
        client.close()
        engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin_token(client: TestClient) -> str:
    r = client.post(
        "/api/auth/login",
        json={"account": "23040101", "password": "admin-pw"},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _student_token(client: TestClient, account: str = "24100001") -> str:
    r = client.post(
        "/api/auth/register",
        json={"account": account, "password": PASSWORD, "username": f"学生{account}"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/auth/login", json={"account": account, "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _doc(title: str, course: str, content: str, doc_id: int = 1, **extra) -> Document:
    metadata = {
        "chunk_id": f"chunk-{title}",
        "doc_id": doc_id,
        "filename": "os.md",
        "course": course,
        "title": title,
        "chunk_index": 0,
        "source_type": "document",
        **extra,
    }
    return Document(page_content=content, metadata=metadata)


class FakeStore:
    """模拟 Chroma：score 是距离，按 filter 里的 course 过滤。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[dict] = []

    def _apply(self, filter_, k):
        courses = ((filter_ or {}).get("course") or {}).get("$in")
        rows = [
            row
            for row in self.rows
            if not courses or row[0].metadata.get("course") in courses
        ]
        return rows[:k]

    def similarity_search_with_score(self, query, k=4, filter=None):
        self.calls.append({"query": query, "k": k, "filter": filter})
        return self._apply(filter, k)

    def similarity_search_with_score_by_vector(self, embedding, k=4, filter=None):
        self.calls.append({"embedding": embedding, "k": k, "filter": filter})
        return self._apply(filter, k)


class FakeEmbeddings:
    def __init__(self):
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2, 0.3]


@pytest.fixture()
def fake_retrieval(monkeypatch):
    """3 条候选：2 条高于阈值（0.75/0.60），1 条低于阈值（0.40）。"""

    store = FakeStore(
        [
            (
                _doc("进程与线程", "操作系统", "进程是资源分配单位，线程是调度单位。", 1),
                0.25,
            ),
            (_doc("TCP 三次握手", "计算机网络", "TCP 建立连接需要三次握手。", 2), 0.15),
            (
                _doc(
                    "死锁的必要条件",
                    "操作系统",
                    "互斥、占有并等待、不可抢占、循环等待。",
                    3,
                ),
                0.60,
            ),
        ]
    )
    embeddings = FakeEmbeddings()
    monkeypatch.setattr(vector_store, "get_vector_store", lambda: store)
    monkeypatch.setattr(vector_store, "get_embeddings", lambda: embeddings)
    monkeypatch.setattr(
        vector_store, "_active_vector_scope", lambda _courses: (set(), set(COURSES))
    )
    return store, embeddings


# ---------------------------------------------------------------- 权限


def test_student_forbidden(api, fake_retrieval):
    client, _ = api
    token = _student_token(client)
    r = client.get(
        "/api/admin/retrieval/debug",
        params={"question": "进程和线程"},
        headers=_auth(token),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "当前角色无权执行此操作"
    # 权限校验在检索之前，学生请求不应触发检索
    assert fake_retrieval[0].calls == []


def test_anonymous_unauthorized(api, fake_retrieval):
    client, _ = api
    r = client.get("/api/admin/retrieval/debug", params={"question": "进程"})
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------- 正常结构


def test_admin_debug_structure(api, fake_retrieval):
    client, _ = api
    store, embeddings = fake_retrieval
    token = _admin_token(client)
    r = client.get(
        "/api/admin/retrieval/debug",
        params={"question": "进程和线程有什么区别？"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["question"] == "进程和线程有什么区别？"
    assert body["courses"] == []
    assert body["threshold"] == pytest.approx(vector_store.settings.UNMATCHED_SIMILARITY)
    for key in ("embedding_ms", "retrieval_ms"):
        assert isinstance(body[key], int) and body[key] >= 0

    candidates = body["candidates"]
    assert [c["rank"] for c in candidates] == list(range(1, len(candidates) + 1))
    first = candidates[0]
    assert set(first) == {
        "chunk_id",
        "doc_id",
        "filename",
        "course",
        "title",
        "score",
        "rank",
        "rerank_score",
        "passed_threshold",
        "preview",
    }
    assert first["doc_id"] in (1, 2, 3)
    assert len(first["preview"]) <= 120
    # 低于阈值的候选也要返回，并标记为未通过门控
    below = [c for c in candidates if c["score"] < body["threshold"]]
    assert below and all(c["passed_threshold"] is False for c in below)
    above = [c for c in candidates if c["score"] >= body["threshold"]]
    assert above and all(c["passed_threshold"] is True for c in above)

    decision = body["decision"]
    assert decision["answerable"] is True
    assert decision["reason"] == "above_threshold"
    assert decision["top_score"] == pytest.approx(max(c["score"] for c in candidates))

    for item in body["reranked"]:
        assert set(item) == {"chunk_id", "rank_before", "rank_after"}
        assert item["rank_before"] != item["rank_after"]

    timings = body["timings_ms"]
    assert set(timings) == {"embed", "query", "rerank"}
    assert all(isinstance(value, int) and value >= 0 for value in timings.values())
    assert body["retrieval_ms"] == timings["query"] + timings["rerank"]

    # 调试端点确实复用了真实检索链路：查询向量自己算一次，再按向量检索
    assert embeddings.queries == ["进程和线程有什么区别？"]
    assert store.calls and "embedding" in store.calls[0]
    assert store.calls[0]["k"] >= vector_store.settings.TOP_K


def test_below_threshold_decision(api, fake_retrieval, monkeypatch):
    client, _ = api
    monkeypatch.setattr(vector_store.settings, "UNMATCHED_SIMILARITY", 0.99)
    token = _admin_token(client)
    r = client.get(
        "/api/admin/retrieval/debug",
        params={"question": "完全不相关的问题"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["threshold"] == pytest.approx(0.99)
    assert body["decision"]["answerable"] is False
    assert body["decision"]["reason"] == "below_threshold"
    assert all(c["passed_threshold"] is False for c in body["candidates"])


# ---------------------------------------------------------------- 课程过滤


def test_courses_filter(api, fake_retrieval):
    client, _ = api
    store, _ = fake_retrieval
    token = _admin_token(client)
    r = client.get(
        "/api/admin/retrieval/debug",
        params=[("question", "进程"), ("courses", "操作系统")],
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["courses"] == ["操作系统"]
    assert {c["course"] for c in body["candidates"]} == {"操作系统"}
    assert store.calls[0]["filter"] == {"course": {"$in": ["操作系统"]}}


def test_courses_repeatable_query_params(api, fake_retrieval):
    client, _ = api
    token = _admin_token(client)
    r = client.get(
        "/api/admin/retrieval/debug",
        params=[
            ("question", "进程"),
            ("courses", "操作系统"),
            ("courses", "计算机网络"),
        ],
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["courses"] == ["操作系统", "计算机网络"]


# ---------------------------------------------------------------- 参数校验


def test_empty_question_422(api, fake_retrieval):
    client, _ = api
    token = _admin_token(client)
    for params in ({"question": ""}, {}, {"question": "x" * 501}):
        r = client.get(
            "/api/admin/retrieval/debug", params=params, headers=_auth(token)
        )
        assert r.status_code == 422, (params, r.text)


# ---------------------------------------------------------------- 检索不可用


def test_retrieval_failure_returns_503(api, fake_retrieval, monkeypatch):
    client, _ = api
    token = _admin_token(client)

    def boom(*_args, **_kwargs):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(vector_store, "_search_pipeline", boom)
    r = client.get(
        "/api/admin/retrieval/debug",
        params={"question": "进程"},
        headers=_auth(token),
    )
    assert r.status_code == 503, r.text
    assert "检索服务暂时不可用" in r.json()["detail"]


class _FakeCollection:
    """模拟 Chroma collection.query：返回 documents/metadatas/distances。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[dict] = []

    def query(self, query_embeddings=None, n_results=4, where=None, include=None):
        self.calls.append(
            {
                "embeddings": query_embeddings,
                "n_results": n_results,
                "where": where,
                "include": include,
            }
        )
        courses = ((where or {}).get("course") or {}).get("$in")
        rows = [
            row
            for row in self.rows
            if not courses or row[0].metadata.get("course") in courses
        ][:n_results]
        return {
            "documents": [[doc.page_content for doc, _ in rows]],
            "metadatas": [[dict(doc.metadata) for doc, _ in rows]],
            "distances": [[score for _doc, score in rows]],
        }


class _LegacyStore:
    """旧版 langchain-chroma：只有底层 collection，没有按向量检索的方法。"""

    def __init__(self, rows):
        self._collection = _FakeCollection(rows)

    def similarity_search_with_score(self, query, k=4, filter=None):  # pragma: no cover
        raise AssertionError("按向量检索不应退回到按文本检索")


def test_query_by_vector_works_without_langchain_helper():
    """真实 Chroma 没有 similarity_search_with_score_by_vector，检索调试曾因此 503。"""
    rows = [
        (_doc("进程与线程", "操作系统", "进程是资源分配单位", doc_id=1), 0.2),
        (_doc("TCP", "计算机网络", "三次握手", doc_id=2), 0.4),
    ]
    store = _LegacyStore(rows)

    results = vector_store._query_by_vector(
        store, [0.1, 0.2, 0.3], 5, {"course": {"$in": ["操作系统"]}}
    )

    assert [doc.metadata["title"] for doc, _score in results] == ["进程与线程"]
    assert results[0][1] == pytest.approx(0.2)
    assert store._collection.calls[0]["include"] == ["documents", "metadatas", "distances"]
