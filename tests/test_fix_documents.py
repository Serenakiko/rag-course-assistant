"""本次审查修复项的回归测试（文档处理事务 / 向量原子性 / 详情 / 下载 / 回收站）。

复用 test_api.py 的 api fixture：独立的 in-memory SQLite + 静态管理端账号。
只测本次改动的行为，不改动已有用例。
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "x" * 64)

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_api import _auth, _login, _register, api  # noqa: F401

from backend.config import settings
from backend.database import get_db
from backend.models import Document, User
from backend.routers import documents
from backend.seed_data import DEFAULT_ADMIN
from backend.services import vector_store


# ---------- 小工具 ----------
def _admin_token(client) -> str:
    return _login(client, DEFAULT_ADMIN[0], DEFAULT_ADMIN[1])


def _student_token(client, account: str = "24100001") -> str:
    return _register(client, account, "学生甲")


def _user_id(SessionFactory, account: str) -> int:
    with SessionFactory() as db:
        return db.scalar(select(User.id).where(User.account == account))


def _make_doc(
    SessionFactory,
    tmp_path,
    *,
    filename: str = "note.txt",
    status: str = "ready",
    course: str = "测试库",
    uploaded_by: int = 1,
    content: bytes = b"hello world",
    storage_name: str | None = None,
) -> int:
    path = tmp_path / (storage_name or f"{uuid.uuid4().hex}.txt")
    path.write_bytes(content)
    with SessionFactory() as db:
        doc = Document(
            filename=filename,
            file_type="txt",
            file_size=len(content),
            stored_path=str(path),
            status=status,
            course=course,
            uploaded_by=uploaded_by,
            chunk_count=0,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc.id


class _FakeCollection:
    """只实现 document_detail / _next_version 用到的 get/delete。"""

    def __init__(self, ids=None, metadatas=None, documents=None):
        self.ids = list(ids or [])
        self.metadatas = list(metadatas or [])
        self.documents = list(documents or [])
        self.deleted: list[str] = []

    def get(self, where=None, include=None, ids=None):
        if ids is not None:
            wanted = set(ids)
            picked = [
                i for i, item_id in enumerate(self.ids) if item_id in wanted
            ]
            return {
                "ids": [self.ids[i] for i in picked],
                "metadatas": [self.metadatas[i] for i in picked],
                "documents": [self.documents[i] for i in picked],
            }
        return {"ids": list(self.ids), "metadatas": list(self.metadatas)}

    def delete(self, ids):
        self.deleted.extend(ids)


# ---------- F6 文件下载 ----------
def test_document_file_preview_download_and_scope(api, tmp_path):
    client, SessionFactory = api
    admin = _admin_token(client)
    student = _student_token(client)
    student_id = _user_id(SessionFactory, "24100001")
    doc_id = _make_doc(SessionFactory, tmp_path, content=b"hello file")

    r = client.get(f"/api/documents/{doc_id}/file", headers=_auth(admin))
    assert r.status_code == 200, r.text
    assert r.content == b"hello file"
    assert "attachment" not in r.headers.get("content-disposition", "")

    r = client.get(
        f"/api/documents/{doc_id}/file?download=1", headers=_auth(admin)
    )
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]

    # 不存在 / 不可见都返回 404，不泄露文档是否存在
    assert (
        client.get("/api/documents/999999/file", headers=_auth(admin)).status_code
        == 404
    )
    assert (
        client.get(f"/api/documents/{doc_id}/file", headers=_auth(student)).status_code
        == 404
    )

    own_id = _make_doc(
        SessionFactory,
        tmp_path,
        filename="mine.txt",
        uploaded_by=student_id,
        content=b"student file",
    )
    r = client.get(f"/api/documents/{own_id}/file", headers=_auth(student))
    assert r.status_code == 200, r.text
    assert r.content == b"student file"

    # 记录还在、文件被删 → 404 而不是 500
    with SessionFactory() as db:
        stored = Path(db.get(Document, own_id).stored_path)
    stored.unlink()
    assert (
        client.get(f"/api/documents/{own_id}/file", headers=_auth(student)).status_code
        == 404
    )


# ---------- F2/R1 上传仅 admin + P2-4/P2-5 ----------
def test_upload_requires_admin_and_duplicate_returns_409(api, monkeypatch):
    client, _SessionFactory = api
    student = _student_token(client)
    admin = _admin_token(client)

    r = client.post(
        "/api/documents/upload",
        headers=_auth(student),
        data={"course": "任意库"},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 403, r.text
    assert list(settings.UPLOAD_DIR.iterdir()) == []

    r = client.post(
        "/api/knowledge/bases",
        json={"name": "上传库", "description": ""},
        headers=_auth(admin),
    )
    assert r.status_code == 200, r.text

    monkeypatch.setattr(documents, "process_document", lambda *a, **k: None)
    r = client.post(
        "/api/documents/upload",
        headers=_auth(admin),
        data={"course": "上传库"},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 200, r.text
    assert len(list(settings.UPLOAD_DIR.iterdir())) == 1

    # 同名重复上传：409，且第二次不留下孤儿文件
    r = client.post(
        "/api/documents/upload",
        headers=_auth(admin),
        data={"course": "上传库"},
        files={"file": ("note.txt", b"hello again", "text/plain")},
    )
    assert r.status_code == 409, r.text
    assert len(list(settings.UPLOAD_DIR.iterdir())) == 1


def test_upload_commit_failure_removes_orphan_file(api, monkeypatch):
    client, SessionFactory = api
    admin = _admin_token(client)
    r = client.post(
        "/api/knowledge/bases",
        json={"name": "失败库", "description": ""},
        headers=_auth(admin),
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr(documents, "process_document", lambda *a, **k: None)

    app = client.app

    def failing_db():
        db = SessionFactory()

        def boom():
            raise RuntimeError("commit failed")

        db.commit = boom
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = failing_db
    try:
        # 路由会重新抛出异常让 500 中间件处理；用不抛异常的 client 断言响应
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as raw_client:
            r = raw_client.post(
                "/api/documents/upload",
                headers=_auth(admin),
                data={"course": "失败库"},
                files={"file": ("orphan.txt", b"data", "text/plain")},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 500, r.text
    assert list(settings.UPLOAD_DIR.iterdir()) == []


# ---------- P2-6 详情只返回 active 版本 ----------
def test_detail_returns_only_latest_version(api, monkeypatch, tmp_path):
    client, SessionFactory = api
    admin = _admin_token(client)
    doc_id = _make_doc(SessionFactory, tmp_path)

    fake = _FakeCollection(
        ids=["doc-1-0-v1", "doc-1-1-v1", "doc-1-0-v2"],
        metadatas=[
            {"chunk_index": 0, "chunk_id": "1:0"},
            {"chunk_index": 1, "chunk_id": "1:1"},
            {"chunk_index": 0, "chunk_id": "2:0"},
        ],
        documents=["旧版本一", "旧版本二", "新版本一"],
    )
    monkeypatch.setattr(documents, "get_collection", lambda: fake)

    r = client.get(f"/api/documents/{doc_id}", headers=_auth(admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chunks_total"] == 1
    assert [c["text"] for c in body["chunks"]] == ["新版本一"]
    assert "新版本一" in body["preview"]
    assert "旧版本" not in body["preview"]


# ---------- P2-7 详情不解析原文件 ----------
def test_detail_never_parses_file(api, monkeypatch, tmp_path):
    client, SessionFactory = api
    admin = _admin_token(client)
    doc_id = _make_doc(SessionFactory, tmp_path, content=b"x" * 1024)

    def _boom(*args, **kwargs):
        raise AssertionError("详情接口不应在请求线程里 parse_file")

    monkeypatch.setattr(documents, "parse_file", _boom)
    monkeypatch.setattr(documents, "get_collection", lambda: _FakeCollection())

    r = client.get(f"/api/documents/{doc_id}", headers=_auth(admin))
    assert r.status_code == 200, r.text
    assert r.json()["preview"] == ""


# ---------- F6 删除进回收站 ----------
def test_delete_document_calls_recycle(api, monkeypatch, tmp_path):
    client, SessionFactory = api
    admin = _admin_token(client)
    doc_id = _make_doc(SessionFactory, tmp_path)

    calls: list[tuple[int, int]] = []

    def fake_recycle(db, doc, user_id):
        calls.append((doc.id, user_id))
        return SimpleNamespace(id=42)

    from backend.services import recycle

    monkeypatch.setattr(recycle, "recycle_document", fake_recycle, raising=False)

    r = client.delete(f"/api/documents/{doc_id}", headers=_auth(admin))
    assert r.status_code == 200, r.text
    assert r.json() == {"recycled": True, "item_id": 42}
    assert calls and calls[0][0] == doc_id
    # 删除由 recycle 负责，路由自身不再直接删记录
    with SessionFactory() as db:
        assert db.get(Document, doc_id) is not None

    student = _student_token(client)
    r = client.delete(f"/api/documents/{doc_id}", headers=_auth(student))
    assert r.status_code == 403, r.text


# ---------- P1-2 向量半截写入按 id 清理 ----------
def test_add_document_chunks_cleans_partial_write(api, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    fake = _FakeCollection()
    written_ids: list[str] = []

    class _FailingStore:
        def add_documents(self, docs, ids):
            written_ids.extend(ids)
            raise RuntimeError("chroma add failed")

    monkeypatch.setattr(vector_store, "get_collection", lambda: fake)
    monkeypatch.setattr(vector_store, "get_vector_store", lambda: _FailingStore())
    monkeypatch.setattr(
        vector_store, "_knowledge_base_vector_key", lambda course: "kb-test"
    )

    with pytest.raises(RuntimeError):
        vector_store.add_document_chunks(7, "a.txt", "课程A", ["第一段", "第二段"])

    assert written_ids == ["doc-7-0-v1", "doc-7-1-v1"]
    assert fake.deleted == ["doc-7-0-v1", "doc-7-1-v1"]


# ---------- P1-6 自动知识点失败不把文档标 failed ----------
def test_autokp_failure_keeps_document_ready(api, monkeypatch, tmp_path):
    _client, SessionFactory = api
    doc_id = _make_doc(SessionFactory, tmp_path, status="processing", uploaded_by=1)

    class _BoomKnowledgePoint:
        def __init__(self, **kwargs):
            raise RuntimeError("自动知识点写库失败")

    monkeypatch.setattr(documents, "SessionLocal", SessionFactory)
    monkeypatch.setattr(documents, "parse_file", lambda path: "课程正文")
    monkeypatch.setattr(documents, "split_text", lambda text: ["课程正文"])
    monkeypatch.setattr(
        documents, "add_document_chunks", lambda *a, **k: 1
    )
    monkeypatch.setattr(documents, "KnowledgePoint", _BoomKnowledgePoint)

    documents.process_document(doc_id, "测试库")

    with SessionFactory() as db:
        doc = db.get(Document, doc_id)
        assert doc.status == "ready"
        assert doc.chunk_count == 1
        assert not doc.error


# ---------- P2-13 知识库身份缓存 + 失效 ----------
def test_vector_key_cache_and_invalidate(monkeypatch):
    import backend.database as database

    calls: list[int] = []

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, stmt):
            calls.append(1)
            if len(calls) % 2 == 1:  # 奇数次是知识库查询，偶数次是 tombstone 查询
                return _Result([("课程A", "kb-1")])
            return _Result([])

    monkeypatch.setattr(database, "SessionLocal", lambda: _Session())
    vector_store.invalidate_vector_key_cache()

    assert vector_store._knowledge_base_vector_key("课程A") == "kb-1"
    assert vector_store._knowledge_base_vector_key("课程A") == "kb-1"
    assert len(calls) == 2  # 两次查询：知识库 + tombstone；第二次命中缓存不再查

    vector_store.invalidate_vector_key_cache()
    assert vector_store._knowledge_base_vector_key("课程A") == "kb-1"
    assert len(calls) == 4
